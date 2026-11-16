"""RunPod Serverless worker for the Vidyra AI Studio.

Runs ComfyUI on the GPU pod and drives it through its local HTTP API.
Each job received from the backend carries:
  tool        e.g. "txt2img" | "txt2video" | "img2video" | "lipsync"
  model       e.g. "sdxl", "wan22-i2v", "wan22-t2v"
  prompt      text
  params      width/height/steps/frames/fps/cfg/denoise/negative/...
  refs        [{path, url}] signed source media URLs to download
  callback    {base_url, artifact_path, progress_path, token}

The worker downloads refs into ComfyUI's input dir, builds the matching
workflow graph, waits for execution, then streams finished files back to the
backend artifact callback (multipart) and reports progress via the progress
callback. In fake/offline mode it takes no GPU and returns immediately.

Model filenames are resolved via MODEL_FILES below; override any of them by
passing the expected ComfyUI checkpoint name in params["model_file"].

Why streaming callbacks: serverless pods go away when a job completes, so the
output image/video must be pushed to the backend before the handler returns.
"""

from __future__ import annotations

import hashlib
import json
import os
import runpod
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# Custom-node model files expected on the pod (drop these into ComfyUI/models
# matching the keys below, or override per-run via params["model_file"]).
MODEL_FILES = {
    "sdxl": {"ckpt": "sd_xl_base_1.0.safetensors"},
    # Wan2.2 14B video: two-pass (high/low noise) MoE experts, fp8, on the
    # Wan2.1 16-channel VAE. The official ComfyUI templates use wan_2.1_vae for
    # both 14B T2V and I2V (wan2.2_vae is only for the 5B TI2V model).
    "wan22-t2v": {
        "unet_high": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "unet_low": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan_2.1_vae.safetensors",
    },
    "wan22-i2v": {
        "unet_high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "unet_low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan_2.1_vae.safetensors",
    },
    # "lipsync" is intentionally absent: Wav2Lip runs directly in the pod (see
    # _run_lipsync) and its checkpoints are baked into the image, so there is
    # nothing to provision on the network volume.
    # Character lock reuses the SDXL img2img graph, so it shares the SDXL ckpt.
    "sdxl-character": {
        "ckpt": "sd_xl_base_1.0.safetensors",
    },
}

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
INPUT_DIR = os.getenv("COMFY_INPUT", "/ComfyUI/input")
OUTPUT_DIR = os.getenv("COMFY_OUTPUT", "/ComfyUI/output")
WAV2LIP_DIR = os.getenv("WAV2LIP_DIR", "/Wav2Lip")
FAKE = os.getenv("RUNPOD_FAKE", "0") == "1"
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "20"))


def _http_json(url: str, payload=None, timeout: int = 60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _post_file(url: str, file_path: str, token: str):
    """Stream one artifact file to the backend callback (multipart)."""
    import mimetypes

    boundary = "----vidyra" + hashlib.sha256(file_path.encode()).hexdigest()[:16]
    filename = os.path.basename(file_path)
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        body = fh.read()
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}token={urllib.parse.quote(token)}",
        data=head + body + tail,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


# ---------------------------------------------------------------------------
# ComfyUI client
# ---------------------------------------------------------------------------

def _comfy_log_tail(lines: int = 20) -> str:
    try:
        with open("/tmp/comfyui.log", encoding="utf-8", errors="ignore") as fh:
            tail = fh.readlines()[-lines:]
        return "".join(tail).strip()
    except OSError:
        return ""


def _comfy_process_alive() -> bool:
    try:
        res = subprocess.run(
            ["pgrep", "-f", "/ComfyUI/main.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception:
        return True


def _restart_comfy() -> None:
    """Start ComfyUI with PYTHONPATH unset so /app/backend/app never shadows /ComfyUI/app."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    port = os.getenv("COMFY_PORT", "8188")
    try:
        log_fh = open("/tmp/comfyui.log", "ab")
        subprocess.Popen(
            [
                sys.executable,
                "/ComfyUI/main.py",
                "--listen",
                "127.0.0.1",
                "--port",
                str(port),
                "--disable-auto-launch",
                "--highvram",
            ],
            cwd="/ComfyUI" if os.path.isdir("/ComfyUI") else None,
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        pass


def _wait_for_comfy():
    restarted = False
    for attempt in range(90):
        try:
            _http_json(f"{COMFY_URL}/system_stats", timeout=5)
            return
        except Exception:
            if not _comfy_process_alive():
                if not restarted:
                    restarted = True
                    _restart_comfy()
                    time.sleep(3)
                    continue
                tail = _comfy_log_tail()
                raise RuntimeError(
                    f"ComfyUI exited during startup"
                    + (f":\n{tail}" if tail else "")
                )
            time.sleep(2)
    tail = _comfy_log_tail()
    raise RuntimeError(
        "ComfyUI did not become ready" + (f":\n{tail}" if tail else "")
    )


def _run_workflow(graph: dict, on_done=None) -> list[str]:
    """Submit an API-format graph, wait for completion, return output filenames."""
    prompt_id = _http_json(f"{COMFY_URL}/prompt", {"prompt": graph})["prompt_id"]
    while True:
        history = _http_json(f"{COMFY_URL}/history/{prompt_id}?max_items=1", timeout=5)
        entry = history.get(prompt_id)
        if entry:
            break
        time.sleep(2)
    status = entry.get("status", {})
    if status.get("status_str") == "error":
        msgs = "\n".join(str(e.get("error")) for e in status.get("messages", []) if isinstance(e, dict))
        raise RuntimeError(f"ComfyUI graph failed:\n{msgs}")
    outputs: list[str] = []
    for node, node_out in (entry.get("outputs") or {}).items():
        for img in node_out.get("images", []):
            outputs.append(img.get("filename", ""))
        for vids in node_out.get("gifs", []) + node_out.get("videos", []):
            outputs.append(vids.get("filename", "") or vids.get("gif_filename", ""))
    return [o for o in outputs if o]


def _vhs_combine(images, fps: int, prefix: str, save: bool = True) -> dict:
    return {
        "images": images,
        "frame_rate": int(fps),
        "loop_count": 0,
        "filename_prefix": prefix,
        "format": "video/h264-mp4",
        "pingpong": False,
        "save_output": bool(save),
    }


# ---------------------------------------------------------------------------
# Workflow builders  (API-format ComfyUI graphs)
# ---------------------------------------------------------------------------

def _build_txt2img(params: dict) -> dict:
    model = params.get("model")
    p = params.get("params") or {}
    w, h = int(p.get("width", 1024)), int(p.get("height", 1024))
    steps = int(p.get("steps", 30))
    cfg = float(p.get("cfg", 7.0))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    negative = str(p.get("negative", ""))
    files = params.get("model_files") or MODEL_FILES.get(model, {})

    # SDXL
    ckpt = files.get("ckpt", "sd_xl_base_1.0.safetensors")
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "5": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "vidyra_txt2img"}},
    }


def _build_txt2video(params: dict) -> dict:
    """Wan2.2 14B text-to-video (fp8, two-pass high/low-noise experts).

    Mirrors ComfyUI's official "Wan 2.2 14B T2V" template: EmptyHunyuanLatentVideo
    makes the latent, the high-noise expert denoises steps 0..half, the low-noise
    expert refines half..end, then the Wan2.1 VAE decodes and VHS muxes to mp4.
    """
    p = params.get("params") or {}
    model = params.get("model")
    w, h = int(p.get("width", 832)), int(p.get("height", 480))
    frames = int(p.get("frames", 81))
    fps = int(p.get("fps", 16))
    steps = int(p.get("steps", 20))
    cfg = float(p.get("cfg", 4.0))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    negative = str(p.get("negative", ""))
    files = params.get("model_files") or MODEL_FILES.get(model, {})
    unet_high = files.get("unet_high", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors")
    unet_low = files.get("unet_low", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors")
    half = max(1, steps // 2)
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_high, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_low, "weight_dtype": "default"}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name1": files.get("clip", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"), "clip_name2": "", "type": "wan", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": files.get("vae", "wan_2.1_vae.safetensors")}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": positive}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": negative}},
        "9": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": w, "height": h, "length": frames, "batch_size": 1}},
        # High-noise expert establishes motion/structure (steps 0..half).
        "10": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["3", 0], "positive": ["7", 0], "negative": ["8", 0], "latent_image": ["9", 0], "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": 0, "end_at_step": half, "return_with_leftover_noise": "enable"}},
        # Low-noise expert refines detail (steps half..end).
        "11": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["4", 0], "positive": ["7", 0], "negative": ["8", 0], "latent_image": ["10", 0], "add_noise": "disable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": half, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["6", 0]}},
        "13": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["12", 0], fps, "vidyra_t2v")},
    }


def _build_img2video(params: dict) -> dict:
    """Wan2.2 14B image-to-video (fp8, two-pass high/low-noise experts).

    Mirrors ComfyUI's official "Wan 2.2 14B I2V" template: the start frame is
    scaled and handed to WanImageToVideo, which stamps it into the conditioning;
    the high-noise expert denoises steps 0..half, the low-noise expert refines
    half..end, then the Wan2.1 VAE decodes and VHS muxes to mp4.
    """
    p = params.get("params") or {}
    model = params.get("model")
    w, h = int(p.get("width", 832)), int(p.get("height", 480))
    frames = int(p.get("frames", 81))
    fps = int(p.get("fps", 16))
    steps = int(p.get("steps", 20))
    cfg = float(p.get("cfg", 4.0))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    negative = str(p.get("negative", ""))
    files = params.get("model_files") or MODEL_FILES.get(model, {})
    image_name = params.get("ref_files", {}).get("image", "SOURCE_IMAGE.png")
    unet_high = files.get("unet_high", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors")
    unet_low = files.get("unet_low", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors")
    half = max(1, steps // 2)
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_high, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_low, "weight_dtype": "default"}},
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name1": files.get("clip", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"), "clip_name2": "", "type": "wan", "device": "default"}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": files.get("vae", "wan_2.1_vae.safetensors")}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": positive}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": negative}},
        "9": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "10": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["9", 0]}},
        "11": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["7", 0], "negative": ["8", 0], "vae": ["6", 0],
            "width": w, "height": h, "length": frames, "batch_size": 1,
            "start_image": ["10", 0],
        }},
        # High-noise expert establishes motion/structure (steps 0..half).
        "12": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["3", 0], "positive": ["11", 0], "negative": ["11", 1], "latent_image": ["11", 2], "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": 0, "end_at_step": half, "return_with_leftover_noise": "enable"}},
        # Low-noise expert refines detail (steps half..end), fixing flicker.
        "13": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["4", 0], "positive": ["11", 0], "negative": ["11", 1], "latent_image": ["12", 0], "add_noise": "disable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": half, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["6", 0]}},
        "15": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["14", 0], fps, "vidyra_i2v")},
    }


def _build_img2img(params: dict) -> dict:
    """Reference-image restyle (SDXL img2img at low denoise).

    Keeps the subject's likeness from the reference photo (found online by the
    backend's image-research pass, or the user's own upload) while reshaping it
    to the requested scene/art direction. Base SDXL nodes only — no custom
    nodes needed on the pod.
    """
    p = params.get("params") or {}
    w, h = int(p.get("width", 1024)), int(p.get("height", 1024))
    steps = int(p.get("steps", 30))
    cfg = float(p.get("cfg", 6.0))
    denoise = float(p.get("denoise", 0.45))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    negative = str(p.get("negative", ""))
    files = params.get("model_files") or MODEL_FILES.get("sdxl", {})
    ckpt = files.get("ckpt", "sd_xl_base_1.0.safetensors")
    reference = params.get("ref_files", {}).get("reference", "REFERENCE.png")
    return {
        "2": {"class_type": "LoadImage", "inputs": {"image": reference}},
        "3": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["2", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["3", 0], "vae": ["4", 2]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": denoise}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "vidyra_img2img"}},
    }


def _build_character(params: dict) -> dict:
    """48GB portrait-consistent restyle (character lock).

    Reuses the proven SDXL img2img graph — no new node contract. 48GB params:
    higher steps + portrait denoise window keep the face while allowing new
    pose/scene. Native InstantID/IP-Adapter graph replaces this body after a
    successful pod smoke test (phase 2); callers stay unchanged.
    """
    p = dict(params.get("params") or {})
    # 48GB window: strong enough to change scene, gentle enough to keep face.
    p["denoise"] = min(0.6, max(0.35, float(p.get("denoise", 0.5))))
    p["steps"] = int(p.get("steps", 35))
    p["cfg"] = float(p.get("cfg", 6.0))
    ref_files = params.get("ref_files", {})
    if "reference" not in ref_files and "image" in ref_files:
        ref_files = dict(ref_files)
        ref_files["reference"] = ref_files.pop("image")
    return _build_img2img({**params, "params": p, "ref_files": ref_files})


def build_workflow(params: dict) -> dict:
    tool = params.get("tool")
    if tool == "txt2img":
        return _build_txt2img(params)
    if tool == "character":
        return _build_character(params)
    if tool == "img2img":
        return _build_img2img(params)
    if tool == "img2video":
        return _build_img2video(params)
    if tool == "txt2video":
        return _build_txt2video(params)
    # "lipsync" never reaches here: the handler routes it to _run_lipsync,
    # which drives Wav2Lip directly instead of a ComfyUI node.
    raise ValueError(f"Unknown tool: {tool}")


# ---------------------------------------------------------------------------
# Download refs into ComfyUI input
# ---------------------------------------------------------------------------

def _fetch_refs(refs: list, run_dir: str) -> list:
    """Download signed media URLs; returns [(role, dest_path), ...] in order."""
    os.makedirs(run_dir, exist_ok=True)
    staged: list = []
    for i, ref in enumerate(refs or []):
        url = ref.get("url")
        src_path = ref.get("path", "")
        if not url:
            continue
        base = os.path.basename(src_path).rsplit("?", 1)[0] or "ref.bin"
        filename = f"{i:02d}_{base}"
        dest = os.path.join(run_dir, filename)
        with urllib.request.urlopen(url, timeout=300) as resp, open(dest, "wb") as fh:
            fh.write(resp.read())
        staged.append((ref.get("role") or "", dest))
    return staged


def _upload_input(dest: str) -> str:
    """Put a file into ComfyUI's input dir; returns the name ComfyUI sees."""
    filename = os.path.basename(dest)
    target = os.path.join(INPUT_DIR, filename)
    try:
        with open(dest, "rb") as src, open(target, "wb") as out:
            out.write(src.read())
    except OSError as exc:
        raise RuntimeError(f"Could not stage input {filename}: {exc}")
    return filename


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _run_lipsync(inputs, base, art_path, token, report, start):
    """Wav2Lip mouth sync - runs in the pod, not through ComfyUI.

    Same reason as voice clone: Wav2Lip ships as a plain inference script, not
    a node pack. The previous implementation asked a community ComfyUI node
    (``Wav2Lip``) that the image never installed, so every podcast took the
    silent img2video fallback instead of a talking host.

    refs: [{role: "video"}, {role: "audio"}]
    params: model (checkpoint stem), pads, nosmooth, resize_factor, static
    """
    run_dir = os.path.join(INPUT_DIR, f"run_{inputs.get('run_id', 'x')}")
    os.makedirs(run_dir, exist_ok=True)
    staged = _fetch_refs(inputs.get("refs"), run_dir)
    by_role = {}
    for role, dest in staged:
        by_role.setdefault(role, dest)
    # Wav2Lip takes a still image for --face just as happily as a video, so
    # accept either role rather than failing a run over the label.
    video = (
        by_role.get("video")
        or by_role.get("image")
        or (staged[0][1] if staged else "")
    )
    audio = by_role.get("audio") or (staged[1][1] if len(staged) > 1 else "")
    if not video or not audio:
        return {
            "status": "error",
            "error": "lipsync needs a video and an audio reference",
        }

    inference = os.path.join(WAV2LIP_DIR, "inference.py")
    if not os.path.isfile(inference):
        return {
            "status": "error",
            "error": f"Wav2Lip is not installed at {WAV2LIP_DIR}",
        }

    p = inputs.get("params") or {}
    # Accept either a bare stem ("wav2lip_gan") or a filename.
    model = str(p.get("model") or "wav2lip_gan").removesuffix(".pth")
    ckpt = os.path.join(WAV2LIP_DIR, "checkpoints", f"{model}.pth")
    if not os.path.isfile(ckpt):
        # The only checkpoint baked into the image; fall back rather than
        # failing a run we could have completed.
        default = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip_gan.pth")
        if not os.path.isfile(default):
            return {
                "status": "error",
                "error": f"Wav2Lip checkpoint missing: {model}.pth and no default",
            }
        ckpt = default

    fd, out_path = tempfile.mkstemp(suffix=".mp4", dir=OUTPUT_DIR)
    os.close(fd)

    cmd = [
        sys.executable, inference,
        "--checkpoint_path", ckpt,
        "--face", video,
        "--audio", audio,
        "--outfile", out_path,
        "--face_det_batch_size", "4",
        "--wav2lip_batch_size", "32",
    ]
    # Wav2Lip only honours --static/--fps for a still image input; passing
    # them for a video makes it error out.
    if os.path.splitext(video)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        cmd += ["--static", "True", "--fps", str(int(p.get("fps", 25)))]
    pads = p.get("pads")
    if isinstance(pads, (list, tuple)) and len(pads) == 4:
        cmd += ["--pads"] + [str(int(x)) for x in pads]
    elif isinstance(pads, int):
        cmd += ["--pads", str(pads), "0", "0", "0"]
    if p.get("nosmooth"):
        cmd += ["--nosmooth"]
    if p.get("resize_factor"):
        cmd += ["--resize_factor", str(int(p["resize_factor"]))]

    report(30, "Syncing mouth to audio")
    try:
        proc = subprocess.run(
            cmd, cwd=WAV2LIP_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=int(os.getenv("LIPSYNC_TIMEOUT", "1800")),
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Wav2Lip timed out"}
    except Exception as exc:
        return {"status": "error", "error": f"could not run Wav2Lip: {exc}"}

    log = (proc.stdout or b"").decode("utf-8", errors="ignore")
    if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        # S3FD finding no face in generated frames is common enough to be
        # worth its own message: the img2video fallback is the right outcome,
        # and a traceback tail would hide that.
        lowered = log.lower()
        if "face not detected" in lowered or "no face" in lowered:
            detail = "no face detected in the source video"
        else:
            detail = log.strip()[-300:] or "unknown error"
        return {"status": "error", "error": f"Wav2Lip failed: {detail}"}

    report(85, "Streaming result back")
    uploaded = []
    if base and art_path and not inputs.get("_local_publish"):
        _post_file(f"{base}{art_path}", out_path, token)
    uploaded.append(os.path.basename(out_path))
    report(95, "Done")
    return {
        "status": "ok",
        "elapsed": round(time.time() - start, 1),
        "uploads": uploaded,
        "output_paths": [out_path],
        "checkpoint": os.path.basename(ckpt),
    }


def handler(job):
    """RunPod handler — the job.input is the backend's studio run payload."""
    start = time.time()
    inputs = job["input"]
    callback = inputs.get("callback") or {}
    token = callback.get("token", "")
    base = (callback.get("base_url") or "").rstrip("/")
    art_path = callback.get("artifact_path", "")
    prog_path = callback.get("progress_path", "")

    local_cb = inputs.get("_on_progress")

    def report(progress: int, message: str):
        if callable(local_cb):
            try:
                local_cb(progress, message)
            except Exception:
                pass
        if not base or not prog_path or FAKE:
            return
        try:
            url = f"{base}{prog_path}?token={urllib.parse.quote(token)}"
            _http_json(url, {"progress": progress, "message": message}, timeout=10)
        except Exception:
            pass

    try:
        if FAKE:
            time.sleep(1)
            return {"status": "ok", "fake": True}

        # Voice clone and lip sync bypass ComfyUI entirely (same
        # transport/callbacks, no existing graph to duplicate).
        if inputs.get("tool") == "voiceclone":
            return _run_voiceclone(inputs, base, art_path, token, report, start)
        if inputs.get("tool") == "lipsync":
            return _run_lipsync(inputs, base, art_path, token, report, start)
        if inputs.get("tool") == "transcribe":
            return _run_transcribe(inputs, report, start)

        report(5, "Preparing GPU")
        _wait_for_comfy()

        run_dir = os.path.join(INPUT_DIR, f"run_{inputs.get('run_id', 'x')}")
        report(10, "Fetching source media")
        staged = _fetch_refs(inputs.get("refs"), run_dir)

        # Map role->staged filename for builders (explicit role, else by order).
        ref_files = {}
        fallback_roles = ["image", "video", "image", "video"]
        for i, (role, dest) in enumerate(staged):
            role = role or (fallback_roles[i] if i < len(fallback_roles) else "media")
            ref_files[role] = _upload_input(dest)

        params = {
            "tool": inputs.get("tool"),
            "model": inputs.get("model"),
            "prompt": inputs.get("prompt", ""),
            "params": {**(inputs.get("params") or {}), "model_file": inputs.get("params", {}).get("model_file")},
            "ref_files": ref_files,
            "model_files": inputs.get("model_files") or None,
        }
        graph = build_workflow(params)

        report(30, "Rendering on GPU")
        out_files = _run_workflow(graph)
        if not out_files:
            raise RuntimeError("ComfyUI produced no output files")

        report(80, "Streaming result back")
        uploaded = []
        output_paths = []
        for name in out_files:
            local_out = os.path.join(OUTPUT_DIR, name)
            if not os.path.isfile(local_out):
                continue
            if base and art_path and not inputs.get("_local_publish"):
                _post_file(f"{base}{art_path}", local_out, token)
            uploaded.append(name)
            output_paths.append(local_out)
    except Exception as exc:
        report(99, f"Worker error: {exc}")
        return {"status": "error", "error": str(exc), "elapsed": round(time.time() - start, 1)}

    report(95, "Done")
    return {
        "status": "ok",
        "elapsed": round(time.time() - start, 1),
        "uploads": uploaded,
        "output_paths": output_paths,
    }


_WHISPER_MODELS = {}


def _get_whisper(model_size: str):
    from faster_whisper import WhisperModel

    key = model_size or "base"
    model = _WHISPER_MODELS.get(key)
    if model is not None:
        return model
    download_root = os.getenv("WHISPER_DOWNLOAD_ROOT") or (
        "/runpod-volume/models/whisper" if os.path.isdir("/runpod-volume") else None
    )
    if download_root:
        os.makedirs(download_root, exist_ok=True)
    try:
        model = WhisperModel(
            key,
            device="cuda",
            compute_type="float16",
            download_root=download_root,
        )
    except Exception:
        # Older GPUs / missing cuDNN: int8 on GPU is slower but works.
        model = WhisperModel(
            key,
            device="cuda",
            compute_type="int8_float16",
            download_root=download_root,
        )
    _WHISPER_MODELS[key] = model
    return model


def _run_transcribe(inputs, report, start):
    """Word-timestamp transcription for karaoke captions.

    Called through /runsync: the backend uploads a 16 kHz mono FLAC to storage
    and passes a signed URL, then reads the words straight from the response.
    """
    url = inputs.get("audio_url") or ""
    if not url:
        raise RuntimeError("transcribe needs audio_url")
    model_size = inputs.get("model") or "base"
    report(10, "Fetching narration")
    tmp = os.path.join(tempfile.gettempdir(), f"whisper_{os.getpid()}_{int(time.time())}.flac")
    try:
        with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        report(35, "Transcribing")
        model = _get_whisper(model_size)
        segments, info = model.transcribe(
            tmp,
            language="en",
            word_timestamps=True,
            vad_filter=True,
            beam_size=1,
        )
        words = []
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                text = str(getattr(word, "word", "") or "").strip()
                if not text:
                    continue
                words.append(
                    {
                        "word": text,
                        "start": float(getattr(word, "start", 0) or 0),
                        "end": float(getattr(word, "end", 0) or 0),
                    }
                )
        if not words:
            raise RuntimeError("Whisper returned no word timestamps")
        report(95, "Transcription ready")
        return {
            "status": "ok",
            "words": words,
            "language": getattr(info, "language", "en"),
            "elapsed": round(time.time() - start, 1),
        }
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _run_voiceclone(inputs, base, art_path, token, report, start):
    """Zero-shot voice clone (Chatterbox Turbo, MIT) - no ComfyUI involved.

    Reuses _fetch_refs/_post_file/callbacks; only the synthesis body is new
    (no existing TTS function to duplicate - Piper is narration-only local).
    refs: [{role: "audio", url}] voice sample (5-30s clean speech).
    prompt: text to speak. params: exaggeration/cfg_weight/speed/seed/language.
    params.lines: optional list of texts - one run then speaks every line in
    the cloned voice back-to-back into a single WAV and returns per-line
    durations, so the backend splits the track exactly (no alignment guess).
    This is how storyboards/films narrate a whole cast in one run per voice.
    """
    import tempfile

    report(10, "Fetching voice sample")
    run_dir = os.path.join(INPUT_DIR, f"run_{inputs.get('run_id', 'x')}")
    staged = _fetch_refs(inputs.get("refs"), run_dir)
    ref_audio = next((dest for role, dest in staged if role in ("audio", "reference", "")), None)
    if not ref_audio:
        return {"status": "error", "error": "voiceclone needs an audio reference (role=audio)"}
    p = inputs.get("params") or {}
    prompt_text = (inputs.get("prompt") or "").strip()
    lines = [str(x).strip() for x in (p.get("lines") or []) if str(x).strip()][:60]
    texts = lines or ([prompt_text[:2000]] if prompt_text else [])
    if not texts:
        return {"status": "error", "error": "voiceclone needs text in prompt or params.lines"}

    report(30, "Cloning voice")
    try:
        from chatterbox.tts import ChatterboxTTS
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = ChatterboxTTS.from_pretrained(device=device)
    except Exception as exc:
        return {"status": "error", "error": f"voice model unavailable: {exc}"}

    exaggeration = float(p.get("exaggeration", 0.5))
    cfg_weight = float(p.get("cfg_weight", 0.5))
    chunks = []
    for index, line in enumerate(texts):
        try:
            wav = model.generate(
                line,
                audio_prompt_path=ref_audio,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
            )
        except Exception as exc:
            return {"status": "error", "error": f"clone failed on line {index + 1}: {exc}"}
        chunks.append(wav.detach().cpu() if hasattr(wav, "detach") else wav)
        if len(texts) > 1:
            report(
                30 + int(45 * (index + 1) / len(texts)),
                f"Speaking line {index + 1}/{len(texts)}",
            )

    try:
        import torch

        audio = chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=-1)
    except Exception as exc:
        return {"status": "error", "error": f"could not join audio: {exc}"}

    report(80, "Streaming result back")
    fd, out_path = tempfile.mkstemp(suffix=".wav", dir=OUTPUT_DIR)
    os.close(fd)
    try:
        import torchaudio

        torchaudio.save(out_path, audio, model.sr)
    except Exception as exc:
        return {"status": "error", "error": f"could not save wav: {exc}"}
    uploaded = []
    if base and art_path and not inputs.get("_local_publish"):
        _post_file(f"{base}{art_path}", out_path, token)
    uploaded.append(os.path.basename(out_path))
    report(95, "Done")
    result = {
        "status": "ok",
        "elapsed": round(time.time() - start, 1),
        "uploads": uploaded,
        "output_paths": [out_path],
    }
    if len(texts) > 1:
        # Per-line durations let the backend split this track without Whisper.
        try:
            result["lines"] = [
                round(float(chunk.shape[-1]) / float(model.sr), 3) for chunk in chunks
            ]
        except Exception:
            pass
    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
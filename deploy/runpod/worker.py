"""RunPod Serverless worker for the Vidyra AI Studio.

Runs ComfyUI on the GPU pod and drives it through its local HTTP API.
Each job received from the backend carries:
  tool        e.g. "txt2img" | "img2video" | "motion" | "v2v"
  model       e.g. "sdxl", "flux-schnell", "wan21-i2v", "liveportrait", ...
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

import base64
import hashlib
import hmac
import json
import os
import re
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
    "flux-schnell": {"unet": "flux1-schnell.safetensors"},
    "flux-dev": {"unet": "flux1-dev.safetensors"},
    "wan21-i2v": {
        "unet": "wan2.1_i2v_480p_14B_fp8_scaled.safetensors",
        "clip_vision": "clip_vision_h.safetensors",
    },
    "wan21-flf2v": {
        "unet": "wan2.1_flf2v_720p_14B_fp8_e4m3fn.safetensors",
        "clip_vision": "clip_vision_h.safetensors",
    },
    "wan21-v2v": {
        "unet": "wan2.1_i2v_480p_14B_fp8_scaled.safetensors",
    },
    "wan22-cam-i2v": {
        # Wan2.2 Fun Camera Control: two-pass (high/low noise) camera-aware I2V.
        # Provision these from the Comfy-Org Wan_2.2_ComfyUI_Repackaged repo.
        "unet_high": "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors",
        "unet_low": "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors",
        "vae": "wan_2.1_vae.safetensors",
        "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    },
    "animatediff": {
        "motion": "mm_sd_v15_v2.ckpt",
        "ckpt": "v1-5-pruned-emaonly.safetensors",
    },
    "liveportrait": {"ckpt": "liveportrait.ckpt"},
    "wan-animate-2": {
        # Wan-Animate-2 14B (Apache-2.0). Full-body motion transfer from a
        # driving video, no pose/skeleton extraction: the model reads the pose
        # branch straight off the reference video. Files from the
        # Comfy-Org/Wan-Animate-2 repack on the network volume.
        "unet": "wan_animate_2_int8_convrot.safetensors",
        "lora": "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
        "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "clip_vision": "clip_vision_h.safetensors",
        "vae": "Wan2_1_VAE_bf16.safetensors",
    },
    "infinitetalk-multi": {
        # Meigen InfiniteTalk Multi — loads its own checkpoints under ComfyUI
        # custom nodes (models/Fastcam / models/VGGFace etc.); no entry needed.
    },
    # "lipsync" is intentionally absent: Wav2Lip runs directly in the pod (see
    # _run_lipsync) and its checkpoints are baked into the image, so there is
    # nothing to provision on the network volume.
    # 48GB-only character lock: SDXL portrait-consistent restyle.
    # Backed by the proven SDXL img2img graph (no new node contract until the
    # native InstantID graph is smoke-tested on the pod). Files provisioned on
    # the network volume for phase 2 native routing.
    "sdxl-character": {
        "ckpt": "sd_xl_base_1.0.safetensors",
        "ipadapter": "ip-adapter-plus-face_sdxl_vit-h.safetensors",
        "instantid_ip": "ip-adapter.bin",
        "instantid_control": "diffusion_pytorch_model.safetensors",
        "insightface": "antelopev2",
    },
}

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
INPUT_DIR = os.getenv("COMFY_INPUT", "/ComfyUI/input")
OUTPUT_DIR = os.getenv("COMFY_OUTPUT", "/ComfyUI/output")
WAV2LIP_DIR = os.getenv("WAV2LIP_DIR", "/Wav2Lip")
FAKE = os.getenv("RUNPOD_FAKE", "0") == "1"
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "20"))
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")

# Wan-Animate-2 renders in fixed 81-frame windows (4k+1 latent length) and
# stitches them with the node's own `continue_motion` / `video_frame_offset`
# extension path, so a 30s clip is 9 sequential ComfyUI prompts on one resident
# model rather than one impossible 720-frame diffusion pass.
WAN_WINDOW = 81
WAN_OVERLAP = 1
WAN_SHIFT = 5.0
WAN_SAMPLER = "lcm"
WAN_SCHEDULER = "simple"
WAN_STEPS = 6
WAN_CACHE_DEVICE = os.getenv("WAN_CACHE_DEVICE", "gpu")
WAN_CACHE_DTYPE = os.getenv("WAN_CACHE_DTYPE", "int8")
# Upstream's own negative; keeps colour/exposure/compression artefacts out and
# is what the model was trained against.
WAN_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)


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

def _wait_for_comfy():
    for _ in range(120):
        try:
            _http_json(f"{COMFY_URL}/system_stats", timeout=5)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("ComfyUI did not become ready")


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


def _fetch_output(filename: str, dest: str):
    url = f"{COMFY_URL}/view?filename={urllib.parse.quote(filename)}&type=output"
    with urllib.request.urlopen(url, timeout=300) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())


# ---------------------------------------------------------------------------
# Media probing / ffmpeg helpers (used by the multi-window motion path)
# ---------------------------------------------------------------------------

def _run_ff(cmd: list):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


def _parse_rate(value) -> float:
    """ffmpeg's "30000/1001" rational frame rate."""
    try:
        text = str(value or "")
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else 0.0
        return round(float(text), 3)
    except (TypeError, ValueError):
        return 0.0


def probe_video(path: str) -> dict:
    """Best-effort ffprobe. Empty dict when ffprobe is unavailable."""
    try:
        proc = _run_ff([
            FFPROBE, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames",
            "-show_entries", "format=duration",
            "-of", "json", path,
        ])
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    streams = data.get("streams") or [{}]
    stream = streams[0] if isinstance(streams[0], dict) else {}
    meta = data.get("format") or {}
    fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
    try:
        duration = float(meta.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        frames = int(stream.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    if not frames and duration and fps:
        frames = int(round(duration * fps))
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": fps,
        "frames": frames,
        "duration": round(duration, 3),
    }


def _snap(value, lo: int, hi: int) -> int:
    """Wan wants dimensions on a 16px grid."""
    v = max(lo, min(hi, int(value or lo)))
    return max(lo, min(hi, (v // 16) * 16 or 16))


def fit_dimensions(src_w: int, src_h: int, lo: int = 256, hi: int = 1280) -> tuple:
    """Orientation-preserving 16px-grid dimensions for a source frame size.

    Wan-Animate-2 centre-crops both the reference image and the driving clip
    into the output box, so a portrait driving clip rendered into a landscape
    box would slice the body out of frame. Follow the source's orientation and
    only round the long side to the grid.
    """
    src_w, src_h = int(src_w or 0), int(src_h or 0)
    if src_w <= 0 or src_h <= 0:
        return 832, 480
    portrait = src_h > src_w
    long_src, short_src = (src_h, src_w) if portrait else (src_w, src_h)
    long_side = _snap(long_src, lo, hi)
    ratio = short_src / float(long_src)
    short_side = max(lo, _snap(int(round(long_side * ratio)), lo, hi))
    return (short_side, long_side) if portrait else (long_side, short_side)


def _pad_filter(src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    """ffmpeg filter that fits a frame into the output box without cropping.

    Scales to fit inside the box, then pads the remainder with a heavily
    blurred, stretched copy of itself. Solid black would read as a broken
    render; the blur reads as deliberate letterboxing.
    """
    if src_w <= 0 or src_h <= 0:
        return f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2"
    scale = (
        f"scale=w={out_w}:h={out_h}:force_original_aspect_ratio=decrease:force_divisible_by=16"
    )
    pad = f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    # Blurred background plate, then the sharp fit composited on top.
    plate = (
        f"split=2[bg][fg];[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},gblur=sigma=28,eq=brightness=-0.06[bgb];"
        f"[fg]{scale}[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )
    return f"{plate},{pad}"


def probe_image(path: str) -> dict:
    """Width/height of a still, via ffmpeg (Pillow may not be installed)."""
    proc = _run_ff([FFMPEG, "-i", path])
    text = (proc.stderr or "")
    width = height = 0
    m = re.search(r"[, ](\d{2,5})x(\d{2,5})[\s,]", text)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    return {"width": width, "height": height}


def pad_image_to(src: str, dest: str, out_w: int, out_h: int) -> str:
    """Fit a still into out_w x out_h, padding rather than cropping.

    Cropping a full-body reference to 16:9 slices arms and legs off, and the
    animation faithfully reproduces a body with no arms.
    """
    info = probe_image(src)
    vf = _pad_filter(int(info.get("width") or 0), int(info.get("height") or 0),
                     out_w, out_h)
    proc = _run_ff([FFMPEG, "-y", "-i", src, "-vf", vf, "-frames:v", "1", dest])
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed padding the reference image: {(proc.stderr or '')[-400:]}")
    return dest


def pad_video_to(src: str, dest: str, out_w: int, out_h: int, fps: int) -> str:
    """Fit a driving clip into out_w x out_h, padding rather than cropping.

    The pose branch reads this clip, so a padded matte means the model sees
    the same framing the reference photo has and the motion stays in frame.
    """
    info = probe_video(src)
    sw = int(info.get("width") or 0)
    sh = int(info.get("height") or 0)
    vf = _pad_filter(sw, sh, out_w, out_h)
    cmd = [FFMPEG, "-y", "-i", src, "-an", "-vf", vf, "-r", str(int(fps)),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
           "-pix_fmt", "yuv420p", dest]
    proc = _run_ff(cmd)
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed padding the driving clip: {(proc.stderr or '')[-400:]}")
    return dest


def _round4k1(value: int) -> int:
    """Smallest 4k+1 window >= value (Wan latent frames are 4k+1)."""
    return max(5, 4 * ((max(1, value) + 3) // 4) + 1)


def _floor4k1(value: int) -> int:
    """Largest 4k+1 window <= value."""
    return max(1, 4 * ((max(1, value) - 1) // 4) + 1)


def wan_fragment_plan(total_frames: int, available: int = 0) -> list:
    """Split an output length into Wan-Animate-2 windows.

    Window 0 emits every frame it renders. Every later window repeats the
    previous window's last frame as `continue_motion` (the node reports that
    overlap as ``trim_image``), so it only adds ``length - 1`` new frames.
    Because a window length has to stay on the 4k+1 grid, the last window is
    allowed to over-produce by up to 3 frames; the stitcher trims the tail to
    the exact request.

    ``available`` is the driving clip's frame count, so a clip shorter than the
    request shortens the result instead of leaving the tail frozen (Wan holds
    the final pose video frame when it runs out).
    """
    total = max(1, int(total_frames))
    if available:
        total = min(total, max(1, int(available)))
    plan: list = []
    clip_pos = 0
    produced = 0
    while produced < total:
        carry = bool(plan)
        floor = 9 if carry else 5  # a carry window needs length > WAN_OVERLAP
        needed = (total - produced) + (WAN_OVERLAP if carry else 0)
        length = min(WAN_WINDOW, _round4k1(needed))
        if available:
            room = available - clip_pos
            if room < floor:
                break
            length = _floor4k1(min(length, room))
        if length < floor:
            break
        keep = length - (WAN_OVERLAP if carry else 0)
        if keep < 1:
            break
        plan.append({
            "length": length,
            "keep": keep,
            "start": clip_pos,
            "carry": carry,
        })
        clip_pos += length
        produced += keep

    # Trim the 4k+1 rounding surplus off the tail.
    while plan:
        over = produced - total
        if over <= 0:
            break
        if over >= plan[-1]["keep"]:
            produced -= plan.pop()["keep"]
            continue
        plan[-1]["keep"] -= over
        break
    return [frag for frag in plan if frag["keep"] >= 1]


def _newest_output(prefix: str) -> str:
    """Newest file in the output dir starting with prefix (VHS names its
    outputs `<prefix>_00001_.mp4` and we do not want to depend on the history
    payload shape)."""
    best, best_mtime = "", -1.0
    try:
        entries = os.listdir(OUTPUT_DIR)
    except OSError:
        return ""
    for name in entries:
        if not name.startswith(prefix) or not name.lower().endswith((".mp4", ".webm", ".mkv")):
            continue
        full = os.path.join(OUTPUT_DIR, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = name, mtime
    return best


def normalise_window(src: str, dest: str, fps: int, drop_first: int, keep: int) -> str:
    """Re-encode one rendered window to CFR and drop the carried frame.

    Stream-copy concat needs every segment to agree on frame count and timing,
    and the first frame of a continuation window is the previous window's last
    frame (the node's ``trim_image`` overlap), so it has to come off here.
    """
    filters = []
    if drop_first:
        filters.append(f"trim=start_frame={int(drop_first)}")
    filters.append("setpts=N/FRAME_RATE/TB")
    cmd = [FFMPEG, "-y", "-i", src, "-an", "-vf", ",".join(filters),
           "-r", str(int(fps)), "-frames:v", str(int(keep))]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", dest]
    proc = _run_ff(cmd)
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed normalising window: {(proc.stderr or '')[-400:]}")
    return dest


def extract_last_frame(src: str, dest: str, keep: int) -> str:
    """Pull the final frame of a normalised window for the next
    `continue_motion`. The caller knows the frame count exactly, so this is a
    deterministic frame pick rather than a seek to the end."""
    last = max(0, int(keep) - 1)
    cmd = [FFMPEG, "-y", "-i", src, "-an", "-vsync", "0",
           "-vf", f"select=eq(n\\,{last})", "-frames:v", "1", "-update", "1", dest]
    proc = _run_ff(cmd)
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed extracting carry frame: {(proc.stderr or '')[-400:]}")
    return dest


def concat_windows(parts: list, dest: str) -> str:
    """Join already-normalised windows without re-encoding."""
    listing = dest + ".txt"
    with open(listing, "w", encoding="utf-8") as fh:
        for part in parts:
            fh.write(f"file '{os.path.abspath(part)}'\n")
    proc = _run_ff([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", "-movflags", "+faststart", dest])
    try:
        os.remove(listing)
    except OSError:
        pass
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed joining windows: {(proc.stderr or '')[-400:]}")
    return dest


def resample_driving(src: str, dest: str, fps: int, seconds: float = 0.0) -> str:
    """Put the driving clip on the output frame rate.

    Frame indices in `continue_motion`/`video_frame_offset` and the output
    frame count are both derived from this file, so it must be constant-rate
    before it reaches ComfyUI.
    """
    cmd = [FFMPEG, "-y", "-i", src, "-an", "-r", str(int(fps))]
    if seconds > 0:
        cmd += ["-t", f"{float(seconds):.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p", dest]
    proc = _run_ff(cmd)
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f"ffmpeg failed resampling the driving clip: {(proc.stderr or '')[-400:]}")
    return dest


def _vhs_load_video(name: str, frame_cap: int = 0, skip_first: int = 0,
                    force_rate: int = 0) -> dict:
    """VHS_LoadVideo inputs.

    Current VideoHelperSuite exposes `frame_load_cap`/`skip_first_frames`; the
    older `video_load_cap` name and the `fps` widget on VHS_VideoCombine are
    gone, and ComfyUI rejects a graph that names an input a node no longer has.
    Kept in one place so a pack upgrade is a one-line change.
    """
    return {
        "video": name,
        "force_rate": int(force_rate or 0),
        "custom_width": 0,
        "custom_height": 0,
        "frame_load_cap": int(frame_cap or 0),
        "skip_first_frames": int(skip_first or 0),
        "select_every_nth": 1,
    }


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
    cfg = float(p.get("cfg", 1.0 if model.startswith("flux") else 7.0))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    negative = str(p.get("negative", ""))
    files = params.get("model_files") or MODEL_FILES.get(model, {})

    if model in ("flux-schnell", "flux-dev"):
        # FLUX via UNETLoader + CLIPLoader (t5v + clip_l fp8) + diffuse
        return {
            "4": {"class_type": "UNETLoader", "inputs": {"unet_name": files.get("unet", "flux1-schnell.safetensors"), "weight_dtype": "default"}},
            "10": {"class_type": "LoraLoader", "inputs": {"model": ["4", 0], "clip": ["11", 0], "lora_name": None, "strength_model": 1.0, "strength_clip": 1.0}},
            "11": {"class_type": "CLIPLoader", "inputs": {"clip_name1": "t5xxl_fp8_e4m3fn.safetensors", "clip_name2": "clip_l.safetensors", "type": "flux", "device": "default"}},
            "12": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1], "text": positive}},
            "13": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
            "14": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["12", 0], "negative": ["12", 0], "latent_image": ["13", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
            "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["16", 0]}},
            "16": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "17": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": "vidyra_txt2img"}},
        }
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


def _build_img2video(params: dict) -> dict:
    p = params.get("params") or {}
    model = params.get("model")
    w, h = int(p.get("width", 832)), int(p.get("height", 480))
    frames = int(p.get("frames", 81))
    fps = int(p.get("fps", 16))
    steps = int(p.get("steps", 20))
    cfg = float(p.get("cfg", 5.0))
    seed = int(p.get("seed", 0))
    denoise = float(p.get("denoise", 1.0))
    positive = params.get("prompt", "")
    files = params.get("model_files") or MODEL_FILES.get(model, {})
    image_name = params.get("ref_files", {}).get("image", "SOURCE_IMAGE.png")

    if model == "animatediff":
        motion = files.get("motion", "mm_sd_v15_v2.ckpt")
        ckpt = files.get("ckpt", "v1-5-pruned-emaonly.safetensors")
        # Canonical ComfyUI-AnimateDiff-Evolved image-to-video graph.
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "2": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "3": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["2", 0]}},
            "4": {"class_type": "VAEEncode", "inputs": {"pixels": ["3", 0], "vae": ["1", 2]}},
            "5": {"class_type": "ADE_StandardAnimateDiff", "inputs": {"model": ["1", 0], "motion_model": motion, "motion_lora": None, "scale_multival": None, "effect_multival": None, "ad_keyframes": None, "sample_settings": None}},
            "6": {"class_type": "ADE_UseEvolvedSampling", "inputs": {"model": ["5", 0], "positive": ["7", 0], "negative": ["7", 0], "latent_image": ["4", 0], "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "karras", "denoise": denoise}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["7", 0], "negative": ["7", 0], "latent_image": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "karras", "denoise": denoise}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
            "10": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["9", 0], fps, "vidyra_i2v")},
        }
    if model == "wan22-cam-i2v":
        # Wan2.2 Fun Camera Control: cinematic camera motion from a still image.
        # Mirrors ComfyUI's official "Wan 2.2 Fun Camera Control" workflow —
        # a camera trajectory (pan/zoom/rotate) built by WanCameraEmbedding is
        # stamped into the conditioning of WanCameraImageToVideo, and sampling
        # runs a two-pass denoise (high-noise model steps 0..half, low-noise
        # model half..end) to keep the subject stable while the camera moves.
        unet_high = files.get("unet_high", "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors")
        unet_low = files.get("unet_low", "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors")
        vae = files.get("vae", "wan_2.1_vae.safetensors")
        clip = files.get("clip", "umt5_xxl_fp8_e4m3fn_scaled.safetensors")
        camera_pose = str(p.get("camera_pose") or "").strip() or "Zoom In"
        camera_speed = float(p.get("camera_speed") or 1.0)
        half = max(1, steps // 2)
        return {
            # Two-pass diffusion models + per-model sampling shift (Wan2.2).
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_high, "weight_dtype": "default"}},
            "2": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_low, "weight_dtype": "default"}},
            "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8}},
            "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": 8}},
            "5": {"class_type": "CLIPLoader", "inputs": {"clip_name1": clip, "clip_name2": "", "type": "wan", "device": "default"}},
            "6": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": positive}},
            "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": str(p.get("negative", ""))}},
            "9": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "10": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["9", 0]}},
            # Camera trajectory: pose (pan/zoom/orbit), length, and speed.
            "11": {"class_type": "WanCameraEmbedding", "inputs": {"camera_pose": camera_pose, "width": w, "height": h, "length": frames, "speed": camera_speed}},
            "12": {"class_type": "WanCameraImageToVideo", "inputs": {
                "positive": ["7", 0], "negative": ["8", 0], "vae": ["6", 0],
                "width": w, "height": h, "length": frames, "batch_size": 1,
                "start_image": ["10", 0], "camera_conditions": ["11", 0],
            }},
            # High-noise pass establishes motion/structure (steps 0..half).
            "13": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["3", 0], "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["12", 2], "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": 0, "end_at_step": half, "return_with_leftover_noise": "enable"}},
            # Low-noise pass refines detail (steps half..end), fixing flicker.
            "14": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1], "latent_image": ["13", 0], "add_noise": "disable", "noise_seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "start_at_step": half, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
            "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["6", 0]}},
            "16": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["15", 0], fps, "vidyra_cam")},
        }
    if model in ("wan21-i2v", "wan21-flf2v"):
        # Wan2.1 image-to-video using ComfyUI's native Wan nodes.
        #   wan21-i2v   : single start frame (+ optional reference clip-vision guide)
        #   wan21-flf2v : first + last frame interpolation (WanFirstLastFrameToVideo)
        unet = files.get("unet", "wan2.1_i2v_480p_14B_fp8_scaled.safetensors")
        vision = files.get("clip_vision", "clip_vision_h.safetensors")
        refs = params.get("ref_files", {})
        start_name = refs.get("image", "START.png")
        end_name = refs.get("end_image")
        reference_name = refs.get("reference")

        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name1": "umt5_xxl_fp8_e4m3fn.safetensors", "clip_name2": "", "type": "wan", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.1_vae.safetensors"}},
            "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": vision}},
            "5": {"class_type": "LoadImage", "inputs": {"image": start_name}},
            "6": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["5", 0]}},
        }

        # Clip-vision guide: use the reference image if given, else the start frame.
        guide_node = "6"
        if reference_name:
            graph["11"] = {"class_type": "LoadImage", "inputs": {"image": reference_name}}
            graph["12"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["11", 0]}}
            guide_node = "12"
        graph["13"] = {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["4", 0], "image": [guide_node, 0], "crop": "center"}}

        if model == "wan21-flf2v" and end_name:
            graph["14"] = {"class_type": "LoadImage", "inputs": {"image": end_name}}
            graph["15"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["14", 0]}}
            graph["7"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {
                "width": w, "height": h, "length": frames, "batch_size": 1,
                "positive": positive, "negative": "",
                "vae": ["3", 0],
                "start_image": ["6", 0], "end_image": ["15", 0],
                "clip_vision_start_image": ["13", 0],
            }}
        else:
            graph["7"] = {"class_type": "WanImageToVideo", "inputs": {
                "width": w, "height": h, "length": frames, "batch_size": 1,
                "positive": positive, "negative": "",
                "vae": ["3", 0],
                "start_image": ["6", 0],
                "clip_vision_output": ["13", 0],
            }}

        graph["8"] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["7", 2], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}}
        graph["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
        graph["10"] = {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["9", 0], fps, "vidyra_i2v")}
        return graph
    raise ValueError(f"Unsupported img2video model: {model}")


def _build_wan_animate(params: dict, length: int, start: int, carry: str = "") -> dict:
    """One Wan-Animate-2 window, as a native ComfyUI graph.

    Wan-Animate-2 (Apache-2.0, core ComfyUI nodes) animates the person in a
    reference image using a driving video: facial animation, body motion and
    hand gestures are read straight off that clip, with no pose/skeleton
    extraction step.

    Windows are chained through the node's own extension inputs rather than a
    ComfyUI loop: `continue_motion` takes the previous window's last frame and
    `video_frame_offset`/`skip_first_frames` advance the driving clip, so each
    window is an independent prompt that the pod stitches together. The
    reference image is re-sent every window, which is what keeps identity
    stable across a long clip.
    """
    p = params.get("params") or {}
    files = params.get("model_files") or MODEL_FILES["wan-animate-2"]
    refs = params.get("ref_files") or {}
    width = _snap(p.get("width") or 832, 256, 1280)
    height = _snap(p.get("height") or 480, 256, 1280)
    fps = int(p.get("fps") or 24)
    steps = int(p.get("steps") or WAN_STEPS)
    cfg = float(p.get("cfg") or 1.0)
    seed = int(p.get("seed") or 0)
    positive = str(params.get("prompt") or "")
    negative = str(p.get("negative") or WAN_NEGATIVE)
    target = refs.get("image") or "TARGET.png"
    driving = refs.get("video") or "DRIVING.mp4"
    cache_device = str(p.get("cache_device") or WAN_CACHE_DEVICE)
    cache_dtype = str(p.get("cache_dtype") or WAN_CACHE_DTYPE)

    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": files.get("unet", MODEL_FILES["wan-animate-2"]["unet"]),
            "weight_dtype": "default"}},
        # LightX2V step-distill LoRA is what makes 4-6 steps viable.
        "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0],
            "lora_name": files.get("lora", MODEL_FILES["wan-animate-2"]["lora"]),
            "strength_model": float(p.get("lora_strength", 1.0))}},
        # Runs the pose branch once per window instead of on every step:
        # roughly halves the time for ~6GB of VRAM (int8) or ~6GB of RAM (cpu).
        "3": {"class_type": "WanAnimate2Cache", "inputs": {
            "model": ["2", 0], "device": cache_device, "dtype": cache_dtype}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["3", 0], "shift": WAN_SHIFT}},
        "5": {"class_type": "CLIPLoader", "inputs": {
            "clip_name1": files.get("clip", MODEL_FILES["wan-animate-2"]["clip"]),
            "clip_name2": "", "type": "wan", "device": "default"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": positive}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["5", 0], "text": negative}},
        "8": {"class_type": "CLIPVisionLoader", "inputs": {
            "clip_name": files.get("clip_vision", MODEL_FILES["wan-animate-2"]["clip_vision"])}},
        "9": {"class_type": "LoadImage", "inputs": {"image": target}},
        "10": {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["8", 0], "image": ["9", 0], "crop": "none"}},
        "11": {"class_type": "VAELoader", "inputs": {
            "vae_name": files.get("vae", MODEL_FILES["wan-animate-2"]["vae"])}},
        "12": {"class_type": "VHS_LoadVideo", "inputs": _vhs_load_video(
            driving, frame_cap=length, skip_first=start)},
        "20": {"class_type": "WanAnimate2ToVideo", "inputs": {
            "positive": ["6", 0],
            "negative": ["7", 0],
            "vae": ["11", 0],
            "width": width,
            "height": height,
            "length": int(length),
            "batch_size": 1,
            "reference_image": ["9", 0],
            "pose_video": ["12", 0],
            "clip_vision_output": ["10", 0],
            "video_frame_offset": 0,
            "pose_strength": float(p.get("pose_strength", 1.0)),
            "pose_start_percent": float(p.get("pose_start_percent", 0.0)),
            "pose_end_percent": float(p.get("pose_end_percent", 1.0)),
            "reference_image_strength": float(p.get("reference_image_strength", 1.0))}},
        # The reference frame is prepended to the latent, so trim it before the
        # decode or every window leads with a duplicate of the still.
        "13": {"class_type": "TrimVideoLatent", "inputs": {
            "samples": ["20", 2], "trim_amount": ["20", 3]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["11", 0]}},
        "15": {"class_type": "BasicScheduler", "inputs": {
            "model": ["4", 0], "scheduler": WAN_SCHEDULER, "steps": steps, "denoise": 1.0}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": WAN_SAMPLER}},
        "17": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "18": {"class_type": "CFGGuider", "inputs": {
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "cfg": cfg}},
        "19": {"class_type": "SamplerCustom", "inputs": {
            "add_noise": True,
            "noise": ["17", 0],
            "guider": ["18", 0],
            "sampler": ["16", 0],
            "sigmas": ["15", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["20", 2],
            "disable_noise": False}},
        "21": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(
            ["14", 0], fps, params.get("output_prefix") or "vidyra_wan2")},
    }
    if carry:
        graph["22"] = {"class_type": "LoadImage", "inputs": {"image": carry}}
        graph["20"]["inputs"]["continue_motion"] = ["22", 0]
    return graph


def _build_motion(params: dict) -> dict:
    """Motion transfer dispatcher.

    ``wan-animate-2`` is the full-body path (target photo + driving video, no
    pose extraction) and needs several windows stitched by
    ``_run_motion_wan``. ``liveportrait`` is the older face-only path and stays
    available for head-and-shoulders shots.
    """
    model = params.get("model") or "liveportrait"
    if model == "wan-animate-2":
        # Only reachable when a caller wants a single window directly; the pod's
        # handler normally routes this model through _run_motion_wan.
        return _build_wan_animate(params, length=WAN_WINDOW, start=0)

    p = params.get("params") or {}
    files = params.get("model_files") or MODEL_FILES.get("liveportrait", {})
    ckpt = files.get("ckpt", "liveportrait.ckpt")
    target = (params.get("ref_files") or {}).get("image", "TARGET.png")
    driving = (params.get("ref_files") or {}).get("video", "DRIVING.mp4")
    fps = int(p.get("fps") or 24)
    frames = int(p.get("frames") or 0)
    load = _vhs_load_video(driving, frame_cap=frames)
    return {
        "1": {"class_type": "LoadFaceModel", "inputs": {"model_name": ckpt}},
        "2": {"class_type": "LoadImage", "inputs": {"image": target}},
        # Load the whole driving clip, not just its first frame: the point of
        # the tool is that the face actually moves over time.
        "3": {"class_type": "VHS_LoadVideo", "inputs": load},
        "4": {"class_type": "LivePortraitProcessFace", "inputs": {"face_model": ["1", 0], "image": ["2", 0]}},
        # Driving faces come from the driving video. Feeding node 4 into both
        # slots made every frame identical to the source and produced a still.
        "5": {"class_type": "LivePortraitProcessFace", "inputs": {"face_model": ["1", 0], "image": ["3", 0]}},
        "6": {"class_type": "LivePortraitTransferMotion", "inputs": {"src_face": ["4", 0], "driving_face": ["5", 0]}},
        "7": {"class_type": "LivePortraitRender", "inputs": {"face_model": ["1", 0], "transferred": ["6", 0], "target_image": ["2", 0], "driving_video": ["3", 0]}},
        "8": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["7", 0], fps, "vidyra_motion")},
    }


def _build_v2v(params: dict) -> dict:
    p = params.get("params") or {}
    w, h = int(p.get("width", 832)), int(p.get("height", 480))
    frames = int(p.get("frames", 81))
    fps = int(p.get("fps", 16))
    steps = int(p.get("steps", 20))
    cfg = float(p.get("cfg", 5.0))
    seed = int(p.get("seed", 0))
    denoise = float(p.get("denoise", 0.7))
    positive = params.get("prompt", "")
    files = params.get("model_files") or MODEL_FILES.get("wan21-v2v", {})
    video_name = params.get("ref_files", {}).get("video", "SOURCE.mp4")
    unet = files.get("unet", "wan2.1_i2v_480p_14B_fp8_scaled.safetensors")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name1": "umt5_xxl_fp8_e4m3fn.safetensors", "clip_name2": "", "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.1_vae.safetensors"}},
        "4": {"class_type": "LoadVideo", "inputs": {"video": video_name}},
        "5": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["4", 0]}},
        "6": {"class_type": "WanVideoToVideo", "inputs": {"width": w, "height": h, "length": frames, "batch_size": 1, "positive": positive, "negative": "", "video": ["5", 0], "clip": ["2", 0]}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["6", 0], "negative": ["6", 1], "latent_image": ["6", 2], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["8", 0], fps, "vidyra_v2v")},
    }


def _build_lip_sync(params: dict) -> dict:
    """Talking head: animate a character portrait with an audio track.

    Targets the Meigen InfiniteTalk Multi ComfyUI custom-node pack
    (https://github.com/MeigenAI/ComfyUI-InfiniteTalkMulti). The graph follows
    the pack's documented dual-speaker workflow: load the portrait, load the
    audio with a clip, build the InfiniteTalkMulti model, and animate.

    NOTE: this graph is authored to the InifiniteTalkMulti node contract and
    MUST be smoke-tested once on a real GPU pod against the installed custom
    nodes (and its companion checkpoints), like the other builders.
    """
    p = params.get("params") or {}
    w, h = int(p.get("width", 832)), int(p.get("height", 480))
    frames = int(p.get("frames", 81))
    fps = int(p.get("fps", 24))
    steps = int(p.get("steps", 20))
    cfg = float(p.get("cfg", 5.0))
    seed = int(p.get("seed", 0))
    positive = params.get("prompt", "")
    refs = params.get("ref_files", {})
    portrait = refs.get("image", "PORTRAIT.png")
    audio = refs.get("audio", "SPEECH.mp3")

    return {
        # Audio -> CLIP (shorter clip clips should be audio-length; 240s cap)
        "1": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
        "2": {"class_type": "CLIPAudioEncode", "inputs": {"clip": ["6", 1], "audio": ["1", 0]}},
        # Portrait load + resize to the target frame
        "3": {"class_type": "LoadImage", "inputs": {"image": portrait}},
        "4": {"class_type": "ImageScaleToTotalPixels", "inputs": {"upscale_method": "lanczos", "megapixels": (w * h) / 1e6, "image": ["3", 0]}},
        # Text prompt (optional) + dual-model setup
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["6", 0], "text": positive}},
        "6": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "mochi_clip_fp32.safetensors", "clip_name2": "mochi_clip_fp32.safetensors", "type": "infinitetalk", "device": "default"}},
        "7": {"class_type": "UNETLoader", "inputs": {"unet_name": "infiniteTalkMultiNoisePredict.safetensors", "weight_dtype": "default"}},
        "8": {"class_type": "InfiniteTalkMulti", "inputs": {
            "model1": ["7", 0], "clip": ["6", 0],
            "steps": steps, "cfg": cfg, "seed": seed,
            "width": w, "height": h, "fps": fps,
            "ask_dro": False, "second_dropovo": True,
            "prompt": ["5", 0], "negative": ["5", 0],
            "clip_audio": ["2", 0],
            "dropova": True, "dropovb": None,
            "num_workers": 1,
            "input_image1": ["4", 0],
        }},
        "9": {"class_type": "VAELoader", "inputs": {"vae_name": "moonshine_vae.safetensors"}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["9", 0]}},
        "11": {"class_type": "VHS_VideoCombine", "inputs": _vhs_combine(["10", 0], fps, "vidyra_lip")},
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
    if tool == "motion":
        return _build_motion(params)
    if tool == "v2v":
        return _build_v2v(params)
    if tool == "lip":
        return _build_lip_sync(params)
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
    if base and art_path:
        _post_file(f"{base}{art_path}", out_path, token)
        uploaded.append(os.path.basename(out_path))
    report(95, "Done")
    return {
        "status": "ok",
        "elapsed": round(time.time() - start, 1),
        "uploads": uploaded,
        "checkpoint": os.path.basename(ckpt),
    }


def _run_motion_wan(inputs, base, art_path, token, report, start):
    """Full-body motion transfer via Wan-Animate-2, run window by window.

    Wan-Animate-2 renders a fixed 81-frame window, so a 10-30s clip is 3-9
    sequential prompts against one already-resident model rather than one
    impossible 720-frame diffusion pass. Each window after the first repeats
    the previous window's last frame through the node's `continue_motion`
    input, which is the model's own video-extension path; the windows are then
    stitched losslessly with ffmpeg.

    Why this runs in the worker rather than through `build_workflow`: it needs
    ffmpeg between prompts, per-window progress, and a fallback if the pose
    cache does not fit in VRAM. Same callback/artifact transport as the other
    pod-side tools.
    """
    run_id = str(inputs.get("run_id") or "x")
    p = dict(inputs.get("params") or {})
    run_dir = os.path.join(INPUT_DIR, f"run_{run_id}")
    report(10, "Fetching source media")
    staged = _fetch_refs(inputs.get("refs"), run_dir)
    by_role: dict = {}
    for role, dest in staged:
        by_role.setdefault(role or "", dest)
    target = by_role.get("image") or by_role.get("reference")
    driving = by_role.get("video")
    if not target or not driving:
        return {"status": "error",
                "error": "Motion transfer needs a target photo (image) and a driving video (video)."}

    info = probe_video(driving)
    # fps 0 = match the driving clip, so motion speed is preserved instead of
    # being resampled (which would slow a 30fps clip to 0.8x at 24fps).
    fps = int(p.get("fps") or 0) or int(round(info.get("fps") or 0)) or 24
    duration = float(p.get("duration") or 0)
    want = int(round(duration * fps)) if duration else int(p.get("frames") or 0)

    work = os.path.join(OUTPUT_DIR, f"wan_{run_id}")
    os.makedirs(work, exist_ok=True)
    try:
        report(12, "Preparing the driving clip")
        cfr = resample_driving(driving, os.path.join(work, "drive_cfr.mp4"), fps)
        cinfo = probe_video(cfr)
        available = int(cinfo.get("frames") or 0)
        if not want:
            want = available or WAN_WINDOW
        want = max(1, min(want, 900))
        if available:
            want = min(want, available)

        plan = wan_fragment_plan(want, available)
        if not plan:
            return {"status": "error",
                    "error": "Could not plan any Wan Animate 2 window for this clip."}

        if p.get("width") and p.get("height"):
            width, height = int(p["width"]), int(p["height"])
        else:
            width, height = fit_dimensions(
                int(cinfo.get("width") or info.get("width") or 0),
                int(cinfo.get("height") or info.get("height") or 0),
            )

        # "crop" (default) lets the model centre-crop into the box, which
        # fills the frame but can cut the subject. "pad" fits both inputs
        # whole onto a blurred matte first, so a forced ratio never amputates
        # anyone's arms or legs.
        fit_mode = str(p.get("fit") or "crop").strip().lower()
        if fit_mode == "pad":
            report(13, f"Fitting source media into {width}x{height} without cropping")
            target = pad_image_to(target, os.path.join(work, "target_pad.png"), width, height)
            cfr = pad_video_to(cfr, os.path.join(work, "drive_pad.mp4"), width, height, fps)
            cinfo = probe_video(cfr)

        target_name = _upload_input(target)
        kept = sum(frag["keep"] for frag in plan)
        seconds = round(kept / fps, 2)
        note = ""
        if available and want < round((duration or seconds) * fps):
            note = f" (clip is {round(available / fps, 1)}s)"
        report(14, f"Wan Animate 2: {len(plan)} window{'s' if len(plan) > 1 else ''} "
                   f"-> {seconds}s at {width}x{height}, {fps}fps{note}")

        carry = ""
        parts: list = []
        for index, frag in enumerate(plan):
            prefix = f"vidyra_wan2_{run_id}_w{index}_"
            graph_params = {
                "model": "wan-animate-2",
                "prompt": inputs.get("prompt") or "",
                "params": {**p, "width": width, "height": height, "fps": fps,
                           "output_prefix": prefix},
                "ref_files": {"image": target_name, "video": os.path.basename(cfr)},
                "model_files": inputs.get("model_files") or None,
            }
            report(16 + int(62 * index / len(plan)),
                   f"Wan Animate 2 window {index + 1}/{len(plan)} "
                   f"({frag['length']} frames, {p.get('steps') or WAN_STEPS} steps)")
            try:
                _run_workflow(_build_wan_animate(graph_params, frag["length"],
                                                 frag["start"], carry))
            except RuntimeError as first:
                # The pose cache is the only thing here sized against VRAM;
                # fall back to system RAM before failing the whole run.
                if str(p.get("cache_device") or WAN_CACHE_DEVICE) == "cpu":
                    raise
                report(16 + int(62 * index / len(plan)),
                       "Pose cache did not fit in VRAM, retrying on system RAM")
                graph_params["params"]["cache_device"] = "cpu"
                _run_workflow(_build_wan_animate(graph_params, frag["length"],
                                                 frag["start"], carry))

            produced = _newest_output(prefix)
            if not produced:
                raise RuntimeError(f"Wan Animate 2 window {index + 1} wrote no video")
            part = normalise_window(
                os.path.join(OUTPUT_DIR, produced),
                os.path.join(work, f"part_{index:02d}.mp4"),
                fps,
                1 if frag["carry"] else 0,
                frag["keep"],
            )
            parts.append(part)
            if index + 1 < len(plan):
                carry_png = os.path.join(INPUT_DIR, f"wan_{run_id}_carry_{index}.png")
                extract_last_frame(part, carry_png, frag["keep"])
                carry = _upload_input(carry_png)

        final = concat_windows(parts, os.path.join(OUTPUT_DIR, f"vidyra_motion_{run_id}.mp4"))
        report(90, f"Done: {seconds}s of motion")
        uploaded = []
        if base and art_path:
            _post_file(f"{base}{art_path}", final, token)
        uploaded.append(os.path.basename(final))
        return {
            "status": "ok",
            "elapsed": round(time.time() - start, 1),
            "uploads": uploaded,
            "windows": len(plan),
            "frames": kept,
            "fps": fps,
            "duration": seconds,
        }
    except Exception as exc:
        report(99, f"Worker error: {exc}")
        return {"status": "error", "error": str(exc), "elapsed": round(time.time() - start, 1)}
    finally:
        try:
            import shutil

            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass


def handler(job):
    """RunPod handler — the job.input is the backend's studio run payload."""
    start = time.time()
    inputs = job["input"]
    callback = inputs.get("callback") or {}
    token = callback.get("token", "")
    base = (callback.get("base_url") or "").rstrip("/")
    art_path = callback.get("artifact_path", "")
    prog_path = callback.get("progress_path", "")

    def report(progress: int, message: str):
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

        # Wan-Animate-2 needs ffmpeg between prompts (81-frame windows stitched
        # through the node's continue_motion chain), so it runs pod-side instead
        # of as one ComfyUI graph.
        if inputs.get("tool") == "motion" and inputs.get("model") == "wan-animate-2":
            return _run_motion_wan(inputs, base, art_path, token, report, start)

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
        for name in out_files:
            local_out = os.path.join(OUTPUT_DIR, name)
            if not os.path.isfile(local_out):
                continue
            if base and art_path:
                _post_file(f"{base}{art_path}", local_out, token)
            uploaded.append(name)
    except Exception as exc:
        report(99, f"Worker error: {exc}")
        return {"status": "error", "error": str(exc), "elapsed": round(time.time() - start, 1)}

    report(95, "Done")
    return {
        "status": "ok",
        "elapsed": round(time.time() - start, 1),
        "uploads": uploaded,
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
    if base and art_path:
        _post_file(f"{base}{art_path}", out_path, token)
        uploaded.append(os.path.basename(out_path))
    report(95, "Done")
    result = {"status": "ok", "elapsed": round(time.time() - start, 1), "uploads": uploaded}
    if len(texts) > 1:
        # Per-line durations let the backend split this track without Whisper.
        try:
            result["lines"] = [
                round(float(chunk.shape[-1]) / float(model.sr), 3) for chunk in chunks
            ]
        except Exception:
            pass
    return result
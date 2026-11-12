"""Preflight the current Studio node contract against a running ComfyUI.

The worker validates its graph locally, but ComfyUI is the final authority on
which nodes and inputs exist. This asks ComfyUI what it actually exposes and
reports every missing node, missing input, and missing model file *before* a
paid run starts.

Inside the pod (ComfyUI on 127.0.0.1:8188):
    python /worker_preflight.py

Against a tunnel or remote ComfyUI:
    python deploy/runpod/worker_preflight.py --comfy http://127.0.0.1:8188

Exit code is non-zero when anything required is missing.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

DEFAULT_COMFY = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
DEFAULT_MODEL_VOLUME = os.getenv("MODEL_VOLUME", "/runpod-volume/models")

# node -> inputs this worker's graphs actually set. Anything a node renamed or
# dropped shows up here instead of as an opaque "prompt validation failed" on
# the first paid run.
REQUIRED_NODES = {
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CheckpointLoaderSimple": ["ckpt_name"],
    "CLIPLoader": ["clip_name1", "clip_name2", "type", "device"],
    "CLIPVisionLoader": ["clip_name"],
    "VAELoader": ["vae_name"],
    "LoraLoader": ["model", "clip", "lora_name", "strength_model", "strength_clip"],
    "CLIPTextEncode": ["clip", "text"],
    "CLIPVisionEncode": ["clip_vision", "image", "crop"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "EmptySD3LatentImage": ["width", "height", "batch_size"],
    "LoadImage": ["image"],
    "ImageScaleToTotalPixels": ["upscale_method", "megapixels", "image"],
    "ModelSamplingSD3": ["model", "shift"],
    "KSampler": [
        "model", "positive", "negative", "latent_image", "seed", "steps",
        "cfg", "sampler_name", "scheduler", "denoise",
    ],
    "KSamplerAdvanced": [
        "model", "positive", "negative", "latent_image", "add_noise",
        "noise_seed", "steps", "cfg", "sampler_name", "scheduler",
        "start_at_step", "end_at_step", "return_with_leftover_noise",
    ],
    "WanImageToVideo": [
        "width", "height", "length", "batch_size", "positive", "negative",
        "vae", "start_image", "clip_vision_output",
    ],
    "WanFirstLastFrameToVideo": [
        "width", "height", "length", "batch_size", "positive", "negative",
        "vae", "start_image", "end_image", "clip_vision_start_image",
    ],
    "WanCameraEmbedding": ["camera_pose", "width", "height", "length", "speed"],
    "WanCameraImageToVideo": [
        "positive", "negative", "vae", "width", "height", "length",
        "batch_size", "start_image", "camera_conditions",
    ],
    "VAEDecode": ["samples", "vae"],
    "SaveImage": ["images", "filename_prefix"],
    "VHS_VideoCombine": [
        "images", "frame_rate", "loop_count", "filename_prefix", "format",
        "pingpong", "save_output",
    ],
}

# Renamed upstream; if any of these reappear the worker's helper is stale.
RETIRED_INPUTS = {
    "VHS_VideoCombine": ["fps", "format_mp4"],
}

# MODEL_FILES key -> (loader node, combo input name).
MODEL_INPUTS = {
    "ckpt": ("CheckpointLoaderSimple", "ckpt_name"),
    "unet": ("UNETLoader", "unet_name"),
    "unet_high": ("UNETLoader", "unet_name"),
    "unet_low": ("UNETLoader", "unet_name"),
    "clip": ("CLIPLoader", "clip_name1"),
    "clip1": ("CLIPLoader", "clip_name1"),
    "clip2": ("CLIPLoader", "clip_name2"),
    "clip_vision": ("CLIPVisionLoader", "clip_name"),
    "vae": ("VAELoader", "vae_name"),
}


def load_worker():
    path = pathlib.Path(__file__).resolve().parent / "worker.py"
    spec = importlib.util.spec_from_file_location("runpod_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_object_info(base: str) -> dict:
    url = f"{base.rstrip('/')}/object_info"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach ComfyUI at {url}: {exc}")


def node_input_names(info: dict) -> set:
    names = set()
    for section in ("required", "optional"):
        names |= set((info.get("input") or {}).get(section, {}))
    return names


def check_nodes(object_info: dict) -> list:
    problems = []
    for node, inputs in REQUIRED_NODES.items():
        info = object_info.get(node)
        if not info:
            problems.append(f"missing node: {node}")
            continue
        have = node_input_names(info)
        gone = [name for name in inputs if name not in have]
        if gone:
            problems.append(f"{node}: graph sets inputs the node no longer has: {', '.join(gone)}")
        for name in RETIRED_INPUTS.get(node, []):
            if name in have:
                problems.append(f"{node}: retired input '{name}' is back - update the worker's VHS helper")
    return problems


def check_models(object_info: dict, worker) -> list:
    """Every model file in the worker manifest must exist on the pod."""
    problems = []
    wanted = set()
    for manifest in worker.MODEL_FILES.values():
        for key, filename in manifest.items():
            node_input = MODEL_INPUTS.get(key)
            if node_input:
                wanted.add((node_input[0], node_input[1], filename))

    for node, input_name, filename in sorted(wanted):
        info = object_info.get(node) or {}
        spec = (info.get("input") or {}).get("required", {}).get(input_name)
        options = spec[0] if isinstance(spec, (list, tuple)) and spec else None
        if isinstance(options, list) and options:
            if filename not in options:
                problems.append(
                    f"{filename} is not in ComfyUI's {input_name} list "
                    f"(found {len(options)} files); provision it on the model volume"
                )
        else:
            problems.append(f"{node}.{input_name} is a combo ComfyUI did not enumerate")
    return problems


def check_tools() -> list:
    from shutil import which

    problems = []
    if not which("ffmpeg"):
        problems.append("ffmpeg is not on PATH - Wav2Lip needs it")
    return problems


def check_volume(volume: str) -> list:
    problems = []
    if not os.path.isdir(volume):
        problems.append(
            f"model volume {volume} is not mounted; Studio models cannot be loaded"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy", default=DEFAULT_COMFY)
    parser.add_argument("--model-volume", default=DEFAULT_MODEL_VOLUME)
    parser.add_argument("--skip-models", action="store_true",
                        help="only check the node contract, not model files")
    args = parser.parse_args()

    worker = load_worker()
    print(f"Checking {args.comfy}")
    try:
        stats = json.load(urllib.request.urlopen(f"{args.comfy.rstrip('/')}/system_stats", timeout=15))
    except Exception as exc:
        raise SystemExit(f"ComfyUI is not answering /system_stats: {exc}")
    devices = stats.get("devices") or []
    for device in devices:
        print(f"  GPU: {device.get('name')} ({round(device.get('vram_total', 0) / 1e9, 1)} GB)")

    object_info = fetch_object_info(args.comfy)
    print(f"  {len(object_info)} nodes exposed")

    problems = check_nodes(object_info)
    problems += check_tools()
    if not args.skip_models:
        problems += check_models(object_info, worker)
        problems += check_volume(args.model_volume)

    if problems:
        print("\nPreflight FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nPreflight passed: Studio node contract and models are in place.")
    print(f"  {len(worker.MODEL_FILES)} model families, volume {args.model_volume}")
    print("  next: python /worker.py (or submit a studio run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

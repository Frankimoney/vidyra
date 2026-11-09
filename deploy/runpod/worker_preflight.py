"""Preflight the Wan-Animate-2 node contract against a running ComfyUI.

Wan-Animate-2 rides nodes that upstream still marks experimental, so a graph
that validates locally can be rejected by the pod. This asks ComfyUI what it
actually exposes and reports every missing node, missing input, and missing
model file *before* a paid run starts.

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

# node -> inputs this worker's graph actually sets. Anything a node renamed or
# dropped shows up here instead of as an opaque "prompt validation failed" on
# the first paid run.
REQUIRED_NODES = {
    "WanAnimate2ToVideo": [
        "positive", "negative", "vae", "width", "height", "length", "batch_size",
        "reference_image", "pose_video", "clip_vision_output", "video_frame_offset",
        "pose_strength", "pose_start_percent", "pose_end_percent",
        "reference_image_strength",
    ],
    "WanAnimate2Cache": ["model", "device", "dtype"],
    "TrimVideoLatent": ["samples", "trim_amount"],
    "ModelSamplingSD3": ["model", "shift"],
    "BasicScheduler": ["model", "scheduler", "steps", "denoise"],
    "KSamplerSelect": ["sampler_name"],
    "RandomNoise": ["noise_seed"],
    "CFGGuider": ["model", "positive", "negative", "cfg"],
    "SamplerCustom": [
        "add_noise", "noise", "guider", "sampler", "sigmas", "positive",
        "negative", "latent_image", "disable_noise",
    ],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "LoraLoaderModelOnly": ["model", "lora_name", "strength_model"],
    "CLIPLoader": ["clip_name1", "clip_name2", "type", "device"],
    "CLIPTextEncode": ["clip", "text"],
    "CLIPVisionLoader": ["clip_name"],
    "CLIPVisionEncode": ["clip_vision", "image", "crop"],
    "LoadImage": ["image"],
    "VAELoader": ["vae_name"],
    "VAEDecode": ["samples", "vae"],
    "VHS_LoadVideo": [
        "video", "force_rate", "custom_width", "custom_height",
        "frame_load_cap", "skip_first_frames", "select_every_nth",
    ],
    "VHS_VideoCombine": [
        "images", "frame_rate", "loop_count", "filename_prefix", "format",
        "pingpong", "save_output",
    ],
}

# Renamed upstream; if any of these reappear the worker's helper is stale.
RETIRED_INPUTS = {
    "VHS_LoadVideo": ["video_load_cap", "force_size"],
    "VHS_VideoCombine": ["fps", "format_mp4"],
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
    """Every model file the Wan manifest names must exist on the pod."""
    problems = []
    wanted = {
        "UNETLoader": ("unet_name", worker.MODEL_FILES["wan-animate-2"]["unet"]),
        "LoraLoaderModelOnly": ("lora_name", worker.MODEL_FILES["wan-animate-2"]["lora"]),
        "CLIPLoader": ("clip_name1", worker.MODEL_FILES["wan-animate-2"]["clip"]),
        "CLIPVisionLoader": ("clip_name", worker.MODEL_FILES["wan-animate-2"]["clip_vision"]),
        "VAELoader": ("vae_name", worker.MODEL_FILES["wan-animate-2"]["vae"]),
    }
    for node, (input_name, filename) in wanted.items():
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
    problems = []
    for binary in ("ffmpeg", "ffprobe"):
        from shutil import which

        if not which(binary):
            problems.append(f"{binary} is not on PATH - the multi-window stitcher needs it")
    return problems


def check_volume(volume: str) -> list:
    problems = []
    if not os.path.isdir(volume):
        problems.append(
            f"model volume {volume} is not mounted; WAN models cannot be loaded"
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

    print("\nPreflight passed: Wan-Animate-2 graph contract and models are in place.")
    print(f"  window size {worker.WAN_WINDOW} frames, overlap {worker.WAN_OVERLAP}, "
          f"steps {worker.WAN_STEPS}, sampler {worker.WAN_SAMPLER}")
    print("  next: python /worker.py (or submit a studio motion run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
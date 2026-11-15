#!/usr/bin/env python3
"""Download the current Studio model bundle onto a RunPod network volume.

Run this from a temporary CPU Pod that has the network volume mounted at
/runpod-volume. It is intentionally stdlib-only so it works in a slim Python
image:

    python provision_models.py --dest /runpod-volume/models

Existing non-empty files are skipped, so a retry resumes without re-downloading
completed files. Stale files from earlier bundles (Wan 2.1 video + Fun Camera,
clip-vision) are pruned by default so the volume never has to hold both the old
and new weights at once; pass --no-prune to keep them.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

HF = "https://huggingface.co"
WAN22 = f"{HF}/Comfy-Org/Wan-2.2/resolve/main/split_files"

MODELS = {
    "checkpoints": {
        "sd_xl_base_1.0.safetensors": (
            f"{HF}/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/"
            "sd_xl_base_1.0.safetensors"
        ),
    },
    "diffusion_models": {
        # Wan2.2 14B video: two-pass MoE experts (high/low noise), fp8, on the
        # Wan2.1 16-channel VAE.
        "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors": (
            f"{WAN22}/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        ),
        "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors": (
            f"{WAN22}/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
        ),
        "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors": (
            f"{WAN22}/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
        ),
        "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors": (
            f"{WAN22}/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
        ),
    },
    "text_encoders": {
        "umt5_xxl_fp8_e4m3fn_scaled.safetensors": (
            f"{WAN22}/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        ),
    },
    "vae": {
        "wan_2.1_vae.safetensors": f"{WAN22}/vae/wan_2.1_vae.safetensors",
    },
}

# Files from earlier bundles that are no longer referenced by any workflow.
# Globs are relative to --dest.
STALE = (
    "diffusion_models/wan2.1_*.safetensors",
    "diffusion_models/wan2.2_fun_*.safetensors",
    "clip_vision/clip_vision_h.safetensors",
)


def prune(dest: Path) -> None:
    for pattern in STALE:
        for target in sorted(dest.glob(pattern)):
            size = target.stat().st_size
            target.unlink()
            print(f"prune {target.relative_to(dest)} ({size / 1e9:.1f} GB)")
    for part in sorted(dest.glob("**/*.part")):
        part.unlink()
        print(f"prune {part.relative_to(dest)} (partial)")


def download(url: str, target: Path, force: bool) -> None:
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"skip {target.name} ({target.stat().st_size / 1e9:.1f} GB)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    if part.exists():
        part.unlink()
    print(f"fetch {target.name}")
    request = urllib.request.Request(url, headers={"User-Agent": "VidyraProvision/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with part.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 / total
                    print(f"\r  {pct:5.1f}%  {done / 1e9:6.1f}/{total / 1e9:.1f} GB", end="")
        print()
    if total and part.stat().st_size != total:
        raise RuntimeError(f"{target.name}: expected {total} bytes, got {part.stat().st_size}")
    os.replace(part, target)
    print(f"done {target.name} ({target.stat().st_size / 1e9:.1f} GB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=Path("/runpod-volume/models"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--no-prune",
        dest="prune",
        action="store_false",
        help="keep stale Wan 2.1 / Fun Camera / clip-vision files instead of deleting them",
    )
    args = parser.parse_args()

    if args.prune:
        prune(args.dest)

    total = 0
    for kind, files in MODELS.items():
        for name, url in files.items():
            target = args.dest / kind / name
            download(url, target, args.force)
            total += target.stat().st_size

    print(f"\nBundle ready in {args.dest} ({total / 1e9:.1f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env bash
# Symlink models from the mounted network volume into ComfyUI's model dirs.
# Place checkpoints/loras under $MODEL_VOLUME/<kind>/ then run this.
#
# Current Studio bundle (keep these exact filenames; they are the names in
# worker.py's MODEL_FILES manifest):
#   checkpoints/     sd_xl_base_1.0.safetensors
#   diffusion_models/ wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
#                    wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
#                    wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
#                    wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
#   text_encoders/   umt5_xxl_fp8_e4m3fn_scaled.safetensors
#   vae/             wan_2.1_vae.safetensors
# worker_preflight.py verifies this bundle before a job is accepted.
set -e

MODEL_VOLUME="${MODEL_VOLUME:-/runpod-volume/models}"
COMFY_ROOT="${COMFY_ROOT:-/ComfyUI}"

kinds=("checkpoints" "loras" "vae" "embeddings" "clip" "clip_vision" "unet" "diffusion_models" "text_encoders")

for kind in "${kinds[@]}"; do
  src="$MODEL_VOLUME/$kind"
  dst="$COMFY_ROOT/models/$kind"
  [ -d "$src" ] || continue
  mkdir -p "$dst"
  for f in "$src"/*; do
    b="$(basename "$f")"
    [ -e "$dst/$b" ] || ln -s "$f" "$dst/$b"
  done
done

echo "Model links ready from $MODEL_VOLUME"

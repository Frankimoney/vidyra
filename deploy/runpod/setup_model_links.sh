#!/usr/bin/env bash
# Symlink models from the mounted network volume into ComfyUI's model dirs.
# Place checkpoints/loras under $MODEL_VOLUME/<kind>/ then run this.
#
# Wan-Animate-2 bundle layout (~25.6 GB) — keep these exact filenames, they
# are the names in worker.py's MODEL_FILES["wan-animate-2"] manifest:
#   unet/            wan_animate_2_int8_convrot.safetensors
#   loras/           lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors
#   text_encoders/   umt5_xxl_fp8_e4m3fn_scaled.safetensors
#   clip_vision/     clip_vision_h.safetensors
#   vae/             Wan2_1_VAE_bf16.safetensors
# worker_preflight.py verifies this bundle before a job is accepted.
set -e

MODEL_VOLUME="${MODEL_VOLUME:-/runpod-volume/models}"
COMFY_ROOT="${COMFY_ROOT:-/ComfyUI}"

kinds=("checkpoints" "loras" "vae" "embeddings" "clip" "clip_vision" "unet" "diffusion_models" "text_encoders" "ipadapter" "instantid" "insightface" "controlnet")

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
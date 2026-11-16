#!/usr/bin/env bash
# Boot ComfyUI headless in the background, then run the RunPod worker.
set -e

# Symlink models from the network volume into ComfyUI's model tree.
if [ -d "$MODEL_VOLUME" ]; then
  bash /setup_model_links.sh
fi

: "${COMFY_PORT:=8188}"

# Start ComfyUI (no UI needed). 48GB-only pod: highvram, no offload stalls.
# IMPORTANT: Unset PYTHONPATH for ComfyUI! /app/backend/app has an __init__.py
# (regular package), which PEP 420 prioritises over ComfyUI's own /ComfyUI/app
# namespace package when /app/backend is on PYTHONPATH, causing ComfyUI to crash
# at boot with "ModuleNotFoundError: No module named 'app.logger'".
(
  cd /ComfyUI
  nohup env -u PYTHONPATH python /ComfyUI/main.py \
    --listen 127.0.0.1 \
    --port "$COMFY_PORT" \
    --disable-auto-launch \
    --highvram \
    >/tmp/comfyui.log 2>&1 &
  echo "ComfyUI starting on port $COMFY_PORT (pid $!)"
)

# Do NOT block here waiting for ComfyUI. A RunPod serverless worker must call
# runpod.serverless.start promptly or RunPod marks it unhealthy and restarts
# it, which leaves every job stuck IN_QUEUE while the GPU still bills. The
# worker waits for ComfyUI per job instead (worker.py:_wait_for_comfy), and
# ComfyUI has the whole cold-start window to come up in parallel.

# One pod runs everything: the film pipeline drives ComfyUI in-process
# (STUDIO_LOCAL=1) and standalone Studio runs hit the same handler.
exec python /app/merged_handler.py
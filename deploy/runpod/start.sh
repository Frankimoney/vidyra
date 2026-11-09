#!/usr/bin/env bash
# Boot ComfyUI headless in the background, then run the RunPod worker.
set -e

# Symlink models from the network volume into ComfyUI's model tree.
if [ -d "$MODEL_VOLUME" ]; then
  bash /setup_model_links.sh
fi

: "${COMFY_PORT:=8188}"

# Start ComfyUI (no UI needed). 48GB-only pod: highvram, no offload stalls.
nohup python /ComfyUI/main.py \
  --listen 127.0.0.1 \
  --port "$COMFY_PORT" \
  --disable-auto-launch \
  --highvram \
  >/tmp/comfyui.log 2>&1 &

echo "ComfyUI starting on port $COMFY_PORT (pid $!)"

# Wait until ComfyUI answers /system_stats (up to 10 min for cold model load).
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${COMFY_PORT}/system_stats" >/dev/null 2>&1; then
    echo "ComfyUI is ready after ${i}0s."
    break
  fi
  sleep 10
done

exec python /worker.py
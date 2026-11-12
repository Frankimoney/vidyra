# RunPod Serverless worker for Vidyra AI Studio (ComfyUI + GPU models)
#
# Build:  docker build -t vidyra/runpod-worker -f deploy/runpod/Dockerfile .
# Then create a serverless endpoint on RunPod pointing at this image.
#
# Models live on a RunPod network volume mounted at /runpod-volume/models and
# are symlinked into ComfyUI's model folders (see setup_model_links.sh).
# Install them once with the model install script into the same volume.

FROM runpod/base:2.1.0-cuda1281-ubuntu2404

# System deps for ComfyUI + ffmpeg + python packages
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
      git curl ffmpeg libgl1 libglib2.0-0 libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /

# Serverless runtime + faster-whisper are needed by worker.py itself, not by
# ComfyUI, so install them before the large model/custom-node layers.
COPY deploy/runpod/requirements.txt /tmp/runpod-requirements.txt
RUN pip install --no-cache-dir -r /tmp/runpod-requirements.txt

# ---- ComfyUI -----------------------------------------------------------
# Pinned, not main: Wan I2V and camera-control node schemas change between
# commits. Bump ARG COMFYUI_REF deliberately; the assertion below fails the
# build if a bump drops a node this worker depends on.
ARG COMFYUI_REF=77c0f39e343aa83597d67cd95811df9e4fbfef2e
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /ComfyUI \
    && cd /ComfyUI \
    && git checkout --quiet "$COMFYUI_REF" \
    && test -f comfy_extras/nodes_wan.py \
    && for node in WanImageToVideo WanFirstLastFrameToVideo WanCameraEmbedding WanCameraImageToVideo; do \
         grep -q "node_id=\"$node\"" comfy_extras/nodes_wan.py \
         || { echo "FATAL: pinned ComfyUI is missing $node"; exit 1; }; \
       done \
    && echo "ComfyUI $(git rev-parse --short HEAD): Wan nodes present" \
    && pip install --no-cache-dir -r requirements.txt

# ---- Custom nodes (video output) ---------------------------------------
# VideoHelperSuite is pinned too: the worker names its exact inputs
# (frame_rate / format on VHS_VideoCombine), and those widgets were renamed
# upstream.
ARG VHS_REF=4d907bee61e92c2e65af3bd6383a4e4d356126d1
RUN git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git \
      /ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite \
    && cd /ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite \
    && git checkout --quiet "$VHS_REF" \
    && grep -q '"frame_rate"' videohelpersuite/nodes.py \
    && pip install --no-cache-dir -r requirements.txt

# 48GB-only voice clone (open-source, MIT): Chatterbox Turbo zero-shot TTS.
# ~0.5B params, needs seconds of ref audio. Runs in the same pod beside
# ComfyUI; the worker routes tool="voiceclone" past ComfyUI straight here.
RUN pip install --no-cache-dir chatterbox-tts torchaudio soundfile || true

# ---- Lip sync --------------------------------------------------------
# Wav2Lip. Upstream pins librosa==0.7.0 / numpy==1.17.1, neither of which
# builds on the CUDA 12.4 base, so we keep the modern libraries and patch the
# two call sites that changed instead. Verified end-to-end on CPU: a real
# Piper voice line drives a 44x44 mouth region while the background stays
# static.
#
# The worker runs this directly (like voiceclone) rather than through ComfyUI.
# Wav2Lip ships as a plain inference script, not a node pack, so routing it
# through ComfyUI would mean depending on a community node whose input names
# we do not control.
COPY deploy/runpod/wav2lip_librosa1.patch /tmp/wav2lip_librosa1.patch
ARG WAV2LIP_REPO=https://github.com/Rudrabha/Wav2Lip.git
ARG WAV2LIP_CKPT_URL=https://huggingface.co/numz/wav2lip_studio/resolve/main/Wav2lip/wav2lip_gan.pth
ARG S3FD_URL=https://huggingface.co/n0x1103/s3fd/resolve/main/s3fd-619a316812.pth
RUN git clone "$WAV2LIP_REPO" /Wav2Lip \
    && cd /Wav2Lip \
    && git apply /tmp/wav2lip_librosa1.patch \
    && rm -rf /tmp/wav2lip_librosa1.patch \
    && pip install --no-cache-dir \
         "librosa>=0.10" soundfile opencv-python-headless scipy \
         torch torchvision torchaudio \
    # Face detector weights, which upstream expects you to fetch by hand.
    && curl -sSL -o face_detection/detection/sfd/s3fd.pth "$S3FD_URL" \
    # GAN checkpoint: better teeth/blur than the base model.
    && curl -sSL -o checkpoints/wav2lip_gan.pth "$WAV2LIP_CKPT_URL" \
    && test -s face_detection/detection/sfd/s3fd.pth \
    && test -s checkpoints/wav2lip_gan.pth \
    && echo "Wav2Lip ready: $(du -h checkpoints/wav2lip_gan.pth | cut -f1) ckpt"

# Git history is not needed at runtime and is large in the final image.
RUN rm -rf /ComfyUI/.git /Wav2Lip/.git /ComfyUI/custom_nodes/*/.git

# ---- Worker ------------------------------------------------------------
COPY deploy/runpod/worker.py /worker.py
COPY deploy/runpod/worker_preflight.py /worker_preflight.py
COPY deploy/runpod/setup_model_links.sh /setup_model_links.sh
RUN chmod +x /setup_model_links.sh

ENV COMFY_URL=http://127.0.0.1:8188 \
    COMFY_INPUT=/ComfyUI/input \
    COMFY_OUTPUT=/ComfyUI/output \
    MODEL_VOLUME=/runpod-volume/models \
    PYTHONUNBUFFERED=1

# start.sh boots ComfyUI in the background then launches the runpod worker
COPY deploy/runpod/start.sh /start.sh
RUN chmod +x /start.sh

CMD [ "/start.sh" ]
# RunPod Serverless worker for the Vidyra render pipeline.
#
# This is separate from the AI Studio worker in this folder: it runs Piper,
# Whisper, and FFmpeg, while ComfyUI/diffusion stays on the Studio endpoint.
#
# Build: docker build -t vidyra/pipeline -f deploy/runpod/pipeline.Dockerfile .
# Create a serverless endpoint with this image and set
# RUNPOD_PIPELINE_ENDPOINT_ID on the API to its endpoint id.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir "runpod==1.3.0"

COPY backend /app/backend
COPY pipeline /app/pipeline
COPY config /app/config
COPY deploy/runpod/pipeline_handler.py /app/pipeline_handler.py
COPY deploy/runpod/fetch_models.py /app/fetch_models.py

RUN APP_ROOT=/app python /app/fetch_models.py

ENV PYTHONPATH=/app:/app/backend \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production

CMD ["python", "-u", "/app/pipeline_handler.py"]

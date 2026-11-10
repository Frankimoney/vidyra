# Vidyra thin API for Render (free web service).
#
# Only auth, Firestore, uploads (to R2), and job submission run here. Piper,
# Whisper, and FFmpeg renders execute on the RunPod pipeline endpoint, so this
# image stays small and starts fast.
#
# Build: docker build -t vidyra/api .

FROM python:3.11-slim

# ffmpeg is still needed for upload splitting; the render engine lives on RunPod.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements-api.txt /app/backend/requirements-api.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-api.txt

COPY backend /app/backend
COPY pipeline /app/pipeline
COPY config /app/config

ENV PYTHONPATH=/app:/app/backend \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    BACKGROUND_LOOPS=false

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --app-dir backend"]

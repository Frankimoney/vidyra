"""Backend configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(value, default=False):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Firebase
    project_id: str = os.getenv("FIREBASE_PROJECT_ID", "")
    bucket: str = os.getenv("FIREBASE_BUCKET", "")
    service_account_path: str = os.getenv("SERVICE_ACCOUNT_PATH", "")
    google_app_credentials: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    # App / security
    app_env: str = os.getenv("APP_ENV", "development")
    cors_origins: list = field(
        default_factory=lambda: [
            o.strip()
            for o in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:5173,http://localhost:4173",
            ).split(",")
            if o.strip()
        ]
    )
    dev_api_key: str = os.getenv("DEV_API_KEY", "")
    rate_limit_per_min: int = int(os.getenv("API_RATE_LIMIT_PER_MIN", "60"))

    # Storage / jobs
    # Media + finished videos live on the server disk ("local", the default)
    # because Firebase Storage requires the paid Blaze plan since Feb 2026.
    # Set STORAGE_BACKEND=firebase only if you upgrade to Blaze.
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local")
    storage_root: str = os.getenv("STORAGE_ROOT", "storage/data")
    public_url: str = os.getenv(
        "PUBLIC_URL", "http://localhost:8000"
    )
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    signed_url_secret: str = os.getenv("SIGNED_URL_SECRET", "")
    # S3-compatible storage (Runpod network volume, MinIO, AWS S3).
    s3_endpoint_url: str = os.getenv("S3_ENDPOINT_URL", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    s3_clock_offset_seconds: float = float(os.getenv("S3_CLOCK_OFFSET_SECONDS", "0") or 0)
    work_root: str = os.getenv("WORK_ROOT", "storage/work")
    output_public_dir: str = os.getenv("OUTPUT_PUBLIC_DIR", "storage/public")
    max_media_count: int = int(os.getenv("MAX_MEDIA_COUNT", "60"))
    max_media_bytes: int = int(os.getenv("MAX_MEDIA_BYTES", str(50 * 1024 * 1024)))
    max_script_chars: int = int(os.getenv("MAX_SCRIPT_CHARS", "6000"))
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "1"))
    max_daily_renders: int = int(os.getenv("MAX_DAILY_RENDERS", "10"))
    job_retention_days: int = int(os.getenv("JOB_RETENTION_DAYS", "7"))
    cleanup_interval_hours: int = int(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))

    # Vidyra engine
    voice_dir: str = os.getenv("VIDYRA_VOICE_DIR", "models/piper")
    music_dir: str = os.getenv("VIDYRA_MUSIC_DIR", "config/music")
    whisper_model: str = os.getenv("VIDYRA_WHISPER_MODEL", "base")
    # "runpod" sends caption transcription to the GPU worker; "local" keeps it
    # on the backend CPU.
    whisper_backend: str = os.getenv("WHISPER_BACKEND", "runpod").strip().lower()

    frontend_dir: str = os.getenv("VIDYRA_FRONTEND_DIR", "")

    # RunPod serverless GPU (AI Studio)
    runpod_api_key: str = os.getenv("RUNPOD_API_KEY", "")
    runpod_endpoint_id: str = os.getenv("RUNPOD_ENDPOINT_ID", "")
    runpod_api_base: str = os.getenv("RUNPOD_API_BASE", "https://api.runpod.ai/v2")
    runpod_fake: bool = _bool(os.getenv("RUNPOD_FAKE"), False)
    runpod_poll_seconds: int = int(os.getenv("RUNPOD_POLL_SECONDS", "6"))
    runpod_timeout_minutes: int = int(os.getenv("RUNPOD_TIMEOUT_MINUTES", "40"))
    studio_max_runs_per_day: int = int(os.getenv("STUDIO_MAX_RUNS_PER_DAY", "20"))
    studio_max_prompt_chars: int = int(os.getenv("STUDIO_MAX_PROMPT_CHARS", "2000"))

    # LLM scriptwriting (OpenAI-compatible endpoint)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))

    # Gemini (Google Generative Language API) — used for internet-grounded
    # writing via Google Search grounding when GEMINI_API_KEY is set.
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_base_url: str = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
    )
    gemini_model: str = os.getenv(
        "GEMINI_MODEL", "gemini-3.1-flash-lite"
    )  # default must be available to new accounts
    gemini_timeout_seconds: int = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "90"))

    # Film generation (auto "long film" from a one-line idea)
    film_max_scenes: int = int(os.getenv("FILM_MAX_SCENES", "16"))
    v1_image_model: str = os.getenv("FILM_IMAGE_MODEL", "flux-schnell")
    v1_video_model: str = os.getenv("FILM_VIDEO_MODEL", "wan21-i2v")

    # Podcast video generation (talking-head dialogue around a topic)
    podcast_max_turns: int = int(os.getenv("PODCAST_MAX_TURNS", "12"))

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def runpod_enabled(self) -> bool:
        return bool(self.runpod_api_key and self.runpod_endpoint_id)

    @property
    def studio_enabled(self) -> bool:
        return self.runpod_fake or self.runpod_enabled

    # Storage paths
    media_prefix: str = "media"
    video_prefix: str = "videos"
    studio_prefix: str = "studio"

    @property
    def dev_mode(self) -> bool:
        return self.app_env == "development"

    @property
    def sign_secret(self) -> str:
        if self.signed_url_secret:
            return self.signed_url_secret
        if self.dev_mode:
            # Persist a random secret per install. A hardcoded dev secret would
            # let anyone who knows a storage path forge media URLs.
            import secrets as _secrets

            os.makedirs(self.work_root, exist_ok=True)
            path = os.path.join(self.work_root, ".signing_secret")
            try:
                if os.path.isfile(path):
                    value = open(path, "r", encoding="utf-8").read().strip()
                    if value:
                        return value
                value = _secrets.token_hex(32)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(value)
                return value
            except OSError:
                return _secrets.token_hex(32)
        raise RuntimeError("SIGNED_URL_SECRET must be set in production")

    def ensure_dirs(self):
        os.makedirs(self.work_root, exist_ok=True)
        os.makedirs(self.output_public_dir, exist_ok=True)
        os.makedirs(os.path.join(self.storage_root, "media"), exist_ok=True)
        os.makedirs(os.path.join(self.storage_root, "videos"), exist_ok=True)


settings = Settings()
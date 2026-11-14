"""RunPod Serverless handler for Vidyra render jobs.

Input:

    {"job_id": "abc123"}

The API only submits this id. The handler loads the job from Firestore, runs
the same VideoWorker/FilmWorker code that used to run inside the API, and lets
those workers update Firestore and publish media to R2 directly. Serverless
pods disappear when the job completes, so nothing may be left only on disk.
"""

from __future__ import annotations

import traceback

import runpod

from app.config import settings
from app.services import jobs as jobs_service
from app.services.firebase import firebase
from app.services.storage import get_storage_driver
from app.workers.film_worker import FilmWorker
from app.workers.remote_worker import (
    endpoint_has_queued_jobs,
    rest_pipeline_endpoint,
    wake_pipeline_endpoint,
)
from app.workers.studio_worker import (
    StudioWorker,
    rest_studio_endpoint,
    wake_studio_endpoint,
)
from app.workers.video_worker import VideoWorker


def _build_workers():
    settings.ensure_dirs()
    if not firebase.init():
        raise RuntimeError(
            "Firebase Admin is not configured on the render worker "
            "(set FIREBASE_CREDENTIALS_JSON or SERVICE_ACCOUNT_PATH)."
        )
    storage = get_storage_driver(settings)
    studio = StudioWorker(settings, storage)
    video = VideoWorker(settings, storage)
    video.studio_worker = studio
    film = FilmWorker(settings, storage, video, studio)
    return video, film


def _release_endpoints() -> None:
    """Release the endpoints pinned for this job once nothing else is waiting.

    Each endpoint refuses to scale up from zero, so it was pinned at
    workersMin=1 for the render; releasing it here (instead of leaving it warm)
    is what keeps an idle deployment from billing. A still-queued job keeps its
    worker so we never strand the next render on a cold endpoint.
    """
    if not endpoint_has_queued_jobs(settings, settings.runpod_pipeline_endpoint_id):
        rest_pipeline_endpoint(settings)
    if not endpoint_has_queued_jobs(settings, settings.studio_endpoint_id):
        rest_studio_endpoint(settings)


def handler(event):
    job_id = str(((event or {}).get("input") or {}).get("job_id") or "").strip()
    if not job_id:
        return {"error": "job_id is required"}

    try:
        video, film = _build_workers()
    except Exception as exc:
        traceback.print_exc()
        try:
            jobs_service.update_job(
                job_id,
                status="failed",
                stage="failed",
                message="Render worker could not start.",
                error=str(exc),
            )
        except Exception:
            pass
        return {"error": f"worker setup failed: {exc}"}

    # Both endpoints refuse to scale up from zero, so keep them pinned for the
    # whole render and release them in the finally below.
    wake_pipeline_endpoint(settings)
    wake_studio_endpoint(settings)
    try:
        job = video._load_job(job_id)
        if job.get("type") == "film":
            film._process(job_id)
        else:
            video._process(job_id)
    except Exception as exc:
        # Workers mark their own render failures; this catches problems that
        # happen before/around the job load so Firestore never stays "queued".
        traceback.print_exc()
        try:
            jobs_service.update_job(
                job_id,
                status="failed",
                stage="failed",
                message="Render worker crashed.",
                error=str(exc),
            )
        except Exception:
            pass
        return {"error": str(exc)}
    finally:
        _release_endpoints()

    final = jobs_service.get_job(job_id) or {}
    return {"job_id": job_id, "status": final.get("status", "completed")}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

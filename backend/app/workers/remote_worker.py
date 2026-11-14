"""Submit render jobs to the RunPod pipeline endpoint.

The deployed API is deliberately thin: it creates the Firestore job and hands
the id to RunPod Serverless, then returns. The GPU worker loads the job, runs
the same VideoWorker/FilmWorker code, writes progress and results back to
Firestore, and publishes media to R2. That way the API can sleep while a long
render finishes.

In development, with no pipeline endpoint configured, jobs fall back to the
in-process workers so `uvicorn` still renders locally.
"""

from __future__ import annotations

import asyncio
import threading

from ..services import jobs as jobs_service
from ..services import runpod as runpod_client


#: Temporary diagnostic: the outcome of the last wake/rest attempt.
LAST_WARM: dict = {}


def _warm_enabled(settings) -> bool:
    return bool(getattr(settings, "runpod_studio_wake", True))


def _set_warm(settings, endpoint_id: str, warm: bool) -> None:
    """Best-effort pin (workersMin=1) or release (workersMin=0) of an endpoint."""
    endpoint_id = (endpoint_id or "").strip()
    if not _warm_enabled(settings):
        LAST_WARM.update(endpoint=endpoint_id, warm=warm, skipped="disabled")
        return
    if not (settings.runpod_api_key and endpoint_id):
        LAST_WARM.update(endpoint=endpoint_id, warm=warm, skipped="no_key_or_id")
        return
    try:
        # Waking also lifts workersMax above zero: a paused endpoint (max=0)
        # rejects new work with ENDPOINT_PAUSED. Releasing only lowers the floor.
        r = runpod_client.set_endpoint_workers(
            settings.runpod_api_key,
            endpoint_id,
            workers_min=1 if warm else 0,
            workers_max=1 if warm else None,
            rest_api_base=getattr(
                settings, "runpod_rest_api_base", runpod_client.DEFAULT_REST_API_BASE
            ),
        )
        LAST_WARM.update(
            endpoint=endpoint_id,
            warm=warm,
            ok=True,
            min=r.get("workersMin"),
            max=r.get("workersMax"),
        )
    except Exception as exc:
        LAST_WARM.update(endpoint=endpoint_id, warm=warm, ok=False, error=repr(exc))


def wake_pipeline_endpoint(settings) -> None:
    """Pin the pipeline endpoint so a submitted job gets a worker at once."""
    _set_warm(settings, getattr(settings, "runpod_pipeline_endpoint_id", ""), True)


def rest_pipeline_endpoint(settings) -> None:
    """Release the pipeline endpoint so it can scale back to zero."""
    _set_warm(settings, getattr(settings, "runpod_pipeline_endpoint_id", ""), False)


def endpoint_has_queued_jobs(settings, endpoint_id: str) -> bool:
    """True if the endpoint still has jobs waiting, so its worker must stay up.

    Unknown (a health error) counts as busy so we fail safe toward keeping a
    render alive rather than stranding a queued job on a cold endpoint.
    """
    endpoint_id = (endpoint_id or "").strip()
    if not (settings.runpod_api_key and endpoint_id):
        return False
    try:
        health = runpod_client.get_endpoint_health(
            getattr(settings, "runpod_api_base", runpod_client.DEFAULT_API_BASE),
            settings.runpod_api_key,
            endpoint_id,
        )
    except Exception:
        return True
    jobs = (health or {}).get("jobs") or {}
    return int(jobs.get("inQueue") or 0) > 0


def _has_active_jobs() -> bool:
    """True while any render job is queued or processing.

    Unknown counts as busy so a Firestore hiccup never releases a worker that a
    running render still needs.
    """
    try:
        from ..services.firebase import firebase

        if firebase.db is None:
            return True
        snaps = (
            firebase.db.collection(jobs_service.COLLECTION)
            .where("status", "in", ["queued", "processing"])
            .limit(1)
            .get()
        )
        return len(snaps) > 0
    except Exception:
        return True


def release_endpoints_if_idle(settings) -> None:
    """Release both endpoints once nothing is queued or processing.

    Safety net for a worker that crashed or timed out while pinned at
    workersMin=1: with no job running anywhere, nothing should keep billing.
    """
    if _has_active_jobs():
        return
    from .studio_worker import rest_studio_endpoint

    rest_studio_endpoint(settings)
    rest_pipeline_endpoint(settings)


class PipelineWorker:
    """Route render jobs to RunPod, or to local workers during development."""

    def __init__(self, settings, storage, studio_worker):
        self.settings = settings
        self.storage = storage
        self.studio_worker = studio_worker
        self._local_lock = threading.RLock()
        self._local_video = None
        self._local_film = None

    @property
    def remote_enabled(self) -> bool:
        return bool(self.settings.remote_pipeline)

    async def submit(self, job_id: str) -> None:
        if self.remote_enabled:
            await asyncio.to_thread(self._wake_for_submit)
            await asyncio.to_thread(self._submit_remote, job_id)
            return
        if self.settings.dev_mode:
            await self._submit_local(job_id)
            return
        jobs_service.update_job(
            job_id,
            status="failed",
            stage="failed",
            progress=0,
            message="Render service is not configured.",
            error=(
                "Set RUNPOD_API_KEY and RUNPOD_PIPELINE_ENDPOINT_ID so the API "
                "can submit renders to RunPod."
            ),
        )

    def _wake_for_submit(self) -> None:
        """Pin both endpoints the moment a render is submitted.

        The pipeline endpoint and the Studio endpoint (pinned to its model
        volume's datacenter) both refuse to scale up from zero, so a queued job
        would otherwise sit at 0% until something forces a worker. Pinning here
        also starts warming the Studio GPU while the script is still being
        written, so the first scene doesn't wait on a cold ComfyUI boot.
        """
        from .studio_worker import wake_studio_endpoint

        wake_pipeline_endpoint(self.settings)
        wake_studio_endpoint(self.settings)

    def _submit_remote(self, job_id: str) -> None:
        api_base = self.settings.runpod_api_base.rstrip("/")
        endpoint = self.settings.runpod_pipeline_endpoint_id.strip()
        try:
            run_id = runpod_client.submit_run(
                api_base,
                self.settings.runpod_api_key,
                endpoint,
                {"job_id": job_id},
            )
        except Exception as exc:
            jobs_service.update_job(
                job_id,
                status="failed",
                stage="failed",
                progress=0,
                message="Could not start the render.",
                error=str(exc),
            )
            return
        # The pod may already have moved the job to "processing"; only attach
        # the run id and message so we never clobber its newer state.
        jobs_service.update_job(
            job_id,
            runpodRunId=run_id,
            message="Queued on the render GPU",
        )

    async def _submit_local(self, job_id: str) -> None:
        job = jobs_service.get_job(job_id) or {}
        if job.get("type") == "film":
            await self._get_local_film().submit(job_id)
        else:
            await self._get_local_video().submit(job_id)

    def _get_local_video(self):
        from .video_worker import VideoWorker

        with self._local_lock:
            if self._local_video is None:
                self._local_video = VideoWorker(self.settings, self.storage)
                self._local_video.studio_worker = self.studio_worker
            return self._local_video

    def _get_local_film(self):
        from .film_worker import FilmWorker

        with self._local_lock:
            if self._local_film is None:
                self._local_film = FilmWorker(
                    self.settings,
                    self.storage,
                    self._get_local_video(),
                    self.studio_worker,
                )
            return self._local_film

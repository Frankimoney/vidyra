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

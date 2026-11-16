"""One-shot maintenance entry point for Render cron / scheduled jobs.

The free web instance sleeps, so it cannot be trusted to run the cleanup and
recurring-series timers. Point a cron job at this module instead:

    python -m app.cron

It expires old jobs, cleans local scratch, and submits any due series renders
to the same RunPod pipeline the API uses.
"""

from __future__ import annotations

import asyncio

from .config import settings
from .services import cleanup
from .services import jobs as jobs_service
from .services import series as series_service
from .services.firebase import firebase
from .services.storage import get_storage_driver
from .workers.remote_worker import PipelineWorker
from .workers.studio_worker import StudioWorker


async def main() -> None:
    settings.ensure_dirs()
    if not firebase.init():
        raise SystemExit("Firebase Admin is not configured; cannot run maintenance.")

    storage = get_storage_driver(settings)
    cleanup.clean_expired_jobs(settings, storage)
    cleanup.clean_orphaned_media(settings, storage)

    from .routes import series as series_routes

    worker = PipelineWorker(settings, storage, StudioWorker(settings, storage))
    for doc in series_service.due_series():
        try:
            job_id = series_routes.create_job_from_series(doc, storage, settings)
            await worker.submit(job_id)
        except Exception as exc:
            print(f"[cron] skipped series {doc.get('seriesId')}: {exc}")
        series_service.mark_ran(doc.get("seriesId") or "")


if __name__ == "__main__":
    asyncio.run(main())

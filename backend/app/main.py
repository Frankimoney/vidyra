"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routes import films, health, jobs, me, public, recipes, series, studio, uploads, usage, workspaces
from .services import cleanup
from .services import jobs as jobs_service
from .services import series as series_service
from .services.firebase import firebase
from .services.storage import get_storage_driver
from .workers.remote_worker import PipelineWorker
from .workers.studio_worker import StudioWorker


cleanup_task: asyncio.Task | None = None
series_task: asyncio.Task | None = None


async def _cleanup_loop(app: FastAPI):
    while True:
        try:
            await asyncio.to_thread(
                cleanup.clean_expired_jobs,
                app.state.settings,
                app.state.storage,
            )
            await asyncio.to_thread(
                cleanup.clean_orphaned_media,
                app.state.settings,
                app.state.storage,
            )
        except Exception as exc:
            print(f"[cleanup] error: {exc}")
        await asyncio.sleep(
            max(1, app.state.settings.cleanup_interval_hours) * 3600
        )




async def _series_loop(app: FastAPI):
    while True:
        try:
            for doc in series_service.due_series():
                payload = doc.get("payload") or {}
                workspace_id = (doc.get("workspaceId") or payload.get("workspace_id") or "").strip()
                job_id = jobs_service.create_job(
                    uid=doc.get("userId") or "",
                    title=str(payload.get("title") or doc.get("name") or "Recurring video")[:120],
                    script=str(payload.get("script") or ""),
                    media=list(payload.get("media") or []),
                    settings=dict(payload.get("settings") or {}),
                    cover=payload.get("cover"),
                    niche=str(payload.get("niche") or ""),
                    scenes=list(payload.get("scenes") or []) or None,
                    characters=list(payload.get("characters") or []) or None,
                    workspace_id=workspace_id,
                )
                await app.state.worker.submit(job_id)
                series_service.mark_ran(doc.get("seriesId") or "")
        except Exception as exc:
            print(f"[series] scheduler error: {exc}")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    app.state.settings = settings
    app.state.firebase = firebase
    app.state.storage = get_storage_driver(settings)
    app.state.studio_worker = StudioWorker(settings, app.state.storage)
    # Renders run on RunPod. The local workers are only built in development
    # when no pipeline endpoint is set (see PipelineWorker.submit).
    app.state.worker = PipelineWorker(
        settings, app.state.storage, app.state.studio_worker
    )
    app.state.film_worker = app.state.worker

    # Firebase must be initialized before recovery: without it the job store is
    # the dev in-memory dict, which a restart empties anyway.
    firebase_ready = firebase.init()
    if firebase_ready:
        print("Firebase Admin initialized.")
    else:
        if settings.app_env == "production":
            raise RuntimeError(
                "Firebase Admin is not configured; set FIREBASE_CREDENTIALS_JSON "
                "or SERVICE_ACCOUNT_PATH before deploying."
            )
        print(
            "WARNING: Firebase credentials not found. "
            "Jobs will run only in dev mode (no cloud storage)."
        )

    # Locally owned jobs die with this process. With a remote pipeline the
    # queued/processing rows belong to RunPod, so leave them alone.
    if firebase_ready and not settings.remote_pipeline:
        try:
            stranded = await asyncio.to_thread(cleanup.fail_interrupted_jobs)
            if stranded:
                print(
                    f"Marked {stranded} interrupted job(s)/run(s) as failed. "
                    "Start them again to render from the beginning."
                )
        except Exception as exc:
            print(f"WARNING: could not reconcile interrupted jobs: {exc!r}")

    if settings.dev_mode:
        print("VIDYRA backend running in DEVELOPMENT mode.")

    global cleanup_task, series_task
    cleanup_task = None
    series_task = None
    if settings.background_loops:
        cleanup_task = asyncio.create_task(_cleanup_loop(app))
        series_task = asyncio.create_task(_series_loop(app))
    yield
    if cleanup_task:
        cleanup_task.cancel()
    if series_task:
        series_task.cancel()


app = FastAPI(
    title="Vidyra API",
    description="AI sports-video generation backend (FastAPI + FFmpeg).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["system"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])
app.include_router(uploads.router, prefix="/api", tags=["uploads"])
app.include_router(me.router, prefix="/api", tags=["profile"])
app.include_router(public.router, prefix="/api", tags=["public"])
app.include_router(studio.router, prefix="/api", tags=["studio"])
app.include_router(films.router, prefix="/api", tags=["films"])
app.include_router(workspaces.router, prefix="/api", tags=["workspaces"])
app.include_router(usage.router, prefix="/api", tags=["usage"])
app.include_router(recipes.router, prefix="/api", tags=["recipes"])
app.include_router(series.router, prefix="/api", tags=["series"])

if settings.frontend_dir and os.path.isdir(settings.frontend_dir):
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.staticfiles import StaticFiles

    class SPAStaticFiles(StaticFiles):
        """Serve the built SPA, falling back to index.html for client routes."""

        async def get_response(self, path, scope):
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                    return await super().get_response("index.html", scope)
                raise
            if response.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            return response

    app.mount(
        "/",
        SPAStaticFiles(directory=settings.frontend_dir, html=True),
        name="frontend",
    )


@app.get("/")
async def root():
    return {"name": "Vidyra API", "docs": "/docs", "health": "/api/health"}
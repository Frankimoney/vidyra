"""Health and system endpoints."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request

from ..models import HealthOut
from ..services.firebase import firebase
from ..workers import remote_worker

__version__ = "0.1.5"

router = APIRouter()

#: The health check is the one endpoint Render pings on a schedule, so it also
#: doubles as the idle-render safety net: if a crashed worker left an endpoint
#: pinned at workersMin=1, the first health check after the job is gone releases
#: it. Throttled (it may PATCH RunPod and query Firestore) and run off the event
#: loop so the health response itself never waits on RunPod.
_last_reconcile = 0.0
_RECONCILE_SECONDS = 120


@router.get("/_diag/wake")
async def diag_wake(request: Request):
    """Temporary: report whether this instance can reach RunPod's REST API."""
    from ..services import runpod as rp

    s = request.app.state.settings
    result = {"restBase": s.runpod_rest_api_base, "pipeline": s.runpod_pipeline_endpoint_id}
    try:
        got = await asyncio.to_thread(
            rp._request,
            f"{s.runpod_rest_api_base.rstrip('/')}/endpoints",
            s.runpod_api_key,
            None,
            20,
            "GET",
        )
        result["list"] = {"ok": True, "count": len(got) if isinstance(got, list) else -1}
    except Exception as e:
        result["list"] = {"ok": False, "error": repr(e)}
    try:
        r = await asyncio.to_thread(
            rp.set_endpoint_workers_min,
            s.runpod_api_key,
            s.runpod_pipeline_endpoint_id,
            1,
        )
        result["patch"] = {"ok": True, "min": r.get("workersMin"), "max": r.get("workersMax")}
    except Exception as e:
        result["patch"] = {"ok": False, "error": repr(e)}
    return result


@router.get("/health", response_model=HealthOut)
async def health(request: Request):
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and settings.remote_pipeline:
        global _last_reconcile
        now = time.monotonic()
        if now - _last_reconcile >= _RECONCILE_SECONDS:
            _last_reconcile = now
            asyncio.create_task(
                asyncio.to_thread(remote_worker.release_endpoints_if_idle, settings)
            )
    return HealthOut(
        status="ok",
        version=__version__,
        firebase=firebase.ready,
        storage=(request.app.state.storage.kind if hasattr(request.app.state, "storage") else None),
    )
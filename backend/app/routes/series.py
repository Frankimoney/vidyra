"""Recurring render series endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import CurrentUser, get_current_user
from ..services import jobs as jobs_service
from ..services import series as series_service
from ..services import workspaces as workspace_service

router = APIRouter()


class SeriesCreate(BaseModel):
    name: str = Field(default="Recurring series", max_length=80)
    interval_hours: int = Field(default=24, ge=1, le=720)
    workspace_id: str = Field(default="", max_length=64)
    enabled: bool = True
    payload: dict = Field(default_factory=dict)


class SeriesPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    enabled: bool | None = None
    payload: dict | None = None


def _start_job(request: Request, doc: dict) -> str:
    payload = doc.get("payload") or {}
    uid = doc.get("userId") or ""
    if not uid:
        raise KeyError("userId")
    workspace_id = (doc.get("workspaceId") or payload.get("workspace_id") or "").strip()
    if workspace_id:
        workspace_service.owner_workspace(workspace_id, uid)
    job_id = jobs_service.create_job(
        uid=uid,
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
    worker = request.app.state.worker
    import asyncio

    asyncio.create_task(worker.submit(job_id))
    return job_id


@router.get("/series")
async def list_series(user: CurrentUser = Depends(get_current_user)):
    return {"items": [series_service._out(d) for d in series_service.list_series(user.uid)]}


@router.post("/series", status_code=status.HTTP_201_CREATED)
async def create_series(payload: SeriesCreate, user: CurrentUser = Depends(get_current_user)):
    if payload.workspace_id:
        try:
            workspace_service.owner_workspace(payload.workspace_id, user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Workspace not found.")
    doc = series_service.create_series(
        user.uid,
        {
            "name": payload.name,
            "intervalHours": payload.interval_hours,
            "workspaceId": payload.workspace_id,
            "enabled": payload.enabled,
            "payload": payload.payload,
        },
    )
    return series_service._out(doc)


@router.patch("/series/{series_id}")
async def patch_series(
    series_id: str,
    payload: SeriesPatch,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        doc = series_service.update_series(
            user.uid, series_id, payload.model_dump(exclude_unset=True)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Series not found.")
    return series_service._out(doc)


@router.delete("/series/{series_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_series(series_id: str, user: CurrentUser = Depends(get_current_user)):
    try:
        series_service.delete_series(user.uid, series_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Series not found.")
    return None


@router.post("/series/{series_id}/run")
async def run_series(
    series_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        doc = series_service.get_series(user.uid, series_id)
        job_id = _start_job(request, doc)
        series_service.mark_ran(series_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Series not found.")
    return {"job_id": job_id, "status": "queued"}

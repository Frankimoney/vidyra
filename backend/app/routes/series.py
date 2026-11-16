"""Recurring render series endpoints."""

from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from pipeline.audio import clamp_music_volume

from ..auth import CurrentUser, get_current_user
from ..config import Settings
from ..services import jobs as jobs_service
from ..services import series as series_service
from ..services import workspaces as workspace_service
from ..services.ratelimit import rate_limiter
from ..services.voice_refs import owned_clone_ref
from .jobs import (
    _check_media_ownership,
    _normalize_characters,
    _normalize_media,
    _normalize_scenes,
)

router = APIRouter()

_ALLOWED_SETTINGS = {
    "voice", "speech_rate", "pacing",
    "hook", "hook_style", "hook_text", "hook_subtext", "hook_duration",
    "captions", "caption_style", "font_size", "words_per_line", "caption_position",
    "music", "music_volume", "transition", "transition_duration", "reframe",
    "color_brightness", "color_contrast", "color_saturation", "normalize_audio",
    "show_page_name", "page_name",
    "aspect", "width", "height", "preview",
}


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


def validate_series_payload(
    raw_payload: dict,
    uid: str,
    storage,
    s: Settings,
    *,
    require_complete: bool = False,
) -> dict:
    """Validate and sanitize a recurring series job payload for ownership & bounds."""
    if not isinstance(raw_payload, dict):
        raise HTTPException(status_code=400, detail="Invalid series payload.")

    raw_scenes = raw_payload.get("scenes")
    storyboard = bool(raw_scenes)
    scenes = (
        _normalize_scenes(raw_scenes, storage, uid, s.max_media_count)
        if storyboard
        else []
    )
    for sc in scenes:
        owned_clone_ref(str(sc.get("voice") or ""), uid)

    raw_chars = raw_payload.get("characters") or []
    for ch in raw_chars:
        if isinstance(ch, dict):
            owned_clone_ref(str(ch.get("voice") or ""), uid)
    characters = _normalize_characters(raw_chars, uid) if raw_chars else []

    raw_media = raw_payload.get("media") or []
    if not storyboard and raw_media:
        media = _normalize_media(raw_media)
        if len(media) > s.max_media_count:
            raise HTTPException(
                status_code=400, detail=f"Too many media files (max {s.max_media_count})."
            )
        _check_media_ownership(media, storage, uid)
    else:
        media = []

    script = str(raw_payload.get("script") or "").strip()
    if storyboard and not script:
        script = "\n\n".join(sc["script"] for sc in scenes)
    if len(script) > s.max_script_chars:
        raise HTTPException(
            status_code=400,
            detail=f"Script exceeds {s.max_script_chars} characters.",
        )

    if require_complete:
        if not script:
            raise HTTPException(status_code=400, detail="Series payload needs a script.")
        if not storyboard and not media:
            raise HTTPException(
                status_code=400, detail="Series payload needs at least one media file."
            )

    raw_settings = raw_payload.get("settings")
    settings = (
        {k: v for k, v in raw_settings.items() if k in _ALLOWED_SETTINGS}
        if isinstance(raw_settings, dict)
        else {}
    )
    if "music_volume" in settings:
        settings["music_volume"] = clamp_music_volume(settings.get("music_volume"))
    chosen_music = str(settings.get("music") or "").strip()
    if chosen_music.startswith("media/") and not chosen_music.startswith(f"media/{uid}/"):
        raise HTTPException(status_code=400, detail="Music track does not belong to you.")
    owned_clone_ref(str(settings.get("voice") or ""), uid)

    cover = str(raw_payload.get("cover") or "").strip() or None
    if cover and not cover.startswith(f"media/{uid}/"):
        raise HTTPException(status_code=400, detail="Cover image does not belong to you.")

    clean: dict = {}
    if raw_payload.get("title"):
        clean["title"] = str(raw_payload.get("title"))[:120]
    if script:
        clean["script"] = script
    if media:
        clean["media"] = media
    if settings:
        clean["settings"] = settings
    if cover:
        clean["cover"] = cover
    if raw_payload.get("niche"):
        clean["niche"] = str(raw_payload.get("niche"))[:80]
    if scenes:
        clean["scenes"] = scenes
    if characters:
        clean["characters"] = characters
    if raw_payload.get("workspace_id"):
        clean["workspace_id"] = str(raw_payload.get("workspace_id")).strip()[:64]
    return clean


def create_job_from_series(doc: dict, storage, s: Settings) -> str:
    """Validate ownership and daily render quota, then create a job for a series."""
    uid = (doc.get("userId") or "").strip()
    if not uid:
        raise KeyError("userId")
    raw_payload = doc.get("payload") or {}
    workspace_id = (doc.get("workspaceId") or raw_payload.get("workspace_id") or "").strip()
    if workspace_id:
        workspace_service.owner_workspace(workspace_id, uid)

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    renders_today = sum(
        1
        for j in jobs_service.list_jobs(uid, limit=200)
        if (j.get("createdAt") or "").startswith(today) and j.get("type") != "face"
    )
    if renders_today >= s.max_daily_renders:
        raise HTTPException(
            status_code=429,
            detail=f"Daily render limit reached ({s.max_daily_renders} videos). Try again tomorrow.",
        )

    payload = validate_series_payload(
        raw_payload, uid, storage, s, require_complete=True
    )
    settings = workspace_service.apply_defaults(
        dict(payload.get("settings") or {}), uid, workspace_id, allowed=_ALLOWED_SETTINGS
    )
    settings["music_volume"] = clamp_music_volume(settings.get("music_volume"))

    return jobs_service.create_job(
        uid=uid,
        title=str(payload.get("title") or doc.get("name") or "Recurring video")[:120],
        script=str(payload.get("script") or ""),
        media=list(payload.get("media") or []),
        settings=settings,
        cover=payload.get("cover"),
        niche=str(payload.get("niche") or ""),
        scenes=list(payload.get("scenes") or []) or None,
        characters=list(payload.get("characters") or []) or None,
        workspace_id=workspace_id,
    )


def _start_job(request: Request, doc: dict) -> str:
    job_id = create_job_from_series(
        doc, request.app.state.storage, request.app.state.settings
    )
    worker = request.app.state.worker
    asyncio.create_task(worker.submit(job_id))
    return job_id


@router.get("/series")
async def list_series(user: CurrentUser = Depends(get_current_user)):
    return {"items": [series_service._out(d) for d in series_service.list_series(user.uid)]}


@router.post("/series", status_code=status.HTTP_201_CREATED)
async def create_series(
    payload: SeriesCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)
    if payload.workspace_id:
        try:
            workspace_service.owner_workspace(payload.workspace_id, user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Workspace not found.")
    clean_payload = (
        validate_series_payload(
            payload.payload, user.uid, request.app.state.storage, s, require_complete=False
        )
        if payload.payload
        else {}
    )
    doc = series_service.create_series(
        user.uid,
        {
            "name": payload.name,
            "intervalHours": payload.interval_hours,
            "workspaceId": payload.workspace_id,
            "enabled": payload.enabled,
            "payload": clean_payload,
        },
    )
    return series_service._out(doc)


@router.patch("/series/{series_id}")
async def patch_series(
    series_id: str,
    payload: SeriesPatch,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    s: Settings = request.app.state.settings
    updates = payload.model_dump(exclude_unset=True)
    if "payload" in updates and updates["payload"] is not None:
        updates["payload"] = validate_series_payload(
            updates["payload"], user.uid, request.app.state.storage, s, require_complete=False
        )
    try:
        doc = series_service.update_series(user.uid, series_id, updates)
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
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)
    try:
        doc = series_service.get_series(user.uid, series_id)
        job_id = _start_job(request, doc)
        series_service.mark_ran(series_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Series not found.")
    return {"job_id": job_id, "status": "queued"}

"""Catalog (voices / music / presets) and job endpoints."""

from __future__ import annotations

import datetime
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from pipeline.audio import clamp_music_volume
from pipeline.utils import classify_media
from pipeline.voices import find_music_tracks, find_voices

from ..auth import CurrentUser, get_current_user
from ..config import Settings
from ..models import CreateOut, JobCreate, JobOut, TitleIn
from ..services import jobs as jobs_service
from ..services import profiles
from ..services import workspaces as workspace_service
from ..services.ratelimit import rate_limiter
from ..services.shares import create_share_token
from ..services.storage.local import as_absolute
from ..workers.video_worker import VideoWorker

router = APIRouter()


@router.get("/catalog/voices")
async def catalog_voices(request: Request, user: CurrentUser = Depends(get_current_user)):
    s: Settings = request.app.state.settings
    return {"voices": sorted(find_voices(s.voice_dir).keys())}


@router.get("/catalog/music")
async def catalog_music(request: Request, user: CurrentUser = Depends(get_current_user)):
    s: Settings = request.app.state.settings
    library = sorted(find_music_tracks(s.music_dir).keys())
    mine = sorted(request.app.state.storage.list_user_audio(user.uid, music_only=True))
    return {"music": library + mine}


@router.get("/catalog/presets")
async def catalog_presets(
    request: Request, user: CurrentUser = Depends(get_current_user)
):
    from pipeline.config import (
        CAPTION_POSITIONS,
        CAPTION_STYLES,
        HOOK_STYLES,
        PACING_SETTINGS,
        TRANSITIONS,
    )

    return {
        "pacing": list(PACING_SETTINGS.keys()),
        "hooks": HOOK_STYLES,
        "caption_styles": list(CAPTION_STYLES.keys()),
        "caption_positions": list(CAPTION_POSITIONS.keys()),
        "transitions": TRANSITIONS,
        "speech_rate": {"min": 0.85, "max": 1.15},
    }


def _normalize_media(raw) -> list[dict]:
    """Validate and normalize media entries to {path, duration?, trimStart?} dicts.

    trimStart is video-only (ignored for images downstream) — single normalize
    point for both Create and Edit flows, no duplicate validator.
    """
    out: list[dict] = []
    for m in raw:
        if isinstance(m, str):
            if not (m or "").strip():
                raise HTTPException(status_code=400, detail="Empty media path.")
            out.append({"path": m.strip()})
        elif isinstance(m, dict):
            p = m.get("path") or m.get("storagePath")
            if not isinstance(p, str) or not p.strip():
                raise HTTPException(status_code=400, detail="Media entry needs a path.")
            duration = m.get("duration")
            if duration not in (None, ""):
                try:
                    duration = float(duration)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Media duration must be a number.")
                if not (0.5 <= duration <= 60):
                    raise HTTPException(
                        status_code=400, detail="Media duration out of range (0.5–60s)."
                    )
            trim_start = m.get("trimStart")
            if trim_start not in (None, ""):
                try:
                    trim_start = float(trim_start)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Trim start must be a number.")
                if not (0 <= trim_start <= 600):
                    raise HTTPException(
                        status_code=400, detail="Trim start out of range (0–600s)."
                    )
            focus = {}
            for key, field in (("focusX", "x"), ("focusY", "y")):
                raw_focus = m.get(key)
                if raw_focus in (None, ""):
                    continue
                try:
                    raw_focus = float(raw_focus)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400, detail=f"Media {key} must be a number."
                    )
                if not (0 <= raw_focus <= 1):
                    raise HTTPException(
                        status_code=400, detail=f"Media {key} out of range (0–1)."
                    )
                focus[field] = round(raw_focus, 4)
            raw_zoom = m.get("zoom")
            if raw_zoom not in (None, ""):
                try:
                    raw_zoom = float(raw_zoom)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400, detail="Media zoom must be a number."
                    )
                if not (1 <= raw_zoom <= 3):
                    raise HTTPException(
                        status_code=400, detail="Media zoom out of range (1–3)."
                    )
                focus["zoom"] = round(raw_zoom, 4)

            entry = {"path": p.strip()}
            if duration:
                entry["duration"] = round(duration, 2)
            if trim_start:
                entry["trimStart"] = round(trim_start, 2)
            if "x" in focus:
                entry["focusX"] = focus["x"]
            if "y" in focus:
                entry["focusY"] = focus["y"]
            if "zoom" in focus:
                entry["zoom"] = focus["zoom"]
            out.append(entry)
        else:
            raise HTTPException(status_code=400, detail="Invalid media entry.")
    return out


def _validate_ref_image(uid: str, ref: str) -> str:
    """A client-supplied image reference must be the user's own stored image."""
    ref = (ref or "").strip().replace("\\", "/")
    if not ref:
        return ""
    if ".." in ref.split("/") or not ref.startswith(
        tuple(f"{p}{uid}/" for p in ("media/", "studio/", "videos/"))
    ):
        raise HTTPException(
            status_code=400, detail=f"Reference image does not belong to you: {ref}"
        )
    if classify_media(ref) != "image":
        raise HTTPException(
            status_code=400, detail="A character reference must be an image file."
        )
    return ref


def _check_media_ownership(entries, storage, uid):
    # The `media/{uid}/` convention is how every path in this app is scoped, on
    # both storage backends -- so it is enforced regardless of driver. Skipping
    # it for firebase would let a job reference (and the worker download)
    # `media/<other-uid>/…`, since that driver's `resolve` fetches any blob
    # with the service account's credentials.
    allowed_prefixes = (
        f"media/{uid}/",
        f"studio/{uid}/",
        f"videos/{uid}/",
    )
    for entry in entries:
        path = entry["path"].replace("\\", "/")
        if ".." in path.split("/"):
            raise HTTPException(
                status_code=400,
                detail=f"Media file does not belong to you: {entry['path']}",
            )
        if not path.startswith(allowed_prefixes):
            raise HTTPException(
                status_code=400,
                detail=f"Media file does not belong to you: {entry['path']}",
            )


def _normalize_scenes(raw, storage, uid, max_media: int) -> list[dict]:
    """Validate storyboard scenes into {script, voice?, transition?, media:[...]}."""
    scenes: list[dict] = []
    total_media = 0
    for i, scene in enumerate(raw or []):
        if not isinstance(scene, dict):
            raise HTTPException(status_code=400, detail=f"Scene {i + 1} is invalid.")
        script = (scene.get("script") or "").strip()
        if not script:
            raise HTTPException(
                status_code=400, detail=f"Scene {i + 1} needs a script."
            )
        media = _normalize_media(scene.get("media") or [])
        if not media:
            raise HTTPException(
                status_code=400, detail=f"Scene {i + 1} needs at least one media file."
            )
        total_media += len(media)
        if total_media > max_media:
            raise HTTPException(
                status_code=400, detail=f"Too many media files (max {max_media})."
            )
        _check_media_ownership(media, storage, uid)
        entry: dict = {"script": script, "media": media}
        voice = (scene.get("voice") or "").strip()
        if voice:
            entry["voice"] = voice
        transition = (scene.get("transition") or "").strip()
        if transition:
            entry["transition"] = transition
        raw_duration = scene.get("duration")
        if raw_duration not in (None, ""):
            try:
                raw_duration = float(raw_duration)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"Scene {i + 1} duration must be a number."
                )
            if not (0.5 <= raw_duration <= 600):
                raise HTTPException(
                    status_code=400,
                    detail=f"Scene {i + 1} duration out of range (0.5–600s).",
                )
            entry["duration"] = round(raw_duration, 2)
        scenes.append(entry)
    if not scenes:
        raise HTTPException(status_code=400, detail="At least one scene is required.")
    return scenes


def _normalize_characters(raw, uid: str = "") -> list[dict]:
    """Validate characters into {name, voice, description?, portrait?} dicts.

    A character the user already saved resolves to their library entry, so a
    storyboard can reuse the face and voice established in Auto Film or the
    Director. Only the name is required — the voice may come from the library.
    """
    from ..services import characters as characters_service

    chars: list[dict] = []
    seen: set[str] = set()
    for i, char in enumerate(raw or []):
        if not isinstance(char, dict):
            raise HTTPException(status_code=400, detail=f"Character {i + 1} is invalid.")
        name = (char.get("name") or "").strip()
        if not name:
            raise HTTPException(
                status_code=400, detail=f"Character {i + 1} needs a name."
            )
        if len(name) > 40:
            raise HTTPException(
                status_code=400, detail=f"Character name too long: {name[:20]}…"
            )

        # Saved identity for this name, if the user has one.
        saved = characters_service.find_by_name(uid, name) if uid else None
        voice = (char.get("voice") or (saved or {}).get("voice") or "").strip()
        if not voice:
            raise HTTPException(
                status_code=400,
                detail=f"Character '{name}' needs a voice.",
            )
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "name": name,
            "voice": voice,
            "description": (char.get("description") or (saved or {}).get("description") or "").strip(),
        }
        # Carry the locked face so this feature renders the same character. It
        # arrives from the client, so it gets the same ownership + image-kind
        # check as every other reference path rather than being stored verbatim.
        portrait = (char.get("portrait") or (saved or {}).get("refImage") or "").strip()
        if portrait:
            entry["portrait"] = _validate_ref_image(uid, portrait)
        chars.append(entry)
    return chars


@router.post("/jobs", response_model=CreateOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)

    storage = request.app.state.storage
    storyboard = bool(payload.scenes)
    scenes = _normalize_scenes(payload.scenes, storage, user.uid, s.max_media_count) if storyboard else []
    characters = _normalize_characters(payload.characters, user.uid)

    if storyboard:
        script = (payload.script or "").strip() or "\n\n".join(
            sc["script"] for sc in scenes
        )
    else:
        script = (payload.script or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script is required.")
    if len(script) > s.max_script_chars:
        raise HTTPException(
            status_code=400,
            detail=f"Script exceeds {s.max_script_chars} characters.",
        )

    if not storyboard:
        media = _normalize_media(payload.media)
        if not media:
            raise HTTPException(status_code=400, detail="At least one media file is required.")
        if len(media) > s.max_media_count:
            raise HTTPException(
                status_code=400, detail=f"Too many media files (max {s.max_media_count})."
            )
        _check_media_ownership(media, storage, user.uid)
    else:
        media = []

    # Whitelist settings keys
    allowed = {
        "voice", "speech_rate", "pacing",
        "hook", "hook_style", "hook_text", "hook_subtext", "hook_duration",
        "captions", "caption_style", "font_size", "words_per_line", "caption_position",
        "music", "music_volume", "transition", "reframe",
        "show_page_name", "page_name",
        # Output shape. "aspect" is a ratio from pipeline.aspect ("auto" keeps
        # the default 9:16 canvas); explicit width/height win when both are sent.
        "aspect", "width", "height",
        # Draft render: low-res + hidden from the library (timeline preview).
        "preview",
    }
    settings = {k: v for k, v in (payload.settings or {}).items() if k in allowed}

    # Brand workspace defaults fill anything the request left blank. Explicit
    # values still win, and an unknown workspace is a 404 rather than silently
    # falling back to another brand.
    workspace_id = (payload.workspace_id or "").strip()
    if workspace_id:
        try:
            workspace_service.owner_workspace(workspace_id, user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Workspace not found.")
    settings = workspace_service.apply_defaults(
        settings, user.uid, workspace_id, allowed=allowed
    )
    settings["music_volume"] = clamp_music_volume(settings.get("music_volume"))

    # Per-user daily render quota (protects a free-tier backend from abuse).
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    renders_today = sum(
        1
        for j in jobs_service.list_jobs(user.uid, limit=200)
        if (j.get("createdAt") or "").startswith(today) and j.get("type") != "face"
    )
    if renders_today >= s.max_daily_renders:
        raise HTTPException(
            status_code=429,
            detail=f"Daily render limit reached ({s.max_daily_renders} videos). Try again tomorrow.",
        )

    cover = (payload.cover or "").strip() or None
    if cover and storage.kind == "local":
        if not cover.startswith(f"media/{user.uid}/"):
            raise HTTPException(status_code=400, detail="Cover image does not belong to you.")

    # User-uploaded music is referenced by its storage path; it must belong
    # to this user. (Built-in catalog names are passed through as-is.)
    chosen_music = (settings.get("music") or "").strip()
    if chosen_music.startswith("media/"):
        if not chosen_music.startswith(f"media/{user.uid}/"):
            raise HTTPException(status_code=400, detail="Music track does not belong to you.")

    # Cloned voices ("clone:<storagePath>") in the narrator slot, per-scene
    # voice, or a character's voice must belong to this user.
    from ..services.voice_refs import owned_clone_ref

    owned_clone_ref(str(settings.get("voice") or ""), user.uid)
    # `characters` and `scenes` are lists of plain dicts (see JobCreate), so
    # `getattr(item, "voice", "")` silently yields "" and this ownership check
    # never ran. `.get` is what the normalizers below actually read.
    for char in payload.characters or []:
        if isinstance(char, dict):
            owned_clone_ref(str(char.get("voice") or ""), user.uid)
    for scene in payload.scenes or []:
        if isinstance(scene, dict):
            owned_clone_ref(str(scene.get("voice") or ""), user.uid)

    niche = profiles.get_page_name(user.uid)
    job_id = jobs_service.create_job(
        uid=user.uid,
        title=(payload.title or "Untitled video").strip(),
        script=script,
        media=media,
        settings=settings,
        cover=cover,
        niche=niche,
        scenes=scenes or None,
        characters=characters or None,
        workspace_id=workspace_id,
    )

    worker: VideoWorker = request.app.state.worker
    await worker.submit(job_id)

    return CreateOut(job_id=job_id)


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    jobs = jobs_service.list_jobs(user.uid, limit=30)
    return [
        JobOut(
            job_id=j.get("jobId"),
            status=j.get("status", "queued"),
            stage=j.get("stage", "queued"),
            progress=int(j.get("progress", 0)),
            message=j.get("message", ""),
            title=j.get("title", "Untitled video"),
            created_at=j.get("createdAt", ""),
            updated_at=j.get("updatedAt", ""),
            error=j.get("error"),
        )
        for j in jobs
    ]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")
    return job | {"jobId": job_id}


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")

    storage = request.app.state.storage

    # Only delete this job's own artifacts (finished video + poster). The
    # uploaded media, cover, and face recordings are the user's library and
    # may be shared by other jobs ("Edit this video", "Retry"), so they are
    # kept here and only removed by the orphaned-media cleanup when they are
    # no longer referenced by any job.
    output = job.get("output") or {}
    for path in [output.get("storagePath"), output.get("posterStoragePath")]:
        if path:
            try:
                storage.delete_file(path)
            except Exception:
                pass

    jobs_service.delete_job(job_id)


@router.post("/jobs/{job_id}/face", status_code=status.HTTP_201_CREATED)
async def create_face_job(
    job_id: str,
    payload: dict,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Overlay a webcam recording onto an already-finished video."""
    parent = jobs_service.get_job(job_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Job not found.")
    if parent.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")
    if parent.get("type") == "face":
        raise HTTPException(status_code=400, detail="Can't add a face to a face video.")
    if not (parent.get("output") or {}).get("storagePath"):
        raise HTTPException(status_code=400, detail="The video isn't finished yet.")

    storage = request.app.state.storage
    recording_path = (payload.get("recordingPath") or "").strip()
    if not recording_path.startswith(f"media/{user.uid}/"):
        raise HTTPException(status_code=400, detail="Recording does not belong to you.")
    try:
        storage.resolve(recording_path)
    except Exception:
        raise HTTPException(status_code=400, detail="Recording not found.")

    duration = payload.get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Recording duration is required.")
    if not (0.5 <= duration <= 600):
        raise HTTPException(status_code=400, detail="Recording duration out of range.")

    overlay = payload.get("overlay") or {}
    shape = str(overlay.get("shape") or "circle").lower()
    if shape not in ("circle", "rounded", "rect"):
        raise HTTPException(status_code=400, detail="Invalid overlay shape.")
    try:
        scale = float(overlay.get("scale") or 18)
        x = float(overlay.get("x") or 0)
        y = float(overlay.get("y") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid overlay position.")
    if not (10 <= scale <= 40):
        raise HTTPException(status_code=400, detail="Overlay scale must be 10–40%.")
    if not (0 <= x <= 80 and 0 <= y <= 80):
        raise HTTPException(status_code=400, detail="Overlay position must be 0–80%.")
    try:
        rec_w = int(overlay["recordingWidth"])
        rec_h = int(overlay["recordingHeight"])
    except (TypeError, ValueError, KeyError):
        rec_w = rec_h = 0
    if rec_w < 0 or rec_h < 0:
        raise HTTPException(status_code=400, detail="Invalid recording dimensions.")

    face_overlay = {
        "shape": shape,
        "scale": scale,
        "x": x,
        "y": y,
        "recordingWidth": rec_w,
        "recordingHeight": rec_h,
    }

    new_id = jobs_service.create_face_job(
        uid=user.uid,
        parent_job_id=job_id,
        title=parent.get("title") or "Untitled video",
        recording_path=recording_path,
        recording_duration=duration,
        overlay=face_overlay,
        include_audio=bool(payload.get("includeAudio")),
    )

    worker: VideoWorker = request.app.state.worker
    await worker.submit(new_id)

    return {"jobId": new_id}


@router.patch("/jobs/{job_id}/title")
async def rename_job(
    job_id: str,
    payload: TitleIn,
    user: CurrentUser = Depends(get_current_user),
):
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title can't be empty.")
    jobs_service.update_job(job_id, title=title)
    return {"title": title}


@router.get("/jobs/{job_id}/media-url")
async def job_media_url(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Fresh playback URL for a completed job's output video."""
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")
    output = job.get("output") or {}
    storage_path = output.get("storagePath")
    if not storage_path:
        raise HTTPException(status_code=404, detail="No output video yet.")
    url = as_absolute(
        request.app.state.storage.media_url(storage_path),
        str(request.base_url),
    )
    return {"url": url}


@router.get("/jobs/{job_id}/share-url")
async def job_share_url(
    job_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Long-lived (7-day) playback URL + a public watch-page token for sharing."""
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your job.")
    output = job.get("output") or {}
    storage_path = output.get("storagePath")
    if not storage_path:
        raise HTTPException(status_code=404, detail="No output video yet.")
    s: Settings = request.app.state.settings
    token = create_share_token(job_id, s.sign_secret)
    return {
        "url": as_absolute(
            request.app.state.storage.share_url(storage_path),
            str(request.base_url),
        ),
        "token": token,
        "pageUrl": f"{s.frontend_url.rstrip('/')}/v/{token}",
    }
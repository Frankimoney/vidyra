"""Media upload and signed-URL media serving (local storage driver)."""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
import uuid

from fastapi.concurrency import run_in_threadpool
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

from pipeline.config import MAX_VIDEO_SEGMENTS, VIDEO_SEGMENT_SECONDS
from pipeline.utils import (
    MUSIC_UPLOAD_TAG,
    VOICE_UPLOAD_TAG,
    classify_media,
    get_duration,
    run_cmd,
)

from ..auth import CurrentUser, get_current_user
from ..config import Settings
from ..services import ratelimit
from ..services.storage.base import (
    _safe_resolve,
    as_absolute,
    verify_signed_url,
)

router = APIRouter()

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# A corrupt or hostile file must not pin an ffmpeg/ffprobe process forever and
# hold the upload request open with it.
PROBE_TIMEOUT_SECONDS = 60
SPLIT_TIMEOUT_SECONDS = 600
# A clip shorter than this cannot carry a beat, so treat the cut as failed
# rather than shipping a sliver into the timeline.
MIN_SEGMENT_SECONDS = 1.0
# Keyframe spacing forced onto sources too sparse to cut directly. Half the
# segment length guarantees a keyframe on every 4s boundary.
DENSE_KEYFRAME_SECONDS = 2.0


def _clean_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return name


def _store_plain(storage, uid: str, upload_id: str, filename: str, tmp_path: str) -> list:
    """Store one file as-is and describe it. Run in a worker thread."""
    storage_path = storage.save_media(uid, upload_id, filename, tmp_path)
    return [
        {
            "storagePath": storage_path,
            "name": filename,
            "size": os.path.getsize(tmp_path),
            "type": classify_media(filename),
        }
    ]


def _store_probed(
    storage,
    uid: str,
    upload_id: str,
    filename: str,
    tmp_path: str,
    label: str,
    tag: str = "",
) -> dict:
    """Store one recording/music file, probing its duration first.

    `get_duration` shells out to ffmpeg, which blocks the calling thread, so
    this only ever runs via `run_in_threadpool`. Left inline in an `async def`
    it would have frozen every other request in the app for the length of the
    probe.
    """
    try:
        duration = get_duration(tmp_path)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Could not read the {label} file.")
    if duration <= 0:
        raise HTTPException(status_code=400, detail=f"The {label} file has no playable content.")
    kwargs = {"tag": tag} if tag else {}
    storage_path = storage.save_media(uid, upload_id, filename, tmp_path, **kwargs)
    return {
        "storagePath": storage_path,
        "duration": round(duration, 2),
        "size": os.path.getsize(tmp_path),
        "type": classify_media(filename),
    }


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_media(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    file: UploadFile = File(...),
):
    s: Settings = request.app.state.settings
    ratelimit.rate_limiter.check(user.uid, s.rate_limit_per_min)
    storage = request.app.state.storage

    filename = _clean_filename(file.filename)
    kind = classify_media(filename)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")

    upload_id = uuid.uuid4().hex[:12]
    total = 0
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(filename)[1].lower()
        ) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > s.max_media_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds size limit.")
                tmp.write(chunk)

        # ffmpeg runs off the event loop: `_split_video` can take minutes on a
        # long upload, and inline it would have stalled every other request in
        # the app for the whole split.
        if kind == "video":
            items = await run_in_threadpool(
                _split_video, storage, user.uid, upload_id, filename, tmp_path
            )
        else:
            items = await run_in_threadpool(
                _store_plain, storage, user.uid, upload_id, filename, tmp_path
            )
        return {"items": items, "split": kind == "video" and len(items) > 1}
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _segment_ext(source: str) -> str:
    """Container to write cut segments into.

    Stream copy has to keep the codec, so the container has to be able to
    hold it: an .avi or .webm source stays in its own container rather than
    being forced into mp4.
    """
    ext = os.path.splitext(source)[1].lower()
    if ext in (".webm", ".mkv", ".avi"):
        return ext
    return ".mp4"


def _list_segments(dest_dir: str, ext: str) -> list[tuple[str, float]]:
    """Return (path, duration) for every segment written, oldest first."""
    out = []
    for name in sorted(os.listdir(dest_dir)):
        if not name.startswith("seg_") or not name.endswith(ext):
            continue
        path = os.path.join(dest_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            out.append((path, get_duration(path, timeout=PROBE_TIMEOUT_SECONDS)))
        except Exception:
            continue
    return out


def _clear_segments(dest_dir: str) -> None:
    for name in os.listdir(dest_dir):
        if name.startswith("seg_"):
            try:
                os.remove(os.path.join(dest_dir, name))
            except OSError:
                pass


def _segments_sane(segments: list[tuple[str, float]], duration: float) -> bool:
    """Guard the cut against a degenerate result.

    Stream copy cuts on the source's own keyframes, so segments land near
    VIDEO_SEGMENT_SECONDS rather than exactly on it. That is fine - the
    timeline sizes clips to the narration, and a longer clip just gets trimmed.
    What is not fine is losing footage, duplicating it, or emitting a sliver.
    """
    if not segments:
        return False
    if any(d < MIN_SEGMENT_SECONDS for _, d in segments):
        return False
    total = sum(d for _, d in segments)
    return duration * 0.98 <= total <= duration * 1.02


def _cut_video_copy(source: str, dest_dir: str, ext: str) -> None:
    """Cut by stream copy: no decode, no re-encode, a second or two at most.

    The previous implementation re-encoded every 4s segment in its own ffmpeg
    process, one after the other, and held the HTTP request open for the whole
    thing - long enough to blow past the client's upload timeout on anything
    past ~60-90s of footage. Copying instead of re-encoding is what makes the
    upload finish in time.
    """
    run_cmd(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", source,
            "-map", "0:v:0", "-an",
            "-c:v", "copy",
            "-f", "segment",
            "-segment_time", str(VIDEO_SEGMENT_SECONDS),
            "-reset_timestamps", "1",
            os.path.join(dest_dir, f"seg_%03d{ext}"),
        ],
        timeout=SPLIT_TIMEOUT_SECONDS,
    )


def _reencode_with_dense_keyframes(source: str, target: str) -> None:
    """Re-encode to uniform H.264 with a keyframe every 2s.

    A 2s grid guarantees a keyframe lands exactly on every 4s segment
    boundary, so the copy pass that follows produces clean cuts. Written as a
    single output (not a split/trim filter graph, which needs one branch per
    segment and exhausts memory on 1080p) so it stays cheap.
    """
    run_cmd(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", source,
            "-map", "0:v:0", "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-g", str(int(30 * DENSE_KEYFRAME_SECONDS)),
            "-force_key_frames", f"expr:gte(t,n_forced*{DENSE_KEYFRAME_SECONDS})",
            "-movflags", "+faststart",
            target,
        ],
        timeout=SPLIT_TIMEOUT_SECONDS,
    )


def _split_video(storage, uid: str, upload_id: str, filename: str, source: str) -> list[dict]:
    """Cut a video into ~4s segment files stored under media/{uid}/{upload_id}/.

    The original source is not kept - the segments cover its entire content.
    Each segment's real duration is returned so the timeline can size it to the
    narration instead of guessing.
    """
    try:
        duration = get_duration(source, timeout=PROBE_TIMEOUT_SECONDS)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the video file.")
    if duration <= 0:
        raise HTTPException(status_code=400, detail="The video has no playable content.")

    count = max(1, int(-(-duration // VIDEO_SEGMENT_SECONDS)))
    if count > MAX_VIDEO_SEGMENTS:
        limit = int(MAX_VIDEO_SEGMENTS * VIDEO_SEGMENT_SECONDS)
        raise HTTPException(
            status_code=413,
            detail=f"Videos must be {limit // 60} minutes or shorter.",
        )

    rel_dir = f"media/{uid}/{upload_id}"
    work_root = tempfile.mkdtemp(prefix="vidyra-split-")
    dest_dir = os.path.join(work_root, "segments")
    work = os.path.join(work_root, "work")
    ext = _segment_ext(source)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        try:
            _cut_video_copy(source, dest_dir, ext)
            segments = _list_segments(dest_dir, ext)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not split the video: {str(exc)[:200]}"
            )

        if not _segments_sane(segments, duration):
            # Keyframes too sparse to cut near 4s: the copy pass emitted
            # overlapping clips (duplicated footage) or slivers. Normalise the
            # keyframe grid to 2s and cut again.
            try:
                _clear_segments(dest_dir)
                os.makedirs(work, exist_ok=True)
                dense = os.path.join(work, f"dense{ext}")
                _reencode_with_dense_keyframes(source, dense)
                _cut_video_copy(dense, dest_dir, ext)
                segments = _list_segments(dest_dir, ext)
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Could not split the video: {str(exc)[:200]}"
                )

        if not _segments_sane(segments, duration):
            raise HTTPException(
                status_code=400,
                detail="Could not cut this video into usable clips. Try exporting it as H.264 MP4.",
            )

        items = []
        for path, seconds in segments:
            name = os.path.basename(path)
            rel = f"{rel_dir}/{name}"
            size = os.path.getsize(path)
            storage.publish(path, rel)
            items.append(
                {
                    "storagePath": rel,
                    "name": name,
                    "size": size,
                    "type": "video",
                    "duration": round(seconds, 3),
                }
            )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    if len(items) == 1:
        items[0]["name"] = filename

    return items


@router.post("/uploads/face", status_code=status.HTTP_201_CREATED)
async def upload_face_recording(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    file: UploadFile = File(...),
):
    """Store a webcam face recording as a single, unsplit file.

    Unlike POST /uploads (which splits long videos into ~4s segments), a face
    recording must stay intact so its timeline lines up with the finished video.
    """
    s: Settings = request.app.state.settings
    ratelimit.rate_limiter.check(user.uid, s.rate_limit_per_min)
    storage = request.app.state.storage

    filename = _clean_filename(file.filename)
    kind = classify_media(filename)
    if kind not in ("video", "audio"):
        raise HTTPException(status_code=400, detail=f"Unsupported recording type: {filename}")

    upload_id = uuid.uuid4().hex[:12]
    total = 0
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(filename)[1].lower() or ".webm"
        ) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > s.max_media_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds size limit.")
                tmp.write(chunk)

        return await run_in_threadpool(
            _store_probed,
            storage,
            user.uid,
            upload_id,
            filename or "face.webm",
            tmp_path,
            "recording",
        )
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.post("/uploads/music", status_code=status.HTTP_201_CREATED)
async def upload_music(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    file: UploadFile = File(...),
    purpose: str = Form("music"),
):
    """Store an uploaded audio file as a single, unsplit file.

    The file lives under media/{uid}/music-… or media/{uid}/voice-… so it is
    only offered where it belongs: music tracks in the music catalog, voice
    samples in the AI Studio voice-clone list.
    """
    s: Settings = request.app.state.settings
    ratelimit.rate_limiter.check(user.uid, s.rate_limit_per_min)
    storage = request.app.state.storage

    if purpose not in (MUSIC_UPLOAD_TAG, VOICE_UPLOAD_TAG):
        raise HTTPException(status_code=400, detail=f"Unknown audio purpose: {purpose}")

    filename = _clean_filename(file.filename)
    kind = classify_media(filename)
    if kind != "audio":
        raise HTTPException(status_code=400, detail=f"Unsupported music file: {filename}")

    upload_id = uuid.uuid4().hex[:12]
    total = 0
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(filename)[1].lower()
        ) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > s.max_media_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds size limit.")
                tmp.write(chunk)

        stored = await run_in_threadpool(
            _store_probed, storage, user.uid, upload_id, filename, tmp_path, "audio", purpose
        )
        stored["name"] = filename
        stored["purpose"] = purpose
        return stored
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.get("/uploads/preview")
async def media_preview(
    path: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Signed thumbnail URL for one of the caller's own media files."""
    allowed = (f"media/{user.uid}/", f"studio/{user.uid}/", f"videos/{user.uid}/")
    if not (path or "").startswith(allowed):
        raise HTTPException(status_code=403, detail="Not your media.")
    storage = request.app.state.storage
    try:
        url = storage.media_url(path)
    except Exception:
        raise HTTPException(status_code=404, detail="Media not found.")
    return {"url": as_absolute(url, str(request.base_url))}


@router.get("/library")
async def media_library(
    request: Request,
    kind: str = "",
    purpose: str = "",
    user: CurrentUser = Depends(get_current_user),
):
    """Every image/video/audio the caller owns (uploads + AI Studio + renders).

    Audio items carry a "purpose" of "music" or "voice" so callers can offer a
    track as background music or as a voice-clone sample, never both.
    """
    storage = request.app.state.storage
    if not hasattr(storage, "list_user_files"):
        return {"items": []}
    items = storage.list_user_files(user.uid)
    if kind in ("image", "video", "audio"):
        items = [it for it in items if it["kind"] == kind]
    if purpose in (MUSIC_UPLOAD_TAG, VOICE_UPLOAD_TAG):
        items = [it for it in items if it.get("purpose") == purpose]
    base = str(request.base_url)
    for it in items:
        it["url"] = as_absolute(it["url"], base)
    return {"items": items}


def _owned_media_path(path: str, uid: str) -> str:
    """Validate a library path belongs to the caller; return it as-is.

    Same ownership prefixes as the signed serving route: uploads, AI Studio
    results and rendered videos. Traversal is rejected outright — a `..`
    segment would otherwise pass a naive ``startswith`` check.
    """
    path = (path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="A storage path is required.")
    if "\\" in path or any(seg in ("", ".", "..") for seg in path.split("/")):
        raise HTTPException(status_code=400, detail="Invalid storage path.")
    if not path.startswith((f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/")):
        raise HTTPException(status_code=403, detail="Not your media.")
    return path


@router.delete("/media/{storage_path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    storage_path: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Delete one of the caller's own library files (upload, Studio out, render)."""
    path = _owned_media_path(storage_path, user.uid)
    storage = request.app.state.storage
    if not hasattr(storage, "delete_file"):
        raise HTTPException(status_code=501, detail="This storage driver cannot delete files.")
    removed = await run_in_threadpool(storage.delete_file, path)
    if not removed:
        raise HTTPException(status_code=404, detail="Media not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/media/{storage_path:path}")
async def serve_media(
    storage_path: str,
    request: Request,
    exp: int = 0,
    sig: str = "",
):
    """Serve a stored file to a valid signed URL, with HTTP Range support."""
    storage = request.app.state.storage
    if storage.kind != "local":
        raise HTTPException(status_code=404, detail="Not found.")
    driver = storage

    if not verify_signed_url(storage_path, exp, sig, driver.secret):
        raise HTTPException(status_code=403, detail="Invalid or expired media link.")

    try:
        full = _safe_resolve(driver.root, storage_path)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid storage path.")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Media not found.")

    media_type = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
    size = os.path.getsize(full)
    range_header = request.headers.get("range", "").strip()
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=600",
        "Content-Disposition": "inline",
    }

    m = _RANGE_RE.match(range_header) if range_header else None
    if m:
        start_s, end_s = m.group(1), m.group(2)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start >= size or start > end:
            headers["Content-Range"] = f"bytes */{size}"
            return Response(status_code=416, headers=headers)
        length = end - start + 1
        with open(full, "rb") as fh:
            fh.seek(start)
            data = fh.read(length)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
        return Response(status_code=206, content=data, media_type=media_type, headers=headers)

    with open(full, "rb") as fh:
        data = fh.read()
    headers["Content-Length"] = str(size)
    return Response(content=data, media_type=media_type, headers=headers)
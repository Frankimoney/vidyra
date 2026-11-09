"""Job document helpers — Firestore, with an in-memory fallback for local dev.

When Firebase credentials aren't configured (local testing before you create
the Spark project), jobs live in a process-local dict. This lets the whole
upload → render → result flow be exercised locally; it is NOT durable across
restarts and is never used once Firestore is available.
"""

from __future__ import annotations

import datetime
import uuid

from firebase_admin import firestore

from .firebase import firebase

COLLECTION = "videos"

# Dev-only fallback store: job_id -> document fields.
_memory_store: dict[str, dict] = {}


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _using_dev_store() -> bool:
    return firebase.db is None


def _db():
    if firebase.db is None:
        raise RuntimeError("Firestore is not configured.")
    return firebase.db


def _firestore_doc(job_id: str):
    return _db().collection(COLLECTION).document(job_id)


def create_job(
    uid: str,
    title: str,
    script: str,
    media: list,
    settings: dict,
    cover: str | None = None,
    niche: str = "",
    scenes: list | None = None,
    characters: list | None = None,
    workspace_id: str = "",
) -> str:
    job_id = uuid.uuid4().hex[:16]
    now = _now_stamp()
    doc = {
        "userId": uid,
        "title": title,
        "script": script,
        "scriptWordCount": len(script.split()) if script else 0,
        "media": media,
        "cover": cover,
        "settings": settings,
        "niche": niche,
        "workspaceId": workspace_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "output": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if scenes:
        doc["scenes"] = scenes
        doc["type"] = "storyboard"
    if characters:
        doc["characters"] = characters
    if _using_dev_store():
        _memory_store[job_id] = doc
    else:
        _firestore_doc(job_id).set(doc)
    return job_id


def create_face_job(
    uid: str,
    parent_job_id: str,
    title: str,
    recording_path: str,
    recording_duration: float,
    overlay: dict,
    include_audio: bool,
) -> str:
    """Create a composite-only job that overlays a webcam recording onto an
    already-finished video. Never re-runs the generation pipeline."""
    job_id = uuid.uuid4().hex[:16]
    now = _now_stamp()
    doc = {
        "userId": uid,
        "title": title,
        "type": "face",
        "parentJobId": parent_job_id,
        "script": "",
        "scriptWordCount": 0,
        "media": [],
        "settings": {},
        "recordingPath": recording_path,
        "recordingDuration": recording_duration,
        "faceOverlay": overlay,
        "faceAudio": include_audio,
        "cover": None,
        "niche": "",
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "output": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if _using_dev_store():
        _memory_store[job_id] = doc
    else:
        _firestore_doc(job_id).set(doc)
    return job_id


def create_film_job(
    uid: str,
    title: str,
    film: dict,
    settings: dict,
    niche: str = "",
    workspace_id: str = "",
) -> str:
    """Create an auto-generated film job. `film` holds the user's idea + options;
    the worker expands it into scenes and renders them."""
    job_id = uuid.uuid4().hex[:16]
    now = _now_stamp()
    doc = {
        "userId": uid,
        "title": title,
        "type": "film",
        "film": film,
        "script": "",
        "scriptWordCount": 0,
        "media": [],
        "cover": None,
        "settings": settings,
        "niche": niche,
        "workspaceId": workspace_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "output": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if _using_dev_store():
        _memory_store[job_id] = doc
    else:
        _firestore_doc(job_id).set(doc)
    return job_id


def get_job(job_id: str) -> dict | None:
    if _using_dev_store():
        doc = _memory_store.get(job_id)
        return dict(doc) if doc else None
    ref = _firestore_doc(job_id)
    snap = ref.get()
    if not snap.exists:
        return None
    return snap.to_dict()


def _is_preview(doc: dict) -> bool:
    """Draft renders are polling-only and never appear in the library."""
    return bool((doc.get("settings") or {}).get("preview"))


def list_jobs(uid: str, limit: int = 30) -> list:
    if _using_dev_store():
        items = [
            (job_id, d)
            for job_id, d in _memory_store.items()
            if d.get("userId") == uid and not _is_preview(d)
        ]
        items.sort(key=lambda pair: pair[1].get("createdAt", ""), reverse=True)
        return [{**d, "jobId": job_id} for job_id, d in items[:limit]]
    snaps = (
        _db()
        .collection(COLLECTION)
        .where("userId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .get()
    )
    return [
        s.to_dict() | {"jobId": s.id}
        for s in snaps
        if not _is_preview(s.to_dict())
    ]


def update_job(job_id: str, **fields):
    fields["updatedAt"] = _now_stamp()
    if _using_dev_store():
        doc = _memory_store.get(job_id)
        if doc is not None:
            doc.update(fields)
        return
    _firestore_doc(job_id).update(fields)


def delete_job(job_id: str) -> None:
    if _using_dev_store():
        _memory_store.pop(job_id, None)
        return
    _firestore_doc(job_id).delete()
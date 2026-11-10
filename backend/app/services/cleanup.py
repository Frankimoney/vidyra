"""Maintenance: reconcile interrupted work on boot, then delete expired jobs.

The first function exists because the backend is the only thing driving a render.
If the process dies — power cut, closed laptop, Ctrl-C — any job still marked
queued or processing has no worker behind it any more, but its document keeps
claiming otherwise. Left alone it looks like live work forever, and the UI
refuses to delete a job it believes is running. So on startup we mark those
documents failed with a message that says what actually happened.
"""

from __future__ import annotations

import datetime
import os
import time

from . import jobs as jobs_service
from . import studio as studio_service
from .firebase import firebase
from .storage.base import _safe_resolve

# Statuses that mean "this document is attached to a worker". A queued job is
# included: a job created just before the crash never got picked up, and it will
# never get picked up now, so it is equally stranded.
IN_FLIGHT_STATUSES = ("queued", "processing")

CLONE_VOICE_PREFIX = "clone:"


def _add_clone_ref(referenced: set, value) -> None:
    """Record the storage path behind a "clone:<path>" voice, if it is one.

    Built-in Piper voice names carry no path, so they add nothing.
    """
    if isinstance(value, str) and value.startswith(CLONE_VOICE_PREFIX):
        path = value[len(CLONE_VOICE_PREFIX):].strip()
        if path:
            referenced.add(path)

INTERRUPTED_MESSAGE = (
    "Interrupted: the app was closed or lost power while this was running, so it "
    "never finished. Your script and uploads are still saved — start it again to "
    "render from the beginning."
)


def _is_in_flight(doc: dict) -> bool:
    return (doc.get("status") or "").strip().lower() in IN_FLIGHT_STATUSES


def fail_interrupted_jobs() -> int:
    """Mark stranded jobs/runs failed. Returns how many documents were changed.

    Safe to run only at startup: a single backend process owns every render, and
    at boot it has not claimed anything yet, so any in-flight document belongs
    to a previous process that is definitively gone. Completed and already-failed
    documents are left untouched.

    The dev-mode in-memory stores are process-local and therefore empty after a
    restart, so there is nothing to reconcile there — this only has work to do
    once Firestore is backing the app.
    """
    db = firebase.db
    if db is None:
        return 0

    changed = 0
    # (collection name, update function) -- jobs and studio runs are separate
    # documents with separate services but the same status vocabulary.
    for collection, update in (
        (jobs_service.COLLECTION, jobs_service.update_job),
        (studio_service.COLLECTION, studio_service.update_run),
    ):
        try:
            snaps = db.collection(collection).get()
        except Exception:
            continue
        for snap in snaps:
            doc = snap.to_dict() or {}
            if not _is_in_flight(doc):
                continue
            try:
                update(
                    snap.id,
                    status="failed",
                    stage="failed",
                    message=INTERRUPTED_MESSAGE,
                    error=INTERRUPTED_MESSAGE,
                    # Keep whatever progress it reached so the card can still
                    # report how far it got before the interruption.
                    progress=doc.get("progress") or 0,
                )
                changed += 1
            except Exception:
                continue
    return changed


def delete_job_files(uid: str, job: dict, storage) -> None:
    """Delete the finished video + poster owned by one job.

    Uploaded media, the cover, face recordings, and music are the user's
    library — they may be referenced by other jobs (edits/retries), so they
    are kept here and only removed by clean_orphaned_media when unreferenced.
    """
    job_id = (job.get("jobId") or "").strip()
    if not job_id:
        # Refuse to unlink media we cannot then delete the record for: the
        # files would be gone while the job survived, which is worse than
        # keeping both past the retention window.
        return

    output = job.get("output") or {}
    # Delete the record FIRST. If this fails the files are still intact and the
    # job stays playable; the reverse order loses the video but keeps the card.
    jobs_service.delete_job(job_id)

    for path in [output.get("storagePath"), output.get("posterStoragePath")]:
        if path:
            try:
                storage.delete_file(path)
            except Exception:
                pass


def clean_expired_jobs(settings, storage) -> int:
    """Remove jobs older than the retention window. Returns how many were removed."""
    db = firebase.db
    if db is None:
        # Dev memory store: no retention cleanup needed here.
        return 0

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=settings.job_retention_days)
    ).isoformat()

    removed = 0
    # Spark-scale scan: read all video docs and filter on the client.
    for snap in db.collection(jobs_service.COLLECTION).get():
        doc = snap.to_dict()
        created = doc.get("createdAt") or ""
        if created and created < cutoff:
            # `to_dict()` does not carry the document id, and jobs never store
            # `jobId` inside the doc -- so it must come from `snap.id`. Without
            # it the files are unlinked but the record survives, leaving a
            # "My Videos" card pointing at a deleted video, forever.
            doc = {**doc, "jobId": snap.id}
            try:
                delete_job_files(doc.get("userId", "unknown"), doc, storage)
                removed += 1
            except Exception:
                continue
    return removed


def clean_orphaned_media(settings, storage) -> int:
    """Delete user uploads no job references and older than the retention window.

    Covers clips, covers, face recordings, and user music. Local-storage only:
    walk media/{uid} and cross-check every file against the storage paths
    referenced by all current job documents. Returns how many files were removed.
    """
    if storage.kind != "local" or firebase.db is None:
        return 0

    referenced: set[str] = set()
    for snap in firebase.db.collection(jobs_service.COLLECTION).get():
        doc = snap.to_dict()
        for m in doc.get("media") or []:
            p = m if isinstance(m, str) else m.get("path", "")
            if p:
                referenced.add(p)
        if doc.get("cover"):
            referenced.add(doc["cover"])
        if doc.get("recordingPath"):
            referenced.add(doc["recordingPath"])
        music = (doc.get("settings") or {}).get("music")
        if isinstance(music, str) and music:
            referenced.add(music)
        # Cloned voices are stored as "clone:<path>" in the narrator slot, in
        # per-scene voices and per-character voices, and never in `media[]`.
        # Missing them here deletes a voice sample a character still points at,
        # which breaks every later render using that voice.
        settings_voice = (doc.get("settings") or {}).get("voice")
        _add_clone_ref(referenced, settings_voice)
        for scene in doc.get("scenes") or []:
            if isinstance(scene, dict):
                _add_clone_ref(referenced, scene.get("voice"))
        for char in doc.get("characters") or []:
            if isinstance(char, dict):
                _add_clone_ref(referenced, char.get("voice"))
                if char.get("portrait"):
                    referenced.add(char["portrait"])

    # Character library entries and project cast portraits are referenced from
    # their own collections, so they must count as live too.
    for snap in firebase.db.collection("characters").get():
        doc = snap.to_dict()
        _add_clone_ref(referenced, doc.get("voice"))
        if doc.get("refImage"):
            referenced.add(doc["refImage"])
    for snap in firebase.db.collection("projects").get():
        for member in (snap.to_dict().get("cast") or []):
            if isinstance(member, dict):
                if member.get("portrait"):
                    referenced.add(member["portrait"])
                _add_clone_ref(referenced, member.get("voice"))

    cutoff = time.time() - settings.job_retention_days * 24 * 3600
    try:
        media_root = _safe_resolve(storage.root, "media")
    except Exception:
        return 0
    if not os.path.isdir(media_root):
        return 0

    removed = 0
    for dirpath, _dirs, files in os.walk(media_root):
        for filename in files:
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, storage.root).replace("\\", "/")
            if rel in referenced:
                continue
            try:
                if os.path.getmtime(full) >= cutoff:
                    continue
            except OSError:
                continue
            try:
                if storage.delete_file(rel):
                    removed += 1
            except Exception:
                continue
    return removed
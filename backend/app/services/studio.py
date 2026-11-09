"""AI Studio run documents — Firestore, with an in-memory fallback for local dev.

Mirrors services/jobs.py: runs for the RunPod-backed AI tools live in a
process-local dict when Firebase isn't configured so the whole txt2img →
img2video → motion/v2v flow can be exercised locally (see RUNPOD_FAKE=1).
"""

from __future__ import annotations

import datetime
import uuid

from firebase_admin import firestore

from .firebase import firebase

COLLECTION = "studio_runs"

# Dev-only fallback store: run_id -> document fields.
_memory_store: dict[str, dict] = {}


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db():
    if firebase.db is None:
        raise RuntimeError("Firestore is not configured.")
    return firebase.db


def create_run(
    uid: str,
    tool: str,
    model: str,
    prompt: str,
    params: dict,
    refs: list,
    title: str = "",
) -> str:
    run_id = uuid.uuid4().hex[:16]
    now = _now_stamp()
    doc = {
        "userId": uid,
        "tool": tool,
        "model": model,
        "prompt": prompt,
        "params": params or {},
        "refs": refs or [],
        "title": title or f"AI {tool}",
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "runpodRunId": None,
        "output": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if firebase.db is None:
        _memory_store[run_id] = doc
    else:
        _db().collection(COLLECTION).document(run_id).set(doc)
    return run_id


def daily_used(uid: str, day: str | None = None) -> int:
    """AI runs the user has spent so far today across all tools.

    Every GPU call the worker or the Studio tab makes creates one run doc, so
    the daily budget is the count of docs stamped today. limit=200 sits well
    above the real ceiling, keeping the count exact for sensible budgets.
    """
    day = day or _now_stamp()[:10]
    return sum(
        1
        for r in list_runs(uid, limit=200)
        if (r.get("createdAt") or "").startswith(day)
    )


def get_run(run_id: str) -> dict | None:
    if firebase.db is None:
        doc = _memory_store.get(run_id)
        return dict(doc) if doc else None
    snap = _db().collection(COLLECTION).document(run_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


def list_runs(uid: str, limit: int = 30) -> list:
    if firebase.db is None:
        items = [(run_id, d) for run_id, d in _memory_store.items() if d.get("userId") == uid]
        items.sort(key=lambda pair: pair[1].get("createdAt", ""), reverse=True)
        return [{**d, "runId": run_id} for run_id, d in items[:limit]]
    snaps = (
        _db()
        .collection(COLLECTION)
        .where("userId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .get()
    )
    return [s.to_dict() | {"runId": s.id} for s in snaps]


def update_run(run_id: str, **fields):
    fields["updatedAt"] = _now_stamp()
    if firebase.db is None:
        doc = _memory_store.get(run_id)
        if doc is not None:
            doc.update(fields)
        return
    _db().collection(COLLECTION).document(run_id).update(fields)


def delete_run(run_id: str) -> None:
    if firebase.db is None:
        _memory_store.pop(run_id, None)
        return
    _db().collection(COLLECTION).document(run_id).delete()


def store_artifact(run_id: str, entry: dict):
    """Append a received artifact to the run's output.artifacts list."""
    run = get_run(run_id)
    if not run:
        return
    output = run.get("output") or {}
    artifacts = list(output.get("artifacts") or [])
    artifacts.append(entry)
    output = {**output, "artifacts": artifacts}
    update_run(run_id, output=output)
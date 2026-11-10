"""Recurring render series — a saved job template plus a cadence.

A series re-renders the same scene/media template on an interval (for example,
a branded weekly recap shell a creator refreshes with new facts later). It is
deliberately separate from publishing: this only schedules *generation*.
"""

from __future__ import annotations

import datetime
import uuid

from .firebase import firebase

COLLECTION = "series"
_memory_store: dict[str, dict] = {}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(value: datetime.datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _db():
    return firebase.db


def _clean(uid: str, payload: dict, series_id: str = "") -> dict:
    try:
        interval = int(payload.get("intervalHours") or payload.get("interval_hours") or 24)
    except (TypeError, ValueError):
        interval = 24
    interval = max(1, min(720, interval))
    now = _now()
    return {
        "seriesId": series_id or uuid.uuid4().hex[:16],
        "userId": uid,
        "name": str(payload.get("name") or "Recurring series").strip()[:80],
        "workspaceId": str(payload.get("workspace_id") or payload.get("workspaceId") or "")[:64],
        "intervalHours": interval,
        "enabled": bool(payload.get("enabled", True)),
        "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
        "nextRunAt": str(payload.get("nextRunAt") or _stamp(now + datetime.timedelta(hours=interval))),
        "createdAt": str(payload.get("createdAt") or _stamp(now)),
        "updatedAt": _stamp(now),
    }


def _out(doc: dict) -> dict:
    return {
        "series_id": doc.get("seriesId") or "",
        "name": doc.get("name") or "",
        "workspace_id": doc.get("workspaceId") or "",
        "interval_hours": int(doc.get("intervalHours") or 24),
        "enabled": bool(doc.get("enabled", True)),
        "next_run_at": doc.get("nextRunAt") or "",
        "created_at": doc.get("createdAt") or "",
        "updated_at": doc.get("updatedAt") or "",
    }


def _store(doc: dict) -> None:
    if _db() is None:
        _memory_store[doc["seriesId"]] = doc
    else:
        _db().collection(COLLECTION).document(doc["seriesId"]).set(doc)


def create_series(uid: str, payload: dict) -> dict:
    doc = _clean(uid, payload)
    _store(doc)
    return doc


def list_series(uid: str) -> list[dict]:
    if _db() is None:
        docs = [dict(d) for d in _memory_store.values() if d.get("userId") == uid]
    else:
        docs = [
            snap.to_dict()
            for snap in _db().collection(COLLECTION).where("userId", "==", uid).stream()
        ]
    docs.sort(key=lambda d: d.get("createdAt") or "")
    return docs


def get_series(uid: str, series_id: str) -> dict:
    if _db() is None:
        doc = _memory_store.get(series_id)
    else:
        snap = _db().collection(COLLECTION).document(series_id).get()
        doc = snap.to_dict() if snap.exists else None
    if not doc or doc.get("userId") != uid:
        raise KeyError(series_id)
    return doc


def update_series(uid: str, series_id: str, patch: dict) -> dict:
    doc = get_series(uid, series_id)
    if "name" in patch:
        doc["name"] = str(patch.get("name") or "").strip()[:80] or doc.get("name")
    if "enabled" in patch:
        doc["enabled"] = bool(patch.get("enabled"))
    if "interval_hours" in patch or "intervalHours" in patch:
        try:
            interval = int(patch.get("interval_hours") or patch.get("intervalHours"))
        except (TypeError, ValueError):
            interval = doc.get("intervalHours") or 24
        doc["intervalHours"] = max(1, min(720, interval))
        doc["nextRunAt"] = _stamp(_now() + datetime.timedelta(hours=doc["intervalHours"]))
    if isinstance(patch.get("payload"), dict):
        doc["payload"] = patch["payload"]
    doc["updatedAt"] = _stamp()
    _store(doc)
    return doc


def delete_series(uid: str, series_id: str) -> None:
    get_series(uid, series_id)
    if _db() is None:
        _memory_store.pop(series_id, None)
    else:
        _db().collection(COLLECTION).document(series_id).delete()


def due_series(limit: int = 50) -> list[dict]:
    now = _stamp()
    if _db() is None:
        docs = [dict(d) for d in _memory_store.values()]
    else:
        docs = [
            snap.to_dict()
            for snap in _db().collection(COLLECTION).where("enabled", "==", True).limit(limit).stream()
        ]
    return [
        d for d in docs
        if d.get("enabled") and (d.get("nextRunAt") or "") <= now
    ]


def mark_ran(series_id: str) -> dict:
    if _db() is None:
        doc = dict(_memory_store.get(series_id) or {})
    else:
        snap = _db().collection(COLLECTION).document(series_id).get()
        doc = snap.to_dict() if snap.exists else {}
    if not doc:
        raise KeyError(series_id)
    interval = max(1, int(doc.get("intervalHours") or 24))
    doc["nextRunAt"] = _stamp(_now() + datetime.timedelta(hours=interval))
    doc["updatedAt"] = _stamp()
    _store(doc)
    return doc

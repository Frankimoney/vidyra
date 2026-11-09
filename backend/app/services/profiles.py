"""User profile helpers — Firestore, with an in-memory fallback for local dev."""

from __future__ import annotations

import datetime

from firebase_admin import firestore as _fs

from .firebase import firebase

COLLECTION = "users"

# Dev-only fallback store: uid -> {pageName, updatedAt}
_memory_store: dict[str, dict] = {}


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db():
    if firebase.db is None:
        return None
    return firebase.db


def get_page_name(uid: str) -> str:
    if not uid:
        return ""
    db = _db()
    if db is None:
        return (_memory_store.get(uid) or {}).get("pageName", "")
    snap = db.collection(COLLECTION).document(uid).get()
    if not snap.exists:
        return ""
    return (snap.to_dict() or {}).get("pageName", "")


def set_page_name(uid: str, page_name: str) -> None:
    if not uid:
        return
    now = _now_stamp()
    db = _db()
    if db is None:
        _memory_store[uid] = {"pageName": page_name, "updatedAt": now}
        return
    db.collection(COLLECTION).document(uid).set(
        {"pageName": page_name, "updatedAt": now}
    )
"""Planning projects — Firestore, with an in-memory fallback for local dev.

A "project" is the conversation + production plan behind one film, kept
between sessions so the planning agent remembers what was already decided.
Mirrors services/studio.py: documents live in a process-local dict when
Firebase isn't configured, so the whole flow is exercisable locally.

Thread messages are stored inline on the document rather than in a
subcollection, so a project's chat is always read in one round trip. That
keeps the document bounded by MAX_MESSAGES — Firestore caps a doc at ~1 MiB and
a planning conversation has no reason to grow past a few dozen turns.
"""

from __future__ import annotations

import datetime
import uuid

from firebase_admin import firestore

from .firebase import firebase

COLLECTION = "projects"

#: Hard cap on stored turns. The oldest are dropped first so a long-lived
#: project can't grow past the Firestore document limit.
MAX_MESSAGES = 80

#: Characters kept per message, so one pasted screenplay can't bloat the doc.
MAX_MESSAGE_CHARS = 8000

# Dev-only fallback store: project_id -> document fields.
_memory_store: dict[str, dict] = {}

_PLANNING = "planning"
_PLANNED = "planned"
_FILMED = "filmed"


class ProjectError(RuntimeError):
    pass


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db():
    if firebase.db is None:
        raise RuntimeError("Firestore is not configured.")
    return firebase.db


def _clean(messages) -> list:
    """Normalize + bound a chat thread for storage."""
    out: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", "") or "").strip()
        if not content:
            continue
        role = str(m.get("role", "user") or "user").lower()
        entry = {
            "role": "assistant" if role == "assistant" else "user",
            "content": content[:MAX_MESSAGE_CHARS],
        }
        if m.get("at"):
            entry["at"] = str(m["at"])
        out.append(entry)
    return out[-MAX_MESSAGES:]


def create_project(uid: str, message: str, aspect: str = "auto", kind: str = "film") -> str:
    project_id = uuid.uuid4().hex[:16]
    now = _now_stamp()
    first = _clean([{"role": "user", "content": message, "at": now}])
    doc = {
        "userId": uid,
        "kind": kind or "film",
        "title": message.strip().splitlines()[0][:80] if message.strip() else "Untitled Project",
        "messages": first,
        "brief": {},
        "plan": None,
        "cast": [],
        "questions": [],
        "status": _PLANNING,
        "aspect": aspect or "auto",
        "jobId": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if firebase.db is None:
        _memory_store[project_id] = doc
    else:
        _db().collection(COLLECTION).document(project_id).set(doc)
    return project_id


def get_project(project_id: str) -> dict | None:
    if firebase.db is None:
        doc = _memory_store.get(project_id)
        return dict(doc) | {"projectId": project_id} if doc else None
    snap = _db().collection(COLLECTION).document(project_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() | {"projectId": snap.id}


def list_projects(uid: str, limit: int = 30) -> list:
    if firebase.db is None:
        items = [
            (pid, d) for pid, d in _memory_store.items() if d.get("userId") == uid
        ]
        items.sort(key=lambda pair: pair[1].get("updatedAt", ""), reverse=True)
        return [{**d, "projectId": pid} for pid, d in items[:limit]]
    snaps = (
        _db()
        .collection(COLLECTION)
        .where("userId", "==", uid)
        .order_by("updatedAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .get()
    )
    return [s.to_dict() | {"projectId": s.id} for s in snaps]


def update_project(project_id: str, **fields):
    fields["updatedAt"] = _now_stamp()
    if "messages" in fields:
        fields["messages"] = _clean(fields["messages"])
    if firebase.db is None:
        doc = _memory_store.get(project_id)
        if doc is not None:
            doc.update(fields)
        return
    _db().collection(COLLECTION).document(project_id).update(fields)


def append_messages(project_id: str, messages: list) -> list:
    """Append turns to the thread and return the stored thread."""
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found.")
    thread = _clean(list(project.get("messages") or []) + list(messages or []))
    update_project(project_id, messages=thread)
    return thread


def delete_project(project_id: str) -> None:
    if firebase.db is None:
        _memory_store.pop(project_id, None)
        return
    _db().collection(COLLECTION).document(project_id).delete()


def owner_project(project_id: str, uid: str) -> dict:
    """Fetch a project and assert ownership, or raise ProjectError."""
    project = get_project(project_id)
    if not project or project.get("userId") != uid:
        # Same response for "missing" and "someone else's" so project ids
        # can't be probed for existence.
        raise ProjectError("Project not found.")
    return project


# --------------------------------------------------------------------------
# Cast — the project's persistent character identities
# --------------------------------------------------------------------------
#
# A project's characters are its "ingredients": the portrait the crew locked for
# Mara in film one is the same portrait film three restyles from, so a face
# doesn't drift between videos. Entries are keyed by name (case-insensitive).


def _key(name: str) -> str:
    return (name or "").strip().lower()


def sync_cast(project_id: str, characters: list) -> list:
    """Merge plan characters into the cast, keeping portraits already locked.

    Planning rewrites descriptions freely; a re-plan must never throw away the
    face that earlier films were rendered from. Characters are also pushed into
    the shared library so Studio and Storyboard see the same identities.
    """
    from . import characters as characters_service

    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found.")
    uid = project.get("userId") or ""
    cast = list(project.get("cast") or [])
    by_key = {_key(c.get("name")): c for c in cast if isinstance(c, dict)}

    for ch in characters or []:
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("name", "") or "").strip()
        if not name:
            continue
        k = _key(name)
        existing = by_key.get(k) or {}
        entry = {
            "name": name,
            "description": str(ch.get("description", "") or existing.get("description", "")),
            "voice": str(ch.get("voice", "") or existing.get("voice", "")),
            # This project's own lock wins; otherwise adopt the shared face, so
            # a character first drawn in Studio arrives here already cast.
            "portrait": (
                existing.get("portrait")
                or (characters_service.ref_for(uid, name) if uid else None)
                or None
            ),
        }
        cast = [c for c in cast if _key(c.get("name")) != k]
        cast.append(entry)
        by_key[k] = entry

    update_project(project_id, cast=cast)

    # Same identities, app-wide.
    if uid:
        characters_service.sync_many(uid, cast)
        characters_service.adopt_refs(
            uid, list(cast) + list(project.get("cast") or [])
        )
    return cast


def locked_portraits(project_id: str) -> dict:
    """{lowercased name: storage path} for every character already locked."""
    if not project_id:
        return {}
    project = get_project(project_id)
    if not project:
        return {}
    out = {}
    for c in project.get("cast") or []:
        if not isinstance(c, dict):
            continue
        k = _key(c.get("name"))
        path = c.get("portrait")
        if k and path:
            out[k] = str(path)
    return out


def save_portrait(project_id: str, name: str, portrait: str, description: str = "") -> None:
    """Lock a character's portrait for the whole project (upsert by name)."""
    if not project_id or not name or not portrait:
        return
    project = get_project(project_id)
    if not project:
        return
    cast = list(project.get("cast") or [])
    k = _key(name)
    cast = [c for c in cast if _key(c.get("name")) != k]
    cast.append(
        {
            "name": name.strip(),
            "description": description
            or next(
                (c.get("description", "") for c in project.get("cast") or []
                 if isinstance(c, dict) and _key(c.get("name")) == k),
                "",
            ),
            "voice": next(
                (c.get("voice", "") for c in project.get("cast") or []
                 if isinstance(c, dict) and _key(c.get("name")) == k),
                "",
            ),
            "portrait": portrait,
        }
    )
    update_project(project_id, cast=cast)


def missing_portraits(project_id: str, characters: list, uid: str = "") -> list:
    """Characters that still need a portrait generated — i.e. the GPU cost.

    Uses the package service's own rule for who has a face, and the shared
    library's record of which faces already exist, so this cost always matches
    what FilmWorker will actually spend.
    """
    from . import characters as characters_service
    from . import package as package_service

    locked = locked_portraits(project_id)
    out = []
    for c in package_service.on_screen_characters(characters):
        name = str(c.get("name", "") or "").strip()
        if name.lower() in locked:
            continue
        # A face already in the library (made in Studio, say) is reused, too.
        if uid and characters_service.ref_for(uid, name):
            continue
        out.append(name)
    return out


__all__ = [
    "COLLECTION",
    "MAX_MESSAGES",
    "ProjectError",
    "append_messages",
    "create_project",
    "delete_project",
    "get_project",
    "list_projects",
    "locked_portraits",
    "missing_portraits",
    "owner_project",
    "save_portrait",
    "sync_cast",
    "update_project",
]
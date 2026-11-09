"""The character library — one identity per character, shared by every feature.

Vidyra can put a character on screen in four places (Auto Film, Storyboard,
Studio, and a planning project) and each one used to invent its own idea of who
that character is. A face locked during a film evaporated with the job, a
storyboard character had no face at all, and Studio knew nothing about names.

This module is the single registry every one of those features reads and writes:

    {name, description, voice, refImage}

``refImage`` is a storage path to the character's locked master face. Whichever
feature first creates the face writes it here, and every other feature then
reuses it instead of inventing a new one — that is what "consistent characters"
means in Vidyra. Refs are reusable *ingredients*: generating them is the only
part that costs GPU runs, so the library also records which ones exist.

Documents live in the ``characters`` collection, one per user per name. They are
server-only (see firestore.rules); every route goes through this service.
"""

from __future__ import annotations

import datetime
import uuid

from .firebase import firebase

COLLECTION = "characters"

#: Storage prefixes a user is allowed to attach as a character reference.
#: Mirrors the ownership check in routes/jobs.py so the same rule applies
#: whichever feature a file came in through.
REF_PREFIXES = ("media/", "studio/", "videos/")

#: Field limits, kept in step with FilmCharacter in app/models.py.
MAX_NAME = 40
MAX_DESCRIPTION = 500
MAX_VOICE = 300

# Dev-only fallback store: character_id -> document fields.
_memory_store: dict[str, dict] = {}


class CharacterError(RuntimeError):
    pass


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db():
    if firebase.db is None:
        raise RuntimeError("Firestore is not configured.")
    return firebase.db


def key_of(name: str) -> str:
    """Identity key for a character name — case/spacing insensitive."""
    return (name or "").strip().lower()


def normalize(data: dict) -> dict:
    """Coerce anything into the canonical character shape.

    Shared by every feature so a Studio ref, a storyboard entry and a planned
    character all land in the library in the same form.
    """
    name = str((data or {}).get("name", "") or "").strip()
    if not name:
        raise CharacterError("A character needs a name.")
    if len(name) > MAX_NAME:
        raise CharacterError(f"Character name too long: {name[:20]}…")
    return {
        "name": name,
        "description": str((data or {}).get("description", "") or "")[:MAX_DESCRIPTION],
        "voice": str((data or {}).get("voice", "") or "")[:MAX_VOICE],
        "refImage": str((data or {}).get("refImage", "") or "") or None,
    }


def _out(doc: dict) -> dict:
    """Public shape for a stored character."""
    char = normalize(doc)
    char["characterId"] = doc.get("characterId", "")
    char["createdAt"] = doc.get("createdAt", "")
    char["updatedAt"] = doc.get("updatedAt", "")
    return char


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def list_characters(uid: str) -> list:
    """Every character this user owns, newest name-sorted update first."""
    if firebase.db is None:
        docs = [d for d in _memory_store.values() if d.get("userId") == uid]
    else:
        docs = [d.to_dict() for d in _db().collection(COLLECTION).where(
            "userId", "==", uid).stream()]
    out = []
    for d in docs:
        if not d:
            continue
        try:
            out.append(_out(d))
        except CharacterError:
            continue  # skip corrupt rows rather than break the whole library
    out.sort(key=lambda c: (c.get("updatedAt") or "", c.get("name") or ""), reverse=True)
    return out


def get_character(uid: str, character_id: str) -> dict | None:
    if not character_id:
        return None
    if firebase.db is None:
        doc = _memory_store.get(character_id)
        if not doc or doc.get("userId") != uid:
            return None
        return _out(doc)
    snap = _db().collection(COLLECTION).document(character_id).get()
    if not snap.exists:
        return None
    doc = snap.to_dict() or {}
    if doc.get("userId") != uid:
        # Same response as "missing" so ids can't be probed for other users.
        return None
    return _out(doc)


def find_by_name(uid: str, name: str) -> dict | None:
    """Look a character up by name — how features resolve a saved identity."""
    k = key_of(name)
    if not k:
        return None
    for c in list_characters(uid):
        if key_of(c["name"]) == k:
            return c
    return None


def _store(character_id: str, doc: dict) -> None:
    if firebase.db is None:
        _memory_store[character_id] = doc
        return
    _db().collection(COLLECTION).document(character_id).set(doc, merge=True)


def save_character(uid: str, data: dict) -> dict:
    """Create or update a character. Matches an existing same-name entry."""
    char = normalize(data)
    existing = find_by_name(uid, char["name"])
    now = _now_stamp()
    if existing:
        character_id = existing["characterId"]
        # A partial update keeps the fields the caller didn't mention — a plan
        # that only knows a character's description must not erase their face.
        merged = {
            "description": char["description"] or existing.get("description", ""),
            "voice": char["voice"] or existing.get("voice", ""),
            "refImage": char["refImage"] or existing.get("refImage") or None,
        }
    else:
        character_id = uuid.uuid4().hex[:16]
        merged = char
    doc = {
        "userId": uid,
        "characterId": character_id,
        "name": char["name"],
        "key": key_of(char["name"]),
        "description": merged["description"],
        "voice": merged["voice"],
        "refImage": merged["refImage"],
        "createdAt": (existing or {}).get("createdAt") or now,
        "updatedAt": now,
    }
    _store(character_id, doc)
    return _out(doc)


def delete_character(uid: str, character_id: str) -> None:
    char = get_character(uid, character_id)
    if not char:
        raise CharacterError("Character not found.")
    if firebase.db is None:
        _memory_store.pop(character_id, None)
        return
    _db().collection(COLLECTION).document(character_id).delete()


def sync_many(uid: str, characters) -> list:
    """Write a whole cast into the library, preserving locked reference images.

    Used whenever a plan or a film package declares characters: they become
    reusable library entries, and a face already locked for a character is
    never overwritten by a later, blunter description.
    """
    out = []
    for c in characters or []:
        if not isinstance(c, dict):
            continue
        try:
            char = normalize(c)
        except CharacterError:
            continue
        # A feature may call the locked face "portrait"; the library calls it
        # refImage. Accept both so callers don't have to rename their fields.
        if not char["refImage"]:
            char["refImage"] = str(c.get("portrait") or "") or None
        existing = find_by_name(uid, char["name"])
        if existing and existing.get("refImage"):
            char["refImage"] = existing["refImage"]
        out.append(save_character(uid, char))
    return out


def ref_for(uid: str, name: str) -> str | None:
    """The locked face for a named character, or None if it has none yet."""
    char = find_by_name(uid, name)
    return (char or {}).get("refImage") or None


def adopt_refs(uid: str, characters) -> list:
    """Fill in each character's library ``refImage`` from their own fields.

    Lets a project or a film that already carries portraits push them into the
    shared library, so Studio and Storyboard immediately see the same face.
    """
    changed = []
    for c in characters or []:
        if not isinstance(c, dict):
            continue
        ref = c.get("portrait") or c.get("refImage")
        name = str(c.get("name", "") or "").strip()
        if not name or not ref:
            continue
        if ref_for(uid, name) == ref:
            continue
        changed.append(save_character(uid, {"name": name, "refImage": ref}))
    return changed


__all__ = [
    "COLLECTION",
    "CharacterError",
    "adopt_refs",
    "delete_character",
    "find_by_name",
    "get_character",
    "key_of",
    "list_characters",
    "normalize",
    "ref_for",
    "save_character",
    "sync_many",
]
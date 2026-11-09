"""Brand workspaces — Firestore, with an in-memory fallback for local dev.

A workspace is the identity behind the videos: page name, brand colours,
default voice/aspect/captions/music and a daily GPU budget. Recipes and
manual jobs inherit these defaults so a creator never re-picks the same
look twice. The first workspace is seeded automatically, which is also the
"single private workspace" mode the product starts in; the same collection
supports many brands later without a schema change.
"""

from __future__ import annotations

import datetime
import re
import uuid

from pipeline import aspect as aspect_lib

from .firebase import firebase

COLLECTION = "workspaces"

#: Settings keys a workspace may seed. Kept in step with the job whitelist so
#: a workspace can never inject a key the render pipeline doesn't understand.
DEFAULT_KEYS = (
    "voice",
    "speech_rate",
    "pacing",
    "hook",
    "hook_style",
    "hook_text",
    "hook_subtext",
    "hook_duration",
    "captions",
    "caption_style",
    "font_size",
    "words_per_line",
    "caption_position",
    "music",
    "music_volume",
    "transition",
    "reframe",
    "show_page_name",
    "page_name",
    "aspect",
)

SEED_NAME = "South Carolina Women's Basketball"
SEED_PAGE_NAME = "South Carolina Women's Basketball"
SEED_NICHE = "sports"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Dev-only fallback store: workspace_id -> document fields.
_memory_store: dict[str, dict] = {}


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _db():
    return firebase.db


def _using_dev_store() -> bool:
    return _db() is None


def _default_id(uid: str) -> str:
    """One stable seed document per user, so it is created at most once."""
    return f"default-{uid}".replace("/", "_")


def _seed_doc(uid: str) -> dict:
    now = _now_stamp()
    return {
        "workspaceId": _default_id(uid),
        "ownerUid": uid,
        "name": SEED_NAME,
        "niche": SEED_NICHE,
        "pageName": SEED_PAGE_NAME,
        "brand": {"primary": "#73000A", "secondary": "#000000", "logoPath": ""},
        "defaults": {
            "voice": "",
            "speech_rate": 1.0,
            "pacing": "Normal Sports Story",
            "hook": True,
            "hook_style": "Breaking News",
            "hook_text": "",
            "hook_subtext": "",
            "hook_duration": 1.5,
            "captions": True,
            "caption_style": "Karaoke",
            "font_size": 58,
            "words_per_line": 5,
            "caption_position": "Bottom",
            "music": "",
            "music_volume": 0.35,
            "transition": "Auto",
            "reframe": "auto",
            "show_page_name": True,
            "page_name": SEED_PAGE_NAME,
            "aspect": "9:16",
        },
        "budget": {"dailyRuns": 0},
        "createdAt": now,
        "updatedAt": now,
    }


def _clean_brand(raw) -> dict:
    brand = raw if isinstance(raw, dict) else {}
    primary = str(brand.get("primary") or "").strip()
    secondary = str(brand.get("secondary") or "").strip()
    logo = str(brand.get("logoPath") or "").strip()
    return {
        "primary": primary if _HEX_RE.match(primary) else "",
        "secondary": secondary if _HEX_RE.match(secondary) else "",
        "logoPath": logo[:400],
    }


def _clean_defaults(raw) -> dict:
    defaults = raw if isinstance(raw, dict) else {}
    clean: dict = {}
    for key in DEFAULT_KEYS:
        if key not in defaults:
            continue
        value = defaults[key]
        if key == "aspect":
            value = value if value in aspect_lib.ASPECT_CHOICES else "9:16"
        elif key in ("music", "voice", "caption_style", "caption_position", "pacing", "transition", "reframe", "hook_style", "page_name"):
            value = str(value or "")[:300]
        elif key in ("speech_rate", "font_size", "words_per_line", "hook_duration", "music_volume"):
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        elif key in ("hook", "captions", "show_page_name"):
            value = bool(value)
        clean[key] = value
    return clean


def _clean_doc(uid: str, payload: dict, workspace_id: str = "") -> dict:
    now = _now_stamp()
    return {
        "workspaceId": workspace_id or uuid.uuid4().hex[:16],
        "ownerUid": uid,
        "name": str(payload.get("name") or "Untitled workspace").strip()[:80],
        "niche": str(payload.get("niche") or "").strip()[:80],
        "pageName": str(payload.get("pageName") or payload.get("page_name") or "").strip()[:64],
        "brand": _clean_brand(payload.get("brand")),
        "defaults": _clean_defaults(payload.get("defaults")),
        "budget": {
            "dailyRuns": max(0, int((payload.get("budget") or {}).get("dailyRuns") or 0))
        },
        "createdAt": now,
        "updatedAt": now,
    }


def _as_out(doc: dict) -> dict:
    return {
        "workspace_id": doc.get("workspaceId") or "",
        "name": doc.get("name") or "",
        "niche": doc.get("niche") or "",
        "page_name": doc.get("pageName") or "",
        "brand": doc.get("brand") or {"primary": "", "secondary": "", "logoPath": ""},
        "defaults": doc.get("defaults") or {},
        "budget": doc.get("budget") or {"dailyRuns": 0},
        "created_at": doc.get("createdAt") or "",
        "updated_at": doc.get("updatedAt") or "",
    }


def _store_set(workspace_id: str, doc: dict) -> None:
    if _using_dev_store():
        _memory_store[workspace_id] = doc
        return
    _db().collection(COLLECTION).document(workspace_id).set(doc)


def get_doc(workspace_id: str) -> dict | None:
    if not workspace_id:
        return None
    if _using_dev_store():
        doc = _memory_store.get(workspace_id)
        return dict(doc) if doc else None
    snap = _db().collection(COLLECTION).document(workspace_id).get()
    return snap.to_dict() if snap.exists else None


def owner_workspace(workspace_id: str, uid: str) -> dict:
    doc = get_doc(workspace_id)
    if not doc or doc.get("ownerUid") != uid:
        raise KeyError(workspace_id)
    return doc


def ensure_default(uid: str) -> dict:
    """Return the seeded workspace, creating it on first use."""
    if not uid:
        raise KeyError("uid")
    workspace_id = _default_id(uid)
    existing = get_doc(workspace_id)
    if existing:
        return existing
    doc = _seed_doc(uid)
    _store_set(workspace_id, doc)
    return doc


def list_workspaces(uid: str) -> list[dict]:
    ensure_default(uid)
    if _using_dev_store():
        docs = [dict(d) for d in _memory_store.values() if d.get("ownerUid") == uid]
    else:
        docs = [
            snap.to_dict()
            for snap in _db()
            .collection(COLLECTION)
            .where("ownerUid", "==", uid)
            .stream()
        ]
    docs.sort(key=lambda d: d.get("createdAt") or "")
    return docs


def create_workspace(uid: str, payload: dict) -> dict:
    ensure_default(uid)
    doc = _clean_doc(uid, payload)
    _store_set(doc["workspaceId"], doc)
    return doc


def update_workspace(uid: str, workspace_id: str, patch: dict) -> dict:
    doc = owner_workspace(workspace_id, uid)
    updated = dict(doc)
    if "name" in patch:
        updated["name"] = str(patch.get("name") or "").strip()[:80] or updated.get("name")
    if "niche" in patch:
        updated["niche"] = str(patch.get("niche") or "").strip()[:80]
    if "page_name" in patch or "pageName" in patch:
        updated["pageName"] = str(
            patch.get("page_name") or patch.get("pageName") or ""
        ).strip()[:64]
    if "brand" in patch:
        updated["brand"] = _clean_brand(patch.get("brand"))
    if "defaults" in patch:
        merged = dict(updated.get("defaults") or {})
        merged.update(_clean_defaults(patch.get("defaults")))
        updated["defaults"] = merged
    if "budget" in patch:
        budget = patch.get("budget") if isinstance(patch.get("budget"), dict) else {}
        updated["budget"] = {
            "dailyRuns": max(0, int(budget.get("dailyRuns") or 0))
        }
    updated["updatedAt"] = _now_stamp()
    _store_set(workspace_id, updated)
    return updated


def resolve_defaults(uid: str, workspace_id: str = "", overrides: dict | None = None) -> dict:
    """Return the settings a workspace contributes, with explicit overrides winning.

    An unknown/blank workspace id falls back to the seeded one, so older
    clients that never send `workspaceId` keep their current behaviour.
    """
    doc = None
    if workspace_id:
        try:
            doc = owner_workspace(workspace_id, uid)
        except KeyError:
            doc = None
    if doc is None:
        try:
            doc = ensure_default(uid)
        except KeyError:
            doc = None

    resolved = dict((doc or {}).get("defaults") or {})
    page_name = (doc or {}).get("pageName") or ""
    if page_name and not resolved.get("page_name"):
        resolved["page_name"] = page_name
    for key, value in (overrides or {}).items():
        if value is not None and value != "":
            resolved[key] = value
    return resolved


def apply_defaults(
    settings: dict,
    uid: str,
    workspace_id: str = "",
    allowed: set[str] | None = None,
) -> dict:
    """Fill any settings the caller left blank from the workspace defaults.

    Explicit job values always win; the workspace is a fallback, not an
    override. Unknown keys are dropped when `allowed` is supplied.
    """
    defaults = resolve_defaults(uid, workspace_id)
    merged = dict(defaults)
    for key, value in (settings or {}).items():
        if value is not None and value != "":
            merged[key] = value
    if allowed is not None:
        merged = {k: v for k, v in merged.items() if k in allowed}
    return merged


def out(doc: dict) -> dict:
    return _as_out(doc)

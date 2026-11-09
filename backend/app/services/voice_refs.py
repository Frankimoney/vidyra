"""Ownership rules for user-picked voices.

A voice is either a built-in Piper name ("en_US-amy-medium") or a cloned
voice written as "clone:<storagePath>". Cloned references point at the
caller's own storage, so every entry point (jobs, films, podcasts) has to
check the same thing — this is that one check.
"""

from __future__ import annotations

from fastapi import HTTPException

from pipeline.narration import CLONE_VOICE_PREFIX, clone_ref
from pipeline.utils import classify_media


def owned_clone_ref(value: str, uid: str) -> str:
    """Storage path of a valid "clone:<path>" the caller owns.

    Returns "" for a built-in Piper voice name (nothing to own). Raises
    HTTPException 400 when the reference points at someone else's file or at
    a non-audio file.
    """
    if not is_clone(value or ""):
        return ""  # built-in Piper voice name — nothing to own
    ref = clone_ref(value or "")
    if not ref:
        raise HTTPException(status_code=400, detail="Cloned voice is missing its audio file.")
    if not ref.startswith((f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/")):
        raise HTTPException(status_code=400, detail="Cloned voice does not belong to you.")
    if classify_media(ref) != "audio":
        raise HTTPException(status_code=400, detail="Cloned voice must be an audio file.")
    return ref


def is_clone(value: str) -> bool:
    return isinstance(value, str) and value.startswith(CLONE_VOICE_PREFIX)

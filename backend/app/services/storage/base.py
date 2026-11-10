"""Shared storage helpers: path safety, signed media URLs, URL joining."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import urllib.parse

from pipeline.utils import classify_media, is_music_upload, upload_tag

MEDIA_URL_TTL_SECONDS = 3600
SHARE_URL_TTL_SECONDS = 7 * 24 * 3600

_HEX_SIG = re.compile(r"[0-9a-f]{24}")
_DRIVE = re.compile(r"^[A-Za-z]:")


class StorageError(Exception):
    """A storage path or operation is invalid."""


def _normalize_rel(path) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("/")


def _safe_resolve(root: str, rel) -> str:
    """Resolve ``rel`` under ``root`` or raise :class:`StorageError`.

    Traversal is rejected textually before the path is normalised so that
    ``media/<uid>/../other`` cannot satisfy an ownership ``startswith`` check
    and still escape the caller's directory.
    """
    raw = str(rel or "").strip()
    if not raw:
        raise StorageError("empty storage path")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE.match(normalized):
        raise StorageError(f"absolute storage path not allowed: {raw!r}")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StorageError(f"unsafe storage path: {raw!r}")

    root_abs = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root_abs, *parts))
    try:
        common = os.path.commonpath([root_abs, target])
    except ValueError as exc:
        raise StorageError(f"unsafe storage path: {raw!r}") from exc
    if common != root_abs:
        raise StorageError(f"storage path escapes root: {raw!r}")
    return target


def _sign_media(path: str, exp: int, secret: str) -> str:
    payload = f"{_normalize_rel(path)}:{int(exp)}".encode()
    return hmac.new(str(secret).encode(), payload, hashlib.sha256).hexdigest()[:24]


def signed_media_path(
    path: str,
    ttl: int = MEDIA_URL_TTL_SECONDS,
    secret: str = "",
) -> str:
    """Return a relative, HMAC-signed media URL for the local driver."""
    rel = _normalize_rel(path)
    exp = int(time.time()) + int(ttl)
    sig = _sign_media(rel, exp, secret)
    return f"/api/media/{urllib.parse.quote(rel, safe='/')}?exp={exp}&sig={sig}"


def verify_signed_url(path: str, exp, sig, secret: str) -> bool:
    """True when ``sig`` is a live, correct signature for ``path``."""
    try:
        expires = int(exp)
    except (TypeError, ValueError):
        return False
    if int(time.time()) > expires:
        return False
    if not isinstance(sig, str) or not _HEX_SIG.fullmatch(sig):
        return False
    expected = _sign_media(_normalize_rel(path), expires, secret)
    return hmac.compare_digest(sig, expected)


def as_absolute(url: str | None, base: str | None) -> str:
    """Turn a relative media URL into an absolute one; leave URLs untouched."""
    value = str(url or "")
    if value.startswith(("http://", "https://")):
        return value
    return f"{str(base or '').rstrip('/')}/{value.lstrip('/')}"


__all__ = [
    "MEDIA_URL_TTL_SECONDS",
    "SHARE_URL_TTL_SECONDS",
    "StorageError",
    "_normalize_rel",
    "_safe_resolve",
    "_sign_media",
    "as_absolute",
    "classify_media",
    "is_music_upload",
    "signed_media_path",
    "upload_tag",
    "verify_signed_url",
]

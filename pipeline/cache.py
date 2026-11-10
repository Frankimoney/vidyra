"""Content-addressed cache for the heavy pipeline steps.

Cached artifacts live under settings.cache_dir (persistent, per-user). Keys are
sha256 hashes of the deterministic inputs that produced an artifact, so when a
video is edited, unchanged pieces (narration, processed clips, the stitched
visual track) are reused instead of being regenerated.
"""

from __future__ import annotations

import hashlib
import os
import shutil


def file_fingerprint(path: str) -> str:
    """Cheap, stable fingerprint of a local media file (full content hash)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(*parts) -> str:
    """Deterministic key from the inputs that produced an artifact."""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:28]


def _path(cache_dir: str, key: str, kind: str, ext: str) -> str:
    return os.path.join(cache_dir, f"{kind}_{key}.{ext}")


def cache_get(cache_dir: str, key: str, kind: str, ext: str) -> str | None:
    if not cache_dir:
        return None
    p = _path(cache_dir, key, kind, ext)
    try:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    except OSError:
        pass
    return None


def cache_put(src: str, cache_dir: str, key: str, kind: str, ext: str) -> str:
    """Store a produced artifact atomically. Returns the cache path."""
    os.makedirs(cache_dir, exist_ok=True)
    dest = _path(cache_dir, key, kind, ext)
    tmp = dest + ".tmp"
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        shutil.copy2(src, tmp)
        if os.path.exists(dest):
            os.remove(dest)
        os.replace(tmp, dest)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return dest


def narration_key(settings) -> str:
    voices = getattr(settings, "dialogue_voices", None) or {}
    voice_plan = "|".join(f"{k}={v}" for k, v in sorted(voices.items()))
    return cache_key(
        "nar",
        settings.script,
        settings.voice_path,
        round(float(settings.speech_rate), 4),
        settings.pacing,
        voice_plan,
    )


def clip_key(
    kind: str,
    media_path: str,
    duration,
    index,
    settings,
    trim_start: float = 0,
    focus=(0.5, 0.5),
    zoom: float = 1.0,
    speed: float = 1.0,
    reverse: bool = False,
    motion: str = "auto",
) -> str:
    try:
        fx, fy = focus
    except (TypeError, ValueError):
        fx, fy = 0.5, 0.5
    return cache_key(
        "clip",
        kind,
        file_fingerprint(media_path),
        round(float(duration), 3),
        int(index),
        round(float(trim_start or 0), 3),
        round(float(fx or 0.5), 4),
        round(float(fy or 0.5), 4),
        round(float(zoom or 1.0), 4),
        round(float(speed or 1.0), 4),
        bool(reverse),
        str(motion or "auto"),
        getattr(settings, "reframe", "auto"),
        settings.width,
        settings.height,
        settings.fps,
    )


def chain_key(clip_keys: list, durations: list, settings) -> str:
    parts = ["chain", len(clip_keys)]
    for k, d in zip(clip_keys, durations):
        parts.extend([k, round(float(d), 3)])
    parts.append(settings.transition)
    parts.extend([settings.width, settings.height, settings.fps])
    return cache_key(*parts)
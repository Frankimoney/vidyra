"""Local-disk storage driver with expiring HMAC-signed URLs."""

from __future__ import annotations

import hmac
import hashlib
import os
import re
import shutil
import time

from . import StorageError

MEDIA_TTL_SECONDS = 7 * 24 * 3600  # published video link lifetime
SHORT_TTL_SECONDS = 24 * 3600  # freshly requested link lifetime

# Signatures are truncated hex, so a valid one is always exactly this shape.
_HEX_SIG = re.compile(r"[0-9a-f]{24}")


def _safe_resolve(root: str, rel: str) -> str:
    """Resolve a storage path under root, rejecting traversal."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel:
        raise StorageError("empty storage path")
    # Reject `..` textually before resolving. Callers authorise paths with
    # `startswith(f"media/{uid}/")`, which a traversal string satisfies while
    # still landing on another user's directory -- so the check has to happen
    # on the literal segments, not only on the resolved path (which merely
    # proves the target is inside the store, not that it is the caller's).
    if any(part == ".." for part in rel.split("/")):
        raise StorageError("invalid storage path")
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, rel))
    try:
        if os.path.commonpath([root_real, full]) != root_real:
            raise StorageError("invalid storage path")
    except ValueError:
        raise StorageError("invalid storage path")
    return full


def signed_media_path(storage_path: str, ttl: int, secret: str) -> str:
    """Build an API-relative signed media URL (callers prefix their origin)."""
    exp = int(time.time()) + ttl
    payload = f"{exp}:{storage_path}".encode()
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:24]
    return f"/api/media/{storage_path}?exp={exp}&sig={sig}"


def verify_signed_url(storage_path: str, exp: int, sig: str, secret: str) -> bool:
    if not sig or int(time.time()) > int(exp or 0):
        return False
    payload = f"{exp}:{storage_path}".encode()
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:24]
    # Guard the shape before comparing: `compare_digest` raises TypeError on
    # non-ASCII str operands, which would turn a bad link into a 500 on this
    # unauthenticated endpoint instead of a clean 403.
    if not _HEX_SIG.fullmatch(sig or ""):
        return False
    return hmac.compare_digest(sig, expected)


def as_absolute(url: str, base: str) -> str:
    """Make sure a media URL is absolute (firebase URLs already are)."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{base.rstrip('/')}{url}"


class LocalStorageDriver:
    kind = "local"

    def __init__(self, storage_root: str, secret: str):
        self.root = storage_root
        self.secret = secret
        os.makedirs(os.path.join(self.root, "media"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "videos"), exist_ok=True)

    # -- worker helpers -------------------------------------------------

    def resolve(self, storage_path: str, local_dest: str | None = None) -> str:
        """Return the on-disk path for a stored object."""
        full = _safe_resolve(self.root, storage_path)
        if not os.path.isfile(full):
            raise StorageError(f"Missing media file: {storage_path}")
        return full

    def blob_size(self, storage_path: str) -> int:
        try:
            return os.path.getsize(_safe_resolve(self.root, storage_path))
        except OSError:
            return 0

    def publish(self, local_path: str, storage_path: str) -> str:
        """Copy a finished video into the store; return a signed URL."""
        dest = _safe_resolve(self.root, storage_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(local_path, dest)
        return signed_media_path(storage_path, MEDIA_TTL_SECONDS, self.secret)

    def media_url(self, storage_path: str) -> str:
        """Fresh signed URL for an existing stored object."""
        return signed_media_path(storage_path, SHORT_TTL_SECONDS, self.secret)

    def share_url(self, storage_path: str) -> str:
        """Long-lived signed URL safe to hand to anyone (7 days)."""
        return signed_media_path(storage_path, MEDIA_TTL_SECONDS, self.secret)

    def delete_file(self, storage_path: str) -> bool:
        """Delete a stored object (and now-empty parents). Returns True if removed."""
        full = _safe_resolve(self.root, storage_path)
        if not os.path.isfile(full):
            return False
        parent = os.path.dirname(full)
        os.remove(full)
        root_real = os.path.realpath(self.root)
        while parent != root_real and parent.startswith(root_real):
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)
        return True

    def list_user_audio(self, uid: str, music_only: bool = False) -> list[str]:
        """Storage paths of the user's uploaded audio.

        With music_only, voice samples are left out so the music catalog does
        not offer someone's cloned voice as a background track.
        """
        from pipeline.config import AUDIO_EXTENSIONS
        from pipeline.utils import get_duration, is_music_upload, upload_tag

        root = _safe_resolve(self.root, f"media/{uid}")
        paths: list[str] = []
        if not os.path.isdir(root):
            return paths
        for dirpath, _dirs, files in os.walk(root):
            for filename in sorted(files):
                if not filename.lower().endswith(AUDIO_EXTENSIONS):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, filename), self.root)
                rel = rel.replace("\\", "/")
                # Only untagged (pre-purpose) uploads need an ffprobe to be
                # classified; tagged ones answer from the path alone.
                duration = None if upload_tag(rel) else get_duration(
                    os.path.join(dirpath, filename)
                )
                if music_only and not is_music_upload(rel, duration):
                    continue
                paths.append(rel)
        return paths

    def list_user_files(self, uid: str) -> list[dict]:
        """Metadata for every file the user owns across media/, studio/, videos/."""
        from pipeline.utils import classify_media, get_duration, is_music_upload, upload_tag

        out: list[dict] = []
        for top in ("media", "studio", "videos"):
            base = _safe_resolve(self.root, f"{top}/{uid}")
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, files in os.walk(base):
                for filename in sorted(files):
                    full = os.path.join(dirpath, filename)
                    kind = classify_media(filename)
                    if kind not in ("image", "video", "audio"):
                        continue
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue
                    rel = os.path.relpath(full, self.root).replace("\\", "/")
                    item = {
                        "path": rel,
                        "name": filename,
                        "kind": kind,
                        "size": stat.st_size,
                        "created": int(stat.st_mtime),
                        "source": top,
                        "url": self.media_url(rel),
                    }
                    if kind == "audio":
                        # Lets callers keep voice samples out of the music
                        # catalog and music out of the voice-clone list.
                        # Only untagged uploads need an ffprobe.
                        duration = None if upload_tag(rel) else get_duration(full)
                        item["purpose"] = (
                            "music" if is_music_upload(rel, duration) else "voice"
                        )
                    out.append(item)
        out.sort(key=lambda x: x["created"], reverse=True)
        return out

    # -- upload route helpers -------------------------------------------

    def save_media(
        self,
        uid: str,
        upload_id: str,
        filename: str,
        source: str,
        tag: str = "",
    ) -> str:
        """Persist an uploaded file; returns its storage path.

        `tag` ("music" / "voice") is prefixed onto the folder so the file can
        be told apart from other uploads of the same media kind later.
        """
        folder = f"{tag}-{upload_id}" if tag else upload_id
        rel = os.path.join("media", uid, folder, filename).replace("\\", "/")
        dest = _safe_resolve(self.root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(source, dest)
        return rel
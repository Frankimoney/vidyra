"""In-memory storage for tests. It never touches disk and is refused in production."""

from __future__ import annotations

import os
import tempfile
import urllib.parse

from .base import (
    MEDIA_URL_TTL_SECONDS,
    SHARE_URL_TTL_SECONDS,
    StorageError,
    _normalize_rel,
    classify_media,
    is_music_upload,
    upload_tag,
)

_SAFE_TAG = ("music", "voice")


class MemoryStorageDriver:
    """A tiny in-memory double so app-starting tests do not need R2 credentials."""

    kind = "memory"
    root = ""

    def __init__(self, settings, secret: str = ""):
        self.settings = settings
        self.secret = secret
        self.blobs: dict[str, bytes] = {}
        self.media_ttl = int(
            getattr(settings, "signed_url_ttl_seconds", MEDIA_URL_TTL_SECONDS)
        )
        self.share_ttl = int(
            getattr(settings, "share_url_ttl_seconds", SHARE_URL_TTL_SECONDS)
        )
        self.work_root = str(getattr(settings, "work_root", "") or tempfile.gettempdir())

    def _key(self, path) -> str:
        key = _normalize_rel(path)
        if not key or ".." in key.split("/"):
            raise StorageError(f"invalid storage key: {path!r}")
        return key

    def save_media(self, uid, upload_id, filename, tmp_path, **kwargs) -> str:
        uid = str(uid or "").strip()
        upload_id = str(upload_id or "").strip()
        name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        if not uid or not upload_id or not name or name in (".", ".."):
            raise StorageError("invalid media path")
        tag = str(kwargs.get("tag") or "").strip()
        folder = f"{tag}-{upload_id}" if tag in _SAFE_TAG else upload_id
        rel = f"media/{uid}/{folder}/{name}"
        with open(tmp_path, "rb") as fh:
            self.blobs[rel] = fh.read()
        return rel

    def publish(self, tmp_path: str, rel: str) -> str:
        key = self._key(rel)
        with open(tmp_path, "rb") as fh:
            self.blobs[key] = fh.read()
        return self.media_url(key)

    def resolve(self, path, local_path: str | None = None) -> str:
        key = self._key(path)
        if key not in self.blobs:
            raise StorageError(f"media not found: {path!r}")
        if local_path is None:
            os.makedirs(self.work_root, exist_ok=True)
            fd, local_path = tempfile.mkstemp(
                prefix="vidyra-mem-", suffix=os.path.splitext(key)[1], dir=self.work_root
            )
            os.close(fd)
        else:
            parent = os.path.dirname(os.path.abspath(local_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(self.blobs[key])
        return local_path

    def exists(self, path) -> bool:
        try:
            return self._key(path) in self.blobs
        except StorageError:
            return False

    def blob_size(self, path) -> int:
        try:
            return len(self.blobs.get(self._key(path), b""))
        except StorageError:
            return 0

    def delete_file(self, path) -> bool:
        try:
            return self.blobs.pop(self._key(path), None) is not None
        except StorageError:
            return False

    def media_url(self, path, ttl: int | None = None) -> str:
        key = self._key(path)
        return f"https://memory.local/{urllib.parse.quote(key)}"

    def share_url(self, path, ttl: int | None = None) -> str:
        return self.media_url(path, ttl)

    def list_user_files(self, uid: str) -> list[dict]:
        uid = str(uid or "").strip()
        items: list[dict] = []
        for key, body in self.blobs.items():
            if not any(
                key.startswith(prefix)
                for prefix in (f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/")
            ):
                continue
            name = key.rsplit("/", 1)[-1]
            if (
                not name
                or name.startswith("seg_")
                or "/_refs/" in key
                or "/.work/" in key
            ):
                continue
            kind = classify_media(key)
            if kind is None:
                continue
            items.append(
                {
                    "path": key,
                    "name": name,
                    "kind": kind,
                    "size": len(body),
                    "url": self.media_url(key),
                    "purpose": upload_tag(key) if kind == "audio" else "",
                }
            )
        items.sort(key=lambda item: item["path"])
        return items

    def list_user_audio(self, uid: str, music_only: bool = True) -> list[str]:
        audio = [item for item in self.list_user_files(uid) if item["kind"] == "audio"]
        if music_only:
            audio = [item for item in audio if is_music_upload(item["path"])]
        return sorted(item["path"] for item in audio)

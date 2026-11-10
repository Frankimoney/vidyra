"""Cloudflare R2 storage driver (S3-compatible, media is never kept on disk)."""

from __future__ import annotations

import mimetypes
import os
import re
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

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def _segment(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or value in (".", "..") or not _SAFE_SEGMENT.fullmatch(value):
        raise StorageError(f"invalid {label}")
    return value


class R2StorageDriver:
    """Store every upload, artifact, and render in a Cloudflare R2 bucket."""

    kind = "r2"
    root = ""

    def __init__(self, settings, secret: str = ""):
        self.settings = settings
        self.secret = secret
        self.bucket = str(getattr(settings, "r2_bucket", "") or "").strip()
        access_key = str(getattr(settings, "r2_access_key_id", "") or "").strip()
        secret_key = str(getattr(settings, "r2_secret_access_key", "") or "").strip()
        endpoint = str(getattr(settings, "r2_endpoint", "") or "").strip()
        account_id = str(getattr(settings, "r2_account_id", "") or "").strip()
        if not endpoint and account_id:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        missing = [
            name
            for name, value in (
                ("R2_BUCKET", self.bucket),
                ("R2_ACCESS_KEY_ID", access_key),
                ("R2_SECRET_ACCESS_KEY", secret_key),
            )
            if not value
        ]
        if not endpoint:
            missing.append("R2_ENDPOINT or R2_ACCOUNT_ID")
        if missing:
            raise StorageError(
                "R2 storage is not configured; set " + ", ".join(missing)
            )

        self.public_base = str(
            getattr(settings, "r2_public_base_url", "") or ""
        ).strip().rstrip("/")
        self.media_ttl = int(
            getattr(settings, "signed_url_ttl_seconds", MEDIA_URL_TTL_SECONDS)
        )
        self.share_ttl = int(
            getattr(settings, "share_url_ttl_seconds", SHARE_URL_TTL_SECONDS)
        )
        self.work_root = str(getattr(settings, "work_root", "") or tempfile.gettempdir())

        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ClientError
        except ImportError as exc:  # pragma: no cover - deployment configuration
            raise StorageError(
                "boto3 is required for R2 storage; add it to requirements"
            ) from exc

        self._client_error = ClientError
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, path) -> str:
        key = _normalize_rel(path)
        if not key or ".." in key.split("/"):
            raise StorageError(f"invalid storage key: {path!r}")
        return key

    def _upload_file(self, local_path: str, key: str) -> None:
        key = self._key(key)
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def _presign(self, key: str, ttl: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=max(1, int(ttl)),
        )

    # ------------------------------------------------------------------
    # Public interface used by routes and workers
    # ------------------------------------------------------------------

    def save_media(self, uid, upload_id, filename, tmp_path, **kwargs) -> str:
        uid = _segment(uid, "user id")
        upload_id = _segment(upload_id, "upload id")
        name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
        if not name or name in (".", ".."):
            raise StorageError("invalid filename")
        tag = str(kwargs.get("tag") or "").strip()
        if tag and not _SAFE_SEGMENT.fullmatch(tag):
            raise StorageError("invalid upload tag")
        folder = f"{tag}-{upload_id}" if tag else upload_id
        rel = f"media/{uid}/{folder}/{name}"
        self._upload_file(tmp_path, rel)
        return rel

    def publish(self, tmp_path: str, rel: str) -> str:
        self._upload_file(tmp_path, rel)
        return self.media_url(rel)

    def resolve(self, path, local_path: str | None = None) -> str:
        key = self._key(path)
        if local_path is None:
            os.makedirs(self.work_root, exist_ok=True)
            fd, local_path = tempfile.mkstemp(
                prefix="vidyra-", suffix=os.path.splitext(key)[1], dir=self.work_root
            )
            os.close(fd)
        else:
            parent = os.path.dirname(os.path.abspath(local_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        try:
            self.client.download_file(self.bucket, key, local_path)
        except Exception:
            try:
                os.remove(local_path)
            except OSError:
                pass
            raise
        return local_path

    def exists(self, path) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(path))
        except self._client_error as exc:
            if _status(exc) == 404:
                return False
            raise
        return True

    def blob_size(self, path) -> int:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._key(path))
        except self._client_error as exc:
            if _status(exc) == 404:
                return 0
            raise
        return int(head.get("ContentLength") or 0)

    def delete_file(self, path) -> bool:
        key = self._key(path)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self._client_error as exc:
            if _status(exc) == 404:
                return False
            raise
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def media_url(self, path, ttl: int | None = None) -> str:
        return self._presign(self._key(path), ttl or self.media_ttl)

    def share_url(self, path, ttl: int | None = None) -> str:
        key = self._key(path)
        if self.public_base:
            return f"{self.public_base}/{urllib.parse.quote(key)}"
        return self._presign(key, ttl or self.share_ttl)

    def list_user_files(self, uid: str) -> list[dict]:
        uid = _segment(uid, "user id")
        items: list[dict] = []
        seen: set[str] = set()
        paginator = self.client.get_paginator("list_objects_v2")
        for prefix in (f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/"):
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    key = str(obj.get("Key") or "")
                    if not key or key in seen:
                        continue
                    seen.add(key)
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
                            "size": int(obj.get("Size") or 0),
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


def _status(exc) -> int:
    response = getattr(exc, "response", None) or {}
    return int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)

"""Media storage. The deployment persists everything in Cloudflare R2."""

from __future__ import annotations

from .base import StorageError, as_absolute
from .r2 import R2StorageDriver


def get_storage_driver(settings):
    backend = str(getattr(settings, "storage_backend", "r2") or "r2").strip().lower()
    if backend in ("r2", "s3", "cloudflare"):
        return R2StorageDriver(settings, settings.sign_secret)
    if backend == "memory" and settings.app_env != "production":
        from .memory import MemoryStorageDriver

        return MemoryStorageDriver(settings, settings.sign_secret)
    raise StorageError(
        f"STORAGE_BACKEND={backend!r} is not supported; deployed media is stored "
        "in Cloudflare R2 only (set STORAGE_BACKEND=r2)."
    )


__all__ = ["R2StorageDriver", "StorageError", "as_absolute", "get_storage_driver"]

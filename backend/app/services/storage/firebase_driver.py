"""Firebase Storage driver (requires the Blaze plan since Feb 2026)."""

from __future__ import annotations

from urllib.parse import quote

from ..firebase import firebase
from . import StorageError


def blob_url(bucket_name: str, path: str) -> str:
    encoded = quote(path, safe="")
    return f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{encoded}?alt=media"


class FirebaseStorageDriver:
    kind = "firebase"

    def __init__(self, settings):
        self.settings = settings

    @property
    def bucket(self):
        if firebase.bucket is None:
            raise StorageError("Firebase Storage is not configured (requires Blaze plan).")
        return firebase.bucket

    def resolve(self, storage_path: str, local_dest: str) -> str:
        blob = self.bucket.blob(storage_path)
        blob.download_to_filename(local_dest)
        return local_dest

    def blob_size(self, storage_path: str) -> int:
        blob = self.bucket.blob(storage_path)
        metadata = blob.bucket.blob(blob.name).get_blob_metadata()
        return int(metadata.get("size", 0) or 0)

    def publish(self, local_path: str, storage_path: str) -> str:
        blob = self.bucket.blob(storage_path)
        blob.upload_from_filename(local_path, content_type="video/mp4")
        url = blob_url(self.bucket.name, storage_path)
        try:
            blob.make_public()
            url = blob.public_url or url
        except Exception:
            pass
        return url

    def media_url(self, storage_path: str) -> str:
        return blob_url(self.bucket.name, storage_path)

    def share_url(self, storage_path: str) -> str:
        return blob_url(self.bucket.name, storage_path)

    def delete_file(self, storage_path: str) -> bool:
        blob = self.bucket.blob(storage_path)
        if not blob.exists():
            return False
        blob.delete()
        return True
"""S3-compatible storage driver (Runpod network volumes, MinIO, AWS S3).

Runpod exposes its network volumes through an S3-compatible API. This driver
keeps the same interface as local/Firebase storage: workers download objects
to a local scratch path, uploads go straight to the bucket, and clients receive
short-lived presigned GET URLs instead of local file paths.

Configuration (env):
    STORAGE_BACKEND=s3
    S3_ENDPOINT_URL=https://s3api-us-ga-2.runpod.io/
    S3_REGION=US-GA-2
    S3_BUCKET=<network-volume-id>
    S3_ACCESS_KEY=user_...
    S3_SECRET_KEY=rps_...
"""

from __future__ import annotations

import datetime
import email.utils
import mimetypes
import os

_CLOCK_OFFSET_SECONDS = 0.0
_CLOCK_PATCHED = False


def _install_clock_offset(offset: float) -> None:
    """Make botocore sign with a corrected clock.

    S3 rejects requests whose signature timestamp is more than ~15 minutes
    from its own clock. A PC with a drifted clock otherwise fails every upload
    with RequestTimeTooSkewed. We calibrate from the server's Date header and
    patch botocore's signing clock for this process only.
    """
    global _CLOCK_OFFSET_SECONDS, _CLOCK_PATCHED
    _CLOCK_OFFSET_SECONDS = float(offset)
    if _CLOCK_PATCHED:
        return
    import botocore.auth as auth

    original = getattr(auth, "_vidyra_original_datetime", None) or auth.get_current_datetime
    auth._vidyra_original_datetime = original

    def current(remove_tzinfo=True):
        value = original(remove_tzinfo=False) + datetime.timedelta(
            seconds=_CLOCK_OFFSET_SECONDS
        )
        return value.replace(tzinfo=None) if remove_tzinfo else value

    auth.get_current_datetime = current
    _CLOCK_PATCHED = True

from . import StorageError


class S3StorageDriver:
    kind = "s3"
    #: Exposed for the one-time local→S3 migration script.

    def __init__(self, settings):
        endpoint = (getattr(settings, "s3_endpoint_url", "") or "").strip()
        bucket = (getattr(settings, "s3_bucket", "") or "").strip()
        access = (getattr(settings, "s3_access_key", "") or "").strip()
        secret = (getattr(settings, "s3_secret_key", "") or "").strip()
        if not (endpoint and bucket and access and secret):
            raise StorageError(
                "S3 storage needs S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY."
            )
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageError("Install boto3 to use S3 storage.") from exc

        self.bucket = bucket
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=(getattr(settings, "s3_region", "") or None),
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        )
        self._calibrate_clock()

    def _calibrate_clock(self) -> None:
        """Correct a drifted local clock using the bucket's Date header."""
        configured = getattr(self.settings, "s3_clock_offset_seconds", 0.0)
        if configured:
            _install_clock_offset(float(configured))
            return
        try:
            self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
            return
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str((response.get("Error") or {}).get("Code") or "")
            if "skew" not in code.lower() and "skew" not in str(exc).lower():
                return
            headers = (response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
            raw = headers.get("date") or headers.get("Date")
            if not raw:
                return
            try:
                server = email.utils.parsedate_to_datetime(raw)
                local = datetime.datetime.now(datetime.timezone.utc)
                _install_clock_offset((server - local).total_seconds())
            except Exception:
                return

    # -- worker helpers -------------------------------------------------

    def resolve(self, storage_path: str, local_dest: str | None = None) -> str:
        if not local_dest:
            raise StorageError("S3 objects must be downloaded to a local path.")
        os.makedirs(os.path.dirname(local_dest), exist_ok=True)
        try:
            self.client.download_file(self.bucket, storage_path, local_dest)
        except Exception as exc:
            raise StorageError(f"Missing media file: {storage_path}") from exc
        return local_dest

    def blob_size(self, storage_path: str) -> int:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=storage_path)
            return int(head.get("ContentLength") or 0)
        except Exception:
            return 0

    def publish(self, local_path: str, storage_path: str) -> str:
        content_type = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
        try:
            self.client.upload_file(
                local_path,
                self.bucket,
                storage_path,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as exc:
            raise StorageError(f"Could not upload {storage_path}") from exc
        return self.share_url(storage_path)

    def media_url(self, storage_path: str) -> str:
        if self.blob_size(storage_path) <= 0:
            raise StorageError(f"Missing media file: {storage_path}")
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_path},
            ExpiresIn=24 * 3600,
        )

    def share_url(self, storage_path: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_path},
            ExpiresIn=7 * 24 * 3600,
        )

    def delete_file(self, storage_path: str) -> bool:
        if self.blob_size(storage_path) <= 0:
            return False
        self.client.delete_object(Bucket=self.bucket, Key=storage_path)
        return True

    # -- upload route helpers -------------------------------------------

    def save_media(
        self,
        uid: str,
        upload_id: str,
        filename: str,
        source: str,
        tag: str = "",
    ) -> str:
        folder = f"{tag}-{upload_id}" if tag else upload_id
        rel = f"media/{uid}/{folder}/{filename}"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            self.client.upload_file(
                source,
                self.bucket,
                rel,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as exc:
            raise StorageError(f"Could not upload {filename}") from exc
        return rel

    def _keys(self, prefix: str):
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents") or []:
                yield obj
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    def list_user_audio(self, uid: str, music_only: bool = False) -> list[str]:
        from pipeline.config import AUDIO_EXTENSIONS
        from pipeline.utils import get_duration, is_music_upload, upload_tag

        paths: list[str] = []
        for obj in self._keys(f"media/{uid}/"):
            key = obj.get("Key") or ""
            if not key.lower().endswith(AUDIO_EXTENSIONS):
                continue
            if music_only:
                tag = upload_tag(key)
                if tag == "voice":
                    continue
                if not tag:
                    # Untagged legacy upload: download once to classify it.
                    local = os.path.join(self.settings.work_root, "probe", os.path.basename(key))
                    try:
                        self.resolve(key, local)
                        if not is_music_upload(key, get_duration(local)):
                            continue
                    except Exception:
                        continue
                    finally:
                        try:
                            os.remove(local)
                        except OSError:
                            pass
            paths.append(key)
        return paths

    def list_user_files(self, uid: str) -> list[dict]:
        from pipeline.utils import classify_media, get_duration, is_music_upload, upload_tag

        out: list[dict] = []
        for top in ("media", "studio", "videos"):
            for obj in self._keys(f"{top}/{uid}/"):
                key = obj.get("Key") or ""
                filename = key.rsplit("/", 1)[-1]
                kind = classify_media(filename)
                if kind not in ("image", "video", "audio"):
                    continue
                item = {
                    "path": key,
                    "name": filename,
                    "kind": kind,
                    "size": int(obj.get("Size") or 0),
                    "created": int((obj.get("LastModified") or 0).timestamp())
                    if obj.get("LastModified")
                    else 0,
                    "source": top,
                    "url": self.media_url(key),
                }
                if kind == "audio":
                    tag = upload_tag(key)
                    if tag:
                        item["purpose"] = "music" if tag == "music" else "voice"
                    else:
                        local = os.path.join(
                            self.settings.work_root, "probe", os.path.basename(key)
                        )
                        try:
                            self.resolve(key, local)
                            item["purpose"] = (
                                "music" if is_music_upload(key, get_duration(local)) else "voice"
                            )
                        except Exception:
                            item["purpose"] = "voice"
                        finally:
                            try:
                                os.remove(local)
                            except OSError:
                                pass
                out.append(item)
        out.sort(key=lambda x: x["created"], reverse=True)
        return out

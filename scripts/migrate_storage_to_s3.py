"""Upload every locally stored media/video object into S3-compatible storage.

Use this once when switching STORAGE_BACKEND=local → s3 so existing library
items, cast portraits, Studio runs and finished videos remain reachable.

Run after filling backend/config/storage.env or exporting the S3_* variables:

    .venv/Scripts/python.exe scripts/migrate_storage_to_s3.py
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)


def load_storage_env() -> None:
    env_file = ROOT / "backend" / "config" / "storage.env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def main() -> int:
    load_storage_env()
    from app.config import Settings
    from app.services.storage.s3 import S3StorageDriver

    settings = Settings()
    if settings.storage_backend.lower() != "s3":
        print("Set STORAGE_BACKEND=s3 before migrating.")
        return 1
    driver = S3StorageDriver(settings)
    root = pathlib.Path(settings.storage_root)
    uploaded = 0
    skipped = 0
    for top in ("media", "studio", "videos"):
        base = root / top
        if not base.is_dir():
            continue
        for file in base.rglob("*"):
            if not file.is_file():
                continue
            key = file.relative_to(root).as_posix()
            if driver.blob_size(key) > 0:
                skipped += 1
                continue
            driver.client.upload_file(str(file), driver.bucket, key)
            uploaded += 1
            if uploaded % 50 == 0:
                print(f"uploaded {uploaded}…")
    print(f"done: uploaded={uploaded} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deploy frontend/dist to Firebase Hosting using the Admin service account.

No `firebase login` is required: this uses the Firebase Hosting REST API with
the same service-account JSON as the backend.

    .venv\\Scripts\\python.exe scripts\\deploy_hosting.py

It creates a version, uploads gzipped files, finalizes the version with the SPA
rewrite, and releases it to the live channel.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SERVICE_ACCOUNT = ROOT / "backend" / "config" / "vidyra-firebase-adminsdk-fbsvc-31c93a2e64.json"
PROJECT = "vidyra"
SITE = "vidyra"
API = "https://firebasehosting.googleapis.com/v1beta1"


def token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT),
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/firebase",
        ],
    )
    creds.refresh(Request())
    return creds.token


def call(method, url, body=None, *, raw=None, content_type="application/json", headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {"Authorization": f"Bearer {token()}", "Content-Type": content_type}
    hdrs.update(headers or {})
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=120) as res:
                payload = res.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:800]
            raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # DNS on the hotspot is occasionally flaky
            last = exc
            time.sleep(2 + attempt * 2)
    raise SystemExit(f"{method} {url} failed: {last}")


def main() -> int:
    if not DIST.is_dir():
        print("frontend/dist is missing; run `npm run build` first.")
        return 1

    version = call(
        "POST",
        f"{API}/sites/{SITE}/versions",
        {"status": "CREATED"},
    )["name"]
    print("version", version)

    files = {}
    blobs = {}
    for path in sorted(DIST.rglob("*")):
        if not path.is_file():
            continue
        rel = "/" + path.relative_to(DIST).as_posix()
        gz = gzip.compress(path.read_bytes(), compresslevel=9)
        digest = hashlib.sha256(gz).hexdigest()
        files[rel] = digest
        blobs[digest] = gz

    populated = call(
        "POST",
        f"https://firebasehosting.googleapis.com/v1beta1/{version}:populateFiles",
        {"files": files},
    )
    upload_url = populated["uploadUrl"].rstrip("/")
    hashes = populated.get("uploadRequiredHashes") or []
    print(f"files={len(files)} uploads={len(hashes)}")

    for digest in hashes:
        call(
            "POST",
            f"{upload_url}/{digest}",
            raw=blobs[digest],
            content_type="application/octet-stream",
        )

    config = {
        "rewrites": [{"glob": "**", "path": "/index.html"}],
        "headers": [
            {
                "glob": "**/*.@(js|css)",
                "headers": {"Cache-Control": "public, max-age=31536000, immutable"},
            }
        ],
    }
    call(
        "PATCH",
        f"{API}/sites/{SITE}/versions/{version.rsplit('/', 1)[-1]}?updateMask=status,config",
        {"status": "FINALIZED", "config": config},
    )
    call(
        "POST",
        f"{API}/sites/{SITE}/channels/live/releases?versionName={urllib.parse.quote(version, safe='')}",
        {},
    )
    print(f"deployed: https://{SITE}.web.app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Create the Vidyra RunPod Serverless templates and endpoints.

Usage:
    python deploy/runpod/deploy_serverless.py --env-file deploy/runpod/.runpod_deploy.env

The env file needs RUNPOD_API_KEY and RUNPOD_NETWORK_VOLUME_ID. It may also set
RUNPOD_DATA_CENTER_ID, SIGNED_URL_SECRET, GEMINI_API_KEY, and PUBLIC_URL. Local
Firebase and R2 credentials are read from the repo so they do not have to be
copied into the env file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://rest.runpod.io/v1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = Path(__file__).with_name(".runpod_deploy.env")
OUTPUT_FILE = Path(__file__).with_name(".runpod_deploy.out")

STUDIO_TEMPLATE = "vidyra-studio-template"
STUDIO_ENDPOINT = "vidyra-studio"
PIPELINE_TEMPLATE = "vidyra-pipeline-template"
PIPELINE_ENDPOINT = "vidyra-pipeline"
STUDIO_IMAGE = "ghcr.io/frankimoney/vidyra-studio:latest"
PIPELINE_IMAGE = "ghcr.io/frankimoney/vidyra-pipeline:latest"
DEFAULT_PUBLIC_URL = "https://vidyra-api.onrender.com"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        values[key] = value
    return values


def read_env_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def local_r2_env(env: dict[str, str]) -> dict[str, str]:
    local = read_env_map(ROOT / "backend" / "config" / "storage.env")
    endpoint = env.get("R2_ENDPOINT") or local.get("S3_ENDPOINT_URL", "")
    result = {
        "R2_ENDPOINT": endpoint,
        "R2_BUCKET": env.get("R2_BUCKET") or local.get("S3_BUCKET", ""),
        "R2_ACCESS_KEY_ID": env.get("R2_ACCESS_KEY_ID")
        or local.get("S3_ACCESS_KEY", ""),
        "R2_SECRET_ACCESS_KEY": env.get("R2_SECRET_ACCESS_KEY")
        or local.get("S3_SECRET_KEY", ""),
    }
    if endpoint:
        host = urllib.parse.urlparse(endpoint).hostname or ""
        if host.endswith(".r2.cloudflarestorage.com"):
            result["R2_ACCOUNT_ID"] = host.split(".", 1)[0]
    if env.get("R2_ACCOUNT_ID"):
        result["R2_ACCOUNT_ID"] = env["R2_ACCOUNT_ID"]
    if env.get("R2_PUBLIC_BASE_URL"):
        result["R2_PUBLIC_BASE_URL"] = env["R2_PUBLIC_BASE_URL"]
    return {key: value for key, value in result.items() if value}


def local_firebase_env(env: dict[str, str]) -> dict[str, str]:
    raw = (env.get("FIREBASE_CREDENTIALS_JSON") or "").strip()
    project_id = (env.get("FIREBASE_PROJECT_ID") or "").strip()
    if not raw:
        candidates = sorted((ROOT / "backend" / "config").glob(
            "vidyra-firebase-adminsdk-*.json"
        ))
        if not candidates:
            candidates = sorted((ROOT / "config").glob(
                "vidyra-firebase-adminsdk-*.json"
            ))
        if candidates:
            raw = candidates[0].read_text(encoding="utf-8")
    if raw:
        try:
            data = json.loads(raw)
            project_id = project_id or str(data.get("project_id") or "")
            raw = json.dumps(data, separators=(",", ":"))
        except json.JSONDecodeError:
            pass
    result: dict[str, str] = {}
    if raw:
        result["FIREBASE_CREDENTIALS_JSON"] = raw
    if project_id:
        result["FIREBASE_PROJECT_ID"] = project_id
    if env.get("FIREBASE_BUCKET"):
        result["FIREBASE_BUCKET"] = env["FIREBASE_BUCKET"]
    return result


def api_request(
    method: str,
    path: str,
    api_key: str,
    body: dict[str, Any] | None = None,
) -> Any:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "VidyraRunpodDeploy/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            if not payload:
                return None
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc


def find_by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("name") == name:
            return item
    return None


def upsert_template(api_key: str, spec: dict[str, Any], dry_run: bool) -> str:
    name = spec["name"]
    if dry_run:
        preview = dict(spec)
        preview["env"] = sorted((spec.get("env") or {}).keys())
        print(f"[dry-run] template {name}: {json.dumps(preview, indent=2)}")
        return f"<{name}-id>"
    existing = find_by_name(api_request("GET", "/templates", api_key), name)
    if existing:
        template_id = existing["id"]
        api_request("PATCH", f"/templates/{template_id}", api_key, spec)
        print(f"Updated template {name} ({template_id})")
        return template_id
    created = api_request("POST", "/templates", api_key, spec)
    template_id = created["id"]
    print(f"Created template {name} ({template_id})")
    return template_id


def upsert_endpoint(api_key: str, spec: dict[str, Any], dry_run: bool) -> str:
    name = spec["name"]
    if dry_run:
        print(f"[dry-run] endpoint {name}: {json.dumps(spec, indent=2)}")
        return f"<{name}-id>"
    existing = find_by_name(api_request("GET", "/endpoints", api_key), name)
    if existing:
        endpoint_id = existing["id"]
        update = {key: value for key, value in spec.items() if key != "computeType"}
        api_request("PATCH", f"/endpoints/{endpoint_id}", api_key, update)
        print(f"Updated endpoint {name} ({endpoint_id})")
        return endpoint_id
    created = api_request("POST", "/endpoints", api_key, spec)
    endpoint_id = created["id"]
    print(f"Created endpoint {name} ({endpoint_id})")
    return endpoint_id


def require(value: str, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing required value: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-volumes", action="store_true")
    parser.add_argument("--create-volume", action="store_true",
                        help="create a network volume, print its ID, and exit")
    parser.add_argument("--volume-size-gb", type=int, default=100)
    parser.add_argument("--volume-name", default="vidyra-models")
    parser.add_argument("--data-center", default="")
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    api_key = env.get("RUNPOD_API_KEY", "").strip()
    volume_id = env.get("RUNPOD_NETWORK_VOLUME_ID", "").strip()

    if args.create_volume:
        require(api_key, "RUNPOD_API_KEY")
        data_center = (
            args.data_center
            or env.get("RUNPOD_DATA_CENTER_ID", "").strip()
        )
        data_center = require(
            data_center,
            "--data-center or RUNPOD_DATA_CENTER_ID (must support the chosen GPU)",
        )
        volume = api_request(
            "POST",
            "/networkvolumes",
            api_key,
            {
                "name": args.volume_name,
                "size": int(args.volume_size_gb),
                "dataCenterId": data_center,
            },
        )
        print(
            f"Created network volume {volume.get('name')} "
            f"({volume.get('id')}) in {volume.get('dataCenterId')} "
            f"({volume.get('size')}GB)"
        )
        print(f"\nPut this in {args.env_file}:")
        print(f"RUNPOD_NETWORK_VOLUME_ID={volume.get('id')}")
        print(f"RUNPOD_DATA_CENTER_ID={volume.get('dataCenterId')}")
        return 0

    if args.list_volumes or (not volume_id and not args.dry_run):
        if not api_key:
            raise SystemExit(
                "Set RUNPOD_API_KEY in the env file, then re-run with --list-volumes."
            )
        volumes = api_request("GET", "/networkvolumes", api_key)
        print("Network volumes:")
        for volume in volumes:
            print(
                f"  {volume.get('id')}  {volume.get('dataCenterId')}  "
                f"{volume.get('size')}GB  {volume.get('name')}"
            )
        if not volume_id:
            print(f"\nPut one ID in {args.env_file} as RUNPOD_NETWORK_VOLUME_ID.")
            return 2

    if not api_key and not args.dry_run:
        raise SystemExit(f"Missing RUNPOD_API_KEY in {args.env_file}.")

    data_center = env.get("RUNPOD_DATA_CENTER_ID", "").strip()
    if args.dry_run and not volume_id:
        volume_id = "<network-volume-id>"
        data_center = data_center or "<data-center>"
    elif api_key and volume_id:
        volumes = api_request("GET", "/networkvolumes", api_key)
        volume = next(
            (item for item in volumes if item.get("id") == volume_id), None
        )
        if volume is None:
            raise SystemExit(
                f"Network volume {volume_id} was not found. Run with --list-volumes."
            )
        data_center = data_center or str(volume.get("dataCenterId") or "")
    data_center = require(data_center, "RUNPOD_DATA_CENTER_ID or volume data center")

    public_url = env.get("PUBLIC_URL", DEFAULT_PUBLIC_URL).rstrip("/")
    signed_url_secret = env.get("SIGNED_URL_SECRET", "").strip()
    if not signed_url_secret and not args.dry_run:
        raise SystemExit(
            "Missing SIGNED_URL_SECRET. Copy it from the Render service env."
        )

    studio_env = {
        "MODEL_VOLUME": "/runpod-volume/models",
        "WHISPER_DOWNLOAD_ROOT": "/runpod-volume/models/whisper",
        "HF_HOME": "/runpod-volume/hf",
        "PYTHONUNBUFFERED": "1",
    }

    studio_template = {
        "name": STUDIO_TEMPLATE,
        "imageName": STUDIO_IMAGE,
        "isServerless": True,
        "category": "NVIDIA",
        "containerDiskInGb": 50,
        "volumeInGb": 0,
        "volumeMountPath": "/runpod-volume",
        "env": studio_env,
    }
    studio_template_id = upsert_template(api_key, studio_template, args.dry_run)

    # A single GPU type (e.g. A100 in one region) throttles whenever that
    # region has no capacity, which stalls every render at 0%. Offer a spread
    # of >=24GB cards so RunPod can place the worker wherever there is stock.
    studio_gpu_types = [
        item.strip()
        for item in (
            env.get("RUNPOD_STUDIO_GPU_TYPES")
            or env.get("RUNPOD_STUDIO_GPU_TYPE")
            or (
                "NVIDIA RTX A6000,NVIDIA L40S,NVIDIA L40,NVIDIA A40,"
                "NVIDIA A100 80GB PCIe,NVIDIA A100-SXM4-80GB,"
                "NVIDIA RTX PRO 6000 Blackwell Server Edition"
            )
        ).split(",")
        if item.strip()
    ]
    studio_endpoint = {
        "name": STUDIO_ENDPOINT,
        "templateId": studio_template_id,
        "computeType": "GPU",
        "gpuTypeIds": studio_gpu_types,
        "gpuCount": 1,
        "workersMin": 0,
        "workersMax": 1,
        # Keep the worker warm briefly so the frame -> video -> lip-sync passes
        # of one scene reuse the same pod instead of cold-starting (and
        # re-billing the model load) between every pass.
        "idleTimeout": int(env.get("RUNPOD_STUDIO_IDLE_TIMEOUT", "180")),
        # Hard cap a single GPU pass so a stuck render cannot bill for hours.
        "executionTimeoutMs": int(env.get("RUNPOD_STUDIO_EXEC_TIMEOUT_MIN", "30")) * 60 * 1000,
        "networkVolumeId": volume_id or "<network-volume-id>",
        "dataCenterIds": [data_center],
        "minCudaVersion": "12.8",
    }
    studio_endpoint_id = upsert_endpoint(api_key, studio_endpoint, args.dry_run)

    pipeline_env = {
        "APP_ENV": "production",
        "BACKGROUND_LOOPS": "false",
        "MAX_CONCURRENCY": "1",
        "STORAGE_BACKEND": "r2",
        "PUBLIC_URL": public_url,
        "SIGNED_URL_SECRET": signed_url_secret,
        "WHISPER_BACKEND": "runpod",
        "RUNPOD_API_KEY": api_key,
        "RUNPOD_STUDIO_ENDPOINT_ID": studio_endpoint_id,
        "RUNPOD_TIMEOUT_MINUTES": env.get("RUNPOD_TIMEOUT_MINUTES", "15"),
    }
    pipeline_env.update(local_firebase_env(env))
    pipeline_env.update(local_r2_env(env))
    if env.get("GEMINI_API_KEY"):
        pipeline_env["GEMINI_API_KEY"] = env["GEMINI_API_KEY"]
    pipeline_env = {key: value for key, value in pipeline_env.items() if value}

    missing = [
        key
        for key in (
            "FIREBASE_CREDENTIALS_JSON",
            "R2_ENDPOINT",
            "R2_BUCKET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        )
        if not pipeline_env.get(key)
    ]
    if missing and not args.dry_run:
        raise SystemExit(f"Missing pipeline env values: {', '.join(missing)}")

    pipeline_template = {
        "name": PIPELINE_TEMPLATE,
        "imageName": PIPELINE_IMAGE,
        "isServerless": True,
        "category": "CPU",
        "containerDiskInGb": 20,
        "volumeInGb": 0,
        "env": pipeline_env,
    }
    pipeline_template_id = upsert_template(api_key, pipeline_template, args.dry_run)

    pipeline_endpoint = {
        "name": PIPELINE_ENDPOINT,
        "templateId": pipeline_template_id,
        "computeType": "CPU",
        "cpuFlavorIds": [env.get("RUNPOD_PIPELINE_CPU_FLAVOR", "cpu5c")],
        "vcpuCount": int(env.get("RUNPOD_PIPELINE_VCPU", "8")),
        "workersMin": 0,
        "workersMax": 1,
        "idleTimeout": 5,
        "executionTimeoutMs": 2 * 60 * 60 * 1000,
    }
    pipeline_endpoint_id = upsert_endpoint(api_key, pipeline_endpoint, args.dry_run)

    if not args.dry_run:
        OUTPUT_FILE.write_text(
            "\n".join(
                [
                    f"RUNPOD_ENDPOINT_ID={studio_endpoint_id}",
                    f"RUNPOD_PIPELINE_ENDPOINT_ID={pipeline_endpoint_id}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {OUTPUT_FILE}")
        print("\nSet these on the Render service, then restart it:")
        print(f"  RUNPOD_API_KEY=<same key from {args.env_file}>")
        print(f"  RUNPOD_ENDPOINT_ID={studio_endpoint_id}")
        print(f"  RUNPOD_PIPELINE_ENDPOINT_ID={pipeline_endpoint_id}")
        if not env.get("GEMINI_API_KEY"):
            print("  GEMINI_API_KEY=<your Gemini key>")
    return 0


if __name__ == "__main__":
    sys.exit(main())

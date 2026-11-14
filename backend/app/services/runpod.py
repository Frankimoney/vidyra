"""Minimal RunPod Serverless API client (HTTP, no SDK dependency).

RunPod Serverless exposes an HTTP API:

  POST /v2/{endpoint_id}/run        {"input": {...}}   -> {id}
  GET  /v2/{endpoint_id}/status/{id}                    -> {status, output, ...}

Status values: IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED, CANCELLED, TIMED_OUT.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


DEFAULT_API_BASE = "https://api.runpod.ai/v2"
DEFAULT_REST_API_BASE = "https://rest.runpod.io/v1"


def normalize_api_base(api_base: str) -> str:
    """Always produce a base ending in /v2, even if env omits or doubles it."""
    base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
    if not base:
        return DEFAULT_API_BASE
    return base if base.endswith("/v2") else f"{base}/v2"


def normalize_endpoint_id(endpoint_id: str) -> str:
    """Accept either a bare id, a path fragment, or a full RunPod endpoint URL."""
    value = (endpoint_id or "").strip().strip("/")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    if not value:
        raise RunPodError("RUNPOD_ENDPOINT_ID is empty.")
    return value


class RunPodError(RuntimeError):
    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


def _request(
    url: str,
    api_key: str,
    payload: dict | None = None,
    timeout: int = 60,
    method: str | None = None,
):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        # RunPod's REST API rejects urllib's default agent with a 403.
        "User-Agent": "VidyraBackend/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if method is None:
        method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        raise RunPodError(f"RunPod API {exc.code}: {body or exc.reason} ({url})")


def submit_run(api_base: str, api_key: str, endpoint_id: str, input_payload: dict) -> str:
    """Create a run on the serverless endpoint; returns the run id."""
    base = normalize_api_base(api_base)
    endpoint_id = normalize_endpoint_id(endpoint_id)
    result = _request(f"{base}/{endpoint_id}/run", api_key, {"input": input_payload})
    run_id = result.get("id")
    if not run_id:
        raise RunPodError(f"RunPod did not return a run id: {result!r}")
    return run_id


def run_sync(
    api_base: str,
    api_key: str,
    endpoint_id: str,
    input_payload: dict,
    timeout: int = 300,
) -> dict:
    """Run synchronously and return the completed response.

    Used by short deterministic tools such as Whisper transcription where the
    caller needs the result inline rather than an artifact callback.
    """
    base = normalize_api_base(api_base)
    endpoint_id = normalize_endpoint_id(endpoint_id)
    return _request(
        f"{base}/{endpoint_id}/runsync",
        api_key,
        {"input": input_payload},
        timeout=timeout,
    )


def get_run_status(api_base: str, api_key: str, endpoint_id: str, run_id: str) -> dict:
    base = normalize_api_base(api_base)
    endpoint_id = normalize_endpoint_id(endpoint_id)
    result = _request(f"{base}/{endpoint_id}/status/{run_id}", api_key)
    if not result:
        raise RunPodError(f"RunPod returned an empty status for {run_id}")
    return result


def cancel_run(api_base: str, api_key: str, endpoint_id: str, run_id: str) -> dict:
    """Ask RunPod to cancel a run.

    Used when the backend gives up on a job (timeout / shutdown) so a GPU pod
    stops billing instead of finishing work nobody will collect.
    """
    try:
        base = normalize_api_base(api_base)
        endpoint_id = normalize_endpoint_id(endpoint_id)
        return _request(
            f"{base}/{endpoint_id}/cancel/{run_id}", api_key, {}, timeout=20
        )
    except RunPodError:
        # A run that already finished/expired can't be cancelled; the caller
        # still wants its own cleanup to continue.
        return {}


def runpod_health(api_base: str, api_key: str, endpoint_id: str) -> bool:
    return endpoint_id.strip() != ""


def get_endpoint_health(
    api_base: str, api_key: str, endpoint_id: str, timeout: int = 15
) -> dict:
    """Return a serverless endpoint's health (worker + queue counts)."""
    base = normalize_api_base(api_base)
    endpoint_id = normalize_endpoint_id(endpoint_id)
    return _request(f"{base}/{endpoint_id}/health", api_key, timeout=timeout)


def set_endpoint_workers(
    api_key: str,
    endpoint_id: str,
    workers_min: int | None = None,
    workers_max: int | None = None,
    rest_api_base: str = DEFAULT_REST_API_BASE,
) -> dict:
    """Adjust a serverless endpoint's worker bounds.

    RunPod's autoscaler can stop scaling a datacenter-pinned endpoint up from
    zero (the Studio endpoint is pinned to its model volume's datacenter), so a
    queued job never gets a pod. Setting ``workersMin=1`` forces a worker to
    provision; setting it back to 0 releases it so nothing bills while idle.

    An endpoint whose ``workersMax`` is 0 is *paused* and rejects new work with
    ``ENDPOINT_PAUSED``, so waking also has to raise ``workersMax`` above zero.
    """
    endpoint_id = normalize_endpoint_id(endpoint_id)
    url = f"{(rest_api_base or DEFAULT_REST_API_BASE).rstrip('/')}/endpoints/{endpoint_id}"
    payload: dict = {}
    if workers_min is not None:
        payload["workersMin"] = int(workers_min)
    if workers_max is not None:
        payload["workersMax"] = int(workers_max)
    if not payload:
        return {}
    return _request(url, api_key, payload, timeout=20, method="PATCH")


def set_endpoint_workers_min(
    api_key: str,
    endpoint_id: str,
    workers_min: int,
    rest_api_base: str = DEFAULT_REST_API_BASE,
) -> dict:
    """Backwards-compatible wrapper around :func:`set_endpoint_workers`."""
    return set_endpoint_workers(
        api_key, endpoint_id, workers_min=workers_min, rest_api_base=rest_api_base
    )
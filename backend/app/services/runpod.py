"""Minimal RunPod Serverless API client (HTTP, no SDK dependency).

RunPod Serverless exposes an HTTP API:

  POST /v2/{endpoint_id}/run        {"input": {...}}   -> {id}
  GET  /v2/{endpoint_id}/runs/{id}                      -> {status, output, ...}

Status values: IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED, CANCELLED, TIMED_OUT.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


class RunPodError(RuntimeError):
    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


def _request(url: str, api_key: str, payload: dict | None = None, timeout: int = 60):
    headers = {"Authorization": f"Bearer {api_key}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")
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
        raise RunPodError(f"RunPod API {exc.code}: {body or exc.reason}")


def submit_run(api_base: str, api_key: str, endpoint_id: str, input_payload: dict) -> str:
    """Create a run on the serverless endpoint; returns the run id."""
    result = _request(f"{api_base}/{endpoint_id}/run", api_key, {"input": input_payload})
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
    return _request(
        f"{api_base}/{endpoint_id}/runsync",
        api_key,
        {"input": input_payload},
        timeout=timeout,
    )


def get_run_status(api_base: str, api_key: str, endpoint_id: str, run_id: str) -> dict:
    result = _request(f"{api_base}/{endpoint_id}/runs/{run_id}", api_key)
    if not result:
        raise RunPodError(f"RunPod returned an empty status for {run_id}")
    return result


def cancel_run(api_base: str, api_key: str, endpoint_id: str, run_id: str) -> dict:
    """Ask RunPod to cancel a run.

    Used when the backend gives up on a job (timeout / shutdown) so a GPU pod
    stops billing instead of finishing work nobody will collect.
    """
    try:
        return _request(
            f"{api_base}/{endpoint_id}/cancel/{run_id}", api_key, {}, timeout=20
        )
    except RunPodError:
        # A run that already finished/expired can't be cancelled; the caller
        # still wants its own cleanup to continue.
        return {}


def runpod_health(api_base: str, api_key: str, endpoint_id: str) -> bool:
    return endpoint_id.strip() != ""
"""Unified RunPod Serverless handler for Vidyra (one endpoint runs everything).

The merged GPU pod serves two job shapes on the same endpoint:

  * ``{"input": {"job_id": "..."}}`` — a film/podcast render. Delegated to
    ``pipeline_handler``, which runs the VideoWorker/FilmWorker code. Those
    workers drive ComfyUI in-process (STUDIO_LOCAL=1) rather than submitting
    sub-jobs to this endpoint, which would deadlock behind the running job when
    ``workersMax=1``.
  * ``{"input": {"run_id": ..., "tool": ...}}`` — a standalone Studio run.
    Delegated to the GPU worker's ComfyUI handler.

RunPod can only start one serverless loop per process, so both handlers are
dispatched from here instead of each calling ``runpod.serverless.start``.
"""

from __future__ import annotations

import traceback

import runpod

import pipeline_handler
import worker


def handler(event):
    payload = (event or {}).get("input") or {}
    if payload.get("job_id"):
        return pipeline_handler.handler(event)
    if payload.get("run_id") or payload.get("tool"):
        return worker.handler(event)
    return {"error": "input must carry either job_id (render) or run_id/tool (studio)"}


if __name__ == "__main__":
    try:
        runpod.serverless.start({"handler": handler})
    except Exception:
        traceback.print_exc()
        raise

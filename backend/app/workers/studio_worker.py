"""AI Studio job runner.

Two operating modes:
  * Real (RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID set): submits the run payload to
    the configured RunPod Serverless ComfyUI endpoint, polls until COMPLETED,
    and finalizes once the GPU worker has streamed artifacts back through the
    signed artifact callback.
  * Fake (RUNPOD_FAKE=1): simulates the whole lifecycle on the PC, producing a
    real placeholder PNG / MP4 so the app flow works end-to-end with no GPU.

Progress notes: serverless runs report coarse stages (queued → generating →
finalizing); the GPU worker can push finer progress via /studio/progress.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import hashlib
import time

from pipeline.utils import classify_media, run_cmd

from ..config import Settings
from ..services import runpod as runpod_client
from ..services import studio as studio_service
from ..services import usage as usage_service
from ..services.storage.base import as_absolute

# Tools whose GPU work is many sequential passes rather than one sample, and
# so need a proportionally longer wall-clock budget on RunPod.
MOTION_TIMEOUT_SCALE = {"motion": 3}


def _artifact_token(run_id: str, secret: str) -> str:
    """HMAC token a GPU worker must present to stream an artifact back."""
    return hmac.new(secret.encode(), f"studio:{run_id}".encode(), hashlib.sha256).hexdigest()[:40]


def _pod_meta(output) -> dict:
    """The pod's own return value, as a dict (RunPod may hand it back as JSON)."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return {}
    if not isinstance(output, dict):
        return {}
    return {k: v for k, v in output.items() if k in ("lines", "uploads", "elapsed")}


class StudioWorker:
    def __init__(self, settings: Settings, storage):
        self.settings = settings
        self.storage = storage
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, run_id: str) -> None:
        task = asyncio.create_task(self._run(run_id))
        task.add_done_callback(self._on_done)

    def _on_done(self, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            pass  # handled inside _process

    async def _run(self, run_id: str) -> None:
        async with self._semaphore:
            await asyncio.to_thread(self._process, run_id)

    # ------------------------------------------------------------------
    # Sync processing (thread)
    # ------------------------------------------------------------------

    def _process(self, run_id: str) -> None:
        try:
            run = self._load(run_id)
            tool = (run.get("tool") or "").strip()
            if self.settings.runpod_fake:
                self._run_fake(run_id, run, tool)
                return
            if not self.settings.runpod_enabled:
                self._fail(
                    run_id,
                    StudioError("AI Studio is not configured. Set RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID, or enable RUNPOD_FAKE=1."),
                )
                return
            self._run_remote(run_id, run, tool)
        except Exception as exc:
            self._fail(run_id, exc)

    def _load(self, run_id: str) -> dict:
        run = studio_service.get_run(run_id)
        if not run:
            raise StudioError(f"Run not found: {run_id}")
        return run

    def _record_usage(self, run: dict, tool: str, *, reference: str = "", estimated_cost: float | None = None) -> None:
        usage_service.record(
            str(run.get("userId") or ""),
            workspace_id=str(run.get("workspaceId") or ""),
            kind="studio",
            tool=tool,
            model=str(run.get("model") or ""),
            job_id=str(run.get("jobId") or ""),
            reference=reference,
            estimated_cost=estimated_cost,
        )

    # ------------------------------------------------------------------
    # Real mode (RunPod Serverless)
    # ------------------------------------------------------------------

    def _run_remote(self, run_id: str, run: dict, tool: str):
        studio_service.update_run(
            run_id,
            status="processing",
            stage="queuing",
            progress=2,
            message="Queuing GPU job",
        )

        payload = self._build_payload(run_id, run)
        api_base = self.settings.runpod_api_base.rstrip("/")
        endpoint = self.settings.runpod_endpoint_id.strip()

        runpod_run_id = runpod_client.submit_run(
            api_base, self.settings.runpod_api_key, endpoint, payload
        )
        # Counted at submit: a run that later times out may still have spent GPU.
        self._record_usage(run, tool, reference=runpod_run_id)
        studio_service.update_run(
            run_id, runpodRunId=runpod_run_id, stage="generating", message="GPU job started"
        )

        started = time.time()
        last_elapsed_progress = 8
        timeout = self.settings.runpod_timeout_minutes * 60
        if tool in MOTION_TIMEOUT_SCALE:
            # A 30s Wan-Animate-2 transfer is up to 12 sequential 81-frame
            # windows on one pod, so it needs a far longer leash than the
            # single-pass tools; everything else keeps the normal budget.
            timeout = max(timeout, self.settings.runpod_timeout_minutes
                          * 60 * MOTION_TIMEOUT_SCALE[tool])
        while True:
            time.sleep(self.settings.runpod_poll_seconds)
            status = runpod_client.get_run_status(api_base, self.settings.runpod_api_key, endpoint, runpod_run_id)
            state = str(status.get("status") or "").upper()

            if state == "COMPLETED":
                break
            if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
                detail = status.get("error") or status.get("output")
                raise StudioError(f"GPU job {state.lower().replace('_', ' ')}: {detail}")

            elapsed = time.time() - started
            if elapsed > timeout:
                # Stop the pod billing instead of letting it finish work nobody
                # will collect.
                runpod_client.cancel_run(
                    api_base, self.settings.runpod_api_key, endpoint, runpod_run_id
                )
                raise StudioError("GPU job timed out.")
            progress = min(84, int(last_elapsed_progress + elapsed * 2))
            if progress != last_elapsed_progress:
                last_elapsed_progress = progress
                studio_service.update_run(
                    run_id, stage="generating", progress=progress, message="Rendering on GPU"
                )

        studio_service.update_run(run_id, stage="finalizing", progress=90, message="Retrieving output")
        output = self._finalize(run_id, run, _pod_meta(status.get("output")))
        studio_service.update_run(
            run_id,
            status="completed",
            stage="completed",
            progress=100,
            message="Result ready",
            output=output,
        )

    def _build_payload(self, run_id: str, run: dict) -> dict:
        """Payload sent to the RunPod ComfyUI worker.

        refs carry signed public URLs so the GPU pod can fetch the user's
        source media; callback carries a signed URL the worker uses to stream
        finished files back to this backend.
        """
        params = run.get("params") or {}
        refs = []
        for ref in run.get("refs") or []:
            if isinstance(ref, str):
                storage_path, role = ref, ""
            else:
                storage_path = (ref or {}).get("path") or (ref or {}).get("storagePath", "")
                role = (ref or {}).get("role", "")
            if not storage_path:
                continue
            try:
                url = as_absolute(
                    self.storage.media_url(storage_path, 7 * 24 * 3600),
                    self.settings.public_url,
                )
            except Exception:
                continue
            refs.append({"path": storage_path, "url": url, "role": role})

        callback_token = _artifact_token(run_id, self.settings.sign_secret)
        return {
            "run_id": run_id,
            "tool": run.get("tool"),
            "model": run.get("model"),
            "prompt": run.get("prompt", ""),
            "params": params,
            "refs": refs,
            "callback": {
                "base_url": f"{self.settings.public_url.rstrip('/')}",
                "artifact_path": f"/api/studio/artifacts/{run_id}",
                "progress_path": f"/api/studio/progress/{run_id}",
                "token": callback_token,
            },
        }

    def _finalize(self, run_id: str, run: dict, meta: dict | None = None) -> dict | None:
        """Build the output summary from artifacts the worker pushed back.

        ``meta`` is the pod's own return value (e.g. per-line durations for a
        multi-line voice clone) so the caller can use data that travels in the
        job result rather than as an extra artifact file.
        """
        run = studio_service.get_run(run_id) or run
        output = run.get("output") or {}
        artifacts = output.get("artifacts") or []
        if not artifacts:
            return None

        primary = None
        for artifact in artifacts:
            kind = classify_media(str(artifact.get("name") or ""))
            if kind is None:
                continue
            entry = {
                "name": artifact.get("name"),
                "kind": kind,
                "storagePath": artifact.get("storagePath"),
                "url": as_absolute(
                    self.storage.media_url(artifact.get("storagePath", "")),
                    self.settings.public_url,
                ),
                "size": artifact.get("size", 0),
                "duration": artifact.get("duration"),
                "width": artifact.get("width"),
                "height": artifact.get("height"),
            }
            if kind == "video" and primary is None:
                primary = entry
            elif primary is None:
                primary = entry
        result = {"artifacts": artifacts, "primary": primary}
        if meta:
            result["meta"] = meta
        return result

    # ------------------------------------------------------------------
    # Fake mode (local, no GPU)
    # ------------------------------------------------------------------

    def _run_fake(self, run_id: str, run: dict, tool: str):
        uid = run.get("userId", "anonymous")
        # Fake runs cost nothing, but still show up in the ledger so the
        # dashboard and budget logic can be exercised locally.
        self._record_usage(run, tool, estimated_cost=0.0)
        params = run.get("params") or {}
        width = int(params.get("width") or (576 if tool != "txt2img" else 1024))
        height = int(params.get("height") or (1024 if tool != "txt2img" else 1024))
        fps = int(params.get("fps") or 24)
        # Multi-window tools (Wan-Animate-2) ask for seconds, single-pass tools
        # for frames; the placeholder should be the length the user requested.
        frames = int(params.get("frames") or 0)
        if not frames and params.get("duration"):
            frames = int(round(float(params["duration"]) * fps))
        frames = frames or 48

        studio_service.update_run(
            run_id,
            status="processing",
            stage="generating",
            progress=10,
            message=f"Simulating {run.get('model', tool)} on local fake GPU",
        )

        work_dir = os.path.join(self.settings.work_root, run_id)
        os.makedirs(work_dir, exist_ok=True)
        time.sleep(2)

        if tool in ("txt2img", "img2img", "character"):
            studio_service.update_run(run_id, progress=50, message="Diffusing image")
            out = self._make_fake_image(work_dir, width, height)
            kind = "image"
        elif tool == "voiceclone":
            studio_service.update_run(run_id, progress=50, message="Synthesizing voice")
            out = self._make_fake_audio(work_dir)
            kind = "audio"
        else:
            studio_service.update_run(run_id, progress=50, message="Rendering video frames")
            out = self._make_fake_video(work_dir, width, height, frames, fps)
            kind = "video"
        time.sleep(2)

        dest = f"{self.settings.studio_prefix}/{uid}/{run_id}/output"
        ext = os.path.splitext(out)[1]
        storage_path = dest + ext
        signed = self.storage.publish(out, storage_path)
        duration = None
        if kind in ("video", "audio"):
            from pipeline.utils import get_duration

            try:
                duration = round(get_duration(out), 2)
            except Exception:
                pass

        artifact = {
            "name": os.path.basename(out),
            "kind": kind,
            "storagePath": storage_path,
            "url": as_absolute(signed, self.settings.public_url),
            "size": os.path.getsize(out),
            "duration": duration,
            # The canvas actually rendered, so the UI can confirm the chosen
            # aspect ratio landed rather than trusting the request.
            "width": width,
            "height": height,
        }
        studio_service.store_artifact(run_id, artifact)
        studio_service.update_run(
            run_id,
            status="completed",
            stage="completed",
            progress=100,
            message="Result ready (fake GPU)",
            output={
                "artifacts": [artifact],
                "primary": artifact,
                "simulated": True,
            },
        )

    def _make_fake_image(self, work_dir: str, width: int, height: int) -> str:
        out = os.path.join(work_dir, "output.png")
        run_cmd(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=0x4f46e5:s={width}x{height}:d=1",
                "-vf", "drawgrid=w=iw/8:h=ih/8:t=2:c=white@0.35",
                "-frames:v", "1",
                out,
            ]
        )
        return out

    def _make_fake_audio(self, work_dir: str) -> str:
        out = os.path.join(work_dir, "output.wav")
        run_cmd(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "sine=frequency=440:duration=2",
                "-ar", "24000",
                "-ac", "1",
                out,
            ]
        )
        return out

    def _make_fake_video(self, work_dir: str, width: int, height: int, frames: int,
                         fps: int = 24) -> str:
        out = os.path.join(work_dir, "output.mp4")
        run_cmd(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"testsrc2=s={width}x{height}:r={fps}:d={max(1, round(frames / max(1, fps), 2))}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                out,
            ]
        )
        return out

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _fail(self, run_id: str, exc: Exception):
        message = " ".join(str(exc).split())[:500]
        try:
            studio_service.update_run(
                run_id,
                status="failed",
                stage="failed",
                progress=0,
                message=message,
                error=message,
            )
        except Exception:
            pass


class StudioError(RuntimeError):
    pass
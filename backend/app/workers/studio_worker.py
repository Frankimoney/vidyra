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
import threading
import time

from pipeline.utils import classify_media, run_cmd

from ..config import Settings
from ..services import runpod as runpod_client
from ..services import studio as studio_service
from ..services import usage as usage_service
from ..services.storage.base import as_absolute

def _artifact_token(run_id: str, secret: str) -> str:
    """HMAC token a GPU worker must present to stream an artifact back."""
    return hmac.new(secret.encode(), f"studio:{run_id}".encode(), hashlib.sha256).hexdigest()[:40]


def wake_studio_endpoint(settings: Settings) -> None:
    """Force the Studio endpoint to provision a worker (best effort).

    The Studio endpoint is pinned to its model volume's datacenter, where
    RunPod's autoscaler will not scale from zero — a queued job would sit
    forever at 0%. Pinning ``workersMin=1`` makes a pod come up; call
    ``rest_studio_endpoint`` to release it so nothing bills while idle.
    """
    if not getattr(settings, "runpod_studio_wake", True):
        return
    if not (settings.runpod_api_key and settings.studio_endpoint_id):
        return
    try:
        runpod_client.set_endpoint_workers_min(
            settings.runpod_api_key,
            settings.studio_endpoint_id,
            1,
            getattr(settings, "runpod_rest_api_base", runpod_client.DEFAULT_REST_API_BASE),
        )
    except Exception:
        pass  # waking is best-effort; the run still queues and may cold-start


def rest_studio_endpoint(settings: Settings) -> None:
    """Release the Studio endpoint's pinned worker so it can scale to zero."""
    if not getattr(settings, "runpod_studio_wake", True):
        return
    if not (settings.runpod_api_key and settings.studio_endpoint_id):
        return
    try:
        runpod_client.set_endpoint_workers_min(
            settings.runpod_api_key,
            settings.studio_endpoint_id,
            0,
            getattr(settings, "runpod_rest_api_base", runpod_client.DEFAULT_REST_API_BASE),
        )
    except Exception:
        pass


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
        # Nesting count for the Studio endpoint wake. A film pins the endpoint
        # once for all of its passes; standalone runs pin it each. It is
        # released only when the last caller finishes, so overlapping work never
        # releases a worker another caller still needs.
        self._wake_depth = 0
        self._wake_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def wake(self) -> None:
        """Pin a Studio worker for the duration of the caller's work."""
        with self._wake_lock:
            self._wake_depth += 1
            first = self._wake_depth == 1
        if first:
            wake_studio_endpoint(self.settings)

    def rest(self) -> None:
        """Release the pinned Studio worker once no caller needs it."""
        with self._wake_lock:
            self._wake_depth = max(0, self._wake_depth - 1)
            last = self._wake_depth == 0
        if last:
            rest_studio_endpoint(self.settings)

    async def submit(self, run_id: str, rest_after: bool = False) -> None:
        task = asyncio.create_task(self._run(run_id, rest_after=rest_after))
        task.add_done_callback(self._on_done)

    def _on_done(self, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            pass  # handled inside _process

    async def _run(self, run_id: str, rest_after: bool = False) -> None:
        try:
            async with self._semaphore:
                await asyncio.to_thread(self._process, run_id)
        finally:
            if rest_after:
                self.rest()

    # ------------------------------------------------------------------
    # Sync processing (thread)
    # ------------------------------------------------------------------

    def _process(self, run_id: str, on_progress=None) -> None:
        """Run one studio pass to completion.

        ``on_progress(progress, message)`` is called as the run advances so a
        caller that owns a bigger job (the film worker) can fold this pass's
        progress into its own bar. Direct callers pass nothing.
        """
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
            self._run_remote(run_id, run, tool, on_progress=on_progress)
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

    def _run_remote(self, run_id: str, run: dict, tool: str, on_progress=None):
        studio_service.update_run(
            run_id,
            status="processing",
            stage="queuing",
            progress=2,
            message="Queuing GPU job",
        )

        payload = self._build_payload(run_id, run)
        api_base = self.settings.runpod_api_base.rstrip("/")
        endpoint = self.settings.studio_endpoint_id

        runpod_run_id = runpod_client.submit_run(
            api_base, self.settings.runpod_api_key, endpoint, payload
        )
        # Counted at submit: a run that later times out may still have spent GPU.
        self._record_usage(run, tool, reference=runpod_run_id)
        studio_service.update_run(
            run_id, runpodRunId=runpod_run_id, stage="generating", message="GPU job started"
        )

        started = time.time()
        timeout = self.settings.runpod_timeout_minutes * 60
        # Progress shown to the user. The GPU worker streams real milestones
        # through /studio/progress (5 preparing, 10 fetching, 30 rendering,
        # 80 streaming, 95 done). Between milestones we crawl slowly so the bar
        # never looks frozen, but we never jump ahead of the next real one: the
        # old elapsed-time ramp raced to 84% in ~40s and then sat there for the
        # whole (slow, billed) render, which is exactly what read as "stuck".
        shown = 2
        last_sent = None
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

            doc = studio_service.get_run(run_id) or {}
            reported = int(doc.get("progress") or 0)
            message = str(doc.get("message") or "Rendering on GPU")
            # Crawl toward the next real milestone, never past it.
            target = 28 if reported < 30 else 78
            shown = max(shown, reported)
            if shown < target:
                shown = min(target, shown + 1)
            if (shown, message) != last_sent:
                last_sent = (shown, message)
                studio_service.update_run(
                    run_id, stage="generating", progress=shown, message=message
                )
                if on_progress:
                    on_progress(shown, message)

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
        # Some callers ask for seconds instead of frames; the placeholder
        # should still be the length the user requested.
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
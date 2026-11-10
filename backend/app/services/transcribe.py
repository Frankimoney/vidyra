"""Runpod Whisper transcription for karaoke captions.

The scene renderer owns the narration WAV. When Runpod is configured, this
service converts that WAV to a compact 16 kHz mono FLAC, uploads it to the
configured storage, calls the Runpod worker's ``transcribe`` tool through
``/runsync``, and returns word timestamps. Any failure returns ``None`` so the
renderer falls back to local Whisper instead of losing captions.

Usage is recorded against the workspace exactly like a Studio run.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid

_DISABLED_UNTIL = 0.0

from pipeline.utils import run_cmd

from . import runpod as runpod_client
from . import usage as usage_service
from .storage.local import as_absolute


def make_transcriber(uid: str, workspace_id: str, storage, settings, job_id: str = ""):
    """Return a callable ``(audio_path) -> words | None`` for RenderSettings."""

    def transcribe(audio_path: str):
        global _DISABLED_UNTIL
        if settings.whisper_backend != "runpod":
            return None
        if not settings.runpod_enabled or settings.runpod_fake:
            return None
        if time.time() < _DISABLED_UNTIL:
            return None
        if not audio_path or not os.path.isfile(audio_path):
            return None

        flac = os.path.join(
            tempfile.gettempdir(), f"vidyra-captions-{uuid.uuid4().hex[:10]}.flac"
        )
        storage_path = ""
        try:
            run_cmd(
                [
                    "ffmpeg", "-y",
                    "-i", audio_path,
                    "-ac", "1",
                    "-ar", "16000",
                    "-c:a", "flac",
                    flac,
                ]
            )
            # Put the clip in the user's own namespace so every storage driver
            # can hand the GPU worker a readable URL.
            storage_path = storage.save_media(
                uid, f"captions-{uuid.uuid4().hex[:10]}", "narration.flac", flac
            )
            url = as_absolute(storage.media_url(storage_path), settings.public_url)
            result = runpod_client.run_sync(
                settings.runpod_api_base.rstrip("/"),
                settings.runpod_api_key,
                settings.runpod_endpoint_id.strip(),
                {
                    "tool": "transcribe",
                    "model": settings.whisper_model or "base",
                    "audio_url": url,
                },
                timeout=max(300, settings.runpod_timeout_minutes * 60),
            )
            if str(result.get("status") or "").upper() not in ("COMPLETED", "SUCCESS"):
                print(f"[captions] transcribe run not completed: {result.get('status')}")
                return None
            output = result.get("output") or {}
            if isinstance(output, str):
                import json

                output = json.loads(output)
            words = (output or {}).get("words")
            if not isinstance(words, list) or not words:
                return None

            clean = []
            for word in words:
                if not isinstance(word, dict):
                    continue
                text = str(word.get("word") or "").strip()
                if not text:
                    continue
                clean.append(
                    {
                        "word": text,
                        "start": float(word.get("start") or 0),
                        "end": float(word.get("end") or 0),
                    }
                )
            if not clean:
                return None

            usage_service.record(
                uid,
                workspace_id=workspace_id,
                kind="transcribe",
                tool="whisper",
                model=f"faster-whisper-{settings.whisper_model or 'base'}",
                job_id=job_id,
                reference="captions",
                estimated_cost=0.005,
            )
            return clean
        except Exception as exc:  # noqa: BLE001 - local fallback is intentional
            # Back off for a few minutes so a missing/misconfigured worker does
            # not add a failed network round-trip to every scene.
            _DISABLED_UNTIL = time.time() + 300
            print(f"[captions] Runpod transcription failed ({exc}); using local Whisper")
            return None
        finally:
            try:
                os.remove(flac)
            except OSError:
                pass
            if storage_path:
                try:
                    storage.delete_file(storage_path)
                except Exception:
                    pass

    return transcribe

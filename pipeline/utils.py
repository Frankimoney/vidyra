"""Pipeline errors and subprocess helpers (preserved from reference pipeline)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


class PipelineError(RuntimeError):
    """Raised when any underlying tool (FFmpeg/Piper/Whisper) fails."""


def run_cmd(command, input_text=None, timeout=None):
    """Run a command; raises PipelineError with the tool's stderr on failure.

    `timeout` is in seconds. Without it a wedged ffmpeg could hang the
    request that spawned it forever, so callers handling user uploads should
    always pass one.
    """
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True if input_text is not None else False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        tool = command[0] if command else "command"
        raise PipelineError(f"{tool} timed out after {timeout}s.")
    if result.returncode != 0:
        error = result.stderr
        if isinstance(error, bytes):
            error = error.decode("utf-8", errors="ignore")
        raise PipelineError(error)
    return result


# --------------------------------------------------------------------------
# Probing without ffprobe
# --------------------------------------------------------------------------
#
# ffprobe ships in the full FFmpeg builds but not in the trimmed ones (and not
# in the pip wheel we use for local runs). Requiring it would mean Vidyra
# refuses to start for lack of a binary it only needs to read three numbers, so
# when ffprobe is absent we read the same facts out of ffmpeg's own report.

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video:.*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"([\d.]+)\s*fps\b")


def _ffmpeg_report(path: str, timeout=None) -> str:
    """ffmpeg's description of a file. Exits non-zero by design (no output)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = result.stdout
    return out.decode("utf-8", errors="ignore") if isinstance(out, bytes) else (out or "")


def _fallback_duration(path: str, timeout=None) -> float:
    match = _DURATION_RE.search(_ffmpeg_report(path, timeout))
    if not match:
        raise PipelineError(f"Could not read the duration of {path}.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _fallback_dimensions(path: str) -> tuple:
    match = _VIDEO_RE.search(_ffmpeg_report(path))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _fallback_fps(path: str) -> float:
    report = _ffmpeg_report(path)
    video = _VIDEO_RE.search(report)
    if not video:
        return 0
    match = _FPS_RE.search(report[video.end():])
    if not match:
        return 0
    try:
        fps = float(match.group(1))
    except ValueError:
        return 0
    return round(fps, 2) if fps > 0 else 0


def get_duration(path, timeout=None):
    """Return media duration in seconds."""
    if shutil.which("ffprobe") is None:
        return _fallback_duration(path, timeout)
    result = run_cmd(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        timeout=timeout,
    )
    return float(result.stdout.strip())


def probe_aspect(path):
    """Return (width, height) of the first video stream, or (0, 0) on failure."""
    if shutil.which("ffprobe") is None:
        return _fallback_dimensions(path)
    try:
        result = run_cmd(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                path,
            ]
        )
        streams = (json.loads(result.stdout) or {}).get("streams") or []
        if not streams:
            return 0, 0
        return int(streams[0].get("width") or 0), int(streams[0].get("height") or 0)
    except (ValueError, OSError, PipelineError):
        return 0, 0


def get_fps(path):
    """Return the first video stream's frame rate, or 0 on failure."""
    if shutil.which("ffprobe") is None:
        return _fallback_fps(path)
    try:
        result = run_cmd(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate",
                "-of", "json",
                path,
            ]
        )
        streams = (json.loads(result.stdout) or {}).get("streams") or []
        if not streams:
            return 0
        num, _, den = (streams[0].get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den) if float(den) else 0.0
        return round(fps, 2) if fps > 0 else 0
    except (ValueError, OSError, PipelineError, ZeroDivisionError):
        return 0


def get_size_mb(path):
    """Return file size in megabytes."""
    return os.path.getsize(path) / (1024 * 1024)


def classify_media(path):
    """Return 'image', 'video', 'audio' or None based on extension."""
    lower = path.lower()
    from .config import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

    if lower.endswith(IMAGE_EXTENSIONS):
        return "image"
    if lower.endswith(VIDEO_EXTENSIONS):
        return "video"
    if lower.endswith(AUDIO_EXTENSIONS):
        return "audio"
    return None


# --- Uploaded audio: music track or voice sample? -------------------------
#
# Both arrive as media/{uid}/{folder}/{filename}, so the folder carries the
# purpose: "music-<id>" or "voice-<id>". Uploads made before the tag existed
# have a bare hex folder and are classified by length instead — a voice
# sample is a short clip, a music track is not.

MUSIC_UPLOAD_TAG = "music"
VOICE_UPLOAD_TAG = "voice"
VOICE_SAMPLE_MAX_SECONDS = 20.0


def upload_tag(storage_path) -> str:
    """Purpose tag ("music" / "voice") encoded in a media path, "" if untagged."""
    parts = [p for p in str(storage_path or "").replace("\\", "/").split("/") if p]
    # media/{uid}/{tag}-{id}/{filename}
    if len(parts) < 3:
        return ""
    folder = parts[2]
    for tag in (MUSIC_UPLOAD_TAG, VOICE_UPLOAD_TAG):
        if folder.startswith(tag + "-"):
            return tag
    return ""


def is_music_upload(storage_path, duration=None) -> bool:
    """True when an uploaded audio file is a music track, not a voice sample."""
    tag = upload_tag(storage_path)
    if tag:
        return tag == MUSIC_UPLOAD_TAG
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        # Untagged and unknown length: treat it as music, which is what the
        # music catalog has always assumed.
        return True
    return seconds > VOICE_SAMPLE_MAX_SECONDS
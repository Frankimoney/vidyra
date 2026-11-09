"""Face-recording compositing — overlays a user's webcam recording onto a
finished Vidyra video without re-running the generation pipeline.

Pure FFmpeg: the finished video stays underneath, the (optionally masked)
face recording is composited on top, and the narrator audio is preserved. If
the user opts in, their microphone audio is mixed in with sidechain ducking.


    generate -> finished video  --(optional)--> composite
"""

from __future__ import annotations

import json
import os

from .audio import MIX_LIMITER, voice_volume
from .utils import get_duration, run_cmd


def has_audio(path: str) -> bool:
    """True if the file has at least one audio stream."""
    try:
        result = run_cmd(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "json",
                path,
            ]
        )
        streams = (json.loads(result.stdout) or {}).get("streams") or []
        return bool(streams)
    except Exception:
        return False


def probe_dimensions(path: str) -> tuple[int, int]:
    """Return the first video stream's (width, height), or (0, 0)."""
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
        if streams:
            return int(streams[0].get("width") or 0), int(streams[0].get("height") or 0)
    except Exception:
        pass
    return 0, 0


def _even(n: int) -> int:
    return n + (n % 2)


def _rounded_alpha_expr(w: int, h: int, radius: int) -> str:
    """geq() expression yielding 255 inside the rounded-rectangle, else 0.

    This ffmpeg build's geq only supports X/Y coordinates plus gt() and plain
    arithmetic (no hypot/lte/W/H), so the shape is expressed with squared
    distances and literal geometry.
    """
    r = radius
    r2 = r * r
    wmr1 = w - r - 1  # rightmost in-shape X
    hmr1 = h - r - 1  # bottommost in-shape Y
    # Straight center bands (vertical OR horizontal).
    rect = f"(gt(X,{r})*gt({wmr1},X)+gt(Y,{r})*gt({hmr1},Y))"
    # Four rounded corners: inside the corner arc within its quadrant.
    tl = f"gt({r2},(X-{r})*(X-{r})+(Y-{r})*(Y-{r}))*gt({r},X)*gt({r},Y)"
    tr = (
        f"gt({r2},(X-{wmr1})*(X-{wmr1})+(Y-{r})*(Y-{r}))"
        f"*gt(X,{wmr1})*gt({r},Y)"
    )
    bl = (
        f"gt({r2},(X-{r})*(X-{r})+(Y-{hmr1})*(Y-{hmr1}))"
        f"*gt({r},X)*gt(Y,{hmr1})"
    )
    br = (
        f"gt({r2},(X-{wmr1})*(X-{wmr1})+(Y-{hmr1})*(Y-{hmr1}))"
        f"*gt(X,{wmr1})*gt(Y,{hmr1})"
    )
    return f"255*gt(({rect}+{tl}+{tr}+{bl}+{br}),0)"


def _make_mask(mask_path, w: int, h: int, shape: str):
    """Write a one-frame black-and-white shape mask.
    The shape is carried in the LUMA so it survives a later format=gray step.
    geq is run on an RGB input plane (the yuv420p path yields mangled chroma in
    this ffmpeg build), then downsampled to real grayscale.
    """
    if shape == "circle":
        cx = w / 2
        cy = h / 2
        r2 = int((min(w, h) / 2) ** 2)
        expr = f"255*gt({r2},(X-{cx})*(X-{cx})+(Y-{cy})*(Y-{cy}))"
    elif shape == "rounded":
        radius = max(2, int(min(w, h) / 6))
        expr = _rounded_alpha_expr(w, h, radius)
    else:
        expr = "255"
    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=black:s={w}x{h}:d=1:r=1",
            "-vf",
            "format=rgb24,"
            f"geq=r='{expr}':g='{expr}':b='{expr}'",
            "-pix_fmt", "gray",
            "-frames:v", "1",
            "-update", "1",
            mask_path,
        ]
    )


def compose_face_overlay(
    main_video: str,
    recording: str,
    overlay: dict | None,
    include_audio: bool,
    temp_dir: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> str:
    """Composite a face recording onto a finished video. Returns output path.

    overlay keys: shape (circle|rounded|rect), scale (percent of main video
    height), x/y (percent offsets of the overlay's top-left corner), plus
    optional recordingWidth/recordingHeight hints from the browser.
    """
    overlay = overlay or {}
    shape = str(overlay.get("shape") or "circle").lower()
    if shape not in ("circle", "rounded", "rect"):
        shape = "circle"

    # Face recording aspect ratio (browser hint first, file is authoritative).
    rec_w, rec_h = probe_dimensions(recording)
    if rec_w <= 0 or rec_h <= 0:
        # Never let a malformed hint (`"abc"`, a missing key) take the render
        # down mid-job; a zero hint simply means "probe the file" -> RuntimeError
        # below, which is the clean, explainable failure.
        try:
            rec_w = int(overlay["recordingWidth"])
            rec_h = int(overlay["recordingHeight"])
        except (TypeError, ValueError, KeyError):
            rec_w = rec_h = 0
    if rec_w <= 0 or rec_h <= 0:
        raise RuntimeError("Could not read the face recording dimensions.")

    try:
        scale_pct = float(overlay.get("scale") or 18)
    except (TypeError, ValueError):
        scale_pct = 18
    scale_pct = max(10, min(40, scale_pct))
    face_w = _even(int(height * scale_pct / 100))
    face_h = _even(int(face_w * rec_h / rec_w))
    if face_h <= 0:
        face_h = _even(int(face_w * 3 / 4))

    x = int(width * float(overlay.get("x") or 0) / 100)
    y = int(height * float(overlay.get("y") or 0) / 100)
    x = max(0, min(x, width - face_w))
    y = max(0, min(y, height - face_h))

    main_duration = 0.0
    recording_duration = 0.0
    try:
        main_duration = get_duration(main_video)
        recording_duration = get_duration(recording)
    except Exception:
        pass
    # Mix the narrator's voice only when both tracks are long enough for the
    # ducking chain to be meaningful; degenerate inputs just keep original audio.
    use_voice = (
        bool(include_audio)
        and has_audio(recording)
        and min(main_duration, recording_duration) >= 2.0
        and main_duration > 0
        and recording_duration > 0
    )

    output_path = os.path.abspath(os.path.join(temp_dir, "final_face.mp4"))
    mask_path = os.path.abspath(os.path.join(temp_dir, "face_mask.png"))

    command = ["ffmpeg", "-y", "-i", main_video, "-i", recording]

    # Video: mask the face for circle/rounded shapes by baking the shape mask
    # into the overlay's alpha channel (this ffmpeg's overlay has no 3rd input).
    if shape in ("circle", "rounded"):
        _make_mask(mask_path, face_w, face_h, shape)
        mask_duration = recording_duration if recording_duration > 0 else 999999
        command += ["-loop", "1", "-t", str(mask_duration), "-i", mask_path]
        video_graph = (
            f"[2:v]scale={face_w}:{face_h},format=gray,setsar=1[maskg];"
            f"[1:v]scale={face_w}:{face_h},format=rgba,setsar=1[base];"
            f"[base][maskg]alphamerge[face];"
            f"[0:v][face]overlay={x}:{y}:eof_action=pass[v]"
        )
    else:
        video_graph = (
            f"[1:v]scale={face_w}:{face_h},setsar=1[face];"
            f"[0:v][face]overlay={x}:{y}:eof_action=pass[v]"
        )

    maps = ["-map", "[v]"]
    if use_voice:
        # Same shape as the narration+music mix: level the voice with a static
        # measured gain, duck the original audio under it, and only *limit* the
        # result. Ending in `loudnorm` here was not reproducible run-to-run, and
        # it dragged the ducked original audio down during speech gaps.
        audio_graph = (
            "[0:a]highpass=f=40,lowpass=f=12000[mainx];"
            "[1:a]highpass=f=70,lowpass=f=14000,"
            "acompressor=threshold=0.08:ratio=2.5:attack=15:release=180,"
            f"volume={voice_volume(recording):.5f}[voice];"
            "[mainx][voice]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:"
            f"dropout_transition=2:normalize=0,{MIX_LIMITER}[aout]"
        )
        maps += ["-map", "[aout]"]
    else:
        audio_graph = None
        maps += ["-map", "0:a"]

    filter_complex = (
        video_graph + (";" + audio_graph if audio_graph else "")
    )
    command += ["-filter_complex", filter_complex]

    if use_voice:
        command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        command += ["-c:a", "copy"]

    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-shortest",
    ]
    command += list(maps)
    command.append(output_path)

    run_cmd(command)
    return output_path
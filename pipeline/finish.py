"""Timeline finishing pass — text, overlays and extra audio on a rendered cut.

The scene engine owns narration, captions and the main visuals. Everything a
creator adds *on top* of the cut (titles, lower thirds, logo/PiP overlays,
extra music/SFX) is applied here in one ffmpeg pass, so the scene pipeline
stays unchanged and the timeline document is the single source of truth.

All times are seconds on the final cut. Text/overlay geometry is normalised
(0-1), which keeps a saved timeline valid across output ratios.
"""

from __future__ import annotations

import os
import re
import shutil

from .hook import _fontfile, _textfile
from .utils import PipelineError, classify_media, run_cmd

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clamp(value, lo, hi, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _color(value, default="white") -> str:
    value = (value or "").strip()
    if _HEX_RE.match(value):
        return "0x" + value[1:].upper()
    return value or default


def _between(start: float, end: float) -> str:
    return f"enable='between(t,{start:.3f},{end:.3f})'"


def apply_timeline(video: str, output_path: str, settings) -> str:
    """Burn timeline text/overlays and mix extra audio into ``video``.

    Returns ``output_path``. When the timeline is empty the input is copied so
    callers can use the same path unconditionally.
    """
    texts = [t for t in (getattr(settings, "timeline_texts", None) or []) if isinstance(t, dict)]
    overlays = [o for o in (getattr(settings, "timeline_overlays", None) or []) if isinstance(o, dict)]
    audios = [a for a in (getattr(settings, "timeline_audio", None) or []) if isinstance(a, dict)]

    if not texts and not overlays and not audios:
        shutil.copy2(video, output_path)
        return output_path

    inputs = ["-i", video]
    filters: list[str] = []
    input_index = 1
    label_id = 0
    current = "0:v"

    for overlay in overlays:
        path = str(overlay.get("path") or "")
        if not path or not os.path.exists(path):
            continue
        kind = str(overlay.get("kind") or classify_media(path) or "image")
        if kind == "video":
            inputs += ["-stream_loop", "-1", "-i", path]
        else:
            inputs += ["-loop", "1", "-i", path]
        width_pct = _clamp(overlay.get("width", 0.3), 0.03, 1.5, 0.3)
        width = max(2, int(settings.width * width_pct))
        opacity = _clamp(overlay.get("opacity", 1.0), 0.0, 1.0, 1.0)
        x = _clamp(overlay.get("x", 0.5), 0.0, 1.0, 0.5)
        y = _clamp(overlay.get("y", 0.5), 0.0, 1.0, 0.5)
        start = _clamp(overlay.get("start", 0), 0, 100000, 0)
        end = max(start + 0.1, _clamp(overlay.get("end", start + 3), 0, 100000, start + 3))
        filters.append(
            f"[{input_index}:v]scale={width}:-2,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f}[ov{label_id}]"
        )
        filters.append(
            f"[{current}][ov{label_id}]overlay="
            f"x=(W-w)*{x:.4f}:y=(H-h)*{y:.4f}:{_between(start, end)}[v{label_id}]"
        )
        current = f"v{label_id}"
        input_index += 1
        label_id += 1

    for i, text in enumerate(texts):
        body = str(text.get("text") or "").strip()
        if not body:
            continue
        start = _clamp(text.get("start", 0), 0, 100000, 0)
        end = max(start + 0.1, _clamp(text.get("end", start + 3), 0, 100000, start + 3))
        x = _clamp(text.get("x", 0.5), 0.0, 1.0, 0.5)
        y = _clamp(text.get("y", 0.78), 0.0, 1.0, 0.78)
        # Sizes are authored against a 1080x1920 canvas, then scaled to output.
        size = int(_clamp(text.get("size", 54), 8, 240, 54) * settings.height / 1920)
        size = max(8, size)
        colour = _color(text.get("color", "#ffffff"))
        text_file = _textfile(settings.temp_dir, f"timeline_text_{i}.txt", body)
        font_arg = f"fontfile={_fontfile(settings.font_file)}" if settings.font_file else ""
        box = ""
        if text.get("background", True):
            box = ":box=1:boxcolor=black@0.55:boxborderw=12"
        alpha = ""
        if str(text.get("animation") or "none") == "fade":
            fade = max(0.05, min(0.3, (end - start) / 3.0))
            alpha = (
                f":alpha='if(lt(t,{start + fade:.3f}),(t-{start:.3f})/{fade:.3f},"
                f"if(gt(t,{end - fade:.3f}),({end:.3f}-t)/{fade:.3f},1))'"
            )
        filters.append(
            f"[{current}]drawtext={font_arg}textfile='{text_file}':expansion=none:"
            f"fontcolor={colour}:fontsize={size}:"
            f"x=(w-text_w)*{x:.4f}:y=(h-text_h)*{y:.4f}{box}{alpha}:{_between(start, end)}[v{label_id}]"
        )
        current = f"v{label_id}"
        label_id += 1

    audio_labels = ["[0:a]"]
    for j, audio in enumerate(audios):
        path = str(audio.get("path") or "")
        if not path or not os.path.exists(path):
            continue
        start = _clamp(audio.get("start", 0), 0, 100000, 0)
        end = max(start + 0.1, _clamp(audio.get("end", start + 3), 0, 100000, start + 3))
        duration = end - start
        volume = _clamp(audio.get("volume", 1.0), 0.0, 4.0, 1.0)
        fade_in = min(_clamp(audio.get("fadeIn", 0), 0, 10, 0), duration / 2)
        fade_out = min(_clamp(audio.get("fadeOut", 0), 0, 10, 0), duration / 2)
        inputs += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", path]
        chain = [f"volume={volume:.3f}"]
        if fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        if fade_out > 0:
            chain.append(f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}")
        delay = int(round(start * 1000))
        chain.append(f"adelay={delay}:all=1")
        filters.append(f"[{input_index}:a]{','.join(chain)}[a{j}]")
        audio_labels.append(f"[a{j}]")
        input_index += 1

    if len(audio_labels) > 1:
        filters.append(
            "".join(audio_labels)
            + f"amix=inputs={len(audio_labels)}:duration=first:dropout_transition=0[outa]"
        )
        audio_map = "[outa]"
    else:
        audio_map = "0:a"

    video_map = f"[{current}]" if current != "0:v" else "0:v"

    command = ["ffmpeg", "-y"] + inputs
    if filters:
        command += ["-filter_complex", ";".join(filters)]
    command += [
        "-map", video_map,
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "ultrafast" if getattr(settings, "preview", False) else "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-shortest",
        output_path,
    ]
    try:
        run_cmd(command)
    except PipelineError as exc:
        raise PipelineError(f"Timeline finishing pass failed: {exc}") from exc
    return output_path

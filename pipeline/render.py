"""Final MP4 rendering — reference pipeline logic."""

from __future__ import annotations

import os

from .hook import _fontfile, _textfile
from .utils import PipelineError, run_cmd


def concat_visuals(hook_video, main_visual, settings):
    """Concatenate hook + main visual into one silent video.

    The hook and the visual track are both encoded with identical settings
    (libx264, CRF 20, yuv420p, same fps and resolution — see pipeline.hook and
    pipeline.transitions), so the two can be remuxed without touching a single
    pixel. That skips a full-length re-encode of the whole video. Falls back to
    re-encoding if the copy is rejected, e.g. on non-monotonic timestamps.
    """
    combined = os.path.join(settings.temp_dir, "visual_with_hook.mp4")

    concat_file = os.path.join(settings.temp_dir, "hook_concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for path in (hook_video, main_visual):
            f.write(f"file '{str(path).replace(chr(92), '/')}'\n")

    try:
        run_cmd(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-an",
                "-c", "copy",
                "-fflags", "+genpts",
                combined,
            ]
        )
        return combined
    except PipelineError:
        pass

    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.fps),
            combined,
        ]
    )
    return combined


def badge_filter(page_name: str, settings) -> str | None:
    """drawtext filter for the top-right @handle corner badge."""
    page = (page_name or "").strip()
    if not page:
        return None
    handle = f"@{page.lstrip('@')}"[:40]
    # Read the handle from a file for the same reason the hook card does: a "%"
    # or a quote in a page name would otherwise abort the whole render. Without
    # a configured font this still works, falling back to ffmpeg's default.
    handle_file = _textfile(settings.temp_dir, "badge.txt", handle)
    font_arg = f"fontfile={_fontfile(settings.font_file)}:" if settings.font_file else ""
    return (
        f"drawtext={font_arg}"
        f"textfile='{handle_file}':expansion=none:"
        "fontcolor=white:fontsize=34:"
        "x=w-text_w-26:y=26:"
        "box=1:boxcolor=black@0.45:boxborderw=12"
    )


def render_final(
    final_visual,
    final_audio,
    caption_file,
    output_path,
    settings,
):
    """Mux video + audio, burn captions + page badge, write final MP4."""
    command = ["ffmpeg", "-y", "-i", final_visual, "-i", final_audio]

    vf_parts = []
    brightness = float(getattr(settings, "color_brightness", 0.0) or 0.0)
    contrast = float(getattr(settings, "color_contrast", 1.0) or 1.0)
    saturation = float(getattr(settings, "color_saturation", 1.0) or 1.0)
    if abs(brightness) > 0.001 or abs(contrast - 1.0) > 0.001 or abs(saturation - 1.0) > 0.001:
        vf_parts.append(
            f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}"
        )
    if caption_file:
        caption_path = (
            caption_file.replace("\\", "/").replace(":", "\\:")
        )
        vf_parts.append(f"ass='{caption_path}'")

    badge = badge_filter(settings.page_name, settings) if settings.show_page_name else None
    if badge:
        vf_parts.append(badge)

    if vf_parts:
        command.extend(["-vf", ",".join(vf_parts)])

    preset = "ultrafast" if getattr(settings, "preview", False) else "veryfast"
    if getattr(settings, "normalize_audio", False):
        command.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    command.extend(
        [
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-shortest",
            output_path,
        ]
    )

    run_cmd(command)
    return output_path
"""Hook / title card generation — reference pipeline logic."""

from __future__ import annotations

import os

from .utils import run_cmd


def build_hook(hook_style, hook_text, hook_subtext):
    """Build the hook main/sub text for the selected style."""
    hook_text = (hook_text or "").strip()
    hook_subtext = (hook_subtext or "").strip()

    if hook_style == "Breaking News":
        return hook_text, hook_subtext

    if hook_style == "Question":
        return "IS THIS THE NEXT BIG THING?", hook_text

    if hook_style == "Bold Statement":
        return f"{hook_text} IS READY.", hook_subtext

    if hook_style == "Debate":
        return f"LET'S TALK ABOUT {hook_text}", hook_subtext

    if hook_style == "Game Day":
        return "GAMECOCKS FANS, WATCH THIS", hook_text

    return hook_text, hook_subtext


def _nodrive(path):
    """Return a filter-argument-safe path with no drive-letter colon.

    A colon inside a ``-vf`` option value terminates it, which breaks
    ``fontfile=`` and ``textfile=`` on Windows. Prefer a relative path (which the
    worker can always use, since it runs from the project root) and otherwise
    escape the colons, the usual ffmpeg idiom for drive letters.
    """
    if os.name == "nt":
        try:
            rel = os.path.relpath(path, os.getcwd()).replace("\\", "/")
            if ":" not in rel:
                return rel
        except ValueError:
            pass
        return path.replace("\\", "/").replace(":", "\\:")
    return str(path).replace("\\", "/")


def _fontfile(setting_path):
    """Return a drawtext-safe font path (no drive-letter colon on Windows)."""
    return _nodrive(setting_path)


def _textfile(directory, name, text):
    """Write overlay text to a file and return its filter-argument-safe path.

    drawtext reads the string from disk instead of the filter graph, so nothing
    in it can be misread as filter syntax. Escaping by hand is not enough: ffmpeg
    tracks quoting state across the whole ``-vf`` string, so an apostrophe in one
    drawtext and a colon in the next one combine to abort the render.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write((text or "").replace("\r", " ").replace("\n", " "))
    return _nodrive(path)


def create_hook(hook_text, hook_subtext, duration, settings):
    """Render a title card (cover image or black) with bold centered text overlay."""
    output = os.path.join(settings.temp_dir, "hook.mp4")

    if not settings.font_file:
        raise RuntimeError(
            "No font file found for hook text. Install DejaVu fonts "
            "or set VIDYRA_FONT_FILE."
        )

    main_file = _textfile(settings.temp_dir, "hook_main.txt", hook_text)
    sub_file = _textfile(settings.temp_dir, "hook_sub.txt", hook_subtext)
    font = _fontfile(settings.font_file)
    # expansion=none: the overlay is literal copy, so "%" neither errors out nor
    # gets substituted (a "%Y" in a headline would otherwise become a year).
    draw = (
        f"drawtext=fontfile={font}:"
        f"textfile='{main_file}':expansion=none:fontcolor=white:fontsize=72:"
        "x=(w-text_w)/2:y=(h-text_h)/2-70:"
        "box=1:boxcolor=black@0.55:boxborderw=22,"
        f"drawtext=fontfile={font}:"
        f"textfile='{sub_file}':expansion=none:fontcolor=white:fontsize=42:"
        "x=(w-text_w)/2:y=(h-text_h)/2+35:"
        "box=1:boxcolor=black@0.55:boxborderw=16"
    )

    has_cover = bool(settings.hook_cover) and os.path.isfile(settings.hook_cover)
    if has_cover:
        # Cover image background: fill the 9:16 frame, dim it so the
        # hook text stays readable, then draw the text on top.
        base_vf = (
            f"scale={settings.width}:{settings.height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={settings.width}:{settings.height},"
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.38:t=fill"
        )
        command = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", settings.hook_cover,
            "-vf", f"{base_vf},{draw}",
            "-t", str(duration),
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.fps),
            output,
        ]
    else:
        command = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i",
            f"color=c=black:s={settings.width}x{settings.height}:"
            f"d={duration}:r={settings.fps}",
            "-vf", draw,
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.fps),
            output,
        ]

    run_cmd(command)
    return output
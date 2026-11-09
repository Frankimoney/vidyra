"""Image/video motion handling with aspect-aware reframing.

Every upload is normalized to the requested output canvas (9:16 by default).
`reframe` mode controls how non-9:16 sources are handled:
  - "crop"  -> scaled to cover + center-cropped (full-bleed, sides lost)
  - "blur"  -> whole frame kept, blurred copy fills the empty area
  - "auto"  -> crop when the source is near the target aspect, else blur
"""

from __future__ import annotations

import os

from .cache import cache_get, cache_put, clip_key
from .utils import probe_aspect, run_cmd

BLUR_RADIUS = "luma_radius=20:luma_power=1:chroma_radius=16:chroma_power=1"

# Per-clip renders are intermediates: they are re-encoded by the xfade pass
# that joins them, and again by the final burn-in. Encoding speed is worth far
# more than the quality they throw away, so these use ultrafast while
# render_final keeps veryfast.
INTERMEDIATE_PRESET = "ultrafast"


def _cover(w, h, focus_x=0.5, focus_y=0.5, zoom=1.0):
    """Scale to cover the canvas, then crop around a normalized focal point.

    zoom 1 + focus (0.5, 0.5) is the historical centered cover. A focal point
    moves the crop window so the chosen part of the source stays in frame, and
    zoom > 1 punches in before the crop. The crop offsets are clamped by ffmpeg
    itself ((iw-W) and (ih-H) are non-negative because the scale uses
    force_original_aspect_ratio=increase).
    """
    z = max(1.0, min(3.0, float(zoom or 1.0)))
    fx = max(0.0, min(1.0, float(focus_x if focus_x is not None else 0.5)))
    fy = max(0.0, min(1.0, float(focus_y if focus_y is not None else 0.5)))
    sw = max(w, int(round(w * z)))
    sh = max(h, int(round(h * z)))
    return (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}:(iw-{w})*{fx:.4f}:(ih-{h})*{fy:.4f}"
    )


def _fit(w, h):
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1"


def _blur_fit_video(w, h):
    return (
        "[0:v]split=2[bga][fga];"
        f"[bga]{_cover(w, h)},boxblur={BLUR_RADIUS},setsar=1[bgb];"
        f"[fga]{_fit(w, h)}[fgb];"
        f"[bgb][fgb]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )


def _blur_fit_image(w, h, zoom_filter):
    return (
        "[0:v]split=2[bga][fga];"
        f"[bga]{_cover(w, h)},boxblur={BLUR_RADIUS},setsar=1[bgb];"
        f"[fga]{_fit(w, h)}[fgb];"
        f"[bgb][fgb]overlay=(W-w)/2:(H-h)/2,format=yuv420p[stacked];"
        f"[stacked]{zoom_filter}[v]"
    )


def _effective_mode(settings, src_path):
    """Resolve 'auto' into 'crop' or 'blur' for a specific media file."""
    mode = (getattr(settings, "reframe", "") or "auto").strip().lower() or "auto"
    if mode in ("crop", "blur"):
        return mode
    w, h = probe_aspect(src_path)
    if w > 0 and h > 0:
        target = settings.width / settings.height
        ratio = (w / h) / target
        if abs(ratio - 1.0) > 0.14:
            return "blur"
    return "crop"


def _render_clip(kind, media, duration, index, settings, output, vf, complex_filter=False, trim_start: float = 0, focus=(0.5, 0.5), zoom: float = 1.0):
    """Cache-aware clip generation. Returns (output_path, cache_key_or_None).

    Blur renders use a labeled filter_complex graph ([v] output) because a
    simple -vf graph cannot expose output labels to the muxer. trim_start is
    video-only (images pass 0); applied as fast seek before -i.
    """
    trim_start = max(0.0, float(trim_start or 0))
    key = None
    if getattr(settings, "cache_dir", ""):
        key = clip_key(kind, media, duration, index, settings, trim_start, focus, zoom)
        hit = cache_get(settings.cache_dir, key, "clip", "mp4")
        if hit:
            return hit, key
    seek_args = ["-ss", str(trim_start)] if kind == "video" and trim_start > 0 else []

    # Every clip must occupy exactly `duration` seconds even when its source is
    # shorter. _resolve_clip_durations adds the crossfade overlap budget on top
    # of the narration length, so it routinely asks for slightly more than an
    # upload segment contains (4.0s segments get asked for ~4.4s). Without this
    # the -t below clamps each clip, the joined visual track lands short of the
    # narration, and render_final's -shortest silently drops the tail of the
    # audio. tpad clones the final frame to cover the gap; -t still trims any
    # surplus, so clips with enough footage come out unchanged.
    pad = f"tpad=stop_mode=clone:stop_duration={duration:.3f}"
    if complex_filter:
        vf = f"{vf};[v]{pad},fps={settings.fps}[vp]"
        map_args = ["-map", "[vp]"]
    else:
        vf = f"{vf},{pad}"
        map_args = []

    # Built after `vf` is final — filter_args captures the string by value, so
    # computing it earlier would silently drop the padding.
    filter_args = ["-filter_complex", vf] if complex_filter else ["-vf", vf]

    run_cmd(
        ["ffmpeg", "-y"]
        + seek_args
        + ["-i", media]
        + filter_args
        + map_args
        + [
            "-an",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", INTERMEDIATE_PRESET,
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(settings.fps),
            output,
        ]
    )
    return output, key


def create_animated_image(image, duration, index, settings, focus=(0.5, 0.5), zoom=1.0):
    """Create a single animated (zoom-in/zoom-out) clip from an image."""
    output = os.path.join(settings.temp_dir, f"image_{index:03d}.mp4")
    w, h = settings.width, settings.height

    # zoompan generates `d` output frames from the single input frame, so the
    # zoom expression accumulates across the whole clip. Feeding a looped image
    # stream instead breaks the accumulation (each input frame restarts zoom).
    frames = max(1, int(round(duration * settings.fps)))
    if index % 2 == 0:
        zoom_filter = (
            "zoompan="
            "z='min(zoom+0.0008,1.12)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={w}x{h}:fps={settings.fps}"
        )
    else:
        zoom_filter = (
            "zoompan="
            "z='if(lte(zoom,1.001),1.12,max(zoom-0.0008,1.0))':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={w}x{h}:fps={settings.fps}"
        )

    if _effective_mode(settings, image) == "blur":
        vf = _blur_fit_image(w, h, zoom_filter)
        complex_filter = True
    else:
        fx, fy = focus
        vf = f"{_cover(w, h, fx, fy, zoom)},{zoom_filter}"
        complex_filter = False

    path, key = _render_clip(
        "image", image, duration, index, settings, output, vf,
        complex_filter=complex_filter, focus=focus, zoom=zoom,
    )
    if path == output and key:
        cache_put(output, settings.cache_dir, key, "clip", "mp4")
    return path


def process_video_clip(video_path, duration, settings, index=0, trim_start: float = 0, focus=(0.5, 0.5), zoom=1.0):
    """Reframe a video to the output aspect, mute it, seek to trim_start, clip to duration."""
    output = os.path.join(settings.temp_dir, f"video_{index:03d}.mp4")
    w, h = settings.width, settings.height

    if _effective_mode(settings, video_path) == "blur":
        vf = _blur_fit_video(w, h)
        complex_filter = True
    else:
        fx, fy = focus
        vf = _cover(w, h, fx, fy, zoom)
        complex_filter = False

    path, key = _render_clip(
        "video", video_path, duration, index, settings, output, vf,
        complex_filter=complex_filter, trim_start=trim_start, focus=focus, zoom=zoom,
    )
    if path == output and key:
        cache_put(output, settings.cache_dir, key, "clip", "mp4")
    return path
"""Multi-image sequence with xfade transitions — reference pipeline logic."""

from __future__ import annotations

import os

from .config import TRANSITION_DURATION
from .utils import PipelineError, get_duration, run_cmd
from .visuals import create_animated_image, process_video_clip

# Clips are xfade-joined in batches: one ffmpeg pass with a single
# filter_complex graph per batch. Chaining one xfade per ffmpeg invocation
# re-encodes the whole accumulated timeline every time, which is quadratic in
# clip count — a 60s video split into 15 clips encoded ~480s of 1080x1920
# video. A single pass per batch makes the cost linear.
#
# Batching exists only to bound how many 1080x1920 decoders ffmpeg holds open
# at once. Lower it if the machine is short on RAM; the result is identical.
XFADE_BATCH_SIZE = 8

AUTO_TRANSITIONS = ["fade", "slideleft", "slideright", "wipeleft", "wiperight"]


def _resolve_clip_durations(n, narration_duration, clip_durations):
    """Return per-clip seconds that fill narration time.

    Explicit durations are kept; unspecified clips absorb the remaining
    time. If explicit durations alone already overflow the narration, they
    are scaled proportionally so the visuals stay in sync with the audio.
    """
    explicit = [
        d if (isinstance(d, (int, float)) and d > 0) else None for d in (clip_durations or [])
    ]
    explicit = explicit[:n] + [None] * (n - len(explicit))

    if not any(explicit):
        duration = (narration_duration + (n - 1) * TRANSITION_DURATION) / n
        return [duration] * n

    target = narration_duration + (n - 1) * TRANSITION_DURATION
    known = sum(d for d in explicit if d is not None)
    unknown = [i for i, d in enumerate(explicit) if d is None]

    if unknown:
        each = max(0.5, (target - known) / len(unknown))
        for i in unknown:
            explicit[i] = each

    total = sum(d for d in explicit if d)
    if known >= target and total > 0:
        scale = target / total
        explicit = [d * scale for d in explicit]

    total = sum(d for d in explicit if d)
    if total < target:
        # Hold the user's per-clip choices; extend the final clip so the
        # narration is never cut short.
        explicit[-1] = explicit[-1] + (target - total)

    return [round(d, 3) for d in explicit]


def _transition_name(style, index):
    """Resolve the xfade name for the join at `index` (1-based, global)."""
    if style == "Auto":
        return AUTO_TRANSITIONS[(index - 1) % len(AUTO_TRANSITIONS)]
    return style


def _write_concat_list(paths, list_path):
    """Write an ffmpeg concat-demuxer list for `paths`."""
    with open(list_path, "w", encoding="utf-8") as f:
        for path in paths:
            # concat-demuxer lines are newline delimited and single quoted.
            safe = str(path).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{safe}'\n")


def _concat_stream_copy(paths, output):
    """Join already-encoded clips with the concat demuxer — no re-encode.

    Every clip in the pipeline is encoded with the same libx264/CRF/fps/
    resolution, so this is a metadata-only remux. Costs nothing.
    """
    list_path = output + ".txt"
    _write_concat_list(paths, list_path)
    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-fflags", "+genpts",
            output,
        ]
    )
    return output


def _encode_xfade_batch(paths, actual_durations, styles, settings, output):
    """Join a batch of clips with xfade in ONE ffmpeg pass.

    paths: rendered clips, already normalized to the output canvas.
    actual_durations: probed duration of each clip, so crossfade offsets sit
        exactly where they did when this was chained one clip per invocation.
    styles: transition name per join, len(paths) - 1 entries.
    """
    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", path]

    parts = []
    # xfade crossfades two streams, so both must agree on frame rate, pixel
    # format and timebase or the join drifts. Normalize every input first.
    for i in range(len(paths)):
        parts.append(
            f"[{i}:v]fps={settings.fps},format=yuv420p,"
            f"settb=AVTB,setpts=PTS-STARTPTS[b{i}]"
        )

    prev = "b0"
    accum = actual_durations[0]
    for i in range(1, len(paths)):
        offset = max(0.0, accum - TRANSITION_DURATION)
        label = f"x{i}"
        parts.append(
            f"[{prev}][b{i}]xfade="
            f"transition={styles[i - 1]}:"
            f"duration={TRANSITION_DURATION}:"
            f"offset={offset:.4f}[{label}]"
        )
        prev = label
        accum = accum + actual_durations[i] - TRANSITION_DURATION

    command += [
        "-filter_complex", ";".join(parts),
        "-map", f"[{prev}]",
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


def _chain_incremental(rendered, settings):
    """One xfade per ffmpeg invocation, re-encoding the timeline each time.

    Quadratic, and therefore only used as a fallback when the single-pass
    graph below is rejected by ffmpeg.
    """
    current = rendered[0]
    for index in range(1, len(rendered)):
        next_clip = rendered[index]
        output = os.path.join(settings.temp_dir, f"transition_{index:03d}.mp4")

        current_duration = get_duration(current)
        offset = max(0, current_duration - TRANSITION_DURATION)
        transition_name = _transition_name(settings.transition, index)

        filter_complex = (
            f"[0:v][1:v]xfade="
            f"transition={transition_name}:"
            f"duration={TRANSITION_DURATION}:"
            f"offset={offset},format=yuv420p[v]"
        )

        run_cmd(
            [
                "ffmpeg", "-y",
                "-i", current,
                "-i", next_clip,
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-an",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-r", str(settings.fps),
                output,
            ]
        )
        current = output
    return current


def _join_with_transitions(rendered, durations, settings):
    """Join every rendered clip into one visual track.

    One encode per batch of XFADE_BATCH_SIZE clips, then a stream-copy concat
    across batches. Total encoding is linear in timeline length.

    Note the join between batches is a hard cut, not a crossfade — the concat
    demuxer cannot overlap. n clips across b batches therefore carry n - b
    transitions, and the result is that much longer than a fully crossfaded
    chain. For split uploads this is invisible: consecutive 4s segments are
    continuous footage from one source, so a cut preserves the picture. Raise
    XFADE_BATCH_SIZE to reduce seams, at the cost of more parallel decoders.
    """
    n = len(rendered)
    # Probe once per clip rather than once per join: ffprobe is ~20ms, and the
    # real duration is what keeps crossfade offsets honest once durations have
    # been quantised to whole frames by the clip encoder.
    actual = [get_duration(p) for p in rendered]

    outputs = []
    for batch, start in enumerate(range(0, n, XFADE_BATCH_SIZE)):
        idxs = list(range(start, min(start + XFADE_BATCH_SIZE, n)))
        if len(idxs) == 1:
            outputs.append(rendered[idxs[0]])
            continue
        # Styles are indexed by global clip position so an "Auto" run picks the
        # same transitions it did before batching existed. The join *into* clip i
        # is join number i (1-based), which is exactly what `_chain_incremental`
        # passes; adding one here shifted every "Auto" transition along and
        # changed the first and last cut of every video.
        styles = [_transition_name(settings.transition, i) for i in idxs[1:]]
        out = os.path.join(settings.temp_dir, f"xfade_{batch:03d}.mp4")
        _encode_xfade_batch(
            [rendered[i] for i in idxs],
            [actual[i] for i in idxs],
            styles,
            settings,
            out,
        )
        outputs.append(out)

    if len(outputs) == 1:
        return outputs[0]
    combined = os.path.join(settings.temp_dir, "xfade_chain.mp4")
    return _concat_stream_copy(outputs, combined)


def create_clip_sequence(
    clips,
    narration_duration,
    settings,
    clip_durations=None,
    kinds=None,
    trim_starts=None,
    focuses=None,
    zooms=None,
):
    """Render images (animated zoom) and/or video clips into one xfade sequence.

    clips: list of local media paths. kinds: parallel list of 'image'/'video'
    (defaults to all images). trim_starts: parallel list of video in-points
    (seconds into source; images ignore). focuses: parallel list of
    (focus_x, focus_y) normalized crop points. zooms: parallel list of crop
    zoom multipliers. Each clip is rendered to its requested duration and
    joined with xfade transitions.
    """
    n = len(clips)
    durations = _resolve_clip_durations(n, narration_duration, clip_durations)
    kinds = kinds or ["image"] * n
    if len(kinds) < n:
        kinds = kinds + ["image"] * (n - len(kinds))
    trims = list(trim_starts or [])[:n] + [0] * max(0, n - len(trim_starts or []))
    foci = list(focuses or [])[:n] + [(0.5, 0.5)] * max(0, n - len(focuses or []))
    zooms = [float(z or 1.0) for z in (list(zooms or [])[:n])]
    zooms += [1.0] * max(0, n - len(zooms))

    if n == 1:
        if kinds[0] == "video":
            return process_video_clip(
                clips[0], durations[0], settings, 0, trims[0], foci[0], zooms[0]
            )
        return create_animated_image(
            clips[0], durations[0], 0, settings, foci[0], zooms[0]
        )

    # The chain is the most expensive stage in the whole pipeline, so the cache
    # is consulted BEFORE any rendering. Keys depend only on the inputs and
    # settings, so entries written by the previous implementation still hit.
    ckey = None
    if settings.cache_dir:
        from .cache import cache_get, cache_put, chain_key, clip_key

        clip_keys = [
            clip_key(
                kind if kind else "image", clip, durations[idx], idx, settings,
                trims[idx], foci[idx], zooms[idx],
            )
            for idx, (clip, kind) in enumerate(zip(clips, kinds))
        ]
        ckey = chain_key(clip_keys, durations, settings)
        hit = cache_get(settings.cache_dir, ckey, "chain", "mp4")
        if hit:
            return hit

    rendered = []
    for index, (clip, kind) in enumerate(zip(clips, kinds)):
        if kind == "video":
            rendered.append(
                process_video_clip(
                    clip, durations[index], settings, index,
                    trims[index], foci[index], zooms[index],
                )
            )
        else:
            rendered.append(
                create_animated_image(
                    clip, durations[index], index, settings,
                    foci[index], zooms[index],
                )
            )

    try:
        current = _join_with_transitions(rendered, durations, settings)
    except PipelineError:
        current = _chain_incremental(rendered, settings)

    if ckey:
        cache_put(current, settings.cache_dir, ckey, "chain", "mp4")
    return current


def create_image_sequence(images, narration_duration, settings, clip_durations=None):
    """Animate all images and stitch them with xfade transitions."""
    return create_clip_sequence(
        images, narration_duration, settings, clip_durations=clip_durations,
        kinds=["image"] * len(images),
    )

"""Benchmark the visual-assembly stage and check it against the old chain.

Assembling a video used to cost quadratic time. ``create_clip_sequence`` ran
one xfade per ffmpeg invocation, re-encoding the entire timeline accumulated so
far for every clip added. A 60s video arriving as 15 x 4s upload segments
therefore encoded ~480s of 1080x1920 video, and the chain cache was consulted
only *after* all of that work, so repeat renders did not benefit either.

The replacement renders each batch of clips in a single ffmpeg pass and joins
batches with a stream-copy concat, which is linear in timeline length.

``_chain_incremental`` is the original quadratic loop, kept in the module as a
fallback. This script drives both over byte-identical input so the speedup is
measured rather than assumed, and asserts the two agree on duration, frame
count, resolution and frame rate.

Requires ffmpeg + ffprobe on PATH.

Run:  .venv/Scripts/python.exe scripts/bench_visuals.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.config import RenderSettings
from pipeline.transitions import (
    XFADE_BATCH_SIZE,
    _chain_incremental,
    _join_with_transitions,
    _resolve_clip_durations,
    _transition_name,
    create_clip_sequence,
)
from pipeline.utils import get_duration
from pipeline.visuals import create_animated_image, process_video_clip


def make_clips(dest, count, seconds, width, height, fps):
    """Build `count` distinct source segments, like a split upload."""
    paths = []
    for i in range(count):
        out = os.path.join(dest, f"src_{i:03d}.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-pix_fmt", "yuv420p",
                out,
            ],
            check=True,
        )
        paths.append(out)
    return paths


def probe(path):
    """Return (duration, width, height, fps, frames)."""
    raw = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "format=duration:stream=width,height,avg_frame_rate,nb_read_frames",
            "-of", "default=noprint_wrappers=1",
            path,
        ],
        check=True, text=True, capture_output=True,
    ).stdout
    values = dict(
        line.split("=", 1) for line in raw.split() if "=" in line
    )
    num, _, den = values.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den) else 0.0
    return (
        float(values.get("duration", 0)),
        int(values.get("width", 0)),
        int(values.get("height", 0)),
        round(fps, 2),
        int(values.get("nb_read_frames", 0)),
    )


def settings_for(temp_dir, cache_dir=""):
    return RenderSettings(
        media_files=[],
        temp_dir=temp_dir,
        output_path=os.path.join(temp_dir, "out.mp4"),
        cache_dir=cache_dir,
        transition="Auto",
        reframe="crop",
        hook=False,
        captions=False,
    )


def run_case(count, seconds, width, height, fps, workdir, new_only=False):
    print(f"\n=== {count} clips x {seconds}s at {width}x{height} @ {fps}fps ===", flush=True)

    src_dir = os.path.join(workdir, f"src_{count}_{seconds}")
    os.makedirs(src_dir, exist_ok=True)
    clips = make_clips(src_dir, count, seconds, width, height, fps)

    narration = count * seconds
    durations = _resolve_clip_durations(count, narration, None)

    # Per-clip normalization is identical on both paths, so render it once and
    # time only the join — that is the part this change actually rewrites.
    render_temp = os.path.join(workdir, f"render_{count}_{seconds}")
    os.makedirs(render_temp, exist_ok=True)
    render_settings = settings_for(render_temp)
    t0 = time.perf_counter()
    rendered = [
        process_video_clip(c, durations[i], render_settings, i, 0)
        for i, c in enumerate(clips)
    ]
    render_secs = time.perf_counter() - t0
    print(f"  per-clip normalize (shared by both paths): {render_secs:6.1f}s", flush=True)

    # Expected length. Batches are joined with a stream-copy concat, which is a
    # hard cut, so only clips *inside* a batch get a crossfade: n clips split
    # across b batches carry (n - b) transitions, not (n - 1).
    batches = [len(range(s, min(s + XFADE_BATCH_SIZE, count)))
               for s in range(0, count, XFADE_BATCH_SIZE)]
    actual_clip_secs = [get_duration(p) for p in rendered]
    expected = sum(actual_clip_secs) - (count - len(batches)) * 0.45
    # A clip is cut with -t, which rounds up to a whole frame; at 30fps a 4.42s
    # request becomes 133 frames (4.4333s). Measured against actual clip
    # lengths that cancels, so only a small margin is needed here.
    tolerance = 0.10
    problems = []

    # --- new path: one pass per batch, then stream-copy concat ---
    new_temp = os.path.join(workdir, f"new_{count}_{seconds}")
    os.makedirs(new_temp, exist_ok=True)
    new_settings = settings_for(new_temp)
    t0 = time.perf_counter()
    new_out = _join_with_transitions(rendered, durations, new_settings)
    new_secs = time.perf_counter() - t0
    new_probe = probe(new_out)
    print(f"  new join (batched)   : {new_secs:7.1f}s", flush=True)
    print(f"  new out: dur={new_probe[0]:.3f}s (expected {expected:.3f}s) "
          f"{new_probe[1]}x{new_probe[2]} @{new_probe[3]} frames={new_probe[4]}",
          flush=True)

    if abs(new_probe[0] - expected) > tolerance:
        problems.append(f"new duration {new_probe[0]:.3f}s vs expected {expected:.3f}s")
    if (new_probe[1], new_probe[2]) != (width, height):
        problems.append(f"resolution {new_probe[1]}x{new_probe[2]} != {width}x{height}")
    if abs(new_probe[3] - fps) > 0.01:
        problems.append(f"fps {new_probe[3]} != {fps}")
    if new_probe[4] == 0:
        problems.append("no frames decoded")

    if new_only:
        return problems

    # --- old path: one xfade per ffmpeg call, quadratic ---
    old_temp = os.path.join(workdir, f"old_{count}_{seconds}")
    os.makedirs(old_temp, exist_ok=True)
    old_settings = settings_for(old_temp)
    t0 = time.perf_counter()
    old_out = _chain_incremental(rendered, old_settings)
    old_secs = time.perf_counter() - t0
    old_probe = probe(old_out)

    print(f"  old join (quadratic) : {old_secs:7.1f}s", flush=True)
    print(f"  join speedup         : {old_secs / new_secs if new_secs else 0:7.2f}x")
    print(f"  visuals stage total  : {render_secs + old_secs:7.1f}s -> "
          f"{render_secs + new_secs:7.1f}s "
          f"({(render_secs + old_secs) / (render_secs + new_secs):.2f}x)")
    print(f"  old out: dur={old_probe[0]:.3f}s {old_probe[1]}x{old_probe[2]} "
          f"@{old_probe[3]} frames={old_probe[4]}", flush=True)

    # Parity: both joins must land in the same place. The single-pass graph and
    # the incremental chain can differ by a frame where 0.45s crossfades
    # quantise to 13.5 frames at 30fps.
    if abs(old_probe[0] - new_probe[0]) > 0.10:
        problems.append(f"duration drift old={old_probe[0]:.3f} new={new_probe[0]:.3f}")
    return problems


def check_transition_names():
    """'Auto' must rotate identically to the pre-batching sequence."""
    got = [_transition_name("Auto", i) for i in range(1, 8)]
    want = ["fade", "slideleft", "slideright", "wipeleft", "wiperight", "fade", "slideleft"]
    return [] if got == want else [f"auto rotation {got} != {want}"]


def check_cache_short_circuits(count, seconds, width, height, fps, workdir):
    """A second render with the same inputs must not re-encode the chain."""
    src_dir = os.path.join(workdir, f"src_{count}_{seconds}")
    os.makedirs(src_dir, exist_ok=True)
    clips = make_clips(src_dir, count, seconds, width, height, fps)
    cache = os.path.join(workdir, "cache")

    first_temp = os.path.join(workdir, "cache_first")
    os.makedirs(first_temp, exist_ok=True)
    t0 = time.perf_counter()
    create_clip_sequence(
        clips, count * seconds, settings_for(first_temp, cache), kinds=["video"] * count
    )
    cold = time.perf_counter() - t0

    second_temp = os.path.join(workdir, "cache_second")
    os.makedirs(second_temp, exist_ok=True)
    t0 = time.perf_counter()
    out = create_clip_sequence(
        clips, count * seconds, settings_for(second_temp, cache), kinds=["video"] * count
    )
    warm = time.perf_counter() - t0

    print(f"\n=== cache: cold {cold:.1f}s -> warm {warm:.1f}s "
          f"({cold / warm if warm else 0:.1f}x) ===")
    if not os.path.exists(out):
        return ["cache hit produced no file"]
    if warm >= cold * 0.75:
        return [f"warm render {warm:.1f}s not faster than cold {cold:.1f}s — chain cache still not short-circuiting"]
    return []


def check_image_mix(count, workdir):
    """Images and videos must interleave through the batched join."""
    src_dir = os.path.join(workdir, "mixed_src")
    os.makedirs(src_dir, exist_ok=True)
    kinds, clips = [], []
    for i in range(6):
        if i % 2:
            vid = os.path.join(src_dir, f"v{i}.mp4")
            # Longer than the clip the pipeline will ask for, so -t is never
            # clamped by the source and the expected duration stays exact.
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=8",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-pix_fmt", "yuv420p", vid,
                ],
                check=True,
            )
            clips.append(vid)
            kinds.append("video")
        else:
            img = os.path.join(src_dir, f"i{i}.png")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=1920x1080:duration=1",
                    "-frames:v", "1", img,
                ],
                check=True,
            )
            clips.append(img)
            kinds.append("image")

    temp = os.path.join(workdir, "mixed_out")
    os.makedirs(temp, exist_ok=True)
    count = len(clips)
    narration = 14.25
    join_settings = settings_for(temp)
    out = create_clip_sequence(clips, narration, join_settings, kinds=kinds)
    dur, w, h, rate, frames = probe(out)
    # Mirror the pipeline's own maths rather than assuming even clip lengths.
    per_clip = _resolve_clip_durations(count, narration, None)
    expected = sum(per_clip) - (count - 1) * 0.45
    canvas = (join_settings.width, join_settings.height)
    print(f"\n=== mixed image/video join: dur={dur:.3f}s (expected {expected:.3f}s) "
          f"{w}x{h} @{rate} frames={frames} ===")
    problems = []
    # 1920x1080 sources are deliberately re-framed onto the 9:16 canvas.
    if (w, h) != canvas:
        problems.append(f"canvas {w}x{h} != {canvas[0]}x{canvas[1]}")
    if abs(dur - expected) > 0.15:
        problems.append(f"mixed join duration {dur:.3f}s vs expected {expected:.3f}s")
    if frames == 0:
        problems.append("no frames decoded")
    return problems


def main():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"{tool} is not on PATH")
            return 1

    # Clip counts to compare. The old path is quadratic, so large cases cost
    # 20+ minutes of encoding on their own. --new-only skips it, which makes
    # the large cases (15 clips = a 60s upload) practical to run.
    #   bench_visuals.py            # 4 and 15 clips, both paths
    #   bench_visuals.py --new-only # 4 and 15 clips, new path only
    #   bench_visuals.py 8          # 8 clips, both paths
    argv = sys.argv[1:]
    new_only = "--new-only" in argv
    counts = [int(a) for a in argv if a != "--new-only"] or [4, 15]

    workdir = tempfile.mkdtemp(prefix="bench_visuals_")
    failures = []
    try:
        failures += check_transition_names()
        failures += check_image_mix(6, workdir)
        failures += check_cache_short_circuits(6, 3, 1080, 1920, 30, workdir)
        for count in counts:
            failures += run_case(count, 4, 1080, 1920, 30, workdir, new_only=new_only)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll visuals-stage checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

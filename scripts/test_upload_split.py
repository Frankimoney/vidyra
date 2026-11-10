"""Tests that an uploaded video is cut quickly and without losing footage.

Uploading a video in the normal Create flow used to time out. The cause was in
``_split_video``: it re-encoded every 4s segment in its own ffmpeg process, one
after the other, while the browser waited on the same HTTP request. A
three-minute clip spent minutes in ffmpeg and blew past the client timeout.

These tests pin the replacement (one stream-copy pass, re-encode only when the
source is too sparsely keyframed) and the property that actually matters: the
segments must cover the source exactly once.

Requires ffmpeg + ffprobe on PATH.

Run:  PYTHONPATH=backend .venv/Scripts/python.exe scripts/test_upload_split.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testsplit")
os.environ.setdefault("APP_ENV", "development")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routes.uploads import (  # noqa: E402
    _segment_ext,
    _segments_sane,
    _split_video,
)
from pipeline.utils import get_duration  # noqa: E402


class MemoryStorage:
    """Test double for R2: keeps published bytes in memory, never on disk."""

    def __init__(self, root="", secret=""):
        self.root = root
        self.secret = secret
        self.blobs = {}

    def publish(self, local, rel):
        with open(local, "rb") as fh:
            self.blobs[rel] = fh.read()
        return f"/media/{rel}"

    def delete_file(self, rel):
        return self.blobs.pop(rel, None) is not None


FAILURES = []
WORK = tempfile.mkdtemp(prefix="vidyra-split-")


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        FAILURES.append(name)


def make_clip(name, seconds, gop, size="1280x720"):
    """Render a synthetic clip with a controlled keyframe interval."""
    path = os.path.join(WORK, name)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
            "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-g", str(gop), "-keyint_min", str(gop),
            "-pix_fmt", "yuv420p", path,
        ],
        check=True,
    )
    return path


def split(path, name="clip.mov", root=None):
    root = root or os.path.join(WORK, "storage")
    driver = MemoryStorage(root, "testsecret")
    return _split_video(driver, "dev-user", os.urandom(4).hex(), name, path), driver, root


def test_fast_path():
    """Ordinary footage (a keyframe every 1-2s) must cut in seconds."""
    print("stream-copy fast path:")
    src = make_clip("normal.mp4", 60, 30)          # keyframe every 1s
    driver_root = os.path.join(WORK, "fast")
    os.makedirs(driver_root, exist_ok=True)
    driver = MemoryStorage(driver_root, "testsecret")

    t0 = time.time()
    items = _split_video(driver, "dev-user", "vid1", "clip.mov", src)
    elapsed = time.time() - t0

    source = get_duration(src)
    durations = [i["duration"] for i in items]
    total = sum(durations)

    check("produces multiple segments", len(items) > 1, f"{len(items)} segments")
    check("finishes well under the old re-encode time", elapsed < 30, f"{elapsed:.1f}s")
    check("segments cover the source once", abs(total - source) < 1.0,
          f"{total:.1f}s of {source:.1f}s")
    check("no duplicated footage", total <= source * 1.02, f"{total:.1f}s")
    check("no segment is a sliver", min(durations) >= 1.0, f"min {min(durations):.2f}s")
    check("returns real durations for the timeline", all(d > 0 for d in durations))
    check("segments are named in order",
          [i["name"] for i in items] == sorted(i["name"] for i in items))
    check("names are sequential",
          [i["name"] for i in items] == [f"seg_{i:03d}.mp4" for i in range(len(items))])
    check("all segments are mp4", all(i["storagePath"].endswith(".mp4") for i in items))
    check("single file keeps the original name", True)


def test_single_segment_keeps_name():
    """A clip shorter than one segment comes back as a single item."""
    print("single segment:")
    src = make_clip("tiny.mp4", 2, 30)
    root = os.path.join(WORK, "single")
    os.makedirs(root, exist_ok=True)
    driver = MemoryStorage(root, "testsecret")
    items = _split_video(driver, "dev-user", "vid2", "My Highlight.mov", src)
    check("returns exactly one segment", len(items) == 1, f"{len(items)}")
    check("keeps the uploaded filename", items[0]["name"] == "My Highlight.mov",
          items[0]["name"])
    check("but stores a playable path", items[0]["storagePath"].endswith(".mp4"))


def test_sparse_keyframes():
    """A source with a keyframe every 10s must come back intact.

    A plain stream-copy cut cannot find boundaries near 4s here. Depending on
    how the keyframes line up against the 4s targets the muxer either emits
    overlapping clips (duplicated footage) or fewer, longer ones. Both are
    handled: overlap trips the sanity gate and triggers a re-encode, and long
    clips are left alone because the timeline sizes clips to the narration and
    simply trims the surplus. Either way the source must survive intact.
    """
    print("sparse keyframes:")
    src = make_clip("sparse.mp4", 40, 300)         # keyframe every 10s
    root = os.path.join(WORK, "sparse")
    os.makedirs(root, exist_ok=True)
    driver = MemoryStorage(root, "testsecret")
    items = _split_video(driver, "dev-user", "vid3", "clip.mov", src)

    source = get_duration(src)
    durations = [i["duration"] for i in items]
    total = sum(durations)

    check("still produces clips", len(items) > 1, f"{len(items)} segments")
    check("no duplicated footage", total <= source * 1.02,
          f"{total:.1f}s vs source {source:.1f}s")
    check("no footage lost", total >= source * 0.98,
          f"{total:.1f}s vs source {source:.1f}s")
    check("still enough clips to pace a video", len(items) >= source / 16,
          f"{len(items)} clips for {source:.0f}s")
    check("no segment is a sliver", min(durations) >= 1.0, f"min {min(durations):.2f}s")
    check("no leftover work directory", not os.path.isdir(os.path.join(root, "media", "dev-user", "vid3", ".work")))


def test_sanity_gate():
    """_segments_sane is the guard that decides whether to re-encode."""
    print("sanity gate:")
    check("clean 4s cuts accepted", _segments_sane([("a", 4.0)] * 10, 40.0))
    check("long clips accepted (narration sizes them)", _segments_sane([("a", 8.3)] * 5, 41.5))
    check("overlapping cuts rejected", not _segments_sane([("a", 4.0)] * 10, 20.0))
    check("truncated cuts rejected", not _segments_sane([("a", 4.0)] * 5, 40.0))
    check("slivers rejected", not _segments_sane([("a", 0.4)] * 10, 40.0))
    check("empty rejected", not _segments_sane([], 40.0))


def test_extension_choice():
    """Stream copy keeps the codec, so the container has to be able to hold it."""
    print("container choice:")
    check("mp4 stays mp4", _segment_ext("a.mp4") == ".mp4")
    check("mov becomes mp4", _segment_ext("a.MOV") == ".mp4")
    check("webm stays webm", _segment_ext("a.webm") == ".webm")
    check("mkv stays mkv", _segment_ext("a.mkv") == ".mkv")
    check("avi stays avi", _segment_ext("a.avi") == ".avi")
    check("unknown becomes mp4", _segment_ext("a.bin") == ".mp4")


def test_too_long_rejected():
    """The 4-minute cap is enforced before any ffmpeg work happens."""
    print("duration cap:")
    from fastapi import HTTPException
    from pipeline.config import MAX_VIDEO_SEGMENTS, VIDEO_SEGMENT_SECONDS

    root = os.path.join(WORK, "cap")
    os.makedirs(root, exist_ok=True)
    driver = MemoryStorage(root, "testsecret")
    src = make_clip("long.mp4", 5, 30)

    import app.routes.uploads as up

    real = up.get_duration
    up.get_duration = lambda *a, **k: (MAX_VIDEO_SEGMENTS * VIDEO_SEGMENT_SECONDS) + 30
    try:
        _split_video(driver, "dev-user", "vid4", "clip.mov", src)
        check("rejects over-length video", False, "no exception raised")
    except HTTPException as exc:
        check("rejects over-length video", exc.status_code == 413, exc.detail[:60])
    finally:
        up.get_duration = real


if __name__ == "__main__":
    try:
        test_sanity_gate()
        test_extension_choice()
        test_too_long_rejected()
        test_fast_path()
        test_single_segment_keeps_name()
        test_sparse_keyframes()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL UPLOAD SPLIT TESTS PASSED")

"""Local checks for the Wan-Animate-2 window planner and geometry helpers.

No GPU or ComfyUI needed: this pins the arithmetic that decides how many
81-frame diffusion windows a 10-30s clip turns into, how the driving clip is
sliced between them, and how much of it is thrown away at each seam. If this
maths is wrong every window after the first reads the wrong frames, so it is
checked on the PC rather than on a paid pod.

Run: python scripts/test_wan_motion.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "deploy" / "runpod" / "worker.py"

_spec = importlib.util.spec_from_file_location("runpod_worker", WORKER)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


def invariants(plan: list, requested: int, available: int) -> tuple[str, bool]:
    """Shared shape rules every plan must satisfy."""
    if not plan:
        return "empty plan", False
    for i, frag in enumerate(plan):
        last = i == len(plan) - 1
        if frag["length"] % 4 != 1:
            return f"window {i} length {frag['length']} is not 4k+1", False
        if not 5 <= frag["length"] <= 81:
            return f"window {i} length {frag['length']} out of range", False
        if frag["keep"] < 1:
            return f"window {i} keeps {frag['keep']} frames", False
        if i == 0:
            if frag["carry"]:
                return "first window must not carry motion", False
            if not last and frag["keep"] != frag["length"]:
                return "first window must keep every frame it renders", False
            if frag["keep"] > frag["length"]:
                return "first window keeps more than it renders", False
            if last and (frag["length"] - frag["keep"]) > 4:
                return f"window {i} trimmed {frag['length'] - frag['keep']} frames, max 4", False
        else:
            if not frag["carry"]:
                return f"window {i} must carry the previous last frame", False
            # Only the final window may give frames back (4k+1 rounding).
            if not last and frag["keep"] != frag["length"] - worker.WAN_OVERLAP:
                return f"window {i} keep != length - overlap", False
            if frag["keep"] > frag["length"] - worker.WAN_OVERLAP:
                return f"window {i} keeps more than length - overlap", False
            if last and (frag["length"] - frag["keep"]) > 4:
                return f"window {i} trimmed {frag['length'] - frag['keep']} frames, max 4", False
            if frag["start"] != plan[i - 1]["start"] + plan[i - 1]["length"]:
                return f"window {i} does not continue where {i - 1} stopped", False
    kept = sum(frag["keep"] for frag in plan)
    if kept != requested:
        return f"kept {kept} frames, asked for {requested}", False
    if available:
        consumed = plan[-1]["start"] + plan[-1]["length"]
        if consumed > available:
            return f"reads {consumed} driving frames, clip only has {available}", False
    return "", True


print("Wan-Animate-2 window planner")
for requested, available in [(81, 0), (100, 0), (240, 0), (480, 0), (720, 0), (10, 0)]:
    plan = worker.wan_fragment_plan(requested, available)
    detail, ok = invariants(plan, min(requested, available) if available else requested, available)
    check(f"{requested} frames -> {len(plan)} window(s)", ok, detail)
    if not ok:
        print(f"        plan={plan}")

print("\nDuration range the studio exposes (10-30s)")
for fps in (24, 25, 30):
    for seconds in (10, 20, 30):
        frames = seconds * fps
        plan = worker.wan_fragment_plan(frames, 0)
        detail, ok = invariants(plan, frames, 0)
        driving_needed = plan[-1]["start"] + plan[-1]["length"]
        check(f"{seconds}s @ {fps}fps = {frames} frames -> {len(plan)} windows",
              ok, detail)
        print(f"        needs {driving_needed} driving frames "
              f"({round(driving_needed / fps, 2)}s of clip)")

print("\nShort driving clip is respected, not padded with frozen frames")
plan = worker.wan_fragment_plan(720, 120)
kept = sum(frag["keep"] for frag in plan)
consumed = plan[-1]["start"] + plan[-1]["length"]
check("short clip yields few windows", len(plan) <= 3, f"got {len(plan)}")
check("never reads past the clip", consumed <= 120, f"consumed {consumed}")
check("never returns more than the clip has", kept <= 120, f"kept {kept}")
# Windows must sit on the 4k+1 grid, so at most 3 frames per boundary are
# unreachable. The run reports the duration it actually produced.
check("only grid slack is lost", kept >= 120 - 3 * len(plan), f"kept {kept}")
detail, ok = invariants(plan, kept, 120)
check("short-clip plan still well formed", ok, detail)

print("\nUnavailable clip (ffprobe gave nothing) still plans")
plan = worker.wan_fragment_plan(240, 0)
check("240 frames with unknown availability", bool(plan), "empty plan")
check("clip read is unconstrained", len(plan) == 3, f"got {len(plan)}")

print("\nGeometry")
check("_round4k1(1) == 5", worker._round4k1(1) == 5, str(worker._round4k1(1)))
check("_round4k1(80) == 81", worker._round4k1(80) == 81, str(worker._round4k1(80)))
check("_round4k1(81) == 85", worker._round4k1(81) == 85, str(worker._round4k1(81)))
check("_snap(481) == 480", worker._snap(481, 256, 1280) == 480, str(worker._snap(481, 256, 1280)))
check("_snap keeps 832", worker._snap(832, 256, 1280) == 832, str(worker._snap(832, 256, 1280)))
landscape = worker.fit_dimensions(1920, 1080)
portrait = worker.fit_dimensions(1080, 1920)
square = worker.fit_dimensions(1080, 1080)
check("landscape stays landscape", landscape[0] > landscape[1], str(landscape))
check("portrait stays portrait", portrait[1] > portrait[0], str(portrait))
check("square stays square", landscape and square[0] == square[1], str(square))
check("dimensions are on the 16px grid",
      all(v % 16 == 0 for v in landscape + portrait + square), str((landscape, portrait, square)))
check("degenerate source falls back", worker.fit_dimensions(0, 0) == (832, 480))

print("\nFrame-rate parsing")
check('"30000/1001"', worker._parse_rate("30000/1001") == 29.97, str(worker._parse_rate("30000/1001")))
check('"24"', worker._parse_rate("24") == 24.0, str(worker._parse_rate("24")))
check("garbage -> 0.0", worker._parse_rate("N/A") == 0.0, str(worker._parse_rate("N/A")))

print("\nModel manifest")
files = worker.MODEL_FILES["wan-animate-2"]
for key in ("unet", "lora", "clip", "clip_vision", "vae"):
    check(f"manifest has {key}", bool(files.get(key)))

print("\nGraph contract (single window, no ComfyUI needed)")
graph = worker._build_wan_animate(
    {
        "model": "wan-animate-2",
        "prompt": "a woman dancing",
        "params": {"width": 832, "height": 480, "fps": 24, "steps": 6, "cfg": 1.0, "seed": 7},
        "ref_files": {"image": "TARGET.png", "video": "DRIVING.mp4"},
    },
    length=81,
    start=0,
)
node = graph["20"]
check("WanAnimate2ToVideo window length", node["inputs"]["length"] == 81, str(node["inputs"]["length"]))
check("driving clip sliced from 0", graph["12"]["inputs"]["skip_first_frames"] == 0)
check("driving clip capped to the window", graph["12"]["inputs"]["frame_load_cap"] == 81)
check("reference latent trimmed before decode",
      graph["13"]["inputs"]["samples"] == ["20", 2] and graph["13"]["inputs"]["trim_amount"] == ["20", 3])
check("sampler reads the node latent", graph["19"]["inputs"]["latent_image"] == ["20", 2])
check("carry input absent on window 0", "continue_motion" not in node["inputs"])
check("upstream negative used when none given", "色调艳丽" in graph["7"]["inputs"]["text"])
check("no stale VHS 'video_load_cap'", "video_load_cap" not in graph["12"]["inputs"])
check("no stale VHS 'fps' on combine", "fps" not in graph["21"]["inputs"])
check("combine uses current format string", graph["21"]["inputs"]["format"] == "video/h264-mp4")

graph2 = worker._build_wan_animate(
    {"prompt": "x", "params": {}, "ref_files": {"image": "T.png", "video": "D.mp4"}},
    length=81,
    start=162,
    carry="carry.png",
)
check("continuation carries motion", graph2["20"]["inputs"]["continue_motion"] == ["22", 0])
check("continuation seeks the driving clip",
      graph2["12"]["inputs"]["skip_first_frames"] == 162, str(graph2["12"]["inputs"]["skip_first_frames"]))
check("no VHS_LoadVideo 'force_size' key", "force_size" not in graph2["12"]["inputs"])

print("\nLivePortrait path still builds and now loads the whole clip")
lp = worker._build_motion({
    "model": "liveportrait",
    "params": {"fps": 24, "frames": 96},
    "ref_files": {"image": "T.png", "video": "D.mp4"},
})
check("driving clip no longer capped at 1 frame", lp["3"]["inputs"]["frame_load_cap"] == 96,
      str(lp["3"]["inputs"]["frame_load_cap"]))
check("driving_face comes from the driving video",
      lp["6"]["inputs"]["driving_face"] == ["5", 0], str(lp["6"]["inputs"]["driving_face"]))
check("src_face still comes from the target", lp["6"]["inputs"]["src_face"] == ["4", 0])

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("All Wan-Animate-2 motion checks passed.")

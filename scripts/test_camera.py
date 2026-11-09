"""End-to-end tests for the Wan2.2 camera-control tool ("wan22-cam-i2v").

A cinematic camera-motion generator: the scene's "shot" direction ("a slow
push-in") is classified into WanCameraEmbedding's camera_pose vocabulary and
stamped as an explicit trajectory (plus a speed multiplier) into every img2video
run that uses the Wan2.2 camera model. This test covers:

  A. _camera_params classification (unit)
  B. Studio catalog exposes wan22-cam-i2v under img2video
  C. build_workflow emits the official two-pass camera graph
  D. a full film makes every img2video run a camera run with the scene's pose

Requires RUNPOD_FAKE=1 and sets FILM_VIDEO_MODEL=wan22-cam-i2v.

Run:  STORAGE_ROOT=storage/testcamera RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key FILM_VIDEO_MODEL=wan22-cam-i2v \
        PYTHONPATH=backend .venv/Scripts/python.exe scripts/test_camera.py
"""

import importlib.util
import json
import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testcamera")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ["FILM_VIDEO_MODEL"] = "wan22-cam-i2v"
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []

PACKAGE = {
    "title": "The Long Cut",
    "style": "cinematic",
    "characters": [
        {"name": "Ava", "description": "a sharp-eyed engineer in a leather jacket"},
    ],
    "scenes": [
        {
            "narration": "Ava watched the monitors flicker to life.",
            "dialogue": {"Ava": "Now we move."},
            "setting": "a dim engineering lab humming with old servers, 9:16 vertical",
            "shot": "a slow push-in on Ava",
            "transition": "Auto",
        },
        {
            "narration": "Beyond the glass, the city waited.",
            "dialogue": {},
            "setting": "a quiet rooftop at sunrise, 9:16 vertical",
            "shot": "a dramatic low angle pulling up to the skyline",
            "transition": "fade",
        },
    ],
}


def _load_worker():
    path = os.path.join(os.path.dirname(__file__), "..", "deploy", "runpod", "worker.py")
    spec = importlib.util.spec_from_file_location("vidyra_runpod_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fast_piper(command, input_text=None):
    import subprocess
    import wave

    if command and str(command[0]).replace("\\", "/").endswith("piper"):
        out = str(command[-1])
        if out.endswith(".wav"):
            with wave.open(out, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 12000)
        return None
    result = subprocess.run(command, input=input_text, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rc={result.returncode} cmd={' '.join(command[:8])}")
    return result


def _run_film():
    import pipeline.narration as narration
    from starlette.testclient import TestClient

    import app.workers.film_worker as fw
    from app.main import app
    from app.services import studio as studio_service

    fw.run_cmd = _fast_piper
    narration.run_cmd = _fast_piper

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}
    with TestClient(app) as client:
        r = client.post("/api/films", headers=H, json={
            "idea": "",
            "outline": json.dumps(PACKAGE),
            "style": "cinematic",
            "scenes_count": 2,
            "characters": [],
            "captions": False,
            "music": "",
            "research": False,
            "lip_sync": True,
        })
        if r.status_code != 201:
            return None, [], f"film POST {r.status_code}: {r.text[:200]}"
        job_id = r.json()["job_id"]

        deadline = time.time() + 240
        job = None
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}", headers=H)
            status = (job.json().get("status") or "")
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        runs = [x for x in studio_service.list_runs("dev-user", limit=200)
                if (x.get("title") or "").startswith("Film")]
        return (job.json() if job else {}), runs, ""


def main() -> int:
    from app.workers.film_worker import _camera_params

    def check(name, cond, extra=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
        if not cond:
            FAILURES.append(name)

    print("A. shot -> camera-pose classification:")
    expect = {
        "a slow push-in": ("Zoom In", 0.6),
        "a gentle pan across": ("Pan Right", 0.6),
        "a subtle zoom-in": ("Zoom In", 0.6),
        "an orbit around the subject": ("ClockWise (CW)", 1.0),
        "dramatic low angle": ("Pan Up", 1.0),
        "high angle over the city": ("Pan Down", 1.0),
        "fast dolly in on the door": ("Zoom In", 1.6),
        "pull back to a wide shot": ("Zoom Out", 1.0),
        "pan left across the room": ("Pan Left", 1.0),
        "static locked-off establishing": ("Static", 1.0),
        "": ("Zoom In", 1.0),
    }
    for shot, want in expect.items():
        got = _camera_params(shot)
        check(f"'{shot}' -> {want}", got == want, str(got))

    print("B. studio catalog exposes wan22-cam-i2v:")
    from starlette.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/catalog/studio", headers={"Authorization": "Bearer vidyra-dev-key"}).json()
        img2video = next((t for t in body["tools"] if t["tool"] == "img2video"), None)
        check("catalog has img2video entry", img2video is not None)
        check("wan22-cam-i2v in img2video models", img2video is not None and "wan22-cam-i2v" in img2video["models"], str(img2video and img2video["models"]))
        role_names = [s["role"] for s in (body.get("modelRefs", {}).get("img2video", {}).get("wan22-cam-i2v") or [])]
        check("wan22-cam-i2v wants only a start frame", role_names == ["image"], str(role_names))

    print("C. worker builds the Wan2.2 camera graph:")
    worker = _load_worker()
    graph = worker.build_workflow({
        "tool": "img2video", "model": "wan22-cam-i2v",
        "prompt": "slow push-in on Ava",
        "params": {"width": 640, "height": 640, "frames": 81, "fps": 16, "steps": 20, "cfg": 3.5, "camera_pose": "Zoom In", "camera_speed": 1.0},
        "ref_files": {"image": "START.png"},
    })
    classes = {n["class_type"] for n in graph.values()}
    check("graph uses WanCameraEmbedding + WanCameraImageToVideo", "WanCameraEmbedding" in classes and "WanCameraImageToVideo" in classes, str(classes))
    cam = next(n for n in graph.values() if n["class_type"] == "WanCameraEmbedding")
    check("camera trajectory stamped (Zoom In, speed 1.0, 81 frames)",
          cam["inputs"]["camera_pose"] == "Zoom In" and cam["inputs"]["speed"] == 1.0 and cam["inputs"]["length"] == 81,
          str(cam["inputs"]))
    samplers = [n for n in graph.values() if n["class_type"] == "KSamplerAdvanced"]
    high, low = samplers[0], samplers[1]
    check("two-pass sampling wired end-to-end",
          len(samplers) == 2 and high["inputs"]["add_noise"] == "enable" and high["inputs"]["return_with_leftover_noise"] == "enable"
          and high["inputs"]["end_at_step"] == 10 and low["inputs"]["add_noise"] == "disable" and low["inputs"]["start_at_step"] == 10
          and low["inputs"]["end_at_step"] == 10000
          and high["inputs"]["latent_image"] == ["12", 2] and low["inputs"]["latent_image"] == ["13", 0],
          str([s["inputs"] for s in samplers]))
    shifts = [n for n in graph.values() if n["class_type"] == "ModelSamplingSD3"]
    check("per-model sampling shift 8 on both UNets",
          len(shifts) == 2 and all(n["inputs"]["shift"] == 8 for n in shifts), str([n["inputs"] for n in shifts]))
    check("output via VHS_VideoCombine (Vidyra convention)", "VHS_VideoCombine" in classes)
    default = worker.build_workflow({
        "tool": "img2video", "model": "wan22-cam-i2v",
        "prompt": "anything",
        "params": {},
        "ref_files": {"image": "START.png"},
    })
    cam2 = next(n for n in default.values() if n["class_type"] == "WanCameraEmbedding")
    check("camera_pose defaults to Zoom In when absent", cam2["inputs"]["camera_pose"] == "Zoom In", str(cam2["inputs"]))

    print("D. a full film runs every scene through the camera model:")
    from app.services import studio as studio_service
    studio_service._memory_store.clear()
    body, runs, err = _run_film()
    status = (body or {}).get("status")
    check("film completed", status == "completed", err or str(status) + " " + str((body or {}).get("error", "")))
    if status == "completed":
        check("all runs completed", all(x.get("status") == "completed" for x in runs),
              str([(x.get("tool"), x.get("status")) for x in runs]))
        i2v = [x for x in runs if x.get("tool") == "img2video"]
        check("dialogue scene acts (frame + motion) and still scene animates = 2 img2video runs",
              len(i2v) == 2, str([(x.get("title"), x.get("model")) for x in runs]))
        check("every img2video ran the Wan2.2 camera model",
              all(x.get("model") == "wan22-cam-i2v" for x in i2v), str([x.get("model") for x in i2v]))
        got = sorted(
            str(x.get("params", {})).replace(",", "").replace("'", "")
            for x in i2v
        )
        check("scene shots became camera trajectories ('Zoom In' slow + 'Pan Up' dramatic)",
              any("camera_pose: Zoom In" in g and "camera_speed: 0.6" in g for g in got) and
              any("camera_pose: Pan Up" in g and "camera_speed: 1.0" in g for g in got),
              str(got))
        check("actor lip-sync still present on the dialogue scene",
              any(x.get("tool") == "lipsync" for x in runs), str([x.get("tool") for x in runs]))

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL CAMERA TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
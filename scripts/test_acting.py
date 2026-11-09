"""End-to-end tests for "as in a movie" acting scenes.

Dialogue scenes should be filmed like a movie: the character gets a scene
frame (txt2img), moves (img2video), and then visibly talks — the scene's own
narration + dialogue is synthesized and lip-synced over the moving character
(lipsync tool). Turning `lip_sync` off returns to the classic still scene
(saving one GPU run per scene).

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testacting RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_acting.py
"""

import json
import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testacting")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []

PACKAGE = {
    "title": "Code & Coffee",
    "style": "cinematic",
    "characters": [
        {"name": "Ava", "description": "a sharp-eyed engineer in a leather jacket"},
        {"name": "Kai", "description": "a calm mentor with a knowing smile"},
    ],
    "scenes": [
        {
            "narration": "The prototype was failing at midnight again.",
            "dialogue": {"Ava": "This build is doomed."},
            "setting": "a dim engineering lab humming with old servers, 9:16 vertical",
            "shot": "a slow push-in on Ava",
            "transition": "Auto",
        },
        {
            "narration": "Then Kai found the bug in seconds flat.",
            "dialogue": {"Kai": "It was the cache all along."},
            "setting": "a quiet rooftop at sunrise, 9:16 vertical",
            "shot": "a gentle pan across the skyline",
            "transition": "fade",
        },
    ],
}


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
        with open(os.path.join(os.path.dirname(__file__), "..", "ffmpeg_err.log"), "a", encoding="utf-8") as f:
            f.write("\n==== rc=%s %s ====\n%s\n" % (result.returncode, command[:10], result.stderr))
        raise RuntimeError(f"rc={result.returncode} cmd={' '.join(command[:8])}")
    return result


def _run_film(lip_sync: bool):
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
            "lip_sync": lip_sync,
        })
        if r.status_code != 201:
            return None, [], {}, f"film POST {r.status_code}: {r.text[:200]}"
        job_id = r.json()["job_id"]

        deadline = time.time() + 240
        job = None
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}", headers=H)
            status = (job.json().get("status") or "")
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        body = job.json() if job else {}

        runs = [x for x in studio_service.list_runs("dev-user", limit=200)
                if (x.get("title") or "").startswith("Film")]
        notes = ((body.get("filmPlot") or {}).get("director") or {}).get("notes", [])
        return body, runs, notes, ""


def main() -> int:
    from app.services import studio as studio_service

    def check(name, cond, extra=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
        if not cond:
            FAILURES.append(name)

    print("acting scenes (lip_sync = True, default):")
    studio_service._memory_store.clear()
    body, runs, notes, err = _run_film(True)
    status = (body or {}).get("status")
    check("film completed", status == "completed", err or str(status) + " " + str((body or {}).get("error", "")))
    if status == "completed":
        lip = [x for x in runs if x.get("tool") == "lipsync"]
        # Per acting scene: 1 portrait lock + 1 img2video + 1 lipsync, plus the
        # 2 txt2img frames the two scenes start from.
        check("2 acting scenes x 4 runs = 8 studio runs", len(runs) == 8, str(len(runs)))
        check("one lipsync run per dialogue scene", len(lip) == 2,
              str([(x.get("tool"), x.get("status")) for x in runs]))
        check("all runs completed", all(x.get("status") == "completed" for x in runs),
              str([(x.get("tool"), x.get("status")) for x in runs]))
        check("lipsync run consumed a video + audio ref",
              all(any(r.get("role") == "video" for r in x.get("refs") or []) and
                  any(r.get("role") == "audio" for r in x.get("refs") or []) for x in lip),
              str([x.get("refs") for x in lip]))
        check("director notes record talking scenes",
              any("talking scene" in n for n in notes), str(notes))
        out = (body or {}).get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"),
              str(out.get("storagePath")))

    print("classic still scenes (lip_sync = False):")
    studio_service._memory_store.clear()
    body, runs, notes, err = _run_film(False)
    status = (body or {}).get("status")
    check("film completed", status == "completed", err or str(status) + " " + str((body or {}).get("error", "")))
    if status == "completed":
        lip = [x for x in runs if x.get("tool") == "lipsync"]
        # Lip sync off: each scene is a still + a motion clip. The portrait
        # locks are NOT repeated — the film above already locked Ava and Kai in
        # the shared character library, so this film reuses those faces and
        # spends no run on them.
        portraits = [x for x in runs if x.get("tool") == "txt2img"]
        check("2 scenes x 2 runs = 4 studio runs (portraits reused, no acting passes)",
              len(runs) == 4, str(len(runs)))
        check("no portrait runs — the earlier film's lock is reused",
              len(portraits) == 0,
              str([(x.get("tool"), x.get("title")) for x in portraits]))
        check("scenes were still restyled from those portraits",
              any("restyled from portrait" in n for n in notes), str(notes))
        check("no lipsync runs when disabled", len(lip) == 0,
              str([(x.get("tool"), x.get("status")) for x in runs]))
        check("no talking-scene notes", all("talking scene" not in n for n in notes), str(notes))

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL ACTING TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
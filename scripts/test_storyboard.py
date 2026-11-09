"""Tests for multi-scene storyboards and multi-character dialogue.

Part 1: pure unit tests (dialogue parsing, storyboard orchestration).
Part 2: API validation via an in-process TestClient (worker submit stubbed).

Run:  STORAGE_ROOT=storage/teststoryboard APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_storyboard.py
"""

import os
import sys

os.environ.setdefault("STORAGE_ROOT", "storage/teststoryboard")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        FAILURES.append(name)


def test_parse_dialogue():
    print("dialogue parsing:")
    from pipeline.narration import parse_dialogue

    voices = {"ada": "ada.onnx", "rex": "rex.onnx"}

    v, text = parse_dialogue("Ada: We have to move now.", voices)
    check("known speaker uses their voice", v == "ada.onnx" and text == "We have to move now.", f"{v} {text!r}")

    v, text = parse_dialogue("Rex: Copy that.", voices)
    check("second speaker", v == "rex.onnx" and text == "Copy that.")

    v, text = parse_dialogue("The narrator sets the scene.", voices)
    check("plain narration keeps default", v is None and text == "The narrator sets the scene.")

    v, text = parse_dialogue("Unknown: hello there.", voices)
    check("unknown speaker keeps default + text", v is None and text == "Unknown: hello there.")

    v, text = parse_dialogue("Ada: We have to move now.", {})
    check("no characters => no parsing", v is None and text == "Ada: We have to move now.")


def test_generate_storyboard_orchestration():
    print("storyboard orchestration:")
    import tempfile
    from dataclasses import replace

    import pipeline.storyboard as sb
    from pipeline.config import RenderSettings

    captured = []
    concat_calls = []
    hook_calls = []
    music_calls = []

    def fake_generate_video(settings, whisper_model=None, on_progress=None, on_stage=None):
        captured.append(settings)
        with open(settings.output_path, "wb") as f:
            f.write(b"scene")
        return settings.output_path

    def fake_concat(files, output, temp_dir):
        concat_calls.append(list(files))
        with open(output, "wb") as f:
            f.write(b"concat")
        return output

    def fake_hook(settings):
        hook_calls.append(settings)
        p = os.path.join(settings.temp_dir, "hook_av.mp4")
        with open(p, "wb") as f:
            f.write(b"hook")
        return p

    def fake_music(video, music, output, volume):
        music_calls.append((video, music, volume))
        with open(output, "wb") as f:
            f.write(b"music")
        return output

    sb.generate_video = fake_generate_video
    sb._concat = fake_concat
    sb._hook_with_audio = fake_hook
    sb._mix_music = fake_music

    tmp = tempfile.mkdtemp(prefix="sb_test_")
    music_file = os.path.join(tmp, "song.mp3")
    with open(music_file, "wb") as f:
        f.write(b"music")
    base = RenderSettings(
        script="ignored",
        voice_path="base.onnx",
        hook=True,
        music_path=music_file,
        music_volume=0.8,
        captions=False,
        temp_dir=os.path.join(tmp, "tmp"),
        output_path=os.path.join(tmp, "final.mp4"),
    )
    scenes = [
        {"script": "Ada: Move.", "voice_path": "a.onnx", "media_files": ["m1.png"], "transition": "Fade"},
        {"script": "Narration here.", "voice_path": "b.onnx", "media_files": ["m2.png"], "dialogue_voices": {"rex": "r.onnx"}},
    ]

    out = sb.generate_storyboard(scenes, base)

    check("renders one video per scene", len(captured) == 2, str(len(captured)))
    check("scene 1 script used", captured[0].script == "Ada: Move.")
    check("scene 1 voice used", captured[0].voice_path == "a.onnx")
    check("scene 1 transition used", captured[0].transition == "Fade")
    check("scene 2 dialogue voices propagated", captured[1].dialogue_voices == {"rex": "r.onnx"})
    check("scene 2 default voice", captured[1].voice_path == "b.onnx")
    check("hook disabled per scene", all(c.hook is False for c in captured))
    check("music disabled per scene", all(c.music_path is None for c in captured))
    check("scene output paths unique", len({c.output_path for c in captured}) == 2)
    check("hook rendered once for final cut", len(hook_calls) == 1, str(len(hook_calls)))
    check("hook prepended to concat", concat_calls and len(concat_calls[0]) == 3, str(concat_calls))
    check("music mixed into final", len(music_calls) == 1, str(len(music_calls)))
    check("music volume forwarded", music_calls[0][2] == 0.8, str(music_calls[0][2]))
    check("final output exists", os.path.isfile(out), out)


def test_api_validation():
    print("API validation:")
    import io

    from starlette.testclient import TestClient

    from app.main import app
    from app.workers.video_worker import VideoWorker

    async def noop_submit(self, job_id):
        return None

    VideoWorker.submit = noop_submit

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    with TestClient(app) as client:
        png = _tiny_png()
        r = client.post("/api/uploads", headers=H, files={"file": ("a.png", png, "image/png")})
        check("upload ok", r.status_code == 201, r.text[:200])
        path = r.json()["items"][0]["storagePath"]

        r = client.post(
            "/api/jobs",
            headers=H,
            json={
                "title": "Two scenes",
                "script": "",
                "settings": {"voice": "en_US-lessac-medium"},
                "scenes": [
                    {"script": "Ada: hello.", "voice": "en_US-lessac-medium", "media": [{"path": path}]},
                    {"script": "Bye.", "media": [{"path": path}]},
                ],
                "characters": [
                    {"name": "Ada", "voice": "en_US-lessac-medium"},
                    {"name": "Rex", "voice": "en_US-lessac-medium"},
                ],
            },
        )
        check("storyboard job 201", r.status_code == 201, r.text[:200])
        job_id = r.json().get("job_id")
        job = client.get(f"/api/jobs/{job_id}", headers=H).json()
        check("stored type=storyboard", job.get("type") == "storyboard", str(job.get("type")))
        check("stored 2 scenes", len(job.get("scenes") or []) == 2)
        check("stored 2 characters", len(job.get("characters") or []) == 2)
        check("script derived from scenes", "Ada: hello." in (job.get("script") or ""), job.get("script", ""))

        r = client.post(
            "/api/jobs",
            headers=H,
            json={"script": "x", "scenes": [{"script": "no media", "media": []}]},
        )
        check("scene without media rejected", r.status_code == 400, str(r.status_code))

        r = client.post(
            "/api/jobs",
            headers=H,
            json={
                "script": "x",
                "scenes": [{"script": "s", "media": [{"path": path}]}],
                "characters": [{"name": "Ada", "voice": ""}],
            },
        )
        check("character without voice rejected", r.status_code == 400, str(r.status_code))

        r = client.post(
            "/api/jobs",
            headers=H,
            json={"script": "normal single video", "media": [{"path": path}]},
        )
        check("plain job still works", r.status_code == 201, r.text[:200])


def test_director_save_back():
    """The exact payload Storyboard sends when saving edits back.

    Storyboard only models the script, setting, shot and transition, so it
    re-reads the stored plan and overlays just those. This pins the two halves
    of that contract: the edited scene comes back split correctly, and every
    field Storyboard knows nothing about survives the round trip.
    """
    print("director save-back:")
    import json

    from starlette.testclient import TestClient

    from app.main import app
    from app.services import planner

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    original = {
        "title": "Gamecocks",
        "style": "cinematic",
        "instructions": "Keep it trophy-focused.",
        "pacing": "brisk",
        "show_page_name": True,
        "characters": [
            {"name": "Dawn Staley", "description": "Head coach.", "voice": "en-GB-SoniaNeural"}
        ],
        "scenes": [
            {"narration": "The arena glows.", "dialogue": {"Dawn Staley": "Old line."},
             "setting": "Colonial Life Arena", "shot": "wide", "transition": "fade"},
            {"narration": "Untouched second scene.", "dialogue": {},
             "setting": "A court at night", "shot": "push in", "transition": "zoomin"},
        ],
    }

    with TestClient(app) as client:
        # Creating a project plans it immediately, so the LLM is stubbed out:
        # first the conversational turn, then the production package itself.
        # Only the save-back contract is under test here.
        scripted = [
            {
                "reply": "Here's the plan.",
                "questions": [],
                "brief": {"logline": "Gamecocks win it all."},
                "ready": True,
                "revise": False,
            },
            original,
        ]
        original_call = planner._call
        planner._call = lambda system, messages, timeout: scripted.pop(0)
        try:
            r = client.post("/api/films/projects", headers=H,
                            json={"message": "Gamecocks", "aspect": "16:9"})
            check("project created for save-back", r.status_code == 201, r.text[:200])
            pid = r.json()["project"]["projectId"]
        finally:
            planner._call = original_call

        r = client.get(f"/api/films/projects/{pid}", headers=H)
        stored = r.json()["project"]["plan"]
        check("director plan seeded", len(stored["scenes"]) == 2
              and stored["instructions"] == "Keep it trophy-focused.", str(stored)[:160])

        # What the Storyboard merge emits: scene 1 rewritten as a combined
        # `script` (the validator re-splits it), scene 2 passed through as-is.
        merged = {
            "title": "Gamecocks",
            "style": original["style"],
            "instructions": original["instructions"],
            "pacing": original["pacing"],
            "show_page_name": original["show_page_name"],
            "characters": original["characters"],
            "scenes": [
                {"script": "EDITED narration.\nDawn Staley: New line.",
                 "setting": "Colonial Life Arena", "shot": "slow push in", "transition": "fade"},
                {"narration": "Untouched second scene.", "dialogue": {},
                 "setting": "A court at night", "shot": "push in", "transition": "zoomin"},
            ],
        }
        r = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": merged})
        check("save-back accepted -> 200", r.status_code == 200, r.text[:250])
        plan = r.json()["project"]["plan"]

        check("edited narration split out", plan["scenes"][0]["narration"] == "EDITED narration.",
              repr(plan["scenes"][0]["narration"]))
        check("edited dialogue split out",
              plan["scenes"][0]["dialogue"] == {"Dawn Staley": "New line."},
              str(plan["scenes"][0]["dialogue"]))
        check("edited shot saved", plan["scenes"][0]["shot"] == "slow push in",
              plan["scenes"][0]["shot"])
        check("untouched scene intact",
              plan["scenes"][1]["narration"] == "Untouched second scene."
              and plan["scenes"][1]["shot"] == "push in",
              str(plan["scenes"][1]))
        check("director notes survived", plan["instructions"] == "Keep it trophy-focused."
              and plan["style"] == "cinematic", str(plan.get("instructions")))
        check("pacing survived", plan.get("pacing") == "brisk", str(plan.get("pacing")))
        # Save-back used to send a hand-maintained field list that omitted this
        # one, so every save silently switched the page label off.
        check("show_page_name survived", plan.get("show_page_name") is True,
              str(plan.get("show_page_name")))
        check("character description survived",
              plan["characters"][0]["description"] == "Head coach."
              and plan["characters"][0]["voice"] == "en-GB-SoniaNeural",
              str(plan["characters"][0]))

        # The delete-all save-back: Storyboard sends a plan whose scenes are
        # empty (the last imported scene was removed). This must clear the plan
        # — keeping the global keys — rather than fail with "at least one scene".
        cleared = {k: v for k, v in merged.items() if k != "scenes"}
        cleared["scenes"] = []
        r = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": cleared})
        check("cleared plan accepted -> 200", r.status_code == 200, r.text[:240])
        cleared_plan = r.json()["project"]["plan"]
        check("cleared plan wipes scenes",
              cleared_plan.get("scenes") == [], str(cleared_plan.get("scenes")))
        check("cleared plan keeps globals",
              cleared_plan.get("show_page_name") is True
              and cleared_plan.get("pacing") == "brisk",
              str(cleared_plan.get("show_page_name")))
        # Restore the two-scene plan so the checks below exercise a full plan.
        restore = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": merged})
        check("full plan restored", restore.status_code == 200, restore.text[:160])

        # A cleared setting would fail validation for the whole plan, which is
        # why the page checks before it ever calls this.
        bad = {**merged, "scenes": [dict(merged["scenes"][0], setting=""), merged["scenes"][1]]}
        r = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": bad})
        check("scene missing setting rejected -> 422", r.status_code == 422, str(r.status_code))
        check("rejected save did not corrupt the plan",
              client.get(f"/api/films/projects/{pid}", headers=H).json()["project"]["plan"]["scenes"][0]["setting"]
              == "Colonial Life Arena")

        # Once the project has been filmed it is locked: a plan PATCH would
        # reset status to `planned` while leaving `jobId` set, detaching the
        # finished film. The server must refuse rather than corrupt it.
        # Film it for real: the render link now requires a job that exists and
        # belongs to this user.
        import base64
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )
        up = client.post("/api/uploads", headers=H, files={"file": ("a.png", png, "image/png")})
        check("upload for render link ok", up.status_code == 201, up.text[:160])
        media_path = up.json()["items"][0]["storagePath"]
        r_job = client.post("/api/jobs", headers=H, json={
            "title": "Gamecocks film", "script": "The arena glows.", "media": [media_path],
        })
        check("job for render link created", r_job.status_code == 201, r_job.text[:160])
        job_id = r_job.json()["job_id"]

        check("unknown job id refused",
              client.post(f"/api/films/projects/{pid}/render", headers=H,
                          json={"job_id": "does-not-exist"}).status_code == 404)
        linked = client.post(f"/api/films/projects/{pid}/render", headers=H, json={"job_id": job_id})
        check("render link saved", linked.status_code == 200
              and linked.json()["project"]["jobId"] == job_id, linked.text[:160])

        # Snapshot the plan as it stood when the film was made, so the refused
        # save can be shown to leave it byte-for-byte alone.
        at_film = client.get(f"/api/films/projects/{pid}", headers=H).json()["project"]
        filmed = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": merged})
        check("save-back on filmed project refused -> 409", filmed.status_code == 409,
              str(filmed.status_code) + " " + filmed.text[:160])
        check("refusal explains why", "already been filmed" in str(filmed.json().get("detail", "")),
              str(filmed.json().get("detail"))[:120])

        after = client.get(f"/api/films/projects/{pid}", headers=H).json()["project"]
        check("filmed project still filmed", after["status"] == "filmed", str(after["status"]))
        check("film link survived", after["jobId"] == job_id, str(after.get("jobId")))
        check("refused save left the plan untouched", after["plan"] == at_film["plan"],
              str(after["plan"])[:160])


def _tiny_png() -> bytes:
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def main() -> int:
    test_parse_dialogue()
    test_generate_storyboard_orchestration()
    test_api_validation()
    test_director_save_back()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL STORYBOARD TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

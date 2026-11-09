"""Tests for Auto Film: scriptwriting + the full fake-mode film pipeline.

Part 1: scriptwriter unit tests (template + LLM-output normalization + voices).
Part 2: end-to-end film in fake-GPU mode through an in-process TestClient
        (real workers: template writer → studio txt2img/img2video → storyboard
        render, all offline).

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testfilm RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_film.py
"""

import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testfilm")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        FAILURES.append(name)


def test_template_writer():
    print("template writer:")
    from app.config import Settings
    from app.services import scriptwriter

    s = Settings()
    out = scriptwriter.write_film_script(
        "a robot learns to bake bread", "comedy", 4, [], s
    )
    check("returns title", bool(out.get("title")), out.get("title", ""))
    check("creates characters", len(out.get("characters") or []) >= 2)
    check("creates 4 scenes", len(out.get("scenes") or []) == 4, str(len(out.get("scenes") or [])))
    for i, sc in enumerate(out["scenes"]):
        check(f"scene {i + 1} has narration", bool(sc.get("narration")))
        check(f"scene {i + 1} has setting", bool(sc.get("setting")))
        check(f"scene {i + 1} has shot", bool(sc.get("shot")))
    check("dialogue speakers are known characters",
          all(n.lower() in {c["name"].lower() for c in out["characters"]}
              for sc in out["scenes"] for n in (sc.get("dialogue") or {}).keys()))

    out2 = scriptwriter.write_film_script("x", "unknown-style", 20, [], s)
    check("unknown style falls back", out2.get("style") == "cinematic")
    check("scene count clamped to film_max_scenes",
          len(out2.get("scenes") or []) <= s.film_max_scenes,
          str(len(out2.get("scenes") or [])))


def test_llm_outline_normalization():
    print("LLM outline normalization:")
    from app.services import scriptwriter

    raw = {
        "title": "  The Signal  ",
        "characters": [
            {"name": "Ava", "description": "a girl with a red coat"},
            {"name": "Ava", "description": "dup"},
            {"name": "Ghost!", "description": "not allowed name"},
        ],
        "scenes": [
            {
                "narration": "Night falls. ",
                "dialogue": {"Ava": "Can you hear it?", "Wrong": "spam"},
                "setting": "  neon street  ",
                "shot": "  slow push-in  ",
            },
            {"narration": "The end."},
        ],
    }
    out = scriptwriter._normalize_outline(raw, "signal", "sci-fi", 8, [])
    check("title cleaned", out["title"] == "The Signal", out["title"])
    check("duplicate characters dropped", len(out["characters"]) == 2, str(out["characters"]))
    check("dialogue filtered to known speakers", out["scenes"][0]["dialogue"] == {"Ava": "Can you hear it?"})
    check("scene 1 setting cleaned", out["scenes"][0]["setting"] == "neon street")

    out2 = scriptwriter._normalize_outline(raw, "x", "sci-fi", 1, [])
    check("scene count capped", len(out2["scenes"]) == 1)


def test_assign_voices():
    print("voice assignment:")
    from app.services import scriptwriter

    out = {
        "title": "t",
        "characters": [{"name": "Ava"}, {"name": "Kai"}],
        "scenes": [],
    }
    voiced = scriptwriter.assign_voices(out, ["en_US-a", "en_US-b"])
    check("every character gets a voice",
          all(c.get("voice") for c in voiced["characters"]))
    check("distinct voices for distinct characters",
          voiced["characters"][0]["voice"] != voiced["characters"][1]["voice"])


def test_character_consistency_helpers():
    print("character consistency helpers:")
    from app.routes.films import _adopt_saved_cast
    from app.services import characters as characters_service
    from app.services import scriptwriter
    from app.workers.film_worker import _scene_character, adopt_library_identity

    check("writer told to spell out on-screen character names",
          "exact name" in scriptwriter._SCHEMA_GUIDE)

    chars = [{"name": "Ava"}, {"name": "Kai"}]
    both = {"dialogue": {"Ava": "hi", "Kai": "yo"}, "narration": "", "setting": "", "shot": ""}
    check("two-speaker scene resolves to a cast member",
          (_scene_character(both, chars) or {}).get("name") in {"Ava", "Kai"},
          str(_scene_character(both, chars)))
    second = {"dialogue": {"Kai": "yo"}, "narration": "", "setting": "", "shot": ""}
    check("second character matches on their own line",
          (_scene_character(second, chars) or {}).get("name") == "Kai")
    unknown = {"dialogue": {"Zed": "?"}, "narration": "", "setting": "", "shot": ""}
    check("unknown speaker matches nothing", _scene_character(unknown, chars) is None)
    narrated = {"dialogue": {}, "narration": "Kai steps forward.", "setting": "", "shot": ""}
    check("narration mention still matches",
          (_scene_character(narrated, chars) or {}).get("name") == "Kai")

    uid = "dev-user"
    characters_service.save_character(
        uid, {"name": "Maya", "description": "green jacket", "voice": "en_US-amy"}
    )
    cast = [{"name": "maya", "description": "", "voice": ""}]
    _adopt_saved_cast(uid, "", cast)
    check("blank description filled from the library",
          cast[0].get("description") == "green jacket", str(cast))
    check("blank voice filled from the library",
          cast[0].get("voice") == "en_US-amy", str(cast))

    named = []
    _adopt_saved_cast(uid, "Maya races through the night market", named)
    check("saved name mentioned in the idea joins the cast",
          [c.get("name") for c in named] == ["Maya"], str(named))
    check("joined character carries the saved identity",
          bool(named) and named[0].get("description") == "green jacket"
          and named[0].get("voice") == "en_US-amy", str(named))
    other = []
    _adopt_saved_cast(uid, "a story about bicycles", other)
    check("idea without saved names adds nothing", other == [])

    outline = {"characters": [{"name": "Maya", "description": "", "voice": "en_US-x"}]}
    adopt_library_identity(outline, uid, set())
    check("undeclared writer name adopts the saved voice",
          outline["characters"][0].get("voice") == "en_US-amy", str(outline))
    outline2 = {"characters": [
        {"name": "Maya", "description": "custom", "voice": "clone:media/dev-user/v.wav"}
    ]}
    adopt_library_identity(outline2, uid, {"maya"})
    check("declared pick is left alone",
          outline2["characters"][0].get("voice") == "clone:media/dev-user/v.wav")
    check("declared description is left alone",
          outline2["characters"][0].get("description") == "custom")

    characters_service.save_character(
        uid, {"name": "Rex", "description": "old dog", "voice": "clone:media/someone-else/v.wav"}
    )
    outline3 = {"characters": [{"name": "Rex", "description": "", "voice": ""}]}
    adopt_library_identity(outline3, uid, set())
    check("foreign clone voice is not adopted",
          not outline3["characters"][0].get("voice"), str(outline3))
    check("description still fills for that character",
          outline3["characters"][0].get("description") == "old dog")


def test_full_film_fake_mode():
    print("full fake-mode film flow:")
    import wave

    import pipeline.narration as narration
    from pipeline.utils import run_cmd as _std_run_cmd

    # Piper TTS is very slow on this machine; stub it with 1s silent WAVs so the
    # rest of the pipeline (ffmpeg visuals/audio/concat) runs at full speed.
    _orig_run_cmd = narration.run_cmd

    def fast_tts_run_cmd(command, input_text=None):
        if command and command[0] == "piper":
            out = None
            for i, arg in enumerate(command):
                if arg == "--output_file":
                    out = command[i + 1]
                    break
            if out:
                with wave.open(out, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(24000)
                    w.writeframes(b"\x00\x00" * 24000)  # 1s silent narration
                return None
        return _orig_run_cmd(command, input_text)

    narration.run_cmd = fast_tts_run_cmd

    from starlette.testclient import TestClient

    from app.main import app

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    with TestClient(app) as client:
        r = client.post(
            "/api/films",
            headers=H,
            json={
                "idea": "A cat discovers a piano that plays by itself every midnight",
                "style": "fantasy",
                "scenes_count": 2,
                "captions": False,
                "music": "",
            },
        )
        check("film job created (201)", r.status_code == 201, r.text[:300])
        if r.status_code != 201:
            return
        job_id = r.json()["job_id"]

        # Poll until the worker finishes (template writer → fake studio runs →
        # storyboard render). The budget is overridable because this is a real
        # render on a 4-core CPU host; on a busy or slower machine 240s is not
        # enough and the test fails on the clock rather than on behaviour.
        budget = float(os.environ.get("FILM_TEST_TIMEOUT", "600"))
        deadline = time.time() + budget
        job = None
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}", headers=H)
            status = (job.json().get("status") or "")
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        body = job.json() if job else {}
        check("film job completed", body.get("status") == "completed", str(body.get("status")) + " " + str(body.get("error", "")))
        if body.get("status") != "completed":
            return

        check("job type is film", body.get("type") == "film", str(body.get("type")))
        check("script was auto-written", bool(body.get("script")), body.get("script", "")[:60])
        check("outline stored on job", bool(body.get("filmPlot")))
        scenes = (body.get("filmPlot") or {}).get("scenes") or []
        check("outline has 2 scenes", len(scenes) == 2, str(len(scenes)))
        check("outline characters have voices",
              all((c.get("voice") or "") for c in (body.get("filmPlot") or {}).get("characters") or []))

        out = body.get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"), str(out.get("storagePath")))
        check("output duration present", bool(out.get("duration")), str(out.get("duration")))

        runs = client.get("/api/studio/runs", headers=H).json()
        film_runs = [r for r in runs if (r.get("title") or "").startswith("Film")]
        # 1 acting scene + 1 still scene: 4 txt2img (frame, portrait lock and
        # their retries included), 2 img2video, 1 lipsync, 1 character lock.
        check("created 8 studio runs (1 acting scene, 1 still scene)",
              len(film_runs) == 8, str(len(film_runs)))
        check("all film runs completed", all(r.get("status") == "completed" for r in film_runs),
              str([(r.get("tool"), r.get("status")) for r in film_runs]))
        tools = sorted({r.get("tool") for r in film_runs})
        check("used character + txt2img + img2video + lipsync",
              tools == ["character", "img2video", "lipsync", "txt2img"], str(tools))


def main() -> int:
    test_template_writer()
    test_llm_outline_normalization()
    test_assign_voices()
    test_character_consistency_helpers()
    test_full_film_fake_mode()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL FILM TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
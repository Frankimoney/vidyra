"""Tests for the production-package feature: import a full package (JSON or
plain text) generated in ChatGPT and have the film agent execute it verbatim.

Part 1: package service units (tolerant JSON parse, validation, offline
        screenplay text parser, voice preservation, Gemini grounding mock).
Part 2: endpoints — POST /api/films/validate-package (zero-GPU preview) and
        POST /api/films with `outline`, executing the package end-to-end in
        fake-GPU mode (real workers, offline).

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testpackage RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_package.py
"""

import json
import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testpackage")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []

GOOD_PACKAGE = {
    "title": "The Gamecock Run",
    "style": "documentary",
    "instructions": "Keep it proud and factually accurate.",
    "characters": [
        {"name": "Narrator", "description": "a calm sports commentator",
         "voice": "en_US-ryan-medium"},
        {"name": "Jae", "description": "a determined young coach"},
    ],
    "scenes": [
        {
            "narration": "The run began on a Tuesday nobody expected.",
            "dialogue": {"Jae": "We weren't ready — so we got ready."},
            "setting": "packed arena in garnet, spotlights, 9:16 vertical",
            "shot": "slow push-in from the baseline",
            "transition": "fade",
        },
        {
            "narration": "College basketball rewards the bold.",
            "dialogue": {},
            "setting": "locker room, dim light, close-up",
            "shot": "dramatic low angle",
            "transition": "zoomin",
        },
    ],
}

TEXT_PACKAGE = """Title: The Gamecock Run
Style: documentary
Instructions: Keep it proud and accurate.
Characters:
Narrator: a calm sports commentator
Jae: a determined young coach
Scene 1
Setting: packed arena in garnet, spotlights, 9:16
Shot: slow push-in from the baseline
Jae: We weren't ready - so we got ready.
The run began on a Tuesday nobody expected.
Scene 2: The huddle
Setting: locker room, dim light, close-up
Camera angle: dramatic low angle
Narrator: College basketball rewards the bold.
"""


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        FAILURES.append(name)


def test_package_units():
    print("package service units:")
    from app.services import package as p

    out = p.normalize_package(GOOD_PACKAGE)
    check("title & style kept", out["title"] == "The Gamecock Run" and out["style"] == "documentary")
    check("voices preserved", out["characters"][0]["voice"] == "en_US-ryan-medium")
    check("scenes normalized (2)", len(out["scenes"]) == 2, str(len(out["scenes"])))
    s1 = out["scenes"][0]
    check("scene1 dialogue kept", s1["dialogue"].get("Jae", "").startswith("We weren't ready"))
    check("scene1 narration kept", "Tuesday nobody expected" in s1["narration"])
    check("scene1 shot kept", s1["shot"] == "slow push-in from the baseline")
    check("no issues", True)

    try:
        p.normalize_package({"characters": [{"name": "N"}], "scenes": [
            {"narration": "n", "dialogue": {"Zed": "hi"}, "setting": "x"},
            {"narration": "y", "setting": "z"},
        ]})
        check("unknown speaker rejected", False)
    except p.PackageError as exc:
        check("unknown speaker rejected", any("Zed" in i for i in exc.issues), str(exc.issues))
        check("partial outline recovered", bool(exc.partial and exc.partial["scenes"]))

    fenced = "ChatGPT said:\n```json\n" + json.dumps(GOOD_PACKAGE) + "\n```\nHope that helps!"
    check("fenced JSON compiles", p.compile_package(fenced)["title"] == "The Gamecock Run")

    text = p.compile_package(TEXT_PACKAGE)
    check("text package compiles (offline parser)", text["title"] == "The Gamecock Run",
          text["title"])
    check("text camera angle parsed", text["scenes"][1]["shot"] == "dramatic low angle",
          text["scenes"][1]["shot"])
    check("text dialogue parsed", "We weren't ready" in (text["scenes"][0]["dialogue"].get("Jae") or ""))

    try:
        p.compile_package("just a short idea sentence")
        check("short idea rejected as package", False)
    except p.PackageError:
        check("short idea rejected as package", True)

    from app.services.scriptwriter import ensure_voices
    av = [os.path.splitext(os.path.basename(v))[0] for v in ["en_US-a-medium.onnx"]]
    voiced = ensure_voices(
        {"characters": [{"name": "N", "voice": "en_US-ryan-medium"}, {"name": "J"}]},
        av + ["en_US-ryan-medium"],
    )
    check("specified voice kept only if available",
          voiced["characters"][0]["voice"] == "en_US-ryan-medium"
          and voiced["characters"][1]["voice"] == "en_US-a-medium",
          str(voiced["characters"]))


def test_gemini_mock():
    print("Gemini grounding (mocked):")
    import app.services.gemini as gemini_module
    from app.config import Settings
    from app.services import scriptwriter

    original = gemini_module._request
    captured = {}

    def fake_request(api_key, model, base_url, system, user, use_search, timeout):
        captured["use_search"] = use_search
        captured["tool"] = use_search
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({
                        "title": "Grounded Film",
                        "characters": [{"name": "Ria", "description": "a reporter"}],
                        "scenes": [
                            {"narration": "verified fact one.", "dialogue": {}, "setting": "a court", "shot": "wide"},
                            {"narration": "verified fact two.", "dialogue": {}, "setting": "a press room", "shot": "slow in"},
                        ],
                    })}]},
                    "groundingMetadata": {
                        "groundingChunks": [{"web": {"title": "Box Score", "uri": "https://example.com/box"}}]
                    },
                }
            ]
        }

    gemini_module._request = fake_request
    try:
        outline, sources = gemini_module.gemini_json("k", "m", "b", "s", "u", use_search=True)
        check("request used google search grounding", captured.get("use_search") is True)
        check("sources extracted", sources == [{"title": "Box Score", "url": "https://example.com/box"}],
              str(sources))

        s = Settings()
        s.gemini_api_key = "k"
        film = scriptwriter.write_film_script("test", "cinematic", 2, [], s, research=True)
        check("write_film_script grounded via gemini", str(film.get("title")) == "Grounded Film",
              str(film.get("title")))
        check("sources attached to outline", (film.get("sources") or [])[0]["url"] == "https://example.com/box")
    finally:
        gemini_module._request = original


def test_write_without_keys_falls_back():
    print("fallback without keys:")
    from app.config import Settings
    from app.services import scriptwriter

    s = Settings()  # keys popped at import; research on but nothing configured
    check("no gemini configured", not s.gemini_configured)
    check("no llm configured", not s.llm_configured)
    film = scriptwriter.write_film_script("a robot learns to bake bread", "cinematic", 3, [], s, research=True)
    check("film still written from templates", len(film.get("scenes") or []) >= 2,
          str(len(film.get("scenes") or [])))
    check("no sources without search", not film.get("sources"))
    pod = scriptwriter.write_podcast_script("sleep science", [], 4, s, research=True)
    check("podcast still written from templates", len(pod.get("turns") or []) >= 2)


def test_validate_endpoint():
    print("POST /api/films/validate-package:")
    from starlette.testclient import TestClient

    from app.main import app

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    with TestClient(app) as client:
        r = client.post("/api/films/validate-package", headers=H,
                        json={"text": json.dumps(GOOD_PACKAGE)})
        check("good JSON package → 200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("valid flag", body.get("valid") is True)
        check("preview counts", body.get("scenesCount") == 2 and body.get("charactersCount") == 2,
              str((body.get("scenesCount"), body.get("charactersCount"))))
        # Charged from the worker's own formula: 2 runs per scene (still +
        # motion) + 1 portrait per character + 1 acting/lip-sync pass per
        # dialogue scene + 1 per cloned voice. GOOD_PACKAGE has 2 scenes (only
        # one carries dialogue) and 2 characters, no clones -> 7.
        check("runs estimate matches the worker's formula",
              body.get("runsNeeded") == 2 * 2 + 2 + 1, str(body.get("runsNeeded")))
        check("no issues", body.get("issues") == [])

        bad = dict(GOOD_PACKAGE)
        bad["scenes"] = [
            {"narration": "only one scene", "setting": "x"},
        ]
        r2 = client.post("/api/films/validate-package", headers=H,
                         json={"text": json.dumps(bad)})
        b2 = r2.json()
        check("too-few-scenes flagged", b2.get("valid") is False and any("at least 2" in i.lower() for i in b2.get("issues", [])),
              str(b2.get("issues")))

        r3 = client.post("/api/films/validate-package", headers=H, json={"text": TEXT_PACKAGE})
        b3 = r3.json()
        check("plain-text package validates (offline parser)", b3.get("valid") is True,
              str(b3.get("issues"))[:200])
        check("text package preview title", b3.get("title") == "The Gamecock Run", str(b3.get("title")))


def test_full_package_fake_mode():
    print("full fake-mode package film:")
    import wave

    from pipeline.utils import run_cmd as _std_run_cmd

    import pipeline.narration as narration

    _orig_narr_run = narration.run_cmd

    def fast_piper(command, input_text=None):
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
        return _std_run_cmd(command, input_text)

    narration.run_cmd = fast_piper
    import app.workers.film_worker as film_worker

    film_worker.run_cmd = fast_piper

    from starlette.testclient import TestClient

    from app.main import app

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    with TestClient(app) as client:
        r = client.post("/api/films", headers=H, json={
            "idea": "",
            "outline": json.dumps(GOOD_PACKAGE),
            "style": "cinematic",
            "scenes_count": 8,
            "characters": [],
            "captions": False,
            "music": "",
            "research": True,
        })
        check("package film job created (201)", r.status_code == 201, r.text[:300])
        if r.status_code != 201:
            return
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
        check("package film completed", body.get("status") == "completed",
              str(body.get("status")) + " " + str(body.get("error", "")))
        if body.get("status") != "completed":
            return

        plot = body.get("filmPlot") or {}
        check("job titled from package", body.get("title") == "The Gamecock Run",
              str(body.get("title")))
        check("exactly the package's scenes executed (2)",
              len(plot.get("scenes") or []) == 2, str(len(plot.get("scenes") or [])))
        s1 = (plot.get("scenes") or [{}])[0]
        check("scene1 dialogue verbatim", s1.get("dialogue", {}).get("Jae", "").startswith("We weren't ready"))
        check("scene1 shot verbatim", s1.get("shot") == "slow push-in from the baseline")
        check("specified narrator voice kept", (plot.get("characters") or [{}])[0].get("voice") == "en_US-ryan-medium")
        check("all characters voiced", all((c.get("voice") or "") for c in plot.get("characters") or []))

        out = body.get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"),
              str(out.get("storagePath")))
        check("output duration present", bool(out.get("duration")), str(out.get("duration")))

        runs = client.get("/api/studio/runs", headers=H).json()
        film_runs = [x for x in runs if (x.get("title") or "").startswith("Film")]
        # 2 scenes x 2 runs + 1 acting lip-sync + 1 portrait for Jae. The
        # package's Narrator is a voice, never drawn, so it spends no run.
        check("6 GPU runs (1 acting lip-sync scene + 1 portrait; narrator is voice-only)",
              len(film_runs) == 6, str(len(film_runs)))
        check("all film runs completed", all(x.get("status") == "completed" for x in film_runs),
              str([(x.get("tool"), x.get("status")) for x in film_runs]))
        check("acting scene ran a lipsync pass",
              any(x.get("tool") == "lipsync" for x in film_runs),
              str([x.get("tool") for x in film_runs]))


def main() -> int:
    test_package_units()
    test_gemini_mock()
    test_write_without_keys_falls_back()
    test_validate_endpoint()
    test_full_package_fake_mode()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PACKAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""Tests for the Director's pass — the agentic upgrades to Auto Film:

Part 1: review — the director reads the final plan, fixes any scene that can't
        be shot (missing setting, missing camera move, unsupported transition,
        empty script) and records notes.
Part 2: salvage — `_run_studio` retries a failed GPU run with adjusted
        direction (simpler composition, lighter params) before giving up.
Part 3: fact-check — package narration is verified sentence-by-sentence
        against grounded Gemini; wrong facts are corrected and sources kept.
Part 4: end-to-end fake-mode film from a *flawed* package (no camera moves,
        bad transition) — the director fixes it, films, and the job shows its notes.

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testdir RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_director.py
"""

import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testdirector")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []


def check(name, cond, detail=""):
    ok = bool(cond)
    show = str(detail).encode("ascii", "replace").decode()[:200] if detail and not ok else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {name} {('-> ' + show) if show else ''}")
    if not ok:
        FAILURES.append(name)


FLAWED_PACKAGE = {
    "title": "The Midnight Bakery",
    "style": "documentary",
    "characters": [
        {"name": "Narrator", "description": "a warm storyteller"},
    ],
    "scenes": [
        {
            "narration": "The bakeshop opens before the sun.",
            "dialogue": {},
            "setting": "a flour-dusted counter at night, 9:16 vertical",
            "shot": "orbit around the sourdough bowl",
            "transition": "zoom-fade",  # unsupported -> must be coerced to Auto
        },
        {
            "narration": "Flour, water, salt — that's all there is.",
            "dialogue": {},
            "setting": "a warm kitchen at dawn, 9:16 vertical",
            "shot": "",
            "transition": "Auto",
        },
    ],
}


def test_director_review():
    print("director review:")
    from app.workers.film_worker import FilmWorker

    outline = {
        "title": "Nutty",
        "style": "documentary",
        "scenes": [
            {"narration": "A squirrel plots.", "dialogue": {}, "setting": "", "shot": "", "transition": "bogus"},
            {"narration": "The heist.", "dialogue": {}, "setting": "winter park", "shot": "slow push-in", "transition": "fade"},
            {"dialogue": {}, "setting": "treetop", "shot": "", "transition": ""},
        ],
    }
    director = FilmWorker._director_review(outline, {"idea": "a squirrel saves the day", "title": "Nutty"})
    sc0, sc1, sc2 = outline["scenes"]
    check("reviewed flag set", director.get("reviewed") is True)
    check("missing setting written from idea",
          sc0.get("setting") == "a squirrel saves the day, documentary cinematic scene",
          str(sc0.get("setting")))
    check("missing shot taken from fallback cycle",
          sc0.get("shot") == "a slow push-in", str(sc0.get("shot")))
    check("unsupported transition coerced to Auto",
          sc0.get("transition") == "Auto", str(sc0.get("transition")))
    check("valid scene untouched", sc1.get("shot") == "slow push-in" and sc1.get("transition") == "fade")
    check("fallback cycle advances per scene", sc2.get("shot") == "a subtle zoom-in", str(sc2.get("shot")))
    check("empty transition normalized to Auto", sc2.get("transition") == "Auto")
    check("notes recorded all fixes", len(director.get("notes") or []) >= 2, str(director.get("notes")))
    check("fixed count", director.get("fixed") >= 2, str(director.get("fixed")))

    empty = {"title": "Silent", "style": "cinematic", "scenes": [{"setting": "x", "shot": "push"}]}
    FilmWorker._director_review(empty, {"idea": "the quiet valley"})
    check("empty scene narrated from idea",
          "quiet valley" in (empty["scenes"][0].get("narration") or ""),
          str(empty["scenes"][0].get("narration")))


def test_coerce_transition():
    print("transition coercion:")
    from app.workers.film_worker import _coerce_transition
    for raw in ["fade", "slideleft", "wipeRight", "ZOOMIN"]:
        check(f"'{raw}' accepted", _coerce_transition(raw).lower() == raw.lower(),
              _coerce_transition(raw))
    check("'cut' -> Auto", _coerce_transition("cut") == "Auto")
    check("None -> Auto", _coerce_transition(None) == "Auto")


def test_salvage_retries():
    print("salvage retries in _run_studio:")
    import types
    from unittest import mock

    import app.workers.film_worker as fw

    state = {"n": 0, "last_prompt": "", "last_params": {}}

    def fake_create(**kwargs):
        state["n"] += 1
        state["last_prompt"] = kwargs.get("prompt", "")
        state["last_params"] = dict(kwargs.get("params", {}) or {})
        return f"run-{state['n']}"

    def fake_get(run_id):
        if state["n"] >= 3:
            return {"status": "completed", "output": {"primary": {"storagePath": "media/x/final.mp4"}}}
        return {"status": "failed", "error": "boom"}

    reporting = []

    def fake_report(self, job_id, stage, progress, message):
        reporting.append(message)

    worker = fw.FilmWorker.__new__(fw.FilmWorker)
    worker.studio_worker = types.SimpleNamespace(_process=lambda run_id: None)
    worker._report = lambda job_id, stage, progress, message: reporting.append(message)

    with mock.patch.object(fw.studio_service, "create_run", side_effect=fake_create), \
         mock.patch.object(fw.studio_service, "get_run", side_effect=fake_get):
        path = worker._run_studio(
            "j1", "u1", "txt2img", "model-x",
            "a dog", {"width": 512, "height": 512, "steps": 30, "cfg": 7.0},
            [], "test", retries=2,
        )

    check("path recovered after 2 retries", path == "media/x/final.mp4", str(path))
    check("3 tries total (1 + 2 retries)", state["n"] == 3, str(state["n"]))
    check("retry prompt simplified", "simple composition, clear subject" in state["last_prompt"],
          state["last_prompt"])
    check("retry steps scaled down", state["last_params"].get("steps") == 21,
          str(state["last_params"].get("steps")))
    check("retry cfg capped to 5", state["last_params"].get("cfg") == 5.0,
          str(state["last_params"].get("cfg")))
    check("retry reported on job", any("Adju" in m for m in reporting), str(reporting))

    # Give up after exhausting all passes.
    exhausting = mock.MagicMock(return_value={"status": "failed", "error": "no"})
    worker._report = lambda *a, **k: None
    with mock.patch.object(fw.studio_service, "create_run", side_effect=fake_create), \
         mock.patch.object(fw.studio_service, "get_run", side_effect=exhausting):
        try:
            worker._run_studio("j1", "u1", "txt2img", "model-x", "p", dict(steps=30), [], "t", retries=2)
            check("fails only after all passes", False)
        except fw.FilmError as exc:
            check("fails only after all passes", "after 3 passes" in str(exc), str(exc))


def test_fact_check_service():
    print("fact-check service:")
    import json
    from unittest import mock

    import app.services.factcheck as factcheck
    from types import SimpleNamespace

    settings = SimpleNamespace(
        gemini_configured=True,
        gemini_api_key="key",
        gemini_model="gemini-2.5-flash",
        gemini_base_url="https://generativelanguage.googleapis.com",
        gemini_timeout_seconds=30,
    )

    outline = {
        "scenes": [
            {"narration": "He scored 59 points that season.", "setting": "court"},
            {"narration": "The record still stands today.", "setting": "court"},
        ]
    }

    fake = {"sentences": [
        {"text": "He scored 59 points that season.", "verdict": False,
         "correction": "He scored 58 points that season.", "note": "official total is 58"},
        {"text": "The record still stands today.", "verdict": True,
         "note": "confirmed"},
    ]}
    sources = [{"title": "NCAA archive", "url": "https://ncaa.example/58"}]

    with mock.patch.object(factcheck.gemini_service, "gemini_json", return_value=(fake, sources)) as gem:
        report = factcheck.fact_check_outline(outline, settings)

    check("checked flag", report.get("checked") is True)
    check("corrected count = 1", report.get("corrected") == 1, str(report.get("corrected")))
    check("wrong sentence rewritten", outline["scenes"][0]["narration"] == "He scored 58 points that season.",
          outline["scenes"][0]["narration"])
    check("true sentence kept verbatim", outline["scenes"][1]["narration"] == "The record still stands today.")
    check("sources surfaced", any(s["url"].endswith("58") for s in report.get("sources", [])),
          str(report.get("sources"))[:120])
    check("notes mention scene number", any("Scene 1" in n for n in report.get("notes", [])),
          str(report.get("notes")))
    gem.assert_called_once()
    _, kwargs = gem.call_args
    check("search grounding used", kwargs.get("use_search") is True)

    # No key → no-op, never blocks.
    nosettings = SimpleNamespace(gemini_configured=False)
    outline2 = {"scenes": [{"narration": "Whatever.", "setting": "x"}]}
    r2 = factcheck.fact_check_outline(outline2, nosettings)
    check("no key -> not checked, no error", r2.get("checked") is False and r2.get("error") is None)
    check("no key -> text untouched", outline2["scenes"][0]["narration"] == "Whatever.")


def test_full_flawed_package_fake_mode():
    print("full fake-mode film from a flawed package (director fixes it):")
    import json
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
                    w.writeframes(b"\x00\x00" * 24000)
                return None
            return _std_run_cmd(command, input_text)
        if not command:
            return None
        return _std_run_cmd(command, input_text)

    narration.run_cmd = fast_piper
    import app.workers.film_worker as fw

    fw.run_cmd = fast_piper

    from starlette.testclient import TestClient

    from app.main import app

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}

    with TestClient(app) as client:
        r = client.post("/api/films", headers=H, json={
            "idea": "",
            "outline": json.dumps(FLAWED_PACKAGE),
            "style": "cinematic",
            "scenes_count": 8,
            "characters": [],
            "captions": False,
            "music": "",
            "research": True,  # no Gemini key in this env → skip, never blocks
        })
        check("flawed package film created (201)", r.status_code == 201, r.text[:300])
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
        check("film completed", body.get("status") == "completed",
              str(body.get("status")) + " " + str(body.get("error", "")))
        if body.get("status") != "completed":
            return

        plot = body.get("filmPlot") or {}
        director = plot.get("director") or {}
        check("director reviewed the plan", director.get("reviewed") is True, str(director))
        check("director notes persisted on job",
              len(director.get("notes") or []) >= 1, str(director.get("notes")))
        scenes = plot.get("scenes") or []
        check("missing shot written in (fallback)",
              scenes[1].get("shot") == "a gentle pan across", str(scenes[1].get("shot")))
        check("originally-specified shot kept", "sourdough" in (scenes[0].get("shot") or ""))
        check("bad transition coerced", scenes[0].get("transition") == "Auto", str(scenes[0].get("transition")))
        check("fact-check politely skipped without key", director.get("factChecked") is not True, str(director))

        out = body.get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"),
              str(out.get("storagePath")))


def main() -> int:
    test_director_review()
    test_coerce_transition()
    test_salvage_retries()
    test_fact_check_service()
    test_full_flawed_package_fake_mode()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL DIRECTOR TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
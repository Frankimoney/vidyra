"""Tests for AI Podcast: scriptwriter + the full fake-mode podcast pipeline.

Part 1: podcast scriptwriter unit tests (template + LLM normalization + scripts).
Part 2: end-to-end podcast in fake-GPU mode through an in-process TestClient
        (real workers: template writer → per-host portraits → per-turn TTS +
        lip-sync clips → storyboard render, all offline).

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testpodcast RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_podcast.py
"""

import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testpodcast")
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


def test_template_podcast():
    print("template podcast writer:")
    from app.config import Settings
    from app.services import scriptwriter

    s = Settings()
    out = scriptwriter.write_podcast_script("why cats rule the internet", [], 4, s)
    check("returns title", bool(out.get("title")), out.get("title", ""))
    hosts = out.get("characters") or []
    check("creates 2 default hosts", len(hosts) >= 2, str(len(hosts)))
    turns = out.get("turns") or []
    check("creates 4 turns", len(turns) == 4, str(len(turns)))
    names = {h["name"].lower() for h in hosts}
    for i, t in enumerate(turns):
        check(f"turn {i + 1} has a line", bool(t.get("line")))
        check(f"turn {i + 1} speaker is a host", (t.get("speaker") or "").lower() in names)
        check(f"turn {i + 1} alternate speaker",
              (turns[i]["speaker"] != turns[(i + 1) % len(turns)]["speaker"] if len(turns) >= 2 else True))
        check(f"turn {i + 1} has visual", bool(t.get("visual")))

    out2 = scriptwriter.write_podcast_script("t", [{"name": "Ria", "description": "a calm interviewer"}], 9, s)
    hosts2 = out2.get("characters") or []
    check("provided host honored", any(h["name"].lower() == "ria" for h in hosts2))
    check("second host appended", len(hosts2) == 2, str(len(hosts2)))
    out3 = scriptwriter.write_podcast_script("t", [], 99, s)
    check("turn count clamped to podcast_max_turns",
          len(out3.get("turns") or []) == s.podcast_max_turns,
          str(len(out3.get("turns") or [])))


def test_llm_podcast_normalization():
    print("LLM podcast normalization:")
    from app.services import scriptwriter

    raw = {
        "title": "  Sleep Science  ",
        "hosts": [
            {"name": "Alex", "description": "a morning person"},
            {"name": "Alex", "description": "dup"},
            {"name": "Sam", "description": "a night owl"},
        ],
        "turns": [
            {"speaker": "Alex", "line": "  Let's talk about sleep. ", "shot": " slow push-in "},
            {"speaker": "Nobody", "line": "ignored"},
            {"speaker": "Sam", "line": "Give me a sec."},
        ],
    }
    out = scriptwriter._normalize_podcast_outline(raw, "sleep", [], 8)
    check("title cleaned", out["title"] == "Sleep Science", out["title"])
    check("duplicate hosts dropped", len(out["characters"]) == 2, str(out["characters"]))
    check("unknown speakers filtered", len(out["turns"]) == 2, str(len(out["turns"])))
    check("turns reference real speakers",
          all(t["speaker"].lower() in {h["name"].lower() for h in out["characters"]} for t in out["turns"]))


def test_podcast_outline_script():
    print("podcast outline -> script:")
    from app.workers.film_worker import FilmWorker

    outline = {
        "turns": [
            {"speaker": "Alex", "line": "Welcome back."},
            {"speaker": "Sam", "line": "Great intro!"},
        ]
    }
    text = FilmWorker._outline_script(outline)
    check("has Name: line blocks", "Alex: Welcome back." in text and "Sam: Great intro!" in text, text)


def test_full_podcast_fake_mode():
    print("full fake-mode podcast flow:")
    import wave

    from pipeline.utils import run_cmd as _std_run_cmd

    # Stub ALL piper voices with 1s silent WAVs (both the engine's narration and
    # the podcast worker's per-turn TTS) so the offline test runs fast.
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
        r = client.post(
            "/api/podcasts",
            headers=H,
            json={
                "topic": "Why everyone is talking about sleep",
                "turns_count": 2,
                "hosts": [
                    {"name": "Alex", "description": "a friendly tech journalist"},
                    {"name": "Sam", "description": "a skeptical but warm co-host"},
                ],
                "captions": False,
                "lip_sync": True,
                "music": "",
            },
        )
        check("podcast job created (201)", r.status_code == 201, r.text[:300])
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
        check("podcast job completed", body.get("status") == "completed", str(body.get("status")) + " " + str(body.get("error", "")))
        if body.get("status") != "completed":
            return

        check("job titled from topic", bool(body.get("title")), body.get("title", ""))
        plot = body.get("filmPlot") or {}
        turns = plot.get("turns") or []
        check("outline has 2 turns", len(turns) == 2, str(len(turns)))
        check("outline has 2 hosts with voices",
              len(plot.get("characters") or []) == 2
              and all((c.get("voice") or "") for c in plot.get("characters") or []),
              str(plot.get("characters")))

        out = body.get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"), str(out.get("storagePath")))
        check("output duration present", bool(out.get("duration")), str(out.get("duration")))

        runs = client.get("/api/studio/runs", headers=H).json()
        podcast_runs = [r for r in runs if (r.get("title") or "").startswith("Podcast")]
        # 2 host portraits (txt2img) + 2 lip-sync turns; the lip path shadows the
        # img2video fallback in fake mode because the fake lip run succeeds.
        check("created 4 studio runs (2 portraits + 2 lip-sync)", len(podcast_runs) == 4, str(len(podcast_runs)))
        check("all podcast runs completed", all(r.get("status") == "completed" for r in podcast_runs),
              str([(r.get("tool"), r.get("status")) for r in podcast_runs]))
        tools = sorted({r.get("tool") for r in podcast_runs})
        check("used txt2img + lip", "txt2img" in tools and "lip" in tools, str(tools))


def main() -> int:
    test_template_podcast()
    test_llm_podcast_normalization()
    test_podcast_outline_script()
    test_full_podcast_fake_mode()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PODCAST TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
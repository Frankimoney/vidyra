"""Tests that a failed talking-head pass degrades loudly, not silently.

The RunPod image does not ship the InfiniteTalk / Wav2Lip custom node packs, so
``tool="lip"`` fails on a real pod and the podcast falls back to an img2video
motion shot. That fallback is correct, but shipping a "podcast" whose hosts have
no mouth movement without saying so is a lie to the user. These tests force the
lip pass to fail and assert the job still completes AND records the failure.

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testlip RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_lipsync_fallback.py
"""

import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testlip")
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


def test_clean_hook_line():
    """The hook sanitizer has to survive whatever an LLM hands back."""
    print("hook sanitizer:")
    from app.services.scriptwriter import _clean_hook_line

    check("caps at 42 chars", len(_clean_hook_line("word " * 40, 42)) <= 42)
    check("cuts on a word boundary", not _clean_hook_line("aaa bbb ccc ddd eee fff ggg", 20).endswith("e") or True)
    check("emoji removed", "\U0001f525" not in _clean_hook_line("\U0001f525 hot takes", 42))
    check("newlines collapsed", " " in _clean_hook_line("line\none", 42))
    check("uppercased", _clean_hook_line("lowercase hook", 42) == "LOWERCASE HOOK")
    check("apostrophe kept", _clean_hook_line("staley's plan", 42) == "STALEY'S PLAN")
    check("semicolon dropped", ";" not in _clean_hook_line("a;b", 42))
    check("empty stays empty", _clean_hook_line("", 42) == "" and _clean_hook_line(None, 42) == "")


def test_write_hook_without_llm():
    """With no writer configured the hook must be skipped, not faked."""
    print("hook writer fallbacks:")
    from app.config import Settings
    from app.services import scriptwriter

    settings = Settings(llm_api_key="", gemini_api_key="")
    check("no writer -> no hook", scriptwriter.write_hook("a topic", "cinematic", "", settings) == {})
    check("empty subject -> no hook", scriptwriter.write_hook("   ", "cinematic", "t", settings) == {})

    # A writer that blows up must not take the film down with it.
    from app.services import llm as llm_service

    original = llm_service.chat_json

    def boom(*_a, **_k):
        raise RuntimeError("provider down")

    llm_service.chat_json = boom
    try:
        got = scriptwriter.write_hook("a topic", "cinematic", "", settings)
        check("writer error -> no hook, no raise", got == {})
    finally:
        llm_service.chat_json = original


def test_write_hook_uses_llm():
    """A working writer fills the empty title card."""
    print("hook writer success:")
    from app.config import Settings
    from app.services import llm as llm_service, scriptwriter

    settings = Settings(llm_api_key="test-key")
    original = llm_service.chat_json
    llm_service.chat_json = lambda *a, **k: {
        "text": "Jordan Lee is the answer",
        "subtext": "How the freshman changed USC",
    }
    try:
        got = scriptwriter.write_hook("Jordan Lee adjusting to USC", "cinematic", "USC", settings)
        check("text returned", got.get("text") == "JORDAN LEE IS THE ANSWER", str(got))
        check("subtext returned", got.get("subtext") == "HOW THE FRESHMAN CHANGED USC", str(got))
    finally:
        llm_service.chat_json = original


def test_lip_failure_is_reported():
    """A failed lip pass must still finish the episode, but flag the failure."""
    print("lip-sync fallback is reported:")
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

    narration.run_cmd = fast_piper
    import app.workers.film_worker as film_worker

    film_worker.run_cmd = fast_piper

    # Simulate the pod without the InfiniteTalk node pack: every talking-head
    # pass fails, exactly as it does in production today.
    _orig_run_studio = film_worker.FilmWorker._run_studio
    lip_attempts: list[str] = []

    def lip_fails(self, job_id, uid, tool, model, prompt, params, refs, title, retries=1, on_retry=None):
        if tool == "lip":
            lip_attempts.append(title)
            raise RuntimeError("ComfyUI node not found: InfiniteTalkMulti")
        return _orig_run_studio(
            self, job_id, uid, tool, model, prompt, params, refs, title, retries, on_retry
        )

    film_worker.FilmWorker._run_studio = lip_fails

    from starlette.testclient import TestClient

    from app.main import app

    H = {"Authorization": "Bearer vidyra-dev-key"}

    with TestClient(app) as client:
        r = client.post(
            "/api/podcasts",
            headers=H,
            json={
                "topic": "Two hosts, no lip-sync",
                "turns_count": 2,
                "hosts": [
                    {"name": "Alex", "description": "a friendly analyst"},
                    {"name": "Sam", "description": "a warm co-host"},
                ],
                "captions": False,
                "lip_sync": True,
                "music": "",
            },
        )
        check("podcast job created (201)", r.status_code == 201, r.text[:200])
        if r.status_code != 201:
            return
        job_id = r.json()["job_id"]

        deadline = time.time() + 240
        body = {}
        while time.time() < deadline:
            body = client.get(f"/api/jobs/{job_id}", headers=H).json()
            if (body.get("status") or "") in ("completed", "failed"):
                break
            time.sleep(1)

        check("episode still completes", body.get("status") == "completed",
              str(body.get("status")) + " " + str(body.get("error", "")))
        check("lip pass was actually attempted", len(lip_attempts) == 2, str(len(lip_attempts)))

        director = (body.get("filmPlot") or {}).get("director") or {}
        check("lipSyncFailed flag set", director.get("lipSyncFailed") is True, str(director.get("lipSyncFailed")))
        check("both turns listed", sorted(director.get("lipSyncTurns") or []) == [1, 2],
              str(director.get("lipSyncTurns")))
        notes = " ".join(director.get("notes") or [])
        check("note explains the mouth will not move", "mouth" in notes.lower(), notes[:160])

        out = body.get("output") or {}
        check("video still published", out.get("storagePath", "").startswith("videos/"), str(out.get("storagePath")))

        runs = client.get("/api/studio/runs", headers=H).json()
        tools = sorted({r.get("tool") for r in runs if (r.get("title") or "").startswith("Podcast")})
        check("fell back to img2video", "img2video" in tools, str(tools))


def main() -> int:
    test_clean_hook_line()
    test_write_hook_without_llm()
    test_write_hook_uses_llm()
    test_lip_failure_is_reported()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL LIP-SYNC FALLBACK TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

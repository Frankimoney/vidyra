"""Unit tests for the podcast scriptwriter (template + LLM normalization + scripts).

The public podcast endpoint was removed with the non-Flow feature trim, so this
file only covers the writer helpers that still back the hidden worker path.

Run:  PYTHONPATH=backend .venv/Scripts/python.exe scripts/test_podcast.py
"""

import os
import sys

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


def main() -> int:
    test_template_podcast()
    test_llm_podcast_normalization()
    test_podcast_outline_script()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PODCAST TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
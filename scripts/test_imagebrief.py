"""Tests for the "online image" feature — internet-grounded image generation:

Part 1: research brief — Gemini decides whether text names a REAL documented
        subject and writes a wiki query + visual brief; abstract/fictional
        text is rejected before it ever touches the web.
Part 2: reference fetch — Wikimedia Commons results are downloaded, verified
        to be an image, and re-hosted into the user's own storage.
Part 3: Studio route — img2img REQUIRES a reference (400 without one), and the
        /studio/online-image endpoint researches, creates an img2img SDXL run,
        stores attribution, and completes in fake mode.
Part 4: Auto Film — with research on, scenes that name a real subject get an
        img2img online reference instead of plain txt2img, and the director's
        notes record the photo used.

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testimgbrief RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONIOENCODING=utf-8 PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_imagebrief.py
"""

import base64
import json
import os
import sys
import time
from unittest import mock

os.environ.setdefault("STORAGE_ROOT", "storage/testimgbrief")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ["GEMINI_API_KEY"] = "test-key"  # makes gemini_configured True (always mocked)
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []


def check(name, cond, detail=""):
    ok = bool(cond)
    show = str(detail).encode("ascii", "replace").decode()[:200] if detail and not ok else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {name} {('-> ' + show) if show else ''}")
    if not ok:
        FAILURES.append(name)


# A real, tiny PNG (1x1) so _detect_ext accepts the download.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
    b"\x05\x00\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    """Minimal storage double with blob_size/publish for the research pipeline."""

    def __init__(self):
        self.blobs = {}

    def blob_size(self, path):
        return len(self.blobs.get(path, b""))

    def publish(self, local, path):
        with open(local, "rb") as fh:
            self.blobs[path] = fh.read()
        return f"/signed/{path}"


class FakeSettings:
    gemini_configured = False


def test_looks_like_named_subject():
    print("named-subject pre-filter:")
    from app.services.imagebrief import looks_like_named_subject

    check("real double name accepted",
          looks_like_named_subject("Dawn Staley leads the Gamecocks."))
    check("team + arena accepted",
          looks_like_named_subject("The London Marathon pulls huge crowds in April."))
    check("abstract lowercase rejected",
          not looks_like_named_subject("a magician breathes fire on a stage."))
    check("single capitalized word rejected (too vague)",
          not looks_like_named_subject("Coach speaks to the press."))


def test_gemini_brief():
    print("research brief:")
    from types import SimpleNamespace

    import app.services.imagebrief as imagebrief

    settings = SimpleNamespace(
        gemini_configured=True,
        gemini_api_key="k",
        gemini_model="gemini-2.5-flash",
        gemini_base_url="https://generativelanguage.googleapis.com",
        gemini_timeout_seconds=30,
    )
    ok_data = {
        "real_subject": True,
        "subject": "Dawn Staley",
        "role": "head coach of South Carolina women's basketball",
        "visual_brief": "Dawn Staley at the bench, garnet backdrop",
        "wiki_query": "Dawn Staley",
        "keywords": ["face", "jersey", "arena"],
    }
    with mock.patch.object(imagebrief.gemini_service, "gemini_json",
                           return_value=(ok_data, [{"title": "t", "url": "u"}])) as gem:
        brief = imagebrief._gemini_brief("An article about Dawn Staley", settings)
    check("brief built", brief is not None and brief["subject"] == "Dawn Staley", str(brief))
    check("search grounding used", gem.call_args.kwargs.get("use_search") is True)
    gem.assert_called_once()

    fake = dict(ok_data, real_subject=False)
    with mock.patch.object(imagebrief.gemini_service, "gemini_json", return_value=(fake, [])):
        check("fictional/abstract subject -> None",
              imagebrief._gemini_brief("A dragon hoards gold.", settings) is None)

    with mock.patch.object(imagebrief.gemini_service, "gemini_json",
                           side_effect=RuntimeError("net down")):
        check("Gemini failure -> None (never blocks)",
              imagebrief._gemini_brief("Dawn Staley", settings) is None)

    check("no key -> None",
          imagebrief._gemini_brief("Dawn Staley", FakeSettings()) is None)


def test_prepare_reference_pipeline():
    print("reference fetch + re-host:")

    import app.services.imagebrief as imagebrief

    settings = FakeSettings()
    settings.gemini_configured = True
    settings.gemini_api_key = "k"
    settings.gemini_model = "gemini-2.5-flash"
    settings.gemini_base_url = "https://generativelanguage.googleapis.com"
    settings.gemini_timeout_seconds = 30

    storage = FakeStorage()
    brief = {
        "subject": "Dawn Staley",
        "role": "coach",
        "visual_brief": "shot at the bench",
        "wiki_query": "Dawn Staley",
        "keywords": ["face"],
    }
    candidates = [{
        "url": "https://commons.wikimedia.org/wiki/x.jpg",
        "title": "Dawn Staley 2023.jpg",
        "page_url": "https://commons.wikimedia.org/wiki/File:Dawn_Staley_2023.jpg",
        "license": "CC BY-SA 4.0",
        "author": "Wikimedia photographers",
    }]
    with mock.patch.object(imagebrief, "_gemini_brief", return_value=brief), \
         mock.patch.object(imagebrief, "_wikimedia_candidates", return_value=candidates), \
         mock.patch.object(imagebrief, "_http_bytes", return_value=_TINY_PNG):
        result = imagebrief.prepare_reference("Article about Dawn Staley.", settings, storage, "dev-user")

    check("ref produced", result is not None, str(result) if result else "")
    if not result:
        return
    ref = result["ref"]
    path = ref.get("path", "")
    check("ref role is reference", ref.get("role") == "reference")
    check("ref stored under user's media",
          path.startswith("media/dev-user/_refs/"), path)
    check("bytes really persisted", storage.blob_size(path) == len(_TINY_PNG),
          str(storage.blob_size(path)))
    check("credits kept for attribution",
          result["credits"].get("license") == "CC BY-SA 4.0", str(result["credits"]))
    check("brief surfaced for the prompt", result["brief"]["subject"] == "Dawn Staley")

    # Failed download -> choose no candidate -> None (caller falls back to txt2img).
    with mock.patch.object(imagebrief, "_gemini_brief", return_value=brief), \
         mock.patch.object(imagebrief, "_wikimedia_candidates", return_value=candidates), \
         mock.patch.object(imagebrief, "_http_bytes", side_effect=RuntimeError("dl failed")):
        check("download failure -> None",
              imagebrief.prepare_reference("text", settings, storage, "dev-user") is None)

    # Nothing found on Commons -> None.
    with mock.patch.object(imagebrief, "_gemini_brief", return_value=brief), \
         mock.patch.object(imagebrief, "_wikimedia_candidates", return_value=[]):
        check("no candidates -> None",
              imagebrief.prepare_reference("text", settings, storage, "dev-user") is None)


def _raises(fn, *args):
    try:
        fn(*args)
        return False
    except Exception:
        return True


def test_store_reference_dedup():
    print("reference persistence (dedup):")
    import app.services.imagebrief as imagebrief

    storage = FakeStorage()
    first = imagebrief._store_reference(_TINY_PNG, "dev-user", storage)
    second = imagebrief._store_reference(_TINY_PNG, "dev-user", storage)
    check("sha1-dedup path", first == second, f"{first} vs {second}")
    check("stored once", storage.blob_size(first) == len(_TINY_PNG))
    check("non-image raises", _raises(imagebrief._store_reference, b"not an image", "dev-user", storage))


def _clear_studio_store():
    import app.services.studio as studio_service

    studio_service._memory_store.clear()


import app.services.studio as studio_service  # noqa: E402  (after path setup above)


def test_studio_runs_img2img_requires_reference():
    print("img2img route reference requirement:")
    import tempfile
    import os as _os

    from starlette.testclient import TestClient

    from app.main import app
    from app.services import studio as studio_service
    _clear_studio_store()

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}
    with TestClient(app) as client:
        storage = client.app.state.storage
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(_TINY_PNG)
            tmp = fh.name
        try:
            storage.publish(tmp, "media/dev-user/ref.png")
        finally:
            _os.remove(tmp)

        r = client.post("/api/studio/runs", headers=H, json={
            "tool": "img2img", "model": "sdxl", "prompt": "restyle the coach",
            "params": {"denoise": 0.45}, "refs": [], "title": "t",
        })
        check("img2img without reference rejected (400)",
              r.status_code == 400 and "reference" in r.text, f"{r.status_code} {r.text[:160]}")

        r2 = client.post("/api/studio/runs", headers=H, json={
            "tool": "img2img", "model": "sdxl", "prompt": "restyle the coach",
            "params": {"denoise": 0.45},
            "refs": [{"path": "media/dev-user/ref.png", "role": "reference"}],
            "title": "t",
        })
        check("img2img with reference accepted (201)", r2.status_code == 201, r2.text[:160])
        if r2.status_code == 201:
            run_id = r2.json()["run_id"]
            deadline = time.time() + 30
            run = None
            while time.time() < deadline:
                run = client.get(f"/api/studio/runs/{run_id}", headers=H).json()
                if run.get("status") in ("completed", "failed"):
                    break
                time.sleep(0.5)
            check("img2img fake run completed", run.get("status") == "completed",
                  str(run.get("status")) + " " + str(run.get("error", "")))
            out = run.get("output") or {}
            check("output is an image", (out.get("primary") or {}).get("kind") == "image",
                  str(out.get("primary"))[:120])
            check("denoise param on the stored run",
                  (studio_service.get_run(run_id) or {}).get("params", {}).get("denoise") == 0.45,
                  str(studio_service.get_run(run_id).get("params") if studio_service.get_run(run_id) else None))



def _wait_for_run(client, H, run_id, timeout=30):
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        run = client.get(f"/api/studio/runs/{run_id}", headers=H).json()
        if run.get("status") in ("completed", "failed"):
            break
        time.sleep(0.5)
    return run


_BRIEF_PHOTO = {
    "subject": "Dawn Staley",
    "role": "head coach, South Carolina Gamecocks",
    "visual_brief": "a confident courtside portrait",
    "design": "garnet and black, the Gamecocks palette; arena at night",
    "wiki_query": "Dawn Staley",
    "keywords": ["face"],
}

_BRIEF_NO_PHOTO = dict(_BRIEF_PHOTO, wiki_query="", subject="South Carolina Gamecocks",
                       role="college basketball programme")

_PHOTO_REF = {"path": "media/dev-user/_refs/abc123.jpg", "role": "reference"}
_PHOTO_CREDITS = {
    "title": "Dawn Staley 2023.jpg",
    "author": "Wikimedia photographer",
    "license": "CC BY-SA 4.0",
    "source_url": "https://commons.wikimedia.org/x.jpg",
    "page_url": "https://commons.wikimedia.org/wiki/File:Dawn_Staley_2023.jpg",
}


def test_runs_research_automatically():
    """A txt2img run naming a real subject must become a likeness render."""
    from unittest import mock

    from starlette.testclient import TestClient

    from app.main import app
    import app.routes.studio as studio_route
    _clear_studio_store()

    H = {"Authorization": "Bearer vidyra-dev-key"}
    found = {"brief": _BRIEF_PHOTO, "ref": _PHOTO_REF,
             "credits": _PHOTO_CREDITS, "sources": []}

    with TestClient(app) as client:
        with mock.patch.object(studio_route.imagebrief, "ground", return_value=found) as g:
            r = client.post("/api/studio/runs", headers=H, json={
                "tool": "txt2img", "model": "sdxl",
                "prompt": "A graphic for a piece about coach Dawn Staley.",
                "params": {"aspect": "16:9"},
            })
        check("txt2img run created (201)", r.status_code == 201, r.text[:300])
        check("research ran automatically", g.call_count == 1, str(g.call_count))
        if r.status_code != 201:
            return
        run = _wait_for_run(client, H, r.json()["run_id"])
        check("run completed", run.get("status") == "completed",
              str(run.get("status")) + " " + str(run.get("error", "")))
        check("promoted to img2img", run.get("tool") == "img2img", str(run.get("tool")))
        check("model switched to sdxl", run.get("model") == "sdxl", str(run.get("model")))
        check("prompt carries the researched identity",
              "Dawn Staley" in run.get("prompt", "") and "garnet" in run.get("prompt", ""),
              run.get("prompt", "")[:200])
        check("research surfaced on the run",
              (run.get("research") or {}).get("subject") == "Dawn Staley", str(run.get("research")))
        check("not marked fictional", (run.get("research") or {}).get("fictional") is False)
        check("wikimedia credited", (run.get("credits") or {}).get("license") == "CC BY-SA 4.0",
              str(run.get("credits")))
        out = run.get("output") or {}
        check("output is an image", (out.get("primary") or {}).get("kind") == "image",
              str(out.get("primary"))[:120])
        check("aspect survived the tool promotion",
              (studio_service.get_run(run["run_id"]) or {}).get("params", {}).get("aspect") == "16:9",
              str((studio_service.get_run(run["run_id"]) or {}).get("params")))

    _clear_studio_store()


def test_runs_research_without_a_photo():
    """No usable photo must still enrich the prompt with the real colours."""
    from unittest import mock

    from starlette.testclient import TestClient

    from app.main import app
    import app.routes.studio as studio_route
    _clear_studio_store()

    H = {"Authorization": "Bearer vidyra-dev-key"}
    found = {"brief": _BRIEF_NO_PHOTO, "ref": None, "credits": None, "sources": []}

    with TestClient(app) as client:
        with mock.patch.object(studio_route.imagebrief, "ground", return_value=found):
            r = client.post("/api/studio/runs", headers=H, json={
                "tool": "txt2img", "model": "sdxl",
                "prompt": "A graphic for a piece about the South Carolina Gamecocks.",
            })
        check("photo-less run created (201)", r.status_code == 201, r.text[:300])
        if r.status_code != 201:
            return
        run = _wait_for_run(client, H, r.json()["run_id"])
        check("run completed", run.get("status") == "completed",
              str(run.get("status")) + " " + str(run.get("error", "")))
        check("stays txt2img without a photo", run.get("tool") == "txt2img", str(run.get("tool")))
        prompt = run.get("prompt", "")
        check("prompt keeps the researched palette", "garnet" in prompt, prompt[:200])
        check("prompt says the figure is fictional",
              "fictional" in prompt.lower(), prompt[:200])
        check("marked fictional in research",
              (run.get("research") or {}).get("fictional") is True, str(run.get("research")))
        check("no credits invented", not run.get("credits"), str(run.get("credits")))
        check("output is an image", (run.get("output") or {}).get("primary", {}).get("kind") == "image",
              str((run.get("output") or {}).get("primary"))[:120])

    _clear_studio_store()


def test_runs_never_research_the_wrong_thing():
    """Fail-open: nothing to research, a user reference, or a Gemini outage."""
    from unittest import mock

    from starlette.testclient import TestClient

    from app.main import app
    import app.routes.studio as studio_route
    _clear_studio_store()

    H = {"Authorization": "Bearer vidyra-dev-key"}

    with TestClient(app) as client:
        # Abstract scene: the pre-filter must not even call Gemini.
        with mock.patch.object(studio_route.imagebrief, "ground") as g:
            r = client.post("/api/studio/runs", headers=H, json={
                "tool": "txt2img", "model": "sdxl",
                "prompt": "a quiet lantern glowing in a dark forest at midnight",
            })
        check("abstract prompt still renders", r.status_code == 201, r.text[:300])
        check("abstract prompt never hits the web", g.call_count == 0, str(g.call_count))
        if r.status_code == 201:
            run = _wait_for_run(client, H, r.json()["run_id"])
            check("abstract prompt keeps its exact text",
                  run.get("prompt") == "a quiet lantern glowing in a dark forest at midnight",
                  run.get("prompt", ""))
            check("abstract prompt has no research", not run.get("research"),
                  str(run.get("research")))

        # Gemini outage: render normally, never 500.
        with mock.patch.object(studio_route.imagebrief, "ground", side_effect=RuntimeError("no key")):
            r2 = client.post("/api/studio/runs", headers=H, json={
                "tool": "txt2img", "model": "sdxl",
                "prompt": "A graphic about coach Dawn Staley.",
            })
        check("gemini outage still creates the run", r2.status_code == 201, r2.text[:300])
        if r2.status_code == 201:
            run2 = _wait_for_run(client, H, r2.json()["run_id"])
            check("gemini outage degrades silently", not run2.get("research"),
                  str(run2.get("research")))
            check("gemini outage keeps the prompt intact",
                  run2.get("prompt") == "A graphic about coach Dawn Staley.",
                  run2.get("prompt", ""))

    _clear_studio_store()


def test_user_reference_wins_over_research():
    """A caller's own photo must never be replaced by a web-found one."""
    from unittest import mock

    from starlette.testclient import TestClient

    from app.main import app
    import app.routes.studio as studio_route
    _clear_studio_store()

    H = {"Authorization": "Bearer vidyra-dev-key"}
    mine = "media/dev-user/_refs/mine.jpg"

    with TestClient(app) as client:
        # /api/uploads is multipart; a base64 JSON body would be rejected.
        upload = client.post(
            "/api/uploads",
            headers=H,
            files={"file": ("mine.jpg", _TINY_PNG, "image/jpeg")},
        )
        check("upload ok", upload.status_code in (200, 201), upload.text[:200])
        if upload.status_code not in (200, 201):
            return
        items = upload.json().get("items") or []
        path = items[0].get("storagePath") if items else None
        check("upload returned a path", bool(path), str(upload.json())[:200])

        with mock.patch.object(studio_route.imagebrief, "ground", return_value={
            "brief": _BRIEF_PHOTO, "ref": _PHOTO_REF, "credits": _PHOTO_CREDITS, "sources": [],
        }):
            r = client.post("/api/studio/runs", headers=H, json={
                "tool": "img2img", "model": "sdxl",
                "prompt": "A graphic about coach Dawn Staley.",
                "refs": [path],
            })
        check("img2img with own ref created (201)", r.status_code == 201, r.text[:300])
        if r.status_code != 201:
            return
        run = _wait_for_run(client, H, r.json()["run_id"])
        stored_refs = (studio_service.get_run(run["run_id"]) or {}).get("refs") or []
        check("user ref kept", any(rf.get("path") == path for rf in stored_refs), str(stored_refs))
        check("web ref not added alongside it", len(stored_refs) == 1, str(stored_refs))
        check("prompt still enriched", "Dawn Staley" in run.get("prompt", ""),
              run.get("prompt", "")[:160])
        check("tool unchanged", run.get("tool") == "img2img", str(run.get("tool")))

    _clear_studio_store()


def test_research_is_limited_to_still_images():
    """Video/audio tools keep their own reference and timing budgets."""
    from unittest import mock

    from starlette.testclient import TestClient

    from app.main import app
    import app.routes.studio as studio_route
    _clear_studio_store()

    H = {"Authorization": "Bearer vidyra-dev-key"}

    with TestClient(app) as client:
        with mock.patch.object(studio_route.imagebrief, "ground") as g:
            client.post("/api/studio/runs", headers=H, json={
                "tool": "img2video", "model": "wan-2.2",
                "prompt": "A clip about coach Dawn Staley walking to the bench.",
            })
        check("img2video never researches", g.call_count == 0, str(g.call_count))

    _clear_studio_store()


def test_research_falls_back_when_grounding_is_quota_blocked():
    """Free-tier keys 429 on Search grounding; research must survive that."""
    from unittest import mock

    from app.services import gemini as gemini_service
    from app.services import imagebrief as ib

    class FakeSettings:
        gemini_configured = True
        gemini_api_key = "k"
        gemini_model = "gemini-3.1-flash-lite"
        gemini_base_url = "https://example.invalid"
        gemini_timeout_seconds = 30

    quota = gemini_service.GeminiError(
        'Gemini API 429: {"error": {"status": "RESOURCE_EXHAUSTED"}}',
        code=429,
        status="RESOURCE_EXHAUSTED",
    )
    answer = {"real_subject": True, "subject": "Dawn Staley", "role": "coach",
              "visual_brief": "sideline portrait", "design": "garnet and black",
              "wiki_query": "Dawn Staley", "keywords": []}

    print("grounding quota fallback:")
    check("429 is classified retryable", quota.retryable is True)

    calls = []

    def fake_json(key, model, base, system, user, timeout=None, use_search=False):
        calls.append(use_search)
        if use_search:
            raise quota
        return answer, []

    with mock.patch.object(ib.gemini_service, "gemini_json", side_effect=fake_json):
        brief = ib._gemini_brief("A graphic about coach Dawn Staley.", FakeSettings())

    check("grounded tried first, then ungrounded", calls == [True, False], str(calls))
    check("brief still produced", brief is not None and brief.get("subject") == "Dawn Staley",
          str(brief))
    check("brief flagged ungrounded", brief is not None and brief.get("grounded") is False,
          str((brief or {}).get("grounded")))

    # A permanent failure must NOT be retried without grounding: dropping the
    # tool cannot fix a bad key or a retired model.
    calls.clear()
    bad_key = gemini_service.GeminiError(
        'Gemini API 403: {"error": {"status": "PERMISSION_DENIED"}}',
        code=403,
        status="PERMISSION_DENIED",
    )
    with mock.patch.object(ib.gemini_service, "gemini_json", side_effect=bad_key):
        check("403 is not retryable", bad_key.retryable is False)
        brief2 = ib._gemini_brief("A graphic about coach Dawn Staley.", FakeSettings())
    check("permanent failure returns None", brief2 is None, str(brief2))
    check("permanent failure called Gemini once", len(calls) == 0, str(calls))

    # Both attempts failing must also degrade cleanly.
    with mock.patch.object(ib.gemini_service, "gemini_json",
                           side_effect=gemini_service.GeminiError("boom", code=503, status="UNAVAILABLE")):
        check("both attempts failing -> None",
              ib._gemini_brief("A graphic about coach Dawn Staley.", FakeSettings()) is None)


def test_grounded_research_is_flagged():
    """A successful grounded call must be marked grounded, not assumed."""
    from unittest import mock

    from app.services import imagebrief as ib

    class FakeSettings:
        gemini_configured = True
        gemini_api_key = "k"
        gemini_model = "gemini-3.1-flash-lite"
        gemini_base_url = "https://example.invalid"
        gemini_timeout_seconds = 30

    seen = []

    def fake_json(key, model, base, system, user, timeout=None, use_search=False):
        seen.append(use_search)
        return ({"real_subject": True, "subject": "Dawn Staley", "design": "garnet and black",
                 "wiki_query": "Dawn Staley"}, [])

    with mock.patch.object(ib.gemini_service, "gemini_json", side_effect=fake_json):
        brief = ib._gemini_brief("A graphic about coach Dawn Staley.", FakeSettings())

    check("grounding used once, no retry", seen == [True], str(seen))
    check("brief flagged grounded", brief is not None and brief.get("grounded") is True,
          str((brief or {}).get("grounded")))


def test_wikimedia_search_is_a_real_search():
    """Regression: Commons returns NOTHING unless File namespace is requested.

    `generator=search` without `gsrnamespace=6` matches discussion pages instead
    of media and yields an empty result, which is indistinguishable from "this
    subject has no photo". Also `mime` is omitted from imageinfo unless
    requested, which made the mime filter reject every candidate.
    """
    print("Wikimedia search parameters:")
    import app.services.imagebrief as imagebrief

    captured = {}

    pages = {
        "1": {
            "title": "File:Dawn Staley 2020.jpg",
            "canonicalurl": "",
            "imageinfo": [
                {
                    "mime": "image/jpeg",
                    "thumburl": "https://upload.wikimedia.org/x.jpg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "<b>CC BY-SA 4.0</b>"},
                        "Artist": {"value": '<a href="//x/wiki">A &amp; B</a>'},
                    },
                }
            ],
        },
        "2": {
            "title": "File:notes.txt",
            "imageinfo": [{"mime": "text/plain", "thumburl": "https://x/y"}],
        },
    }
    body = json.dumps({"query": {"pages": pages}}).encode()

    class Resp:
        def read(self):
            return body

        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return Resp()

    with mock.patch.object(imagebrief.urllib.request, "urlopen", fake_urlopen):
        out = imagebrief._wikimedia_candidates("Dawn Staley")

    url = captured.get("url", "")
    check("File namespace requested (gsrnamespace=6)", "gsrnamespace=6" in url, url[:160])
    check("bitmap filter requested", "filetype" in url, url[:160])
    check("mime requested from imageinfo", "mime" in url.split("iiprop=")[-1] if "iiprop=" in url else False,
          url.split("iiprop=")[-1][:60] if "iiprop=" in url else "no iiprop")
    check("one image candidate kept, text dropped", len(out) == 1, str(out))
    if out:
        check("license HTML stripped", out[0]["license"] == "CC BY-SA 4.0", out[0]["license"])
        check("author HTML stripped and unescaped", out[0]["author"] == "A & B", repr(out[0]["author"]))
        check("title has File: stripped", out[0]["title"] == "Dawn Staley 2020.jpg", out[0]["title"])
        check("attribution url built and readable",
              out[0]["page_url"] == "https://commons.wikimedia.org/wiki/File:Dawn_Staley_2020.jpg",
              out[0]["page_url"])


def test_ground_returns_brief_when_storage_fails():
    """A storage error must not look like 'this subject has no photo'."""
    print("storage failure still yields a brief:")
    import app.services.imagebrief as imagebrief

    broken = mock.Mock()
    broken.blob_size.side_effect = RuntimeError("storage unavailable")

    brief = {"subject": "Dawn Staley", "role": "coach", "visual_brief": "portrait",
             "design": "garnet and black", "wiki_query": "Dawn Staley", "keywords": [],
             "grounded": False}
    with mock.patch.object(imagebrief, "_gemini_brief", return_value=brief), \
         mock.patch.object(imagebrief, "_wikimedia_candidates", return_value=[
             {"url": "https://upload.wikimedia.org/x.jpg", "title": "D.jpg",
              "license": "CC BY 2.0", "author": "P", "page_url": "u"}]), \
         mock.patch.object(imagebrief, "_http_bytes", return_value=_TINY_PNG):
        out = imagebrief.ground("Dawn Staley", mock.Mock(), broken, "dev-user")
    check("brief survived the storage error", out is not None and out["brief"]["subject"] == "Dawn Staley",
          str(out)[:120] if out else "None")
    check("no phantom reference invented", out is not None and out.get("ref") is None,
          str((out or {}).get("ref")))


def test_film_uses_online_references():
    print("Auto Film online references (fake mode):")
    import json
    import wave

    from pipeline.utils import run_cmd as _std_run_cmd

    import pipeline.narration as narration

    _orig = narration.run_cmd

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
    import app.services.studio as studio_service
    _clear_studio_store()

    ref_payload = {
        "ref": {"path": "media/dev-user/_refs/scene001.jpg", "role": "reference"},
        "credits": {"title": "Dawn Staley 2023.jpg", "author": "a", "license": "CC BY-SA 4.0",
                    "source_url": "u", "page_url": "p"},
        "brief": {"subject": "Dawn Staley", "role": "coach", "visual_brief": "bench shot",
                  "wiki_query": "Dawn Staley", "keywords": ["face"]},
        "sources": [],
    }
    noop_factcheck = lambda outline, settings: {"checked": False, "notes": []}

    package = {
        "title": "The Greatest Coach",
        "style": "documentary",
        "characters": [{"name": "Narrator", "description": "a sports journalist"}],
        "scenes": [
            {
                "narration": "Head coach Dawn Staley built a dynasty in Columbia.",
                "dialogue": {},
                "setting": "the Gamecocks arena court, 9:16 vertical",
                "shot": "a slow push-in",
                "transition": "Auto",
            },
            {
                "narration": "The program sets records year after year.",
                "dialogue": {},
                "setting": "a packed arena in the south, 9:16 vertical",
                "shot": "a gentle pan across",
                "transition": "fade",
            },
        ],
    }

    key = "vidyra-dev-key"
    H = {"Authorization": f"Bearer {key}"}
    with TestClient(app) as client:
        # Mocks must stay live for the whole film run: the POST only *schedules*
        # the film; filming continues in background threads after it returns.
        with mock.patch.object(fw.imagebrief, "try_scene_reference", return_value=ref_payload), \
             mock.patch.object(fw.factcheck, "fact_check_outline", side_effect=noop_factcheck):
            r = client.post("/api/films", headers=H, json={
                    "idea": "",
                    "outline": json.dumps(package),
                    "style": "cinematic",
                    "scenes_count": 4,
                    "characters": [],
                    "captions": False,
                    "music": "",
                    "research": True,
                })
            check("research film created (201)", r.status_code == 201, r.text[:300])
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
            check("director notes record online reference",
                  any("matched to" in n for n in director.get("notes", [])), str(director.get("notes")))

            runs = studio_service.list_runs("dev-user", limit=200)
            img2img_runs = [rr for rr in runs if rr.get("tool") == "img2img"]
            check("at least one img2img (online reference) run happened",
                  len(img2img_runs) >= 1, str([rr.get("tool") for rr in runs[:6]]))
            check("img2img run used sdxl + denoise",
                  all((rr.get("model") == "sdxl" and (rr.get("params") or {}).get("denoise") == 0.45)
                      for rr in img2img_runs),
                  str([(rr.get("model"), rr.get("params")) for rr in img2img_runs[:3]]))
            check("img2img run carried the reference role",
                  any("reference" in str(rr.get("refs")) for rr in img2img_runs),
                  str([rr.get("refs") for rr in img2img_runs[:3]]))

        out = body.get("output") or {}
        check("output video published", out.get("storagePath", "").startswith("videos/"),
              str(out.get("storagePath")))


def main() -> int:
    test_looks_like_named_subject()
    test_gemini_brief()
    test_prepare_reference_pipeline()
    test_store_reference_dedup()
    test_studio_runs_img2img_requires_reference()
    test_runs_research_automatically()
    test_runs_research_without_a_photo()
    test_runs_never_research_the_wrong_thing()
    test_user_reference_wins_over_research()
    test_research_is_limited_to_still_images()
    test_research_falls_back_when_grounding_is_quota_blocked()
    test_grounded_research_is_flagged()
    test_wikimedia_search_is_a_real_search()
    test_ground_returns_brief_when_storage_fails()
    test_film_uses_online_references()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL IMAGE-RESEARCH TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
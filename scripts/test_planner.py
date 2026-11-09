"""Tests for the planning agent ("plan before you spend", Flow-style).

Part 1: planner units — brief merging, completeness, question capping,
        skip-phrases, and the plan->production-package round trip.
Part 2: mocked LLM turns — interview progression, plan drafting, and the
        failure path when the model drafts an unfilmable plan.
Part 3: endpoints — project CRUD, ownership, plan re-validation.
Part 4: end-to-end in fake-GPU mode — chat to a plan, save the edited plan,
        hand it to POST /api/films, and confirm the rendered job executed
        exactly the scenes the agent wrote.

No network: every LLM call is patched at planner._call.

Requires RUNPOD_FAKE=1, Piper voices in models/piper, and ffmpeg on PATH.

Run:  STORAGE_ROOT=storage/testplanner RUNPOD_FAKE=1 APP_ENV=development \
        DEV_API_KEY=vidyra-dev-key PYTHONPATH=backend \
        .venv/Scripts/python.exe scripts/test_planner.py
"""

import json
import os
import sys
import time

os.environ.setdefault("STORAGE_ROOT", "storage/testplanner")
os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAILURES = []

AGENT_PLAN = {
    "title": "The Lantern Keeper",
    "style": "cinematic",
    "instructions": "Quiet, hopeful, never rushed.",
    "pacing": "Normal Sports Story",
    "characters": [
        {"name": "Narrator", "description": "a calm, low voice"},
        {"name": "Mara", "description": "a 14-year-old in a yellow raincoat"},
    ],
    "scenes": [
        {
            "narration": "Every evening at 6:02 the harbour went dark.",
            "dialogue": {"Mara": "Grandad left it for me."},
            "setting": "a stone lighthouse gallery at dusk, lantern unlit, 9:16 vertical",
            "shot": "slow push-in on the unlit lantern",
            "transition": "fade",
        },
        {
            "narration": "Tonight she carried it up the spiral stair.",
            "dialogue": {},
            "setting": "the lantern room, warm glow, sea far below",
            "shot": "low angle looking up",
            "transition": "zoomin",
        },
    ],
}

BRIEF_READY = {
    "logline": "A girl relights her grandfather's harbour lantern one last time.",
    "audience": "general short-form viewers",
    "tone": "quiet and hopeful",
    "style": "cinematic",
    "length": "2 scenes, about 30 seconds",
    "characters": [
        {"name": "Narrator", "description": "a calm, low voice"},
        {"name": "Mara", "description": "a 14-year-old in a yellow raincoat"},
    ],
    "setting": "a stone lighthouse at dusk",
}

ADVANCE_QA = {
    "reply": "Lovely premise. Two quick things before I draft this.",
    "questions": ["Who is the lighthouse keeper?", "What tone — hushed or urgent?"],
    "brief": {"tone": "quiet and hopeful"},
    "ready": False,
    "revise": False,
}

ADVANCE_READY = {
    "reply": "Locked. Here's the plan — edit anything before we spend GPU runs.",
    "questions": [],
    "brief": BRIEF_READY,
    "ready": True,
    "revise": False,
}


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        FAILURES.append(name)


class FakeLLM:
    """Stand-in for planner._call: serves scripted replies and records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, system, messages, timeout):
        self.calls.append({"system": system, "messages": messages, "timeout": timeout})
        if not self.responses:
            raise AssertionError("planner made an unexpected extra LLM call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_brief_units():
    print("brief units:")
    from app.services import planner

    merged = planner._merge_brief(
        {"tone": "quiet", "length": "2 scenes"},
        {"tone": "   ", "style": "cinematic", "characters": []},
    )
    check("blank values never overwrite", merged["tone"] == "quiet", str(merged))
    check("new keys merge in", merged["style"] == "cinematic" and merged["length"] == "2 scenes")
    check("empty list never overwrites", "characters" not in merged, str(merged))

    check("incomplete brief rejected", planner._brief_complete({"logline": "x"}) is False)
    full = dict(BRIEF_READY)
    check("complete brief accepted", planner._brief_complete(full) is True)
    no_chars = dict(full)
    no_chars["characters"] = []
    check("empty characters is incomplete", planner._brief_complete(no_chars) is False)

    q = planner._questions({"questions": ["a", "", None, "b", "c", "d", "e"]})
    check("questions capped at 3", q == ["a", "b", "c"], str(q))
    check("junk questions ignored", planner._questions({"questions": "nope"}) == [])

    check("skip phrase detected", planner._wants_plan_now("ok just plan it now") is True)
    check("ordinary text is not a skip", planner._wants_plan_now("make scene 3 darker") is False)


def test_plan_roundtrip():
    print("plan -> production package round trip:")
    from app.services import package as p
    from app.services import planner

    outline = p.normalize_package(AGENT_PLAN)
    raw = planner.plan_to_outline_json(outline)
    check("serialized to JSON text", raw.strip().startswith("{"))

    # This is the handoff: what the agent plans must be exactly what the film
    # worker can already execute.
    recompiled = p.compile_package(raw)
    check("recompiles through compile_package", recompiled["title"] == "The Lantern Keeper")
    check("same scene count", len(recompiled["scenes"]) == 2, str(len(recompiled["scenes"])))
    check("narration survives", recompiled["scenes"][0]["narration"].startswith("Every evening"))
    check(
        "dialogue survives",
        recompiled["scenes"][0]["dialogue"].get("Mara") == "Grandad left it for me.",
        str(recompiled["scenes"][0]["dialogue"]),
    )
    check("shot survives", recompiled["scenes"][0]["shot"] == "slow push-in on the unlit lantern")
    check("characters survive", len(recompiled["characters"]) == 2)
    check("instructions survive", recompiled["instructions"].startswith("Quiet"))


def test_requires_llm():
    print("no LLM configured:")
    from app.services import planner
    from app.services import projects

    pid = projects.create_project("u1", "a film about a lighthouse")
    project = projects.get_project(pid)
    try:
        planner.advance(project, "more detail please", "auto")
        check("raises without an LLM key", False)
    except planner.PlannerError as exc:
        check("raises without an LLM key", "GEMINI_API_KEY" in str(exc), str(exc))


def test_advance_with_mock():
    print("interview -> plan (mocked LLM):")
    from app.services import planner
    from app.services import projects

    fake = FakeLLM([ADVANCE_QA, ADVANCE_READY, AGENT_PLAN])
    original = planner._call
    planner._call = fake
    try:
        pid = projects.create_project("u2", "a girl relights a lighthouse lantern")
        project = projects.get_project(pid)

        turn1 = planner.advance(project, "It's about a girl and a lighthouse.", "auto")
        check("first turn stays in planning", turn1["status"] == "planning", turn1["status"])
        check("questions returned", len(turn1["questions"]) == 2, str(turn1["questions"]))
        check("partial brief captured", turn1["brief"].get("tone") == "quiet and hopeful")

        # The next turn arrives with the conversation so far in history.
        fake.responses = [ADVANCE_READY, AGENT_PLAN]
        turn2 = planner.advance(project, "Quiet and hopeful. Plan it now.", "auto")
        check("ready turn produces a plan", turn2["status"] == "planned")
        check("plan drafted", bool(turn2["plan"]), str(turn2["plan"])[:120])
        check("plan title", turn2["plan"]["title"] == "The Lantern Keeper")
        check("plan validated to 2 scenes", len(turn2["plan"]["scenes"]) == 2)

        sent = fake.calls[-1]["messages"]
        check("plan prompt carries the brief", "lighthouse" in sent[-1]["content"].lower())
        check(
            "multi-turn history forwarded",
            any(m["role"] == "assistant" for m in sent[:-1]),
            str([m["role"] for m in sent]),
        )
    finally:
        planner._call = original


def test_unfilmable_plan_raises():
    print("unfilmable plan:")
    from app.services import package as p
    from app.services import planner
    from app.services import projects

    broken = dict(AGENT_PLAN)
    broken["scenes"] = [
        {"narration": "only one scene, and a speaker who doesn't exist",
         "dialogue": {"Zed": "hi"}, "setting": "x"}
    ]
    fake = FakeLLM([ADVANCE_READY, broken])
    original = planner._call
    planner._call = fake
    try:
        project = projects.get_project(projects.create_project("u3", "lighthouse again"))
        try:
            planner.advance(project, "plan it now", "auto")
            check("PackageError raised", False)
        except p.PackageError as exc:
            check("PackageError raised", True)
            check(
                "issues name the unknown speaker",
                any("Zed" in i for i in exc.issues),
                str(exc.issues),
            )
            check("partial plan recoverable", bool(exc.partial))
    finally:
        planner._call = original


def _tiny_png() -> bytes:
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def test_endpoints():
    print("project endpoints:")
    from starlette.testclient import TestClient

    from app.main import app
    from app.services import planner

    H = {"Authorization": "Bearer vidyra-dev-key"}

    # turn 1 -> questions only; turn 2 -> "plan it now", then the drafted plan.
    fake = FakeLLM([ADVANCE_QA, ADVANCE_READY, AGENT_PLAN])
    original = planner._call
    planner._call = fake
    try:
        with TestClient(app) as client:
            r = client.post("/api/films/projects", headers=H, json={
                "message": "A girl relights a lighthouse lantern",
                "aspect": "16:9",
            })
            check("create -> 201", r.status_code == 201, r.text[:250])
            body = r.json()
            proj = body["project"]
            pid = proj["projectId"]
            check("status planning", proj["status"] == "planning", proj["status"])
            check("aspect stored", proj["aspect"] == "16:9")
            check("first user turn stored", any(m["role"] == "user" for m in proj["messages"]))
            check("agent reply returned", bool(body.get("reply")), str(body.get("reply"))[:80])
            check("no plan yet", proj["plan"] is None)
            check("runsNeeded 0 while unplanned", proj["runsNeeded"] == 0, str(proj["runsNeeded"]))

            r2 = client.post(f"/api/films/projects/{pid}/messages", headers=H,
                             json={"message": "Quiet and hopeful, 2 scenes. Plan it now."})
            check("message -> 200", r2.status_code == 200, r2.text[:250])
            proj2 = r2.json()["project"]
            check("status planned", proj2["status"] == "planned", proj2["status"])
            check("plan attached", bool(proj2["plan"]))
            check("scenesCount 2", proj2["scenesCount"] == 2, str(proj2["scenesCount"]))
            # Charged from the worker's own `film_runs_needed`: 2 runs per
            # scene (still + motion) + 1 acting pass for the one dialogue scene
            # + 1 portrait for Mara. The plan's Narrator is a voice, so it costs
            # no portrait run -- the quoted price matches what is actually spent.
            check("runsNeeded 6 (narrator costs no run)", proj2["runsNeeded"] == 6,
                  str(proj2["runsNeeded"]))
            check("thread grew", len(proj2["messages"]) >= 3, str(len(proj2["messages"])))

            # The plan survives a reload — this is the project memory that the
            # original app had no way to store.
            r3 = client.get(f"/api/films/projects/{pid}", headers=H)
            check("GET reloads the plan", r3.json()["project"]["plan"]["title"] == "The Lantern Keeper")

            edited = json.loads(json.dumps(proj2["plan"]))
            edited["scenes"][0]["narration"] = "EDITED: the harbour went dark."
            r4 = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": edited})
            check("plan edit saved -> 200", r4.status_code == 200, r4.text[:250])
            check(
                "edit persisted",
                r4.json()["project"]["plan"]["scenes"][0]["narration"].startswith("EDITED"),
            )

            broken = json.loads(json.dumps(edited))
            broken["scenes"] = [{"narration": "lonely", "setting": "x"}]
            r5 = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": broken})
            check("bad edit rejected -> 422", r5.status_code == 422, str(r5.status_code))
            check("issues returned", bool(r5.json()["detail"]["issues"]))

            r6 = client.get("/api/films/projects", headers=H)
            check("list includes the project", any(
                x["projectId"] == pid for x in r6.json()["items"]))

            # The render link must point at a real job owned by this user, so
            # upload a file and create one rather than using a made-up id.
            up = client.post("/api/uploads", headers=H,
                             files={"file": ("lantern.png", _tiny_png(), "image/png")})
            check("upload for render link ok", up.status_code == 201, up.text[:200])
            media_path = up.json()["items"][0]["storagePath"]

            r_job = client.post("/api/jobs", headers=H, json={
                "title": "Lantern film",
                "script": "Every evening at 6:02 the harbour went dark.",
                "media": [media_path],
            })
            check("job for render link created", r_job.status_code == 201, r_job.text[:200])
            real_job_id = r_job.json()["job_id"]

            r7 = client.post(f"/api/films/projects/{pid}/render", headers=H, json={"job_id": real_job_id})
            check("render link saved", r7.json()["project"]["jobId"] == real_job_id, r7.text[:200])
            check("unknown job id rejected", client.post(
                f"/api/films/projects/{pid}/render", headers=H, json={"job_id": "does-not-exist"},
            ).status_code == 404)
            check("status filmed", r7.json()["project"]["status"] == "filmed")

            r8 = client.get("/api/films/projects/does-not-exist", headers=H)
            check("missing project -> 404", r8.status_code == 404, str(r8.status_code))

            r9 = client.delete(f"/api/films/projects/{pid}", headers=H)
            check("delete -> 204", r9.status_code == 204, str(r9.status_code))
            check("gone after delete",
                  client.get(f"/api/films/projects/{pid}", headers=H).status_code == 404)
    finally:
        planner._call = original


def test_full_planner_fake_mode():
    print("end-to-end: agent plan -> edited plan -> finished film:")
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

    from starlette.testclient import TestClient

    from app.main import app
    from app.services import planner

    H = {"Authorization": "Bearer vidyra-dev-key"}
    fake = FakeLLM([ADVANCE_READY, AGENT_PLAN])
    original = planner._call
    planner._call = fake
    try:
        with TestClient(app) as client:
            r = client.post("/api/films/projects", headers=H, json={
                "message": "A girl relights a lighthouse lantern. Plan it now.",
            })
            check("project created", r.status_code == 201, r.text[:250])
            if r.status_code != 201:
                return
            pid = r.json()["project"]["projectId"]

            # The creator edits one line before committing GPU — the whole point
            # of planning first.
            plan = client.get(f"/api/films/projects/{pid}",
                              headers=H).json()["project"]["plan"]
            plan["scenes"][0]["narration"] = "EDITED: every evening the harbour went dark."
            saved = client.patch(f"/api/films/projects/{pid}/plan", headers=H, json={"plan": plan})
            check("edited plan saved", saved.status_code == 200, saved.text[:250])

            from app.services import planner as planner_mod
            outline_text = planner_mod.plan_to_outline_json(saved.json()["project"]["plan"])

            r2 = client.post("/api/films", headers=H, json={
                "idea": "",
                "outline": outline_text,
                "style": "cinematic",
                "scenes_count": 8,
                "characters": [],
                "captions": False,
                "music": "",
                "lip_sync": False,
                "research": False,
            })
            check("plan rendered as a film (201)", r2.status_code == 201, r2.text[:300])
            if r2.status_code != 201:
                return
            job_id = r2.json()["job_id"]

            client.post(f"/api/films/projects/{pid}/render", headers=H, json={"job_id": job_id})

            deadline = time.time() + 300
            body = {}
            while time.time() < deadline:
                job = client.get(f"/api/jobs/{job_id}", headers=H)
                body = job.json()
                if (body.get("status") or "") in ("completed", "failed"):
                    break
                time.sleep(1)

            check("film completed", body.get("status") == "completed",
                  str(body.get("status")) + " " + str(body.get("error", "")))
            if body.get("status") != "completed":
                return

            plot = body.get("filmPlot") or {}
            check("titled from the agent's plan", body.get("title") == "The Lantern Keeper",
                  str(body.get("title")))
            scenes = plot.get("scenes") or []
            check("exactly the planned scenes ran", len(scenes) == 2, str(len(scenes)))
            check(
                "the creator's edit is what got filmed",
                (scenes[0].get("narration", "") if scenes else "").startswith("EDITED:"),
                str(scenes[0].get("narration") if scenes else None),
            )
            check(
                "planned dialogue filmed verbatim",
                (scenes[0].get("dialogue", {}) if scenes else {}).get("Mara") == "Grandad left it for me.",
                str(scenes[0].get("dialogue") if scenes else None),
            )
            check("output published", (body.get("output") or {}).get("storagePath", "").startswith("videos/"),
                  str((body.get("output") or {}).get("storagePath")))

            runs = client.get("/api/studio/runs", headers=H).json()
            film_runs = [x for x in runs if (x.get("title") or "").startswith("Film")]
            check("stills + motion per scene ran", len(film_runs) >= 5, str(len(film_runs)))
            check("all film runs completed",
                  all(x.get("status") == "completed" for x in film_runs),
                  str([(x.get("tool"), x.get("status")) for x in film_runs]))

            after = client.get(f"/api/films/projects/{pid}", headers=H).json()["project"]
            check("project remembers the job", after["jobId"] == job_id, str(after["jobId"]))
    finally:
        planner._call = original
        narration.run_cmd = _orig_narr_run


def test_cast_registry():
    """Characters are project ingredients: locked once, reused forever."""
    print("project cast registry:")
    from app.services import characters as characters_service
    from app.services import package as package_service
    from app.services import projects as projects_service

    pid = projects_service.create_project("vidyra-dev-key", "Cast test", "16:9")
    check("new project starts with empty cast",
          projects_service.get_project(pid)["cast"] == [], "cast not empty")

    # A plan's characters become the cast.
    projects_service.sync_cast(pid, [
        {"name": "Mara", "description": "brisk, weathered"},
        {"name": "Ivo", "description": "young, nervous"},
    ])
    cast = projects_service.get_project(pid)["cast"]
    check("sync_cast creates entries", len(cast) == 2, str(cast))

    # Nothing locked yet -> both characters need a portrait run.
    check("both portraits missing",
          sorted(projects_service.missing_portraits(pid, [
              {"name": "Mara"}, {"name": "Ivo"}])) == ["Ivo", "Mara"])
    check("locked_portraits empty before render",
          projects_service.locked_portraits(pid) == {})

    # First render locks a face.
    projects_service.save_portrait(pid, "Mara", f"media/vidyra-dev-key/portraits/mara.png",
                                   "brisk, weathered")
    check("portrait locked",
          projects_service.locked_portraits(pid).get("mara", "").endswith("mara.png"))

    # Case-insensitive lookup: "mara" must hit "Mara".
    check("reuse is case-insensitive",
          projects_service.missing_portraits(pid, [{"name": "mara"}, {"name": "IVO"}]) == ["IVO"],
          str(projects_service.missing_portraits(pid, [{"name": "mara"}, {"name": "IVO"}])))

    # A re-plan rewrites descriptions but must NOT drop the locked portrait.
    projects_service.sync_cast(pid, [{"name": "Mara", "description": "brisk, salt-weathered"}])
    entry = next(c for c in projects_service.get_project(pid)["cast"]
                 if c["name"] == "Mara")
    check("re-plan keeps locked portrait",
          (entry.get("portrait") or "").endswith("mara.png"), str(entry))
    check("re-plan updates description",
          entry["description"] == "brisk, salt-weathered", str(entry))

    # Re-syncing the same name must not duplicate the cast.
    projects_service.sync_cast(pid, [{"name": "Mara"}, {"name": "Ivo"}])
    names = [c["name"] for c in projects_service.get_project(pid)["cast"]]
    check("cast stays unique by name", len(names) == len(set(names)), str(names))

    # A locked portrait saves GPU runs: only Ivo still needs one.
    check("locked portraits are free",
          projects_service.missing_portraits(pid, [{"name": "Mara"}, {"name": "Ivo"}]) == ["Ivo"],
          str(projects_service.missing_portraits(pid, [{"name": "Mara"}, {"name": "Ivo"}])))

    # A narrator is a voice, never a face: it must not cost a GPU run anywhere.
    cast_mixed = [{"name": "Mara"}, {"name": "Narrator"}]
    check("narrator needs no portrait",
          projects_service.missing_portraits(pid, cast_mixed) == [],
          str(projects_service.missing_portraits(pid, cast_mixed)))
    check("quota and worker agree on who has a face",
          [c["name"] for c in package_service.on_screen_characters(cast_mixed)] == ["Mara"],
          str(package_service.on_screen_characters(cast_mixed)))
    check("voice-only detection is case-insensitive",
          package_service.is_voice_only({"name": "voiceOver"}), "not detected")
    check("real characters are not voice-only",
          not package_service.is_voice_only({"name": "Mara"}), "wrongly detected")

    # No project (one-off film) -> every character needs a portrait.
    check("no project means no reuse",
          projects_service.missing_portraits("", [{"name": "Mara"}]) == ["Mara"])

    projects_service.delete_project(pid)


def test_character_library():
    """One identity per character, shared by Film, Storyboard, Studio, Director."""
    print("shared character library:")
    from app.services import characters as characters_service
    from app.services import projects as projects_service

    uid = "vidyra-dev-key"
    characters_service._memory_store.clear()

    # A face first built in Studio becomes reusable everywhere.
    mara = characters_service.save_character(uid, {
        "name": "Mara", "description": "brisk, weathered",
        "voice": "en_GB-alba-medium",
        "refImage": f"media/{uid}/studio/mara.png",
    })
    check("saved to library", mara["name"] == "Mara", str(mara))
    check("ref stored", mara["refImage"].endswith("mara.png"), str(mara["refImage"]))

    # Name lookup is the join key between features: case/space insensitive.
    check("found by name (different case + spaces)",
          (characters_service.find_by_name(uid, "  mArA ") or {}).get("characterId")
          == mara["characterId"], str(characters_service.find_by_name(uid, " mArA ")))
    check("ref_for returns the locked face",
          characters_service.ref_for(uid, "Mara") == mara["refImage"])
    check("unknown character has no ref", characters_service.ref_for(uid, "Zed") is None)

    # Same name saves to the same row — no duplicate identities.
    again = characters_service.save_character(uid, {"name": "Mara", "description": "older"})
    check("same name updates in place",
          again["characterId"] == mara["characterId"], str(again))
    check("library holds one Mara", len(characters_service.list_characters(uid)) == 1)

    # A partial update must NOT wipe a locked face or voice.
    check("partial update keeps ref + voice",
          again["refImage"] == mara["refImage"] and again["voice"] == mara["voice"],
          str(again))
    check("partial update applies description", again["description"] == "older", str(again))

    # Planning a film with the same character adopts the existing face.
    pid = projects_service.create_project(uid, "Library cast", "16:9")
    projects_service.sync_cast(pid, [{"name": "Mara", "description": "brisk, weathered"}])
    entry = next(c for c in projects_service.get_project(pid)["cast"] if c["name"] == "Mara")
    check("project cast adopts the library face",
          (entry.get("portrait") or "").endswith("mara.png"), str(entry))
    check("a saved face costs no GPU run",
          projects_service.missing_portraits(pid, [{"name": "Mara"}], uid) == [],
          str(projects_service.missing_portraits(pid, [{"name": "Mara"}], uid)))

    # Two faces named Mara in two projects must not fight over one identity.
    projects_service.save_portrait(pid, "Mara", f"media/{uid}/project/mara-2.png")
    check("project lock overrides library face",
          projects_service.locked_portraits(pid)["mara"].endswith("mara-2.png"),
          str(projects_service.locked_portraits(pid)))
    check("project lock is then reused for free",
          projects_service.missing_portraits(pid, [{"name": "Mara"}], uid) == [])

    # A face rendered in a film must reach the library, so Studio sees it too.
    characters_service.save_character(uid, {"name": "Ivo", "refImage": f"media/{uid}/film/ivo.png"})
    projects_service.sync_cast(pid, [{"name": "Ivo"}])
    check("film portrait reaches the library",
          (characters_service.ref_for(uid, "Ivo") or "").endswith("film/ivo.png"),
          str(characters_service.ref_for(uid, "Ivo")))

    # Another user's library is invisible.
    check("library is per-user",
          characters_service.ref_for("someone-else", "Mara") is None)

    projects_service.delete_project(pid)
    characters_service._memory_store.clear()


def test_library_endpoints():
    """The library over HTTP, and a storyboard character resolving from it."""
    print("character library endpoints:")
    from starlette.testclient import TestClient

    from app.main import app
    from app.services import characters as characters_service
    from app.services import jobs as jobs_service

    H = {"Authorization": "Bearer vidyra-dev-key"}
    UID = "dev-user"  # the uid dev-mode auth hands out
    characters_service._memory_store.clear()
    ref = f"media/{UID}/studio/mara.png"

    with TestClient(app) as client:
        check("list -> 200", client.get("/api/characters", headers=H).status_code == 200)
        check("empty at first", client.get("/api/characters", headers=H).json() == [])

        r = client.post("/api/characters", headers=H, json={
            "name": "Mara", "description": "brisk, weathered",
            "voice": "en_GB-alba-medium", "ref_image": ref})
        check("create -> 201", r.status_code == 201, r.text[:200])
        cid = r.json()["character_id"]
        check("ref_image returned", r.json()["ref_image"].endswith("mara.png"), r.text[:160])

        # Someone else's file must never be attachable as a face.
        r = client.post("/api/characters", headers=H, json={
            "name": "Ivo", "ref_image": "media/someone-else/x.png"})
        check("foreign ref rejected -> 400", r.status_code == 400, r.text[:160])

        # Upsert by name; omitted fields survive.
        r = client.post("/api/characters", headers=H, json={"name": "mara", "description": "older"})
        check("upsert same id", r.json()["character_id"] == cid, r.text[:160])
        check("partial keeps ref", r.json()["ref_image"].endswith("mara.png"), r.text[:160])
        check("partial keeps voice", r.json()["voice"] == "en_GB-alba-medium", r.text[:160])
        check("one character only", len(client.get("/api/characters", headers=H).json()) == 1)

        # A storyboard character given only a NAME resolves voice + face here.
        r = client.post("/api/jobs", headers=H, json={
            "title": "Board", "script": "Mara: hello there.",
            "settings": {"voice": "en_GB-alba-medium"},
            "media": [{"path": f"media/{UID}/a.png"}],
            "characters": [{"name": "Mara"}]})
        check("storyboard job -> 201", r.status_code == 201, r.text[:200])
        chars = jobs_service.get_job(r.json()["job_id"]).get("characters") or []
        check("storyboard voice resolved from library",
              any(c["name"] == "Mara" and c["voice"] == "en_GB-alba-medium" for c in chars),
              str(chars))
        check("storyboard face carried in from library",
              any(c.get("portrait", "").endswith("mara.png") for c in chars), str(chars))

        # An unknown character still has to be given a voice.
        r = client.post("/api/jobs", headers=H, json={
            "title": "Board2", "script": "Zed: hi.",
            "media": [{"path": f"media/{UID}/a.png"}],
            "characters": [{"name": "Zed"}]})
        check("unknown character still needs a voice -> 400", r.status_code == 400, r.text[:160])

        check("delete -> 204", client.delete(f"/api/characters/{cid}", headers=H).status_code == 204)
        check("gone after delete", client.get("/api/characters", headers=H).json() == [])

    characters_service._memory_store.clear()


def test_front_door():
    """The homepage starts a session; the workspace must be able to reopen it.

    The homepage is the only way in now, so a failed agent call has to stay
    recoverable: the idea survives, the error is legible, and the UI can fall
    back to the one-shot film flow instead of dead-ending.
    """
    print("front door contract:")
    from starlette.testclient import TestClient

    from app.main import app
    from app.services import planner
    from app.services import projects as projects_service

    H = {"Authorization": "Bearer vidyra-dev-key"}

    # No LLM configured: a clean 503 the UI can fall back from.
    with TestClient(app) as client:
        r = client.post("/api/films/projects", headers=H,
                        json={"message": "a shy robot", "aspect": "auto"})
        check("missing agent -> 503, not a crash", r.status_code == 503,
              f"{r.status_code} {r.text[:120]}")
        check("error explains the cause", "LLM" in r.text or "agent" in r.text.lower(),
              r.text[:160])
        items = client.get("/api/films/projects", headers=H).json()["items"]
        check("idea survives a failed first call",
              any("shy robot" in (p["title"] or "") for p in items),
              str([p["title"] for p in items]))

    # With an agent: 201 plus a project the workspace can reopen by id.
    original = planner._call
    planner._call = lambda *a, **k: ADVANCE_QA
    try:
        with TestClient(app) as client:
            r = client.post("/api/films/projects", headers=H,
                            json={"message": "a lighthouse that goes dark", "aspect": "auto"})
            check("with agent -> 201", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
            pid = r.json()["project"]["projectId"]
            check("returns a project id to deep-link to", bool(pid), str(r.json())[:120])

            g = client.get(f"/api/films/projects/{pid}", headers=H)
            check("deep link reopens the project", g.status_code == 200, str(g.status_code))
            check("thread restored on reopen",
                  any(m["content"] == "a lighthouse that goes dark"
                      for m in g.json()["project"]["messages"]),
                  str(g.json()["project"]["messages"])[:160])
            check("appears in the resumable list",
                  any(p["projectId"] == pid
                      for p in client.get("/api/films/projects", headers=H).json()["items"]))
    finally:
        planner._call = original
        projects_service._memory_store.clear()


def test_uploaded_character_drives_a_film():
    """A photo uploaded in the Cast is the same face every later video reuses."""
    print("uploaded character -> film:")
    import io as _io
    from starlette.testclient import TestClient

    from app.main import app
    from app.services import characters as characters_service

    uid = "dev-user"  # the uid dev-mode auth hands out
    characters_service._memory_store.clear()
    H = {"Authorization": "Bearer vidyra-dev-key"}  # the configured dev key

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c6360000002000100ffff03000006"
        "0005570d3f0000000049454e44ae426082")

    with TestClient(app) as client:
        # The Cast page uses the normal media pipeline -- no bespoke uploader.
        up = client.post("/api/uploads", headers=H,
                         files={"file": ("biscuit.png", _io.BytesIO(png), "image/png")})
        check("photo uploads through the media pipeline", up.status_code == 201, up.text[:200])
        if up.status_code != 201:
            return
        path = up.json()["items"][0]["storagePath"]
        check("stored under the user's own prefix", path.startswith(f"media/{uid}/"), path)

        # A character needs a face, not an audio or video clip.
        bad = client.post("/api/characters", headers=H,
                          json={"name": "Bad", "ref_image": f"media/{uid}/clip.mp3"})
        check("non-image reference rejected", bad.status_code == 400, f"{bad.status_code} {bad.text[:120]}")

        made = client.post("/api/characters", headers=H, json={
            "name": "Biscuit", "description": "orange tabby, green eyes", "ref_image": path})
        check("character saved with the uploaded photo", made.status_code == 201, made.text[:200])
        if made.status_code != 201:
            return

        # The Cast page renders faces through /uploads/preview.
        pv = client.get("/api/uploads/preview", headers=H, params={"path": path})
        check("cast preview serves the photo", pv.status_code == 200
              and pv.json().get("url", "").startswith("http"), pv.text[:160])

        film = {
            "idea": "", "style": "cinematic", "captions": False, "music": "",
            "research": False, "lip_sync": False, "scenes_count": 2,
            "characters": [{"name": "Biscuit", "description": "orange tabby"}],
            "outline": json.dumps({
                "title": "Biscuit Rides Again", "style": "cinematic",
                "characters": [{"name": "Biscuit", "description": "orange tabby, green eyes"}],
                "scenes": [
                    {"narration": "Biscuit owned the whole street.", "dialogue": {},
                     "setting": "a sunny pavement, 9:16 vertical", "shot": "low angle",
                     "transition": "fade"},
                    {"narration": "And nobody disagreed.", "dialogue": {},
                     "setting": "a park bench, 9:16 vertical", "shot": "wide",
                     "transition": "fade"},
                ]}),
        }
        r = client.post("/api/films", headers=H, json=film)
        check("film accepted", r.status_code == 201, r.text[:200])
        if r.status_code != 201:
            return
        jid = r.json()["job_id"]

        deadline = time.time() + 300
        body = {}
        while time.time() < deadline:
            body = client.get(f"/api/jobs/{jid}", headers=H).json()
            if (body.get("status") or "") in ("completed", "failed"):
                break
            time.sleep(1)
        check("film completed", body.get("status") == "completed",
              str(body.get("status")) + " " + str(body.get("error", "")))
        if body.get("status") != "completed":
            return

        titles = [x.get("title") or "" for x in
                  client.get("/api/studio/runs", headers=H).json()]
        check("no portrait run -- the uploaded photo was reused",
              not any("portrait" in t for t in titles), str(titles))
        check("a scene was restyled from that locked face",
              any("(locked)" in t for t in titles), str(titles))

        # Filming must not overwrite the face the creator uploaded.
        after = characters_service.find_by_name(uid, "Biscuit")
        check("uploaded face preserved after filming",
              (after or {}).get("refImage") == path, str((after or {}).get("refImage")))

    characters_service._memory_store.clear()


def main() -> int:
    test_brief_units()
    test_plan_roundtrip()
    test_requires_llm()
    test_advance_with_mock()
    test_unfilmable_plan_raises()
    test_cast_registry()
    test_character_library()
    test_library_endpoints()
    test_uploaded_character_drives_a_film()
    test_front_door()
    test_endpoints()
    test_full_planner_fake_mode()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PLANNER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
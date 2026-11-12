"""Auto Film endpoints.

POST /api/films turns a one-line idea into a finished multi-scene film. The
film worker writes the script (LLM or templates), films every scene through
AI Studio (2 GPU runs per scene), then edits it with the storyboard engine.

Film jobs land in the normal jobs collection (type "film"), so existing
status / My Videos / Watch pages work unchanged.
"""

from __future__ import annotations


import re

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from pipeline import aspect as aspect_lib
from pipeline.audio import clamp_music_volume
from pipeline.narration import is_clone_voice
from pipeline.utils import classify_media

from ..auth import CurrentUser, Depends, get_current_user
from ..config import Settings
from ..models import (
    CharacterIn,
    CharacterOut,
    CreateOut,
    FilmCreate,
    ProjectCreate,
    ProjectMessageIn,
    ProjectPlanIn,
    ProjectRenderIn,
    ValidatePackageRequest,
)
from ..services import characters as characters_service
from ..services import jobs as jobs_service
from ..services import package as package_service
from ..services import planner as planner_service
from ..services import profiles
from ..services import projects as projects_service
from ..services import studio as studio_service
from ..services import usage as usage_service
from ..services import voice_refs
from ..services import workspaces as workspace_service
from ..services.ratelimit import rate_limiter
from ..workers.film_worker import clone_voice_runs, film_runs_needed
from ..workers.remote_worker import PipelineWorker

router = APIRouter()


@router.post("/films", response_model=CreateOut, status_code=status.HTTP_201_CREATED)
async def create_film(
    payload: FilmCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)

    if not s.studio_enabled:
        raise HTTPException(
            status_code=503,
            detail="AI Studio isn't configured. Set RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID to generate films.",
        )

    idea = payload.idea.strip()
    outline_raw = (payload.outline or "").strip()
    if not idea and not outline_raw:
        raise HTTPException(status_code=400, detail="Send an idea or a production package.")

    workspace_id = (payload.workspace_id or "").strip()
    if workspace_id:
        try:
            workspace_service.owner_workspace(workspace_id, user.uid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Workspace not found.")

    style = (payload.style or "cinematic").strip()
    scenes_count = max(2, min(int(payload.scenes_count), int(s.film_max_scenes)))
    characters = []
    for c in payload.characters:
        if not (c.name or "").strip():
            continue
        voice = (c.voice or "").strip()
        voice_refs.owned_clone_ref(voice, user.uid)  # rejects foreign/invalid refs
        characters.append(
            {"name": c.name.strip(), "description": c.description.strip(), "voice": voice}
        )

    film_outline = None
    title = idea[:120] or "Untitled Film"
    if outline_raw:
        # The user pasted a full production package (JSON or text) — the agent
        # executes it as written; it supplies its own scenes, characters, style.
        try:
            # compile_package may call the LLM to structure free text; that
            # blocks, so it runs on a worker thread with the rest of the app
            # still serving.
            film_outline = await run_in_threadpool(
                package_service.compile_package, outline_raw, payload.aspect
            )
        except package_service.PackageError as exc:
            raise HTTPException(status_code=400, detail={"issues": exc.issues})
        scenes = film_outline.get("scenes") or []
        scenes_count = len(scenes)
        style = film_outline.get("style") or style
        characters = film_outline.get("characters") or []
        title = film_outline.get("title") or title

    # The Characters page promises "mention them by name": a saved character
    # named in the idea joins the cast, and every blank description/voice is
    # filled from that saved identity (the worker already reuses the locked
    # face via ref_for, so voice and written look now stay in step with it).
    # A pasted package brings its own cast, which is filled here too.
    _adopt_saved_cast(user.uid, "" if film_outline else idea, characters)

    # GPU runs this film will spend: 2 per scene, +1 per character whose face
    # still needs locking, +1 per dialogue scene when acting is on, +1 per
    # distinct cloned voice. `film_runs_needed` is the same function the worker
    # enforces. It used to be a private copy of "scenes x 2 + characters" that
    # omitted acting and cloned voices, so a film could be approved here and
    # then rejected by the worker with a quota error after the user waited.
    film_project_id = ""
    if (payload.project_id or "").strip():
        try:
            film_project_id = projects_service.owner_project(
                payload.project_id.strip(), user.uid
            )["projectId"]
        except projects_service.ProjectError:
            raise HTTPException(status_code=404, detail="Project not found.")

    acting = payload.lip_sync and (s.runpod_enabled or s.runpod_fake)
    # A pasted package declares its own scenes, so their dialogue count is exact
    # -- and that is the path the planning UI quotes. An idea-only film has no
    # scenes until the agent writes them, so its estimate is a lower bound and
    # the worker still re-checks the real number before spending anything.
    declared_scenes = scenes if film_outline else []
    runs_needed = film_runs_needed(
        declared_scenes or [{} for _ in range(scenes_count)],
        acting=acting and bool(declared_scenes),
        new_locks=len(
            projects_service.missing_portraits(film_project_id, characters, user.uid)
        ),
        clone_voices=clone_voice_runs(
            film_outline or {"characters": characters},
            payload.voice,
            runpod_fake=s.runpod_fake,
        ),
    )
    used = studio_service.daily_used(user.uid)
    remaining = max(0, int(s.studio_max_runs_per_day) - used)
    if remaining < runs_needed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"{scenes_count} scenes need {runs_needed} AI Studio runs, but only "
                f"{remaining} of your daily quota ({s.studio_max_runs_per_day}) is left. "
                "Shorten the film or try again tomorrow."
            ),
        )

    # Workspace budget is an additional ceiling (0 = unlimited). It counts
    # every GPU entry for this brand today, including Studio tools.
    if workspace_id:
        doc = workspace_service.get_doc(workspace_id) or {}
        budget = int((doc.get("budget") or {}).get("dailyRuns") or 0)
        if budget:
            spent = usage_service.daily_runs(user.uid, workspace_id)
            if spent + runs_needed > budget:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"This workspace allows {budget} GPU runs/day and {spent} "
                        f"were already used. This film needs {runs_needed} more."
                    ),
                )

    film = {
        "idea": idea,
        "style": style,
        "scenes_count": scenes_count,
        "characters": characters,
        "research": bool(payload.research),
        "lip_sync": bool(payload.lip_sync),
        "aspect": _validate_aspect(payload.aspect),
    }
    if film_project_id:
        film["projectId"] = film_project_id
        # A project film also seeds the project cast from its package, so the
        # plan's characters are the project's identities.
        projects_service.sync_cast(film_project_id, characters)
    if film_outline:
        film["outline"] = film_outline

    settings = _base_settings(
        user.uid, payload.captions, payload.music, payload.music_volume, payload.aspect
    )
    # Workspace look fills choices the request left at their "unset" sentinels.
    # The production package still overrides these just below.
    brand = workspace_service.resolve_defaults(user.uid, workspace_id)
    if not (payload.voice or "").strip() and brand.get("voice"):
        settings["voice"] = brand["voice"]
    if not (payload.music or "").strip() and brand.get("music"):
        settings["music"] = brand["music"]
        settings["music_volume"] = brand.get("music_volume", settings["music_volume"])
    if (payload.aspect or "auto") == "auto" and brand.get("aspect"):
        settings["aspect"] = brand["aspect"]
    if brand.get("page_name"):
        settings["page_name"] = brand["page_name"]
    for key in (
        "speech_rate", "pacing", "hook", "hook_style", "captions", "caption_style",
        "font_size", "words_per_line", "caption_position", "transition", "reframe",
        "show_page_name",
    ):
        if brand.get(key) not in (None, ""):
            settings[key] = brand[key]
    if film_outline:
        settings = _merge_production_settings(film_outline, settings, user.uid)
    narrator_voice = voice_refs.owned_clone_ref((payload.voice or "").strip(), user.uid)
    if narrator_voice:
        settings["voice"] = (payload.voice or "").strip()
    music = (settings.get("music") or "").strip()
    if music.startswith("media/") and not music.startswith(f"media/{user.uid}/"):
        raise HTTPException(status_code=400, detail="Music track does not belong to you.")

    timeline = payload.timeline if isinstance(payload.timeline, dict) else {}
    prefixes = tuple(f"{p}{user.uid}/" for p in ("media/", "studio/", "videos/"))
    for key in ("overlays", "audio"):
        for item in timeline.get(key) or []:
            if not isinstance(item, dict):
                continue
            path = (item.get("path") or "").replace("\\", "/")
            if path and not path.startswith(prefixes):
                raise HTTPException(
                    status_code=400, detail="Timeline media does not belong to you."
                )

    job_id = jobs_service.create_film_job(
        uid=user.uid,
        title=title,
        film=film,
        settings=settings,
        niche=settings.get("page_name") or profiles.get_page_name(user.uid),
        workspace_id=workspace_id,
        timeline=timeline or None,
    )

    worker: PipelineWorker = request.app.state.film_worker
    await worker.submit(job_id)

    return CreateOut(job_id=job_id)


def _adopt_saved_cast(uid: str, idea: str, characters: list) -> None:
    """Adopt the saved character library by name for one film's cast.

    With an idea, every saved character named in it joins the cast (the
    Characters page says "mention them by name"). Then blank description or
    voice fields are filled from the saved identity, matched case-insensitively
    by name, so reusing a locked face can never fork into a second voice.
    """
    if not isinstance(characters, list):
        return
    saved_list = characters_service.list_characters(uid)
    if idea:
        declared = {
            characters_service.key_of(c.get("name"))
            for c in characters
            if isinstance(c, dict)
        }
        for saved in saved_list:
            name = str(saved.get("name") or "").strip()
            if not name or characters_service.key_of(name) in declared:
                continue
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", idea, flags=re.IGNORECASE):
                characters.append({"name": name, "description": "", "voice": ""})
                declared.add(characters_service.key_of(name))
    saved_by_key = {
        characters_service.key_of(str(c.get("name") or "")): c for c in saved_list
    }
    for c in characters:
        if not isinstance(c, dict):
            continue
        c["name"] = characters_service.clean_name(str(c.get("name") or ""))
        saved = saved_by_key.get(characters_service.key_of(str(c.get("name") or "")))
        if not saved:
            continue
        if not str(c.get("description") or "").strip():
            c["description"] = str(saved.get("description") or "")
        if not str(c.get("voice") or "").strip():
            voice = str(saved.get("voice") or "").strip()
            if is_clone_voice(voice):
                try:
                    voice_refs.owned_clone_ref(voice, uid)
                except HTTPException:
                    voice = ""  # a foreign or non-audio ref never reaches a job
            if voice:
                c["voice"] = voice


def _validate_aspect(value: str) -> str:
    """Accept only ratios pipeline.aspect knows about, or 400."""
    aspect = (value or aspect_lib.AUTO).strip()
    if not aspect_lib.is_valid(aspect):
        raise HTTPException(status_code=400, detail=f"Unknown aspect ratio: {aspect}")
    return aspect


def _base_settings(
    user_uid: str,
    captions: bool,
    music: str,
    music_volume: float,
    aspect: str = aspect_lib.AUTO,
) -> dict:
    allowed = {
        "speech_rate", "pacing",
        "hook", "hook_style", "hook_text", "hook_subtext", "hook_duration",
        "captions", "caption_style", "font_size", "words_per_line", "caption_position",
        "music", "music_volume", "transition", "transition_duration", "reframe",
        "color_brightness", "color_contrast", "color_saturation", "normalize_audio",
        "show_page_name", "page_name", "voice",
        # Output shape: a ratio from pipeline.aspect, or explicit pixels.
        "aspect", "width", "height",
    }
    settings = {
        "speech_rate": 1.0,
        "pacing": "Normal Sports Story",
        "hook": True,
        "hook_style": "Breaking News",
        "hook_text": "",
        "hook_subtext": "",
        "captions": bool(captions),
        "caption_style": "Karaoke",
        "font_size": 58,
        "words_per_line": 5,
        "caption_position": "Bottom",
        "music": music.strip(),
        "music_volume": clamp_music_volume(music_volume),
        "transition": "Auto",
        "reframe": "auto",
        "aspect": _validate_aspect(aspect),
        "show_page_name": True,
        "page_name": profiles.get_page_name(user_uid),
    }
    return {k: v for k, v in settings.items() if k in allowed}


def _merge_production_settings(outline: dict, settings: dict, user_uid: str) -> dict:
    """Let a production package override the global film settings (pacing, hook,
    music, transitions…). Only keys the pipeline understands are applied."""
    allowed = {
        "speech_rate", "pacing",
        "hook", "hook_style", "hook_text", "hook_subtext", "hook_duration",
        "captions", "caption_style", "font_size", "words_per_line", "caption_position",
        "music", "music_volume", "transition", "transition_duration", "reframe",
        "color_brightness", "color_contrast", "color_saturation", "normalize_audio",
        "show_page_name", "page_name", "voice",
        # Output shape: a ratio from pipeline.aspect, or explicit pixels.
        "aspect", "width", "height",
    }
    hooks = outline.get("hook")
    if isinstance(hooks, dict):
        settings["hook"] = bool(hooks.get("enabled", True))
        for k in ("style", "text", "subtext"):
            v = hooks.get(k)
            if v:
                settings[f"hook_{k}"] = str(v)
    elif isinstance(hooks, bool):
        settings["hook"] = hooks
    for k in ("pacing", "music", "transition", "captions", "show_page_name"):
        if outline.get(k) is not None:
            settings[k] = outline[k]
    settings["music_volume"] = clamp_music_volume(settings.get("music_volume"))
    return {k: v for k, v in settings.items() if k in allowed}

@router.post("/films/validate-package")
async def validate_film_package(
    payload: ValidatePackageRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Zero-GPU preview of a pasted production package (JSON or text).

    Compiles and validates it, returning the normalized outline plus any issues
    so the frontend can show a preview card before the user commits GPU runs.
    """
    # No rate limit — this spends only a parser/LLM call, never GPU.
    try:
        # May consult the LLM, so it runs off the event loop.
        outline = await run_in_threadpool(package_service.compile_package, payload.text)
        issues: list[str] = []
    except package_service.PackageError as exc:
        return {"valid": False, "issues": exc.issues, "outline": exc.partial}
    scenes = outline.get("scenes") or []
    chars = outline.get("characters") or []
    return {
        "valid": True,
        "issues": [],
        "outline": outline,
        "title": outline.get("title", ""),
        "charactersCount": len(chars),
        "scenesCount": len(scenes),
        # Quoted from the worker's own formula so the number the user approves
        # is the number the render will be charged.
        "runsNeeded": film_runs_needed(
            scenes,
            acting=bool(outline.get("lip_sync", True))
            and (request.app.state.settings.runpod_enabled
                 or request.app.state.settings.runpod_fake),
            new_locks=len(chars),
            clone_voices=clone_voice_runs(
                outline,
                outline.get("voice") or "",
                runpod_fake=request.app.state.settings.runpod_fake,
            ),
        ),
    }


# --------------------------------------------------------------------------
# Planning agent (Flow-style "plan before you spend")
# --------------------------------------------------------------------------
#
# The agent interviews the creator, drafts a production plan, and the creator
# edits it. Nothing here touches a GPU. Rendering happens only when the plan is
# submitted to POST /films as a production package, which is what makes the
# handoff free — the film worker is unchanged.


def _project_out(project: dict, s: "Settings | None" = None) -> dict:
    plan = project.get("plan")
    scenes = (plan or {}).get("scenes") or []
    chars = (plan or {}).get("characters") or []
    # Same formula the worker charges with (see `film_runs_needed`), so the
    # figure beside "Film this" is the real cost rather than a stale estimate
    # that omitted acting and cloned voices.
    if plan:
        acting = bool(plan.get("lip_sync", True)) and bool(
            s and (s.runpod_enabled or s.runpod_fake)
        )
        runs_needed = film_runs_needed(
            scenes,
            acting=acting,
            new_locks=len(
                projects_service.missing_portraits(
                    project.get("projectId", ""), chars, project.get("userId", "")
                )
            ),
            clone_voices=clone_voice_runs(
                plan, plan.get("voice") or "", runpod_fake=bool(s and s.runpod_fake)
            ),
        )
    else:
        # No plan yet means nothing to spend.
        runs_needed = 0
    return {
        "projectId": project.get("projectId", ""),
        "title": project.get("title", ""),
        "status": project.get("status", "planning"),
        "aspect": project.get("aspect", "auto"),
        "brief": project.get("brief") or {},
        "questions": project.get("questions") or [],
        "messages": project.get("messages") or [],
        "plan": plan,
        "cast": project.get("cast") or [],
        "jobId": project.get("jobId"),
        "scenesCount": len(scenes),
        "charactersCount": len(chars),
        "runsNeeded": runs_needed,
        "createdAt": project.get("createdAt", ""),
        "updatedAt": project.get("updatedAt", ""),
    }


def _owned(project_id: str, uid: str) -> dict:
    try:
        return projects_service.owner_project(project_id, uid)
    except projects_service.ProjectError:
        raise HTTPException(status_code=404, detail="Project not found.")


def _run_turn(
    project: dict,
    message: str,
    aspect: str,
    message_stored: bool = False,
    s: "Settings | None" = None,
) -> dict:
    """One planning turn: talk to the LLM, persist the result, shape the reply.

    The creator's message is stored *before* the agent runs, so a draft that
    fails validation costs them a retry of the LLM call but never their words.
    """
    project_id = project["projectId"]
    if not message_stored:
        projects_service.append_messages(project_id, [{"role": "user", "content": message}])

    try:
        turn = planner_service.advance(project, message, aspect)
    except planner_service.PlannerError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except package_service.PackageError as exc:
        # The model drafted something the crew can't execute. Surface the
        # issues so the creator can ask for another attempt instead of
        # discovering it after spending GPU runs.
        raise HTTPException(status_code=422, detail={"issues": exc.issues})

    thread = projects_service.append_messages(
        project_id,
        [{"role": "assistant", "content": turn["reply"]}] if turn["reply"] else [],
    )
    updates = {
        "brief": turn["brief"],
        "questions": turn["questions"],
        "status": turn["status"],
        "messages": thread,
    }
    # Only overwrite the plan when a new one was actually drafted — a turn that
    # just answered a question must not wipe the plan the creator already edited.
    if turn["plan"] is not None:
        updates["plan"] = turn["plan"]
    projects_service.update_project(project_id, **updates)
    return {
        "project": _project_out(projects_service.get_project(project_id) or {}, s),
        "reply": turn["reply"],
    }


@router.post("/films/projects", status_code=status.HTTP_201_CREATED)
async def create_planning_project(
    payload: ProjectCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Open a planning session and get the agent's first questions."""
    aspect = _validate_aspect(payload.aspect)
    project_id = projects_service.create_project(
        user.uid, payload.message.strip(), aspect=aspect
    )
    project = projects_service.get_project(project_id) or {}
    # create_project already stored the opening message.
    # A planning turn is an LLM round-trip measured in seconds. Called inline
    # it would freeze job polling, uploads and every other request until the
    # agent answered, so it runs on a worker thread.
    return await run_in_threadpool(
        _run_turn,
        project,
        payload.message.strip(),
        aspect,
        message_stored=True,
        s=request.app.state.settings,
    )


@router.get("/films/projects")
async def list_planning_projects(
    request: Request, user: CurrentUser = Depends(get_current_user)
):
    """Recent planning sessions, newest first, so a project can be resumed."""
    s = request.app.state.settings
    return {
        "items": [
            _project_out(p, s) for p in projects_service.list_projects(user.uid, limit=30)
        ]
    }


@router.get("/films/projects/{project_id}")
async def get_planning_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    return {
        "project": _project_out(_owned(project_id, user.uid), request.app.state.settings)
    }


@router.post("/films/projects/{project_id}/messages")
async def send_planning_message(
    project_id: str,
    payload: ProjectMessageIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Continue the interview, or revise the plan once one exists."""
    # Rate limited: every turn is an LLM call, and this is free-form input.
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)
    project = _owned(project_id, user.uid)
    aspect = project.get("aspect", "auto")
    return await run_in_threadpool(
        _run_turn, project, payload.message.strip(), aspect, s=s
    )


@router.patch("/films/projects/{project_id}/plan")
async def save_planning_plan(
    project_id: str,
    payload: ProjectPlanIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Save the creator's edited plan. Re-validated so the crew can run it."""
    project = _owned(project_id, user.uid)
    # A filmed project keeps its `jobId` as the record of what was rendered.
    # Forcing status back to `planned` here would leave the two disagreeing and
    # silently detach the finished film, so refuse rather than corrupt it.
    if project.get("status") == "filmed" or project.get("jobId"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This project has already been filmed. Start a new project to "
                "plan changes, so the finished film keeps its link."
            ),
        )
    plan = dict(payload.plan or {})
    if (plan.get("scenes") or []):
        try:
            outline = package_service.normalize_package(plan)
        except package_service.PackageError as exc:
            raise HTTPException(status_code=422, detail={"issues": exc.issues})
    else:
        # Deleting the last storyboard scene "saves back" an empty plan. The
        # firming pipeline still refuses empty outlines (validate runs again at
        # create_film), but the cleared plan round-trips its global keys and
        # wipes the storyboard instead of 422-"remove everything".
        outline, _issues = package_service.validate_package(plan)
        outline["scenes"] = []
    # The plan's characters become the project's cast. Portraits already locked
    # from earlier films survive a re-plan.
    projects_service.sync_cast(project_id, outline.get("characters") or [])
    projects_service.update_project(
        project_id,
        plan=outline,
        status="planned",
        questions=[],
        title=(outline.get("title") or project.get("title", ""))[:80],
    )
    return {
        "project": _project_out(
            projects_service.get_project(project_id) or {}, request.app.state.settings
        )
    }


@router.post("/films/projects/{project_id}/render")
async def mark_planning_project_filmed(
    project_id: str,
    payload: ProjectRenderIn,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Record which job a project was filmed as, after POST /films succeeds."""
    _owned(project_id, user.uid)
    # The job must exist and be this user's. Accepting any string both leaks a
    # foreign job id back through the response and, because `save_planning_plan`
    # refuses to edit a project that has a jobId, one typo would lock the
    # project out of editing for good.
    job_id = (payload.job_id or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="A job id is required.")
    job = jobs_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("userId") != user.uid:
        raise HTTPException(status_code=404, detail="Job not found.")
    projects_service.update_project(project_id, jobId=job_id, status="filmed")
    return {
        "project": _project_out(
            projects_service.get_project(project_id) or {}, request.app.state.settings
        )
    }


@router.delete("/films/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_planning_project(
    project_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _owned(project_id, user.uid)
    projects_service.delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# --------------------------------------------------------------------------
# Character library — the shared identity behind every feature
# --------------------------------------------------------------------------
#
# Auto Film, Storyboard, Studio and the Director all read and write this one
# registry, so a character built in one feature is the same character in the
# others. Mounted under /films because character work is part of directing;
# the paths are feature-neutral.


def _char_out(char: dict) -> dict:
    return {
        "character_id": char.get("characterId", ""),
        "name": char.get("name", ""),
        "description": char.get("description", ""),
        "voice": char.get("voice", ""),
        "ref_image": char.get("refImage") or None,
        "created_at": char.get("createdAt", ""),
        "updated_at": char.get("updatedAt", ""),
    }


def _validate_ref(uid: str, ref: str) -> str:
    """A reference image must be one of the user's own stored images.

    Uses the same classifier and prefixes as the upload pipeline, so a character
    can only point at a file Vidyra actually stored for this user, and only at
    an image — a character needs a face, not an audio clip.
    """
    ref = (ref or "").strip()
    if not ref:
        return ""
    if not ref.startswith(tuple(f"{p}{uid}/" for p in characters_service.REF_PREFIXES)):
        raise HTTPException(
            status_code=400, detail=f"Reference image does not belong to you: {ref}"
        )
    if classify_media(ref) != "image":
        raise HTTPException(
            status_code=400, detail="A character reference must be an image file."
        )
    return ref


@router.get("/characters", response_model=list[CharacterOut])
async def list_character_library(
    user: CurrentUser = Depends(get_current_user),
):
    """Every saved character, newest first."""
    return [_char_out(c) for c in characters_service.list_characters(user.uid)]


@router.post("/characters", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
async def save_character_library(
    payload: CharacterIn,
    user: CurrentUser = Depends(get_current_user),
):
    """Create or update a character. Matches an existing entry by name.

    Partial updates keep the fields you don't mention, so saving just a new
    description never discards a locked face or voice.
    """
    ref = _validate_ref(user.uid, payload.ref_image)
    voice = (payload.voice or "").strip()
    voice_refs.owned_clone_ref(voice, user.uid)  # rejects foreign/invalid refs
    try:
        char = characters_service.save_character(user.uid, {
            "name": payload.name,
            "description": payload.description,
            "voice": voice,
            "refImage": ref,
        })
    except characters_service.CharacterError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _char_out(char)


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character_library(
    character_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        characters_service.delete_character(user.uid, character_id)
    except characters_service.CharacterError:
        raise HTTPException(status_code=404, detail="Character not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

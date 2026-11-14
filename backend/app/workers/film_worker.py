"""Auto Film worker.

Turns a one-line idea into a finished multi-scene film, end to end:

    directing  → LLM (or template) writes title, characters, and scene-by-scene
                  narration + dialogue, plus visual/camera prompts.
    filming     → for each scene, runs AI Studio Text→Image then Image→Video on
                  the generated frame (2 GPU runs per scene, against the daily
                  studio quota), falling back to a still if video fails.
    editing     → hands all clips + script + character voices to the storyboard
                  engine, which narrates, captions, hooks, and mixes music.

Runs on a thread so the asyncio loop stays responsive, same pattern as
VideoWorker.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import replace

from pipeline.config import RenderSettings
from pipeline.narration import CLONE_VOICE_PREFIX, create_narration, is_clone_voice
from pipeline.storyboard import generate_storyboard
from pipeline.utils import classify_media, get_duration, run_cmd
from pipeline.voices import find_voices

from pipeline import aspect as aspect_lib

from ..config import Settings
from ..services import characters as characters_service
from ..services import factcheck
from ..services import imagebrief
from ..services import jobs as jobs_service
from ..services import package as package_service
from ..services import projects as projects_service
from ..services import scriptwriter
from ..services import studio as studio_service

# Per-model run params for the visuals a film generates (narrow, cost-aware).
_IMAGE_PARAMS = {
    "sdxl": {"width": 896, "height": 1600, "steps": 30, "cfg": 7.0},
}
_VIDEO_PARAMS = {
    "wan21-i2v": {"width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 5.0, "denoise": 1.0},
    "wan21-flf2v": {"width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 5.0, "denoise": 1.0},
    # Wan2.2 camera control runs a two-pass 14B (high/low noise) with a gentler
    # cfg, mirroring the official ComfyUI Fun Camera workflow.
    "wan22-cam-i2v": {"width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 3.5, "denoise": 1.0},
}
# Lip-sync a full moving shot (Wav2Lip class) — no diffusion knobs, just framing.
_ACTING_PARAMS = {"width": 832, "height": 480, "frames": 81, "fps": 16}
_DEFAULT_IMAGE_PARAMS = {"width": 896, "height": 1600, "steps": 30, "cfg": 7.0}
_IMG2IMG_PARAMS = {"width": 896, "height": 1600, "steps": 30, "cfg": 6.0, "denoise": 0.45}
_DEFAULT_VIDEO_PARAMS = {"width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 5.0, "denoise": 1.0}

#: Films and podcasts are portrait-first products (the writers are prompted for
#: "9:16 vertical"), so this is the shape "auto" resolves to for them.
DEFAULT_FILM_ASPECT = "9:16"

# How one scene's progress slice is shared between the passes that film it: the
# still frame first, then the motion clip, then the lip-sync. Values are
# fractions of the scene's slice, so the bar keeps moving *within* a single
# scene instead of sitting on the scene's start value for minutes.
_PASS_WEIGHTS = {
    "txt2img": (0.0, 0.45),
    "img2img": (0.0, 0.45),
    "character": (0.0, 0.45),
    "img2video": (0.45, 0.85),
    "lipsync": (0.85, 1.0),
}


def _scene_slice(scene: int, scene_count: int) -> tuple[int, int]:
    """(start, span) of a scene's slice of the 10-80% filming band."""
    base = 10 + int(70 * (scene - 1) / max(1, scene_count))
    nxt = 10 + int(70 * scene / max(1, scene_count))
    return base, max(1, nxt - base)


def _scene_frames(duration, fps: int, default: int) -> int:
    """Convert a per-scene length in seconds to a Wan-valid frame count.

    Wan image-to-video models require ``4n+1`` frames; snap up to the nearest
    valid count and clamp to a 2-8s window so a bad value can't blow up VRAM.
    Returns ``default`` when no duration was chosen.
    """
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        return default
    if seconds <= 0:
        return default
    seconds = max(2.0, min(8.0, seconds))
    frames = int(round(seconds * max(1, int(fps or 16))))
    frames = max(33, min(129, frames))
    remainder = (frames - 1) % 4
    if remainder:
        frames += 4 - remainder
    return frames


def _shape(params: dict, aspect: str) -> dict:
    """Re-shape one generated run's params to the film's aspect ratio.

    The per-model tables above were written for a vertical short, but they
    disagreed with each other: stills were 896x1600 portrait while the video
    pass rendered 832x480 landscape, so every scene's first frame was a tall
    image being re-framed into a wide box. Reshaping both to the same ratio at
    the single point every run passes through keeps the whole film consistent
    whatever shape was asked for.

    "auto" returns the params untouched, so existing vertical films render
    exactly as before.
    """
    out = dict(params or {})
    aspect = (aspect or "auto").strip()
    if aspect == "auto" or not aspect_lib.is_valid(aspect):
        return out
    width = int(out.get("width") or 0)
    height = int(out.get("height") or 0)
    budget = max(width, height)
    if budget <= 0:
        budget = aspect_lib.VIDEO_TIERS["sd"]
    out["width"], out["height"] = aspect_lib.resolve_size(aspect, budget)
    out["aspect"] = aspect
    return out


# The director's fallback direction when a package omits the camera move.
_SHOT_FALLBACKS = [
    "a slow push-in",
    "a gentle pan across",
    "a subtle zoom-in",
    "an orbit around the subject",
]

# Wan2.2 camera-control vocabulary (WanCameraEmbedding's camera_pose options),
# ordered so the most specific match wins.
_CAMERA_MOVES = [
    ("ClockWise (CW)", ("clockwise", "orbit", "circular", "around the subject", "round the subject", "arc around")),
    ("Anti Clockwise (ACW)", ("anticlockwise", "anti-clockwise", "counterclockwise", "counter-clockwise", "reverse orbit")),
    ("Zoom In", ("zoom in", "zoom-in", "push in", "push-in", "slow in", "dolly in", "dolly-in", "close up", "close-up", "creep in")),
    ("Zoom Out", ("zoom out", "zoom-out", "pull back", "pull-back", "push out", "dolly out", "dolly-out", "wide shot", "wide ")),
    ("Pan Left", ("pan left", "track left", "truck left", "move left", "crane left")),
    ("Pan Right", ("pan right", "pan across", "track right", "truck right", "move right", "crane right")),
    ("Pan Up", ("pan up", "tilt up", "crane up", "low angle", "rising", "lift up")),
    ("Pan Down", ("pan down", "tilt down", "crane down", "high angle", "pulling down")),
]


def _camera_params(shot: str) -> tuple:
    """Map a director's camera-move text ("a slow push-in") onto the Wan2.2
    camera-control vocabulary plus a speed multiplier. Unrecognized direction
    degrades to a gentle Zoom In so shots are never static by accident."""
    text = (shot or "").lower()
    if not text.strip():
        return "Zoom In", 1.0
    pose = None
    for candidate, keys in _CAMERA_MOVES:
        if any(k in text for k in keys):
            pose = candidate
            break
    if pose is None:
        if any(k in text for k in ("static", "still ", "locked off", "steady", "fixed")):
            pose = "Static"
        else:
            pose = "Zoom In"
    speed = 1.0
    if any(k in text for k in ("fast", "quick", "rapid", "speedy", "energetic")):
        speed = 1.6
    elif any(k in text for k in ("slow", "gentle", "subtle", "soft ", "calm")):
        speed = 0.6
    return pose, speed
_ALLOWED_TRANSITIONS = {
    "Auto", "fade", "slideleft", "slideright", "wipeleft", "wiperight", "zoomin",
}


def _coerce_transition(value) -> str:
    """Return a transition the editor supports ('Auto' for anything unknown)."""
    if not value:
        return "Auto"
    flat = str(value).strip()
    for allowed in _ALLOWED_TRANSITIONS:
        if flat.lower() == allowed.lower():
            return allowed
    return "Auto"


def _stable_seed(key: str, base: int = 0) -> int:
    """Deterministic seed for a character/scene (same name = same face).

    Single seed helper for the whole film worker — no other seed function
    exists here, so this is reuse-safe. 0 base = hash only.
    """
    import hashlib

    h = hashlib.sha256((key or "").strip().lower().encode()).hexdigest()
    return (int(h[:8], 16) + int(base or 0)) % 99999999


def _scene_character(sc: dict, characters: list) -> dict | None:
    """Match a scene to its dominant named character, if any.

    Priority: any dialogue speaker name (in cast order, so multi-speaker
    scenes still resolve), then name mention in setting/narration/shot text.
    Returns the character dict or None.
    Only matcher in this module — podcast path uses direct host lookup.
    """
    if not isinstance(sc, dict) or not characters:
        return None
    dialogue = sc.get("dialogue") or {}
    if isinstance(dialogue, dict) and dialogue:
        speakers = {str(k).strip().lower() for k in dialogue}
        for c in characters:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip().lower()
            if name and name in speakers:
                return c
    hay = " ".join(
        str(sc.get(k) or "") for k in ("setting", "narration", "shot")
    ).lower()
    for c in characters:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip().lower()
        if name and name in hay:
            return c
    return None


def adopt_library_identity(outline: dict, uid: str, declared: set) -> None:
    """Fill description/voice from the user's saved character library by name.

    The writer invents characters for idea-only films, so a name it makes up
    that matches the library adopts that identity's voice (the locked face
    already arrives separately via ref_for). `declared` holds name keys the
    client explicitly picked a voice for — those stay untouched, but a blank
    voice still resolves to the saved one before auto-assignment runs.
    """
    chars = [c for c in (outline or {}).get("characters") or [] if isinstance(c, dict)]
    if not chars:
        return
    saved_by_key = {
        characters_service.key_of(str(c.get("name"))): c
        for c in characters_service.list_characters(uid)
    }
    if not saved_by_key:
        return
    declared = declared or set()
    for c in chars:
        name = str(c.get("name") or "")
        key = characters_service.key_of(name)
        saved = saved_by_key.get(key)
        if not saved:
            continue
        if not str(c.get("description") or "").strip():
            c["description"] = str(saved.get("description") or "")
        if str(c.get("voice") or "").strip() and key in declared:
            continue  # the Film page's explicit pick wins
        voice = str(saved.get("voice") or "").strip()
        if is_clone_voice(voice):
            ref = voice[len(CLONE_VOICE_PREFIX):]
            if not ref.startswith((f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/")) \
                    or classify_media(ref) != "audio":
                voice = ""  # a foreign or non-audio ref never reaches a job
        if voice:
            c["voice"] = voice


class FilmError(RuntimeError):
    pass


def acting_scene_count(scenes: list, acting: bool) -> int:
    """Dialogue scenes, which each cost an extra acting/lip-sync GPU run."""
    if not acting:
        return 0
    return sum(1 for sc in scenes if isinstance(sc, dict) and sc.get("dialogue"))


def clone_voice_runs(outline: dict, default_voice: str, *, runpod_fake: bool = False) -> int:
    """Distinct cloned voices in this film, each of which costs one GPU run.

    A clone speaks every one of its lines in a single run and the lines are
    split locally afterwards, so the cost is per *voice*, not per line. Fake
    mode never reaches a real voice model, so clones resolve to local Piper
    audio there and cost nothing.
    """
    if runpod_fake:
        return 0
    refs: set[str] = set()
    if is_clone_voice(default_voice or ""):
        refs.add(default_voice)
    for char in outline.get("characters") or []:
        voice = char.get("voice") if isinstance(char, dict) else ""
        if is_clone_voice(voice or ""):
            refs.add(voice)
    return len(refs)


def film_runs_needed(
    scenes: list,
    *,
    acting: bool = False,
    new_locks: int = 0,
    clone_voices: int = 0,
) -> int:
    """Total AI Studio GPU runs one film spends.

    The single source of truth for the daily-quota guard. It lives here, next
    to the worker that actually spends the runs, because the route that
    pre-checks the quota and the number shown in the UI both used to carry their
    own copy of a formula that omitted acting and cloned voices -- so a film
    could be approved at creation and then die part-way through rendering.
    """
    return 2 * len(scenes) + acting_scene_count(scenes, acting) + new_locks + clone_voices


class FilmWorker:
    # Output shape for the job currently rendering; read by _run_studio when it
    # re-shapes each generated still and video pass. Class-level default so a
    # partially constructed worker (tests build one with __new__) still has it.
    _job_settings: dict = {}

    def __init__(self, settings: Settings, storage, video_worker, studio_worker):
        self.settings = settings
        self.storage = storage
        self.video_worker = video_worker
        self.studio_worker = studio_worker
        self._quality = "standard"
        self._semaphore = asyncio.Semaphore(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, job_id: str) -> None:
        task = asyncio.create_task(self._run(job_id))
        task.add_done_callback(self._on_done)

    def _on_done(self, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            pass  # handled inside _process

    async def _run(self, job_id: str) -> None:
        async with self._semaphore:
            await asyncio.to_thread(self._process, job_id)

    # ------------------------------------------------------------------
    # Sync processing (thread)
    # ------------------------------------------------------------------

    def _process(self, job_id: str) -> None:
        work_dir = os.path.join(self.settings.work_root, job_id)
        try:
            os.makedirs(work_dir, exist_ok=True)
            job = self._load(job_id)
            self._job_settings = dict(job.get("settings") or {})
            # Every film/podcast prompt already asks for "9:16 vertical", so
            # "auto" here means 9:16 rather than "whatever each param table
            # happened to say". That also settles the old mismatch where a
            # 1024x1792 portrait still was handed to an 832x480 landscape video
            # pass; both now render vertical at the same ratio.
            requested = str(self._job_settings.get("aspect") or "auto").strip()
            self._job_settings["aspect"] = (
                DEFAULT_FILM_ASPECT if requested == "auto" else requested
            )
            film = job.get("film") or {}
            quality = str(film.get("quality") or "standard").strip().lower()
            self._quality = quality if quality in ("draft", "standard", "high") else "standard"
            if not (self.settings.studio_enabled):
                raise FilmError(
                    "AI Studio isn't configured. Set RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID "
                    "(or RUNPOD_FAKE=1) to generate films."
                )

            outline = self._direct(job_id, job, film)
            if film.get("mode") == "podcast":
                self._film_podcast(job_id, job, outline, work_dir)
            else:
                self._film(job_id, job, outline, work_dir)
        except Exception as exc:
            self.video_worker._fail(job_id, exc)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Stage 1 — writing
    # ------------------------------------------------------------------

    @staticmethod
    def _director_review(outline: dict, context: dict) -> dict:
        """Director's pass over the final script.

        Reads the plan like a human director reading a brief: finds anything a
        scene cannot shoot (missing setting or camera move, unsupported
        transition, empty script), quietly fixes it, and keeps notes. A package
        from ChatGPT is treated as a draft, not gospel.
        """
        scenes = outline.get("scenes") or []
        idea = str(context.get("idea") or context.get("topic") or outline.get("title") or "a story")
        style = str(outline.get("style") or context.get("style") or "cinematic")
        notes: list[str] = []
        fixed = 0
        for i, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                continue
            if not str(sc.get("setting") or "").strip():
                sc["setting"] = f"{idea}, {style} cinematic scene"
                fixed += 1
                notes.append(f"Scene {i + 1}: wrote the missing setting from the idea.")
            shot = str(sc.get("shot") or "").strip()
            if not shot:
                sc["shot"] = _SHOT_FALLBACKS[i % len(_SHOT_FALLBACKS)]
                fixed += 1
                notes.append(f"Scene {i + 1}: set the camera move to “{sc['shot']}”.")
            provided_trans = str(sc.get("transition") or "").strip()
            trans = _coerce_transition(sc.get("transition"))
            sc["transition"] = trans
            if provided_trans and provided_trans != trans:
                notes.append(f"Scene {i + 1}: swapped unsupported transition for “{trans}”.")
            script = "\n\n".join(
                [sc.get("narration") or ""]
                + [f"{n}: {l}" for n, l in (sc.get("dialogue") or {}).items()]
            )
            if not script.strip():
                sc["narration"] = idea
                notes.append(f"Scene {i + 1}: the scene was empty — narrated the idea.")
            sc["setting"] = sc["setting"][:1500]
            sc["shot"] = sc["shot"][:1500]
        director = dict(outline.get("director") or {})
        director["reviewed"] = True
        director["fixed"] = director.get("fixed", 0) + fixed
        director["notes"] = list(director.get("notes") or []) + notes
        outline["director"] = director
        return director

    def _direct(self, job_id: str, job: dict, film: dict) -> dict:
        import copy as _copy

        uid = job.get("userId", "unknown")
        is_podcast = (film.get("mode") or "") == "podcast"
        idea = (film.get("idea") or film.get("topic") or "").strip()
        available = sorted(find_voices(self.settings.voice_dir).keys())
        if not available:
            raise FilmError("No Piper voices available on the server.")

        provided_outline = film.get("outline")
        if provided_outline:
            # Production-package path: the client wrote everything (instructions,
            # camera angles, scenes, dialogue) — execute it verbatim. Saved
            # library identities fill any blank voice/description first, then
            # Piper voices go in wherever the package still specified none.
            declared = {
                characters_service.key_of(c.get("name"))
                for c in (provided_outline.get("characters") or [])
                if isinstance(c, dict)
            }
            adopt_library_identity(provided_outline, uid, declared)
            outline = scriptwriter.ensure_voices(_copy.deepcopy(provided_outline), available)
            self._report(
                job_id,
                stage="directing",
                progress=3,
                message="Package accepted — reviewing your scenes now",
            )
        else:
            self._report(
                job_id,
                stage="directing",
                progress=3,
                message="Writing your podcast script" if is_podcast else "Writing your film script",
            )
            characters = [
                {
                    "name": (c.get("name") or "").strip(),
                    "description": (c.get("description") or "").strip(),
                    "voice": (c.get("voice") or "").strip(),
                }
                for c in (film.get("characters") or film.get("hosts") or [])
                if (c.get("name") or "").strip()
            ]
            research = bool(film.get("research", False))
            if is_podcast:
                outline = scriptwriter.write_podcast_script(
                    idea, characters, int(film.get("turns_count") or 8), self.settings,
                    research=research, aspect=self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT,
                )
            else:
                style = (film.get("style") or "cinematic").strip()
                scene_count = int(film.get("scenes_count") or 8)
                outline = scriptwriter.write_film_script(
                    idea, style, scene_count, characters, self.settings, research=research,
                    aspect=self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT,
                )
            outline = scriptwriter.assign_voices(outline, available)
            # Cast picks from the Film page win over auto-assignment (this is
            # how a cloned voice reaches a character).
            chosen = film.get("characters") or film.get("hosts") or []
            self._apply_chosen_voices(outline, chosen)
            # Any other name the writer invented that matches the saved
            # library adopts that identity's voice — the same name in the
            # Characters page must never mean a different voice here.
            adopt_library_identity(
                outline,
                uid,
                {
                    characters_service.key_of(c.get("name"))
                    for c in chosen
                    if isinstance(c, dict) and (c.get("name") or "").strip()
                },
            )

        # Director's pass: read the plan like a human director, patch anything a
        # scene cannot shoot, and keep notes of what was fixed.
        research = bool(film.get("research", False))
        outline["research"] = research
        director = self._director_review(
            outline,
            {"idea": idea, "style": film.get("style"), "title": outline.get("title")},
        )
        if provided_outline and research and outline.get("scenes"):
            # Fact-check a client package against live web sources when research
            # is on and Gemini is configured (best-effort; never blocks the job).
            self._report(
                job_id,
                stage="directing",
                progress=4,
                message="Fact-checking your package against live web sources",
            )
            report = factcheck.fact_check_outline(outline, self.settings)
            if report.get("checked"):
                director["factChecked"] = True
                existing = outline.get("sources") or []
                seen = {s.get("url") for s in existing if s.get("url")}
                for s in report.get("sources") or []:
                    if s.get("url") and s["url"] not in seen:
                        seen.add(s["url"])
                        existing.append(s)
                outline["sources"] = existing
            director["notes"] = list(director.get("notes") or []) + (report.get("notes") or [])
            if report.get("error"):
                director["factCheckError"] = report["error"]
            outline["director"] = director

        # The film/podcast defaults ship a title card with no hook text, which
        # renders a blank frame for the first 1.5s. Write one from the finished
        # outline so the opening beat lands, and persist it so the job page can
        # show what was used. Never overwrites a hook the client supplied.
        film_settings = job.get("settings") or {}
        if film_settings.get("hook") and not str(film_settings.get("hook_text") or "").strip():
            hook = scriptwriter.write_hook(
                idea,
                film.get("style") or outline.get("style") or "cinematic",
                outline.get("title") or "",
                self.settings,
                hook_style=film_settings.get("hook_style") or "Breaking News",
                research=research,
                aspect=self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT,
            )
            if hook:
                film_settings = dict(film_settings)
                film_settings["hook_text"] = hook["text"]
                film_settings["hook_subtext"] = hook["subtext"]
                job["settings"] = film_settings
                self._report(
                    job_id,
                    stage="directed",
                    progress=7,
                    message="Writing your opening hook",
                )
            else:
                director["hookSkipped"] = True
                director.setdefault("notes", []).append(
                    "No LLM writer was available, so the opening title card has no text."
                )
        outline["director"] = director

        combined_script = self._outline_script(outline)
        jobs_service.update_job(
            job_id,
            stage="directed",
            progress=8,
            message="Script ready",
            filmPlot=outline,
            script=combined_script,
            scriptWordCount=len(combined_script.split()),
            title=(outline.get("title") or job.get("title") or "Untitled film")[:120],
            settings=film_settings,
        )
        return outline

    @staticmethod
    def _outline_script(outline: dict) -> str:
        if outline.get("turns"):
            blocks = [
                f"{t.get('speaker', '')}: {t.get('line', '')}"
                for t in outline.get("turns") or []
                if t.get("line")
            ]
            return "\n\n".join(blocks)
        blocks = []
        for sc in outline.get("scenes") or []:
            parts = []
            if sc.get("narration"):
                parts.append(sc["narration"])
            for name, line in (sc.get("dialogue") or {}).items():
                parts.append(f"{name}: {line}")
            blocks.append("\n\n".join(parts))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Stage 2 — filming
    # ------------------------------------------------------------------

    def _film(self, job_id: str, job: dict, outline: dict, work_dir: str) -> None:
        uid = job.get("userId", "unknown")
        scenes = outline.get("scenes") or []
        n = len(scenes)
        image_model = self.settings.v1_image_model
        video_model = self.settings.v1_video_model

        # Cost guard against the daily studio quota. `film_runs_needed` is the
        # same function the creation route and the UI count with, so an approved
        # film can never die part-way through for want of quota.
        lip_sync = bool((job.get("film") or {}).get("lip_sync", True))
        acting = lip_sync and (self.settings.runpod_enabled or self.settings.runpod_fake)
        acting_scenes = acting_scene_count(scenes, acting)
        lock_chars = [
            c for c in (outline.get("characters") or [])
            if isinstance(c, dict) and (c.get("name") or "").strip()
        ]
        # Characters the project has already locked keep the same face across
        # every film in it, and cost no extra GPU run. The shared library is
        # consulted too, so a face built in Studio or Storyboard is reused
        # here rather than reinvented. Narrators are voices, never faces.
        project_id = (job.get("film") or {}).get("projectId") or ""
        locked = projects_service.locked_portraits(project_id)
        for c in package_service.on_screen_characters(lock_chars):
            name = (c.get("name") or "").strip().lower()
            if name not in locked and characters_service.ref_for(uid, c.get("name", "")):
                locked[name] = characters_service.ref_for(uid, c.get("name", ""))
        lock_chars = package_service.on_screen_characters(lock_chars)
        new_locks = [c for c in lock_chars if (c.get("name") or "").strip().lower() not in locked]
        dialogue_voices = self._dialogue_voices(outline)
        default_voice = self.video_worker._voice_or_clone(job.get("settings", {}).get("voice", ""))
        # +1 run per distinct cloned voice (one run speaks that voice's whole
        # part in the film, split back into lines locally).
        runs_needed = film_runs_needed(
            scenes,
            acting=acting,
            new_locks=len(new_locks),
            clone_voices=clone_voice_runs(
                outline, default_voice, runpod_fake=self.settings.runpod_fake
            ),
        )
        remaining = self._studio_quota_remaining(uid)
        if remaining < runs_needed:
            raise FilmError(
                f"Not enough daily AI Studio quota for {n} scenes "
                f"({runs_needed} GPU runs needed incl. {acting_scenes} acting/lip-sync "
                f"passes, {remaining} remaining). "
                "Raise STUDIO_MAX_RUNS_PER_DAY or try a shorter film."
            )

        media_root = os.path.join(work_dir, "scenes")
        os.makedirs(media_root, exist_ok=True)

        all_media: list = []
        storyboard_scenes = []
        salvaged: list[str] = []
        referenced: list[str] = []
        noted: list[str] = []
        locked_count = 0
        # (scene number, reason) for every talking scene whose mouth could not be
        # animated, so the job can say so instead of shipping a still portrait.
        lip_failures: list[tuple] = []

        def on_retry(msg):
            salvaged.append(msg)

        # Pre-clone the whole cast before filming, so no scene waits on a TTS
        # run mid-shoot. Same builder the storyboard/single-video paths use.
        clone_banks = self.video_worker._clone_line_banks(
            job_id,
            uid,
            self._voice_segments(outline, dialogue_voices, default_voice),
            work_dir,
            label="Film narration",
        )


        # Character portrait-lock (Option A, open-source, no new models):
        # one master 9:16 portrait per named character via the existing
        # _run_studio txt2img path (same gateway as podcast portraits — no
        # duplicate GPU function). Scenes matching a character restyle that
        # portrait with the existing img2img path instead of fresh txt2img.
        portraits: dict[str, str] = {}
        for c in lock_chars:
            name = (c.get("name") or "").strip()
            desc = (c.get("description") or "").strip()
            seed = _stable_seed(name)
            c["seed"] = seed
            key = name.lower()

            # Already locked for this project: reuse the exact image rather than
            # paying another run, so the face is identical across films.
            if key in locked:
                portraits[key] = locked[key]
                c["portrait"] = locked[key]
                continue

            try:
                self._report(
                    job_id, stage="filming", progress=10,
                    message=f"Locking {name}'s look",
                )
                prompt = (
                    "character reference portrait, front-facing, neutral expression, "
                    "plain background, "
                    + name
                    + (f", {desc}" if desc else "")
                    + ", centered, facing camera, "
                    + aspect_lib.prompt_phrase(self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT)
                )[:1500]
                params = dict(_IMAGE_PARAMS.get(image_model, _DEFAULT_IMAGE_PARAMS))
                params["seed"] = seed
                portraits[key] = self._run_studio(
                    job_id, uid, tool="txt2img", model=image_model,
                    prompt=prompt, params=params, refs=[],
                    title=f"Film · {name} portrait", on_retry=on_retry,
                )
                c["portrait"] = portraits[key]
                # Lock it for the project AND the shared library, so the next
                # film — in any feature — reuses this same face.
                projects_service.save_portrait(project_id, name, portraits[key], desc)
                characters_service.save_character(
                    uid, {"name": name, "description": desc, "refImage": portraits[key]}
                )
            except Exception as exc:
                print(f"[film] portrait lock failed for {name} ({exc}); scenes fall back to txt2img")
        if portraits:
            jobs_service.update_job(job_id, filmPlot=outline)

        for i, sc in enumerate(scenes):
            base = 10 + int(70 * i / n)
            self._report(
                job_id,
                stage="filming",
                progress=base,
                message=f"Generating visuals for scene {i + 1} of {n}",
                scene=i + 1,
                scene_count=n,
            )

            image_prompt = (sc.get("setting") or "")[:1500]
            shot_prompt = ((sc.get("shot") or "") + ", " + (outline.get("title") or ""))[:1500]

            # Acting scenes: when the character speaks and lip-sync is on, film
            # the whole shot like a movie — character in the scene, moving (Wan
            # image-to-video), then animate the mouth over the scene's own
            # narration/dialogue so they visibly talk as the words play.
            # Portrait-lock flows through so acting frames reuse the master
            # portrait instead of generating a fresh face.
            if acting and (sc.get("dialogue") or {}):
                acted =                 self._try_acting_scene(
                    job_id, uid, i, n, job, sc, outline,
                    image_model, video_model, default_voice, dialogue_voices,
                    media_root, on_retry, portraits, clone_banks, lip_failures,
                )
                if acted:
                    entry, acted_media, note = acted
                    storyboard_scenes.append(entry)
                    all_media.extend(acted_media)
                    noted.append(note)
                    continue

            # Online image: when research is on and the scene names a real
            # person/team/place, the director fetches a real reference photo and
            # restyles it (img2img) so the shot looks like the actual subject.
            online_ref = None
            if outline.get("research") and self.settings.gemini_configured:
                try:
                    online = imagebrief.try_scene_reference(
                        f"{sc.get('setting') or ''} {sc.get('narration') or ''}",
                        self.settings,
                        self.storage,
                        uid,
                    )
                except Exception:
                    online = None
                if online:
                    online_ref = online["ref"]
                    referenced.append((i, online["credits"]))

            # Portrait-lock: restyle the master portrait (existing img2img
            # path, low denoise keeps the face) instead of fresh txt2img.
            # Online research refs win when present — real likeness first.
            locked_ref = None
            if not online_ref and portraits:
                match = _scene_character(sc, lock_chars)
                if match:
                    p = portraits.get((match.get("name") or "").strip().lower())
                    if p:
                        locked_ref = {"path": p, "role": "reference"}
            custom_ref = str(sc.get("reference_image") or "").strip()
            if custom_ref:
                image_path = custom_ref
            elif online_ref:
                image_path = self._run_studio(
                    job_id,
                    uid,
                    tool="img2img",
                    model="sdxl",
                    prompt=image_prompt,
                    params=dict(_IMG2IMG_PARAMS),
                    refs=[online_ref],
                    title=f"Film · Scene {i + 1} of {n}",
                    on_retry=on_retry,
                    scene=i + 1,
                    scene_count=n,
                )
            elif locked_ref:
                # 48GB character tool (proven img2img graph, portrait window).
                params = {"width": 896, "height": 1600, "steps": 35, "cfg": 6.0, "denoise": 0.5}
                seed = _stable_seed(
                    (match.get("name") or "") + str(i),
                    _stable_seed(match.get("name") or ""),
                )
                params["seed"] = seed
                image_path = self._run_studio(
                    job_id,
                    uid,
                    tool="character",
                    model="sdxl-character",
                    prompt=f"{image_prompt}, same character as reference portrait, consistent face"[:1500],
                    params=params,
                    refs=[locked_ref],
                    title=f"Film · Scene {i + 1} of {n} (locked)",
                    on_retry=on_retry,
                    scene=i + 1,
                    scene_count=n,
                )
                locked_count += 1
            else:
                params = dict(_IMAGE_PARAMS.get(image_model, {"width": 896, "height": 1600, "steps": 30, "cfg": 7.0}))
                params["seed"] = _stable_seed(f"scene-{i}-{image_prompt[:64]}")
                image_path = self._run_studio(
                    job_id,
                    uid,
                    tool="txt2img",
                    model=image_model,
                    prompt=image_prompt,
                    params=params,
                    refs=[],
                    title=f"Film · Scene {i + 1} of {n}",
                    on_retry=on_retry,
                    scene=i + 1,
                    scene_count=n,
                )

            clip_path = None
            try:
                clip_path = self._run_studio(
                    job_id,
                    uid,
                    tool="img2video",
                    model=video_model,
                    prompt=shot_prompt,
                    params=self._video_params(
                        video_model, sc.get("shot"), sc.get("duration")
                    ),
                    refs=[{"path": image_path, "role": "image"}],
                    title=f"Film · Scene {i + 1} of {n}",
                    scene=i + 1,
                    scene_count=n,
                )
            except Exception:
                clip_path = None  # fall back to the still image

            desired = sc.get("shot") or shot_prompt
            clip = clip_path or image_path
            local = os.path.join(media_root, f"scene_{i:02d}{os.path.splitext(clip)[1]}")
            local = self.storage.resolve(clip, local)
            all_media.append(local)

            from pipeline.utils import classify_media, get_duration

            dur = None
            if classify_media(local) == "video":
                try:
                    dur = round(get_duration(local), 2)
                except Exception:
                    pass

            storyboard_scenes.append(
                {
                    "script": self._scene_script(sc),
                    "voice_path": default_voice,
                    "media_files": [local],
                    "clip_durations": [dur],
                    "transition": "Auto",
                    "dialogue_voices": dialogue_voices,
                    "motion_hint": desired if clip_path is None else None,
                }
            )

        if salvaged or referenced or noted or portraits or locked_count:
            director = dict(outline.get("director") or {})
            if salvaged:
                director["salvaged"] = list(director.get("salvaged") or []) + salvaged
            if referenced:
                notes = []
                for i, cr in referenced:
                    name = (cr or {}).get("title") or "online reference"
                    lic = (cr or {}).get("license") or ""
                    notes.append(f"Scene {i + 1}: matched to “{name}”{f' ({lic})' if lic else ''} via online research")
                director["notes"] = list(director.get("notes") or []) + notes
            if noted:
                director["notes"] = list(director.get("notes") or []) + noted
            if portraits:
                director["notes"] = list(director.get("notes") or []) + [
                    f"Character lock: {len(portraits)} portrait(s), {locked_count} scene(s) restyled from portrait."
                ]
            if lip_failures:
                reason = lip_failures[0][1][:200]
                director["lipSyncFailed"] = True
                director["lipSyncTurns"] = [num for num, _ in lip_failures]
                director["notes"] = list(director.get("notes") or []) + [
                    f"Lip-sync could not be generated ({reason}) — {len(lip_failures)} talking "
                    "scene(s) are silent motion shots. The characters still speak, but their "
                    "mouths will not move."
                ]
            outline["director"] = director
            # Persist the director's notes (filmPlot was stored in _direct).
            jobs_service.update_job(
                job_id,
                stage="filming",
                progress=70,
                message=f"Filmed {n} scenes · {len(salvaged)} re-shot · {len(noted)} talking scenes · {locked_count} locked",
                filmPlot=outline,
            )
        elif lip_failures:
            # Lip-sync failed on the only talking scene, so there are no notes to
            # publish yet — still tell the user before the edit stage starts.
            outline.setdefault("director", {})["lipSyncFailed"] = True
            outline["director"]["lipSyncTurns"] = [num for num, _ in lip_failures]
            outline["director"].setdefault("notes", []).append(
                f"Lip-sync could not be generated ({lip_failures[0][1][:200]}) — "
                f"{len(lip_failures)} talking scene(s) are silent motion shots."
            )
            jobs_service.update_job(job_id, filmPlot=outline)
            self._report(
                job_id,
                stage="filming",
                progress=None,
                message="Lip-sync unavailable — rendering motion shots instead",
            )
        self._edit(
            job_id, job, storyboard_scenes, all_media, default_voice, work_dir, clone_banks
        )

    def _video_params(self, video_model: str, shot_text: str = "", duration=None) -> dict:
        """Per-model img2video params. For the Wan2.2 camera-control model the
        scene's own camera move is stamped in as an explicit trajectory. A
        per-scene ``duration`` (seconds) overrides the default frame count."""
        params = dict(_VIDEO_PARAMS.get(video_model, _DEFAULT_VIDEO_PARAMS))
        params["frames"] = _scene_frames(
            duration, params.get("fps", 16), params.get("frames", 81)
        )
        if video_model == "wan22-cam-i2v":
            pose, speed = _camera_params(shot_text)
            params["camera_pose"] = pose
            params["camera_speed"] = speed
        return params

    def _scene_script(self, sc: dict) -> str:
        parts = []
        if sc.get("narration"):
            parts.append(sc["narration"])
        for name, line in (sc.get("dialogue") or {}).items():
            parts.append(f"{name}: {line}")
        return "\n\n".join(parts)

    def _dialogue_voices(self, outline: dict) -> dict:
        out = {}
        for c in outline.get("characters") or []:
            name = (c.get("name") or "").strip().lower()
            voice = c.get("voice")
            if name and voice:
                # Keeps "clone:<path>" values intact; Piper names resolve to models.
                out[name] = self.video_worker._voice_or_clone(voice)
        return out

    def _apply_chosen_voices(self, outline: dict, chosen) -> None:
        """Keep the voice the user picked for each named character/host."""
        picks = {
            (c.get("name") or "").strip().lower(): (c.get("voice") or "").strip()
            for c in (chosen or [])
            if isinstance(c, dict) and (c.get("name") or "").strip() and (c.get("voice") or "").strip()
        }
        if not picks:
            return
        for char in outline.get("characters") or []:
            if not isinstance(char, dict):
                continue
            voice = picks.get((char.get("name") or "").strip().lower())
            if voice:
                char["voice"] = voice

    def _voice_segments(self, outline: dict, dialogue_voices: dict, default_voice: str) -> list:
        """[(scene script, dialogue voices, default voice)] for the clone builder."""
        return [
            (self._scene_script(sc), dialogue_voices, default_voice)
            for sc in (outline.get("scenes") or [])
            if isinstance(sc, dict)
        ]

    def _clone_voice_count(self, outline: dict, default_voice: str) -> int:
        """Distinct cloned voices in this film = extra GPU runs needed."""
        return clone_voice_runs(
            outline, default_voice, runpod_fake=self.settings.runpod_fake
        )

    def _try_acting_scene(self, job_id, uid, i, n, job, sc, outline, image_model, video_model, default_voice, dialogue_voices, media_root, on_retry, portraits=None, clone_banks=None, lip_failures=None):
        """Film one dialogue scene "as in a movie".

        Generates the character-in-scene frame, renders the scene's own speech
        (narration + per-character dialogue, identical to what the editor would
        synthesize), moves the character with image-to-video, then lip-syncs the
        mouth over that speech. Returns (storyboard_entry, [media], director_note)
        or None so the caller falls back to the standard still scene.
        """
        speaking = list((sc.get("dialogue") or {}).items())
        if not speaking:
            return None
        speaker = str(speaking[0][0]).strip()
        speaker_title = speaker or "the speaker"

        lookup = {str(c.get("name") or "").strip().lower(): c for c in (outline.get("characters") or [])}
        who = lookup.get(speaker.lower()) or {"name": speaker_title, "description": "", "personality": "", "age": ""}
        desc = " ".join(str(who.get(k) or "") for k in ("description", "personality", "age")).strip()
        who_desc = (f"{speaker_title}, {desc}" if desc else speaker_title)[:500]

        acting_title = f"Film · Scene {i + 1} of {n}"
        frame_prompt = (
            f"cinematic film still, {(sc.get('setting') or '')[:1200]}, {who_desc} acting in this scene, "
            "speaking to camera, centered, full character visible, "
            + aspect_lib.prompt_phrase(self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT)
        )[:1500]
        # Reuse portrait-lock when available: restyle master portrait instead
        # of fresh txt2img (same _run_studio gateway, no duplicate function).
        _portrait = (portraits or {}).get(speaker.lower()) if portraits else None
        if _portrait:
            _fp = {"width": 896, "height": 1600, "steps": 35, "cfg": 6.0, "denoise": 0.5}
            _fp["seed"] = _stable_seed(speaker + str(i), _stable_seed(speaker))
            frame_path = self._run_studio(
                job_id, uid,
                tool="character", model="sdxl-character",
                prompt=f"{frame_prompt}, same character as reference portrait, consistent face"[:1500],
                params=_fp,
                refs=[{"path": _portrait, "role": "reference"}],
                title=acting_title + " (locked)", on_retry=on_retry,
                scene=i + 1, scene_count=n,
            )
        else:
            _tp = dict(_IMAGE_PARAMS.get(image_model, _DEFAULT_IMAGE_PARAMS))
            _tp["seed"] = _stable_seed(speaker + frame_prompt[:64])
            frame_path = self._run_studio(
                job_id, uid,
                tool="txt2img", model=image_model,
                prompt=frame_prompt,
                params=_tp,
                refs=[], title=acting_title, on_retry=on_retry,
                scene=i + 1, scene_count=n,
            )

        # Scene speech exactly as the editor would narrate it (speaker voices
        # and all), biased so the delivered audio matches the acting clip.
        audio_local = os.path.join(media_root, f"scene_{i:02d}_speech.wav")
        try:
            # Absolute temp dir: create_narration's ffmpeg concat list embeds
            # full paths, and relative list paths get doubled against the list's
            # own directory (same reason pipeline.engine forces abspath).
            scene_audio_dir = os.path.abspath(
                os.path.join(media_root, f"scene_{i:02d}_narration")
            )
            os.makedirs(scene_audio_dir, exist_ok=True)
            create_narration(
                RenderSettings(
                    script=self._scene_script(sc),
                    voice_path=default_voice,
                    dialogue_voices=dialogue_voices,
                    clone_line_banks=dict(clone_banks or {}),
                    pacing=(job.get("settings") or {}).get("pacing") or "Normal Sports Story",
                    speech_rate=float(job.get("settings", {}).get("speech_rate", 1.0)),
                    temp_dir=scene_audio_dir,
                ),
                audio_local,
            )
        except Exception as exc:
            print(f"[film] scene narration failed ({exc}); falling back to standard scene")
            return None
        audio_path = self._publish_media(uid, job_id, audio_local, f"scene_{i:02d}_speech.wav")

        motion_prompt = (
            f"{sc.get('shot') or 'a natural acting move while the character speaks'}, "
            + (outline.get("title") or "")
        )[:1500]
        motion_path = self._run_studio(
            job_id, uid,
            tool="img2video", model=video_model,
            prompt=motion_prompt,
            params=self._video_params(
                video_model, sc.get("shot"), sc.get("duration")
            ),
            refs=[{"path": frame_path, "role": "image"}],
            title=acting_title, on_retry=on_retry,
            scene=i + 1, scene_count=n,
        )

        used_acting = False
        acting_path = None
        if self.settings.runpod_enabled or self.settings.runpod_fake:
            try:
                acting_path = self._run_studio(
                    job_id, uid,
                    tool="lipsync", model="wav2lip",
                    prompt="",
                    params=dict(_ACTING_PARAMS),
                    refs=[
                        {"path": motion_path, "role": "video"},
                        {"path": audio_path, "role": "audio"},
                    ],
                    title=f"Film · {speaker_title} talks",
                    on_retry=on_retry,
                    scene=i + 1,
                    scene_count=n,
                )
                used_acting = True
            except Exception as exc:
                print(f"[film] lip-sync motion failed ({exc}); keeping the moving character")
                acting_path = None
                if lip_failures is not None:
                    lip_failures.append((i + 1, str(exc) or exc.__class__.__name__))

        clip = acting_path or motion_path
        local = os.path.join(media_root, f"scene_{i:02d}{os.path.splitext(clip)[1]}")
        local = self.storage.resolve(clip, local)
        dur = None
        if classify_media(local) == "video":
            try:
                dur = round(get_duration(local), 2)
            except Exception:
                pass

        entry = {
            "script": self._scene_script(sc),
            "voice_path": default_voice,
            "media_files": [local],
            "clip_durations": [dur],
            "transition": "Auto",
            # When the lip-sync clip carries the speech it is used as narration
            # (matches the podcast); otherwise the script still narrates.
            "use_clip_audio": used_acting,
            "dialogue_voices": {} if used_acting else dialogue_voices,
        }
        note = (
            f"Scene {i + 1}: shot as a talking scene — {speaker_title} "
            f"{'lip-syncs as they speak' if used_acting else 'moves the scene, engine-narrated'}."
        )
        return entry, [local], note

    # ------------------------------------------------------------------
    # Stage 2b — podcast filming
    # ------------------------------------------------------------------

    def _film_podcast(self, job_id: str, job: dict, outline: dict, work_dir: str) -> None:
        uid = job.get("userId", "unknown")
        hosts = outline.get("characters") or []
        turns = outline.get("turns") or []
        n = len(turns)
        image_model = self.settings.v1_image_model
        video_model = self.settings.v1_video_model
        lip_sync = bool((job.get("film") or {}).get("lip_sync", True))

        runs_needed = max(2, len(hosts)) + 2 * n
        dialogue_voices = self._dialogue_voices(outline)
        default_voice = self.video_worker._voice_or_clone(job.get("settings", {}).get("voice", ""))
        host_voice = {
            name.lower(): self.video_worker._voice_or_clone(voice)
            for name, voice in dialogue_voices.items()
        }
        turn_voices = [
            host_voice.get((turn.get("speaker") or "").strip().lower()) or default_voice
            for turn in turns
        ]
        # Podcast turns are single lines: +1 run per distinct cloned host voice
        # (each line stays whole so it can drive lip-sync and the scene track).
        runs_needed += len({v for v in turn_voices if is_clone_voice(v)})
        remaining = self._studio_quota_remaining(uid)
        if remaining < runs_needed:
            raise FilmError(
                f"Not enough daily AI Studio quota for {n} talking turns "
                f"(~{runs_needed} GPU runs needed, {remaining} remaining). "
                "Raise STUDIO_MAX_RUNS_PER_DAY or try a shorter episode."
            )

        clone_banks = self.video_worker._clone_line_banks(
            job_id,
            uid,
            [(turn.get("line") or "", {}, voice) for turn, voice in zip(turns, turn_voices)],
            work_dir,
            label="Podcast voices",
        )

        media_root = os.path.join(work_dir, "turns")
        os.makedirs(media_root, exist_ok=True)

        # 1. One consistent portrait per host (reused across all of their turns).
        portraits = {}
        salvaged: list[str] = []
        # Turn numbers (1-based) whose talking head could not be generated. Kept
        # so the job can tell the user their hosts are not actually lip-syncing
        # instead of silently shipping a still portrait that drifts.
        static_turns: list[int] = []
        static_reason = ""

        def on_retry(msg):
            salvaged.append(msg)

        for hi, host in enumerate(hosts):
            self._report(
                job_id,
                stage="filming",
                progress=10 + int(35 * hi / max(1, len(hosts))),
                message=f"Designing {host.get('name', 'host')}'s look",
            )
            prompt = (
                (host.get("description") or "")
                + ", professional podcast studio, soft key lighting, shallow depth of field, "
                + "center framed, looking into camera, "
                + aspect_lib.prompt_phrase(self._job_settings.get("aspect") or DEFAULT_FILM_ASPECT)
                + ", highly detailed"
            )[:1500]
            portraits[host.get("name", "")] = self._run_studio(
                job_id,
                uid,
                tool="txt2img",
                model=image_model,
                prompt=prompt,
                params=dict(_IMAGE_PARAMS.get(image_model, _DEFAULT_IMAGE_PARAMS)),
                refs=[],
                title=f"Podcast · {host.get('name', 'host')} portrait",
                on_retry=on_retry,
            )

        # 2. One voiced talking shot per turn.
        all_media: list = []
        storyboard_scenes = []
        for i, turn in enumerate(turns):
            speaker = (turn.get("speaker") or "").strip()
            line = (turn.get("line") or "").strip()
            host = next(
                (h for h in hosts if (h.get("name") or "").lower() == speaker.lower()),
                hosts[0] if hosts else None,
            )
            portrait = portraits.get(host.get("name", "")) if host else None
            if not portrait:
                raise FilmError(f"Podcast host has no portrait: {speaker}")
            voice = host_voice.get(speaker.lower()) or default_voice
            if not voice:
                raise FilmError(f"No Piper voice assigned to host: {speaker}")

            self._report(
                job_id,
                stage="filming",
                progress=45 + int(45 * i / n),
                message=f"Recording turn {i + 1} of {n} · {speaker}",
                scene=i + 1,
                scene_count=n,
            )

            # Voice the line so lip-sync can animate from it.
            wav = os.path.join(media_root, f"turn_{i:02d}.wav")
            self._tts_line(line, voice, wav, clone_banks)
            audio_path = self._publish_media(uid, job_id, wav, f"turn_{i:02d}.wav")

            shot = ((turn.get("shot") or "a subtle slow push-in") + ", " + (outline.get("title") or ""))[:1500]
            motion_path = self._run_studio(
                job_id,
                uid,
                tool="img2video",
                model=video_model,
                prompt=shot,
                params=self._video_params(video_model, turn.get("shot")),
                refs=[{"path": portrait, "role": "image"}],
                title=f"Podcast · {speaker}",
                on_retry=on_retry,
                scene=i + 1,
                scene_count=n,
            )

            clip_path = motion_path
            used_lip = False
            if lip_sync and (self.settings.runpod_enabled or self.settings.runpod_fake):
                try:
                    clip_path = self._run_studio(
                        job_id,
                        uid,
                        tool="lipsync",
                        model="wav2lip",
                        prompt="",
                        params=dict(_ACTING_PARAMS),
                        refs=[{"path": motion_path, "role": "video"}, {"path": audio_path, "role": "audio"}],
                        title=f"Podcast · {speaker} talks",
                        on_retry=on_retry,
                        scene=i + 1,
                        scene_count=n,
                    )
                    used_lip = True
                except Exception as exc:
                    print(f"[podcast] lip-sync failed ({exc}); keeping the moving portrait")
                    clip_path = motion_path
                    static_turns.append(i + 1)
                    static_reason = str(exc) or exc.__class__.__name__

            clip = clip_path or portrait
            local = os.path.join(media_root, f"turn_{i:02d}{os.path.splitext(clip)[1]}")
            local = self.storage.resolve(clip, local)
            all_media.append(local)
            dur = None
            if classify_media(local) == "video":
                try:
                    dur = round(get_duration(local), 2)
                except Exception:
                    pass

            storyboard_scenes.append(
                {
                    "script": line,
                    "voice_path": voice,
                    "media_files": [local],
                    "clip_durations": [dur],
                    "transition": "Auto",
                    "use_clip_audio": used_lip,
                    "dialogue_voices": {},
                }
            )

        if static_turns:
            detail = f" ({static_reason[:200]})" if static_reason else ""
            director = dict(outline.get("director") or {})
            director["lipSyncFailed"] = True
            director["lipSyncTurns"] = static_turns
            director.setdefault("notes", []).append(
                f"Lip-sync could not be generated{detail} — {len(static_turns)} of {n} turns "
                "are silent motion shots instead of talking hosts. The hosts still speak, but "
                "their mouths will not move."
            )
            outline["director"] = director
            self._report(
                job_id,
                stage="filming",
                progress=None,
                message="Lip-sync unavailable — rendering motion shots instead",
            )

        if salvaged:
            director = dict(outline.get("director") or {})
            director["salvaged"] = list(director.get("salvaged") or []) + salvaged
            outline["director"] = director
            jobs_service.update_job(
                job_id,
                stage="filming",
                progress=None,
                message=f"Recorded {n} turns · {len(salvaged)} re-shot",
                filmPlot=outline,
            )
        elif static_turns:
            jobs_service.update_job(job_id, filmPlot=outline)

        self._edit(
            job_id,
            job,
            storyboard_scenes,
            all_media,
            default_voice or (next(iter(host_voice.values()), "") if host_voice else ""),
            work_dir,
            clone_banks,
        )

    def _tts_line(self, text: str, voice_path: str, out_wav: str, banks: dict | None = None) -> None:
        """Voice one podcast turn. A cloned host reuses the lines already
        rendered for this job (joined back into the full turn) instead of
        calling Piper or the GPU again."""
        from pipeline.narration import clone_lines_for, synth_chunk

        bank = (banks or {}).get(voice_path) or {}
        wanted = clone_lines_for(text, {}, voice_path).get(voice_path) or []
        sources = [bank[t] for t in wanted if t in bank]
        if is_clone_voice(voice_path):
            if not sources:
                raise FilmError("Cloned host voice missing its pre-rendered lines.")
            self.video_worker._join_wavs(sources, out_wav)
            return
        # One call for the whole turn, not one per sentence: the chunker already
        # grouped the sentences, so Piper carries intonation across them.
        synth_chunk(text, voice_path, out_wav)

    def _publish_media(self, uid: str, job_id: str, local_path: str, name: str) -> str:
        storage_path = f"media/{uid}/{job_id}/{name}"
        self.storage.publish(local_path, storage_path)
        return storage_path

    def _quality_params(self, tool: str, params: dict) -> dict:
        """Scale sampling effort for the user's draft/standard/high choice."""
        if tool not in ("txt2img", "img2img", "character", "img2video"):
            return params
        if "steps" not in params:
            return params
        try:
            base = int(params["steps"])
        except (TypeError, ValueError):
            return params
        if self._quality == "draft":
            params["steps"] = max(4, round(base * 0.6))
        elif self._quality == "high":
            params["steps"] = min(60, round(base * 1.5))
        return params

    def _run_studio(self, job_id: str, uid: str, tool: str, model: str, prompt: str, params: dict, refs: list, title: str, retries: int = 1, on_retry=None, scene: int | None = None, scene_count: int | None = None) -> str:
        """Run one AI Studio pass, with an agentic salvage loop.

        If a run fails, the director adjusts the direction (simpler composition,
        lighter params) and re-shoots up to ``retries`` more times before giving
        up. Every retry is reported on the job so the user sees it working.

        When ``scene``/``scene_count`` are given, the pass's own progress is
        folded into that scene's slice of the job bar (see ``_PASS_WEIGHTS``),
        so the job page shows "Scene 3 of 8 · Rendering on GPU …" moving instead
        of a frozen percentage.
        """

        def _sub_progress(progress: int, message: str):
            if scene is None or not scene_count:
                self._report(job_id, stage="filming", progress=None, message=message)
                return
            base, span = _scene_slice(scene, scene_count)
            w0, w1 = _PASS_WEIGHTS.get(tool, (0.0, 1.0))
            frac = w0 + (w1 - w0) * (max(0, min(100, int(progress))) / 100.0)
            label = f"Scene {scene} of {scene_count} · {message}" if scene_count > 1 else message
            self._report(
                job_id,
                stage="filming",
                progress=min(base + int(span * frac), base + span - 1),
                message=label,
                scene=scene,
                scene_count=scene_count,
            )

        last_error = None
        film_aspect = ((self._job_settings or {}).get("aspect") or "auto").strip()
        for attempt in range(retries + 1):
            p = prompt
            pa = self._quality_params(tool, _shape(params, film_aspect))
            if attempt > 0:
                p = (prompt or "").strip() + ", simple composition, clear subject"
                if pa.get("steps"):
                    pa["steps"] = max(4, int(pa["steps"] * 0.7))
                if pa.get("cfg") and float(pa["cfg"]) > 5.0:
                    pa["cfg"] = 5.0

            run_id = studio_service.create_run(
                uid=uid, tool=tool, model=model, prompt=p, params=pa, refs=refs,
                title=title, job_id=job_id,
            )
            self.studio_worker._process(run_id, on_progress=_sub_progress)  # sync, so same thread waits for the artifact
            doc = studio_service.get_run(run_id) or {}
            if (doc.get("status") or "") == "completed":
                primary = (doc.get("output") or {}).get("primary") or {}
                path = primary.get("storagePath")
                if path:
                    return path
                last_error = FilmError(f"{tool} run finished without an output file.")
            else:
                last_error = FilmError(
                    f"{tool} run failed: {doc.get('error') or doc.get('message') or 'unknown error'}"
                )
            if attempt < retries:
                msg = f"Adjusting direction and re-shooting · {title} (pass {attempt + 1}/{retries})"
                self._report(job_id, stage="filming", progress=None, message=msg)
                if on_retry:
                    on_retry(msg)
        raise FilmError(f"{tool} run failed after {retries + 1} passes: {last_error}")

    def _studio_quota_remaining(self, uid: str) -> int:
        return self.video_worker.studio_quota_remaining(uid)

    # ------------------------------------------------------------------
    # Stage 3 — editing
    # ------------------------------------------------------------------

    def _edit(
        self,
        job_id: str,
        job: dict,
        scenes: list,
        all_media: list,
        default_voice: str,
        work_dir: str,
        clone_banks: dict | None = None,
    ) -> None:
        self._report(job_id, stage="editing", progress=82, message="Editing your film")

        settings = self.video_worker._build_settings(
            job,
            all_media,
            [],
            self.video_worker._resolve_cover(job, all_media),
            default_voice,
            self.video_worker._resolve_music(job.get("settings", {}).get("music")),
            work_dir,
        )
        # Cloned cast audio, already rendered in one run per voice.
        settings.clone_line_banks = dict(clone_banks or {})

        def report(stage, progress, message):
            jobs_service.update_job(
                job_id,
                status="processing",
                stage=stage,
                progress=progress,
                message=message or stage,
            )

        whisper = None
        whisper = None

        output_path = generate_storyboard(
            scenes,
            settings,
            whisper_model=whisper,
            on_progress=report,
            on_stage=lambda st: report(st, 0, st),
        )
        self.video_worker._publish(job_id, job, output_path, settings)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load(self, job_id: str) -> dict:
        job = jobs_service.get_job(job_id)
        if not job:
            raise FilmError(f"Job not found: {job_id}")
        if job.get("type") != "film":
            raise FilmError("Not a film job.")
        return job

    def _report(
        self,
        job_id: str,
        stage: str,
        progress,
        message: str,
        scene: int | None = None,
        scene_count: int | None = None,
    ):
        """Progress may be None → keep whatever the job already shows.

        ``scene``/``scene_count`` mark which storyboard scene is being filmed so
        the UI can show "Scene 3 of 8" instead of a bare progress bar.
        """
        kwargs = {"status": "processing", "stage": stage, "message": message or stage}
        if progress is not None:
            kwargs["progress"] = progress
        if scene is not None:
            kwargs["sceneIndex"] = scene
        if scene_count is not None:
            kwargs["sceneCount"] = scene_count
        jobs_service.update_job(job_id, **kwargs)
"""AI Studio endpoints: create runs, list/status, and the signed callback
endpoints the RunPod GPU worker uses to stream artifacts/progress back."""

from __future__ import annotations

import datetime
import hmac
import hashlib
import mimetypes
import os
import shutil
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from pipeline import aspect as aspect_lib
from pipeline.utils import classify_media, get_duration

from ..auth import CurrentUser, get_current_user
from ..config import Settings
from ..models import StudioRunCreate
from ..services import imagebrief
from ..services import ratelimit
from ..services import studio as studio_service
from ..services import usage as usage_service
from ..services.ratelimit import rate_limiter
from ..services.storage.base import as_absolute
from ..workers.studio_worker import StudioWorker, _artifact_token

router = APIRouter()

# Enum choices exposed to the UI and enforced in _normalize_params.
STUDIO_ENUM_VALUES = {"aspect": tuple(aspect_lib.ASPECT_CHOICES)}

# WanCameraEmbedding's accepted trajectory names, shared with the Studio UI so
# the camera model's extra controls are not hardcoded in the frontend.
CAMERA_POSES = (
    "Zoom In",
    "Zoom Out",
    "Pan Left",
    "Pan Right",
    "Pan Up",
    "Pan Down",
    "ClockWise (CW)",
    "Anti Clockwise (ACW)",
    "Static",
)

# tool -> {model -> default params} (conservative, cost-aware defaults)
# NOTE: keys are the complete set of controls the model actually honors. The
# Studio UI derives its controls from these defaults, so unused knobs (e.g.
# seed on Chatterbox, canvas on Wav2Lip) never appear.
TOOL_MODELS = {
    "txt2img": {
        "sdxl": {"aspect": "auto", "width": 1024, "height": 1024, "steps": 30, "cfg": 7.0, "seed": 0, "prompt": "", "negative": ""},
    },
    # Image restyle: reference photo (a real person/team from the online pass,
    # or any uploaded image) re-rendered to match a prompt while keeping the
    # subject's likeness (SDXL, low denoise). Also reused for film character
    # portrait-lock: master portrait -> per-scene restyle.
    "img2img": {
        "sdxl": {"aspect": "auto", "width": 1024, "height": 1024, "steps": 30, "cfg": 6.0, "denoise": 0.45, "seed": 0, "prompt": "", "negative": ""},
    },
    # 48GB-only character lock: master portrait -> per-scene restyle with a
    # portrait denoise window (SDXL, higher steps). Backed by the proven
    # img2img graph in the worker; native InstantID replaces the graph body
    # in phase 2 without changing callers.
    "character": {
        "sdxl-character": {"aspect": "auto", "width": 896, "height": 1600, "steps": 35, "cfg": 6.0, "denoise": 0.5, "seed": 0, "prompt": "", "negative": ""},
    },
    # 48GB-only voice clone (Chatterbox Turbo, MIT, zero-shot). Prompt = text
    # to speak (max 2000 chars, enforced in worker); ref = 5-30s clean voice
    # sample. Bypasses ComfyUI in the worker; same Studio queue/callbacks.
    "voiceclone": {
        # Chatterbox reads exaggeration/cfg_weight and the prompt text; it has
        # no negative prompt or seed input.
        "chatterbox-turbo": {"exaggeration": 0.5, "cfg_weight": 0.5, "prompt": ""},
    },
    # Lip sync for a full (moving) video: the character acts the scene, then
    # a Wav2Lip-class node animates the mouth to the speech audio.
    "lipsync": {
        # Wav2Lip animates an existing clip to a speech track. It has no
        # diffusion knobs, canvas, seed, or prompt, so the catalog exposes no
        # parameter controls for this tool.
        "wav2lip": {},
    },
    "img2video": {
        "wan21-i2v": {"aspect": "auto", "width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 5.0, "denoise": 1.0, "seed": 0, "prompt": "", "negative": ""},
        "wan21-flf2v": {"aspect": "auto", "width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 5.0, "denoise": 1.0, "seed": 0, "prompt": "", "negative": ""},
        # Wan2.2 Fun Camera Control: an explicit camera trajectory (pan/zoom/
        # orbit) is stamped into the conditioning instead of only being hinted
        # in the prompt text.
        "wan22-cam-i2v": {"aspect": "auto", "width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg": 3.5, "camera_pose": "Zoom In", "camera_speed": 1.0, "seed": 0, "prompt": "", "negative": ""},
    },
}

# Media ref slots per tool/model. Each slot: role, label, required, kind.
_START = {"role": "image", "label": "Start frame", "required": True, "kind": "image"}
_END = {"role": "end_image", "label": "End frame", "required": False, "kind": "image"}
_REFERENCE = {"role": "reference", "label": "Reference image", "required": False, "kind": "image"}
_IMG2IMG_REF = {"role": "reference", "label": "Reference image", "required": True, "kind": "image"}
_LIPSYNC_REFS = [
    {"role": "video", "label": "Character video", "required": True, "kind": "video"},
    {"role": "audio", "label": "Speech audio", "required": True, "kind": "audio"},
]

# Single source of truth: which ref slots each model actually honors.
MODEL_REFS = {
    "txt2img": {"sdxl": []},
    "img2img": {"sdxl": [_IMG2IMG_REF]},
    "character": {"sdxl-character": [_IMG2IMG_REF]},
    "voiceclone": {"chatterbox-turbo": [{"role": "audio", "label": "Voice sample (5-30s)", "required": True, "kind": "audio"}]},
    "lipsync": {"wav2lip": _LIPSYNC_REFS},
    "img2video": {
        # wan21-i2v uses a start frame (+ optional clip-vision reference guide).
        "wan21-i2v": [_START, _REFERENCE],
        # flf2v interpolates first -> last frame, with an optional reference.
        "wan21-flf2v": [_START, _END, _REFERENCE],
        # Wan2.2 camera control animates a single start frame along a camera path.
        "wan22-cam-i2v": [_START],
    },
}

# Tool-level default (union of slots) for older clients / fallback validation.
TOOL_REFS = {
    "txt2img": [],
    "img2img": [_IMG2IMG_REF],
    "character": [_IMG2IMG_REF],
    "voiceclone": [{"role": "audio", "label": "Voice sample (5-30s)", "required": True, "kind": "audio"}],
    "lipsync": _LIPSYNC_REFS,
    "img2video": [_START, _END, _REFERENCE],
}

_TASK_PATHS = ["/studio/artifacts/{run_id}", "/studio/progress/{run_id}"]


def _verify_worker_token(run_id: str, token: str, sign_secret: str) -> bool:
    expected = _artifact_token(run_id, sign_secret)
    return token and hmac.compare_digest(token or "", expected)


@router.get("/catalog/studio")
async def studio_catalog(request: Request, user: CurrentUser = Depends(get_current_user)):
    s: Settings = request.app.state.settings
    return {
        "enabled": s.studio_enabled,
        "fake": s.runpod_fake,
        "tools": [
            {
                "tool": "txt2img",
                "promptLabel": 'Prompt',
                "promptHint": 'Describe the image: subject, setting, lighting, style — e.g. “a striker scoring at golden hour, cinematic, 8k”…',
                "label": "Text to Image",
                "kind": "image",
                "models": ["sdxl"],
                "refs": [],
            },
            {
                "tool": "img2img",
                "promptLabel": 'Restyle direction',
                "promptHint": 'How to change the reference image — e.g. “ink-and-grain anime”, “vintage film still”, “pop-art poster”…',
                "label": "Image Restyle (Reference to Image)",
                "kind": "image",
                "models": ["sdxl"],
                "refs": TOOL_REFS["img2img"],
            },
            {
                "tool": "character",
                "promptLabel": 'Scene prompt',
                "promptHint": 'Describe the scene the locked character appears in — setting, action, lighting…',
                "label": "Character Lock (Portrait to Scene)",
                "kind": "image",
                "models": ["sdxl-character"],
                "refs": TOOL_REFS["character"],
            },
            {
                "tool": "voiceclone",
                "promptLabel": 'What to say',
                "promptHint": 'The line the cloned voice should read, e.g. “Welcome back to the show.” — the voice sample supplies the voice.',
                "label": "Voice Clone (Sample to Speech)",
                "kind": "audio",
                "models": ["chatterbox-turbo"],
                "refs": TOOL_REFS["voiceclone"],
            },
            {
                "tool": "img2video",
                "promptLabel": 'Motion prompt',
                "promptHint": 'How the still should move — e.g. “slow push-in”, “hair in the wind”, “camera drifts left”…',
                "label": "Image to Video",
                "kind": "video",
                "models": ["wan21-i2v", "wan21-flf2v", "wan22-cam-i2v"],
                "refs": TOOL_REFS["img2video"],
            },
            {
                "tool": "lipsync",
                "promptLabel": 'Scene prompt',
                "promptHint": 'The audio drives the mouth — add a short description of the shot if you need it.',
                "label": "Lip-Sync Motion",
                "kind": "video",
                "models": ["wav2lip"],
                "refs": TOOL_REFS["lipsync"],
            },
        ],
        "modelParams": TOOL_MODELS,
        "modelRefs": MODEL_REFS,
        "modelLabels": {
            "sdxl": "SDXL",
            "sdxl-character": "SDXL Character",
            "chatterbox-turbo": "Chatterbox Turbo",
            "wav2lip": "Wav2Lip",
            "wan21-i2v": "Wan 2.1 Image to Video",
            "wan21-flf2v": "Wan 2.1 First/Last Frame",
            "wan22-cam-i2v": "Wan 2.2 Camera Control",
        },
        # Choice lists for the enum-style params, so the Studio UI never
        # hardcodes a list that pipeline.aspect owns.
        "paramOptions": {
            "aspect": list(aspect_lib.ASPECT_CHOICES),
            "aspectLabels": dict(aspect_lib.ASPECT_LABELS),
            "camera_pose": list(CAMERA_POSES),
        },
        # Conservative per-run USD estimates, shown before the user spends.
        "prices": dict(usage_service.PRICE_PER_RUN),
        "defaultPrice": usage_service.DEFAULT_PRICE,
    }


def _ground_run(
    tool: str,
    model: str,
    prompt: str,
    refs: list,
    settings: Settings,
    storage,
    uid: str,
) -> tuple[str, str, str, list, dict | None]:
    """Research the prompt's subject and fold what we learn into the run.

    Only the still-image tools ground. Anything else (video, audio, lip-sync)
    has its own reference slots and timing budget, so it is left alone.

    Two outcomes, both automatic — there is no user-facing switch:

    * A real photo was found, so a text-to-image request becomes an img2img run
      against that photo and the graphic keeps the subject's likeness. A caller's
      own uploaded reference always wins; we only enrich the prompt.
    * Research succeeded but Wikimedia had nothing usable, so the run stays
      text-to-image and the prompt carries the subject's real colours, symbols
      and era — producing a fictional figure that still reads as the subject.

    Returns the possibly-upgraded ``(tool, model, prompt, refs, research)``.
    ``research`` is None when nothing was grounded, and every failure inside
    here degrades to "render exactly as asked".
    """
    if tool not in ("txt2img", "img2img"):
        return tool, model, prompt, refs, None
    # Cheap gate: abstract scenes must never reach Gemini or the web.
    if not imagebrief.looks_like_named_subject(prompt):
        return tool, model, prompt, refs, None

    try:
        found = imagebrief.ground(prompt, settings, storage, uid)
    except Exception:
        found = None
    if not found:
        return tool, model, prompt, refs, None

    brief = found["brief"]
    ref = found.get("ref")
    prompt = imagebrief.compose_prompt(brief, prompt, fictional=ref is None)

    if ref and not refs:
        if tool == "txt2img":
            # txt2img has no reference slot at all, so a photo forces img2img.
            # SDXL is the only img2img model; params get re-normalized by the
            # caller against the new tool/model.
            tool, model = "img2img", "sdxl"
        refs = [ref]

    return tool, model, prompt, refs, {
        "subject": brief.get("subject", ""),
        "role": brief.get("role", ""),
        "fictional": ref is None,
        # False when the brief came from the model's own knowledge because
        # Search grounding was unavailable (free-tier quota) at the time.
        "grounded": bool(brief.get("grounded")),
        "source": "wikimedia" if ref else "",
        "credits": found.get("credits") or {},
    }


@router.post("/studio/runs", status_code=status.HTTP_201_CREATED)
async def create_studio_run(
    payload: StudioRunCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    s: Settings = request.app.state.settings
    rate_limiter.check(user.uid, s.rate_limit_per_min)

    if not s.studio_enabled:
        raise HTTPException(
            status_code=503,
            detail="AI Studio isn't configured. Set RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID to enable GPU runs.",
        )

    tool = (payload.tool or "").strip()
    models = TOOL_MODELS.get(tool)
    if not models:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

    model = (payload.model or "").strip()
    if model not in models:
        raise HTTPException(status_code=400, detail=f"Unknown model for {tool}: {model}")

    prompt = (payload.prompt or "").strip()
    if not prompt and tool != "lipsync":
        raise HTTPException(status_code=400, detail="Prompt is required.")
    if len(prompt) > s.studio_max_prompt_chars:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds {s.studio_max_prompt_chars} characters.",
        )

    storage = request.app.state.storage
    refs = _normalize_refs(payload.refs or [], tool, model, user.uid, storage)

    # Research happens before params are normalized so that a txt2img promoted to
    # img2img gets its parameters bounded by the model that will actually run.
    tool, model, prompt, refs, research = _ground_run(
        tool, model, prompt, refs, s, storage, user.uid
    )
    if research:
        models = TOOL_MODELS[tool]
        # The composed prompt is longer than what the user typed, so the length
        # guard has to be re-applied here rather than only on the raw input.
        if len(prompt) > s.studio_max_prompt_chars:
            prompt = prompt[: s.studio_max_prompt_chars].rstrip(" .,")

    params = _apply_aspect(tool, model, _normalize_params(
        payload.params or {},
        models[model],
        _param_bounds(tool, model),
    ))

    # Per-user daily studio quota (cost guard for a serverless GPU budget).
    if studio_service.daily_used(user.uid) >= s.studio_max_runs_per_day:
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI Studio limit reached ({s.studio_max_runs_per_day}). Try again tomorrow.",
        )

    run_id = studio_service.create_run(
        uid=user.uid,
        tool=tool,
        model=model,
        prompt=prompt,
        params=params,
        refs=refs,
        title=(payload.title or "").strip(),
    )

    if research:
        # Surfaced in the UI so a generated likeness is never passed off as a
        # real photograph, and so the Commons photo is credited.
        studio_service.update_run(run_id, research=research)

    worker: StudioWorker = request.app.state.studio_worker
    await worker.submit(run_id)

    if s.runpod_fake:
        return {"run_id": run_id, "status": "queued", "fake": True}
    return {"run_id": run_id, "status": "queued", "endpoint": s.runpod_endpoint_id}



@router.get("/studio/runs")
async def list_studio_runs(
    request: Request, user: CurrentUser = Depends(get_current_user)
):
    return [_run_out(r) for r in studio_service.list_runs(user.uid, limit=30)]


@router.get("/studio/quota")
async def studio_quota(
    request: Request, user: CurrentUser = Depends(get_current_user)
):
    """Daily AI Studio budget for this user: used runs, ceiling, what's left.

    Read-only — the film/podcast guards and the Studio tab still enforce the
    ceiling before anything new is queued.
    """
    s: Settings = request.app.state.settings
    max_runs = int(s.studio_max_runs_per_day)
    used = studio_service.daily_used(user.uid)
    return {"max": max_runs, "used": used, "remaining": max(0, max_runs - used)}


@router.get("/studio/runs/{run_id}")
async def get_studio_run(
    run_id: str, request: Request, user: CurrentUser = Depends(get_current_user)
):
    run = studio_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your run.")
    return _run_out(run, run_id=run_id)


@router.delete("/studio/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_studio_run(
    run_id: str, request: Request, user: CurrentUser = Depends(get_current_user)
):
    run = studio_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.get("userId") != user.uid:
        raise HTTPException(status_code=403, detail="Not your run.")

    storage = request.app.state.storage
    output = run.get("output") or {}
    for artifact in output.get("artifacts") or []:
        path = artifact.get("storagePath")
        if path:
            try:
                storage.delete_file(path)
            except Exception:
                pass
    _cleanup_work_dir(request.app.state.settings, run_id)
    studio_service.delete_run(run_id)


def _cleanup_work_dir(s: Settings, run_id: str) -> None:
    """Remove a run's local scratch directory.

    The worker copies sources into the work dir to transcode them, and those
    copies survive a delete -- on a long-lived machine the work dir grows by
    one directory per run forever. The path is rebuilt from run_id rather than
    trusted from the request, and it is confined to work_root.
    """
    if not s.work_root:
        return
    try:
        root = os.path.abspath(s.work_root)
        target = os.path.abspath(os.path.join(root, str(run_id)))
        # Refuse anything that escapes the work root.
        if os.path.commonpath([root, target]) != root or target == root:
            return
        shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Signed callbacks (called by the RunPod GPU worker, not by the browser)
# ---------------------------------------------------------------------------


@router.post("/studio/artifacts/{run_id}")
async def receive_artifact(
    run_id: str,
    request: Request,
    file: UploadFile = File(...),
    token: str = "",
):
    token = (token or request.query_params.get("token") or "").strip()
    s: Settings = request.app.state.settings
    if not _verify_worker_token(run_id, token, s.sign_secret):
        raise HTTPException(status_code=403, detail="Invalid worker token.")
    run = studio_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    uid = run.get("userId", "unknown")

    filename = os.path.basename((file.filename or "output.bin").replace("\\", "/")).strip() or "output.bin"
    kind = classify_media(filename)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact type: {filename}")

    # Save to temp then publish into studio/{uid}/{run_id}/
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)

        rel = os.path.join("studio", uid, run_id, filename).replace("\\", "/")
        signed = request.app.state.storage.publish(tmp_path, rel)

        duration = None
        if kind == "video":
            try:
                duration = round(get_duration(tmp_path), 2)
            except Exception:
                pass

        artifact = {
            "name": filename,
            "kind": kind,
            "storagePath": rel,
            "url": as_absolute(signed, str(request.base_url)),
            "size": os.path.getsize(tmp_path),
            "duration": duration,
            "receivedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        studio_service.store_artifact(run_id, artifact)

        if (run.get("status") or "") == "queued":
            studio_service.update_run(run_id, status="processing", stage="receiving", progress=30)
        return {"ok": True, "saved": rel}
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.post("/studio/progress/{run_id}")
async def receive_progress(run_id: str, request: Request):
    """Worker streams coarse progress; body may be {progress, message}."""
    run = studio_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    token = (request.query_params.get("token") or "").strip()
    s: Settings = request.app.state.settings
    if not _verify_worker_token(run_id, token, s.sign_secret):
        raise HTTPException(status_code=403, detail="Invalid worker token.")
    if (run.get("status") or "") not in ("queued", "processing"):
        return {"ok": True}

    body = None
    try:
        body = await request.json()
    except Exception:
        pass
    body = body or {}
    progress = min(99, int(body.get("progress", run.get("progress") or 0)))
    message = str(body.get("message") or run.get("message") or "")
    studio_service.update_run(
        run_id, status="processing", stage="generating", progress=progress, message=message
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_refs(raw, tool: str, model: str, uid: str, storage) -> list:
    """Validate + convert media refs. Roles come from the entry or slot order."""
    spec = (
        MODEL_REFS.get(tool, {}).get(model)
        or TOOL_REFS.get(tool, [])
    )
    valid_roles = {slot["role"] for slot in spec}
    if not isinstance(raw, list):
        raw = [raw] if raw else raw
    refs = []
    for i, entry in enumerate(raw):
        role = ""
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, dict):
            path = entry.get("path")
            role = (entry.get("role") or "").strip()
        else:
            raise HTTPException(status_code=400, detail="Invalid ref entry.")
        path = (path or "").strip()
        if not path:
            continue
        if not role and i < len(spec):
            role = spec[i]["role"]
        if role and role not in valid_roles:
            role = ""
        if not (
            path.startswith(f"media/{uid}/")
            or path.startswith(f"studio/{uid}/")
            or path.startswith(f"videos/{uid}/")
        ):
            raise HTTPException(status_code=403, detail=f"Media does not belong to you: {path}")
        try:
            storage.exists(path)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Media not found: {path}")
        refs.append({"path": path, "role": role})
    present = {r["role"] for r in refs if r["role"]}
    missing = [slot["role"] for slot in spec if slot["required"] and slot["role"] not in present]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"{tool} requires media for: {', '.join(missing)}",
        )
    return refs


def _apply_aspect(tool: str, model: str, params: dict) -> dict:
    """Turn a chosen aspect ratio into concrete width/height.

    "auto" leaves the model's tuned canvas alone, so this is a no-op for every
    existing caller. A fixed ratio re-shapes the canvas but keeps its pixel
    budget (the longer existing edge), so picking 16:9 on an SDXL 1024x1024
    default gives 1024x576 rather than dropping to the video tier.

    Video models may arrive with width/height at 0, meaning "follow the source
    clip". When the user picks a ratio there is no existing budget to keep, so
    those get the video tier (832px long side, the native Wan training size).
    """
    aspect = (params.get("aspect") or aspect_lib.AUTO).strip()
    if aspect == aspect_lib.AUTO:
        return params
    if not aspect_lib.is_valid(aspect):
        raise HTTPException(status_code=400, detail=f"Unknown aspect ratio: {aspect}")
    width = int(params.get("width") or 0)
    height = int(params.get("height") or 0)
    budget = max(width, height)
    if budget <= 0:
        budget = aspect_lib.VIDEO_TIERS["sd"]
    params["width"], params["height"] = aspect_lib.resolve_size(aspect, budget)
    return params


def _param_bounds(tool: str, model: str) -> dict:
    """Cost/stability bounds per parameter."""
    return {
        "width": (64, 2048),
        "height": (64, 2048),
        "steps": (1, 60),
        "cfg": (1.0, 20.0),
        "denoise": (0.0, 1.0),
        "frames": (1, 512),
        "fps": (1, 60),
        "seed": (0, 99999999),
        "exaggeration": (0.0, 1.0),
        "cfg_weight": (0.0, 1.0),
        "camera_speed": (0.1, 2.0),
    }


def _normalize_params(params: dict, defaults: dict, bounds: dict | None = None) -> dict:
    out = {}
    for key, defval in defaults.items():
        if key in ("prompt", "negative"):
            continue
        if key not in params:
            out[key] = defval
            continue
        val = params[key]
        try:
            if isinstance(defval, float):
                val = float(val)
            elif isinstance(defval, int):
                val = int(val)
            else:
                val = val
        except (TypeError, ValueError):
            val = defval
        out[key] = val
    # Guard against absurd values (cost + stability).
    for key, (lo, hi) in (bounds or {}).items():
        if key not in out:
            continue
        try:
            out[key] = (
                min(hi, max(lo, float(out[key])))
                if isinstance(out[key], float)
                else min(hi, max(lo, int(out[key])))
            )
        except (TypeError, ValueError):
            pass
    # Enum params are strings, so the numeric bounds above never touch them —
    # validate them explicitly rather than passing junk through to the pod.
    if out.get("aspect") is not None:
        aspect = str(out["aspect"]).strip()
        if not aspect_lib.is_valid(aspect):
            raise HTTPException(status_code=400, detail=f"Unknown aspect ratio: {aspect}")
        out["aspect"] = aspect
    if out.get("camera_pose") is not None:
        pose = str(out["camera_pose"]).strip()
        if pose not in CAMERA_POSES:
            raise HTTPException(status_code=400, detail=f"Unknown camera move: {pose}")
        out["camera_pose"] = pose
    return out


def _run_out(run: dict, run_id: str = "") -> dict:
    research = run.get("research") or None
    return {
        "run_id": run.get("runId") or run.get("run_id") or run_id,
        "title": run.get("title", ""),
        "tool": run.get("tool", ""),
        "model": run.get("model", ""),
        "prompt": run.get("prompt", ""),
        "status": run.get("status", "queued"),
        "stage": run.get("stage", "queued"),
        "progress": int(run.get("progress", 0)),
        "message": run.get("message", ""),
        "created_at": run.get("createdAt", ""),
        "updated_at": run.get("updatedAt", ""),
        "output": run.get("output"),
        "error": run.get("error"),
        "research": research,
        # Flattened for convenience; `research.credits` is the stored truth.
        "credits": (research or {}).get("credits") or run.get("credits"),
    }
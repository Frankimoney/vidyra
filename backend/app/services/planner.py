"""The planning agent: interview the creator, then draft a production plan.

Modelled on Google Flow's Agent. Flow's insight is an economic one — asking
questions is free, a wrong render is not — so this agent spends a conversation
resolving ambiguity and only then writes the production package that Auto Film
executes. The user edits that plan before a single GPU run is spent.

Two LLM operations, both multi-turn:

1. ``advance`` — one conversational turn. The agent replies, narrows the brief
   and asks the next questions. Returns ``ready`` once the brief is complete.
2. ``_draft_plan`` — turn the finished brief into a production package. The
   result is compiled through ``package.validate_package``, so a drafted plan
   is guaranteed to satisfy the same validation the paste-a-package path uses.

The drafted plan is an ordinary production package, which is what makes the
handoff free: it is submitted to POST /films via the existing `outline` field
and executed by the untouched film worker.
"""

from __future__ import annotations

import json

from pipeline import aspect as aspect_lib

from ..config import settings
from . import gemini
from . import llm as llm_service
from . import package as package_service

#: Brief keys that must be non-empty before a plan is drafted. `characters`
#: is required because package validation demands at least one speaker/voice;
#: a narrator-only film declares a "Narrator".
REQUIRED_BRIEF = ("logline", "style", "length", "characters")

#: Upper bound on questions per turn — an interview, not an interrogation.
MAX_QUESTIONS = 3

#: Phrases that mean "stop asking, write the plan".
_SKIP_RE = ("plan it now", "just plan", "make the plan", "write the plan",
            "build the plan", "skip the questions", "draft it", "go ahead and plan")


class PlannerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_ADVANCE_SYSTEM = (
    "You are the planning agent for Vidyra's Auto Film, a co-director who helps "
    "someone plan a short AI-generated film BEFORE any GPU is spent on it. "
    "Generating footage is expensive and irreversible; talking is free. Your job "
    "is to remove ambiguity in conversation so the crew renders once, correctly.\n\n"
    "Each turn, return ONLY a JSON object:\n"
    '{"reply": str (2-4 sentences, spoken to the creator), '
    '"questions": [str] (0-3 short questions, ask only what you still need), '
    '"brief": {'
    '"logline": str (one sentence: what happens), '
    '"audience": str (who watches), '
    '"tone": str (how it should feel), '
    '"style": str (one of the visual styles), '
    '"length": str (e.g. "6 scenes, about 45 seconds"), '
    '"characters": [{"name": str, "description": str}], '
    '"setting": str (where it happens), '
    '"constraints": str (must-avoid, real people, brand rules, on-screen text)'
    '}, '
    '"ready": bool (true only when logline, style, length and characters are all settled), '
    '"revise": bool (true only when a plan already exists and the creator asked '
    'for a change to it)}\n\n'
    "Rules:\n"
    "- Merge the brief forward. Keep earlier answers; never drop a settled field.\n"
    "- Every film needs at least one character. A narrator-only film declares a "
    '"Narrator" character.\n'
    "- Ask at most 3 questions per turn and only about things you truly cannot "
    "infer. Never re-ask something already answered.\n"
    "- Offer a concrete suggestion with each question so the creator can accept "
    "it by saying yes.\n"
    "- If the creator says to stop asking, set ready to true.\n"
    "- Be brief and warm. No preamble, no lists outside the JSON."
)

_PLAN_SYSTEM = (
    "You are the head writer for Vidyra Auto Film. Turn an agreed creative brief "
    "into a complete production package that a film crew will execute shot by "
    "shot, with no further writing.\n\n"
    "Return ONLY a JSON object with this shape (no prose, no code fences):\n"
    '{"title": str, "style": str, "instructions": str (director notes), '
    '"pacing": str, "characters": [{"name": str, "description": str}], '
    '"scenes": [{"narration": str, "dialogue": {"CharacterName": "line"} '
    'optional, "setting": str (rich visual prompt: location, subjects, objects, '
    'time of day, lighting — end with "__SHAPE__"), "shot": str (camera angle '
    'and motion), "transition": str (one of fade slideleft slideright wipeleft '
    'wiperight zoomin)}]}\n\n'
    "Rules:\n"
    "- Honour the brief exactly: its tone, length, style and characters. Do not "
    "wander into a different story.\n"
    "- Every scene needs a `setting` and either narration or dialogue.\n"
    "- Only characters declared in `characters` may speak.\n"
    "- Keep narration to 1-3 sentences per scene so the film stays watchable.\n"
    "- `setting` is fed to an image generator: describe what is visible, not "
    "what is felt.\n"
    "- Never invent statistics or quotes about real people."
)


# --------------------------------------------------------------------------
# LLM plumbing
# --------------------------------------------------------------------------

def _require_llm():
    if not (settings.gemini_configured or settings.llm_configured):
        raise PlannerError(
            "The planning agent needs an LLM. Set GEMINI_API_KEY (or "
            "LLM_API_KEY + LLM_BASE_URL) on the backend to use it."
        )


def _call(system: str, messages: list, timeout: int) -> dict:
    """Multi-turn JSON call, Gemini first then any OpenAI-compatible endpoint.

    Search grounding is off: this is a writing task, and the film pipeline
    already has a dedicated fact-check pass for live facts.
    """
    _require_llm()
    if settings.gemini_configured:
        data, _ = gemini.gemini_json_history(
            settings.gemini_api_key,
            settings.gemini_model,
            settings.gemini_base_url,
            system,
            messages,
            timeout=timeout,
            use_search=False,
        )
        return data
    return llm_service.chat_json_history(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        system,
        messages,
        timeout=timeout,
    )


# --------------------------------------------------------------------------
# Brief handling
# --------------------------------------------------------------------------

def _merge_brief(previous: dict, incoming) -> dict:
    """Fold the agent's brief into the stored one, keeping settled fields."""
    merged = dict(previous or {})
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif isinstance(value, list):
            value = [v for v in value if v]
            if not value:
                continue
        merged[key] = value
    return merged


def _brief_complete(brief: dict) -> bool:
    for key in REQUIRED_BRIEF:
        value = brief.get(key)
        if isinstance(value, list):
            if not value:
                return False
        elif not str(value or "").strip():
            return False
    return True


def _questions(data: dict) -> list:
    raw = data.get("questions")
    if not isinstance(raw, list):
        return []
    out = [str(q).strip() for q in raw if str(q or "").strip()]
    return out[:MAX_QUESTIONS]


def _wants_plan_now(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(phrase in text for phrase in _SKIP_RE)


# --------------------------------------------------------------------------
# Plan drafting
# --------------------------------------------------------------------------

def _draft_plan(brief: dict, aspect: str, history: list) -> dict:
    """Write the production package and validate it like a pasted package."""
    # Both prompts embed literal JSON braces, so substitute rather than format.
    shape = aspect_lib.prompt_phrase(aspect) or aspect_lib.prompt_phrase(aspect_lib.AUTO)
    system = _PLAN_SYSTEM.replace("__SHAPE__", shape or "9:16 vertical")

    user = (
        "Creative brief agreed with the creator:\n"
        f"{brief}\n\n"
        "Write the production package for it now."
    )
    messages = [*history, {"role": "user", "content": user}]
    data = _call(system, messages, timeout=max(90, settings.gemini_timeout_seconds))

    # Raises PackageError if the model broke the contract; the caller turns
    # that into a 422 with the issues so the user can ask for another draft.
    return package_service.normalize_package(data)


def plan_to_outline_json(plan: dict) -> str:
    """Serialize a plan for POST /films' `outline` field.

    Scenes are re-shaped from the normalized outline back into the authoring
    shape (`script` instead of split narration/dialogue) because that is what a
    person expects to read when reviewing a plan before spending GPU runs.
    """
    scenes = []
    for scene in plan.get("scenes") or []:
        entry = {
            "narration": scene.get("narration", ""),
            "setting": scene.get("setting", ""),
            "shot": scene.get("shot", ""),
        }
        if scene.get("dialogue"):
            entry["dialogue"] = scene["dialogue"]
        if scene.get("transition"):
            entry["transition"] = scene["transition"]
        scenes.append(entry)
    payload = {
        "title": plan.get("title", "Untitled Film"),
        "style": plan.get("style", "cinematic"),
        "characters": [
            {"name": c.get("name", ""), "description": c.get("description", "")}
            for c in plan.get("characters") or []
        ],
        "scenes": scenes,
    }
    if plan.get("instructions"):
        payload["instructions"] = plan["instructions"]
    if plan.get("pacing"):
        payload["pacing"] = plan["pacing"]
    return json.dumps(payload, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def advance(project: dict, message: str, aspect: str) -> dict:
    """Run one planning turn against a stored project.

    Returns ``{"reply", "questions", "brief", "status", "plan"}`` where
    `status` is "planning" or "planned". Raises PlannerError when no LLM is
    configured, and package_service.PackageError when a drafted plan fails
    validation (so the caller can offer another attempt).
    """
    message = (message or "").strip()
    if not message:
        raise PlannerError("Say something first.")

    thread = list(project.get("messages") or [])
    history = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in thread
        if m.get("content")
    ]

    brief = dict(project.get("brief") or {})
    system = _ADVANCE_SYSTEM
    if project.get("status") == "planned" and project.get("plan"):
        system += (
            "\n\nA production plan already exists and the creator has seen it. "
            "If they ask for a change to the film, update the brief and set "
            "revise to true so the plan is rewritten. If they only ask a "
            "question, answer it and set revise to false."
        )

    data = _call(
        system,
        [*history, {"role": "user", "content": message}],
        timeout=max(60, settings.gemini_timeout_seconds),
    )

    brief = _merge_brief(brief, data.get("brief"))
    questions = _questions(data)
    reply = str(data.get("reply", "") or "").strip()
    wants_plan = _wants_plan_now(message)
    revise = bool(data.get("revise"))
    ready = bool(data.get("ready")) or wants_plan or _brief_complete(brief)

    result = {
        "reply": reply,
        "questions": questions,
        "brief": brief,
        "status": "planned" if ready else "planning",
        "plan": None,
    }

    if ready and (wants_plan or revise or project.get("status") != "planned" or not project.get("plan")):
        result["plan"] = _draft_plan(brief, aspect, [*history, {"role": "assistant", "content": reply or message}])

    return result
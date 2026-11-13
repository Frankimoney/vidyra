"""Production-package handling for the Film pipeline.

A "production package" is everything the film agent needs to execute a video
without writing anything itself: instructions, camera angles, scenes and their
dialogue. Users paste EITHER chat-ChatGPT prose OR JSON — this module compiles
whatever arrives into the normalized outline the worker already consumes.

Compile ladder (best to worst):
  1. Parses as JSON ................. straight to validation
  2. LLM available (Gemini/OpenAI) . "structurizer" converts free text -> JSON
  3. No key ......................... lightweight offline screenplay parser
In every case the result is validated and reported as issues before any GPU is
spent (preview endpoint, zero-cost).
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pipeline import aspect as aspect_lib

from ..config import settings

from . import llm as llm_service
from . import gemini

_FENCES_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.MULTILINE)
_JSON_HEURISTIC_RE = re.compile(r"^\s*[\[{]")


class PackageError(RuntimeError):
    def __init__(self, issues: list[str], partial: Optional[dict] = None):
        super().__init__("; ".join(issues))
        self.issues = issues
        self.partial = partial


def looks_like_json(raw: str) -> bool:
    return bool(_JSON_HEURISTIC_RE.match(raw.strip()))


def _strip_fences(raw: str) -> str:
    m = _FENCES_RE.search(raw)
    return m.group(1).strip() if m else raw.strip()


def parse_json(raw: str) -> dict:
    """Tolerant JSON parse: strips fences and leading prose."""
    text = _strip_fences(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first balanced {...} block as a last resort.
        start = text.find("{")
        if start < 0:
            obj = None
        else:
            depth = 0
            in_str = False
            esc = False
            end = -1
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
            obj = json.loads(text[start:end]) if end > 0 else None
    if not isinstance(obj, dict):
        raise PackageError(["The production package must be a single JSON object."])
    return obj


# --------------------------------------------------------------------------
# Validation / normalization
# --------------------------------------------------------------------------

_GLOBAL_KEYWORDS = {"title", "style", "instructions", "pacing", "hook", "music",
                    "transition", "captions", "show_page_name", "sources"}

_FILM_STYLES = {
    "cinematic", "anime", "comedy", "horror", "sci-fi", "fantasy",
    "sports", "documentary",
}


def normalize_style(value) -> str:
    """Keep style to the values the script templates actually support."""
    style = str(value or "").strip().lower()
    return style if style in _FILM_STYLES else "cinematic"


#: Names that stand for a voice rather than a person on screen. A narrator is
#: heard, never drawn, so it must never spend a GPU run on a portrait lock.
#: Everything downstream (quota math, portrait locking, the project cast) reads
#: this one rule so the features cannot disagree about who needs a face.
_VOICE_ONLY_NAMES = frozenset({
    "narrator", "voiceover", "voice over", "voice-over", "announcer",
    "commentator", "n/a", "none",
})


def is_voice_only(character) -> bool:
    """True when this character is a voice, not someone we can draw."""
    if not isinstance(character, dict):
        return False
    return (character.get("name") or "").strip().lower() in _VOICE_ONLY_NAMES


def on_screen_characters(characters) -> list:
    """Characters that actually appear on camera and so need a portrait."""
    return [
        c for c in (characters or [])
        if isinstance(c, dict) and (c.get("name") or "").strip() and not is_voice_only(c)
    ]


def _normalize_characters(chars_raw, issues):
    if not isinstance(chars_raw, list) or not chars_raw:
        issues.append("At least one character is required (field `characters`).")
        return []
    if len(chars_raw) > 8:
        issues.append("Too many characters (max 8).")
    characters = []
    for idx, c in enumerate(chars_raw):
        if not isinstance(c, dict):
            issues.append(f"Character #{idx + 1} must be an object with `name`.")
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            issues.append(f"Character #{idx + 1} is missing `name`.")
            continue
        characters.append({
            "name": name,
            "description": str(c.get("description", "") or name),
            "voice": str(c.get("voice", "") or ""),
        })
    return characters


def _split_dialogue(raw, characters):
    """Split `raw` into (narration, dialogue {name: text}) using character names.

    Handles both multiline blocks (``{"Name": "line"}`` already split by an LLM)
    and inline ``Name: line`` lines. Lines that do not start with a known
    character name are treated as narration.
    """
    if not raw:
        return "", {}
    if isinstance(raw, dict):
        dialogue = {str(k): str(v) for k, v in raw.items() if str(v).strip()}
        return "", dialogue
    text = str(raw)
    names = sorted([c["name"] for c in characters], key=len, reverse=True)
    dialogue = {}
    narration_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = None
        for name in names:
            if line.lower().startswith(name.lower() + ":"):
                match = name
                break
        if match:
            rest = line[len(match):][1:].strip()
            dialogue.setdefault(match, rest)
        else:
            narration_lines.append(line)
    narration = "\n".join(narration_lines).strip()
    return narration, dialogue


def _scene_duration(value):
    """Scene length in seconds (2-8), or None to use the model default."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return round(max(2.0, min(8.0, seconds)), 2)


def _normalize_scenes(data, characters, issues):
    scenes_raw = data.get("scenes") or []
    if not isinstance(scenes_raw, list):
        issues.append("`scenes` must be a list.")
        return []
    if not scenes_raw:
        issues.append("At least one scene is required (field `scenes`).")
        return []
    total = len(scenes_raw)
    if total > settings.film_max_scenes:
        issues.append(f"Too many scenes ({total} > {settings.film_max_scenes}).")
    scenes = []
    for idx, s in enumerate(scenes_raw):
        if not isinstance(s, dict):
            issues.append(f"Scene #{idx + 1} must be an object.")
            continue
        setting = str(s.get("setting", "") or s.get("settings", "") or "")
        shot = str(s.get("shot", "") or s.get("camera", "") or s.get("angle", "") or "")
        script_source = str(s.get("script", "") or s.get("narration", "") or "")
        narration, dialogue = _split_dialogue(script_source, characters)
        if isinstance(s.get("dialogue"), dict):
            for k, v in s["dialogue"].items():
                if str(v).strip():
                    dialogue.setdefault(str(k), str(v))
        if not narration and not dialogue:
            issues.append(f"Scene #{idx + 1} has no narration or dialogue.")
        if not setting:
            issues.append(f"Scene #{idx + 1} is missing `setting` (what to draw).")
        for speaker in dialogue:
            if speaker not in {c["name"] for c in characters}:
                issues.append(
                    f"Scene #{idx + 1}: speaker “{speaker}” is not a declared character."
                )
        scenes.append({
            "narration": narration,
            "dialogue": {k: str(v) for k, v in dialogue.items()},
            "setting": setting,
            "shot": shot,
            "transition": str(s.get("transition", "") or ""),
            # Creator-chosen scene length (seconds); None = model default (~5s).
            "duration": _scene_duration(s.get("duration")),
            # A creator-selected take (Studio image) can replace generation.
            "reference_image": str(s.get("reference_image", "") or ""),
        })
    return scenes


def validate_package(data: dict) -> tuple:
    """Validate and normalize a package dict into the worker outline shape.

    Returns ``(outline, issues)``. The outline always contains as much usable
    structure as could be recovered so callers can preview a partial package.
    """
    issues: list[str] = []
    characters = _normalize_characters(data.get("characters") or data.get("hosts") or [], issues)
    scenes = _normalize_scenes(data, characters, issues)
    title = str(data.get("title", "") or "Untitled Film").strip()
    if len(title) > 80:
        issues.append("Title is too long (max 80 chars).")
    outline = {
        "title": title,
        "style": normalize_style(data.get("style")),
        "instructions": str(data.get("instructions", "") or ""),
        "characters": characters,
        "scenes": scenes,
    }
    for key in ("pacing", "transition", "music", "captions", "show_page_name", "hook", "sources"):
        if key in data:
            outline[key] = data[key]
    return outline, issues


def normalize_package(data: dict) -> dict:
    """Validate and normalize; raises PackageError on any issue."""
    outline, issues = validate_package(data)
    if issues:
        raise PackageError(issues, partial=outline)
    return outline


def compile_package(raw: str, aspect: str = "9:16") -> dict:
    """Turn user-supplied production-package text (JSON or prose) into an outline.

    Raises PackageError on failure with human-readable issues.
    """
    raw = (raw or "").strip()
    if not raw:
        raise PackageError(["Nothing was pasted — send an idea or a production package."])
    if _FENCES_RE.search(raw) or looks_like_json(raw):
        return normalize_package(parse_json(raw))
    if settings.gemini_configured or settings.llm_configured:
        return _structurize(raw, aspect)
    return normalize_package(parse_text_package(raw))


# --------------------------------------------------------------------------
# LLM structurizer (free-text package -> JSON)
# --------------------------------------------------------------------------

_SYSTEM = (
    "You convert a client's plain-text video production package into strict JSON. "
    "Return ONLY a JSON object with this shape (no prose, no code fences): "
    '{"title": str, "style": str optional, "instructions": str optional '
    '(director notes from the client), "characters": [{"name": str, '
    '"description": str}], "scenes": [{"narration": str, "dialogue": '
    '{"CharacterName": "line"} optional, "setting": str (rich visual prompt for '
    'an AI image generator, __SHAPE__), "shot": str (camera angle and motion, '
    'e.g. "slow push-in from the baseline"), "transition": str optional (one of '
    'fade slideleft slideright wipeleft wiperight zoomin) }]}. '
    "Keep every camera angle, scene, and dialogue line the client wrote — do not "
    "invent new scenes. Narration lines that no character speaks go into "
    '"narration". Do not include character names into narration.'
)


def _structurize(raw: str, aspect: str = "9:16") -> dict:
    user = f"Production package to convert:\n\n{raw}"
    # _SYSTEM embeds literal JSON braces, so substitute a placeholder rather
    # than str.format (which would choke on them).
    system = _SYSTEM.replace("__SHAPE__", aspect_lib.prompt_phrase(aspect) or "9:16 vertical")
    if settings.gemini_configured:
        outline, _ = gemini.gemini_json(
            settings.gemini_api_key, settings.gemini_model, settings.gemini_base_url,
            system, user, timeout=settings.gemini_timeout_seconds, use_search=False,
        )
        return normalize_package(outline)
    outline = llm_service.chat_json(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        system,
        user,
        timeout=settings.llm_timeout_seconds,
    )
    return normalize_package(outline)


# --------------------------------------------------------------------------
# Offline screenplay text parser (no LLM key required)
# --------------------------------------------------------------------------

_SCENE_HEADING_RE = re.compile(r"^\s*scene\s*(\d*)?\s*[:.]?", re.IGNORECASE)
_SECTION_NAMES = {
    "title", "style", "instructions", "characters", "setting", "visual", "prompt",
    "shot", "camera", "angle", "motion", "transition", "hook", "pacing", "music",
}
_GLOBAL_SECTIONS = {"title", "style", "instructions", "hook", "pacing", "music"}
_SCENE_SECTIONS = {"setting": "setting", "visual": "setting", "prompt": "setting",
                   "shot": "shot", "camera": "shot", "camera angle": "shot",
                   "angle": "shot", "motion": "shot", "transition": "transition"}
_ALL_SECTIONS = _GLOBAL_SECTIONS | {"characters"} | set(_SCENE_SECTIONS)


def parse_text_package(raw: str) -> dict:
    """Best-effort parse of a screenplay-style plain-text package.

    Recognizes sections (Title / Characters / Setting / Shot / Transition…),
    `Scene N` headings, `Character: line` dialogue and free narration lines.
    Anything the parser can't satisfy becomes a validation issue later.
    """
    data: dict = {"characters": [], "scenes": []}
    current_scene = None
    current_field = None
    name_list = []
    for raw_line in (raw or "").splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            current_field = None
            continue
        if _SCENE_HEADING_RE.match(line):
            current_scene = {"setting": "", "shot": "", "transition": "", "script": ""}
            data["scenes"].append(current_scene)
            current_field = None
            continue
        key = None
        value = ""
        if line.lower() in _SECTION_NAMES:
            key, value = line.lower(), ""
        else:
            cm = re.match(r"^\s*([A-Za-z][A-Za-z0-9 ]+)\s*:\s*(.*)$", line)
            if cm and cm.group(1).strip().lower() in _ALL_SECTIONS:
                key, value = cm.group(1).strip().lower(), cm.group(2).strip()
        if key:
            if key in _GLOBAL_SECTIONS:
                if value:
                    data[key] = value
                current_field = key
            elif key == "characters":
                current_field = "characters"
                current_scene = None
            elif current_scene is not None and key in _SCENE_SECTIONS:
                target = _SCENE_SECTIONS[key]
                current_scene[target] = (current_scene[target] + " " + value).strip()
                current_field = f"scene:{target}"
            continue
        if current_field == "characters":
            m = re.match(r"^\s*[\-\*]?\s*([A-Za-z][A-Za-z0-9 ]{0,40}?)\s*:\s*(.*)$", line)
            if m:
                data["characters"].append({"name": m.group(1).strip(), "description": m.group(2).strip()})
                name_list.append(m.group(1).strip())
            continue
        if current_scene is None:
            continue
        speaker = next((n for n in name_list if line.lower().startswith(n.lower() + ":")), None)
        if speaker:
            current_scene["script"] = (current_scene["script"] + "\n" if current_scene["script"] else "") + f"{speaker}: {line[len(speaker):][1:].strip()}"
        else:
            current_scene["script"] = (current_scene["script"] + "\n" if current_scene["script"] else "") + line
    if not data.get("title"):
        head = next((s["script"].splitlines()[0].strip() for s in data["scenes"] if s.get("script", "").strip()), None)
        data["title"] = (head or "Untitled Film")[:60]
    return data
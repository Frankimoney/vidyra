"""Player Story recipe — a structured profile into a short documentary.

Narrator-only by design: portrait/lip-sync acting costs extra GPU runs, and a
profile reads better as a narrated arc. The existing imagebrief grounding in
the film worker still puts the real player's likeness and team colours into
the visuals.
"""

from __future__ import annotations

from . import base

META = {
    "id": "player_story",
    "name": "Player story",
    "description": "A three-act player profile from achievements, stats and quotes.",
    "fields": [
        {"name": "name", "label": "Player", "type": "text", "placeholder": "e.g. A'ja Wilson", "required": True},
        {"name": "team", "label": "Team", "type": "text", "placeholder": "e.g. South Carolina", "required": True},
        {"name": "position", "label": "Position", "type": "text", "placeholder": "e.g. Forward", "required": False},
        {"name": "era", "label": "Era / years", "type": "text", "placeholder": "e.g. 2014–2018", "required": False},
        {"name": "achievements", "label": "Achievements", "type": "textarea", "placeholder": "One per line…", "required": False},
        {"name": "stats", "label": "Key stats", "type": "textarea", "placeholder": "One per line, e.g. 17.3 PPG / 9.1 RPG", "required": False},
        {"name": "quotes", "label": "Quotes", "type": "textarea", "placeholder": "One per line, with attribution…", "required": False},
        {"name": "tone", "label": "Tone", "type": "text", "placeholder": "e.g. celebratory, reflective", "required": False},
        {"name": "scene_count", "label": "Scenes", "type": "number", "default": 7, "min": 2, "max": 16},
    ],
}

_SYSTEM = (
    "You write a short three-act sports documentary about one player for a vertical feed. "
    "Single Narrator, no dialogue. Act 1 origins, Act 2 rise, Act 3 legacy. "
    "Use only facts supplied by the user or verified search results — never invent "
    "stats, awards, scores or quotes. If a detail is missing, write around it rather "
    "than guessing. 35-60 words per scene. `setting` is a rich visual prompt for an "
    "AI image generator naming the real player/team/arena and ends with the frame "
    "phrase given below; `shot` is camera angle plus motion. Return ONLY JSON: "
    '{"title": str, "style": "cinematic", "characters": [{"name": "Narrator", '
    '"description": str}], "scenes": [{"narration": str, "setting": str, '
    '"shot": str, "transition": "fade"}]}'
)


def _lines(payload: dict, key: str) -> list[str]:
    raw = payload.get(key)
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items = [ln.strip() for ln in str(raw or "").splitlines()]
    return [i for i in items if i]


def _offline(payload: dict, count: int, shape: str) -> dict:
    name = str(payload.get("name") or "The player").strip()
    team = str(payload.get("team") or "their team").strip()
    position = str(payload.get("position") or "").strip()
    era = str(payload.get("era") or "").strip()
    achievements = _lines(payload, "achievements")
    stats = _lines(payload, "stats")
    quotes = _lines(payload, "quotes")

    beats = [
        f"{name} became a defining figure for {team}"
        + (f" {era}" if era else "")
        + (f" at {position}" if position else "")
        + "."
    ]
    beats += achievements
    beats += [f"The numbers tell part of the story: {s}." for s in stats]
    beats += [f'"{q}"' for q in quotes]
    beats.append(f"From first appearance to lasting legacy, {name} left a standard behind.")

    buckets = base.chunks(beats, count)
    scenes = []
    for bucket in buckets:
        narration = " ".join(bucket)[:520].strip()
        scenes.append(
            {
                "narration": narration,
                "setting": (
                    f"cinematic documentary portrait of {name} of {team}, "
                    f"dramatic arena light, {narration[:90]}, {shape}"
                ),
                "shot": "slow push-in",
                "transition": "fade",
            }
        )
    return {
        "title": f"{name}: the {team} story"[:80],
        "style": "cinematic",
        "characters": [base.narrator()],
        "scenes": scenes,
    }


def draft(payload: dict, defaults: dict, aspect: str) -> dict:
    name = str(payload.get("name") or "").strip()
    team = str(payload.get("team") or "").strip()
    if not name or not team:
        raise base.RecipeError("A player story needs a player name and a team.")

    count = base.scene_count(payload, default=7)
    shape = base.shape_phrase(aspect)
    facts = {
        "player": name,
        "team": team,
        "position": payload.get("position") or "",
        "era": payload.get("era") or "",
        "achievements": _lines(payload, "achievements"),
        "stats": _lines(payload, "stats"),
        "quotes": _lines(payload, "quotes"),
        "tone": payload.get("tone") or "reflective and celebratory",
    }
    user = (
        f"Player facts (only source of truth):\n{facts}\n"
        f"Scenes: {count}\nFrame phrase for every `setting`: {shape}"
    )
    data, sources, provider = base.call_json(_SYSTEM, user, use_search=True)
    fallback = _offline(payload, count, shape)
    return base.finish(
        data,
        fallback,
        provider,
        {**payload, "sources": sources},
        defaults,
    )

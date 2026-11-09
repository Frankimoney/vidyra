"""Game Recap recipe — a manual/JSON box score into a highlight recap.

Entered by hand for v1 (no sports-data API): score, a few stats, highlights
and optional quotes. The same package shape feeds the existing film worker.
"""

from __future__ import annotations

from . import base

META = {
    "id": "game_recap",
    "name": "Game recap",
    "description": "A final-score recap from stats, highlights and quotes you enter.",
    "fields": [
        {"name": "team", "label": "Team", "type": "text", "placeholder": "e.g. South Carolina", "required": True},
        {"name": "opponent", "label": "Opponent", "type": "text", "placeholder": "e.g. LSU", "required": True},
        {"name": "team_score", "label": "Team score", "type": "number", "placeholder": "e.g. 78", "required": True},
        {"name": "opponent_score", "label": "Opponent score", "type": "number", "placeholder": "e.g. 64", "required": True},
        {"name": "date", "label": "Date", "type": "text", "placeholder": "e.g. Feb 8, 2026", "required": False},
        {"name": "stats", "label": "Key stats", "type": "textarea", "placeholder": "One per line, e.g. Rebounds 42–30", "required": False},
        {"name": "highlights", "label": "Turning points", "type": "textarea", "placeholder": "One per line…", "required": False},
        {"name": "quotes", "label": "Quotes", "type": "textarea", "placeholder": "One per line, with attribution…", "required": False},
        {"name": "scene_count", "label": "Scenes", "type": "number", "default": 6, "min": 2, "max": 16},
    ],
}

_SYSTEM = (
    "You write a short sports game recap for a vertical feed. Single Narrator, no dialogue. "
    "Order: final score hook, the decisive stretch, two or three key stats, player of the "
    "game if supplied, what it means / next up. Use ONLY the supplied score, stats and "
    "quotes — never invent numbers or quotes. 30-55 words per scene. `setting` is a rich "
    "visual prompt for an AI image generator naming the real teams/arena/action and ends "
    "with the frame phrase given below; `shot` is camera angle plus motion. Return ONLY "
    'JSON: {"title": str, "style": "sports", "characters": [{"name": "Narrator", '
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


def _scoreline(payload: dict) -> str:
    team = str(payload.get("team") or "The team").strip()
    opponent = str(payload.get("opponent") or "the opponent").strip()
    try:
        our = int(payload.get("team_score"))
        theirs = int(payload.get("opponent_score"))
    except (TypeError, ValueError):
        return f"{team} faced {opponent}."
    verb = "beat" if our > theirs else "fell to" if our < theirs else "tied"
    return f"{team} {verb} {opponent} {our}-{theirs}."


def _offline(payload: dict, count: int, shape: str) -> dict:
    team = str(payload.get("team") or "The team").strip()
    opponent = str(payload.get("opponent") or "the opponent").strip()
    date = str(payload.get("date") or "").strip()
    stats = _lines(payload, "stats")
    highlights = _lines(payload, "highlights")
    quotes = _lines(payload, "quotes")

    beats = [_scoreline(payload) + (f" ({date})" if date else "")]
    beats += highlights
    beats += [f"By the numbers: {s}." for s in stats]
    beats += [f'"{q}"' for q in quotes]
    beats.append(f"{team} now turns to what comes next.")

    buckets = base.chunks(beats, count)
    scenes = []
    for bucket in buckets:
        narration = " ".join(bucket)[:520].strip()
        scenes.append(
            {
                "narration": narration,
                "setting": (
                    f"cinematic sports recap B-roll of {team} versus {opponent}, "
                    f"stadium lights, decisive action, {narration[:90]}, {shape}"
                ),
                "shot": "slow push-in",
                "transition": "fade",
            }
        )
    return {
        "title": f"{team} vs {opponent} recap"[:80],
        "style": "sports",
        "characters": [base.narrator()],
        "scenes": scenes,
    }


def draft(payload: dict, defaults: dict, aspect: str) -> dict:
    team = str(payload.get("team") or "").strip()
    opponent = str(payload.get("opponent") or "").strip()
    if not team or not opponent:
        raise base.RecipeError("A game recap needs both teams.")
    try:
        int(payload.get("team_score"))
        int(payload.get("opponent_score"))
    except (TypeError, ValueError):
        raise base.RecipeError("A game recap needs both final scores as numbers.")

    count = base.scene_count(payload, default=6)
    shape = base.shape_phrase(aspect)
    facts = {
        "scoreline": _scoreline(payload),
        "date": payload.get("date") or "",
        "stats": _lines(payload, "stats"),
        "highlights": _lines(payload, "highlights"),
        "quotes": _lines(payload, "quotes"),
    }
    user = (
        f"Game facts (only source of truth):\n{facts}\n"
        f"Scenes: {count}\nFrame phrase for every `setting`: {shape}"
    )
    data, sources, provider = base.call_json(_SYSTEM, user, use_search=False)
    fallback = _offline(payload, count, shape)
    return base.finish(
        data,
        fallback,
        provider,
        {**payload, "sources": sources, "research": False},
        defaults,
    )

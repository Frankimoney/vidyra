"""Recipe registry.

Add a module here and it appears in the New Video hub automatically. Every
recipe exposes ``META`` and ``draft(payload, defaults, aspect) -> dict`` and
returns a validated production package without spending GPU time.
"""

from __future__ import annotations

from . import custom, game_recap, news, player_story
from .base import RecipeError

_MODULES = (news, player_story, game_recap, custom)
RECIPES = {mod.META["id"]: mod for mod in _MODULES}


def get(recipe_id: str):
    return RECIPES.get((recipe_id or "").strip().lower())


def catalog() -> list[dict]:
    return [dict(mod.META) for mod in _MODULES]


__all__ = ["RecipeError", "RECIPES", "catalog", "get"]

"""Checks for workspaces, recipe drafting, and the usage ledger.

Drafting must be free and offline-safe: every recipe here runs with no API
keys and still produces a package the film worker accepts (>=2 scenes, each
with narration and a `setting`). Run:

    .venv/Scripts/python.exe scripts/test_recipes.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.services import recipes, usage, workspaces  # noqa: E402
from app.services.recipes import base  # noqa: E402
from app.services.recipes.base import RecipeError  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def section(title: str) -> None:
    print(f"\n{title}")


UID = "test-recipes-user"
WORKSPACE = workspaces.ensure_default(UID)["workspaceId"]
DEFAULTS = workspaces.resolve_defaults(UID, WORKSPACE)

ARTICLE = (
    "South Carolina beat LSU 78-64 on Sunday night. The Gamecocks closed the "
    "third quarter on a 12-0 run to take control. Forward A'ja Wilson led all "
    "scorers with 24 points and 11 rebounds. The win moves South Carolina to "
    "20-1 on the season."
)


section("Workspace seed and precedence")
check("seed exists", bool(WORKSPACE))
check("seed page name", DEFAULTS.get("page_name") == "South Carolina Women's Basketball")
check("seed aspect is vertical", DEFAULTS.get("aspect") == "9:16")
check(
    "explicit job settings win over workspace defaults",
    workspaces.apply_defaults({"aspect": "16:9"}, UID, WORKSPACE).get("aspect") == "16:9",
)
check(
    "workspace fills blanks",
    workspaces.apply_defaults({"aspect": ""}, UID, WORKSPACE).get("aspect") == "9:16",
)
check(
    "unknown keys are dropped when a whitelist is given",
    "page_name"
    not in workspaces.apply_defaults(
        {"page_name": "x"}, UID, WORKSPACE, allowed={"aspect"}
    ),
)
check(
    "unknown workspace falls back to the seed",
    workspaces.resolve_defaults(UID, "does-not-exist").get("aspect") == "9:16",
)


section("Recipe catalog")
ids = [m["id"] for m in recipes.catalog()]
check(
    "recipes ship",
    ids == ["news", "player_story", "game_recap", "custom"],
    str(ids),
)
for meta in recipes.catalog():
    check(f"{meta['id']} has fields", bool(meta.get("fields")))


section("News recipe (offline)")
result = recipes.get("news").draft({"text": ARTICLE}, DEFAULTS, "9:16")
outline = result["outline"]
check("no issues", result["issues"] == [], str(result["issues"]))
check("provider is offline", result["provider"] == "offline", result["provider"])
check("at least two scenes", len(outline["scenes"]) >= 2, str(len(outline["scenes"])))
check(
    "every scene has narration and setting",
    all(s.get("narration") and s.get("setting") for s in outline["scenes"]),
)
check(
    "frame phrase is in the setting",
    all("9:16" in s["setting"] for s in outline["scenes"]),
)
check("score survives extraction", "78-64" in outline["scenes"][0]["narration"])
check("article metadata returned", result["article"]["word_count"] > 30)
check(
    "estimate is two runs per scene",
    result["estimate"]["gpu_runs"] == 2 * len(outline["scenes"]),
)

try:
    recipes.get("news").draft({"url": "file:///etc/passwd"}, DEFAULTS, "9:16")
    check("non-http link rejected", False)
except RecipeError as exc:
    check("non-http link rejected", "http" in str(exc))

try:
    recipes.get("news").draft({"url": "http://127.0.0.1/secret"}, DEFAULTS, "9:16")
    check("private address rejected", False)
except RecipeError as exc:
    check("private address rejected", "private" in str(exc).lower(), str(exc))


section("Player Story recipe (offline)")
try:
    recipes.get("player_story").draft({"name": "A'ja Wilson"}, DEFAULTS, "9:16")
    check("missing team rejected", False)
except RecipeError:
    check("missing team rejected", True)

result = recipes.get("player_story").draft(
    {
        "name": "A'ja Wilson",
        "team": "South Carolina",
        "position": "Forward",
        "achievements": ["National champion", "National Player of the Year"],
        "stats": ["17.3 PPG", "9.1 RPG"],
        "quotes": ["Hard work beats talent."],
    },
    DEFAULTS,
    "9:16",
)
outline = result["outline"]
check("player story validates", result["issues"] == [], str(result["issues"]))
check("player story has scenes", len(outline["scenes"]) >= 2)
check(
    "player name stays in the visuals",
    all("A'ja Wilson" in s["setting"] for s in outline["scenes"]),
)


section("Game Recap recipe (offline)")
try:
    recipes.get("game_recap").draft(
        {"team": "South Carolina", "opponent": "LSU"}, DEFAULTS, "9:16"
    )
    check("missing scores rejected", False)
except RecipeError:
    check("missing scores rejected", True)

result = recipes.get("game_recap").draft(
    {
        "team": "South Carolina",
        "opponent": "LSU",
        "team_score": 78,
        "opponent_score": 64,
        "highlights": ["12-0 run in the third"],
        "stats": ["Rebounds 42-30"],
        "scene_count": 6,
    },
    DEFAULTS,
    "9:16",
)
outline = result["outline"]
check("recap validates", result["issues"] == [], str(result["issues"]))
check("recap opens on the score", "78-64" in outline["scenes"][0]["narration"])
check(
    "no duplicate narration when beats < requested scenes",
    len({s["narration"] for s in outline["scenes"]}) == len(outline["scenes"]),
)


section("Usage ledger")
usage.record(UID, workspace_id=WORKSPACE, model="sdxl", reference="test-1")
usage.record(UID, workspace_id=WORKSPACE, model="wan21-i2v", units=2, reference="test-2")
summary = usage.summary(UID, workspace_id=WORKSPACE, days=1)
check("runs counted", summary["runs"] == 3, str(summary["runs"]))
check("cost uses the price table", abs(summary["estimated_cost"] - 0.192) < 1e-6,
      str(summary["estimated_cost"]))
check("daily runs counted", usage.daily_runs(UID, WORKSPACE) == 3)
check("other workspace is isolated", usage.daily_runs(UID, "other") == 0)
check(
    "prices are ordered by model weight",
    usage.price_for("wan21-i2v") > usage.price_for("sdxl"),
)
check("unknown model has a fallback price", usage.price_for("mystery") == usage.DEFAULT_PRICE)


section(f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)

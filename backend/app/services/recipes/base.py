"""Shared helpers for recipe drafting.

A recipe turns structured input (an article, a player, a box score) into a
production package the existing film worker already understands. Drafting is
zero-GPU: the only paid work happens after the creator reviews the plan.

Everything here degrades: Gemini first (grounded when useful), then an
OpenAI-compatible LLM, then a deterministic offline template. A recipe must
never fail just because no key is configured.
"""

from __future__ import annotations

import re

from pipeline import aspect as aspect_lib
from pipeline.narration import is_clone_voice

from ...config import settings
from .. import factcheck as factcheck_service
from .. import gemini
from .. import llm
from .. import package as package_service


class RecipeError(RuntimeError):
    pass


def scene_count(payload: dict, default: int = 7) -> int:
    try:
        count = int(payload.get("scene_count") or default)
    except (TypeError, ValueError):
        count = default
    return max(2, min(count, int(settings.film_max_scenes)))


def shape_phrase(aspect: str) -> str:
    return aspect_lib.prompt_phrase(aspect) or "9:16 vertical frame"


def narrator(voice: str = "") -> dict:
    return {
        "name": "Narrator",
        "description": "warm, authoritative sports narrator",
        "voice": voice or "",
    }


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip().split()) >= 4]


def chunks(items: list, count: int) -> list[list]:
    """Split items into at most `count` non-empty contiguous buckets."""
    if not items:
        return [[] for _ in range(count)]
    count = max(1, min(count, len(items)))
    buckets: list[list] = [[] for _ in range(count)]
    for i, item in enumerate(items):
        buckets[min(count - 1, (i * count) // len(items))].append(item)
    return buckets


def call_json(system: str, user: str, use_search: bool = True) -> tuple[dict | None, list, str]:
    """Ask the best configured model for a JSON package."""
    if settings.gemini_configured:
        try:
            data, sources = gemini.gemini_json(
                settings.gemini_api_key,
                settings.gemini_model,
                settings.gemini_base_url,
                system,
                user,
                timeout=settings.gemini_timeout_seconds,
                use_search=use_search,
            )
            if isinstance(data, dict):
                return data, sources or [], "gemini"
        except Exception:
            pass
    if settings.llm_configured:
        try:
            data = llm.chat_json(
                settings.llm_base_url,
                settings.llm_api_key,
                settings.llm_model,
                system,
                user,
                timeout=settings.llm_timeout_seconds,
            )
            if isinstance(data, dict):
                return data, [], "llm"
        except Exception:
            pass
    return None, [], "offline"


def estimate(outline: dict, voice: str = "") -> dict:
    """Lower-bound GPU estimate: 2 runs per scene, +1 for a cloned narrator."""
    scenes = len(outline.get("scenes") or [])
    runs = scenes * 2 + (1 if is_clone_voice(voice or "") else 0)
    return {"scenes": scenes, "gpu_runs": runs}


def _with_narrator_voice(outline: dict, voice: str) -> None:
    if not voice:
        return
    for char in outline.get("characters") or []:
        if not char.get("voice"):
            char["voice"] = voice


def finish(
    data: dict | None,
    fallback: dict,
    provider: str,
    payload: dict,
    defaults: dict,
) -> dict:
    """Validate, fall back, fact-check, and shape the API response."""
    voice = str(defaults.get("voice") or payload.get("voice") or "")
    outline, issues = package_service.validate_package(data or {})
    if issues:
        fb_outline, fb_issues = package_service.validate_package(fallback)
        if not fb_issues:
            outline, issues, provider = fb_outline, [], "offline"
        else:
            # Keep the more complete of the two so the creator still sees a plan.
            if len(fb_outline.get("scenes") or []) > len(outline.get("scenes") or []):
                outline, issues = fb_outline, fb_issues

    _with_narrator_voice(outline, voice)

    report = {"checked": False, "sources": [], "corrected": 0, "notes": [], "error": None}
    if provider != "offline" and payload.get("research", True):
        try:
            report = factcheck_service.fact_check_outline(outline, settings)
        except Exception as exc:  # noqa: BLE001 - research is best-effort
            report["error"] = f"fact-check unavailable: {exc}"

    sources = [s for s in report.get("sources") or [] if isinstance(s, dict) and s.get("url")]
    for src in payload.get("sources") or []:
        if isinstance(src, dict) and src.get("url"):
            sources.append(src)
    if sources:
        outline["sources"] = sources

    return {
        "outline": outline,
        "issues": issues,
        "sources": sources,
        "fact_check": report,
        "provider": provider,
        "estimate": estimate(outline, voice),
    }

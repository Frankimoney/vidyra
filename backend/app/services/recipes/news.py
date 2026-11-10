"""News recipe — article or topic → a short, fact-checked vertical video."""

from __future__ import annotations

from ...config import settings
from .. import articles
from . import base

META = {
    "id": "news",
    "name": "News video",
    "description": "Turn an article link or pasted text into a narrated, captioned vertical video.",
    "fields": [
        {"name": "url", "label": "Article link", "type": "url", "placeholder": "https://…", "required": False},
        {"name": "text", "label": "Or paste the article", "type": "textarea", "placeholder": "Paste the article text…", "required": False},
        {"name": "scene_count", "label": "Scenes", "type": "number", "default": 7, "min": 2, "max": 16},
        {"name": "instructions", "label": "Angle / notes", "type": "textarea", "placeholder": "Tone, focus, anything to emphasise…", "required": False},
    ],
}

_SYSTEM = (
    "You write short sports news videos for a vertical social feed. "
    "Turn the supplied article into a production package. Rules: "
    "single Narrator, no dialogue; open on the single most newsworthy fact; "
    "never invent scores, names, dates or quotes — use only the article, or "
    "verified search facts when search is enabled; 35-60 words of narration per scene. "
    "`setting` must be a rich visual prompt for an AI image generator that names the "
    "real people/places/action and ends with the frame phrase given below. "
    "`shot` is a camera angle plus motion. Title at most 70 characters. "
    "Return ONLY JSON: "
    '{"title": str, "style": "sports", "characters": [{"name": "Narrator", '
    '"description": str}], "scenes": [{"narration": str, "setting": str, '
    '"shot": str, "transition": "fade"}]}'
)


def _offline(article: dict, count: int, shape: str) -> dict:
    lines = base.sentences(article.get("text") or "")
    buckets = base.chunks(lines, count)
    scenes = []
    for bucket in buckets:
        narration = " ".join(bucket)[:520].strip()
        if not narration:
            narration = (article.get("title") or "The story is developing.")[:300]
        scenes.append(
            {
                "narration": narration,
                "setting": (
                    "cinematic sports-news B-roll illustrating: "
                    f"{narration[:140]}, {shape}"
                ),
                "shot": "slow push-in",
                "transition": "fade",
            }
        )
    title = (article.get("title") or (lines[0] if lines else "Sports news"))[:80]
    return {
        "title": title,
        "style": "sports",
        "characters": [base.narrator()],
        "scenes": scenes,
    }


def draft(payload: dict, defaults: dict, aspect: str) -> dict:
    source = (
        payload.get("url")
        or payload.get("text")
        or payload.get("source")
        or ""
    ).strip()
    try:
        article = articles.extract(source, str(payload.get("title") or ""))
    except articles.ArticleError as exc:
        raise base.RecipeError(str(exc))

    count = base.scene_count(payload, default=7)
    shape = base.shape_phrase(aspect)
    instructions = str(payload.get("instructions") or "").strip()

    user = (
        f"Headline: {article['title']}\n"
        f"Source: {article['site'] or 'pasted text'}\n"
        f"Scenes: {count}\n"
        f"Frame phrase for every `setting`: {shape}\n"
        f"Extra instructions: {instructions or 'none'}\n\n"
        f"Article:\n{article['text'][:12000]}"
    )
    data, sources, provider = base.call_json(
        _SYSTEM,
        user,
        use_search=bool(settings.gemini_configured),
    )
    fallback = _offline(article, count, shape)
    result = base.finish(
        data,
        fallback,
        provider,
        {**payload, "sources": sources},
        defaults,
    )
    result["article"] = {
        "title": article["title"],
        "url": article["url"],
        "site": article["site"],
        "word_count": article["word_count"],
        "truncated": article["truncated"],
    }
    return result

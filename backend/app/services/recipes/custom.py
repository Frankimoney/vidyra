"""Custom recipe — the creator's own brief, structured like the built-ins.

This is the escape hatch between a fixed recipe and a full production package:
a topic, free-form instructions, a tone and a scene count. It still returns a
validated package and still drafts for free.
"""

from __future__ import annotations

from . import base

META = {
    "id": "custom",
    "name": "Custom video",
    "description": "Your own topic and directions, structured into a scene plan.",
    "fields": [
        {"name": "topic", "label": "Topic / title", "type": "text", "placeholder": "e.g. The comeback nobody saw coming", "required": True},
        {"name": "instructions", "label": "Directions", "type": "textarea", "placeholder": "Tone, angle, must-say facts, call to action…", "required": False},
        {"name": "tone", "label": "Tone", "type": "text", "placeholder": "e.g. hype, reflective, documentary", "required": False},
        {"name": "scene_count", "label": "Scenes", "type": "number", "default": 7, "min": 2, "max": 16},
    ],
}

_SYSTEM = (
    "You write short vertical social videos. Turn the creator's topic and directions "
    "into a production package. Single Narrator, no dialogue. Use only facts supplied "
    "by the creator; if a fact is missing, write around it rather than inventing it. "
    "35-60 words of narration per scene. `setting` is a rich visual prompt for an AI "
    "image generator naming real people/places/action and ends with the frame phrase "
    "given below. `shot` is a camera angle plus motion. Return ONLY JSON: "
    '{"title": str, "style": "sports", "characters": [{"name": "Narrator", '
    '"description": str}], "scenes": [{"narration": str, "setting": str, '
    '"shot": str, "transition": "fade"}]}'
)


def _offline(payload: dict, count: int, shape: str) -> dict:
    topic = str(payload.get("topic") or "A new video").strip()
    instructions = str(payload.get("instructions") or "").strip()
    tone = str(payload.get("tone") or "confident").strip()
    lines = base.sentences(instructions) or [
        f"Here is the story behind {topic}.",
        f"Told with a {tone} point of view.",
        "The details that matter, in order.",
        "What it means, and what comes next.",
    ]
    beats = [
        f"The story behind {topic}.",
        f"Seen through a {tone} lens.",
        "The moment that changed it.",
        "What happens next.",
    ]
    while len(lines) < count:
        lines.append(beats[(len(lines) - 1) % len(beats)])
    buckets = base.chunks(lines, count)
    scenes = []
    for i, bucket in enumerate(buckets):
        narration = " ".join(bucket)[:520]
        scenes.append(
            {
                "narration": narration,
                "setting": (
                    f"cinematic social video shot about {topic}, {tone} mood, "
                    f"{narration[:90]}, {shape}"
                ),
                "shot": "slow push-in" if i % 2 == 0 else "slow pull-back",
                "transition": "fade",
            }
        )
    return {
        "title": topic[:80],
        "style": "sports",
        "characters": [base.narrator()],
        "scenes": scenes,
    }


def draft(payload: dict, defaults: dict, aspect: str) -> dict:
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        raise base.RecipeError("A custom video needs a topic or title.")

    count = base.scene_count(payload, default=7)
    shape = base.shape_phrase(aspect)
    user = (
        f"Topic: {topic}\n"
        f"Tone: {payload.get('tone') or 'confident'}\n"
        f"Directions:\n{payload.get('instructions') or 'none'}\n"
        f"Scenes: {count}\nFrame phrase for every `setting`: {shape}"
    )
    data, sources, provider = base.call_json(_SYSTEM, user, use_search=False)
    fallback = _offline(payload, count, shape)
    return base.finish(data, fallback, provider, {**payload, "sources": sources}, defaults)

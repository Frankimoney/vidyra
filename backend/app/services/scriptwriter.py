"""Screenplay writer for Auto Film.

Turns a one-line idea into a full film structure (title, characters, scenes
with narration + dialogue + visual prompts). Uses the configured LLM when
available, otherwise falls back to built-in story templates so the feature
works with zero API keys.

Output contract (same shape whether LLM or template produced it):

    {
      "title": str,
      "style": str,
      "characters": [{"name", "description", "voice"}],
      "scenes": [
        {
          "narration": str,           # narrator text
          "dialogue": {"Name": "line"} or {},
          "setting": str,             # txt2img visual prompt
          "shot": str,                # img2video motion prompt
        }
      ]
    }
"""

from __future__ import annotations

import datetime
import re

from pipeline import aspect as aspect_lib
from pipeline.narration import CLONE_VOICE_PREFIX

from . import llm as llm_service
from . import gemini as gemini_service

# ---------------------------------------------------------------- templates

_TEMPLATES = {
    "cinematic": {
        "title": "{idea}",
        "act": [
            "Our story opens as a quiet morning gives way to something unexpected. {idea}",
            "A chance discovery raises the stakes, and the journey truly begins.",
            "Tension builds as old habits collide with a new resolve.",
            "Everything comes down to one impossible choice — and one brave step.",
        ],
    },
    "anime": {
        "title": "{idea}",
        "act": [
            "In a world where lights flicker with secrets, a promise awakens: {idea}",
            "A rival appears, forcing our hero to train harder than ever.",
            "An old legend resurfaces, and nothing will ever be the same.",
            "With friends at their side, the final battle unleashes a power no one expected.",
        ],
    },
    "comedy": {
        "title": "{idea}",
        "act": [
            "It all started as a perfectly normal day, until {idea}",
            "One small misunderstanding snowballs into total chaos.",
            "Just when things calm down, the biggest surprise yet shows up.",
            "In the end, everyone learns the lesson the hard way — and laughs anyway.",
        ],
    },
    "horror": {
        "title": "{idea}",
        "act": [
            "Night falls, and something in the dark is drawn to {idea}",
            "A shadow moves where no one is standing. It's getting closer.",
            "The truth is worse than any rumor — and there's no one to call.",
            "The silence breaks, and only one thing remains standing.",
        ],
    },
    "sci-fi": {
        "title": "{idea}",
        "act": [
            "Decades from now, the answer to an old signal lies in {idea}",
            "An anomaly defies every law of physics they were taught.",
            "The crew must decide who to trust before the reactor dial burns out.",
            "Against all odds, a lone signal answers — and a new frontier opens.",
        ],
    },
    "fantasy": {
        "title": "{idea}",
        "act": [
            "Long ago, a prophecy whispered about {idea}",
            "A hidden artifact awakens, and allies rally from every valley.",
            "The dark one marches, and hope rides on a single flame.",
            "With the realm healed, the broken sword stands proud again.",
        ],
    },
    "sports": {
        "title": "{idea}",
        "act": [
            "Every underdog story begins the same way — with {idea}",
            "Months of sweat pay off as the season's biggest test arrives.",
            "Down, but never out, the team digs deeper than anyone believed.",
            "The final whistle blows, and a dynasty is born.",
        ],
    },
    "documentary": {
        "title": "{idea}",
        "act": [
            "To understand {idea}, we start where it all began.",
            "The experts share what the archives have kept hidden.",
            "A turning point reshapes everything we assumed.",
            "Today, the legacy lives on — and the story is far from over.",
        ],
    },
}

_DIALOGUE = [
    ("Is that really it?", "That's the question everybody keeps asking."),
    ("I don't believe this is happening.", "Believe it. We're already in the middle of it."),
    ("What do we do now?", "We do exactly what we came here to do."),
    ("It's not over yet.", "No — this is where it all comes together."),
]

_DEFAULT_CHARACTERS = [
    {"name": "Ava", "description": "a determined, warm-hearted protagonist"},
    {"name": "Kai", "description": "a sharp-witted problem-solver with a hidden past"},
    {"name": "Mara", "description": "the cautious voice of reason"},
]

_SETTINGS = [
    "a rain-swept harbor at dusk with strings of warm lights",
    "a sunlit courtyard framed by towering old arches",
    "a neon city street dissolving into fog after midnight",
    "a windswept ridge above a quiet valley at golden hour",
    "a cluttered workshop glowing with amber lamps and strange tools",
    "an empty train platform where a single bird waits",
]

_DEFAULT_HOSTS = [
    {"name": "Alex", "description": "a charismatic podcast host in a modern studio"},
    {"name": "Sam", "description": "a thoughtful co-host who loves a good debate"},
]

_PODCAST_TURNS = [
    ("opener", "Welcome back to the show. Today we're unpacking {idea} — and honestly, this one has been on my mind all week."),
    ("agree", "What surprises me most is how central {idea} has become to how we all live now."),
    ("question", "But here's what I keep coming back to: where should someone even start?"),
    ("counter", "I'd push back on that a little — the real story is in the details nobody talks about."),
    ("insight", "That's exactly the point though. Once you see it clearly, {idea} changes your perspective completely."),
    ("close", "Great conversation, as always. If you enjoyed this dive into {idea}, share it with a friend."),
]


def _template_podcast(topic: str, hosts: list, turn_count: int, aspect: str = "9:16") -> dict:
    frame = aspect_lib.prompt_phrase(aspect) or "9:16 vertical frame"
    topic = _clean(topic) or "a topic everyone is talking about"
    if hosts:
        named = [h for h in hosts if (h.get("name") or "").strip()]
        hlist = [{"name": h["name"].strip(), "description": _clean(h.get("description")) or f"a podcast host named {h['name'].strip()}"} for h in named]
    else:
        hlist = [dict(h) for h in _DEFAULT_HOSTS]
    if len(hlist) < 2:
        hlist.append({**dict(_DEFAULT_HOSTS[1]), "name": "Sam"})

    total = max(2, min(int(turn_count or 8), 12))
    turns = []
    for i in range(total):
        speaker = hlist[i % len(hlist)]
        kind, line = _cyc(_PODCAST_TURNS, i)
        title_cased = topic.title() if i == 0 else topic
        line = line.replace("{idea}", title_cased)
        line = line[:1].upper() + line[1:]
        turns.append(
            {
                "speaker": speaker["name"],
                "line": line,
                "beat": kind,
                "visual": f"close-up of {speaker['description']}, professional podcast studio, soft key light, shallow depth of field, {frame}",
            }
        )
    return {
        "title": f"{topic.title()} — Podcast",
        "style": "podcast",
        "characters": hlist,
        "turns": turns,
    }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _cyc(seq, i):
    return seq[i % len(seq)]


def _template_script(idea: str, style: str, scene_count: int, characters: list, aspect: str = "9:16") -> dict:
    style = style if style in _TEMPLATES else "cinematic"
    tpl = _TEMPLATES[style]
    idea = _clean(idea) or "an ordinary day turning extraordinary"
    lower = idea.lower()

    if characters:
        named = [c for c in characters if (c.get("name") or "").strip()]
        chars = [{"name": c["name"].strip(), "description": _clean(c.get("description")) or f"the character of {c['name'].strip()}"} for c in named]
    else:
        chars = [dict(c) for c in _DEFAULT_CHARACTERS]

    scenes = []
    total = max(2, min(int(scene_count), 16))
    acts = tpl["act"]
    for i in range(total):
        narration = acts[i % len(acts)].replace(
            "{idea}", idea.title() if i == 0 else idea
        )
        narration = narration[:1].upper() + narration[1:]
        hero = chars[i % len(chars)] if chars else None
        dialogue = {}
        if chars and i % 2 == 0:
            speaker = chars[(i + 1) % len(chars)]
            line_a, line_b = _cyc(_DIALOGUE, i)
            dialogue[speaker["name"]] = line_b if "?" in line_a else line_a
            dialogue[hero["name"]] = line_a if "?" in line_a else line_b
        setting = _cyc(_SETTINGS, i)
        hero_desc = " " + hero["description"] if hero else ""
        motion = {
            "opening": "A slow push-in as daylight drifts across the scene",
            "mid": "A measured dolly shot tracking motion through the frame",
            "energetic": "A gentle crane up lifting the mood of the frame",
        }
        scenes.append(
            {
                "narration": narration,
                "dialogue": dialogue,
                "setting": f"{setting}{hero_desc}, {style} look, cinematic lighting, highly detailed, {aspect_lib.prompt_phrase(aspect)}".strip(),
                "shot": _cyc(list(motion.values()), i),
            }
        )
    return {
        "title": tpl["title"].replace("{idea}", idea.title()).title(),
        "style": style,
        "characters": chars,
        "scenes": scenes,
    }


# ---------------------------------------------------------------- LLM path

_SYSTEM = "You are a professional screenwriter. You turn one-line ideas into short film structures. Always answer with ONLY raw JSON, no markdown, no commentary."

_SCHEMA_GUIDE = """
Return JSON with this exact shape:
{
  "title": "string, short film title",
  "characters": [{"name": "string (short, 1-2 words)", "description": "string, visual description for an AI image generator (appearance, clothes, age)"}],
  "scenes": [
    {
      "narration": "string, 1-3 sentences of narrator voice-over",
      "dialogue": {"CharacterName": "one spoken line"} ,  (empty object if the scene has no dialogue; use only character names listed above)
      "setting": "string, rich visual prompt for an AI IMAGE generator: location, objects, time of day, lighting, with 'cinematic, "
               + aspect_lib.prompt_phrase(aspect) + "'",
      "shot": "string, short camera-motion prompt for an AI VIDEO generator (1 sentence, e.g. 'slow push-in', 'pan across')"
    }
  ]
}
Rules:
- Do not invent new character names in dialogue chains; characters can appear in narration.
- Whenever a character you listed appears on screen in a scene, spell their exact name in the narration (and in `setting` if their description alone wouldn't identify them), so the same character renders consistently in every scene.
- Every scene must have a narration; dialogue is optional.
- The film should have a satisfying arc: setup, rising action, climax, resolution.
- Scenes are sequential; each is a different location or time.
"""


def _llm_script(idea: str, style: str, scene_count: int, characters: list, settings, aspect: str = "9:16") -> dict:
    style = style if style in _TEMPLATES else "cinematic"
    char_brief = (
        "Characters the user supplied:\n"
        + "\n".join(f"- {c.get('name', '')}: {c.get('description', '')}" for c in (characters or []) if c.get("name"))
        if characters
        else "Create 2-4 unique characters yourself."
    )
    user = (
        f"Film idea: {idea}\n"
        f"Style: {style}\n"
        f"Number of scenes: {scene_count}\n"
        f"{char_brief}\n"
        "Write the film now."
    )
    raw = llm_service.chat_json(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        _SYSTEM + "\n" + _SCHEMA_GUIDE,
        user,
        timeout=settings.llm_timeout_seconds,
    )
    return _normalize_outline(raw, idea, style, scene_count, characters, aspect)


def _llm_script_gemini(idea: str, style: str, scene_count: int, characters: list, settings, use_search: bool, aspect: str = "9:16") -> tuple:
    """Gemini film writer. With `use_search` the model gets live Google Search
    grounding for factual accuracy (scores, records, current events). Returns
    ``(outline, sources)``."""
    style = style if style in _TEMPLATES else "cinematic"
    char_brief = (
        "Characters the user supplied:\n"
        + "\n".join(f"- {c.get('name', '')}: {c.get('description', '')}" for c in (characters or []) if c.get("name"))
        if characters
        else "Create 2-4 unique characters yourself."
    )
    user = (
        f"Film idea: {idea}\n"
        f"Style: {style}\n"
        f"Number of scenes: {scene_count}\n"
        f"{char_brief}\n"
        + ("Use Google Search to verify real facts, dates, scores and names. "
           "Never invent statistics; prefer verified details.\n" if use_search else "")
        + "Write the film now."
    )
    raw, sources = gemini_service.gemini_json(
        settings.gemini_api_key,
        settings.gemini_model,
        settings.gemini_base_url,
        _SYSTEM + "\n" + _SCHEMA_GUIDE,
        user,
        timeout=settings.gemini_timeout_seconds,
        use_search=use_search,
    )
    outline = _normalize_outline(raw, idea, style, scene_count, characters, aspect)
    if sources:
        outline["sources"] = sources
    return outline, sources


def _normalize_outline(raw: dict, idea: str, style: str, scene_count: int, characters: list, aspect: str = "9:16") -> dict:
    title = _clean(raw.get("title"))[:120] or _clean(idea).title()
    chars = []
    seen_names: set[str] = set()
    for c in raw.get("characters") or []:
        name = _clean(c.get("name"))[:40]
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            chars.append({"name": name, "description": _clean(c.get("description"))})
    if not chars:
        raise ValueError("LLM returned no characters")

    scenes = []
    for sc in (raw.get("scenes") or [])[: int(scene_count)]:
        narration = _clean(sc.get("narration"))
        if not narration:
            raise ValueError("LLM returned a scene with no narration")
        dialogue = {}
        for k, v in (sc.get("dialogue") or {}).items():
            k = _clean(str(k))
            v = _clean(str(v))
            if k and v and k.lower() in {c["name"].lower() for c in chars}:
                dialogue[k] = v
        scenes.append(
            {
                "narration": narration,
                "dialogue": dialogue,
                "setting": _clean(sc.get("setting")) or f"{idea}, {style} cinematic scene",
                "shot": _clean(sc.get("shot")) or "a slow push-in",
            }
        )
    if not scenes:
        raise ValueError("LLM returned no scenes")
    return {
        "title": title,
        "style": style,
        "characters": chars,
        "scenes": scenes,
    }


# ---------------------------------------------------------------- podcast

_PODCAST_SYSTEM = "You are a professional podcast producer. You turn a topic into a two-host podcast outline. Always answer with ONLY raw JSON, no markdown, no commentary."

_PODCAST_SCHEMA = """
Return JSON with this exact shape:
{
  "title": "string, short podcast episode title",
  "hosts": [{"name": "string (1-2 words)", "description": "string, visual description for an AI image generator (appearance, clothes)"}],
  "turns": [
    {
      "speaker": "string, one of the host names",
      "line": "string, exactly one spoken line for that host",
      "shot": "string, short camera-motion prompt (1 sentence)"
    }
  ]
}
Rules:
- Use exactly the hosts you listed; only their names may appear as speakers.
- Turns alternate between hosts naturally; the episode has an arc: hook, development, back-and-forth, payoff, outro.
- Each line is conversational, casual, and specific to the topic.
"""


def _podcast_llm(topic: str, hosts: list, turn_count: int, settings, aspect: str = "9:16") -> dict:
    host_brief = (
        "Hosts the user supplied:\n"
        + "\n".join(f"- {h.get('name', '')}: {h.get('description', '')}" for h in (hosts or []) if h.get("name"))
        if hosts
        else "Create exactly 2 hosts yourself."
    )
    user = (
        f"Podcast topic: {topic}\n"
        f"Number of turns: {turn_count}\n"
        f"{host_brief}\n"
        "Write the podcast outline now."
    )
    raw = llm_service.chat_json(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        _PODCAST_SYSTEM + "\n" + _PODCAST_SCHEMA,
        user,
        timeout=settings.llm_timeout_seconds,
    )
    return _normalize_podcast_outline(raw, topic, hosts, turn_count, aspect)


def _podcast_gemini(topic: str, hosts: list, turn_count: int, settings, use_search: bool, aspect: str = "9:16") -> tuple:
    host_brief = (
        "Hosts the user supplied:\n"
        + "\n".join(f"- {h.get('name', '')}: {h.get('description', '')}" for h in (hosts or []) if h.get("name"))
        if hosts
        else "Create exactly 2 hosts yourself."
    )
    user = (
        f"Podcast topic: {topic}\n"
        f"Number of turns: {turn_count}\n"
        f"{host_brief}\n"
        + ("Use Google Search to verify real facts, dates, scores and names. "
           "Never invent statistics; prefer verified details.\n" if use_search else "")
        + "Write the podcast outline now."
    )
    raw, sources = gemini_service.gemini_json(
        settings.gemini_api_key,
        settings.gemini_model,
        settings.gemini_base_url,
        _PODCAST_SYSTEM + "\n" + _PODCAST_SCHEMA,
        user,
        timeout=settings.gemini_timeout_seconds,
        use_search=use_search,
    )
    outline = _normalize_podcast_outline(raw, topic, hosts, turn_count, aspect)
    if sources:
        outline["sources"] = sources
    return outline, sources


def _normalize_podcast_outline(raw: dict, topic: str, hosts: list, turn_count: int, aspect: str = "9:16") -> dict:
    title = _clean(raw.get("title"))[:120] or _clean(topic).title()
    hlist = []
    seen: set[str] = set()
    for h in raw.get("hosts") or []:
        name = _clean(h.get("name"))[:40]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            hlist.append({"name": name, "description": _clean(h.get("description"))})
    if len(hlist) < 2:
        raise ValueError("LLM returned fewer than 2 hosts")
    names = {h["name"].lower() for h in hlist}

    turns = []
    for t in (raw.get("turns") or [])[: int(turn_count)]:
        speaker = _clean(t.get("speaker"))
        line = _clean(t.get("line"))
        if speaker.lower() not in names or not line:
            continue
        turns.append(
            {
                "speaker": speaker,
                "line": line,
                "beat": "turn",
                "visual": f"close-up of {next(h['description'] for h in hlist if h['name'].lower() == speaker.lower())}, professional podcast studio, soft key light, {aspect_lib.prompt_phrase(aspect)}",
                "shot": _clean(t.get("shot")) or "a subtle slow push-in",
            }
        )
    if not turns:
        raise ValueError("LLM returned no usable turns")
    return {"title": title, "style": "podcast", "characters": hlist, "turns": turns}


def write_podcast_script(topic: str, hosts: list, turn_count: int, settings, research: bool = False, aspect: str = "9:16") -> dict:
    """Write a two-host podcast outline. Gemini-first (optionally grounded in
    live web search), then OpenAI-compatible LLM, then template fallback."""
    turn_count = max(2, min(int(turn_count or 8), int(getattr(settings, "podcast_max_turns", 12))))
    if settings.gemini_configured:
        try:
            outline, _ = _podcast_gemini(topic, hosts, turn_count, settings, use_search=bool(research), aspect=aspect)
            return outline
        except Exception as exc:  # noqa: BLE001 — fall back as configured
            print(f"[podcast] Gemini scriptwriting failed ({exc}); trying next writer")
    if settings.llm_configured:
        try:
            return _podcast_llm(topic, hosts, turn_count, settings, aspect)
        except Exception as exc:  # noqa: BLE001 — fall back to templates
            print(f"[podcast] LLM scriptwriting failed ({exc}); using template writer")
    return _template_podcast(topic, hosts, turn_count, aspect)


# ---------------------------------------------------------------- public


def write_film_script(
    idea: str,
    style: str,
    scene_count: int,
    characters: list,
    settings,
    research: bool = False,
    aspect: str = "9:16",
) -> dict:
    """Write a full film structure. Gemini-first (optionally grounded in live
    web search), then OpenAI-compatible LLM, then template fallback."""
    scene_count = max(2, min(int(scene_count or 8), int(getattr(settings, "film_max_scenes", 16))))
    if settings.gemini_configured:
        try:
            outline, _ = _llm_script_gemini(idea, style, scene_count, characters, settings, use_search=bool(research), aspect=aspect)
            return outline
        except Exception as exc:  # noqa: BLE001 — fall back as configured
            print(f"[film] Gemini scriptwriting failed ({exc}); trying next writer")
    if settings.llm_configured:
        try:
            return _llm_script(idea, style, scene_count, characters, settings, aspect)
        except Exception as exc:  # noqa: BLE001 — fall back to templates
            print(f"[film] LLM scriptwriting failed ({exc}); using template writer")
    return _template_script(idea, style, scene_count, characters, aspect)


# ---------------------------------------------------------------- hooks

# {shape} is filled in per request from the chosen aspect ratio, so a 16:9 film
# is never asked for a "short vertical" opening.
_HOOK_SYSTEM = (
    "You write the on-screen opening hook for a short {shape} social video. "
    "It is the only thing on screen in the first second, so it has to stop the scroll "
    "and say something specific. Always answer with ONLY raw JSON, no markdown, no commentary."
)

_HOOK_SCHEMA = """
Return JSON with this exact shape:
{"text": "string", "subtext": "string"}
Rules:
- "text" is the hook itself: at most 6 words and 42 characters. State the specific
  stake, tension, surprise or claim the video is about.
- "subtext" is one smaller supporting line (at most 9 words, 60 characters), or ""
  when the hook stands alone.
- Be specific to this subject. A line that would fit any video is a failed hook.
- No emoji, no hashtags, no surrounding quotes, no trailing punctuation.
- No clickbait filler ("you won't believe", "this changes everything").
- Plain ASCII letters, digits and simple punctuation only.
"""

# create_hook draws "text" at fontsize 72 and "subtext" at 42 in a 1080px-wide
# frame, so a long line runs off the edge. These caps keep the card on one line.
_HOOK_TEXT_MAX = 42
_HOOK_SUBTEXT_MAX = 60

# The renderer uppercases, escapes ':' and "'", and drops backslashes, so those
# are safe to keep. Emoji and curly quotes turn into tofu boxes and newlines or
# tabs would break the single-line drawtext filter, so everything else folds to
# a space.
_HOOK_UNSAFE = re.compile(r"[^A-Za-z0-9 ,.!?&/':-]+")

# build_hook() only shows hook_text verbatim for "Breaking News"; the other four
# styles wrap it. The prompt tells the model how its line will be placed so the
# result still reads as a sentence.
_HOOK_STYLE_NOTES = {
    "Breaking News": (
        'The renderer shows "text" on its own line, unchanged. Write it as a complete '
        "standalone line that works with no other words around it."
    ),
    "Question": (
        'The renderer prints a fixed "IS THIS THE NEXT BIG THING?" as the main line and puts '
        'your "text" underneath it. Write "text" as a short, punchy question with no question mark.'
    ),
    "Bold Statement": (
        'The renderer appends " IS READY." to your "text" to make the main line. Write "text" as a '
        'short noun phrase that reads correctly in front of that, e.g. "THE TURNAROUND" -> "THE '
        'TURNAROUND IS READY."'
    ),
    "Debate": (
        'The renderer prefixes your "text" with "LET\'S TALK ABOUT " to make the main line. Write '
        '"text" as a short topic noun phrase, e.g. "JORDAN LEE\'S EARLY FIT".'
    ),
    "Game Day": (
        'The renderer prints a fixed "GAMECOCKS FANS, WATCH THIS" as the main line and puts your '
        '"text" underneath it. Write "text" as a short punchy claim with no trailing punctuation.'
    ),
}


def _clean_hook_line(value, limit: int) -> str:
    """Make an LLM hook line safe for drawtext and short enough to fit the card."""
    text = _HOOK_UNSAFE.sub(" ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" -:,.!?&/")
    if len(text) > limit:
        # Cut on a word boundary so the line never ends mid-word.
        text = text[:limit].rsplit(" ", 1)[0].strip(" -:,.!?&/")
    return text.upper()


def write_hook(
    idea: str,
    style: str,
    title: str,
    settings,
    hook_style: str = "Breaking News",
    research: bool = False,
    aspect: str = "9:16",
) -> dict:
    """Write the on-screen opening hook for a film or podcast episode.

    The film/podcast defaults ship a title card with empty text, which renders a
    blank frame for the first 1.5s. This fills it in from the finished outline.

    Returns ``{"text", "subtext"}`` ready for ``hook_text`` / ``hook_subtext``, or
    ``{}`` when no writer is configured or both writers failed — the caller then
    leaves the hook text alone rather than rendering something half-written.
    """
    subject = (idea or title or "").strip()
    if not subject:
        return {}
    note = _HOOK_STYLE_NOTES.get(hook_style, _HOOK_STYLE_NOTES["Breaking News"])
    user = (
        f"Video subject: {subject}\n"
        f"Style: {style or 'cinematic'}\n"
        + (f"Working title: {title}\n" if title else "")
        + f"Placement: {note}\n"
        "Write the hook now."
    )
    shape = (aspect_lib.prompt_phrase(aspect).split(" frame")[0] or "9:16 vertical")
    system = _HOOK_SYSTEM.format(shape=shape) + "\n" + _HOOK_SCHEMA
    raw = None
    if settings.gemini_configured:
        try:
            raw, _ = gemini_service.gemini_json(
                settings.gemini_api_key,
                settings.gemini_model,
                settings.gemini_base_url,
                system,
                user,
                timeout=settings.gemini_timeout_seconds,
                use_search=bool(research),
            )
        except Exception as exc:  # noqa: BLE001 — fall back as configured
            print(f"[hook] Gemini hook failed ({exc}); trying next writer")
    if raw is None and settings.llm_configured:
        try:
            raw = llm_service.chat_json(
                settings.llm_base_url,
                settings.llm_api_key,
                settings.llm_model,
                system,
                user,
                timeout=settings.llm_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — no hook is better than a bad one
            print(f"[hook] LLM hook failed ({exc}); rendering without hook text")
    if not isinstance(raw, dict):
        return {}
    text = _clean_hook_line(raw.get("text"), _HOOK_TEXT_MAX)
    if not text:
        return {}
    return {"text": text, "subtext": _clean_hook_line(raw.get("subtext"), _HOOK_SUBTEXT_MAX)}


def assign_voices(outline: dict, available: list) -> dict:
    """Deterministically assign Piper voices so characters sound distinct."""
    available = sorted(a for a in (available or []) if a)
    if not available:
        raise ValueError("No Piper voices available on the server.")
    import copy

    out = copy.deepcopy(outline)
    chars = out.get("characters") or []
    for i, c in enumerate(chars):
        c["voice"] = available[i % len(available)]
    out["characters"] = chars
    return out


def ensure_voices(outline: dict, available: list, keep: bool = True) -> dict:
    """Fill in voices only where the package did not already specify one.

    Unlike `assign_voices` (which overwrites everything) this preserves voices
    the client wrote, so package-produced films keep their intended cast.
    """
    available = sorted(a for a in (available or []) if a)
    if not available:
        raise ValueError("No Piper voices available on the server.")
    import copy

    out = copy.deepcopy(outline)
    chars = out.get("characters") or []
    available_set = set(available)
    next_i = 0
    for c in chars:
        voice = c.get("voice", "")
        # A cloned voice ("clone:<path>") is an explicit user choice like a
        # Piper name — never overwrite it with an auto-assigned voice.
        if isinstance(voice, str) and voice.startswith(CLONE_VOICE_PREFIX):
            continue
        if not keep or not voice or voice not in available_set:
            c["voice"] = available[next_i % len(available)]
            next_i += 1
    out["characters"] = chars
    return out
"""Internet-grounded image research — an automatic capability, not a mode.

When a text-to-image or image-to-image request names a real, documented subject,
the backend researches it before rendering:

  1. Gemini + Google Search grounding reads the text and identifies the subject
     (a person like Dawn Staley, a team, a brand, a place), plus a precise
     Wikimedia query, a visual scene and a design brief carrying that subject's
     real colours, symbols and era.
  2. Wikimedia Commons (free API, no key) returns a real photo of the subject.
     We download it and re-host it in the user's own storage as a signed `ref`
     with role "reference" — exactly the transport the RunPod worker fetches.
  3. If a photo was found the run renders through img2img (low denoise), so the
     graphic is generated but still looks like the real person.
  4. If NO usable photo exists the run still renders — through txt2img — with the
     researched identity and palette in the prompt, producing a *fictional*
     figure that reads as the subject rather than dropping them.

So the research pays off with or without a photograph: the brief is what makes
the no-photo fallback specific (Gamecocks garnet and black) instead of generic.

Failures never block: no GEMINI key, an abstract/fictional subject, a missed
search, or a download error all degrade to plain text-to-image.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import gemini as gemini_service

_WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "Vidyra/0.1 (internal image reference resolver; local app)"
_MAX_REF_BYTES = 8 * 1024 * 1024

# A scene "looks like" a real named subject only when it names something with
# real text-worthy capitalization (a person, team, brand, place).
_NAME_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z0-9'\-]+(?:\s+[A-Z][a-zA-Z0-9'\-]+){1,})\b"
)

_BRIEF_SYSTEM = (
    "You are a researcher helping an AI *image* tool make fitting graphics for an article. "
    "Read the article text and decide whether it is about a REAL, publicly documented "
    "person / team / brand / place that has reputable photos (e.g. a famous athlete, coach, "
    "university, company). For abstract or fictional subjects, set real_subject to false. "
    "Answer with ONLY one JSON object (no prose, no fences): "
    '{"real_subject": true or false, "subject": "the exact name", '
    '"role": "one short line about who/what they are", '
    '"visual_brief": "a short image-prompt describing the subject and a fitting visual scene", '
    '"design": "the factual visual identity a designer must use: the exact colours with their '
    'names (e.g. garnet and black for South Carolina Gamecocks), the era and setting, the '
    'symbols or objects that read as this subject, and the mood a fitting graphic conveys", '
    '"wiki_query": "the precise name to search on Wikimedia Commons", '
    '"keywords": ["face", "jersey", "arena", "venue", "uniform", "city"]}. '
    "Use Google Search to confirm the subject really exists and current facts. "
    "Keep every string factual and concise."
)


class ImageBriefError(RuntimeError):
    pass


def _http_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise ImageBriefError(f"HTTP {exc.code} from image search")
    except urllib.error.URLError as exc:
        raise ImageBriefError(f"image search connection failed: {exc.reason}")


def _http_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read(_MAX_REF_BYTES + 1)
    if len(body) > _MAX_REF_BYTES:
        raise ImageBriefError("reference photo is too large")
    return body


def looks_like_named_subject(text: str) -> bool:
    """Cheap pre-filter so ABSTRCT/fictional scenes never hit Gemini or the web."""
    return bool(_NAME_PATTERN.search(text or ""))


# ---------------------------------------------------------------------------
# Step 1: Gemini research brief
# ---------------------------------------------------------------------------


def _research_question(text: str, settings) -> tuple[dict | None, bool]:
    """Ask Gemini to identify the subject. Returns ``(data, grounded)``.

    Search grounding is preferred: it confirms the subject is real *now* and
    gives us citable sources. But grounding is metered separately from ordinary
    generation and is the first thing to break on a free-tier key (429
    "exceeded your current quota"), which would otherwise disable research
    entirely.

    So when a *grounded* call fails for a retryable reason, ask the same
    question without the tool. The model still recognises well-known subjects
    and still returns the design brief; we lose live-web freshness and
    citations, and the brief is flagged ``grounded: False`` so the UI can say so.

    Permanent failures (401/403 bad key, 404 retired model) are never retried
    without grounding, because dropping the tool cannot fix them.
    """
    user = f"Article text:\n{text[:6000]}"
    kwargs = dict(
        timeout=settings.gemini_timeout_seconds,
    )
    try:
        data, _sources = gemini_service.gemini_json(
            settings.gemini_api_key,
            settings.gemini_model,
            settings.gemini_base_url,
            _BRIEF_SYSTEM,
            user,
            use_search=True,
            **kwargs,
        )
        return data, True
    except Exception as exc:  # noqa: BLE001 — any failure degrades, never blocks
        retryable = getattr(exc, "retryable", False)
        if not retryable:
            return None, False
    try:
        data, _sources = gemini_service.gemini_json(
            settings.gemini_api_key,
            settings.gemini_model,
            settings.gemini_base_url,
            _BRIEF_SYSTEM,
            user,
            use_search=False,
            **kwargs,
        )
        return data, False
    except Exception:
        return None, False


def _gemini_brief(text: str, settings) -> dict | None:
    if not settings.gemini_configured:
        return None
    data, grounded = _research_question(text, settings)
    if not isinstance(data, dict) or data.get("real_subject") is not True:
        return None
    brief = {
        "subject": str(data.get("subject") or "").strip(),
        "role": str(data.get("role") or "").strip(),
        "visual_brief": str(data.get("visual_brief") or "").strip(),
        "design": str(data.get("design") or "").strip(),
        "wiki_query": str(data.get("wiki_query") or "").strip(),
        "keywords": [str(k) for k in (data.get("keywords") or []) if str(k).strip()][:8],
        "grounded": grounded,
    }
    # A brief with neither a scene nor a design gives the renderer nothing to
    # work with, so treat it as no research at all.
    if not brief["wiki_query"] and not (brief["visual_brief"] or brief["design"]):
        return None
    return brief


# ---------------------------------------------------------------------------
# Step 2: Wikimedia Commons real-photo fetch
# ---------------------------------------------------------------------------


def _strip_html(value: str) -> str:
    """Flatten a Commons metadata HTML fragment to readable plain text."""
    import re as _re

    text = _re.sub(r"<[^>]+>", " ", str(value or ""))
    text = (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return _re.sub(r"\s+", " ", text).strip()


def _wikimedia_candidates(query: str, timeout: int = 30) -> list[dict]:
    """Real photos of the subject from Commons. Returns [{url, title, licence,...}]."""
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        # Namespace 6 is File. Without this the search generator matches
        # discussion/help pages instead of media and returns NOTHING, which
        # looks exactly like "no photo of this subject exists".
        "gsrnamespace": "6",
        "gsrlimit": "6",
        "prop": "imageinfo",
        # `mime` must be requested explicitly or imageinfo omits it and every
        # candidate is then rejected by the mime filter below.
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": "1600",
        "format": "json",
        "origin": "*",
    }
    url = f"{_WIKIMEDIA_API}?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": _USER_AGENT}
    try:
        data = _http_json(url, headers=headers, timeout=timeout)
    except ImageBriefError:
        return []
    pages = (data.get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        info = ((page.get("imageinfo") or [{}])[0]) or {}
        if not info:
            continue
        mime = str(info.get("mime") or "")
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            continue
        direct = info.get("thumburl") or info.get("url")
        if not direct:
            continue
        meta = info.get("extmetadata") or {}
        title = _strip_html(page.get("title") or "").replace("File:", "")
        # imageinfo does not return canonicalurl, so build the Commons file page
        # ourselves -- the attribution link must work for CC attribution.
        wiki_title = str(page.get("title") or "").replace(" ", "_")
        page_url = str(page.get("canonicalurl") or "") or (
            f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(wiki_title, safe=':')}"
        )
        out.append(
            {
                "url": direct,
                "title": title,
                "page_url": page_url,
                # Commons metadata is HTML-wrapped ("<a href=...>Name</a>"), and
                # the UI renders this as plain text next to the run.
                "license": _strip_html(
                    (meta.get("LicenseShortName") or {}).get("value") or "see source"
                ).strip(),
                "author": _strip_html(
                    (meta.get("Artist") or {}).get("value") or ""
                ).replace("&amp;", "&").strip()[:120],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Step 3: download + re-host
# ---------------------------------------------------------------------------

_IMAGE_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}


def _detect_ext(body: bytes) -> str | None:
    for magic, ext in _IMAGE_MAGIC.items():
        if body.startswith(magic):
            return ext
    # WebP (RIFF....WEBP) — fine for ComfyUI LoadImage? Prefer raster-safe ext.
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "webp"
    return None


def _reference_path(uid: str, image_id: str, ext: str) -> str:
    return f"media/{uid}/_refs/{image_id}.{ext}"


def _store_reference(body: bytes, uid: str, storage) -> str:
    """Persist fetched photo into the user's store; returns storage path."""
    ext = _detect_ext(body)
    if not ext:
        raise ImageBriefError("download was not a recognizable image")
    image_id = hashlib.sha1(body).hexdigest()[:20]
    path = _reference_path(uid, image_id, ext)
    if storage.blob_size(path) > 0:
        return path
    tmp = os.path.join(tempfile.gettempdir(), "vidyra-refs")
    os.makedirs(tmp, exist_ok=True)
    local = os.path.join(tmp, f"ref-{image_id}.{ext}")
    with open(local, "wb") as fh:
        fh.write(body)
    storage.publish(local, path)
    try:
        os.remove(local)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ground(
    text: str,
    settings,
    storage,
    uid: str,
    *,
    want_photo: bool = True,
) -> dict | None:
    """Research the text and report what the renderer should know about it.

    Returns None when the text names no real, documented subject (or Gemini is
    unavailable) — the caller then renders exactly as it would have.

    Otherwise the research is returned even when NO photo could be fetched:

        {
          "brief": {"subject", "role", "visual_brief", "design", "wiki_query", "keywords"},
          "ref": {"path": ..., "role": "reference"} | None,
          "credits": {...} | None,
          "sources": [...],
        }

    That distinction is the point. A real subject with no usable Commons photo is
    still worth researching: the brief carries the colours, symbols and mood, so
    the renderer can design a *fictional* figure that reads as that subject
    rather than losing the subject entirely.
    """
    brief = _gemini_brief(text, settings)
    if not brief:
        return None

    result = {"brief": brief, "ref": None, "credits": None, "sources": []}
    if not want_photo or not brief["wiki_query"]:
        return result

    for cand in _wikimedia_candidates(brief["wiki_query"]):
        try:
            body = _http_bytes(cand["url"])
            _detect_ext(body)
        except Exception:
            continue
        try:
            path = _store_reference(body, uid, storage)
        except Exception as exc:  # noqa: BLE001
            # Storage/permission problems must not masquerade as "this subject
            # has no photo", so say so before moving to the next candidate.
            print(f"[imagebrief] could not store reference for {cand.get('title', '')!r}: {exc}")
            continue
        result["ref"] = {"path": path, "role": "reference"}
        result["credits"] = {
            "title": cand.get("title", ""),
            "author": cand.get("author", ""),
            "license": cand.get("license", "see source"),
            "source_url": cand.get("url", ""),
            "page_url": cand.get("page_url", ""),
        }
        break
    return result


def compose_prompt(brief: dict, user_text: str, *, fictional: bool) -> str:
    """Fold the research into the render prompt.

    ``fictional=True`` (research succeeded but no real photo was available)
    asks for an invented figure that still carries the researched visual
    identity — the subject's actual colours and symbols — rather than a
    disconnected new character.
    """
    subject = (brief.get("subject") or "").strip()
    role = (brief.get("role") or "").strip()
    scene = (brief.get("visual_brief") or "").strip()
    design = (brief.get("design") or "").strip()
    user_text = (user_text or "").strip()

    if fictional and subject:
        lead = (
            f"A fictional character representing {subject}"
            + (f", {role}" if role else "")
            + " — not a real photograph of them"
        )
    elif subject:
        lead = subject + (f", {role}" if role else "")
    else:
        # No identified subject: the "fictional stand-in" framing has no referent,
        # so fall back to just the scene and design guidance.
        lead = ""

    # The user's own words lead: they asked for this graphic, the research only
    # supplies the identity and the design language.
    parts = [p for p in (user_text, lead, scene, design) if p]
    out = ". ".join(parts)
    return re.sub(r"\s*\.\s*", ". ", out).strip()


def prepare_reference(text: str, settings, storage, uid: str) -> dict | None:
    """Full online pass. Returns None when nothing referenceable was found.

    A successful result looks like:
        {
          "ref": {"path": "media/<uid>/_refs/<id>.jpg", "role": "reference"},
          "credits": {"title", "author", "license", "source_url", "page_url"},
          "brief": {"subject", "role", "visual_brief", "design", "wiki_query", "keywords"},
          "sources": [{"title", "url"}],   # grounding web pages about the subject
        }

    Kept separate from :func:`ground` because callers that need an actual photo
    (the film worker's scene references) must not be handed a photo-less brief.
    """
    found = ground(text, settings, storage, uid)
    if not found or not found.get("ref"):
        return None
    return {
        "ref": found["ref"],
        "credits": found["credits"],
        "brief": found["brief"],
        "sources": found["sources"],
    }


def try_scene_reference(scene_text: str, settings, storage, uid: str) -> dict | None:
    """Film-worker helper: only attempt the web for scenes that name a real subject."""
    if not looks_like_named_subject(scene_text or ""):
        return None
    return prepare_reference(scene_text or "", settings, storage, uid)
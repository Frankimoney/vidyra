"""Minimal Google Gemini client (plain HTTP, no SDK).

Used by the film/podcast writers for internet-grounded scriptwriting: when
`use_search` is set we enable Google Search grounding so live facts (scores,
records, current events) are available to the model, and the response's
`groundingMetadata` is surfaced as `sources` (titles + URLs) for the job.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from . import llm as llm_service


class GeminiError(RuntimeError):
    """A Gemini API failure.

    Carries the HTTP `code` and Google `status` so callers can react to a
    *retryable* problem (429 quota, 503 capacity) differently from a permanent
    one (401/403 bad key, 404 retired model) instead of matching on message text.
    """

    def __init__(self, message: str, code: int | None = None, status: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.status = status or ""

    @property
    def retryable(self) -> bool:
        """True when the same call could plausibly succeed moments later."""
        return self.code in (429, 500, 502, 503, 504) or self.status in (
            "RESOURCE_EXHAUSTED",
            "UNAVAILABLE",
            "INTERNAL",
            "DEADLINE_EXCEEDED",
        )


def _contents(messages) -> list:
    """Map a stored chat thread onto Gemini `contents` (assistant -> "model")."""
    out: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", "") or "").strip()
        if not content:
            continue
        role = str(m.get("role", "user") or "user").lower()
        out.append(
            {
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": content}],
            }
        )
    return out


def _request(
    api_key: str,
    model: str,
    base_url: str,
    system: str,
    contents: list,
    use_search: bool,
    timeout: int,
):
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent?key={api_key}"
    body: dict = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7,
        },
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:400]
        except Exception:
            pass
        # Google's JSON error body carries the canonical status; prefer it over
        # the HTTP code alone.
        status = ""
        try:
            status = str(json.loads(detail).get("error", {}).get("status", "") or "")
        except Exception:
            pass
        raise GeminiError(
            f"Gemini API {exc.code}: {detail or exc.reason}",
            code=exc.code,
            status=status,
        )
    except urllib.error.URLError as exc:
        raise GeminiError(f"Gemini connection failed: {exc.reason}")


def _extract_sources(result: dict) -> list:
    """Pull web-search grounding sources from a generateContent response."""
    try:
        meta = (result.get("candidates") or [{}])[0].get("groundingMetadata") or {}
    except (AttributeError, IndexError):
        return []
    out = []
    seen = set()
    for chunk in meta.get("groundingChunks") or []:
        web = (chunk or {}).get("web") or {}
        uri = web.get("uri")
        if uri and uri not in seen:
            seen.add(uri)
            out.append({"title": web.get("title") or "", "url": uri})
    return out


def _extract_json_text(result: dict) -> str:
    try:
        parts = (result["candidates"][0]["content"]["parts"] or [{}])
    except (KeyError, IndexError, TypeError):
        raise GeminiError(f"Unexpected Gemini response shape: {str(result)[:300]}")
    return "".join(str(p.get("text", "")) for p in parts)


def gemini_json_history(
    api_key: str,
    model: str,
    base_url: str,
    system: str,
    messages: list,
    timeout: int = 90,
    use_search: bool = False,
    temperature: float = 0.7,
) -> tuple[dict, list]:
    """Multi-turn variant of :func:`gemini_json` for the planning agent.

    `messages` is the whole conversation so far as
    ``[{"role": "user"|"assistant", "content": str}, ...]``, oldest first.
    """
    result = _request(
        api_key, model, base_url, system, _contents(messages), use_search, timeout
    )
    outline = llm_service._extract_json(_extract_json_text(result))
    sources = _extract_sources(result) if use_search else []
    return outline, sources


def gemini_json(
    api_key: str,
    model: str,
    base_url: str,
    system: str,
    user: str,
    timeout: int = 90,
    use_search: bool = True,
) -> tuple[dict, list]:
    """Send a generateContent request and parse the reply as a JSON object.

    Returns ``(outline_json, sources)`` where sources are the Google Search
    grounding references (empty if search was disabled or unavailable).
    """
    result = _request(
        api_key,
        model,
        base_url,
        system,
        [{"role": "user", "parts": [{"text": user}]}],
        use_search,
        timeout,
    )
    outline = llm_service._extract_json(_extract_json_text(result))
    sources = _extract_sources(result) if use_search else []
    return outline, sources
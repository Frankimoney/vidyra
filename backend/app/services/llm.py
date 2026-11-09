"""Minimal OpenAI-compatible chat client (HTTP, no SDK dependency).

Used by the Film generator to write story outlines. Any provider that speaks
the OpenAI /chat/completions protocol works: OpenAI, OpenRouter, Groq, a local
Ollama/LM Studio endpoint, etc.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


def _request(base_url: str, api_key: str, payload: dict, timeout: int):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:300]
        except Exception:
            pass
        raise LLMError(f"LLM API {exc.code}: {body or exc.reason}")
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM connection failed: {exc.reason}")


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of an LLM response, tolerating fences/preamble."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text).rstrip("`").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM did not return valid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise LLMError("LLM response JSON must be an object.")
    return parsed


def _reply_json(result: dict) -> dict:
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Unexpected LLM response shape: {str(result)[:300]}")
    return _extract_json(content)


def _history_messages(messages) -> list:
    """Normalize a stored chat thread into API `messages`, dropping blanks."""
    history: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content", "") or "").strip()
        if not content:
            continue
        role = str(m.get("role", "user") or "user").lower()
        history.append(
            {"role": "assistant" if role == "assistant" else "user", "content": content}
        )
    return history


def chat_json_history(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    messages: list,
    timeout: int = 45,
    temperature: float = 0.8,
) -> dict:
    """Multi-turn variant of :func:`chat_json`.

    `messages` is the whole conversation so far as
    ``[{"role": "user"|"assistant", "content": str}, ...]``, oldest first. The
    system prompt is prepended. Used by the planning agent, whose value comes
    from remembering earlier turns rather than from one-shot prompting.
    """
    result = _request(
        base_url,
        api_key,
        {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                *_history_messages(messages),
            ],
        },
        timeout,
    )
    return _reply_json(result)


def chat_json(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    timeout: int = 45,
) -> dict:
    """Send a chat request and parse the assistant's reply as a JSON object."""
    result = _request(
        base_url,
        api_key,
        {
            "model": model,
            "temperature": 0.8,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout,
    )
    return _reply_json(result)
"""Director's fact-check pass.

Before filming a client-supplied production package (which may contain real
names, scores and records), the director verifies every narration sentence
against live Google Search via Gemini, fixes clear factual errors, and keeps
the grounding sources so the job shows where the facts came from.

Runs only when:
  * GEMINI_API_KEY is configured, and
  * the user left "Research online" on for the film.

No key or a Gemini error never blocks a job — the pass just reports
"not checked" and filming proceeds with the client's text as written.
"""

from __future__ import annotations

from . import gemini as gemini_service

_SYSTEM = (
    "You are a meticulous fact-checker for a video narrator's voice-over. "
    "Each sentence may contain checkable factual claims (dates, scores, records, "
    "names, championships, history). Use Google Search to verify them against "
    "reliable, current sources. Never invent statistics; trust the search results. "
    'Answer with ONLY one JSON object, exact shape (no prose, no fences): '
    '{"sentences":[{"text":"the original sentence", "verdict": true or false, '
    '"correction":"the corrected sentence worded for narration, verbatim original when '
    'the claim was fine or cannot be verified safely", "note":"one short reason"}]} '
    "Keep the same order and the same number of entries as the input. "
    "If in doubt (unverifiable or vague), set verdict true and keep the original text."
)


def fact_check_outline(outline: dict, settings) -> dict:
    """Verify narration sentences in ``outline.scenes`` via grounded Gemini.

    Returns a report dict and, when a sentence was clearly wrong with a usable
    correction, rewrites that scene's narration in place:

        {"checked": bool, "sources": [{"title", "url"}], "corrected": int,
         "notes": [str], "error": str | None}
    """
    report = {"checked": False, "sources": [], "corrected": 0, "notes": [], "error": None}
    if not settings.gemini_configured:
        return report

    scenes = outline.get("scenes") or []
    numbered: list = []
    for i, sc in enumerate(scenes):
        text = (sc.get("narration") or "").strip()
        if text:
            numbered.append({"index": i, "text": text})
    if not numbered:
        return report

    user = "Verify each sentence listed below. Numbered sentences:\n" + "\n".join(
        f"{k + 1}. {entry['text']}" for k, entry in enumerate(numbered)
    )
    try:
        data, sources = gemini_service.gemini_json(
            settings.gemini_api_key,
            settings.gemini_model,
            settings.gemini_base_url,
            _SYSTEM,
            user,
            timeout=settings.gemini_timeout_seconds,
            use_search=True,
        )
    except Exception as exc:  # noqa: BLE001 — research is best-effort
        report["error"] = f"fact-check unavailable: {exc}"
        return report

    # Gemini is loose about returning "exactly one JSON object": a list, a bare
    # string, or a dict without `sentences` would raise AttributeError right
    # here and turn a best-effort pass into a dead job. Any of those shapes is
    # "nothing usable", which by design means leave the text exactly as written.
    if not isinstance(data, dict) or not isinstance(sentences := data.get("sentences"), list):
        report["error"] = "fact-check returned an unexpected shape"
        return report

    report["checked"] = True
    report["sources"] = [s for s in sources if isinstance(s, dict) and s.get("url")]
    for k, verdict in enumerate(sentences):
        if k >= len(numbered) or not isinstance(verdict, dict):
            continue
        entry = numbered[k]
        text = str(verdict.get("text") or "")
        note = str(verdict.get("note") or "").strip()
        if isinstance(verdict.get("verdict"), bool) and verdict["verdict"]:
            continue
        correction = str(verdict.get("correction") or "").strip()
        if correction and correction != text:
            sc = scenes[entry["index"]]
            sc["narration"] = correction
            report["corrected"] += 1
            report["notes"].append(
                f"Scene {entry['index'] + 1}: fact corrected — {note}"
                + (f" ({_source_brief(sources)})" if sources else "")
            )
        elif note:
            report["notes"].append(f"Scene {entry['index'] + 1}: {note}")
    return report


def _source_brief(sources: list) -> str:
    for s in sources[:1]:
        return s.get("title") or s.get("url") or ""
    return ""
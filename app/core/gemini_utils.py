"""
Shared Gemini response utilities.
"""

import re as _re

# Matches "thought", "thoughtful", "thoughtfully", "thoughts", etc. — any word
# starting with "thought" followed by whitespace.  Incident 2026-04-21: Gemini
# emitted "thoughtful\n..." and the narrower pattern ^thought[\n\r ]\w missed it.
_THOUGHT_PREFIX_RE = _re.compile(r"^thought\w*[\n\r ]", _re.IGNORECASE)


def extract_text(response) -> str | None:
    """
    Extract non-thinking text from a Gemini response.

    Filters out parts where thought=True (chain-of-thought from thinking models
    like gemini-pro-latest, gemini-2.5-flash). Joins remaining text parts.
    Falls back to response.text when candidates structure is unavailable.
    """
    try:
        candidates = response.candidates or []
        if not candidates:
            return response.text or None
        parts = getattr(candidates[0].content, "parts", None) or []
        texts = [
            p.text
            for p in parts
            if getattr(p, "text", None) and not getattr(p, "thought", False)
        ]
        return "".join(texts).strip() or None
    except Exception:
        return response.text or None


def strip_thought_preamble(text: str) -> str | None:
    """
    Remove a raw 'thought\\n...' preamble that slipped through thought=False filtering.

    Returns None when the entire text appears to be thinking (no HTML or emoji anchor).
    """
    if not _THOUGHT_PREFIX_RE.match(text):
        return text

    anchor = _re.search(r"(<[bBiIaA][\s>]|📊|📰|🔍|📈|✅)", text)
    if not anchor:
        return None
    return text[anchor.start() :].strip() or None

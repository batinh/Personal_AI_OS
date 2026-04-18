"""
News agent persistent memory — stores and retrieves personalization signals
learned from the user's Telegram conversations.

Memory is stored as key-value pairs in news_agent_state (SQLite):
  - "liked_topics"   : JSON list of topics the user wants more of
  - "disliked_topics": JSON list of topics the user wants less of
  - "extra_notes"    : free-text additional preferences
"""

import json
import logging
import threading
from typing import Optional

from google import genai

from app.core.database import get_news_state, set_news_state
from app.core.logging_conf import get_module_logger

_client = genai.Client()

logger = get_module_logger("news")


def load_news_memory(user_id: str) -> dict:
    """
    Load user's news preferences from persistent state.

    Returns dict with keys:
      liked_topics   : list[str]
      disliked_topics: list[str]
      extra_notes    : str
    """
    liked_raw = get_news_state(user_id, "liked_topics")
    disliked_raw = get_news_state(user_id, "disliked_topics")
    notes_raw = get_news_state(user_id, "extra_notes")

    liked: list = []
    disliked: list = []
    if liked_raw:
        try:
            liked = json.loads(liked_raw)
        except json.JSONDecodeError:
            logger.warning("[NEWS-MEMORY] Could not parse liked_topics JSON")
    if disliked_raw:
        try:
            disliked = json.loads(disliked_raw)
        except json.JSONDecodeError:
            logger.warning("[NEWS-MEMORY] Could not parse disliked_topics JSON")

    return {
        "liked_topics": liked,
        "disliked_topics": disliked,
        "extra_notes": notes_raw or "",
    }


def save_news_memory(user_id: str, key: str, value: str) -> None:
    """Persist a single memory key for the news agent."""
    set_news_state(user_id, key, value)


def _merge_topics(existing: list, new_items: list, max_items: int = 20) -> list:
    """Merge new topics into existing list, keeping latest, bounded by max_items."""
    merged = list(existing)
    for item in new_items:
        item = str(item).strip()
        if item and item not in merged:
            merged.append(item)
    return merged[-max_items:]


def extract_and_save_signals(user_id: str, chat_text: str, model: str) -> None:
    """
    Call Gemini to extract preference signals from a conversation turn,
    then merge into persistent memory.

    Runs synchronously — call from a background thread to avoid blocking.

    Args:
        user_id : user identifier
        chat_text: the full exchange (user message + agent reply)
        model   : model ID string
    """
    from app.agents.news.prompts import build_memory_extraction_prompt

    prompt = build_memory_extraction_prompt(chat_text)
    try:
        response = _client.models.generate_content(
            model=model,
            contents=prompt,
        )
        raw = (response.text or "").strip()
        logger.debug(f"[NEWS-MEMORY] Extraction response: {raw[:200]}")

        signals = _parse_extraction(raw)
        if not signals:
            return

        mem = load_news_memory(user_id)

        if signals.get("liked"):
            updated = _merge_topics(mem["liked_topics"], signals["liked"])
            save_news_memory(user_id, "liked_topics", json.dumps(updated, ensure_ascii=False))

        if signals.get("disliked"):
            updated = _merge_topics(mem["disliked_topics"], signals["disliked"])
            save_news_memory(user_id, "disliked_topics", json.dumps(updated, ensure_ascii=False))

        if signals.get("notes"):
            note = signals["notes"].strip()
            if note:
                existing = mem["extra_notes"]
                combined = (existing + "\n" + note).strip()[-500:]
                save_news_memory(user_id, "extra_notes", combined)

        logger.info(f"[NEWS-MEMORY] Updated memory for user {user_id}")
    except Exception as e:
        logger.error(f"[NEWS-MEMORY] Signal extraction failed: {e}")


def _parse_extraction(raw: str) -> Optional[dict]:
    """
    Parse the JSON blob returned by the extraction prompt.
    Returns None if nothing useful was found.
    """
    try:
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def run_extract_in_background(user_id: str, chat_text: str, model: str) -> None:
    """Fire-and-forget: extract memory signals in a daemon thread."""
    t = threading.Thread(
        target=extract_and_save_signals,
        args=(user_id, chat_text, model),
        daemon=True,
    )
    t.start()

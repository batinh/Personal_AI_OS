"""
News Agent orchestrator — LLM-native architecture.

Flow: build prompt → Gemini with google_search grounding → single consolidated Telegram message.

No RSS fetching, no separate scoring call.  One LLM call per briefing session.
Fallback: if grounding fails, send a knowledge-only digest (no crash/skip).

Telegram routing:
  news_agent.telegram_chat_id set  → send to that channel/group
  news_agent.telegram_chat_id empty → fallback to primary TELEGRAM_CHAT_ID
"""
import logging
import os
import pytz
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.agents.news.prompts import build_news_system_instruction, build_session_prompt
from app.agents.news.memory import load_news_memory

logger = logging.getLogger("AI_COACH")
client = genai.Client()

_MAX_TELEGRAM_CHARS = 4000
_DEFAULT_MODEL = "models/gemini-flash-latest"


def _resolve_chat_id(config: dict) -> str | None:
    """
    Resolve Telegram chat ID for news delivery.
    Priority: news_agent.telegram_chat_id (if set) > primary user fallback.
    """
    news_chat_id = config.get("news_agent", {}).get("telegram_chat_id", "").strip()
    if news_chat_id:
        return news_chat_id
    primary = get_primary_user_id()
    return str(primary) if primary else None


def _get_model(config: dict) -> str:
    return config.get("news_agent", {}).get("news_model", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _now_date_str() -> str:
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    return datetime.now(tz).strftime("%d/%m/%Y")


def _call_with_search(model: str, system_inst: str, prompt: str) -> str | None:
    """Call Gemini with google_search grounding. Returns text or None on failure."""
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=2000,
            ),
        )
        # Log whether grounding was actually used
        candidates = response.candidates or []
        grounding_used = any(
            getattr(c, "grounding_metadata", None) is not None
            for c in candidates
        )
        logger.info(f"[NEWS] Grounded call completed. grounding_used={grounding_used}")
        if not grounding_used:
            logger.warning("[NEWS] Gemini did not invoke google_search grounding — response may use training data only.")
        return response.text or None
    except Exception as e:
        logger.warning(f"[NEWS] Grounded search call failed: {e}")
        return None


def _call_knowledge_only(model: str, system_inst: str, prompt: str) -> str | None:
    """Fallback: call Gemini without search grounding (knowledge-only digest)."""
    try:
        fallback_prompt = (
            prompt
            + "\n\n(Lưu ý: tính năng tìm kiếm tạm thời không khả dụng. "
            "Hãy tổng hợp dựa trên kiến thức hiện có của bạn, không cần kèm link nếu không chắc chắn.)"
        )
        response = client.models.generate_content(
            model=model,
            contents=fallback_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                max_output_tokens=2000,
            ),
        )
        return response.text or None
    except Exception as e:
        logger.error(f"[NEWS] Knowledge-only fallback also failed: {e}")
        return None


def generate_news_briefing(config: dict, session: str = "morning") -> None:
    """
    Generate and send a single news briefing via Gemini with google_search grounding.

    Args:
        config : loaded config dict from load_config()
        session: "morning" | "afternoon" | "evening"
    """
    news_cfg = config.get("news_agent", {})
    if not news_cfg.get("enabled", False):
        logger.info("[NEWS] Agent disabled in config. Skipping.")
        return

    chat_id = _resolve_chat_id(config)
    if not chat_id:
        logger.warning("[NEWS] No Telegram chat ID resolved. Skipping.")
        return

    interest_profile = news_cfg.get("interest_profile", {})
    model = _get_model(config)
    user_id = str(get_primary_user_id())
    date_str = _now_date_str()

    memory = load_news_memory(user_id)
    prompt = build_session_prompt(session, interest_profile, date_str, memory)
    system_inst = build_news_system_instruction()

    logger.debug(f"[NEWS] Prompt ({session}) length={len(prompt)}: {prompt[:300]!r}")

    # Primary: grounded search
    reply = _call_with_search(model, system_inst, prompt)

    # Fallback: knowledge-only digest
    if not reply:
        logger.info("[NEWS] Grounded search returned empty — falling back to knowledge-only digest.")
        reply = _call_knowledge_only(model, system_inst, prompt)

    if not reply:
        logger.error(f"[NEWS] Both grounded and fallback calls failed for {session}. Skipping send.")
        return

    if len(reply) > _MAX_TELEGRAM_CHARS:
        reply = reply[:_MAX_TELEGRAM_CHARS] + "..."

    logger.debug(f"[NEWS] Reply length={len(reply)}: {reply[:300]!r}")
    send_telegram_msg(chat_id, reply)
    logger.info(f"[NEWS] Sent {session} briefing to chat_id={chat_id}")

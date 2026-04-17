"""
News Agent orchestrator — LLM-native architecture.

Scheduled flow (parallel per-topic):
  For each topic in config.news_agent.topics, one Gemini call with google_search grounding.
  Calls run in parallel via ThreadPoolExecutor.
  Results merged into a single Telegram message.

On-demand flow:
  generate_on_demand_briefing(query, chat_id, config)
  Called when user sends @news <query> via Telegram.

Fallback (backward compat):
  If topics not in config, derives topics from interest_profile keys.

Telegram routing:
  news_agent.telegram_chat_id set  → send to that channel/group
  news_agent.telegram_chat_id empty → fallback to primary TELEGRAM_CHAT_ID
"""
import logging
import os
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.agents.news.prompts import (
    build_news_system_instruction,
    build_session_prompt,
    build_topic_system_instruction,
    build_topic_prompt,
    build_on_demand_system_instruction,
    build_on_demand_prompt,
)
from app.agents.news.memory import load_news_memory
from app.core.logging_conf import get_module_logger

logger = get_module_logger("news")
client = genai.Client()

# Chunking is handled by send_telegram_msg() in notification.py (HTML-balanced, multi-message).
# Do NOT truncate here — truncation would cut mid-tag and lose content.

# Max parallel topic workers
_MAX_TOPIC_WORKERS = 4


# ==========================================
# HELPERS
# ==========================================

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


def _resolve_topics(config: dict) -> list[dict]:
    """
    Return topics list from config.
    Falls back to deriving from interest_profile keys if topics not configured.
    """
    topics = config.get("news_agent", {}).get("topics", [])
    if topics:
        return topics

    # Backward compat: derive from interest_profile
    interest_profile = config.get("news_agent", {}).get("interest_profile", {})
    _emoji_map = {
        "technology": "💻",
        "sports_running": "🏃",
        "it_workforce": "👨‍💻",
        "economics_politics": "📊",
    }
    return [
        {"name": k.replace("_", " ").title(), "emoji": _emoji_map.get(k, "📰")}
        for k in interest_profile
    ]


def _session_header(session: str, date_str: str) -> str:
    headers = {
        "morning": f"📰 <b>TIN TỨC BUỔI SÁNG — {date_str}</b>",
        "afternoon": f"🌆 <b>CẬP NHẬT CHIỀU — {date_str}</b>",
        "evening": f"🌙 <b>ĐIỂM TIN CUỐI NGÀY — {date_str}</b>",
    }
    return headers.get(session, f"📰 <b>TIN TỨC — {date_str}</b>")


# ==========================================
# GEMINI CALL WRAPPERS
# ==========================================

def _extract_text(response) -> str | None:
    """
    Extract full text from a Gemini response, excluding thinking/reasoning parts.

    Gemini thinking models (e.g. gemini-flash-latest, gemini-2.0-flash-thinking)
    include an internal chain-of-thought before the final answer. These parts have
    ``thought=True`` on the Part object and must never be forwarded to users.

    Also handles the AFC / post-search pattern: when google_search executes, the
    final response may have multiple text parts (pre-search stub + grounded answer).
    We join only the non-thinking text parts.
    """
    try:
        candidates = response.candidates or []
        if not candidates:
            return response.text or None
        parts = getattr(candidates[0].content, "parts", None) or []
        texts = [
            p.text for p in parts
            if getattr(p, "text", None) and not getattr(p, "thought", False)
        ]
        return "".join(texts).strip() or None
    except Exception:
        # Fallback: let SDK handle it
        return response.text or None


_DEBUG_NEWS = os.getenv("DEBUG_NEWS", "").lower() in ("1", "true", "yes")

# Regex that matches a "thought" preamble Gemini thinking models sometimes emit
# when the thought=True attribute is absent or when SDK fallback is used.
# Pattern: text starts with "thought\n" or "thought " (case-insensitive) followed
# by at least one Unicode word character.
import re as _re
_THOUGHT_PREFIX_RE = _re.compile(r"^thought[\n\r ]\w", _re.IGNORECASE)


def _strip_thought_preamble(text: str) -> str | None:
    """Remove a raw 'thought\\n...' preamble that slipped through thought=False filtering.

    Gemini thinking models occasionally emit the chain-of-thought as plain text
    without setting thought=True on the Part.  The real answer always follows the
    preamble, separated from it by the first HTML tag (e.g. <b>, 📊).  We look
    for that boundary and strip everything before it.

    Returns None when the entire text appears to be thinking (no HTML or emoji
    anchor found), so the caller can treat it as an empty/failed response.
    """
    if not _THOUGHT_PREFIX_RE.match(text):
        return text  # no preamble detected

    # Find the first HTML tag or news-emoji that marks the real answer
    anchor = _re.search(r'(<[bBiIaA][\s>]|📊|📰|🔍|📈|✅)', text)
    if not anchor:
        return None  # all thinking, no answer
    return text[anchor.start():].strip() or None

# Default model — Gemini 1.5 Pro uses forced retrieval grounding (dynamic_threshold=0),
# guaranteeing a web search on every call regardless of model confidence.
# Gemini 2.0 Flash uses the agentic google_search tool but the model decides whether to search,
# which is unreliable for generic queries.
_DEFAULT_MODEL = "models/gemini-1.5-pro"


def _is_gemini_15(model: str) -> bool:
    """Return True for Gemini 1.5 family models which support forced retrieval grounding."""
    return "1.5" in model


def _build_search_tool(model: str) -> list:
    """
    Return the correct grounding tool config for the given model.

    Gemini 1.5: google_search_retrieval with dynamic_threshold=0.0 forces a web
    search on every call — the model cannot skip it regardless of confidence.

    Gemini 2.0+: agentic google_search where the model decides whether to invoke
    search. AFC is disabled so the server handles it transparently.
    """
    if _is_gemini_15(model):
        return [
            types.Tool(
                google_search_retrieval=types.GoogleSearchRetrieval(
                    dynamic_retrieval_config=types.DynamicRetrievalConfig(
                        mode=types.DynamicRetrievalConfigMode.MODE_DYNAMIC,
                        dynamic_threshold=0.0,
                    )
                )
            )
        ]
    return [types.Tool(google_search=types.GoogleSearch())]


def _call_gemini_with_search(model: str, system_inst: str, prompt: str, max_tokens: int = 1500) -> str | None:
    """
    Call Gemini with guaranteed web search grounding.

    For Gemini 1.5 models (default): uses google_search_retrieval with
    dynamic_threshold=0.0 which forces a live web search on every request.

    For Gemini 2.0+ models: uses the agentic google_search tool with AFC
    disabled. The model decides whether to search — less reliable for generic
    queries but required by the 2.0 API.

    Set DEBUG_NEWS=true to log the full prompt and response part structure.
    """
    if _DEBUG_NEWS:
        logger.debug(
            "[NEWS-DEBUG] model=%s system_instruction=\n%s\n\nprompt=\n%s",
            model,
            system_inst,
            prompt,
        )

    tools = _build_search_tool(model)
    config_kwargs: dict = dict(
        system_instruction=system_inst,
        tools=tools,
        max_output_tokens=max_tokens,
    )
    # AFC disable only needed for 2.0+ agentic tool — 1.5 retrieval doesn't use AFC
    if not _is_gemini_15(model):
        config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
            disable=True
        )

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        if _DEBUG_NEWS:
            candidates = response.candidates or []
            for ci, cand in enumerate(candidates):
                parts = getattr(getattr(cand, "content", None), "parts", None) or []
                part_summary = [
                    {
                        "type": type(p).__name__,
                        "has_text": bool(getattr(p, "text", None)),
                        "is_thought": bool(getattr(p, "thought", False)),
                    }
                    for p in parts
                ]
                logger.debug(
                    "[NEWS-DEBUG] candidate[%d] finish_reason=%s parts=%s grounding_metadata=%s",
                    ci,
                    getattr(cand, "finish_reason", "?"),
                    part_summary,
                    getattr(cand, "grounding_metadata", None) is not None,
                )

        candidates = response.candidates or []
        grounding_used = any(
            getattr(c, "grounding_metadata", None) is not None
            for c in candidates
        )
        if not grounding_used:
            logger.warning("[NEWS] Gemini did not invoke google_search — response may use training data only.")
        else:
            logger.info("[NEWS] Grounded call completed. grounding_used=True")

        text = _extract_text(response)

        # Defense-in-depth: strip any "thought\n..." preamble that slipped through
        # when the thought=True attribute was absent (seen with gemini-flash-latest).
        if text:
            stripped = _strip_thought_preamble(text)
            if stripped != text:
                thinking_len = len(text) - len(stripped) if stripped else len(text)
                logger.warning(
                    "[NEWS] Stripped %d-char thinking preamble from response (thought attr was False/missing).",
                    thinking_len,
                )
            text = stripped

        if _DEBUG_NEWS:
            logger.debug("[NEWS-DEBUG] extracted text (%d chars): %r", len(text) if text else 0, (text or "")[:300])

        return text
    except Exception as e:
        logger.warning(f"[NEWS] Gemini call failed: {e}")
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


# ==========================================
# PER-TOPIC WORKER (called in thread pool)
# ==========================================

def _call_topic(topic: dict, session: str, date_str: str, model: str) -> tuple[dict, str | None]:
    """
    Fetch news for a single topic. Designed to run in a ThreadPoolExecutor worker.

    Returns:
        (topic dict, formatted block string or None if failed)
    """
    topic_name = topic.get("name", "")
    emoji = topic.get("emoji", "📰")

    system_inst = build_topic_system_instruction()
    prompt = build_topic_prompt(topic_name, emoji, session, date_str)

    logger.info(f"[NEWS-TOPIC] Fetching '{topic_name}'...")
    block = _call_gemini_with_search(model, system_inst, prompt, max_tokens=2000)

    if not block:
        logger.warning(f"[NEWS-TOPIC] No result for '{topic_name}'. Skipping.")
        return topic, None

    # Reject training-data stubs: a real grounded response always has at least
    # one news headline + summary + trend line (> 150 chars). Short responses
    # are pre-search scaffolding that slipped through when AFC didn't complete.
    if len(block) < 150:
        logger.warning(
            f"[NEWS-TOPIC] Response for '{topic_name}' is too short ({len(block)} chars) — "
            "likely a training-data stub. Skipping."
        )
        return topic, None

    # Wrap in topic header
    formatted = f"{emoji} <b>{topic_name.upper()}</b>\n\n{block.strip()}"
    logger.info(f"[NEWS-TOPIC] Got {len(block)} chars for '{topic_name}'")
    return topic, formatted


# ==========================================
# SCHEDULED BRIEFING (parallel per-topic)
# ==========================================

def generate_news_briefing(config: dict, session: str = "morning") -> None:
    """
    Generate and send a scheduled news briefing.

    Runs one Gemini call per topic in parallel (ThreadPoolExecutor),
    then merges all topic blocks into a single Telegram message.

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

    model = _get_model(config)
    date_str = _now_date_str()
    topics = _resolve_topics(config)

    if not topics:
        logger.warning("[NEWS] No topics configured. Falling back to legacy single-call.")
        _generate_legacy_briefing(config, session, chat_id, model, date_str)
        return

    logger.info(f"[NEWS] Starting parallel briefing: {len(topics)} topics, session={session}")

    # Run all topic calls in parallel
    results: dict[int, str] = {}  # index → block, preserves topic order
    with ThreadPoolExecutor(max_workers=min(_MAX_TOPIC_WORKERS, len(topics))) as executor:
        future_map = {
            executor.submit(_call_topic, topic, session, date_str, model): idx
            for idx, topic in enumerate(topics)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                _, block = future.result()
                if block:
                    results[idx] = block
            except Exception as e:
                logger.error(f"[NEWS-TOPIC] Worker error for topic index {idx}: {e}")

    if not results:
        logger.warning("[NEWS] All topic calls failed. Falling back to legacy single-call.")
        _generate_legacy_briefing(config, session, chat_id, model, date_str)
        return

    # Merge in topic order
    header = _session_header(session, date_str)
    blocks = [results[i] for i in sorted(results)]
    message = header + "\n\n" + "\n\n─────\n\n".join(blocks)

    logger.info(f"[NEWS] Merged message length={len(message)}")
    logger.info(f"[TELEGRAM] Prepared message length={len(message)}; head={message[:80]!r}; tail={message[-60:]!r}")
    send_telegram_msg(chat_id, message)
    logger.info(f"[NEWS] Sent {session} briefing to chat_id={chat_id}")


# ==========================================
# ON-DEMAND BRIEFING (user query via @news)
# ==========================================

def generate_on_demand_briefing(query: str, chat_id: str, config: dict) -> str | None:
    """
    Generate and send an on-demand news report for a user-supplied query.

    Called when user sends "@news <query>" via Telegram.
    Performs a focused Gemini search on the query topic and reports back.

    Args:
        query  : user's query text (routing prefix already stripped), e.g. "trending AI"
        chat_id: Telegram chat ID to reply to
        config : loaded config dict from load_config()

    Returns:
        The reply text sent to Telegram, or None if the call failed.
        Callers may use the returned text for memory extraction.
    """
    news_cfg = config.get("news_agent", {})
    if not news_cfg.get("enabled", False):
        return None

    model = _get_model(config)
    date_str = _now_date_str()

    logger.info(f"[NEWS-ONDEMAND] Query='{query[:80]}' for chat_id={chat_id}")

    system_inst = build_on_demand_system_instruction()
    prompt = build_on_demand_prompt(query, date_str)

    reply = _call_gemini_with_search(model, system_inst, prompt, max_tokens=2000)

    if not reply or len(reply) < 100:
        logger.warning(
            f"[NEWS-ONDEMAND] Reply too short or empty ({len(reply) if reply else 0} chars) "
            "— likely training-data stub without search. Sending error fallback."
        )
        send_telegram_msg(chat_id, "⚠️ Không tìm thấy kết quả cho yêu cầu này. Thử lại sau.")
        return None

    logger.info(f"[NEWS-ONDEMAND] Reply length={len(reply)}")
    send_telegram_msg(chat_id, reply)
    logger.info(f"[NEWS-ONDEMAND] Sent reply to chat_id={chat_id}")
    return reply


# ==========================================
# LEGACY SINGLE-CALL FALLBACK
# ==========================================

def _generate_legacy_briefing(config: dict, session: str, chat_id: str, model: str, date_str: str) -> None:
    """
    Legacy single-call briefing. Used as fallback when topics list is empty
    or all parallel topic calls fail.
    """
    user_id = str(get_primary_user_id())
    interest_profile = config.get("news_agent", {}).get("interest_profile", {})
    memory = load_news_memory(user_id)
    prompt = build_session_prompt(session, interest_profile, date_str, memory)
    system_inst = build_news_system_instruction()

    reply = _call_gemini_with_search(model, system_inst, prompt, max_tokens=2000)
    if not reply:
        logger.info("[NEWS] Legacy grounded search empty — falling back to knowledge-only.")
        reply = _call_knowledge_only(model, system_inst, prompt)

    if not reply:
        logger.error(f"[NEWS] Both calls failed for {session}. Skipping send.")
        return

    if len(reply) > _MAX_TELEGRAM_CHARS:
        reply = reply[:_MAX_TELEGRAM_CHARS] + "..."

    send_telegram_msg(chat_id, reply)
    logger.info(f"[NEWS] Sent legacy {session} briefing to chat_id={chat_id}")

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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.core.timezone_utils import get_local_tz
from app.agents.news.prompts import (
    build_news_system_instruction,
    build_session_prompt,
    build_topic_system_instruction,
    build_topic_prompt,
    build_on_demand_system_instruction,
    build_on_demand_prompt,
)
from app.agents.news.memory import load_news_memory
from app.agents.news.telegram_handler import ERR_001, ERR_002
from app.core.logging_conf import get_module_logger

logger = get_module_logger("news")
client = genai.Client(http_options=types.HttpOptions(timeout=30000))  # 30s in ms

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
    return datetime.now(get_local_tz()).strftime("%d/%m/%Y")


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

import re

from app.core.gemini_utils import extract_text as _extract_text, strip_thought_preamble as _strip_thought_preamble

_DEBUG_NEWS = os.getenv("DEBUG_NEWS", "").lower() in ("1", "true", "yes")

_DEFAULT_MODEL = "models/gemini-2.5-flash"
_MAX_TELEGRAM_CHARS = 3500

_SEARCH_TOOL = [types.Tool(google_search=types.GoogleSearch())]


def _extract_grounding_urls(candidates: list) -> list[tuple[str, str]]:
    """Extract (title, uri) pairs from grounding_metadata.grounding_chunks. Deduped, ordered."""
    seen: set[str] = set()
    urls: list[tuple[str, str]] = []
    for cand in candidates:
        meta = getattr(cand, "grounding_metadata", None)
        chunks = getattr(meta, "grounding_chunks", None) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            uri = getattr(web, "uri", "") or ""
            title = getattr(web, "title", "") or ""
            if uri and uri not in seen:
                seen.add(uri)
                urls.append((title, uri))
    return urls


_DOC_THEM_RE = re.compile(r'<a\s+href=["\'][^"\']*["\']>\s*Đọc thêm\s*</a>', re.IGNORECASE)


def _build_sources_block(urls: list[tuple[str, str]], max_sources: int = 3) -> str:
    """Fallback sources block when no 📰 markers found in model output."""
    if not urls:
        return ""
    lines = ["📎 <b>Nguồn:</b>"]
    for title, uri in urls[:max_sources]:
        label = (title.strip() or uri)[:60]
        lines.append(f'• <a href="{uri}">{label}</a>')
    return "\n".join(lines)


def _call_gemini_with_search(
    model: str, system_inst: str, prompt: str, max_tokens: int = 1500
) -> tuple[str | None, list[tuple[str, str]]]:
    """
    Call Gemini with google_search grounding.

    Returns:
        (text, grounding_urls) where grounding_urls is a list of (title, uri) tuples.
        Set DEBUG_NEWS=true to log prompts and response parts.
    """
    if _DEBUG_NEWS:
        logger.debug(
            "[NEWS-DEBUG] model=%s system_instruction=\n%s\n\nprompt=\n%s",
            model,
            system_inst,
            prompt,
        )

    config_kwargs: dict = dict(
        system_instruction=system_inst,
        tools=_SEARCH_TOOL,
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=0),  # 0 = disable thinking output
    )
    _t0 = time.monotonic()
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
        for cand in candidates:
            fr = getattr(cand, "finish_reason", None)
            if str(fr) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS"):
                logger.warning("[NEWS] finish_reason=MAX_TOKENS — response may be truncated. Increase max_output_tokens.")

        latency_ms = int((time.monotonic() - _t0) * 1000)
        grounding_used = any(
            getattr(c, "grounding_metadata", None) is not None
            for c in candidates
        )
        if not grounding_used:
            logger.warning(
                "[NEWS] Grounding not invoked — REJECTING training-data response. latency_ms=%d", latency_ms
            )
            return None, []

        grounding_urls = _extract_grounding_urls(candidates)
        logger.info(
            "[NEWS] Grounded call completed. grounding_used=True source_count=%d latency_ms=%d",
            len(grounding_urls), latency_ms,
        )
        for title, uri in grounding_urls:
            logger.info("[NEWS-SOURCE] %s: %s", title, uri)

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

        return text, grounding_urls
    except Exception as e:
        logger.warning(f"[NEWS] Gemini call failed: {e}")
        return None, []


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
    block, grounding_urls = _call_gemini_with_search(model, system_inst, prompt, max_tokens=6000)

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

    # Strip any LLM-written URLs (unreliable), then append real grounding sources block.
    body = _DOC_THEM_RE.sub("", block.strip())
    sources = _build_sources_block(grounding_urls)
    if sources:
        body = body + "\n\n" + sources
    formatted = f"{emoji} <b>{topic_name.upper()}</b>\n\n{body}"
    logger.info(f"[NEWS-TOPIC] Got {len(block)} chars, {len(grounding_urls)} sources for '{topic_name}'")
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
        logger.warning("[NEWS] No topics configured. Sending ERR_001.")
        send_telegram_msg(chat_id, ERR_001)
        return

    logger.info(f"[NEWS] Starting parallel briefing: {len(topics)} topics, session={session}")
    _t_start = time.monotonic()

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

    duration_ms = int((time.monotonic() - _t_start) * 1000)

    if not results:
        logger.error(
            "[NEWS] All topic calls failed — briefing not sent. "
            "session=%s topics_attempted=%d topics_succeeded=0 duration_ms=%d",
            session, len(topics), duration_ms,
        )
        send_telegram_msg(chat_id, ERR_001)
        return

    # Merge in topic order
    header = _session_header(session, date_str)
    blocks = [results[i] for i in sorted(results)]
    message = header + "\n\n" + "\n\n─────\n\n".join(blocks)

    logger.info(
        "[NEWS] Briefing assembled. session=%s topics_attempted=%d topics_succeeded=%d "
        "chars_sent=%d duration_ms=%d",
        session, len(topics), len(results), len(message), duration_ms,
    )
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

    reply, grounding_urls = _call_gemini_with_search(model, system_inst, prompt, max_tokens=8000)

    if not reply or len(reply) < 100:
        logger.warning(
            f"[NEWS-ONDEMAND] Reply too short or empty ({len(reply) if reply else 0} chars) "
            "— likely training-data stub without search. Sending error fallback."
        )
        send_telegram_msg(chat_id, ERR_002)
        return None

    reply = _DOC_THEM_RE.sub("", reply)
    sources = _build_sources_block(grounding_urls)
    if sources:
        reply = reply.rstrip() + "\n\n" + sources

    logger.info(f"[NEWS-ONDEMAND] Reply length={len(reply)}, sources={len(grounding_urls)}")
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

    reply, grounding_urls = _call_gemini_with_search(model, system_inst, prompt, max_tokens=2000)
    if not reply:
        logger.error(f"[NEWS] Grounded call failed for {session}. Skipping send.")
        return

    reply = _DOC_THEM_RE.sub("", reply)
    sources = _build_sources_block(grounding_urls)
    if sources:
        reply = reply.rstrip() + "\n\n" + sources

    if len(reply) > _MAX_TELEGRAM_CHARS:
        reply = reply[:_MAX_TELEGRAM_CHARS] + "..."

    send_telegram_msg(chat_id, reply)
    logger.info(f"[NEWS] Sent legacy {session} briefing to chat_id={chat_id}")

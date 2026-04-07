"""
News Agent orchestrator.

Flow: fetch RSS feeds → dedup against sent history → summarize with Gemini → send Telegram.

Telegram routing (Option B):
  - news_agent.telegram_chat_id set  → send to that channel/group
  - news_agent.telegram_chat_id empty → fallback to primary TELEGRAM_CHAT_ID
"""
import logging
import os
import pytz
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.core.database import save_sent_articles, get_recent_sent_links
from app.agents.news.feeds import fetch_all_feeds, Article
from app.agents.news.prompts import (
    build_news_system_instruction,
    build_morning_news_prompt,
    build_afternoon_news_prompt,
)

logger = logging.getLogger("AI_COACH")
client = genai.Client()

_MAX_TELEGRAM_CHARS = 4000


def _resolve_chat_id(config: dict) -> str | None:
    """
    Resolve Telegram chat ID for news delivery.
    Priority: news_agent.telegram_chat_id (if set) > primary user ID fallback.
    An empty string in config means "use the main coach chat".
    """
    news_chat_id = config.get("news_agent", {}).get("telegram_chat_id", "").strip()
    if news_chat_id:
        return news_chat_id
    primary = get_primary_user_id()
    return str(primary) if primary else None


def _format_articles_text(articles: list[Article]) -> str:
    lines = []
    for a in articles:
        lines.append(f"[{a.source}] {a.title}\n{a.summary}")
    return "\n\n".join(lines)


def generate_news_briefing(config: dict, session: str = "morning") -> None:
    """
    Fetch news, deduplicate, summarize with Gemini, and send via Telegram.

    Args:
        config: loaded config dict from load_config()
        session: "morning" or "afternoon" — controls tone and dedup tracking
    """
    news_cfg = config.get("news_agent", {})
    if not news_cfg.get("enabled", False):
        logger.info("[NEWS] Agent disabled in config. Skipping.")
        return

    chat_id = _resolve_chat_id(config)
    if not chat_id:
        logger.warning("[NEWS] No Telegram chat ID resolved. Skipping.")
        return

    feeds = news_cfg.get("feeds", [])
    max_articles = int(news_cfg.get("max_articles_per_feed", 5))

    logger.info(f"[NEWS] Fetching articles from {len(feeds)} feed(s)...")
    articles = fetch_all_feeds(feeds, max_per_feed=max_articles)

    if not articles:
        logger.warning("[NEWS] No articles fetched from any feed. Skipping.")
        return

    # Dedup: filter out articles already sent to this user in the last 24 hours
    user_id = str(get_primary_user_id())
    sent_links = get_recent_sent_links(user_id, hours=24)
    fresh_articles = [a for a in articles if a.link not in sent_links]

    if not fresh_articles:
        logger.info("[NEWS] All articles already sent today. Skipping.")
        return

    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    date_str = datetime.now(tz).strftime('%A, %d/%m/%Y')
    articles_text = _format_articles_text(fresh_articles)

    if session == "morning":
        prompt = build_morning_news_prompt(articles_text, date_str)
    else:
        prompt = build_afternoon_news_prompt(articles_text, date_str)

    try:
        system_inst = build_news_system_instruction()
        response = client.models.generate_content(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                max_output_tokens=2000,
            ),
        )
        reply = response.text or "⚠️ Không thể tải tin tức lúc này."

        # Enforce Telegram 4096-char limit
        if len(reply) > _MAX_TELEGRAM_CHARS:
            reply = reply[:_MAX_TELEGRAM_CHARS] + "..."

        send_telegram_msg(chat_id, reply)
        save_sent_articles(user_id, [a.link for a in fresh_articles], session)
        logger.info(f"[NEWS] Sent {session} briefing to chat_id={chat_id} ({len(fresh_articles)} articles)")

    except Exception as e:
        logger.error(f"[NEWS] Error generating {session} briefing: {e}")

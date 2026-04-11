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
from app.core.database import (
    save_sent_articles,
    get_recent_sent_links,
    get_recent_alert_links,
    save_article_score,
    get_cached_scores,
)
from app.agents.news.feeds import fetch_all_feeds, Article
from app.agents.news.scorer import score_articles
from app.agents.news.prompts import (
    build_news_system_instruction,
    build_categorized_digest_prompt,
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
    Fetch news, score articles, send categorized digest with Gemini.

    Filters by digest_threshold (include articles scoring >= digest_threshold).
    Skips articles already alerted as breaking news.

    Args:
        config: loaded config dict from load_config()
        session: "morning" or "afternoon" — controls session name in Telegram message
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
    digest_threshold = int(news_cfg.get("digest_threshold", 4))
    interest_profile = news_cfg.get("interest_profile", {})
    generation_model = config.get("model_name", "models/gemini-2.0-flash")
    # Always use flash-latest for batch scoring — consistent JSON, cost-efficient
    scoring_model = "models/gemini-flash-latest"

    logger.info(f"[NEWS] Fetching articles from {len(feeds)} feed(s)...")
    articles = fetch_all_feeds(feeds, max_per_feed=max_articles)

    if not articles:
        logger.warning("[NEWS] No articles fetched from any feed. Skipping.")
        return

    # Dedup: filter out articles already sent in the last 24 hours
    user_id = str(get_primary_user_id())
    sent_links = get_recent_sent_links(user_id, hours=24)
    fresh_articles = [a for a in articles if a.link not in sent_links]

    if not fresh_articles:
        logger.info("[NEWS] All articles already sent today. Skipping.")
        return

    logger.info(f"[NEWS] {len(fresh_articles)} fresh articles (not sent in last 24h)")

    # Also skip articles already alerted as breaking
    alert_links = get_recent_alert_links(user_id, hours=24)
    briefing_articles = [a for a in fresh_articles if a.link not in alert_links]

    if not briefing_articles:
        logger.info("[NEWS] All fresh articles were already sent as breaking alerts. Skipping digest.")
        return

    # Score articles if interest_profile is configured
    if not interest_profile:
        logger.warning("[NEWS] No interest profile configured. Using basic briefing.")
        tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
        date_str = datetime.now(tz).strftime('%A, %d/%m/%Y')
        articles_text = _format_articles_text(briefing_articles)
        _SESSION_LABELS = {"morning": "sáng", "afternoon": "chiều", "evening": "tối"}
        prompt = build_categorized_digest_prompt(
            {"general": briefing_articles},
            date_str,
            session=_SESSION_LABELS.get(session, session)
        )
    else:
        # Check cache and score fresh articles
        cached_scores = get_cached_scores([a.link for a in briefing_articles])
        to_score = []
        scored_map = {}

        for article in briefing_articles:
            if article.link in cached_scores:
                cached = cached_scores[article.link]
                from app.agents.news.scorer import ScoredArticle
                scored = ScoredArticle(
                    title=article.title,
                    summary=article.summary,
                    link=article.link,
                    source=article.source,
                    published=article.published,
                    score=cached["score"],
                    category=cached["category"],
                    reason="[cached]"
                )
                scored_map[article.link] = scored
            else:
                to_score.append(article)

        # Score uncached articles
        if to_score:
            try:
                newly_scored = score_articles(to_score, interest_profile, scoring_model)
                for scored in newly_scored:
                    scored_map[scored.link] = scored
                    save_article_score(scored.link, scored.score, scored.category)
            except Exception as e:
                logger.warning(f"[NEWS] Error scoring articles: {e}")
                # Fall back to all neutral scores
                from app.agents.news.scorer import ScoredArticle
                for a in to_score:
                    scored_map[a.link] = ScoredArticle(
                        title=a.title,
                        summary=a.summary,
                        link=a.link,
                        source=a.source,
                        published=a.published,
                        score=5,
                        category="general",
                        reason="Lỗi đánh giá"
                    )

        # Filter by digest_threshold and group by category
        categorized: dict[str, list] = {}
        for article in briefing_articles:
            if article.link in scored_map:
                scored = scored_map[article.link]
                if scored.score >= digest_threshold:
                    if scored.category not in categorized:
                        categorized[scored.category] = []
                    categorized[scored.category].append(scored)

        if not categorized:
            logger.info(f"[NEWS] No articles met digest threshold ({digest_threshold}). Skipping.")
            return

        tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
        date_str = datetime.now(tz).strftime('%A, %d/%m/%Y')
        _SESSION_LABELS = {"morning": "sáng", "afternoon": "chiều", "evening": "tối"}
        prompt = build_categorized_digest_prompt(
            categorized,
            date_str,
            session=_SESSION_LABELS.get(session, session)
        )

    try:
        system_inst = build_news_system_instruction()
        response = client.models.generate_content(
            model=generation_model,
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
        save_sent_articles(user_id, [a.link for a in briefing_articles], session)
        logger.info(f"[NEWS] Sent {session} digest to chat_id={chat_id} ({len(briefing_articles)} articles)")

    except Exception as e:
        logger.error(f"[NEWS] Error generating {session} digest: {e}")

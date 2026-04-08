"""
Alert Engine — event-driven news notifications.

Runs every N minutes (configurable, default 30), scores new articles,
sends breaking alerts when score >= threshold. Respects topic cool-down
to prevent spam (max 3 alerts per category per cooldown window).

Key flow:
1. Fetch all feeds
2. Filter out already-sent (24h) and already-alerted (recent)
3. Check cache for pre-scored articles
4. Batch-score remaining articles via Gemini
5. For each article with score >= alert_threshold:
   - Check cool-down: skip if max alerts already sent for this category
   - Build alert message
   - Send via Telegram
   - Log alert + update sent articles table
"""
import logging
import os
import pytz
from datetime import datetime

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.core.database import (
    save_sent_articles,
    get_recent_sent_links,
    save_alert_log,
    get_recent_alert_links,
    get_recent_alerts_by_category,
    save_article_score,
    get_cached_scores,
)
from app.agents.news.feeds import fetch_all_feeds, Article
from app.agents.news.scorer import score_articles, ScoredArticle
from app.agents.news.prompts import build_alert_prompt

logger = logging.getLogger("AI_COACH")

_MAX_TELEGRAM_CHARS = 4000


def run_news_watch(config: dict) -> None:
    """
    Fetch fresh articles, score relevance, send breaking alerts.

    Called by scheduler task_news_watch() every N minutes (default 30).

    Algorithm:
    1. Fetch articles from all feeds
    2. Filter out recently-sent and recently-alerted
    3. Score articles (using cache for already-scored ones)
    4. For each high-relevance article:
       - Check topic cool-down (max 3 alerts per category per window)
       - Send breaking alert if threshold met
       - Log alert
    """
    news_cfg = config.get("news_agent", {})
    if not news_cfg.get("enabled", False):
        logger.info("[NEWS-WATCH] Agent disabled in config. Skipping.")
        return

    user_id = str(get_primary_user_id())
    if not user_id or user_id == "None":
        logger.warning("[NEWS-WATCH] No primary user ID. Skipping.")
        return

    chat_id = news_cfg.get("telegram_chat_id", "").strip() or user_id
    if not chat_id:
        logger.warning("[NEWS-WATCH] No Telegram chat ID. Skipping.")
        return

    # Configuration
    feeds = news_cfg.get("feeds", [])
    max_per_feed = int(news_cfg.get("max_articles_per_feed", 5))
    alert_threshold = int(news_cfg.get("alert_threshold", 7))
    cooldown_hours = int(news_cfg.get("topic_cooldown_hours", 2))
    interest_profile = news_cfg.get("interest_profile", {})
    # Always use flash-latest for batch scoring — cheaper, faster, more reliable JSON output
    model_name = "models/gemini-flash-latest"

    if not interest_profile:
        logger.warning("[NEWS-WATCH] No interest profile configured. Skipping.")
        return

    logger.info(f"[NEWS-WATCH] Starting watch cycle (threshold={alert_threshold}, cooldown={cooldown_hours}h)...")

    # Step 1: Fetch all feeds
    all_articles = fetch_all_feeds(feeds, max_per_feed=max_per_feed)
    if not all_articles:
        logger.info("[NEWS-WATCH] No articles fetched. Skipping.")
        return

    logger.info(f"[NEWS-WATCH] Fetched {len(all_articles)} articles from {len(feeds)} feeds")

    # Step 2: Filter out recently-sent (24h) and recently-alerted
    sent_links = get_recent_sent_links(user_id, hours=24)
    alert_links = get_recent_alert_links(user_id, hours=24)
    fresh_articles = [
        a for a in all_articles
        if a.link not in sent_links and a.link not in alert_links
    ]

    if not fresh_articles:
        logger.info("[NEWS-WATCH] All articles already sent/alerted. Skipping.")
        return

    logger.info(f"[NEWS-WATCH] {len(fresh_articles)} articles are fresh (not sent/alerted)")

    # Step 3: Check cache and separate into cached vs fresh-to-score
    cached_scores = get_cached_scores([a.link for a in fresh_articles])
    to_score = []
    scored_articles_map = {}

    for article in fresh_articles:
        if article.link in cached_scores:
            # Use cached score
            cached = cached_scores[article.link]
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
            scored_articles_map[article.link] = scored
        else:
            to_score.append(article)

    # Step 4: Batch-score remaining articles
    if to_score:
        logger.info(f"[NEWS-WATCH] Scoring {len(to_score)} fresh articles (cache hit: {len(cached_scores)})")
        try:
            newly_scored = score_articles(to_score, interest_profile, model_name)
            for scored in newly_scored:
                scored_articles_map[scored.link] = scored
                # Cache the score for future use
                save_article_score(scored.link, scored.score, scored.category)
        except Exception as e:
            logger.error(f"[NEWS-WATCH] Error scoring articles: {e}")
            # Fall back to treating all as low-score (neutral)
            for a in to_score:
                scored_articles_map[a.link] = ScoredArticle(
                    title=a.title,
                    summary=a.summary,
                    link=a.link,
                    source=a.source,
                    published=a.published,
                    score=5,
                    category="general",
                    reason="Lỗi đánh giá"
                )

    # Step 5: Filter and send alerts for high-relevance articles
    alerts_sent = 0
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    date_str = datetime.now(tz).strftime('%A, %d/%m/%Y')

    for link, scored in scored_articles_map.items():
        if scored.score >= alert_threshold:
            # Check topic cool-down
            alert_count_in_window = get_recent_alerts_by_category(
                user_id, scored.category, hours=cooldown_hours
            )

            if alert_count_in_window >= 3:
                logger.info(
                    f"[NEWS-WATCH] Skipping {link} due to cool-down "
                    f"({alert_count_in_window}/3 alerts for {scored.category})"
                )
                continue

            # Build and send alert
            try:
                alert_msg = build_alert_prompt(scored.title, scored.source, scored.summary, date_str)
                send_telegram_msg(chat_id, alert_msg)

                # Log alert and mark as sent
                save_alert_log(user_id, link, scored.score, scored.category, "breaking")
                save_sent_articles(user_id, [link], "alert")

                alerts_sent += 1
                logger.info(f"[NEWS-WATCH] Sent alert for '{scored.title[:50]}...' (score={scored.score})")

            except Exception as e:
                logger.error(f"[NEWS-WATCH] Error sending alert for {link}: {e}")

    logger.info(f"[NEWS-WATCH] Watch cycle complete. Sent {alerts_sent} breaking alert(s).")

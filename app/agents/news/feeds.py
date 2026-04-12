"""
RSS feed fetching for the News Agent.

Design decisions:
- requests.get() with explicit timeout instead of feedparser.parse(url) to prevent
  scheduler thread blocking on a hung RSS server.
- Per-feed error isolation: one broken feed does not abort the others.
- summary truncated to 300 chars to prevent token overflow in Gemini prompts.
"""
import logging
from dataclasses import dataclass

import feedparser
import requests
from requests.exceptions import RequestException

logger = logging.getLogger("AI_COACH")

_SUMMARY_MAX_LEN = 300
_DEFAULT_TIMEOUT = 10
_USER_AGENT = "Personal-AI-OS/1.0 (RSS reader)"


@dataclass(frozen=True)
class Article:
    title: str
    summary: str
    link: str
    source: str
    published: str


def fetch_feed(url: str, name: str, max_articles: int, timeout: int = _DEFAULT_TIMEOUT) -> list[Article]:
    """
    Fetch and parse a single RSS feed.
    Returns empty list on any network or parse error (per-feed isolation).
    """
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})
        feed = feedparser.parse(response.content)
        articles = []
        for entry in feed.entries[:max_articles]:
            # Prefer 'summary', then 'description', then 'content' (if available)
            summary = entry.get("summary", entry.get("description", ""))
            if not summary:
                content = entry.get("content") or []
                if isinstance(content, list) and content:
                    # content is often a list of dicts with 'value'
                    first = content[0]
                    if isinstance(first, dict):
                        summary = first.get("value", "")
            # Strip/limit length
            summary = (summary or "").strip()[:_SUMMARY_MAX_LEN]

            link = entry.get("link", "") or entry.get("id", "")

            # Skip articles that have neither link nor any summary to avoid empty digests
            if not link or not summary:
                logger.debug(f"[NEWS] Dropping article from feed {name} due to missing link/summary: title='{entry.get('title','')[:60]}'")
                continue

            articles.append(Article(
                title=entry.get("title", "").strip(),
                summary=summary.strip(),
                link=link,
                source=name,
                published=entry.get("published", ""),
            ))
        return articles
    except RequestException as e:
        logger.warning(f"[NEWS] Failed to fetch feed '{name}' ({url}): {e}")
        return []
    except Exception as e:
        logger.warning(f"[NEWS] Unexpected error parsing feed '{name}': {e}")
        return []


def fetch_all_feeds(feeds: list[dict], max_per_feed: int = 5) -> list[Article]:
    """
    Fetch articles from all configured feeds.
    Individual feed failures are isolated — partial results are returned.
    """
    all_articles: list[Article] = []
    for feed_cfg in feeds:
        articles = fetch_feed(
            url=feed_cfg["url"],
            name=feed_cfg["name"],
            max_articles=max_per_feed,
        )
        all_articles.extend(articles)
    return all_articles

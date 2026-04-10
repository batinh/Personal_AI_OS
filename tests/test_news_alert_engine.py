"""
test_news_alert_engine.py — Tests for app/agents/news/alert_engine.py
======================================================================
Covers run_news_watch():
  - Agent disabled → early return, no alert sent
  - No user_id → early return
  - No interest_profile → early return
  - Score >= threshold → alert sent via Telegram
  - Score < threshold → no alert
  - Cool-down (>=3 alerts for category in window) → article skipped
  - Cache hit path → cached score used, no re-scoring
  - Scoring error → neutral score fallback (5), below threshold → no alert
  - No fresh articles after filter → early return
"""
import unittest
from unittest.mock import patch, MagicMock, call

from app.agents.news.alert_engine import run_news_watch
from app.agents.news.scorer import ScoredArticle
from app.agents.news.feeds import Article


def _make_config(enabled=True, alert_threshold=7, interest_profile=None):
    return {
        "news_agent": {
            "enabled": enabled,
            "feeds": [],
            "max_articles_per_feed": 5,
            "alert_threshold": alert_threshold,
            "topic_cooldown_hours": 2,
            "interest_profile": interest_profile or {"running": ["marathon", "training"]},
            "telegram_chat_id": "",
        }
    }


def _make_article(link="http://example.com/1", title="Test Article", source="BBC"):
    return Article(
        title=title,
        summary="A breaking story about running.",
        link=link,
        source=source,
        published="2025-01-15",
    )


def _make_scored(article, score=8, category="running"):
    return ScoredArticle(
        title=article.title,
        summary=article.summary,
        link=article.link,
        source=article.source,
        published=article.published,
        score=score,
        category=category,
        reason="High relevance",
    )


_DB_PATCHES = [
    "app.agents.news.alert_engine.save_sent_articles",
    "app.agents.news.alert_engine.get_recent_sent_links",
    "app.agents.news.alert_engine.save_alert_log",
    "app.agents.news.alert_engine.get_recent_alert_links",
    "app.agents.news.alert_engine.get_recent_alerts_by_category",
    "app.agents.news.alert_engine.save_article_score",
    "app.agents.news.alert_engine.get_cached_scores",
]


def _start_db_patches(test_instance):
    mocks = {}
    for target in _DB_PATCHES:
        name = target.split(".")[-1]
        m = patch(target)
        mocks[name] = m.start()
        test_instance.addCleanup(m.stop)
    mocks["get_recent_sent_links"].return_value = []
    mocks["get_recent_alert_links"].return_value = []
    mocks["get_recent_alerts_by_category"].return_value = 0
    mocks["get_cached_scores"].return_value = {}
    return mocks


class TestRunNewsWatchEarlyReturns(unittest.TestCase):

    def test_agent_disabled_skips_all(self):
        cfg = _make_config(enabled=False)
        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_no_user_id_skips_all(self):
        cfg = _make_config()
        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value=None):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_no_interest_profile_skips_all(self):
        cfg = _make_config(interest_profile={})
        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_no_articles_fetched_skips_alert(self):
        cfg = _make_config()
        mocks = _start_db_patches(self)
        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[]):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_all_articles_already_sent_skips_alert(self):
        cfg = _make_config()
        article = _make_article()
        mocks = _start_db_patches(self)
        mocks["get_recent_sent_links"].return_value = [article.link]
        mocks["get_recent_alert_links"].return_value = []

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]):
            run_news_watch(cfg)
            mock_send.assert_not_called()


class TestRunNewsWatchAlertThreshold(unittest.TestCase):

    def test_score_above_threshold_sends_alert(self):
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=8)
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]), \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="Alert msg"):
            run_news_watch(cfg)
            mock_send.assert_called_once()

    def test_score_equal_threshold_sends_alert(self):
        """score == threshold should trigger alert (>=)."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=7)
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]), \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="Alert msg"):
            run_news_watch(cfg)
            mock_send.assert_called_once()

    def test_score_below_threshold_no_alert(self):
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=5)
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_alert_sent_to_correct_chat_id(self):
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=9)
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="999888"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]), \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="msg"):
            run_news_watch(cfg)
            chat_id_used = mock_send.call_args[0][0]
            self.assertEqual(chat_id_used, "999888")


class TestRunNewsWatchCooldown(unittest.TestCase):

    def test_cooldown_reached_skips_article(self):
        """>=3 alerts for this category in window → article should not be sent."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=9)
        mocks = _start_db_patches(self)
        mocks["get_recent_alerts_by_category"].return_value = 3  # at cooldown cap

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_cooldown_not_reached_sends_alert(self):
        """2 alerts for category → still under cap → alert should be sent."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        scored = _make_scored(article, score=9)
        mocks = _start_db_patches(self)
        mocks["get_recent_alerts_by_category"].return_value = 2

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles", return_value=[scored]), \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="msg"):
            run_news_watch(cfg)
            mock_send.assert_called_once()


class TestRunNewsWatchCache(unittest.TestCase):

    def test_cached_article_skips_scorer(self):
        """Article with cached score should not be passed to score_articles."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        mocks = _start_db_patches(self)
        mocks["get_cached_scores"].return_value = {
            article.link: {"score": 4, "category": "general"}
        }

        with patch("app.agents.news.alert_engine.send_telegram_msg"), \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles") as mock_scorer:
            run_news_watch(cfg)
            mock_scorer.assert_not_called()

    def test_cached_high_score_sends_alert(self):
        """Cached score >= threshold should still trigger alert without re-scoring."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        mocks = _start_db_patches(self)
        mocks["get_cached_scores"].return_value = {
            article.link: {"score": 9, "category": "running"}
        }

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles") as mock_scorer, \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="msg"):
            run_news_watch(cfg)
            mock_scorer.assert_not_called()
            mock_send.assert_called_once()


class TestRunNewsWatchScoringError(unittest.TestCase):

    def test_scoring_error_falls_back_to_neutral_score(self):
        """If score_articles raises, articles get score=5 — below default threshold=7, no alert."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles",
                   side_effect=Exception("Gemini unavailable")):
            run_news_watch(cfg)
            mock_send.assert_not_called()

    def test_scoring_error_does_not_raise(self):
        """Scoring failure must be caught — run_news_watch should not propagate exception."""
        cfg = _make_config(alert_threshold=7)
        article = _make_article()
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg"), \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles",
                   side_effect=Exception("Gemini unavailable")):
            try:
                run_news_watch(cfg)
            except Exception:
                self.fail("run_news_watch raised an exception on scoring failure")

    def test_scoring_error_with_low_threshold_sends_alert(self):
        """If threshold=5 and fallback score=5, alert should still be sent (score >= threshold)."""
        cfg = _make_config(alert_threshold=5)
        article = _make_article()
        mocks = _start_db_patches(self)

        with patch("app.agents.news.alert_engine.send_telegram_msg") as mock_send, \
             patch("app.agents.news.alert_engine.get_primary_user_id", return_value="123456"), \
             patch("app.agents.news.alert_engine.fetch_all_feeds", return_value=[article]), \
             patch("app.agents.news.alert_engine.score_articles",
                   side_effect=Exception("Gemini unavailable")), \
             patch("app.agents.news.alert_engine.build_alert_prompt", return_value="msg"):
            run_news_watch(cfg)
            mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()

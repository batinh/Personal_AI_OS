"""
Integration tests for app/agents/news/agent.py
RED phase: all tests must FAIL before implementation exists.

Patch targets: where symbols are imported (agent.py), not where defined.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from app.agents.news.agent import generate_news_briefing, _resolve_chat_id
from app.agents.news.feeds import Article


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_config():
    return {
        "model_name": "models/gemini-2.0-flash",
        "news_agent": {
            "enabled": True,
            "telegram_chat_id": "",
            "feeds": [{"name": "VnExpress", "url": "http://vnexpress.net/rss"}],
            "max_articles_per_feed": 5,
            "digest_threshold": 4,
            "watch_interval_minutes": 30,
            "interest_profile": {
                "technology": {
                    "weight": 10,
                    "keywords": ["AI", "LLM", "chip"]
                }
            }
        }
    }


@pytest.fixture
def sample_articles():
    return [
        Article(title="Tin 1", summary="Tóm tắt 1", link="http://x.com/1", source="VnExpress", published="2026-04-05"),
        Article(title="Tin 2", summary="Tóm tắt 2", link="http://x.com/2", source="Tuoi Tre", published="2026-04-05"),
    ]


# ---------------------------------------------------------------------------
# _resolve_chat_id
# ---------------------------------------------------------------------------

@patch("app.agents.news.agent.get_primary_user_id", return_value=123456)
def test_resolve_chat_id_falls_back_to_primary(mock_uid):
    config = {"news_agent": {"telegram_chat_id": ""}}
    assert _resolve_chat_id(config) == "123456"


@patch("app.agents.news.agent.get_primary_user_id", return_value=123456)
def test_resolve_chat_id_uses_news_config_when_set(mock_uid):
    config = {"news_agent": {"telegram_chat_id": "-100999888777"}}
    assert _resolve_chat_id(config) == "-100999888777"


@patch("app.agents.news.agent.get_primary_user_id", return_value=None)
def test_resolve_chat_id_returns_none_when_no_id(mock_uid):
    config = {"news_agent": {"telegram_chat_id": ""}}
    assert _resolve_chat_id(config) is None


# ---------------------------------------------------------------------------
# generate_news_briefing — early exits
# ---------------------------------------------------------------------------

@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.send_telegram_msg")
def test_disabled_agent_does_nothing(mock_send, mock_fetch, base_config):
    base_config["news_agent"]["enabled"] = False
    generate_news_briefing(base_config)
    mock_fetch.assert_not_called()
    mock_send.assert_not_called()


@patch("app.agents.news.agent.get_primary_user_id", return_value=None)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.send_telegram_msg")
def test_no_chat_id_does_nothing(mock_send, mock_fetch, mock_uid, base_config):
    generate_news_briefing(base_config)
    mock_fetch.assert_not_called()
    mock_send.assert_not_called()


@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.fetch_all_feeds", return_value=[])
@patch("app.agents.news.agent.send_telegram_msg")
def test_no_articles_skips_gemini(mock_send, mock_fetch, mock_alert, mock_links, mock_uid, base_config):
    generate_news_briefing(base_config)
    mock_send.assert_not_called()


@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links")
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.send_telegram_msg")
def test_all_deduped_skips_gemini(mock_send, mock_fetch, mock_links, mock_alert, mock_uid, base_config, sample_articles):
    mock_fetch.return_value = sample_articles
    # All article links already in sent set
    mock_links.return_value = {a.link for a in sample_articles}
    generate_news_briefing(base_config)
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# generate_news_briefing — success path
# ---------------------------------------------------------------------------

@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_morning_briefing_sends_to_telegram(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    mock_fetch.return_value = sample_articles
    # Mock scorer to return articles with digest-worthy scores
    scored = [
        ScoredArticle(title=sample_articles[0].title, summary=sample_articles[0].summary,
                      link=sample_articles[0].link, source=sample_articles[0].source,
                      published=sample_articles[0].published, score=8, category="technology", reason="AI-related"),
        ScoredArticle(title=sample_articles[1].title, summary=sample_articles[1].summary,
                      link=sample_articles[1].link, source=sample_articles[1].source,
                      published=sample_articles[1].published, score=5, category="general", reason="General"),
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = "📰 TIN TỨC BUỔI SÁNG\n\nTin 1..."

    generate_news_briefing(base_config, session="morning")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "111"
    assert len(args[1]) > 0


@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
@patch("app.agents.news.agent.build_news_system_instruction")
def test_passes_news_system_instruction_to_gemini(mock_si, mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    """Verify the news agent passes its own system_instruction (not coach's) to Gemini."""
    from app.agents.news.scorer import ScoredArticle
    mock_si.return_value = "Bạn là News Curator, biên tập viên tin tức AI."
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = "Tin tức..."

    generate_news_briefing(base_config, session="morning")

    # build_news_system_instruction must be called
    mock_si.assert_called_once()
    # Gemini generate_content must be called with our system instruction in config
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert "config" in call_kwargs
    # The config object is constructed via types.GenerateContentConfig — since types is a stub
    # MagicMock from conftest, verify via the constructor's call_args that the correct
    # system_instruction was passed.
    from google.genai import types as _types
    si_passed = _types.GenerateContentConfig.call_args.kwargs.get("system_instruction")
    assert si_passed == "Bạn là News Curator, biên tập viên tin tức AI."


@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_save_sent_articles_called_after_success(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = "Tin tức buổi sáng..."

    generate_news_briefing(base_config, session="morning")

    mock_save.assert_called_once()
    save_args = mock_save.call_args[0]
    assert save_args[0] == "111"                         # user_id
    assert set(save_args[1]) == {a.link for a in sample_articles}  # links
    assert save_args[2] == "morning"                     # session


@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_uses_news_chat_id_when_configured(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    base_config["news_agent"]["telegram_chat_id"] = "-100555444333"
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = "Tin tức..."

    generate_news_briefing(base_config)

    sent_chat_id = mock_send.call_args[0][0]
    assert sent_chat_id == "-100555444333"


# ---------------------------------------------------------------------------
# generate_news_briefing — edge cases
# ---------------------------------------------------------------------------

@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_long_reply_is_truncated(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = "X" * 5000

    generate_news_briefing(base_config)

    sent_text = mock_send.call_args[0][1]
    assert len(sent_text) <= 4003  # 4000 + "..."


@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_gemini_error_does_not_crash(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.side_effect = Exception("Gemini quota exceeded")

    # Must NOT raise
    generate_news_briefing(base_config)
    mock_send.assert_not_called()


@patch("app.agents.news.agent.save_article_score")
@patch("app.agents.news.agent.score_articles")
@patch("app.agents.news.agent.get_cached_scores", return_value={})
@patch("app.agents.news.agent.save_sent_articles")
@patch("app.agents.news.agent.send_telegram_msg")
@patch("app.agents.news.agent.get_recent_alert_links", return_value=set())
@patch("app.agents.news.agent.get_recent_sent_links", return_value=set())
@patch("app.agents.news.agent.get_primary_user_id", return_value=111)
@patch("app.agents.news.agent.fetch_all_feeds")
@patch("app.agents.news.agent.client")
def test_gemini_empty_response_sends_fallback(mock_client, mock_fetch, mock_uid, mock_links, mock_alert, mock_send, mock_save, mock_cached, mock_score, mock_score_article, base_config, sample_articles):
    from app.agents.news.scorer import ScoredArticle
    mock_fetch.return_value = sample_articles
    scored = [
        ScoredArticle(title=s.title, summary=s.summary, link=s.link, source=s.source,
                      published=s.published, score=8, category="technology", reason="Test")
        for s in sample_articles
    ]
    mock_score.return_value = scored
    mock_client.models.generate_content.return_value.text = None

    generate_news_briefing(base_config)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][1]
    # Fallback string is "⚠️ Không thể tải tin tức lúc này."
    assert "⚠️" in sent_text
    assert "Không thể tải" in sent_text

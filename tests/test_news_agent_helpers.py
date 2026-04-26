"""Tests for pure helper functions in app/agents/news/agent.py."""
import pytest
from unittest.mock import patch, MagicMock
from app.agents.news.agent import (
    _resolve_chat_id,
    _get_model,
    _now_date_str,
    _resolve_topics,
    _session_header,
    _extract_grounding_urls,
)


_DEFAULT_MODEL = "models/gemini-2.5-flash"


class TestResolveChatId:
    def test_uses_configured_chat_id_when_set(self):
        config = {"news_agent": {"telegram_chat_id": "999888"}}
        assert _resolve_chat_id(config) == "999888"

    def test_falls_back_to_primary_user_id(self):
        config = {"news_agent": {"telegram_chat_id": ""}}
        with patch("app.agents.news.agent.get_primary_user_id", return_value=42):
            result = _resolve_chat_id(config)
        assert result == "42"

    def test_returns_none_when_no_user(self):
        config = {}
        with patch("app.agents.news.agent.get_primary_user_id", return_value=None):
            result = _resolve_chat_id(config)
        assert result is None

    def test_strips_whitespace_from_chat_id(self):
        config = {"news_agent": {"telegram_chat_id": "  777  "}}
        assert _resolve_chat_id(config) == "777"


class TestGetModel:
    def test_returns_configured_model(self):
        config = {"news_agent": {"news_model": "models/gemini-pro"}}
        assert _get_model(config) == "models/gemini-pro"

    def test_returns_default_when_not_configured(self):
        assert _get_model({}) == _DEFAULT_MODEL

    def test_returns_default_when_empty_string(self):
        config = {"news_agent": {"news_model": ""}}
        assert _get_model(config) == _DEFAULT_MODEL

    def test_strips_whitespace(self):
        config = {"news_agent": {"news_model": "  models/gemini-pro  "}}
        assert _get_model(config) == "models/gemini-pro"


class TestNowDateStr:
    def test_returns_string_in_dd_mm_yyyy_format(self):
        result = _now_date_str()
        parts = result.split("/")
        assert len(parts) == 3
        assert len(parts[0]) == 2  # DD
        assert len(parts[1]) == 2  # MM
        assert len(parts[2]) == 4  # YYYY


class TestResolveTopics:
    def test_returns_configured_topics(self):
        topics = [{"name": "AI", "emoji": "🤖"}]
        config = {"news_agent": {"topics": topics}}
        assert _resolve_topics(config) == topics

    def test_falls_back_to_interest_profile_keys(self):
        config = {"news_agent": {"interest_profile": {"technology": 8, "sports_running": 6}}}
        result = _resolve_topics(config)
        names = [t["name"] for t in result]
        assert "Technology" in names
        assert "Sports Running" in names

    def test_emoji_mapped_for_known_key(self):
        config = {"news_agent": {"interest_profile": {"technology": 8}}}
        result = _resolve_topics(config)
        assert result[0]["emoji"] == "💻"

    def test_unknown_key_gets_default_emoji(self):
        config = {"news_agent": {"interest_profile": {"random_topic": 5}}}
        result = _resolve_topics(config)
        assert result[0]["emoji"] == "📰"

    def test_empty_config_returns_empty_list(self):
        result = _resolve_topics({})
        assert result == []


class TestSessionHeader:
    def test_morning_header(self):
        result = _session_header("morning", "22/04/2026")
        assert "SÁNG" in result
        assert "22/04/2026" in result

    def test_afternoon_header(self):
        result = _session_header("afternoon", "22/04/2026")
        assert "CHIỀU" in result

    def test_evening_header(self):
        result = _session_header("evening", "22/04/2026")
        assert "CUỐI NGÀY" in result

    def test_unknown_session_fallback(self):
        result = _session_header("weekly", "22/04/2026")
        assert "TIN TỨC" in result
        assert "22/04/2026" in result


class TestExtractGroundingUrls:
    def test_empty_candidates_returns_empty(self):
        assert _extract_grounding_urls([]) == []

    def test_extracts_title_and_uri(self):
        web = MagicMock(uri="https://example.com", title="Example")
        chunk = MagicMock(web=web)
        meta = MagicMock(grounding_chunks=[chunk])
        cand = MagicMock(grounding_metadata=meta)
        result = _extract_grounding_urls([cand])
        assert result == [("Example", "https://example.com")]

    def test_deduplicates_same_uri(self):
        web = MagicMock(uri="https://example.com", title="Example")
        chunk = MagicMock(web=web)
        meta = MagicMock(grounding_chunks=[chunk, chunk])
        cand = MagicMock(grounding_metadata=meta)
        result = _extract_grounding_urls([cand])
        assert len(result) == 1

    def test_skips_chunks_without_uri(self):
        web = MagicMock(uri="", title="NoUri")
        chunk = MagicMock(web=web)
        meta = MagicMock(grounding_chunks=[chunk])
        cand = MagicMock(grounding_metadata=meta)
        assert _extract_grounding_urls([cand]) == []

    def test_skips_candidate_without_grounding_metadata(self):
        cand = MagicMock(spec=[])  # no grounding_metadata attr
        result = _extract_grounding_urls([cand])
        assert result == []

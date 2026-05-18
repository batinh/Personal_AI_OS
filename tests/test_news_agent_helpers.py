"""Tests for pure helper functions in app/agents/news/agent.py."""

from unittest.mock import patch, MagicMock
from app.agents.news.agent import (
    _resolve_chat_id,
    _get_model,
    _now_date_str,
    _resolve_topics,
    _session_header,
    _extract_grounding_urls,
    _inject_inline_links,
    _call_gemini_with_search,
    _DOC_THEM_RE,
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
        config = {
            "news_agent": {"interest_profile": {"technology": 8, "sports_running": 6}}
        }
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


class TestInjectInlineLinks:
    """Tests for inline link injection after each 📰 article."""

    def test_single_article_gets_inline_link(self):
        body = "📰 <b>Tin 1</b> — Tóm tắt."
        urls = [("Reuters", "https://reuters.com/a")]
        result = _inject_inline_links(body, urls)
        assert "→ đọc thêm" in result
        assert "reuters.com/a" in result

    def test_two_articles_each_get_own_link(self):
        body = "📰 <b>Tin 1</b> — Tóm tắt 1.\n\n📰 <b>Tin 2</b> — Tóm tắt 2."
        urls = [("A", "https://example.com/1"), ("B", "https://example.com/2")]
        result = _inject_inline_links(body, urls)
        assert "example.com/1" in result
        assert "example.com/2" in result

    def test_fewer_urls_than_articles_no_error(self):
        body = "📰 <b>Tin 1</b> — Tóm tắt.\n\n📰 <b>Tin 2</b> — Tóm tắt 2."
        urls = [("A", "https://example.com/1")]
        result = _inject_inline_links(body, urls)
        assert "example.com/1" in result
        assert result.count("→ đọc thêm") == 1

    def test_empty_urls_returns_body_unchanged(self):
        body = "📰 <b>Tin 1</b> — Tóm tắt."
        result = _inject_inline_links(body, [])
        assert result == body

    def test_links_appear_inline_not_at_end(self):
        body = "📰 <b>Tin 1</b> — Tóm tắt.\n\n📰 <b>Tin 2</b> — Tóm tắt 2."
        urls = [("A", "https://a.com"), ("B", "https://b.com")]
        result = _inject_inline_links(body, urls)
        idx_a = result.index("a.com")
        idx_b = result.index("b.com")
        idx_tin2 = result.index("Tin 2")
        assert idx_a < idx_tin2 < idx_b


class TestDocThemRegex:
    """Tests for DEF-003 — LLM-authored link stripping."""

    def test_strips_doc_them_link(self):
        text = 'Tin hay. <a href="https://hallucinated.com">Đọc thêm</a> next item.'
        result = _DOC_THEM_RE.sub("", text)
        assert "hallucinated.com" not in result
        assert "<a href" not in result

    def test_strips_case_insensitive(self):
        text = '<a href="https://x.com">ĐỌC THÊM</a>'
        result = _DOC_THEM_RE.sub("", text)
        assert "<a href" not in result

    def test_leaves_non_doc_them_links_intact(self):
        text = '<a href="https://real.com">Real Source</a>'
        result = _DOC_THEM_RE.sub("", text)
        assert "real.com" in result

    def test_multiple_occurrences_all_stripped(self):
        text = (
            '<a href="https://a.com">Đọc thêm</a> mid '
            '<a href="https://b.com">đọc thêm</a>'
        )
        result = _DOC_THEM_RE.sub("", text)
        assert "<a href" not in result


class TestCallGeminiWithSearchGroundingGate:
    """Tests for DEF-001 (thinking_budget=0) and DEF-005 (grounding gate)."""

    def _make_response(self, grounded: bool, text: str = "news content"):
        """Build a minimal mock Gemini response."""
        part = MagicMock()
        part.text = text
        part.thought = False
        content = MagicMock()
        content.parts = [part]
        cand = MagicMock()
        cand.content = content
        cand.finish_reason = "STOP"
        if grounded:
            web = MagicMock(uri="https://real.com", title="Real")
            chunk = MagicMock(web=web)
            meta = MagicMock(grounding_chunks=[chunk])
            cand.grounding_metadata = meta
        else:
            cand.grounding_metadata = None
        response = MagicMock()
        response.candidates = [cand]
        response.text = text
        return response

    def test_grounding_used_false_returns_none(self):
        """DEF-005: non-grounded response must be rejected."""
        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.return_value = self._make_response(
                grounded=False
            )
            text, urls = _call_gemini_with_search("model", "sys", "prompt")
        assert text is None
        assert urls == []

    def test_grounding_used_true_returns_text(self):
        with patch("app.agents.news.agent.client") as mock_client:
            with patch(
                "app.agents.news.agent._extract_text", return_value="news content"
            ):
                mock_client.models.generate_content.return_value = self._make_response(
                    grounded=True
                )
                text, urls = _call_gemini_with_search("model", "sys", "prompt")
        assert text is not None

    def test_thinking_budget_zero_passed(self):
        """DEF-001: thinking_budget=0 must be configured to prevent thought leakage."""
        with patch("app.agents.news.agent.client") as mock_client:
            with patch("app.agents.news.agent.types") as mock_types:
                mock_client.models.generate_content.return_value = self._make_response(
                    grounded=True
                )
                with patch(
                    "app.agents.news.agent._extract_text", return_value="content"
                ):
                    _call_gemini_with_search("model", "sys", "prompt")
        mock_types.ThinkingConfig.assert_called_once_with(thinking_budget=0)

    def test_api_exception_returns_none(self):
        """DEF-005: any exception → (None, []), never propagates."""
        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.side_effect = Exception("API error")
            text, urls = _call_gemini_with_search("model", "sys", "prompt")
        assert text is None
        assert urls == []

    def test_grounding_used_false_logs_reject_message(self, caplog):
        import logging

        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.return_value = self._make_response(
                grounded=False
            )
            with caplog.at_level(logging.WARNING, logger="news"):
                _call_gemini_with_search("model", "sys", "prompt")
        assert any("REJECTING" in r.message for r in caplog.records)

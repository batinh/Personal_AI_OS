"""Tests for generate_news_briefing, generate_on_demand_briefing, and _generate_legacy_briefing."""

from unittest.mock import patch
from app.agents.news.agent import (
    generate_news_briefing,
    generate_on_demand_briefing,
)
from app.agents.news.telegram_handler import ERR_001, ERR_002

_ENABLED_CFG = {
    "news_agent": {
        "enabled": True,
        "telegram_chat_id": "777",
        "topics": [{"name": "AI", "emoji": "🤖"}],
        "news_model": "models/gemini-flash",
    }
}

_DISABLED_CFG = {"news_agent": {"enabled": False}}


class TestGenerateNewsBriefingDisabled:
    def test_disabled_returns_early(self):
        with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
            generate_news_briefing(_DISABLED_CFG, "morning")
        mock_send.assert_not_called()


class TestGenerateNewsBriefingNoChatId:
    def test_no_chat_id_skips_send(self):
        cfg = {
            "news_agent": {
                "enabled": True,
                "telegram_chat_id": "",
                "topics": [{"name": "AI", "emoji": "🤖"}],
            }
        }
        with patch("app.agents.news.agent.get_primary_user_id", return_value=None):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                generate_news_briefing(cfg, "morning")
        mock_send.assert_not_called()


class TestGenerateNewsBriefingNoTopics:
    def test_no_topics_sends_err001(self):
        cfg = {
            "news_agent": {
                "enabled": True,
                "telegram_chat_id": "777",
                "topics": [],
                "interest_profile": {},
            }
        }
        with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
            generate_news_briefing(cfg, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001


class TestGenerateNewsBriefingHappyPath:
    def test_sends_merged_message(self):
        with patch(
            "app.agents.news.agent._call_topic",
            return_value=({"name": "AI"}, "<b>AI news</b>"),
        ):
            with patch("app.agents.news.agent.send_telegram_html") as mock_send:
                generate_news_briefing(_ENABLED_CFG, "morning")
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "AI news" in text

    def test_header_in_message(self):
        with patch(
            "app.agents.news.agent._call_topic",
            return_value=({"name": "AI"}, "news block"),
        ):
            with patch("app.agents.news.agent.send_telegram_html") as mock_send:
                with patch(
                    "app.agents.news.agent._now_date_str", return_value="22/04/2026"
                ):
                    generate_news_briefing(_ENABLED_CFG, "morning")
        text = mock_send.call_args[0][1]
        assert "22/04/2026" in text

    def test_all_topics_fail_sends_err001(self):
        with patch(
            "app.agents.news.agent._call_topic",
            side_effect=RuntimeError("topic failed"),
        ):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                generate_news_briefing(_ENABLED_CFG, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001


class TestGenerateOnDemandBriefingDisabled:
    def test_disabled_returns_none(self):
        result = generate_on_demand_briefing("some query", "123", _DISABLED_CFG)
        assert result is None


class TestGenerateOnDemandBriefingShortReply:
    def test_short_reply_sends_error_and_returns_none(self):
        with patch(
            "app.agents.news.agent._call_gemini_with_search",
            return_value=("short", []),
        ):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                result = generate_on_demand_briefing("query", "123", _ENABLED_CFG)
        assert result is None
        mock_send.assert_called_once()
        assert "⚠️" in mock_send.call_args[0][1]


class TestGenerateOnDemandBriefingHappyPath:
    def test_happy_path_sends_reply(self):
        long_reply = "x" * 200
        with patch(
            "app.agents.news.agent._call_gemini_with_search",
            return_value=(long_reply, []),
        ):
            with patch("app.agents.news.agent.send_telegram_html") as mock_send:
                result = generate_on_demand_briefing("query", "123", _ENABLED_CFG)
        assert result == long_reply
        mock_send.assert_called_once_with("123", long_reply)

    def test_grounding_sources_appended(self):
        long_reply = "x" * 200
        sources = [("Example", "https://example.com")]
        with patch(
            "app.agents.news.agent._call_gemini_with_search",
            return_value=(long_reply, sources),
        ):
            with patch(
                "app.agents.news.agent._inject_inline_links",
                return_value=long_reply + " inline link",
            ):
                with patch("app.agents.news.agent.send_telegram_html") as mock_send:
                    generate_on_demand_briefing("query", "123", _ENABLED_CFG)
        sent_text = mock_send.call_args[0][1]
        assert "inline link" in sent_text


class TestGenerateLegacyBriefing:
    def test_reply_sends_to_telegram(self):
        from app.agents.news.agent import _generate_legacy_briefing

        long_reply = "y" * 200
        with patch("app.agents.news.agent.get_primary_user_id", return_value=1):
            with patch("app.agents.news.agent.load_news_memory", return_value={}):
                with patch(
                    "app.agents.news.agent._call_gemini_with_search",
                    return_value=(long_reply, []),
                ):
                    with patch("app.agents.news.agent.send_telegram_html") as mock_send:
                        _generate_legacy_briefing(
                            _ENABLED_CFG,
                            "morning",
                            "777",
                            "models/gemini-flash",
                            "22/04/2026",
                        )
        mock_send.assert_called_once()

    def test_grounded_fails_no_send(self):
        from app.agents.news.agent import _generate_legacy_briefing

        with patch("app.agents.news.agent.get_primary_user_id", return_value=1):
            with patch("app.agents.news.agent.load_news_memory", return_value={}):
                with patch(
                    "app.agents.news.agent._call_gemini_with_search",
                    return_value=(None, []),
                ):
                    with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                        _generate_legacy_briefing(
                            _ENABLED_CFG,
                            "morning",
                            "777",
                            "models/gemini-flash",
                            "22/04/2026",
                        )
        mock_send.assert_not_called()

    def test_sources_block_appended_when_grounding_urls_present(self):
        from app.agents.news.agent import _generate_legacy_briefing

        reply = "z" * 200
        sources = [("Source", "https://example.com")]
        with patch("app.agents.news.agent.get_primary_user_id", return_value=1):
            with patch("app.agents.news.agent.load_news_memory", return_value={}):
                with patch(
                    "app.agents.news.agent._call_gemini_with_search",
                    return_value=(reply, sources),
                ):
                    with patch(
                        "app.agents.news.agent._inject_inline_links",
                        return_value=reply + " inline nguồn",
                    ):
                        with patch(
                            "app.agents.news.agent.send_telegram_html"
                        ) as mock_send:
                            _generate_legacy_briefing(
                                _ENABLED_CFG,
                                "morning",
                                "777",
                                "models/gemini-flash",
                                "22/04/2026",
                            )
        sent_text = mock_send.call_args[0][1]
        assert "inline nguồn" in sent_text


class TestOnDemandShortReplyUsesConstant:
    def test_short_reply_sends_err002_constant(self):
        with patch(
            "app.agents.news.agent._call_gemini_with_search",
            return_value=("short", []),
        ):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                generate_on_demand_briefing("query", "123", _ENABLED_CFG)
        assert mock_send.call_args[0][1] == ERR_002


class TestGenerateNewsBriefingStructuredLog:
    def test_all_fail_sends_err001_not_legacy(self):
        """Behavioral: all topics fail → ERR_001 sent, _generate_legacy_briefing NOT called."""
        with patch(
            "app.agents.news.agent._call_topic", return_value=({"name": "AI"}, None)
        ):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                with patch(
                    "app.agents.news.agent._generate_legacy_briefing"
                ) as mock_legacy:
                    generate_news_briefing(_ENABLED_CFG, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001
        mock_legacy.assert_not_called()

    def test_empty_topics_sends_err001_not_legacy(self):
        cfg = {"news_agent": {"enabled": True, "telegram_chat_id": "777", "topics": []}}
        with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
            with patch(
                "app.agents.news.agent._generate_legacy_briefing"
            ) as mock_legacy:
                generate_news_briefing(cfg, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001
        mock_legacy.assert_not_called()


class TestCallGeminiWithSearchRetry:
    """Verify _call_gemini_with_search retries on 503/504 and fails cleanly after max retries."""

    def test_retries_on_504_then_succeeds(self):
        """First call raises 504 DEADLINE_EXCEEDED; second call succeeds."""
        from unittest.mock import MagicMock, patch
        from app.agents.news.agent import _call_gemini_with_search

        call_count = 0

        def fake_generate(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("504 DEADLINE_EXCEEDED: Deadline expired")
            mock_resp = MagicMock()
            mock_cand = MagicMock()
            mock_cand.finish_reason = None
            mock_cand.grounding_metadata = object()
            mock_resp.candidates = [mock_cand]
            return mock_resp

        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.side_effect = fake_generate
            with patch("app.agents.news.agent.time.sleep") as mock_sleep:
                with patch("app.agents.news.agent._extract_text", return_value="news text"):
                    with patch("app.agents.news.agent._extract_grounding_urls", return_value=[("T", "http://example.com")]):
                        result_text, result_urls = _call_gemini_with_search(
                            "model", "sys", "prompt"
                        )

        assert call_count == 2, "Should have retried exactly once"
        mock_sleep.assert_called_once_with(5)  # first retry delay
        assert result_text == "news text"

    def test_all_retries_exhausted_returns_none(self):
        """All 3 attempts (1 + 2 retries) raise 503 → returns (None, [])."""
        from unittest.mock import patch
        from app.agents.news.agent import _call_gemini_with_search, _NEWS_MAX_RETRIES

        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.side_effect = Exception(
                "503 UNAVAILABLE: high demand"
            )
            with patch("app.agents.news.agent.time.sleep"):
                result_text, result_urls = _call_gemini_with_search(
                    "model", "sys", "prompt"
                )

        assert result_text is None
        assert result_urls == []
        assert mock_client.models.generate_content.call_count == _NEWS_MAX_RETRIES + 1

    def test_non_retryable_error_fails_immediately(self):
        """A non-transient error (e.g. invalid API key) should NOT be retried."""
        from unittest.mock import patch
        from app.agents.news.agent import _call_gemini_with_search

        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.side_effect = Exception(
                "400 INVALID_ARGUMENT: API key invalid"
            )
            with patch("app.agents.news.agent.time.sleep") as mock_sleep:
                result_text, result_urls = _call_gemini_with_search(
                    "model", "sys", "prompt"
                )

        assert result_text is None
        assert result_urls == []
        mock_client.models.generate_content.call_count == 1  # no retry
        mock_sleep.assert_not_called()

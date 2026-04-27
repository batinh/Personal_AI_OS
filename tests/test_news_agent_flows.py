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
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                generate_news_briefing(_ENABLED_CFG, "morning")
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "AI news" in text

    def test_header_in_message(self):
        with patch(
            "app.agents.news.agent._call_topic",
            return_value=({"name": "AI"}, "news block"),
        ):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                with patch("app.agents.news.agent._now_date_str", return_value="22/04/2026"):
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
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
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
                "app.agents.news.agent._build_sources_block",
                return_value="📎 sources block",
            ):
                with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                    generate_on_demand_briefing("query", "123", _ENABLED_CFG)
        sent_text = mock_send.call_args[0][1]
        assert "sources block" in sent_text


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
                    with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                        _generate_legacy_briefing(
                            _ENABLED_CFG, "morning", "777", "models/gemini-flash", "22/04/2026"
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
                            _ENABLED_CFG, "morning", "777", "models/gemini-flash", "22/04/2026"
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
                        "app.agents.news.agent._build_sources_block",
                        return_value="📎 Nguồn block",
                    ):
                        with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                            _generate_legacy_briefing(
                                _ENABLED_CFG, "morning", "777", "models/gemini-flash", "22/04/2026"
                            )
        sent_text = mock_send.call_args[0][1]
        assert "Nguồn block" in sent_text


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
        with patch("app.agents.news.agent._call_topic", return_value=({"name": "AI"}, None)):
            with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
                with patch("app.agents.news.agent._generate_legacy_briefing") as mock_legacy:
                    generate_news_briefing(_ENABLED_CFG, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001
        mock_legacy.assert_not_called()

    def test_empty_topics_sends_err001_not_legacy(self):
        cfg = {"news_agent": {"enabled": True, "telegram_chat_id": "777", "topics": []}}
        with patch("app.agents.news.agent.send_telegram_msg") as mock_send:
            with patch("app.agents.news.agent._generate_legacy_briefing") as mock_legacy:
                generate_news_briefing(cfg, "morning")
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_001
        mock_legacy.assert_not_called()

"""Tests for app/agents/news/telegram_handler.py."""
import pytest
from unittest.mock import patch, MagicMock
from app.agents.news.telegram_handler import handle_news_command, handle_news_chat


_DISABLED_CFG = {"news_agent": {"enabled": False}}
_ENABLED_CFG = {"news_agent": {"enabled": True}}


class TestHandleNewsCommandDisabled:
    def test_sends_disabled_message(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", [], _DISABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "tắt" in text.lower() or "disabled" in text.lower() or "đang tắt" in text

    def test_does_not_call_generate(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch("app.agents.news.agent.generate_news_briefing") as mock_gen:
                handle_news_command("123", [], _DISABLED_CFG)
        mock_gen.assert_not_called()


class TestHandleNewsCommandHelp:
    def test_help_arg_sends_help_message(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", ["help"], _ENABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "News Agent" in text

    def test_help_case_insensitive(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", ["HELP"], _ENABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "News Agent" in text


class TestHandleNewsCommandInvalid:
    def test_invalid_arg_sends_error(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", ["weekly"], _ENABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "weekly" in text
        assert "❌" in text

    def test_invalid_arg_includes_help(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", ["bogus"], _ENABLED_CFG)
        text = mock_send.call_args[0][1]
        assert "News Agent" in text


class TestHandleNewsCommandValid:
    @pytest.mark.parametrize("session", ["morning", "afternoon", "evening"])
    def test_valid_session_calls_generate(self, session: str):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch(
                "app.agents.news.agent.generate_news_briefing"
            ) as mock_gen:
                handle_news_command("123", [session], _ENABLED_CFG)
        mock_gen.assert_called_once_with(_ENABLED_CFG, session=session)

    def test_no_args_defaults_to_morning(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch(
                "app.agents.news.agent.generate_news_briefing"
            ) as mock_gen:
                handle_news_command("123", [], _ENABLED_CFG)
        mock_gen.assert_called_once_with(_ENABLED_CFG, session="morning")

    def test_exception_sends_error_message(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            with patch(
                "app.agents.news.agent.generate_news_briefing",
                side_effect=RuntimeError("boom"),
            ):
                handle_news_command("123", ["morning"], _ENABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "❌" in text
        assert "sáng" in text


class TestHandleNewsChatDisabled:
    def test_sends_disabled_message(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_chat("123", "any query", _DISABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "tắt" in text.lower() or "đang tắt" in text


class TestHandleNewsChatEmptyText:
    def test_empty_string_sends_help(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_chat("123", "", _ENABLED_CFG)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "News Agent" in text


class TestHandleNewsChatHappyPath:
    def test_calls_on_demand_briefing(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch("app.agents.news.telegram_handler.get_primary_user_id", return_value=1):
                with patch("app.agents.news.agent._get_model", return_value=MagicMock()):
                    with patch(
                        "app.agents.news.agent.generate_on_demand_briefing",
                        return_value="Some reply",
                    ) as mock_od:
                        with patch("app.agents.news.telegram_handler.run_extract_in_background"):
                            handle_news_chat("123", "ETF Việt Nam", _ENABLED_CFG)
        mock_od.assert_called_once_with("ETF Việt Nam", "123", _ENABLED_CFG)

    def test_runs_memory_extraction_when_reply(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch("app.agents.news.telegram_handler.get_primary_user_id", return_value=42):
                with patch("app.agents.news.agent._get_model", return_value=MagicMock()):
                    with patch(
                        "app.agents.news.agent.generate_on_demand_briefing",
                        return_value="Reply content",
                    ):
                        with patch(
                            "app.agents.news.telegram_handler.run_extract_in_background"
                        ) as mock_extract:
                            handle_news_chat("123", "some query", _ENABLED_CFG)
        mock_extract.assert_called_once()

    def test_no_memory_extraction_when_no_reply(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch("app.agents.news.telegram_handler.get_primary_user_id", return_value=1):
                with patch("app.agents.news.agent._get_model", return_value=MagicMock()):
                    with patch(
                        "app.agents.news.agent.generate_on_demand_briefing",
                        return_value=None,
                    ):
                        with patch(
                            "app.agents.news.telegram_handler.run_extract_in_background"
                        ) as mock_extract:
                            handle_news_chat("123", "some query", _ENABLED_CFG)
        mock_extract.assert_not_called()

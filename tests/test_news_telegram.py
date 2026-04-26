"""Tests for app/agents/news/telegram_handler.py."""
import pytest
import time
from unittest.mock import patch, MagicMock
from app.agents.news.telegram_handler import (
    handle_news_command,
    handle_news_chat,
    _check_rate_limit,
    _rate_limit_store,
    ERR_001, ERR_002, ERR_003, ERR_004, ERR_005, ERR_006,
    RATE_LIMIT, RATE_WINDOW,
)


_DISABLED_CFG = {"news_agent": {"enabled": False}}
_ENABLED_CFG = {"news_agent": {"enabled": True}}


class TestHandleNewsCommandDisabled:
    def test_sends_disabled_message(self):
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            handle_news_command("123", [], _DISABLED_CFG)
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_004

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
        assert mock_send.call_args[0][1] == ERR_004


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


class TestCheckRateLimit:
    def setup_method(self):
        # Isolate each test by clearing the store for the test chat_id
        _rate_limit_store.pop("rl_test", None)

    def test_first_request_allowed(self):
        assert _check_rate_limit("rl_test") is True

    def test_up_to_limit_allowed(self):
        for _ in range(RATE_LIMIT):
            assert _check_rate_limit("rl_test") is True

    def test_exceeding_limit_blocked(self):
        for _ in range(RATE_LIMIT):
            _check_rate_limit("rl_test")
        assert _check_rate_limit("rl_test") is False

    def test_old_timestamps_expire(self):
        # Fill up with timestamps older than RATE_WINDOW
        _rate_limit_store["rl_test"] = [time.time() - RATE_WINDOW - 1] * RATE_LIMIT
        assert _check_rate_limit("rl_test") is True

    def test_mixed_old_and_recent(self):
        now = time.time()
        # 9 recent + RATE_LIMIT old expired → should allow one more
        _rate_limit_store["rl_test"] = (
            [now - RATE_WINDOW - 1] * RATE_LIMIT  # all expired
            + [now - 60] * (RATE_LIMIT - 1)        # 9 recent
        )
        assert _check_rate_limit("rl_test") is True  # 9+1 = 10 = at limit, still allowed
        assert _check_rate_limit("rl_test") is False  # 11th blocked


class TestHandleNewsChatRateLimit:
    def setup_method(self):
        _rate_limit_store.pop("rl_chat", None)

    def test_11th_request_sends_err003(self):
        # Fill the bucket to RATE_LIMIT
        for _ in range(RATE_LIMIT):
            _rate_limit_store.setdefault("rl_chat", []).append(time.time())
        with patch("app.agents.news.telegram_handler.send_telegram_msg") as mock_send:
            with patch("app.agents.news.agent.generate_on_demand_briefing") as mock_od:
                handle_news_chat("rl_chat", "query", _ENABLED_CFG)
        mock_send.assert_called_once()
        assert mock_send.call_args[0][1] == ERR_003
        mock_od.assert_not_called()

    def test_rate_limited_does_not_call_llm(self):
        _rate_limit_store["rl_chat"] = [time.time()] * RATE_LIMIT
        with patch("app.agents.news.telegram_handler.send_telegram_msg"):
            with patch("app.agents.news.agent.generate_on_demand_briefing") as mock_od:
                handle_news_chat("rl_chat", "query", _ENABLED_CFG)
        mock_od.assert_not_called()


class TestErrorConstants:
    def test_err_constants_are_strings(self):
        for const in [ERR_001, ERR_002, ERR_003, ERR_004, ERR_005, ERR_006]:
            assert isinstance(const, str)
            assert len(const) > 0

    def test_err001_contains_warning_emoji(self):
        assert "⚠️" in ERR_001

    def test_err003_mentions_retry(self):
        assert "Thử lại" in ERR_003

    def test_err004_mentions_disabled(self):
        assert "tắt" in ERR_004.lower() or "News Agent" in ERR_004

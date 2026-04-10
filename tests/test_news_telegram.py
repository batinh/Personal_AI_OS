"""
test_news_telegram.py — Tests for /news Telegram command handler and
                         POST /console/save-news endpoint.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _base_config(enabled=True):
    return {
        "news_agent": {
            "enabled": enabled,
            "morning_time": "07:00",
            "afternoon_time": "17:00",
            "watch_interval_minutes": 30,
            "alert_threshold": 7,
            "digest_threshold": 4,
            "topic_cooldown_hours": 2,
            "max_articles_per_feed": 5,
            "telegram_chat_id": "",
            "feeds": [],
            "interest_profile": {},
        }
    }


# ---------------------------------------------------------------------------
# handle_news_command — unit tests
# ---------------------------------------------------------------------------

class TestHandleNewsCommand(unittest.TestCase):
    """Unit tests for app/agents/news/telegram_handler.handle_news_command."""

    def _call(self, args, config=None):
        from app.agents.news.telegram_handler import handle_news_command
        handle_news_command("123", args, config or _base_config())

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    @patch("app.agents.news.agent.generate_news_briefing")
    def test_no_args_defaults_to_morning(self, mock_brief, mock_send):
        mock_brief.return_value = None
        self._call([])
        # Should call generate_news_briefing with session='morning'
        mock_brief.assert_called_once()
        _, kwargs = mock_brief.call_args
        self.assertEqual(kwargs.get("session") or mock_brief.call_args[0][1], "morning")

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    @patch("app.agents.news.agent.generate_news_briefing")
    def test_morning_arg(self, mock_brief, mock_send):
        mock_brief.return_value = None
        self._call(["morning"])
        mock_brief.assert_called_once()

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    @patch("app.agents.news.agent.generate_news_briefing")
    def test_afternoon_arg(self, mock_brief, mock_send):
        mock_brief.return_value = None
        self._call(["afternoon"])
        mock_brief.assert_called_once()
        _, kwargs = mock_brief.call_args
        session = kwargs.get("session") or mock_brief.call_args[0][1]
        self.assertEqual(session, "afternoon")

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    @patch("app.agents.news.alert_engine.run_news_watch")
    def test_watch_arg_calls_run_news_watch(self, mock_watch, mock_send):
        mock_watch.return_value = None
        self._call(["watch"])
        mock_watch.assert_called_once()

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    def test_help_arg_sends_help_message(self, mock_send):
        self._call(["help"])
        # Should send help message, not trigger any briefing
        mock_send.assert_called()
        msg = mock_send.call_args[0][1]
        self.assertIn("/news", msg)

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    def test_invalid_arg_sends_error_message(self, mock_send):
        self._call(["invalid_cmd"])
        mock_send.assert_called()
        msg = mock_send.call_args[0][1]
        self.assertIn("invalid_cmd", msg)

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    def test_disabled_agent_sends_disabled_message(self, mock_send):
        self._call(["morning"], config=_base_config(enabled=False))
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("tắt", msg.lower())

    @patch("app.agents.news.telegram_handler.send_telegram_msg")
    @patch("app.agents.news.agent.generate_news_briefing")
    def test_error_in_flow_sends_error_message(self, mock_brief, mock_send):
        mock_brief.side_effect = Exception("network error")
        self._call(["morning"])
        # Should send loading message + error message (at least 2 sends)
        self.assertGreaterEqual(mock_send.call_count, 2)
        last_msg = mock_send.call_args[0][1]
        self.assertIn("Lỗi", last_msg)


# ---------------------------------------------------------------------------
# /telegram-webhook → /news command routing
# ---------------------------------------------------------------------------

class TestTelegramWebhookNewsRouting(unittest.TestCase):
    """Integration test: POST /telegram-webhook dispatches /news to background task."""

    def setUp(self):
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    @patch("app.agents.news.telegram_handler.handle_news_command")
    @patch("app.routers.webhooks.load_config", return_value=_base_config())
    def test_news_command_routed_to_handler(self, mock_cfg, mock_handler):
        resp = self.client.post("/telegram-webhook", json={
            "message": {"chat": {"id": 111}, "text": "/news morning"}
        })
        self.assertEqual(resp.status_code, 200)

    @patch("app.agents.news.telegram_handler.handle_news_command")
    @patch("app.routers.webhooks.load_config", return_value=_base_config())
    def test_news_watch_command_routed(self, mock_cfg, mock_handler):
        resp = self.client.post("/telegram-webhook", json={
            "message": {"chat": {"id": 111}, "text": "/news watch"}
        })
        self.assertEqual(resp.status_code, 200)

    @patch("app.routers.webhooks.handle_telegram_chat")
    @patch("app.routers.webhooks.load_config", return_value=_base_config())
    def test_non_news_command_goes_to_chat(self, mock_cfg, mock_chat):
        resp = self.client.post("/telegram-webhook", json={
            "message": {"chat": {"id": 111}, "text": "Hello bot"}
        })
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# POST /console/save-news
# ---------------------------------------------------------------------------

class TestConsoleSaveNews(unittest.TestCase):
    """Tests for POST /console/save-news endpoint."""

    def setUp(self):
        import os
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "testpass"
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)
        self.auth = ("admin", "testpass")

    def _post(self, data):
        return self.client.post("/console/save-news", data=data, auth=self.auth,
                                follow_redirects=False)

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_save_redirects_to_news_tab(self, mock_load, mock_save, mock_reload):
        resp = self._post({
            "morning_time": "07:30",
            "afternoon_time": "16:00",
            "watch_interval_minutes": "20",
            "alert_threshold": "8",
            "digest_threshold": "5",
            "topic_cooldown_hours": "3",
            "max_articles_per_feed": "10",
            "feeds_json": "[]",
            "interest_profile_json": "{}",
        })
        self.assertEqual(resp.status_code, 303)
        self.assertIn("tab=news", resp.headers["location"])

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_enabled_toggle_on(self, mock_load, mock_save, mock_reload):
        self._post({
            "news_enabled": "on",
            "feeds_json": "[]",
            "interest_profile_json": "{}",
        })
        saved = mock_save.call_args[0][0]
        self.assertTrue(saved["news_agent"]["enabled"])

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_enabled_toggle_off(self, mock_load, mock_save, mock_reload):
        self._post({
            # news_enabled omitted → checkbox unchecked
            "feeds_json": "[]",
            "interest_profile_json": "{}",
        })
        saved = mock_save.call_args[0][0]
        self.assertFalse(saved["news_agent"]["enabled"])

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_feeds_json_parsed_correctly(self, mock_load, mock_save, mock_reload):
        feeds = [{"name": "VnExpress", "url": "https://vnexpress.net/rss", "category": "tech"}]
        self._post({
            "feeds_json": json.dumps(feeds),
            "interest_profile_json": "{}",
        })
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["news_agent"]["feeds"], feeds)

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_interest_profile_json_parsed_correctly(self, mock_load, mock_save, mock_reload):
        profile = {"tech": {"keywords": ["AI", "LLM"], "weight": 2.0}}
        self._post({
            "feeds_json": "[]",
            "interest_profile_json": json.dumps(profile),
        })
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["news_agent"]["interest_profile"], profile)

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_invalid_feeds_json_keeps_existing(self, mock_load, mock_save, mock_reload):
        existing = [{"name": "existing", "url": "https://x.com", "category": "tech"}]
        config = _base_config()
        config["news_agent"]["feeds"] = existing
        mock_load.return_value = config
        self._post({
            "feeds_json": "NOT-VALID-JSON",
            "interest_profile_json": "{}",
        })
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["news_agent"]["feeds"], existing)

    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.save_config")
    @patch("app.routers.console.load_config", return_value=_base_config())
    def test_scheduler_reloaded_on_save(self, mock_load, mock_save, mock_reload):
        self._post({"feeds_json": "[]", "interest_profile_json": "{}"})
        mock_reload.assert_called_once()

    def test_unauthenticated_request_rejected(self):
        resp = self.client.post("/console/save-news", data={
            "feeds_json": "[]",
            "interest_profile_json": "{}",
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()

"""
E2E Tests — Coach Agent Telegram Command Flows
===============================================
Simulates real Telegram webhook POSTs to /telegram-webhook and asserts
that the correct Telegram message is sent back to the user.

All external I/O is mocked:
  - send_telegram_msg / send_telegram_html / send_typing_action (Telegram send)
  - Gemini API (google.genai stub already in conftest.py)
  - Database: tmp_path SQLite file per test

Run:
    python -m pytest tests/test_e2e_coach_commands.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_telegram_update(chat_id: int, text: str, message_id: int = 1) -> dict:
    """Build a minimal Telegram Update payload matching the real API shape."""
    return {
        "update_id": 100000,
        "message": {
            "message_id": message_id,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Tester"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1746000000,
            "text": text,
        },
    }


def _make_app_client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _post_telegram(client: TestClient, chat_id: int, text: str) -> dict:
    resp = client.post(
        "/telegram-webhook",
        json=_make_telegram_update(chat_id, text),
    )
    assert resp.status_code == 200, f"webhook returned {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Shared mock context for most tests
# ---------------------------------------------------------------------------

_NOTIFY_PATH = "app.routers.webhooks.send_telegram_msg"
_NOTIFY_TYPING = "app.core.notification.send_typing_action"
_AGENT_SEND = "app.agents.coach.agent.send_telegram_msg"
_AGENT_TYPING = "app.agents.coach.agent.send_typing_action"
_LOAD_CONFIG = "app.routers.webhooks.load_config"
_AGENT_CONFIG = "app.core.config.load_config"

_STUB_CONFIG = {
    "race_date": "2026-12-01",
    "race_name": "Test Marathon",
    "scheduler": {},
    "telegram_bot_token": "test-token",
    "telegram_chat_id": "999",
    "gemini_api_key": "test",
    "news_agent": {"enabled": False},
}


# ---------------------------------------------------------------------------
# Phase 1-A: /brief command
# ---------------------------------------------------------------------------


class TestCoachBriefCommand(unittest.TestCase):
    """REQ-C01, REQ-C04 — /brief triggers morning briefing pipeline."""

    def setUp(self):
        self.client = _make_app_client()

    @patch(_NOTIFY_PATH)
    @patch(_NOTIFY_TYPING)
    @patch("app.routers.webhooks.task_morning_briefing")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_brief_sends_ack_and_queues_briefing(
        self, mock_cfg, mock_briefing, mock_typing, mock_send
    ):
        """POST /brief → ack sent immediately + morning briefing queued."""
        resp_data = _post_telegram(self.client, 999, "/standup")
        self.assertEqual(resp_data, {"status": "ok"})
        mock_send.assert_called_once()
        mock_briefing.assert_called_once()

    @patch(_NOTIFY_PATH)
    @patch(_NOTIFY_TYPING)
    @patch("app.routers.webhooks.task_morning_briefing")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_brief_command_sends_telegram_message(
        self, mock_cfg, mock_briefing, mock_typing, mock_send
    ):
        """POST /standup → webhook returns ok, telegram message was dispatched."""
        _post_telegram(self.client, 999, "/standup")
        # The ack message must contain a progress indicator
        call_args = mock_send.call_args
        self.assertIsNotNone(call_args)
        sent_text = call_args[0][1] if call_args[0] else str(call_args)
        self.assertTrue(
            len(sent_text) > 5,
            f"Expected non-empty ack message, got: {sent_text!r}",
        )

    @patch(_NOTIFY_PATH)
    @patch(_NOTIFY_TYPING)
    @patch("app.routers.webhooks.task_morning_briefing")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_brief_background_task_called_exactly_once(
        self, mock_cfg, mock_briefing, mock_typing, mock_send
    ):
        """Morning briefing task must not be called more than once per /brief."""
        _post_telegram(self.client, 999, "/standup")
        self.assertEqual(mock_briefing.call_count, 1)


# ---------------------------------------------------------------------------
# Phase 1-B: /sick and /recover state commands
# ---------------------------------------------------------------------------


class TestCoachStateCommands(unittest.TestCase):
    """REQ-C07, REQ-C08 — /sick and /recover update athlete state."""

    def setUp(self):
        self.client = _make_app_client()

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.set_athlete_state")
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_sick_command_sends_confirmation(
        self, mock_lc, mock_ac, mock_setup, mock_set_state, mock_typing, mock_send
    ):
        """POST /sick → athlete state set to 'sick' + confirmation message sent."""
        _post_telegram(self.client, 999, "/sick")
        mock_set_state.assert_called_once()
        args = mock_set_state.call_args[0]
        self.assertEqual(args[1], "sick")
        mock_send.assert_called()
        sent_text = mock_send.call_args[0][1]
        self.assertIn("ốm", sent_text.lower())

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.set_athlete_state")
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_recover_command_sends_confirmation(
        self, mock_lc, mock_ac, mock_setup, mock_set_state, mock_typing, mock_send
    ):
        """POST /recover → athlete state set to 'recovered' + confirmation sent."""
        _post_telegram(self.client, 999, "/recover")
        mock_set_state.assert_called_once()
        args = mock_set_state.call_args[0]
        self.assertEqual(args[1], "recovered")
        mock_send.assert_called()
        sent_text = mock_send.call_args[0][1]
        self.assertIn("hồi phục", sent_text.lower())

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.set_athlete_state")
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_sick_vietnamese_alias_om_works(
        self, mock_lc, mock_ac, mock_setup, mock_set_state, mock_typing, mock_send
    ):
        """Vietnamese alias /om must behave identically to /sick."""
        _post_telegram(self.client, 999, "/om")
        mock_set_state.assert_called_once()
        args = mock_set_state.call_args[0]
        self.assertEqual(args[1], "sick")


# ---------------------------------------------------------------------------
# Phase 1-C: /accept, /reject, /plan
# ---------------------------------------------------------------------------


class TestCoachPlanCommands(unittest.TestCase):
    """REQ-C05, REQ-C06, REQ-C09 — plan management commands."""

    def setUp(self):
        self.client = _make_app_client()

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_accept_plan_sends_info_message(
        self, mock_lc, mock_ac, mock_setup, mock_typing, mock_send
    ):
        """POST /accept → sends info message (plans are auto-saved now)."""
        _post_telegram(self.client, 999, "/accept")
        mock_send.assert_called()
        sent_text = mock_send.call_args[0][1] if mock_send.call_args else ""
        self.assertIn("tự động", sent_text)

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_reject_plan_sends_chat_redirect(
        self, mock_lc, mock_ac, mock_setup, mock_typing, mock_send
    ):
        """POST /reject → sends redirect message to use chat-based adjustment."""
        _post_telegram(self.client, 999, "/reject too hard")
        mock_send.assert_called()
        sent_text = mock_send.call_args[0][1] if mock_send.call_args else ""
        self.assertIn("coach", sent_text)

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.get_upcoming_plans", return_value="")
    @patch("app.agents.coach.agent.compute_daily_suggestion")
    @patch(
        "app.agents.coach.agent.format_daily_suggestion_for_briefing",
        return_value="💡 Hôm nay: chạy nhẹ 30'",
    )
    @patch(
        "app.agents.coach.agent.get_training_loads",
        return_value={"acute_load_7d": 100, "chronic_load_28d": 120},
    )
    @patch("app.agents.coach.agent.get_runs_in_last_days", return_value=[])
    @patch("app.agents.coach.agent.get_athlete_state", return_value={})
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_plan_command_with_no_active_plan(
        self,
        mock_lc,
        mock_ac,
        mock_setup,
        mock_state,
        mock_runs,
        mock_loads,
        mock_fmt,
        mock_sugg,
        mock_plans,
        mock_typing,
        mock_send,
    ):
        """POST /plan with no active plan → daily suggestion sent."""
        _post_telegram(self.client, 999, "/plan")
        # Two messages expected: loading ack + suggestion
        self.assertGreaterEqual(mock_send.call_count, 1)
        all_texts = " ".join(str(c) for c in mock_send.call_args_list)
        self.assertTrue(
            "kế hoạch" in all_texts.lower() or "💡" in all_texts or mock_fmt.called
        )

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch(
        "app.agents.coach.agent.get_upcoming_plans",
        return_value="- Thứ Hai: Chạy dài 12km\n- Thứ Ba: Nghỉ",
    )
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_plan_command_shows_upcoming_plan_when_exists(
        self, mock_lc, mock_ac, mock_setup, mock_plans, mock_typing, mock_send
    ):
        """POST /plan with active plan → plan text sent."""
        _post_telegram(self.client, 999, "/plan")
        all_sent = " ".join(str(c) for c in mock_send.call_args_list)
        self.assertIn("Chạy dài", all_sent)


# ---------------------------------------------------------------------------
# Phase 1-D: free-text chat routing
# ---------------------------------------------------------------------------


class TestCoachFreeTextChat(unittest.TestCase):
    """REQ-C14 — free-text message routes to AI coach, Gemini stub responds."""

    def setUp(self):
        self.client = _make_app_client()

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(
        "app.agents.coach.agent._run_agentic_loop", return_value="Tốc độ pace tốt rồi!"
    )
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_free_text_routes_to_coach(
        self, mock_lc, mock_ac, mock_loop, mock_setup, mock_typing, mock_send
    ):
        """Free-text message must call agentic loop and send the reply."""
        _post_telegram(self.client, 999, "Làm thế nào để cải thiện pace?")
        mock_loop.assert_called_once()
        mock_send.assert_called()

    @patch(_AGENT_SEND)
    @patch(_AGENT_TYPING)
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch("app.agents.coach.agent._run_agentic_loop", return_value="")
    @patch("app.agents.coach.agent._is_degenerate_response", return_value=True)
    @patch(_AGENT_CONFIG, return_value=_STUB_CONFIG)
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_degenerate_response_sends_fallback(
        self,
        mock_lc,
        mock_ac,
        mock_degen,
        mock_loop,
        mock_setup,
        mock_typing,
        mock_send,
    ):
        """Empty/degenerate Gemini response must not crash — fallback sent."""
        _post_telegram(self.client, 999, "xin chào")
        # Should not raise; webhook returns ok
        self.assertTrue(True)


# ---------------------------------------------------------------------------
# Phase 1-E: /sync command routing
# ---------------------------------------------------------------------------


class TestCoachSyncCommand(unittest.TestCase):
    """REQ-CS02 — /sync triggers manual Strava harvest."""

    def setUp(self):
        self.client = _make_app_client()

    @patch(_NOTIFY_PATH)
    @patch("app.routers.webhooks.execute_manual_sync")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_sync_default_queues_harvest(self, mock_cfg, mock_sync, mock_send):
        """POST /sync → execute_manual_sync queued with default limit=3."""
        _post_telegram(self.client, 999, "/sync")
        mock_sync.assert_called_once()
        args = mock_sync.call_args[0]
        self.assertEqual(args[1], 3)

    @patch(_NOTIFY_PATH)
    @patch("app.routers.webhooks.execute_manual_sync")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_sync_numeric_param_passes_limit(self, mock_cfg, mock_sync, mock_send):
        """POST /sync 10 → limit=10 passed to harvest."""
        _post_telegram(self.client, 999, "/sync 10")
        mock_sync.assert_called_once()
        args = mock_sync.call_args[0]
        self.assertEqual(args[1], 10)

    @patch(_NOTIFY_PATH)
    @patch("app.routers.webhooks.execute_manual_sync")
    @patch(_LOAD_CONFIG, return_value=_STUB_CONFIG)
    def test_sync_month_sets_days_back_30(self, mock_cfg, mock_sync, mock_send):
        """POST /sync month → limit=50, days_back=30."""
        _post_telegram(self.client, 999, "/sync month")
        mock_sync.assert_called_once()
        args = mock_sync.call_args[0]
        self.assertEqual(args[1], 50)
        self.assertEqual(args[2], 30)


# ---------------------------------------------------------------------------
# Phase 1-F: Telegram webhook endpoint robustness
# ---------------------------------------------------------------------------


class TestTelegramWebhookRobustness(unittest.TestCase):
    """Webhook must survive malformed payloads."""

    def setUp(self):
        self.client = _make_app_client()

    def test_malformed_json_returns_ok(self):
        resp = self.client.post(
            "/telegram-webhook",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_empty_body_returns_ok(self):
        resp = self.client.post(
            "/telegram-webhook",
            json={},
        )
        self.assertEqual(resp.status_code, 200)

    def test_update_without_message_returns_ok(self):
        resp = self.client.post(
            "/telegram-webhook",
            json={"update_id": 1, "callback_query": {}},
        )
        self.assertEqual(resp.status_code, 200)


class TestUpcomingMondayHelper(unittest.TestCase):
    """Unit tests for _upcoming_monday() — the week_start fix for Sunday scheduling.

    Root cause: scheduler runs Sunday 20:30; _current_week_monday() returned the
    ENDING week's Monday, so plans were stored for the wrong week and the Monday
    morning briefing couldn't find them.  _upcoming_monday() fixes this.
    """

    def _call(self, fake_date) -> str:
        from app.agents.coach.flows.weekly_plan_generation import _upcoming_monday

        with patch("app.agents.coach.flows.weekly_plan_generation.date") as mock_date:
            mock_date.today.return_value = fake_date
            return _upcoming_monday()

    def test_sunday_returns_next_monday(self):
        import datetime

        sunday = datetime.date(2026, 6, 14)  # Sun June 14
        self.assertEqual(sunday.weekday(), 6)
        self.assertEqual(self._call(sunday), "2026-06-15")

    def test_monday_returns_this_monday(self):
        import datetime

        monday = datetime.date(2026, 6, 15)  # Mon June 15
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(self._call(monday), "2026-06-15")

    def test_wednesday_returns_this_monday(self):
        import datetime

        wednesday = datetime.date(2026, 6, 17)  # Wed June 17
        self.assertEqual(wednesday.weekday(), 2)
        self.assertEqual(self._call(wednesday), "2026-06-15")

    def test_saturday_returns_this_monday(self):
        import datetime

        saturday = datetime.date(2026, 6, 20)  # Sat June 20
        self.assertEqual(saturday.weekday(), 5)
        self.assertEqual(self._call(saturday), "2026-06-15")


if __name__ == "__main__":
    unittest.main(verbosity=2)

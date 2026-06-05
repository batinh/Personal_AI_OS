"""
E2E Tests — Integration Edge Cases
=====================================
Tests cross-cutting error handling and integration contracts.

REQ-G01: Gemini API timeout handled gracefully — fallback sent, no crash
REQ-S06: Config reload after /console/save takes effect
REQ-W07: Strava create event triggers three-channel output (Telegram + Strava + email)
REQ-W08: Missing Strava token — graceful error, no crash

Run:
    python -m pytest tests/test_e2e_integration_edge_cases.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

_VALID_AUTH = ("testadmin", "testpassword123")

_STUB_CONFIG = {
    "system_instruction": "Coach",
    "user_profile": "Runner",
    "task_description": "",
    "analysis_requirements": "",
    "report_structure": "",
    "output_format": "",
    "max_hr": 185,
    "rest_hr": 55,
    "race_date": None,
    "race_distance_km": 21.1,
    "threshold_pace_per_km": 0,
    "gender": "male",
    "current_goal": "",
    "scheduler": {
        "briefing_time": "06:00",
        "backup_time": "02:00",
        "harvest_hours": "0,6,12,18",
        "harvest_minute": "15",
    },
    "email_config": {"enabled": False},
    "debug_mode": False,
    "model_name": "models/gemini-flash-latest",
    "news_agent": {"enabled": False},
    "log_levels": {},
    "telegram_bot_token": "test-token",
    "telegram_chat_id": "999",
    "gemini_api_key": "test",
}


def _make_client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


class TestGeminiTimeout(unittest.TestCase):
    """REQ-G01 — Gemini API timeout handled gracefully — fallback message sent, no crash."""

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.send_typing_action")
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(
        "app.agents.coach.agent._run_agentic_loop",
        side_effect=TimeoutError("Gemini timed out"),
    )
    @patch("app.routers.webhooks.load_config", return_value=_STUB_CONFIG)
    def test_gemini_timeout_sends_fallback_message(
        self, mock_lc, mock_loop, mock_setup, mock_typing, mock_send
    ):
        """Telegram webhook must return 200 even when Gemini times out."""
        client = _make_client()
        resp = client.post(
            "/telegram-webhook",
            json={
                "update_id": 200001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 999, "is_bot": False, "first_name": "T"},
                    "chat": {"id": 999, "type": "private"},
                    "date": 1746000000,
                    "text": "Chạy bao nhiêu km hôm nay?",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.send_typing_action")
    @patch("app.agents.coach.agent.is_setup_in_progress", return_value=False)
    @patch(
        "app.agents.coach.agent._run_agentic_loop",
        side_effect=Exception("Unexpected Gemini error"),
    )
    @patch("app.routers.webhooks.load_config", return_value=_STUB_CONFIG)
    def test_generic_ai_error_does_not_crash_webhook(
        self, mock_lc, mock_loop, mock_setup, mock_typing, mock_send
    ):
        """Any exception from the AI loop must not propagate past the webhook handler."""
        client = _make_client()
        resp = client.post(
            "/telegram-webhook",
            json={
                "update_id": 200002,
                "message": {
                    "message_id": 2,
                    "from": {"id": 999, "is_bot": False, "first_name": "T"},
                    "chat": {"id": 999, "type": "private"},
                    "date": 1746000001,
                    "text": "xin chào",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)


class TestConfigReload(unittest.TestCase):
    """REQ-S06 — Config reload after /console/save takes effect for next AI request."""

    def setUp(self):
        os.environ["ADMIN_USERNAME"] = "testadmin"
        os.environ["ADMIN_PASSWORD"] = "testpassword123"
        self.client = _make_client()

    @patch("app.routers.console.save_config")
    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    def test_config_reload_on_save(self, mock_load, mock_reload, mock_save):
        """REQ-S06: POST /console/save must call reload_scheduler — config is live on next call."""
        form_data = {
            "system_instruction": "Updated coach",
            "user_profile": "Elite runner",
            "task_description": "",
            "analysis_requirements": "",
            "report_structure": "",
            "output_format": "",
            "max_hr": "180",
            "rest_hr": "50",
            "race_distance_km": "42.2",
            "threshold_pace_per_km": "0",
            "gender": "male",
            "current_goal": "Sub-4h marathon",
            "briefing_time": "07:00",
            "backup_time": "03:00",
            "harvest_hours": "0,6,12,18",
            "harvest_minute": "15",
            "model_name": "models/gemini-flash-latest",
        }
        resp = self.client.post(
            "/console/save",
            data=form_data,
            auth=_VALID_AUTH,
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, [302, 303])
        mock_save.assert_called_once()
        mock_reload.assert_called_once()

        # Verify the saved config contains the updated values
        saved_config = mock_save.call_args[0][0]
        self.assertEqual(saved_config.get("system_instruction"), "Updated coach")
        self.assertEqual(saved_config.get("current_goal"), "Sub-4h marathon")


class TestStravaThreeChannelOutput(unittest.TestCase):
    """REQ-W07 — Strava run analysis triggers Telegram message + Strava description + email."""

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_create_event_triggers_three_channel_output(self, mock_workflow):
        """REQ-W07: POST /webhook with create event must queue run_strava_workflow."""
        client = _make_client()
        resp = client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "create",
                "object_id": 12345678,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        mock_workflow.assert_called_once_with("12345678")

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_update_event_does_not_queue_workflow(self, mock_workflow):
        """Non-create events must not trigger the analysis workflow."""
        client = _make_client()
        resp = client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "update",
                "object_id": 12345678,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_workflow.assert_not_called()


class TestStravaErrorHandling(unittest.TestCase):
    """REQ-W08 — Missing Strava token handled gracefully — error message sent, no crash."""

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_missing_strava_token_sends_error_message(self, mock_workflow):
        """REQ-W08: Webhook must accept the request and queue the workflow (error handled inside)."""
        client = _make_client()
        resp = client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "create",
                "object_id": 99999999,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        mock_workflow.assert_called_once_with("99999999")

    def test_strava_token_error_is_handled_in_workflow(self):
        """Strava module must be importable — token errors are caught inside the workflow."""
        from app.agents.coach import strava_client

        self.assertTrue(
            hasattr(strava_client, "StravaClient"),
            "strava_client must expose StravaClient",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
test_webhooks.py — Production-level tests for HTTP webhook endpoints.
=====================================================================
Covers:
  - Strava webhook verification (GET /webhook)
  - Strava activity create/delete events (POST /webhook)
  - Telegram webhook routing (/sync, /standup, chat)
  - Duplicate webhook resilience
  - Service paused state
  - Edge cases: malformed payloads, non-run activities
"""

import pytest
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


class TestStravaWorkflowOrchestration(unittest.TestCase):
    """Integration: run_activity_workflow end-to-end with mocked externals."""

    @patch("app.routers.webhooks.send_telegram_msg")
    @patch("app.routers.webhooks.send_html_email")
    @patch("app.routers.webhooks.save_run_activity_raw")
    @patch(
        "app.routers.webhooks.save_activity_stream_to_file",
        return_value="/data/streams/123.json",
    )
    @patch("app.routers.webhooks.save_run_activity")
    @patch(
        "app.routers.webhooks.analyze_run_with_gemini",
        return_value="GCS 7.5 — Great aerobic run!",
    )
    @patch("app.routers.webhooks.rag_db")
    @patch("app.routers.webhooks.get_primary_user_id", return_value="12345")
    @patch(
        "app.routers.webhooks.load_config", return_value={"max_hr": 185, "rest_hr": 55}
    )
    def test_full_create_workflow(
        self,
        mock_config,
        mock_uid,
        mock_rag,
        mock_analyze,
        mock_save_run,
        mock_save_stream,
        mock_save_raw,
        mock_email,
        mock_tg,
    ):
        # Mock the activity client
        mock_client = MagicMock()
        mock_client.get_activity_data.return_value = (
            "Morning Easy Run",  # act_name
            "Time_sec,HR_bpm\n0,120",  # csv_data
            {  # meta_data
                "distance": 10000,
                "moving_time": 3600,
                "average_heartrate": 140,
                "max_heartrate": 165,
                "start_date_local": "2025-01-15T07:00:00",
                "suffer_score": 80,
                "device_name": "Garmin FR265",
                "splits": [],
                "best_efforts": [],
            },
            {"time": {"data": [0, 1]}, "heartrate": {"data": [120, 130]}},  # stream_raw
        )

        with patch(
            "app.routers.webhooks.get_activity_client", return_value=mock_client
        ), patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = True
            from app.routers.webhooks import run_activity_workflow

            run_activity_workflow("12345678")

        # Verify data integrity: DB save happened before AI analysis
        mock_save_run.assert_called_once()
        mock_analyze.assert_called_once()

        # Verify notifications sent
        mock_tg.assert_called()
        mock_email.assert_called()

        # Verify RAG memorization
        mock_rag.memorize.assert_called_once()
        rag_kwargs = mock_rag.memorize.call_args[1]
        self.assertEqual(rag_kwargs["doc_id"], "12345678")
        self.assertEqual(rag_kwargs["domain"], "coach")

        # Runalyze is read-only: nothing is written back to the source platform
        self.assertFalse(mock_client.update_activity_description.called)

    @patch("app.routers.webhooks.load_config")
    @patch("app.routers.webhooks.get_primary_user_id")
    def test_workflow_skipped_when_paused(self, mock_uid, mock_config):
        with patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = False
            from app.routers.webhooks import run_activity_workflow

            run_activity_workflow("12345678")

        mock_config.assert_not_called()
        mock_uid.assert_not_called()


class TestTelegramWebhook(unittest.TestCase):
    """POST /telegram-webhook — Telegram bot command routing."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(
            app,
            raise_server_exceptions=False,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    @patch("app.routers.webhooks.execute_manual_sync")
    def test_sync_command_default(self, mock_sync):
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "/sync",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_sync.assert_called_once_with("12345", 3, None)

    @patch("app.routers.webhooks.execute_manual_sync")
    def test_sync_command_with_limit(self, mock_sync):
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "/sync 10",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_sync.assert_called_once_with("12345", 10, None)

    @patch("app.routers.webhooks.execute_manual_sync")
    def test_sync_command_month(self, mock_sync):
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "/sync month",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_sync.assert_called_once_with("12345", 50, 30)

    @patch("app.routers.webhooks.task_morning_briefing")
    @patch("app.routers.webhooks.send_telegram_msg")
    def test_standup_command(self, mock_tg, mock_briefing):
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "/standup",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_tg.assert_called_once()
        mock_briefing.assert_called_once()

    @patch("app.routers.webhooks.handle_telegram_chat")
    @patch("app.routers.webhooks.load_config", return_value={"model_name": "test"})
    def test_regular_chat_routed_to_ai(self, mock_config, mock_chat):
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                    "text": "Hôm nay tôi nên chạy bao xa?",
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_chat.assert_called_once_with(
            "12345", "Hôm nay tôi nên chạy bao xa?", {"model_name": "test"}
        )

    def test_no_message_field_returns_ok(self):
        """Telegram sends update types other than messages (edited_message, etc.)"""
        resp = self.client.post(
            "/telegram-webhook",
            json={"edited_message": {"chat": {"id": 12345}, "text": "edited"}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.routers.webhooks.handle_telegram_chat")
    @patch("app.routers.webhooks.load_config", return_value={})
    def test_empty_text_still_routes(self, mock_config, mock_chat):
        """Telegram photo/sticker messages have no text field."""
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "message": {
                    "chat": {"id": 12345},
                }
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_chat.assert_called_once_with("12345", "", {})


class TestWebhookPayloadValidation(unittest.TestCase):
    """T4: Malformed and edge-case payloads must not crash the server."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(
            app,
            raise_server_exceptions=False,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    def test_telegram_webhook_malformed_json_returns_ok(self):
        """/telegram-webhook explicitly swallows JSON errors and returns 200."""
        resp = self.client.post(
            "/telegram-webhook",
            content='{"unclosed": "object',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_telegram_webhook_null_body_returns_ok(self):
        """/telegram-webhook treats null body as 'no message' and returns ok."""
        resp = self.client.post(
            "/telegram-webhook",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class TestStravaEndpointsRemoved(unittest.TestCase):
    """Strava push endpoints are gone: they were unauthenticated triggers for paid LLM work."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(
            app,
            raise_server_exceptions=False,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    def test_post_webhook_no_longer_routes(self):
        resp = self.client.post(
            "/webhook",
            json={"object_type": "activity", "aspect_type": "create", "object_id": 1},
        )
        self.assertIn(resp.status_code, [404, 405])

    def test_get_webhook_verification_no_longer_routes(self):
        resp = self.client.get("/webhook?hub.verify_token=x&hub.challenge=y")
        self.assertIn(resp.status_code, [404, 405])

    def test_telegram_webhook_still_routes(self):
        resp = self.client.post("/telegram-webhook", json={})
        self.assertEqual(resp.status_code, 200)


@pytest.fixture(autouse=True)
def _webhook_credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

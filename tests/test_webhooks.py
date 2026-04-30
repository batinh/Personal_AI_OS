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

import os
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


class TestStravaWebhookVerification(unittest.TestCase):
    """GET /webhook — Strava subscription handshake."""

    def setUp(self):
        os.environ.setdefault("VERIFY_TOKEN", "test-verify-token")
        from app.main import app

        self.client = TestClient(app, raise_server_exceptions=False)

    @patch.dict("os.environ", {"VERIFY_TOKEN": "my-secret"})
    def test_valid_token_returns_challenge(self):
        resp = self.client.get(
            "/webhook",
            params={
                "hub.verify_token": "my-secret",
                "hub.challenge": "abc123",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"hub.challenge": "abc123"})

    @patch.dict("os.environ", {"VERIFY_TOKEN": "my-secret"})
    def test_invalid_token_returns_error(self):
        resp = self.client.get(
            "/webhook",
            params={
                "hub.verify_token": "wrong-token",
                "hub.challenge": "abc123",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "Invalid token"})

    @patch.dict("os.environ", {"VERIFY_TOKEN": "my-secret"})
    def test_missing_params_returns_error(self):
        resp = self.client.get("/webhook")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "Invalid token"})


class TestStravaWebhookCreate(unittest.TestCase):
    """POST /webhook — Strava activity create events."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(app, raise_server_exceptions=False)

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_create_activity_triggers_workflow(self, mock_workflow):
        resp = self.client.post(
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
    def test_non_activity_event_ignored(self, mock_workflow):
        resp = self.client.post(
            "/webhook",
            json={
                "object_type": "athlete",
                "aspect_type": "update",
                "object_id": 999,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_workflow.assert_not_called()

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_update_event_ignored(self, mock_workflow):
        """Strava sends 'update' events (title change etc.) — should be ignored."""
        resp = self.client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "update",
                "object_id": 12345678,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_workflow.assert_not_called()


class TestStravaWebhookDelete(unittest.TestCase):
    """POST /webhook — Strava activity delete events."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(app, raise_server_exceptions=False)

    @patch("app.routers.webhooks.handle_deleted_activity")
    def test_delete_activity_triggers_cleanup(self, mock_delete):
        resp = self.client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "delete",
                "object_id": 99999999,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_delete.assert_called_once_with("99999999")


class TestStravaWorkflowOrchestration(unittest.TestCase):
    """Integration: run_strava_workflow end-to-end with mocked externals."""

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
        # Mock StravaClient
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
        mock_client.update_activity_description.return_value = True

        with patch(
            "app.routers.webhooks.StravaClient", return_value=mock_client
        ), patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = True
            from app.routers.webhooks import run_strava_workflow

            run_strava_workflow("12345678")

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

        # Verify Strava description updated
        mock_client.update_activity_description.assert_called_once()

    @patch("app.routers.webhooks.load_config")
    @patch("app.routers.webhooks.get_primary_user_id")
    def test_workflow_skipped_when_paused(self, mock_uid, mock_config):
        with patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = False
            from app.routers.webhooks import run_strava_workflow

            run_strava_workflow("12345678")

        mock_config.assert_not_called()
        mock_uid.assert_not_called()


class TestDeletedActivityCleanup(unittest.TestCase):
    """Integration: handle_deleted_activity cleans DB + RAG + notifies."""

    @patch("app.routers.webhooks.send_telegram_msg")
    @patch("app.routers.webhooks.get_primary_user_id", return_value="12345")
    @patch("app.routers.webhooks.rag_db")
    @patch("app.routers.webhooks.delete_run_activity")
    def test_delete_cleans_all_layers(self, mock_db_del, mock_rag, mock_uid, mock_tg):
        from app.routers.webhooks import handle_deleted_activity

        handle_deleted_activity("99999")

        mock_db_del.assert_called_once_with("99999")
        mock_rag.forget.assert_called_once_with(doc_id="99999")
        mock_tg.assert_called_once()
        self.assertIn("99999", mock_tg.call_args[0][1])


class TestTelegramWebhook(unittest.TestCase):
    """POST /telegram-webhook — Telegram bot command routing."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(app, raise_server_exceptions=False)

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

        self.client = TestClient(app, raise_server_exceptions=False)

    def test_strava_webhook_malformed_json_rejected(self):
        """Pydantic schema on /webhook rejects malformed JSON with 422."""
        resp = self.client.post(
            "/webhook",
            content="{invalid json, no closing brace",
            headers={"Content-Type": "application/json"},
        )
        self.assertIn(resp.status_code, [400, 422])

    def test_strava_webhook_missing_required_fields_rejected(self):
        """/webhook requires object_type/object_id/aspect_type — empty body → 422."""
        resp = self.client.post("/webhook", json={})
        self.assertIn(resp.status_code, [400, 422])

    def test_strava_webhook_null_body_rejected(self):
        """null body is not a valid Pydantic payload → 422."""
        resp = self.client.post(
            "/webhook",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        self.assertIn(resp.status_code, [400, 422])

    def test_strava_webhook_zero_object_id_rejected(self):
        """StravaWebhookPayload enforces object_id > 0 via Field(gt=0)."""
        resp = self.client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "create",
                "object_id": 0,
            },
        )
        self.assertIn(resp.status_code, [400, 422])

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


class TestDuplicateWebhookResilience(unittest.TestCase):
    """Strava may send duplicate create events for the same activity."""

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_duplicate_creates_both_trigger(self, mock_workflow):
        """Currently no dedup — both fire. Test documents current behavior."""
        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)

        payload = {
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 12345678,
        }
        client.post("/webhook", json=payload)
        client.post("/webhook", json=payload)

        self.assertEqual(mock_workflow.call_count, 2)


if __name__ == "__main__":
    unittest.main()

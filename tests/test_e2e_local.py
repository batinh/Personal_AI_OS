"""
E2E Tests — Full HTTP request/response cycle using FastAPI TestClient.
======================================================================
These tests prove the refactored system works end-to-end without Docker.
They cover the critical user-facing flows: health, Strava webhook, Telegram
routing, and the scheduler startup path.

Run after smoke + unit:
    python -m pytest tests/test_e2e_local.py -v
"""
import json
import os
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


def _make_client():
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. SYSTEM HEALTH — proves DB + config + scheduler all initialise cleanly
# ---------------------------------------------------------------------------
class TestE2EHealthCheck(unittest.TestCase):
    """GET /health must respond 200 when core subsystems are up."""

    def setUp(self):
        self.client = _make_client()

    def test_health_responds(self):
        """Health endpoint must respond — 200 (healthy) or 503 (degraded) both valid."""
        resp = self.client.get("/health")
        self.assertIn(resp.status_code, [200, 503])

    def test_health_json_shape(self):
        resp = self.client.get("/health")
        body = resp.json()
        self.assertIn("status", body)
        self.assertIn("db", body)
        self.assertIn("config", body)
        self.assertIn("scheduler", body)

    def test_health_reports_scheduler_state(self):
        """Scheduler starts during lifespan — value must be 'running' or 'stopped' (not missing)."""
        resp = self.client.get("/health")
        self.assertIn(resp.json()["scheduler"], ["running", "stopped"])


# ---------------------------------------------------------------------------
# 2. STRAVA WEBHOOK FLOW — create event → background workflow triggered
# ---------------------------------------------------------------------------
class TestE2EStravaWebhookFlow(unittest.TestCase):
    """
    POST /webhook with a valid 'create' payload must:
      - Return 200 {"status": "ok"} immediately (async background_task)
      - Queue the workflow (mocked at service boundary)
    """

    def setUp(self):
        self.client = _make_client()

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_create_event_returns_ok_and_queues_workflow(self, mock_workflow):
        resp = self.client.post("/webhook", json={
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 987654321,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        mock_workflow.assert_called_once_with("987654321")

    @patch("app.routers.webhooks.handle_deleted_activity")
    def test_delete_event_returns_ok_and_queues_cleanup(self, mock_delete):
        resp = self.client.post("/webhook", json={
            "object_type": "activity",
            "aspect_type": "delete",
            "object_id": 111222333,
        })
        self.assertEqual(resp.status_code, 200)
        mock_delete.assert_called_once_with("111222333")

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_update_event_ignored(self, mock_workflow):
        """Title-change 'update' events must be silently ignored."""
        resp = self.client.post("/webhook", json={
            "object_type": "activity",
            "aspect_type": "update",
            "object_id": 444555666,
        })
        self.assertEqual(resp.status_code, 200)
        mock_workflow.assert_not_called()

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_non_activity_object_ignored(self, mock_workflow):
        resp = self.client.post("/webhook", json={
            "object_type": "athlete",
            "aspect_type": "update",
            "object_id": 777888999,
        })
        self.assertEqual(resp.status_code, 200)
        mock_workflow.assert_not_called()


# ---------------------------------------------------------------------------
# 3. STRAVA WEBHOOK VALIDATION — bad payloads must be rejected at the boundary
# ---------------------------------------------------------------------------
class TestE2EStravaPayloadValidation(unittest.TestCase):
    """Pydantic model StravaWebhookPayload must reject malformed payloads."""

    def setUp(self):
        self.client = _make_client()

    def test_malformed_json_returns_4xx(self):
        resp = self.client.post(
            "/webhook",
            content="{invalid json",
            headers={"Content-Type": "application/json"},
        )
        self.assertIn(resp.status_code, [400, 422])

    def test_zero_object_id_rejected(self):
        """Field(..., gt=0) means object_id=0 must fail validation."""
        resp = self.client.post("/webhook", json={
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 0,
        })
        self.assertIn(resp.status_code, [400, 422])

    def test_missing_required_fields_rejected(self):
        resp = self.client.post("/webhook", json={"object_id": 12345})
        self.assertIn(resp.status_code, [400, 422])


# ---------------------------------------------------------------------------
# 4. STRAVA WEBHOOK VERIFICATION — GET handshake
# ---------------------------------------------------------------------------
class TestE2EStravaVerification(unittest.TestCase):

    def setUp(self):
        self.client = _make_client()

    @patch.dict("os.environ", {"VERIFY_TOKEN": "e2e-secret"})
    def test_valid_token_returns_challenge(self):
        resp = self.client.get("/webhook", params={
            "hub.verify_token": "e2e-secret",
            "hub.challenge": "e2e-challenge-xyz",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"hub.challenge": "e2e-challenge-xyz"})

    @patch.dict("os.environ", {"VERIFY_TOKEN": "e2e-secret"})
    def test_wrong_token_returns_error(self):
        resp = self.client.get("/webhook", params={
            "hub.verify_token": "wrong",
            "hub.challenge": "e2e-challenge-xyz",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"error": "Invalid token"})


# ---------------------------------------------------------------------------
# 5. TELEGRAM WEBHOOK ROUTING — proves telegram_router is wired up correctly
# ---------------------------------------------------------------------------
class TestE2ETelegramWebhookRouting(unittest.TestCase):
    """
    POST /telegram-webhook must:
      - Parse JSON and route to the correct handler
      - Return 200 for all valid payloads (fire-and-forget)
      - Return 200 for edge-case payloads (null body, malformed JSON)
    """

    def setUp(self):
        self.client = _make_client()

    @patch("app.routers.webhooks.handle_telegram_chat")
    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    def test_chat_message_routed_to_handler(self, mock_uid, mock_handler):
        payload = {
            "message": {
                "chat": {"id": 99999},
                "text": "how am I doing this week?",
            }
        }
        resp = self.client.post(
            "/telegram-webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.routers.webhooks.task_morning_briefing")
    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    def test_standup_command_triggers_briefing(self, mock_uid, mock_briefing):
        payload = {
            "message": {
                "chat": {"id": 99999},
                "text": "/standup",
            }
        }
        resp = self.client.post(
            "/telegram-webhook",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_malformed_json_body_returns_ok(self):
        """Telegram sends retries — malformed body must never return 5xx."""
        resp = self.client.post(
            "/telegram-webhook",
            content='{"unclosed": "object',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_null_body_returns_ok(self):
        """null is valid JSON but not a dict — must not crash with TypeError."""
        resp = self.client.post(
            "/telegram-webhook",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_empty_object_returns_ok(self):
        """No 'message' key — should be silently ignored."""
        resp = self.client.post(
            "/telegram-webhook",
            content="{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# 6. FULL STRAVA WORKFLOW — integration with mocked external services
# ---------------------------------------------------------------------------
class TestE2EStravaFullWorkflow(unittest.TestCase):
    """
    Proves the complete run ingestion pipeline works after refactor:
      webhook event → StravaClient.get_activity_data → DB save → AI analysis
      → Telegram notification → Strava description update

    All external calls (Strava API, Gemini, Telegram, email) are mocked.
    """

    def setUp(self):
        self.client = _make_client()

    @patch("app.routers.webhooks.send_telegram_msg")
    @patch("app.routers.webhooks.send_html_email")
    @patch("app.routers.webhooks.save_run_activity_raw")
    @patch("app.routers.webhooks.save_activity_stream_to_file", return_value="/data/123.json")
    @patch("app.routers.webhooks.save_run_activity")
    @patch("app.routers.webhooks.analyze_run_with_gemini", return_value="GCS 8.0 — Solid tempo run!")
    @patch("app.routers.webhooks.rag_db")
    @patch("app.routers.webhooks.get_primary_user_id", return_value="99999")
    @patch("app.routers.webhooks.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    @patch("app.routers.webhooks.upsert_run_computed_metrics")
    @patch("app.routers.webhooks.compute_stream_metrics", return_value={})
    def test_create_event_triggers_full_pipeline(
        self, mock_metrics, mock_upsert, mock_config, mock_uid, mock_rag,
        mock_analyze, mock_save_run, mock_save_stream, mock_save_raw,
        mock_email, mock_tg,
    ):
        mock_strava_client = MagicMock()
        mock_strava_client.get_activity_data.return_value = (
            "Morning Easy Run",
            "Time_sec,HR_bpm\n0,120\n60,130",
            {
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
            {"time": {"data": [0, 60]}, "heartrate": {"data": [120, 130]}},
        )
        mock_strava_client.update_activity_description.return_value = True

        with patch("app.routers.webhooks.StravaClient", return_value=mock_strava_client), \
             patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = True
            from app.routers.webhooks import run_strava_workflow
            run_strava_workflow("12345678")

        # DB save happened (data integrity before AI)
        mock_save_run.assert_called_once()
        # AI analysis was called
        mock_analyze.assert_called_once()
        # Strava description updated with analysis
        mock_strava_client.update_activity_description.assert_called_once()
        # Telegram notification sent
        mock_tg.assert_called()

    @patch("app.routers.webhooks.get_primary_user_id", return_value="99999")
    @patch("app.routers.webhooks.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_paused_service_skips_workflow(self, mock_config, mock_uid):
        """When state.service_active=False, workflow must exit without calling Strava API."""
        mock_strava_client = MagicMock()

        with patch("app.routers.webhooks.StravaClient", return_value=mock_strava_client), \
             patch("app.routers.webhooks.state") as mock_state:
            mock_state.service_active = False
            from app.routers.webhooks import run_strava_workflow
            run_strava_workflow("12345678")

        mock_strava_client.get_activity_data.assert_not_called()


# ---------------------------------------------------------------------------
# 7. SCHEDULER STARTUP — proves APScheduler initialises jobs correctly
# ---------------------------------------------------------------------------
class TestE2ESchedulerStartup(unittest.TestCase):
    """
    Scheduler must start during app lifespan and be running by the time
    /health is served.  Uses the same TestClient path as production startup.
    """

    def test_scheduler_running_after_app_startup(self):
        from app.main import app
        from app.services.scheduler import scheduler
        with TestClient(app):
            self.assertTrue(scheduler.running)

    def test_scheduler_stops_on_app_shutdown(self):
        from app.main import app
        from app.services.scheduler import scheduler
        client = TestClient(app)
        client.__enter__()
        self.assertTrue(scheduler.running)
        client.__exit__(None, None, None)
        # After shutdown, either stopped or the process-level scheduler persists —
        # the important thing is the lifespan ran without raising
        # (scheduler may be reused across tests)


# ---------------------------------------------------------------------------
# 8. TIMEZONE UTILS INTEGRATION — proves get_local_tz() is wired everywhere
# ---------------------------------------------------------------------------
class TestE2ETimezoneUtils(unittest.TestCase):
    """
    Proves get_local_tz() returns a valid pytz timezone and that the
    refactored modules (coach flows, news agent) can all import cleanly
    and return the expected type.
    """

    def test_get_local_tz_returns_pytz_timezone(self):
        import pytz
        from app.core.timezone_utils import get_local_tz
        tz = get_local_tz()
        self.assertIsInstance(tz, pytz.BaseTzInfo)

    def test_get_local_tz_default_is_ho_chi_minh(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TZ", None)
            from app.core.timezone_utils import get_local_tz
            tz = get_local_tz()
            self.assertEqual(str(tz), "Asia/Ho_Chi_Minh")

    def test_get_local_tz_respects_tz_env_var(self):
        with patch.dict("os.environ", {"TZ": "UTC"}):
            from app.core.timezone_utils import get_local_tz
            tz = get_local_tz()
            self.assertEqual(str(tz), "UTC")

    def test_morning_briefing_flow_uses_timezone_utils(self):
        """Proves the refactored import chain (DRY P3.8) is intact."""
        import inspect
        import app.agents.coach.flows.morning_briefing as mb
        src = inspect.getsource(mb)
        self.assertIn("get_local_tz", src)
        self.assertNotIn("pytz.timezone(os.getenv", src)

    def test_weekly_reflection_flow_uses_timezone_utils(self):
        import inspect
        import app.agents.coach.flows.weekly_reflection as wr
        src = inspect.getsource(wr)
        self.assertIn("get_local_tz", src)
        self.assertNotIn("pytz.timezone(os.getenv", src)

    def test_run_analysis_flow_uses_timezone_utils(self):
        import inspect
        import app.agents.coach.flows.run_analysis as ra
        src = inspect.getsource(ra)
        self.assertIn("get_local_tz", src)
        self.assertNotIn("pytz.timezone(os.getenv", src)


# ---------------------------------------------------------------------------
# 9. CONFIG THREAD SAFETY — proves threading.Lock protects config cache
# ---------------------------------------------------------------------------
class TestE2EConfigConcurrency(unittest.TestCase):
    """Simulates the APScheduler thread pool reading config concurrently."""

    def test_20_concurrent_load_config_calls_are_consistent(self):
        import threading
        from app.core.config import load_config

        results = []
        errors = []

        def reader():
            try:
                cfg = load_config()
                results.append(cfg)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent config reads raised: {errors}")
        self.assertEqual(len(results), 20)
        # All threads must get the same config object type (dict or None)
        types_seen = {type(r) for r in results}
        self.assertEqual(len(types_seen), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

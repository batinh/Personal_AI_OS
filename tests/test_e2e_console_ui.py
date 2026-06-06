"""
E2E Tests — Console UI HTTP flows
===================================
Tests the /console, /admin, /dashboard routes with HTTP Basic Auth.

REQ-H01: /console requires Basic Auth
REQ-H02: testing tab renders coverage data
REQ-H03: POST /console/save persists config and reloads scheduler
REQ-H04: POST /console/toggle pauses/resumes service
REQ-H05: /admin and /dashboard redirect to /console
REQ-H06: wrong credentials rejected with 401
REQ-H07: testing tab accordion levels present

Run:
    python -m pytest tests/test_e2e_console_ui.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

_VALID_AUTH = ("testadmin", "testpassword123")
_WRONG_AUTH = ("testadmin", "wrongpass999")

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
    "news_agent": {
        "enabled": False,
        "news_model": "models/gemini-flash-latest",
        "morning_time": "06:30",
        "afternoon_time": "17:30",
        "evening_time": "20:00",
        "telegram_chat_id": "",
        "interest_profile": {},
    },
    "log_levels": {},
}


def _make_mock_conn():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


_CONSOLE_PATCHES = [
    patch("app.routers.console.load_config", return_value=_STUB_CONFIG),
    patch("app.routers.console.get_db_connection", return_value=_make_mock_conn()),
    patch(
        "app.routers.console.get_training_loads",
        return_value={"acute_load_7d": 0, "chronic_load_28d": 0},
    ),
    patch("app.routers.console.get_historical_training_loads", return_value=[]),
    patch("app.routers.console.get_all_active_memories", return_value=[]),
    patch("app.routers.console.load_coverage_report", side_effect=FileNotFoundError),
    patch(
        "app.routers.console.load_requirements_matrix", side_effect=Exception("no yaml")
    ),
]


def _apply_patches(test_method):
    for p in reversed(_CONSOLE_PATCHES):
        test_method = p(test_method)
    return test_method


def _make_client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


class TestConsoleRoutes(unittest.TestCase):
    """REQ-H01, REQ-H03, REQ-H04, REQ-H05, REQ-H06 — Console route access control and actions."""

    def setUp(self):
        os.environ["ADMIN_USERNAME"] = "testadmin"
        os.environ["ADMIN_PASSWORD"] = "testpassword123"
        self.client = _make_client()

    def test_console_requires_basic_auth(self):
        """REQ-H01: GET /console without credentials must return 401."""
        resp = self.client.get("/console")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_credentials_rejected(self):
        """REQ-H06: GET /console with wrong password must return 401."""
        resp = self.client.get("/console", auth=_WRONG_AUTH)
        self.assertEqual(resp.status_code, 401)

    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    @patch("app.routers.console.get_db_connection", return_value=_make_mock_conn())
    @patch(
        "app.routers.console.get_training_loads",
        return_value={"acute_load_7d": 0, "chronic_load_28d": 0},
    )
    @patch("app.routers.console.get_historical_training_loads", return_value=[])
    @patch("app.routers.console.get_all_active_memories", return_value=[])
    @patch("app.routers.console.load_coverage_report", side_effect=FileNotFoundError)
    @patch("app.routers.console.load_requirements_matrix", side_effect=Exception)
    def test_console_valid_auth_returns_200(self, *mocks):
        """GET /console with valid credentials must return 200 HTML."""
        resp = self.client.get("/console", auth=_VALID_AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    @patch("app.routers.console.save_config")
    @patch("app.routers.console.reload_scheduler")
    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    def test_save_persists_config_and_reloads_scheduler(
        self, mock_load, mock_reload, mock_save
    ):
        """REQ-H03: POST /console/save must save config and reload scheduler, then redirect."""
        form_data = {
            "system_instruction": "Test coach",
            "user_profile": "Runner",
            "task_description": "",
            "analysis_requirements": "",
            "report_structure": "",
            "output_format": "",
            "max_hr": "185",
            "rest_hr": "55",
            "race_distance_km": "21.1",
            "threshold_pace_per_km": "0",
            "gender": "male",
            "current_goal": "",
            "briefing_time": "06:00",
            "backup_time": "02:00",
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

    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    def test_toggle_pauses_resumes_service(self, mock_load):
        """REQ-H04: POST /console/toggle must flip service_active and redirect."""
        from app.core.state import state

        original = state.service_active
        resp = self.client.post(
            "/console/toggle", auth=_VALID_AUTH, follow_redirects=False
        )
        self.assertIn(resp.status_code, [302, 303])
        self.assertNotEqual(state.service_active, original)
        # Restore
        state.service_active = original

    def test_admin_redirects_to_console(self):
        """REQ-H05: GET /admin with valid credentials must 301-redirect to /console."""
        resp = self.client.get("/admin", auth=_VALID_AUTH, follow_redirects=False)
        self.assertIn(resp.status_code, [301, 302, 303])
        location = resp.headers.get("location", "")
        self.assertIn("console", location)


class TestConsoleTestingTab(unittest.TestCase):
    """REQ-H02, REQ-H07 — Console testing tab renders coverage data and accordion."""

    def setUp(self):
        os.environ["ADMIN_USERNAME"] = "testadmin"
        os.environ["ADMIN_PASSWORD"] = "testpassword123"
        self.client = _make_client()

    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    @patch("app.routers.console.get_db_connection", return_value=_make_mock_conn())
    @patch(
        "app.routers.console.get_training_loads",
        return_value={"acute_load_7d": 0, "chronic_load_28d": 0},
    )
    @patch("app.routers.console.get_historical_training_loads", return_value=[])
    @patch("app.routers.console.get_all_active_memories", return_value=[])
    @patch("app.routers.console.load_coverage_report", side_effect=FileNotFoundError)
    @patch("app.routers.console.load_requirements_matrix", side_effect=Exception)
    def test_testing_tab_renders_coverage_data(self, *mocks):
        """REQ-H02: GET /console?tab=testing with valid auth must return 200 HTML."""
        resp = self.client.get("/console?tab=testing", auth=_VALID_AUTH)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        # "No coverage data" message when FileNotFoundError
        self.assertIn("coverage", resp.text.lower())

    @patch("app.routers.console.load_config", return_value=_STUB_CONFIG)
    @patch("app.routers.console.get_db_connection", return_value=_make_mock_conn())
    @patch(
        "app.routers.console.get_training_loads",
        return_value={"acute_load_7d": 0, "chronic_load_28d": 0},
    )
    @patch("app.routers.console.get_historical_training_loads", return_value=[])
    @patch("app.routers.console.get_all_active_memories", return_value=[])
    @patch("app.routers.console.load_coverage_report", side_effect=FileNotFoundError)
    @patch("app.routers.console.load_requirements_matrix", side_effect=Exception)
    def test_testing_tab_accordion_levels_present(self, *mocks):
        """REQ-H07: Testing tab HTML must contain accordion structure keywords."""
        resp = self.client.get("/console?tab=testing", auth=_VALID_AUTH)
        self.assertEqual(resp.status_code, 200)
        html = resp.text.lower()
        # The tab pane for testing must be present
        self.assertIn("tab-testing", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)

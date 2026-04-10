"""
test_harvest.py — Production tests for harvest_data() and execute_manual_sync().
=================================================================================
Covers:
  - Cron harvest: fetch recent activities, filter runs, save to DB
  - Manual sync: limit/days_back params, RAG gap detection, dedup
  - build_activity_record: TRIMP calculation, field normalization
  - Error resilience: Strava API failures, empty responses
"""
import os
import unittest
from unittest.mock import patch, MagicMock, call

from app.agents.coach.harvest import build_activity_record


class TestBuildActivityRecord(unittest.TestCase):
    """build_activity_record normalizes Strava API response to DB schema."""

    def test_basic_conversion(self):
        raw = {
            "id": 12345678,
            "name": "Morning Easy Run",
            "start_date_local": "2025-01-15T07:00:00",
            "distance": 10000,      # meters
            "moving_time": 3600,     # seconds
            "average_heartrate": 140,
            "max_heartrate": 165,
            "suffer_score": 80,
        }
        result = build_activity_record(raw, max_hr=185, rest_hr=55)

        self.assertEqual(result["activity_id"], "12345678")
        self.assertEqual(result["name"], "Morning Easy Run")
        self.assertAlmostEqual(result["distance_km"], 10.0, places=2)
        self.assertAlmostEqual(result["moving_time_min"], 60.0, places=2)
        self.assertEqual(result["avg_hr"], 140)
        self.assertEqual(result["max_hr"], 165)
        self.assertEqual(result["suffer_score"], 80)
        self.assertGreater(result["trimp_score"], 0)
        self.assertIn("_trimp_data", result)

    def test_zero_distance(self):
        """Treadmill with 0 GPS distance should not crash."""
        raw = {
            "id": 99,
            "name": "Indoor Run",
            "distance": 0,
            "moving_time": 1800,
            "average_heartrate": 130,
            "max_heartrate": 150,
        }
        result = build_activity_record(raw)
        self.assertEqual(result["distance_km"], 0.0)

    def test_missing_optional_fields(self):
        """Strava API may omit suffer_score, max_heartrate etc."""
        raw = {"id": 100, "distance": 5000, "moving_time": 1500}
        result = build_activity_record(raw)

        self.assertEqual(result["suffer_score"], 0)
        self.assertEqual(result["max_hr"], 0)
        self.assertEqual(result["avg_hr"], 0)

    def test_none_suffer_score(self):
        """suffer_score can be None from Strava API."""
        raw = {
            "id": 101,
            "distance": 5000,
            "moving_time": 1500,
            "suffer_score": None,
        }
        result = build_activity_record(raw)
        self.assertEqual(result["suffer_score"], 0)

    def test_activity_id_from_activity_id_key(self):
        """Some contexts pass 'activity_id' instead of 'id'."""
        raw = {"activity_id": "ABC123", "distance": 5000, "moving_time": 1500}
        result = build_activity_record(raw)
        self.assertEqual(result["activity_id"], "ABC123")


class TestHarvestData(unittest.TestCase):
    """Cron harvest_data() integration with mocked Strava + DB."""

    @patch("app.agents.coach.harvest.get_db_connection")
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.upsert_user")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    @patch("app.agents.coach.harvest.get_primary_user_id", return_value="12345")
    @patch.dict("os.environ", {"STRAVA_ATHLETE_ID": "99999"})
    def test_harvest_saves_only_runs(self, mock_uid, mock_cfg, mock_init,
                                     mock_upsert, mock_save, mock_db):
        mock_client = MagicMock()
        mock_client.get_athlete_stats.return_value = None
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130, "start_date_local": "2025-01-15"},
            {"id": 2, "type": "Ride", "distance": 30000, "moving_time": 3600},
            {"id": 3, "type": "TrailRun", "distance": 8000, "moving_time": 3000,
             "average_heartrate": 145, "start_date_local": "2025-01-16"},
        ]

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client), \
             patch("builtins.open", MagicMock()):
            from app.agents.coach.harvest import harvest_data
            harvest_data()

        # Only Run and TrailRun should be saved (not Ride)
        self.assertEqual(mock_save.call_count, 2)

    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={})
    @patch("app.agents.coach.harvest.get_primary_user_id", return_value=None)
    def test_harvest_aborts_without_chat_id(self, mock_uid, mock_cfg, mock_init):
        """If no TELEGRAM_CHAT_ID, harvest should exit early."""
        mock_client = MagicMock()

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import harvest_data
            harvest_data()

        mock_client.get_recent_activities.assert_not_called()


class TestExecuteManualSync(unittest.TestCase):
    """Manual /sync command flow with RAG gap detection."""

    @patch("app.agents.coach.harvest.time.sleep")  # Don't actually sleep in tests
    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_sync_skips_existing_rag_memories(self, mock_cfg, mock_init,
                                               mock_save, mock_rag, mock_tg, mock_sleep):
        """Activities already in ChromaDB should skip re-analysis."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130, "start_date_local": "2025-01-15T07:00:00"},
        ]

        # RAG already has this activity
        mock_rag.collection.get.return_value = {"ids": ["1"]}

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=3)

        # DB should still be updated (UPSERT)
        mock_save.assert_called_once()
        # But Strava streams should NOT be fetched (skip expensive API call)
        mock_client.get_activity_data.assert_not_called()

    @patch("app.agents.coach.harvest.time.sleep")
    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity_raw")
    @patch("app.agents.coach.harvest.save_activity_stream_to_file", return_value="/data/streams/2.json")
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_sync_fetches_streams_for_missing_rag(self, mock_cfg, mock_init,
                                                   mock_save, mock_save_stream,
                                                   mock_save_raw, mock_rag,
                                                   mock_tg, mock_sleep):
        """Activities NOT in ChromaDB should fetch streams and memorize."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 2, "type": "Run", "distance": 10000, "moving_time": 3600,
             "average_heartrate": 140, "start_date_local": "2025-01-15T07:00:00"},
        ]
        mock_client.get_activity_data.return_value = (
            "Long Run", "Time_sec,HR_bpm\n0,140", {"distance": 10000}, {"time": {"data": [0]}}
        )

        # RAG does NOT have this activity
        mock_rag.collection.get.return_value = {"ids": []}

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=3)

        mock_client.get_activity_data.assert_called_once_with("2")
        mock_rag.memorize.assert_called_once()

    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={})
    def test_sync_with_no_activities_sends_warning(self, mock_cfg, mock_init, mock_tg):
        """When Strava returns empty list, user should be notified."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = []

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=3)

        # Should send both "syncing..." and "no activities" messages
        self.assertEqual(mock_tg.call_count, 2)

    @patch("app.agents.coach.harvest.time.sleep")
    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_sync_filters_non_run_activities(self, mock_cfg, mock_init,
                                              mock_save, mock_rag, mock_tg, mock_sleep):
        """Only Run/TrailRun/VirtualRun should be processed."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "Ride", "distance": 30000, "moving_time": 3600},
            {"id": 2, "type": "Swim", "distance": 1500, "moving_time": 1800},
        ]

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=5)

        mock_save.assert_not_called()

    @patch("app.agents.coach.harvest.time.sleep")
    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_sync_month_filters_by_date(self, mock_cfg, mock_init,
                                         mock_save, mock_rag, mock_tg, mock_sleep):
        """'/sync month' should filter activities within last 30 days."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130,
             "start_date_local": "2020-01-01T07:00:00"},  # Old: should be excluded
            {"id": 2, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130,
             "start_date_local": "2099-01-01T07:00:00"},  # Future: should be included
        ]
        mock_rag.collection.get.return_value = {"ids": ["1", "2"]}

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=50, days_back=30)

        # Only the future-dated activity should pass the date filter
        self.assertEqual(mock_save.call_count, 1)


class TestHarvestActivityTypes(unittest.TestCase):
    """Verify harvest includes VirtualRun and excludes non-run types."""

    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity_raw")
    @patch("app.agents.coach.harvest.save_activity_stream_to_file", return_value=None)
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_harvest_includes_virtual_run(self, mock_cfg, mock_init, mock_save,
                                          mock_save_stream, mock_save_raw,
                                          mock_rag, mock_tg):
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "VirtualRun", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 140, "start_date_local": "2025-01-15T07:00:00"},
        ]
        mock_client.get_activity_data.return_value = ("Virtual Run", None, None, None)
        mock_rag.collection.get.return_value = {"ids": []}

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=5)

        mock_save.assert_called_once()

    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity_raw")
    @patch("app.agents.coach.harvest.save_activity_stream_to_file", return_value=None)
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_save_failure_on_one_activity_continues_to_next(self, mock_cfg, mock_init,
                                                             mock_save, mock_save_stream,
                                                             mock_save_raw, mock_rag, mock_tg):
        """A DB error saving one activity should not abort processing the remaining ones."""
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": 1, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130, "start_date_local": "2025-01-15T07:00:00"},
            {"id": 2, "type": "Run", "distance": 6000, "moving_time": 1800,
             "average_heartrate": 135, "start_date_local": "2025-01-16T07:00:00"},
        ]
        mock_client.get_activity_data.return_value = ("Morning Run", None, None, None)
        mock_rag.collection.get.return_value = {"ids": []}
        mock_save.side_effect = [Exception("DB write failed"), None]

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=5)

        # Second activity should still have been attempted
        self.assertEqual(mock_save.call_count, 2)


class TestManualSyncRateLimiting(unittest.TestCase):
    """Verify time.sleep(1) is called between API calls to respect Strava rate limits."""

    @patch("app.agents.coach.harvest.time.sleep")
    @patch("app.agents.coach.harvest.send_telegram_msg")
    @patch("app.agents.coach.harvest.rag_db")
    @patch("app.agents.coach.harvest.save_run_activity_raw")
    @patch("app.agents.coach.harvest.save_activity_stream_to_file", return_value=None)
    @patch("app.agents.coach.harvest.save_run_activity")
    @patch("app.agents.coach.harvest.init_db")
    @patch("app.agents.coach.harvest.load_config", return_value={"max_hr": 185, "rest_hr": 55})
    def test_sleep_between_activities(self, mock_cfg, mock_init, mock_save,
                                      mock_save_stream, mock_save_raw,
                                      mock_rag, mock_tg, mock_sleep):
        mock_client = MagicMock()
        mock_client.get_recent_activities.return_value = [
            {"id": i, "type": "Run", "distance": 5000, "moving_time": 1500,
             "average_heartrate": 130, "start_date_local": "2025-01-15T07:00:00"}
            for i in range(3)
        ]
        mock_client.get_activity_data.return_value = ("Run", None, None, None)
        mock_rag.collection.get.return_value = {"ids": []}

        with patch("app.agents.coach.harvest.StravaClient", return_value=mock_client):
            from app.agents.coach.harvest import execute_manual_sync
            execute_manual_sync("12345", limit=5)

        # time.sleep(1) should be called for each activity that triggers stream fetch
        self.assertEqual(mock_sleep.call_count, 3)
        mock_sleep.assert_called_with(1)


if __name__ == "__main__":
    unittest.main()

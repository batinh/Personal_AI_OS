"""
Tests for run_activity_raw: stream_file_path storage and retrieval.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import database


class TestRunActivityRawStreamFilePath(unittest.TestCase):
    """Test save_run_activity_raw and get_run_activity_raw with stream_file_path."""

    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.addCleanup(lambda: os.unlink(self.tmp_db.name))
        with patch.object(database, "DB_PATH", self.tmp_db.name):
            database.init_db()

    def _patch_db(self):
        return patch.object(database, "DB_PATH", self.tmp_db.name)

    def test_save_and_get_run_activity_raw_with_stream_file_path(self):
        user_id = "user1"
        activity_id = "act1"
        activity_name = "Morning Run"
        full_meta = {
            "start_date_local": "2026-03-14T06:00:00",
            "moving_time": 3600,
            "average_heartrate": 150,
            "distance": 10000,
            "splits": [{"km": 1, "pace": 3.5, "hr": 145}],
        }
        stream_file_path = "streams/user1/act1.json"

        with self._patch_db():
            database.save_run_activity(user_id, {
                "activity_id": activity_id,
                "name": activity_name,
                "start_date": full_meta["start_date_local"],
                "distance_km": 10.0,
                "moving_time_min": 60.0,
                "avg_hr": 150,
                "max_hr": 165,
                "suffer_score": 100,
                "trimp_score": 50.0,
            })
            database.save_run_activity_raw(
                user_id,
                activity_id,
                activity_name,
                full_meta,
                stream_csv="",
                stream_file_path=stream_file_path,
            )
            raw = database.get_run_activity_raw(activity_id)

        self.assertIsNotNone(raw)
        self.assertEqual(raw["activity_name"], activity_name)
        self.assertEqual(raw["full_meta"]["start_date_local"], full_meta["start_date_local"])
        self.assertEqual(raw["full_meta"]["splits"], full_meta["splits"])
        self.assertEqual(raw["stream_file_path"], stream_file_path)
        self.assertEqual(raw["stream_csv"], "")

    def test_get_run_activity_raw_returns_none_for_unknown_activity(self):
        with self._patch_db():
            raw = database.get_run_activity_raw("nonexistent_id")
        self.assertIsNone(raw)

    def test_save_run_activity_raw_upserts_and_updates_stream_file_path(self):
        user_id = "u2"
        activity_id = "a2"
        with self._patch_db():
            database.save_run_activity(user_id, {
                "activity_id": activity_id,
                "name": "Run",
                "start_date": "2026-03-14T07:00:00",
                "distance_km": 5.0,
                "moving_time_min": 30.0,
                "avg_hr": 140,
                "max_hr": 155,
                "suffer_score": 50,
                "trimp_score": 25.0,
            })
            database.save_run_activity_raw(
                user_id, activity_id, "Run", {"distance": 5000}, stream_file_path="streams/u2/a2_v1.json"
            )
            raw1 = database.get_run_activity_raw(activity_id)
            database.save_run_activity_raw(
                user_id, activity_id, "Run Updated", {"distance": 5000}, stream_file_path="streams/u2/a2_v2.json"
            )
            raw2 = database.get_run_activity_raw(activity_id)
        self.assertEqual(raw1["stream_file_path"], "streams/u2/a2_v1.json")
        self.assertEqual(raw2["stream_file_path"], "streams/u2/a2_v2.json")
        self.assertEqual(raw2["activity_name"], "Run Updated")


if __name__ == "__main__":
    unittest.main()

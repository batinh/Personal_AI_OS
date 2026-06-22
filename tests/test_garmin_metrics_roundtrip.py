"""
Garmin daily metrics — upsert/get round-trip + schema reconcile.
================================================================
Regression coverage for the schema-drift bug where upsert silently failed
(missing/renamed columns) and reads returned None despite "sync success".

Each test uses an isolated temp DB via init_db().
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core import database

_USER = "7939957821"
_DATE = "2026-06-22"

_FULL_METRICS = {
    "training_readiness_score": 72,
    "hrv_status": "BALANCED",
    "hrv_weekly_avg": 58.0,
    "hrv_last_night": 61.0,
    "sleep_score": 84,
    "sleep_duration_sec": 27000,
    "deep_sleep_sec": 5400,
    "body_battery_morning": 90,
    "body_battery_evening": 18,
    "resting_hr": 49,
    "stress_avg": 32,
    "training_status": "PRODUCTIVE",
    "daily_steps": 8421,
    "spo2_avg": 96.0,
}


class _TempDbMixin:
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)


class TestGarminRoundTrip(_TempDbMixin, unittest.TestCase):
    def test_upsert_then_get_returns_all_fields(self):
        database.upsert_garmin_daily_metrics(_USER, _DATE, _FULL_METRICS)
        row = database.get_garmin_daily_metrics(_USER, _DATE)

        self.assertIsNotNone(row)
        for key, value in _FULL_METRICS.items():
            self.assertEqual(row[key], value, f"mismatch on {key}")

    def test_get_includes_created_at_timestamp(self):
        database.upsert_garmin_daily_metrics(_USER, _DATE, {"resting_hr": 50})
        row = database.get_garmin_daily_metrics(_USER, _DATE)
        self.assertIn("created_at", row)
        self.assertIsNotNone(row["created_at"])

    def test_partial_metrics_leave_other_columns_null(self):
        database.upsert_garmin_daily_metrics(
            _USER, _DATE, {"training_readiness_score": 40}
        )
        row = database.get_garmin_daily_metrics(_USER, _DATE)
        self.assertEqual(row["training_readiness_score"], 40)
        self.assertIsNone(row["hrv_last_night"])

    def test_upsert_is_idempotent_on_conflict(self):
        database.upsert_garmin_daily_metrics(_USER, _DATE, {"resting_hr": 50})
        database.upsert_garmin_daily_metrics(_USER, _DATE, {"resting_hr": 47})

        with sqlite3.connect(self._tmp.name) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM garmin_daily_metrics WHERE user_id=? AND date=?",
                (_USER, _DATE),
            ).fetchone()[0]
        self.assertEqual(count, 1)

        row = database.get_garmin_daily_metrics(_USER, _DATE)
        self.assertEqual(row["resting_hr"], 47)

    def test_raw_json_preserves_full_payload(self):
        import json

        database.upsert_garmin_daily_metrics(_USER, _DATE, _FULL_METRICS)
        row = database.get_garmin_daily_metrics(_USER, _DATE)
        self.assertEqual(json.loads(row["raw_json"]), _FULL_METRICS)

    def test_get_missing_date_returns_none(self):
        self.assertIsNone(database.get_garmin_daily_metrics(_USER, "1999-01-01"))


class TestStaleFallback(_TempDbMixin, unittest.TestCase):
    def test_falls_back_to_recent_row_within_window(self):
        database.upsert_garmin_daily_metrics(_USER, "2026-06-20", {"resting_hr": 51})
        # Asking for the 22nd with a 3-day window should find the 20th.
        row = database.get_garmin_daily_metrics(_USER, "2026-06-22", max_stale_days=3)
        self.assertIsNotNone(row)
        self.assertEqual(row["resting_hr"], 51)

    def test_no_fallback_when_window_too_small(self):
        database.upsert_garmin_daily_metrics(_USER, "2026-06-18", {"resting_hr": 51})
        row = database.get_garmin_daily_metrics(_USER, "2026-06-22", max_stale_days=2)
        self.assertIsNone(row)

    def test_exact_date_preferred_over_stale(self):
        database.upsert_garmin_daily_metrics(_USER, "2026-06-21", {"resting_hr": 60})
        database.upsert_garmin_daily_metrics(_USER, "2026-06-22", {"resting_hr": 48})
        row = database.get_garmin_daily_metrics(_USER, "2026-06-22", max_stale_days=3)
        self.assertEqual(row["resting_hr"], 48)


class TestSchemaReconcile(_TempDbMixin, unittest.TestCase):
    def test_migrates_legacy_table_missing_columns(self):
        """A table created with an old minimal schema gets missing columns added."""
        with sqlite3.connect(self._tmp.name) as conn:
            conn.execute("DROP TABLE IF EXISTS garmin_daily_metrics")
            conn.execute("""
                CREATE TABLE garmin_daily_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    training_readiness_score INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date)
                )
                """)

        # upsert must reconcile the schema and persist the full metric set.
        database.upsert_garmin_daily_metrics(_USER, _DATE, _FULL_METRICS)
        row = database.get_garmin_daily_metrics(_USER, _DATE)
        self.assertEqual(row["hrv_last_night"], 61.0)
        self.assertEqual(row["spo2_avg"], 96.0)


if __name__ == "__main__":
    unittest.main()

"""run_activities.source column, duplicate detection and source-filtered listing."""

import os
import tempfile
import unittest
from unittest.mock import patch

from app.core import database

_USER = "u1"


def _run(activity_id, start="2026-06-26T18:43:50Z", km=7.02, **kw):
    return {
        "activity_id": activity_id,
        "name": "Run",
        "start_date": start,
        "distance_km": km,
        "moving_time_min": 43.0,
        "avg_hr": 150,
        "max_hr": 170,
        "suffer_score": 0,
        "trimp_score": 80.0,
        **kw,
    }


class _TempDb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        patcher = patch.object(database, "DB_PATH", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.unlink, self._tmp.name)
        database.init_db()
        database.upsert_user(user_id=_USER, name="n", max_hr=185, rest_hr=55)


class TestSourceColumn(_TempDb):
    def _source(self, activity_id):
        with database.get_db() as conn:
            return conn.execute(
                "SELECT source FROM run_activities WHERE activity_id=?", (activity_id,)
            ).fetchone()["source"]

    def test_defaults_to_strava_when_not_given(self):
        database.save_run_activity(_USER, _run("1"))
        self.assertEqual(self._source("1"), "strava")

    def test_stores_given_source(self):
        database.save_run_activity(_USER, _run("2", source="runalyze"))
        self.assertEqual(self._source("2"), "runalyze")

    def test_upsert_does_not_overwrite_source(self):
        database.save_run_activity(_USER, _run("3", source="strava"))
        database.save_run_activity(_USER, _run("3", source="runalyze"))
        self.assertEqual(self._source("3"), "strava")


class TestHasRunActivity(_TempDb):
    def test_true_only_for_stored_ids(self):
        database.save_run_activity(_USER, _run("10"))
        self.assertTrue(database.has_run_activity("10"))
        self.assertFalse(database.has_run_activity("11"))


class TestFindDuplicateRun(_TempDb):
    def setUp(self):
        super().setUp()
        database.save_run_activity(_USER, _run("strava-1"))

    def test_finds_same_run_under_another_id(self):
        found = database.find_duplicate_run(_USER, "2026-06-26T18:44:10Z", 7.0, "rz-1")
        self.assertEqual(found, "strava-1")

    def test_ignores_run_far_in_time(self):
        self.assertIsNone(
            database.find_duplicate_run(_USER, "2026-06-26T19:30:00Z", 7.02, "rz-1")
        )

    def test_ignores_run_with_different_distance(self):
        self.assertIsNone(
            database.find_duplicate_run(_USER, "2026-06-26T18:43:50Z", 10.0, "rz-1")
        )

    def test_excludes_own_id(self):
        self.assertIsNone(
            database.find_duplicate_run(_USER, "2026-06-26T18:43:50Z", 7.02, "strava-1")
        )

    def test_scoped_to_user(self):
        database.upsert_user(user_id="u2", name="n", max_hr=185, rest_hr=55)
        self.assertIsNone(
            database.find_duplicate_run("u2", "2026-06-26T18:43:50Z", 7.02, "rz-1")
        )

    def test_none_for_missing_start_or_zero_distance(self):
        self.assertIsNone(database.find_duplicate_run(_USER, None, 7.02, "x"))
        self.assertIsNone(
            database.find_duplicate_run(_USER, "2026-06-26T18:43:50Z", 0, "x")
        )


class TestListBySource(_TempDb):
    def test_source_filter_returns_only_that_source(self):
        database.save_run_activity(_USER, _run("s1", start="2026-06-20T06:00:00Z"))
        database.save_run_activity(
            _USER, _run("r1", start="2026-06-21T06:00:00Z", source="runalyze")
        )
        both = database.list_run_activity_ids_in_date_range(
            _USER, "2026-06-01", "2026-06-30"
        )
        only = database.list_run_activity_ids_in_date_range(
            _USER, "2026-06-01", "2026-06-30", source="runalyze"
        )
        self.assertEqual({r["activity_id"] for r in both}, {"s1", "r1"})
        self.assertEqual([r["activity_id"] for r in only], ["r1"])


class TestRunTotalsAndLatestDate(_TempDb):
    def test_latest_run_date_none_when_empty(self):
        self.assertIsNone(database.get_latest_run_date(_USER))

    def test_latest_run_date_is_newest_day(self):
        database.save_run_activity(_USER, _run("a", start="2026-06-20T06:00:00Z"))
        database.save_run_activity(_USER, _run("b", start="2026-06-26T18:43:50Z"))
        self.assertEqual(database.get_latest_run_date(_USER), "2026-06-26")

    def test_totals_sum_distance_and_scope_to_user(self):
        database.save_run_activity(
            _USER, _run("a", start="2020-01-01T06:00:00Z", km=10)
        )
        database.save_run_activity(
            _USER, _run("b", start="2020-02-01T06:00:00Z", km=5.5)
        )
        totals = database.get_run_totals_km(_USER)
        self.assertEqual(totals["all_run_totals"], 15.5)
        self.assertEqual(totals["recent_run_totals"], 0)
        self.assertEqual(database.get_run_totals_km("nobody")["all_run_totals"], 0)

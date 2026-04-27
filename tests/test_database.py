"""
Layer 2 – Database CRUD Tests: app/core/database.py
=====================================================
Mỗi test class dùng một temporary SQLite file riêng → hoàn toàn isolated.
Không cần network, không cần Gemini API.
Covers: Users, RunActivities, ChatHistory, TrainingPlans, WeeklyTargets, CoreMemory.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from app.core import database


# ─────────────────────────────────────────────────────────────────────────────
# Base class: spin up a fresh DB for every test class
# ─────────────────────────────────────────────────────────────────────────────
class _TempDbMixin:
    """Provide a patched DB_PATH backed by a temp file."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patcher = patch.object(database, "DB_PATH", self._tmp.name)
        self._patcher.start()
        database.init_db()

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._tmp.name)


# ══════════════════════════════════════════════════════════════════════════════
# 1. USERS
# ══════════════════════════════════════════════════════════════════════════════
class TestUsers(_TempDbMixin, unittest.TestCase):

    def test_upsert_and_get_user(self):
        database.upsert_user("u1", name="Tín", max_hr=190, rest_hr=48)
        user = database.get_user("u1")
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "Tín")
        self.assertEqual(user["max_hr"], 190)
        self.assertEqual(user["rest_hr"], 48)

    def test_upsert_updates_existing_user(self):
        database.upsert_user("u1", name="Old Name")
        database.upsert_user("u1", name="New Name", max_hr=195)
        user = database.get_user("u1")
        self.assertEqual(user["name"], "New Name")
        self.assertEqual(user["max_hr"], 195)

    def test_get_nonexistent_user_returns_none(self):
        self.assertIsNone(database.get_user("ghost"))


# ══════════════════════════════════════════════════════════════════════════════
# 2. RUN ACTIVITIES
# ══════════════════════════════════════════════════════════════════════════════
class TestRunActivities(_TempDbMixin, unittest.TestCase):

    _BASE_ACTIVITY = {
        "activity_id": "act1",
        "name": "Morning Run",
        "start_date": "2026-03-22T06:00:00",
        "distance_km": 10.0,
        "moving_time_min": 60.0,
        "avg_hr": 148,
        "max_hr": 168,
        "suffer_score": 80,
        "trimp_score": 55.0,
    }

    def test_save_and_retrieve_via_recent_logs(self):
        database.save_run_activity("u1", self._BASE_ACTIVITY)
        log = database.get_recent_runs_log("u1", limit=5)
        self.assertIn("Morning Run", log)
        self.assertIn("10.0km", log)

    def test_upsert_does_not_duplicate(self):
        database.save_run_activity("u1", self._BASE_ACTIVITY)
        database.save_run_activity("u1", self._BASE_ACTIVITY)  # second upsert
        log = database.get_recent_runs_log("u1", limit=10)
        self.assertEqual(log.count("act1"), 0)   # activity_id not in display
        self.assertEqual(log.count("Morning Run"), 1)  # exactly 1 row

    def test_gcs_score_update(self):
        database.save_run_activity("u1", self._BASE_ACTIVITY)
        database.update_run_gcs_score("act1", "u1", 87)
        log = database.get_recent_runs_log("u1")
        self.assertIn("GCS: 87%", log)

    def test_gcs_placeholder_created_before_harvest(self):
        # update_run_gcs_score should INSERT a placeholder row if activity not yet harvested
        database.update_run_gcs_score("orphan_act", "u1", 72)
        log = database.get_recent_runs_log("u1")
        self.assertIn("GCS: 72%", log)

    def test_delete_run_activity(self):
        database.save_run_activity("u1", self._BASE_ACTIVITY)
        database.delete_run_activity("act1")
        log = database.get_recent_runs_log("u1")
        self.assertNotIn("Morning Run", log)

    def test_no_runs_returns_empty_message(self):
        log = database.get_recent_runs_log("nobody")
        self.assertIn("No recent runs", log)

    def test_get_training_loads_empty_user_returns_zeros(self):
        loads = database.get_training_loads("empty_user")
        self.assertEqual(loads["acute_load_7d"], 0)
        self.assertEqual(loads["chronic_load_28d"], 0)
        self.assertEqual(loads["avg_weekly_mileage"], 0)

    def test_get_training_loads_calculates_correctly(self):
        # Insert a run 3 days ago (acute) and a run 20 days ago (chronic only)
        recent = {**self._BASE_ACTIVITY, "activity_id": "act_recent",
                  "start_date": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT06:00:00"),
                  "trimp_score": 60.0, "distance_km": 10.0}
        old = {**self._BASE_ACTIVITY, "activity_id": "act_old",
               "start_date": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%dT06:00:00"),
               "trimp_score": 80.0, "distance_km": 12.0}
        database.save_run_activity("u1", recent)
        database.save_run_activity("u1", old)

        loads = database.get_training_loads("u1")
        self.assertEqual(loads["acute_load_7d"], 60.0)   # Only recent run
        self.assertEqual(loads["chronic_load_28d"], 140.0)  # Both runs
        self.assertAlmostEqual(loads["avg_weekly_mileage"], (10.0 + 12.0) / 4, places=1)

    def test_get_weekly_volume_only_counts_current_week(self):
        tz = pytz.timezone("Asia/Ho_Chi_Minh")
        now = datetime.now(tz)
        monday = now - timedelta(days=now.weekday())
        in_week = {**self._BASE_ACTIVITY, "activity_id": "in_week",
                   "start_date": monday.strftime("%Y-%m-%dT08:00:00"),
                   "distance_km": 8.0}
        last_week = {**self._BASE_ACTIVITY, "activity_id": "last_week",
                     "start_date": (monday - timedelta(days=2)).strftime("%Y-%m-%dT08:00:00"),
                     "distance_km": 15.0}
        database.save_run_activity("u1", in_week)
        database.save_run_activity("u1", last_week)
        vol = database.get_weekly_volume("u1")
        self.assertEqual(vol, 8.0)

    def test_get_runs_in_last_days_format(self):
        recent = {**self._BASE_ACTIVITY, "activity_id": "act_today",
                  "start_date": datetime.now().strftime("%Y-%m-%dT07:00:00")}
        database.save_run_activity("u1", recent)
        log = database.get_runs_in_last_days("u1", days=7)
        self.assertIn("Morning Run", log)
        self.assertIn("10.0km", log)


# ══════════════════════════════════════════════════════════════════════════════
# 3. CHAT HISTORY
# ══════════════════════════════════════════════════════════════════════════════
class TestChatHistory(_TempDbMixin, unittest.TestCase):

    def test_save_and_load_messages(self):
        database.save_message("u1", "user", "Hello coach!")
        database.save_message("u1", "model", "Hi runner!")
        history = database.load_history_for_gemini("u1", limit=10)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["parts"][0], "Hello coach!")
        self.assertEqual(history[1]["role"], "model")

    def test_load_respects_limit(self):
        for i in range(15):
            database.save_message("u1", "user", f"msg {i}")
        history = database.load_history_for_gemini("u1", limit=5)
        self.assertEqual(len(history), 5)

    def test_load_returns_chronological_order(self):
        database.save_message("u1", "user", "first")
        database.save_message("u1", "model", "second")
        database.save_message("u1", "user", "third")
        history = database.load_history_for_gemini("u1")
        texts = [h["parts"][0] for h in history]
        self.assertEqual(texts, ["first", "second", "third"])

    def test_clear_history_removes_all(self):
        database.save_message("u1", "user", "to be cleared")
        database.clear_history("u1")
        history = database.load_history_for_gemini("u1")
        self.assertEqual(history, [])

    def test_clear_history_only_affects_target_user(self):
        database.save_message("u1", "user", "user1 msg")
        database.save_message("u2", "user", "user2 msg")
        database.clear_history("u1")
        self.assertEqual(len(database.load_history_for_gemini("u1")), 0)
        self.assertEqual(len(database.load_history_for_gemini("u2")), 1)

    def test_empty_history_returns_empty_list(self):
        result = database.load_history_for_gemini("nobody")
        self.assertEqual(result, [])


# ══════════════════════════════════════════════════════════════════════════════
# 4. TRAINING PLANS
# ══════════════════════════════════════════════════════════════════════════════
class TestTrainingPlans(_TempDbMixin, unittest.TestCase):

    def test_create_and_get_plan(self):
        database.update_daily_plan("u1", "2026-03-25", "Easy Run", "30 min Z2", "Pending")
        plan = database.get_plan_for_date("u1", "2026-03-25")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["workout_title"], "Easy Run")
        self.assertEqual(plan["status"], "Pending")

    def test_upsert_plan_updates_existing(self):
        database.update_daily_plan("u1", "2026-03-25", "Easy Run", "30 min", "Pending")
        database.update_daily_plan("u1", "2026-03-25", "Rest Day", "Recovery", "Pending")
        plan = database.get_plan_for_date("u1", "2026-03-25")
        self.assertEqual(plan["workout_title"], "Rest Day")

    def test_update_plan_status(self):
        database.update_daily_plan("u1", "2026-03-25", "Long Run", "20km", "Pending")
        database.update_plan_status("u1", "2026-03-25", "Completed")
        plan = database.get_plan_for_date("u1", "2026-03-25")
        self.assertEqual(plan["status"], "Completed")

    def test_get_plan_for_nonexistent_date_returns_none(self):
        self.assertIsNone(database.get_plan_for_date("u1", "2099-01-01"))

    def test_get_upcoming_plans_returns_formatted_string(self):
        database.update_daily_plan("u1", "2099-01-01", "Future Run", "Test", "Pending")
        # upcoming plans from today → won't include far-future, but at least no crash
        result = database.get_upcoming_plans("u1", limit_days=7)
        self.assertIsInstance(result, str)

    def test_plans_are_user_isolated(self):
        database.update_daily_plan("u1", "2026-03-25", "Run A", "desc", "Pending")
        self.assertIsNone(database.get_plan_for_date("u2", "2026-03-25"))


# ══════════════════════════════════════════════════════════════════════════════
# 5. WEEKLY TARGETS
# ══════════════════════════════════════════════════════════════════════════════
class TestWeeklyTargets(_TempDbMixin, unittest.TestCase):

    def test_upsert_and_get_weekly_target(self):
        success = database.upsert_weekly_target("u1", "2026-03-16", 50.0, 45.0, "Cutback week")
        self.assertTrue(success)
        target = database.get_weekly_target("u1", "2026-03-16")
        self.assertIsNotNone(target)
        self.assertEqual(target["standard_target_km"], 50.0)
        self.assertEqual(target["actual_target_km"], 45.0)
        self.assertIn("Cutback", target["ai_reasoning"])

    def test_upsert_overwrites_existing(self):
        database.upsert_weekly_target("u1", "2026-03-16", 50.0, 45.0, "first")
        database.upsert_weekly_target("u1", "2026-03-16", 50.0, 40.0, "second")
        target = database.get_weekly_target("u1", "2026-03-16")
        self.assertEqual(target["actual_target_km"], 40.0)
        self.assertEqual(target["ai_reasoning"], "second")

    def test_get_nonexistent_week_returns_none(self):
        self.assertIsNone(database.get_weekly_target("u1", "2099-01-01"))

    def test_targets_are_user_isolated(self):
        database.upsert_weekly_target("u1", "2026-03-16", 50.0, 45.0, "u1 week")
        self.assertIsNone(database.get_weekly_target("u2", "2026-03-16"))


# ══════════════════════════════════════════════════════════════════════════════
# 6. CORE MEMORY (Multi-Tenant)
# ══════════════════════════════════════════════════════════════════════════════
class TestCoreMemory(_TempDbMixin, unittest.TestCase):

    def test_insert_and_get_active_memory(self):
        database.insert_memory("u1", "health", "injury_status", "Right knee pain", "active")
        memories = database.get_all_active_memories("u1")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["category"], "injury_status")
        self.assertIn("knee", memories[0]["fact"])

    def test_inactive_memory_not_returned(self):
        database.insert_memory("u1", "health", "injury_status", "Old injury", "inactive")
        memories = database.get_all_active_memories("u1")
        self.assertEqual(len(memories), 0)

    def test_global_deduplication_latest_wins(self):
        """Only the most recently inserted active memory per category should appear."""
        database.insert_memory("u1", "health", "injury_status", "Left knee pain", "active")
        database.insert_memory("u1", "health", "injury_status", "Right knee pain - updated", "active")
        memories = database.get_all_active_memories("u1")
        # Deduplication: only 1 row per category
        self.assertEqual(len(memories), 1)
        self.assertIn("updated", memories[0]["fact"])

    def test_inactive_overrides_active_for_same_category(self):
        """If latest entry for a category is 'inactive', it should NOT appear."""
        database.insert_memory("u1", "health", "injury_status", "Had knee pain", "active")
        database.insert_memory("u1", "health", "injury_status", "Knee healed", "inactive")
        memories = database.get_all_active_memories("u1")
        self.assertEqual(len(memories), 0)

    def test_multiple_categories_all_returned(self):
        database.insert_memory("u1", "sports", "main_goal", "Run 42km in 4h", "active")
        database.insert_memory("u1", "health", "injury_status", "Healthy", "active")
        database.insert_memory("u1", "sports", "gear_preference", "Loves Vaporfly", "active")
        memories = database.get_all_active_memories("u1")
        categories = [m["category"] for m in memories]
        self.assertIn("main_goal", categories)
        self.assertIn("injury_status", categories)
        self.assertIn("gear_preference", categories)
        self.assertEqual(len(memories), 3)

    def test_memories_are_user_isolated(self):
        database.insert_memory("u1", "health", "injury_status", "u1 injury", "active")
        memories_u2 = database.get_all_active_memories("u2")
        self.assertEqual(len(memories_u2), 0)

    def test_archive_memory(self):
        database.insert_memory("u1", "health", "injury_status", "Knee pain", "active")
        memories = database.get_all_active_memories("u1")
        mem_id = memories[0]["id"]
        result = database.archive_memory("u1", mem_id)
        self.assertTrue(result)
        # Archived memory should not appear in active list
        # (archive_memory sets status='archived', not 'active')
        # Re-fetch active memories
        conn = database.get_db_connection()
        c = conn.cursor()
        c.execute("SELECT status FROM core_memory WHERE id = ?", (mem_id,))
        row = c.fetchone()
        conn.close()
        self.assertEqual(row["status"], "archived")

    def test_archive_memory_wrong_user_fails_silently(self):
        database.insert_memory("u1", "health", "injury_status", "Knee pain", "active")
        memories = database.get_all_active_memories("u1")
        mem_id = memories[0]["id"]
        # Try archiving with wrong user_id → should not update u1's record
        database.archive_memory("u2", mem_id)
        conn = database.get_db_connection()
        c = conn.cursor()
        c.execute("SELECT status FROM core_memory WHERE id = ?", (mem_id,))
        row = c.fetchone()
        conn.close()
        self.assertEqual(row["status"], "active")  # Unchanged


# ══════════════════════════════════════════════════════════════════════════════
# 7. HISTORICAL TRAINING LOADS (charting)
# ══════════════════════════════════════════════════════════════════════════════
class TestHistoricalTrainingLoads(_TempDbMixin, unittest.TestCase):

    def test_returns_correct_structure(self):
        result = database.get_historical_training_loads("u1", days=7)
        for key in ("dates", "acute", "chronic", "optimal_min", "optimal_max"):
            self.assertIn(key, result)

    def test_returns_correct_number_of_days(self):
        result = database.get_historical_training_loads("u1", days=14)
        self.assertEqual(len(result["dates"]), 14)

    def test_no_runs_all_zeros(self):
        result = database.get_historical_training_loads("u1", days=7)
        self.assertTrue(all(v == 0 for v in result["acute"]))
        self.assertTrue(all(v == 0 for v in result["chronic"]))

    def test_optimal_band_is_80_to_130_of_chronic(self):
        # Insert a run 3 days ago
        run = {
            "activity_id": "hist1",
            "name": "Test", "start_date": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT06:00:00"),
            "distance_km": 10.0, "moving_time_min": 60.0, "avg_hr": 148,
            "max_hr": 168, "suffer_score": 80, "trimp_score": 60.0,
        }
        database.save_run_activity("u1", run)
        result = database.get_historical_training_loads("u1", days=7)
        for i, chronic in enumerate(result["chronic"]):
            if chronic > 0:
                self.assertAlmostEqual(result["optimal_min"][i], round(chronic * 0.8, 2), places=1)
                self.assertAlmostEqual(result["optimal_max"][i], round(chronic * 1.3, 2), places=1)


# ══════════════════════════════════════════════════════════════════════════════
# 8. MULTI-TENANT RUN ACTIVITY ISOLATION (T7)
# ══════════════════════════════════════════════════════════════════════════════
class TestMultiTenantRunActivityIsolation(_TempDbMixin, unittest.TestCase):
    """Run activities and training loads must be fully isolated by user_id."""

    def _make_run(self, activity_id: str, distance_km: float, trimp: float):
        from datetime import datetime
        return {
            "activity_id": activity_id,
            "name": f"Run {activity_id}",
            "start_date": datetime.now().strftime("%Y-%m-%dT06:00:00"),
            "distance_km": distance_km,
            "moving_time_min": 60.0,
            "avg_hr": 140,
            "max_hr": 165,
            "suffer_score": 60,
            "trimp_score": trimp,
        }

    def test_training_loads_isolated_by_user(self):
        """get_training_loads('user_a') must not include user_b's trimp."""
        database.save_run_activity("user_a", self._make_run("run_a1", 10.0, 100.0))
        database.save_run_activity("user_b", self._make_run("run_b1", 5.0, 50.0))

        loads_a = database.get_training_loads("user_a")
        loads_b = database.get_training_loads("user_b")

        # user_a has 100 trimp; user_b has 50 trimp — they must not bleed
        self.assertAlmostEqual(loads_a["acute_load_7d"], 100.0, places=0)
        self.assertAlmostEqual(loads_b["acute_load_7d"], 50.0, places=0)

    def test_weekly_mileage_isolated_by_user(self):
        """avg_weekly_mileage counts only the requesting user's runs."""
        database.save_run_activity("user_a", self._make_run("dist_a", 20.0, 80.0))
        database.save_run_activity("user_b", self._make_run("dist_b", 8.0, 30.0))

        loads_a = database.get_training_loads("user_a")
        loads_b = database.get_training_loads("user_b")

        # avg_weekly_mileage = total_28d / 4
        self.assertAlmostEqual(loads_a["avg_weekly_mileage"], 20.0 / 4, places=1)
        self.assertAlmostEqual(loads_b["avg_weekly_mileage"], 8.0 / 4, places=1)

    def test_recent_logs_isolated_by_user(self):
        """get_recent_runs_log only returns that user's runs (returned as formatted string)."""
        database.save_run_activity("user_a", self._make_run("log_a", 12.0, 90.0))
        database.save_run_activity("user_b", self._make_run("log_b", 6.0, 45.0))

        log_a = database.get_recent_runs_log("user_a", limit=10)
        log_b = database.get_recent_runs_log("user_b", limit=10)

        self.assertIn("log_a", log_a)
        self.assertNotIn("log_b", log_a)
        self.assertIn("log_b", log_b)
        self.assertNotIn("log_a", log_b)

    def test_empty_user_returns_zero_loads(self):
        """A user with no runs always gets zero loads — no data leaks from other users."""
        database.save_run_activity("user_a", self._make_run("only_a", 15.0, 120.0))

        loads_b = database.get_training_loads("user_b")

        self.assertAlmostEqual(loads_b["acute_load_7d"], 0.0, places=0)
        self.assertAlmostEqual(loads_b["avg_weekly_mileage"], 0.0, places=0)



# ══════════════════════════════════════════════════════════════════════════════
# 9. RUN ACTIVITY RAW (stream storage)
# ══════════════════════════════════════════════════════════════════════════════
class TestRunActivityRaw(_TempDbMixin, unittest.TestCase):

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(database.get_run_activity_raw("ghost_act"))

    def test_save_and_retrieve_raw_activity(self):
        database.save_run_activity_raw(
            activity_id="raw1",
            user_id="u1",
            activity_name="Morning Run",
            full_meta={"type": "Run"},
            stream_csv="",
            stream_file_path="/data/raw1.json",
        )
        result = database.get_run_activity_raw("raw1")
        self.assertIsNotNone(result)
        self.assertEqual(result["activity_name"], "Morning Run")
        self.assertEqual(result["stream_file_path"], "/data/raw1.json")

    def test_full_meta_is_deserialized(self):
        database.save_run_activity_raw(
            activity_id="raw2",
            user_id="u1",
            activity_name="Test",
            full_meta={"key": "value"},
            stream_csv="",
            stream_file_path="",
        )
        result = database.get_run_activity_raw("raw2")
        self.assertIsInstance(result["full_meta"], dict)
        self.assertEqual(result["full_meta"]["key"], "value")


# ══════════════════════════════════════════════════════════════════════════════
# 10. COMPUTED METRICS
# ══════════════════════════════════════════════════════════════════════════════
class TestComputedMetrics(_TempDbMixin, unittest.TestCase):

    _BASE_RUN = {
        "activity_id": "met1",
        "name": "Tempo",
        "start_date": "2026-04-01T07:00:00",
        "distance_km": 12.0,
        "moving_time_min": 60.0,
        "avg_hr": 155,
        "max_hr": 172,
        "suffer_score": 90,
        "trimp_score": 70.0,
    }

    def setUp(self):
        super().setUp()
        database.save_run_activity("u1", self._BASE_RUN)

    def test_upsert_returns_true(self):
        ok = database.upsert_run_computed_metrics("met1", "u1", {"avg_cadence_spm": 172.0})
        self.assertTrue(ok)

    def test_empty_metrics_returns_false(self):
        ok = database.upsert_run_computed_metrics("met1", "u1", {})
        self.assertFalse(ok)

    def test_unknown_columns_filtered_out(self):
        ok = database.upsert_run_computed_metrics("met1", "u1", {"invalid_col": 99})
        self.assertFalse(ok)

    def test_get_metrics_after_upsert(self):
        database.upsert_run_computed_metrics("met1", "u1", {"avg_cadence_spm": 172.0, "training_stress_score": 58.5})
        result = database.get_run_metrics_from_db("met1", "u1")
        self.assertAlmostEqual(result["avg_cadence_spm"], 172.0)
        self.assertAlmostEqual(result["training_stress_score"], 58.5)

    def test_get_metrics_missing_activity_returns_empty(self):
        result = database.get_run_metrics_from_db("nonexistent", "u1")
        self.assertEqual(result, {})

    def test_get_metrics_wrong_user_returns_empty(self):
        database.upsert_run_computed_metrics("met1", "u1", {"avg_cadence_spm": 172.0})
        result = database.get_run_metrics_from_db("met1", "u2")
        self.assertEqual(result, {})


# ══════════════════════════════════════════════════════════════════════════════
# 11. METRIC TREND DATA
# ══════════════════════════════════════════════════════════════════════════════
class TestMetricTrendData(_TempDbMixin, unittest.TestCase):

    def _insert_run_with_metric(self, activity_id, start_date, cadence):
        database.save_run_activity("u1", {
            "activity_id": activity_id,
            "name": f"Run {activity_id}",
            "start_date": start_date,
            "distance_km": 10.0,
            "moving_time_min": 55.0,
            "avg_hr": 148,
            "max_hr": 165,
            "suffer_score": 70,
            "trimp_score": 60.0,
        })
        database.upsert_run_computed_metrics(activity_id, "u1", {"avg_cadence_spm": cadence})

    def test_unknown_metric_returns_empty(self):
        rows = database.get_metric_trend_data("u1", "nonexistent_metric")
        self.assertEqual(rows, [])

    def test_no_data_returns_empty(self):
        rows = database.get_metric_trend_data("u1", "avg_cadence_spm", days=28)
        self.assertEqual(rows, [])

    def test_returns_rows_with_metric_value(self):
        self._insert_run_with_metric("trd1", "2026-04-15T07:00:00", 174.0)
        rows = database.get_metric_trend_data("u1", "avg_cadence_spm", days=365)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["avg_cadence_spm"], 174.0)

    def test_rows_have_expected_keys(self):
        self._insert_run_with_metric("trd2", "2026-04-15T07:00:00", 170.0)
        rows = database.get_metric_trend_data("u1", "avg_cadence_spm", days=365)
        self.assertIn("activity_id", rows[0])
        self.assertIn("start_date", rows[0])

    def test_user_isolation(self):
        self._insert_run_with_metric("trd3", "2026-04-15T07:00:00", 168.0)
        rows = database.get_metric_trend_data("u2", "avg_cadence_spm", days=365)
        self.assertEqual(rows, [])


# ══════════════════════════════════════════════════════════════════════════════
# 12. MONTHLY AND YEARLY VOLUME
# ══════════════════════════════════════════════════════════════════════════════
class TestVolumeStats(_TempDbMixin, unittest.TestCase):

    def _insert_run(self, activity_id, start_date, distance_km, user_id="u1"):
        database.save_run_activity(user_id, {
            "activity_id": activity_id,
            "name": f"Run {activity_id}",
            "start_date": start_date,
            "distance_km": distance_km,
            "moving_time_min": 60.0,
            "avg_hr": 148,
            "max_hr": 165,
            "suffer_score": 70,
            "trimp_score": 60.0,
        })

    def test_monthly_volume_empty_user(self):
        result = database.get_monthly_volume("nobody", 2026, 4)
        self.assertEqual(result["run_count"], 0)
        self.assertEqual(result["total_km"], 0.0)

    def test_monthly_volume_sums_correctly(self):
        self._insert_run("mv1", "2026-04-05T07:00:00", 10.0)
        self._insert_run("mv2", "2026-04-12T07:00:00", 15.0)
        result = database.get_monthly_volume("u1", 2026, 4)
        self.assertEqual(result["run_count"], 2)
        self.assertAlmostEqual(result["total_km"], 25.0)

    def test_monthly_volume_excludes_other_months(self):
        self._insert_run("mv3", "2026-03-31T07:00:00", 20.0)  # March
        self._insert_run("mv4", "2026-04-01T07:00:00", 10.0)  # April
        result = database.get_monthly_volume("u1", 2026, 4)
        self.assertEqual(result["run_count"], 1)
        self.assertAlmostEqual(result["total_km"], 10.0)

    def test_monthly_volume_december_boundary(self):
        self._insert_run("mv5", "2026-12-31T07:00:00", 12.0)
        self._insert_run("mv6", "2027-01-01T07:00:00", 8.0)
        result = database.get_monthly_volume("u1", 2026, 12)
        self.assertEqual(result["run_count"], 1)
        self.assertAlmostEqual(result["total_km"], 12.0)

    def test_yearly_volume_empty_user(self):
        result = database.get_yearly_volume("nobody", 2026)
        self.assertEqual(result["run_count"], 0)
        self.assertEqual(result["total_km"], 0.0)

    def test_yearly_volume_sums_all_months(self):
        self._insert_run("yv1", "2026-01-10T07:00:00", 10.0)
        self._insert_run("yv2", "2026-06-20T07:00:00", 20.0)
        result = database.get_yearly_volume("u1", 2026)
        self.assertEqual(result["run_count"], 2)
        self.assertAlmostEqual(result["total_km"], 30.0)

    def test_yearly_volume_monthly_breakdown_present(self):
        self._insert_run("yv3", "2026-03-15T07:00:00", 15.0)
        result = database.get_yearly_volume("u1", 2026)
        self.assertIn("monthly_breakdown", result)
        self.assertIsInstance(result["monthly_breakdown"], list)


# ══════════════════════════════════════════════════════════════════════════════
# 9. CORE MEMORY — insert, dedup, get_all_active, archive
# ══════════════════════════════════════════════════════════════════════════════
class TestCoreMemoryExtended(_TempDbMixin, unittest.TestCase):
    UID = "mem_user"

    def test_insert_new_memory(self):
        database.insert_memory(self.UID, "coach", "goal", "Run 5k in 25min")
        memories = database.get_all_active_memories(self.UID)
        facts = [m["fact"] for m in memories]
        self.assertIn("Run 5k in 25min", facts)

    def test_duplicate_insert_does_not_create_second_row(self):
        database.insert_memory(self.UID, "coach", "goal", "Run 5k in 25min")
        database.insert_memory(self.UID, "coach", "goal", "Run 5k in 25min")
        memories = database.get_all_active_memories(self.UID)
        matching = [m for m in memories if m["fact"] == "Run 5k in 25min"]
        self.assertEqual(len(matching), 1)

    def test_get_all_active_memories_empty(self):
        result = database.get_all_active_memories(self.UID)
        self.assertEqual(result, [])

    def test_get_all_active_memories_multiple(self):
        database.insert_memory(self.UID, "coach", "goal", "First fact")
        database.insert_memory(self.UID, "coach", "sleep", "Second fact")
        memories = database.get_all_active_memories(self.UID)
        self.assertEqual(len(memories), 2)

    def test_archived_memory_not_returned(self):
        database.insert_memory(self.UID, "coach", "goal", "To archive")
        memories = database.get_all_active_memories(self.UID)
        mem_id = memories[0]["id"]
        database.archive_memory(self.UID, mem_id)
        after = database.get_all_active_memories(self.UID)
        facts = [m["fact"] for m in after]
        self.assertNotIn("To archive", facts)

    def test_archive_returns_true(self):
        database.insert_memory(self.UID, "coach", "goal", "Fact")
        memories = database.get_all_active_memories(self.UID)
        result = database.archive_memory(self.UID, memories[0]["id"])
        self.assertTrue(result)

    def test_archive_wrong_user_no_effect(self):
        database.insert_memory(self.UID, "coach", "goal", "Fact")
        memories = database.get_all_active_memories(self.UID)
        mem_id = memories[0]["id"]
        database.archive_memory("other_user", mem_id)
        after = database.get_all_active_memories(self.UID)
        self.assertEqual(len(after), 1)


# ══════════════════════════════════════════════════════════════════════════════
# 10. NEWS AGENT STATE — get/set key-value store
# ══════════════════════════════════════════════════════════════════════════════
class TestNewsAgentState(_TempDbMixin, unittest.TestCase):
    UID = "news_user"

    def test_get_missing_key_returns_none(self):
        result = database.get_news_state(self.UID, "last_run")
        self.assertIsNone(result)

    def test_set_and_get_roundtrip(self):
        database.set_news_state(self.UID, "last_run", "2026-04-22T06:30:00")
        result = database.get_news_state(self.UID, "last_run")
        self.assertEqual(result, "2026-04-22T06:30:00")

    def test_set_overwrites_existing(self):
        database.set_news_state(self.UID, "topic", "AI")
        database.set_news_state(self.UID, "topic", "Finance")
        result = database.get_news_state(self.UID, "topic")
        self.assertEqual(result, "Finance")

    def test_different_users_isolated(self):
        database.set_news_state("user_a", "key", "value_a")
        database.set_news_state("user_b", "key", "value_b")
        self.assertEqual(database.get_news_state("user_a", "key"), "value_a")
        self.assertEqual(database.get_news_state("user_b", "key"), "value_b")


if __name__ == "__main__":
    unittest.main(verbosity=2)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

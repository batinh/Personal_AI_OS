"""
Layer 3 – Service/Tool Tests: app/agents/coach/tools.py
=========================================================
Toàn bộ I/O bên ngoài (DB, RAG, filesystem) được mock.
Test tập trung vào: output format đúng, error handling graceful,
tool routing logic, và guard clauses.
"""

import unittest
from unittest.mock import patch

from app.agents.coach.tools import (
    check_training_status,
    get_recent_workouts,
    get_total_run_stats,
    search_long_term_memory,
    set_actual_weekly_target,
    set_workout_plan,
    update_todays_plan,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. check_training_status
# ══════════════════════════════════════════════════════════════════════════════
class TestCheckTrainingStatus(unittest.TestCase):

    @patch("app.agents.coach.tools.get_training_loads")
    def test_returns_acwr_string(self, mock_loads):
        mock_loads.return_value = {"acute_load_7d": 100, "chronic_load_28d": 400}
        result = check_training_status("u1")
        self.assertIn("ACWR", result)
        self.assertIn("Sweet Spot", result)
        self.assertIn("Acute Load", result)

    @patch("app.agents.coach.tools.get_training_loads")
    def test_danger_zone_reflected_in_output(self, mock_loads):
        # ACWR > 1.5 → Danger Zone
        mock_loads.return_value = {"acute_load_7d": 250, "chronic_load_28d": 400}
        result = check_training_status("u1")
        self.assertIn("Danger Zone", result)

    @patch("app.agents.coach.tools.get_training_loads")
    def test_zero_loads_returns_no_chronic_data(self, mock_loads):
        mock_loads.return_value = {"acute_load_7d": 0, "chronic_load_28d": 0}
        result = check_training_status("u1")
        self.assertIn("No Chronic", result)

    @patch("app.agents.coach.tools.get_training_loads")
    def test_output_is_single_line_string(self, mock_loads):
        mock_loads.return_value = {"acute_load_7d": 80, "chronic_load_28d": 300}
        result = check_training_status("u1")
        self.assertIsInstance(result, str)
        self.assertIn("|", result)  # Pipe-separated format


# ══════════════════════════════════════════════════════════════════════════════
# 2. get_recent_workouts
# ══════════════════════════════════════════════════════════════════════════════
class TestGetRecentWorkouts(unittest.TestCase):

    @patch("app.agents.coach.tools.get_recent_runs_log")
    def test_returns_log_string(self, mock_log):
        mock_log.return_value = "- 2026-03-20: Morning Run | 10.0km"
        result = get_recent_workouts("u1")
        self.assertIn("Morning Run", result)
        mock_log.assert_called_once_with("u1", limit=10)

    @patch("app.agents.coach.tools.get_recent_runs_log")
    def test_empty_log_returns_empty_message(self, mock_log):
        mock_log.return_value = "No recent runs found in database."
        result = get_recent_workouts("u1")
        self.assertIn("No recent runs", result)


# ══════════════════════════════════════════════════════════════════════════════
# 3. update_todays_plan
# ══════════════════════════════════════════════════════════════════════════════
class TestUpdateTodaysPlan(unittest.TestCase):

    @patch("app.agents.coach.tools.update_daily_plan")
    def test_calls_db_with_todays_date(self, mock_update):
        mock_update.return_value = "✅ Đã cập nhật giáo án"
        update_todays_plan("u1", "Rest Day", "Recovery - easy walk")
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0]
        # arg[0]=user_id, arg[1]=date_str, arg[2]=title, arg[3]=description
        self.assertEqual(call_args[0], "u1")
        self.assertEqual(call_args[2], "Rest Day")
        # Date should be in YYYY-MM-DD format
        self.assertRegex(call_args[1], r"\d{4}-\d{2}-\d{2}")

    @patch("app.agents.coach.tools.update_daily_plan")
    def test_returns_db_result(self, mock_update):
        mock_update.return_value = "✅ Đã cập nhật"
        result = update_todays_plan("u1", "Easy Run", "30 min")
        self.assertIn("✅", result)


# ══════════════════════════════════════════════════════════════════════════════
# 4. set_workout_plan
# ══════════════════════════════════════════════════════════════════════════════
class TestSetWorkoutPlan(unittest.TestCase):

    @patch("app.agents.coach.tools.update_daily_plan")
    def test_passes_target_date_to_db(self, mock_update):
        mock_update.return_value = "✅ Done"
        set_workout_plan("u1", "2026-03-25", "Tempo Run", "5x1km @ threshold")
        call_args = mock_update.call_args[0]
        self.assertEqual(call_args[1], "2026-03-25")
        self.assertEqual(call_args[2], "Tempo Run")

    @patch("app.agents.coach.tools.update_daily_plan")
    def test_returns_db_message(self, mock_update):
        mock_update.return_value = "✅ Đã lên lịch"
        result = set_workout_plan("u1", "2026-03-25", "Long Run", "20km easy")
        self.assertIn("✅", result)


# ══════════════════════════════════════════════════════════════════════════════
# 5. set_actual_weekly_target
# ══════════════════════════════════════════════════════════════════════════════
class TestSetActualWeeklyTarget(unittest.TestCase):

    @patch("app.agents.coach.tools.get_weekly_target")
    @patch("app.agents.coach.tools.upsert_weekly_target")
    def test_success_returns_confirmation(self, mock_upsert, mock_get):
        mock_get.return_value = {"standard_target_km": 50.0, "actual_target_km": 45.0}
        mock_upsert.return_value = True
        result = set_actual_weekly_target("u1", "2026-03-16", 45.0, "Cutback week")
        self.assertIn("Thành công", result)
        self.assertIn("45.0km", result)

    @patch("app.agents.coach.tools.get_weekly_target")
    @patch("app.agents.coach.tools.upsert_weekly_target")
    def test_failure_returns_error_message(self, mock_upsert, mock_get):
        mock_get.return_value = None
        mock_upsert.return_value = False
        result = set_actual_weekly_target("u1", "2026-03-16", 45.0, "reason")
        self.assertIn("Thất bại", result)

    @patch("app.agents.coach.tools.get_weekly_target")
    @patch("app.agents.coach.tools.upsert_weekly_target")
    def test_preserves_standard_target_from_db(self, mock_upsert, mock_get):
        """AI should NOT overwrite standard_target with 0 when no existing record."""
        mock_get.return_value = None  # No existing record
        mock_upsert.return_value = True
        set_actual_weekly_target("u1", "2026-03-16", 42.0, "first time")
        call_args = mock_upsert.call_args[0]
        # standard_target should fall back to actual_target when no DB record
        self.assertEqual(call_args[2], 42.0)  # standard = actual as fallback
        self.assertEqual(call_args[3], 42.0)  # actual_target_km

    @patch("app.agents.coach.tools.get_weekly_target")
    @patch("app.agents.coach.tools.upsert_weekly_target")
    def test_does_not_overwrite_existing_standard_target(self, mock_upsert, mock_get):
        """Standard target from DB should be preserved even when AI sets new actual."""
        mock_get.return_value = {"standard_target_km": 55.0, "actual_target_km": 50.0}
        mock_upsert.return_value = True
        set_actual_weekly_target("u1", "2026-03-16", 40.0, "injury cutback")
        call_args = mock_upsert.call_args[0]
        self.assertEqual(call_args[2], 55.0)  # standard_target preserved
        self.assertEqual(call_args[3], 40.0)  # actual_target updated


# ══════════════════════════════════════════════════════════════════════════════
# 6. search_long_term_memory
# ══════════════════════════════════════════════════════════════════════════════
class TestSearchLongTermMemory(unittest.TestCase):

    @patch("app.agents.coach.tools.rag_db")
    def test_returns_formatted_memories(self, mock_rag):
        mock_rag.recall.return_value = {
            "documents": [["Ran 10km last Sunday", "Had knee pain in March"]]
        }
        result = search_long_term_memory("recent runs")
        self.assertIn("Ký ức", result)
        self.assertIn("10km", result)
        self.assertIn("knee pain", result)

    @patch("app.agents.coach.tools.rag_db")
    def test_empty_results_return_not_found_message(self, mock_rag):
        mock_rag.recall.return_value = {"documents": [[]]}
        result = search_long_term_memory("nothing here")
        self.assertIn("Không tìm thấy", result)

    @patch("app.agents.coach.tools.rag_db")
    def test_none_results_return_not_found_message(self, mock_rag):
        mock_rag.recall.return_value = None
        result = search_long_term_memory("query")
        self.assertIn("Không tìm thấy", result)

    @patch("app.agents.coach.tools.rag_db")
    def test_rag_exception_returns_error_string(self, mock_rag):
        mock_rag.recall.side_effect = Exception("ChromaDB unavailable")
        result = search_long_term_memory("query")
        self.assertIn("Lỗi", result)
        # Should NOT raise exception to caller
        self.assertIsInstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# 7. get_total_run_stats
# ══════════════════════════════════════════════════════════════════════════════
class TestGetTotalRunStats(unittest.TestCase):

    @patch(
        "builtins.open",
        new_callable=unittest.mock.mock_open,
        read_data='{"recent_run_totals": 180.5, "ytd_run_totals": 950.2}',
    )
    def test_returns_formatted_stats(self, mock_file):
        result = get_total_run_stats("u1")
        self.assertIn("180.5km", result)
        self.assertIn("950.2km", result)

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_missing_file_returns_fallback_message(self, mock_file):
        result = get_total_run_stats("u1")
        self.assertIn("Chưa có dữ liệu", result)
        # Should NOT raise exception
        self.assertIsInstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# 8. TOOL ROUTING (_select_tools_for_message)
# ══════════════════════════════════════════════════════════════════════════════
class TestToolRouting(unittest.TestCase):
    """Verify the read-only vs write tool routing logic."""

    def setUp(self):
        from app.agents.coach.agent import (
            _select_tools_for_message,
            _TOOLS_READ_ONLY,
            _TOOLS_WRITE,
        )

        self._select = _select_tools_for_message
        self._read_count = len(_TOOLS_READ_ONLY)
        self._write_count = len(_TOOLS_WRITE)

    def test_informational_query_gets_read_only_tools(self):
        tools = self._select("hôm nay tôi chạy được bao nhiêu km?")
        self.assertEqual(len(tools), self._read_count)

    def test_write_keyword_triggers_write_tools(self):
        for keyword in [
            "đổi lịch",
            "hủy buổi chạy",
            "tăng target",
            "set target",
            "chốt tuần này",
        ]:
            tools = self._select(keyword)
            self.assertEqual(
                len(tools),
                self._read_count + self._write_count,
                f"Expected write tools for keyword: '{keyword}'",
            )

    def test_mixed_message_with_write_keyword_gets_all_tools(self):
        tools = self._select("tôi muốn thay đổi lịch chạy ngày mai")
        self.assertEqual(len(tools), self._read_count + self._write_count)

    def test_case_insensitive_routing(self):
        tools = self._select("HỦY buổi chạy sáng nay")
        self.assertEqual(len(tools), self._read_count + self._write_count)


# ══════════════════════════════════════════════════════════════════════════════
# 9. get_run_stream_csv
# ══════════════════════════════════════════════════════════════════════════════
class TestGetRunStreamCsv(unittest.TestCase):

    @patch("app.agents.coach.tools.get_run_activity_raw")
    def test_no_raw_activity_returns_not_found(self, mock_raw):
        from app.agents.coach.tools import get_run_stream_csv

        mock_raw.return_value = None
        result = get_run_stream_csv("act1")
        self.assertIn("Không tìm thấy", result)

    @patch("app.agents.coach.tools.get_run_activity_raw")
    def test_no_stream_file_path_returns_not_found(self, mock_raw):
        from app.agents.coach.tools import get_run_stream_csv

        mock_raw.return_value = {"stream_file_path": ""}
        result = get_run_stream_csv("act1")
        self.assertIn("Không tìm thấy", result)

    @patch("app.agents.coach.tools.load_activity_stream_from_file")
    @patch("app.agents.coach.tools.get_run_activity_raw")
    def test_unloadable_payload_returns_error(self, mock_raw, mock_load):
        from app.agents.coach.tools import get_run_stream_csv

        mock_raw.return_value = {"stream_file_path": "/data/act1.json"}
        mock_load.return_value = None
        result = get_run_stream_csv("act1")
        self.assertIn("Không thể đọc", result)

    @patch("app.agents.coach.tools.get_stream_arrays")
    @patch("app.agents.coach.tools.load_activity_stream_from_file")
    @patch("app.agents.coach.tools.get_run_activity_raw")
    def test_empty_arrays_returns_empty_message(self, mock_raw, mock_load, mock_arrays):
        from app.agents.coach.tools import get_run_stream_csv

        mock_raw.return_value = {"stream_file_path": "/data/act1.json"}
        mock_load.return_value = {"time": [], "heartrate": []}
        mock_arrays.return_value = {}
        result = get_run_stream_csv("act1")
        self.assertIn("rỗng", result)

    @patch("app.agents.coach.tools.get_stream_arrays")
    @patch("app.agents.coach.tools.load_activity_stream_from_file")
    @patch("app.agents.coach.tools.get_run_activity_raw")
    def test_happy_path_returns_csv_header(self, mock_raw, mock_load, mock_arrays):
        from app.agents.coach.tools import get_run_stream_csv

        mock_raw.return_value = {"stream_file_path": "/data/act1.json"}
        mock_load.return_value = {"time": [0, 1, 2]}
        mock_arrays.return_value = {
            "time": [0, 1, 2],
            "velocity_smooth": [3.0, 3.1, 3.2],
            "heartrate": [148, 150, 152],
            "cadence": [86, 87, 88],
        }
        result = get_run_stream_csv("act1")
        self.assertIn("t(s)", result)
        self.assertIn("HR", result)


# ══════════════════════════════════════════════════════════════════════════════
# 10. get_run_computed_metrics
# ══════════════════════════════════════════════════════════════════════════════
class TestGetRunComputedMetrics(unittest.TestCase):

    @patch("app.agents.coach.tools.get_run_metrics_from_db")
    def test_not_found_returns_not_found_message(self, mock_db):
        from app.agents.coach.tools import get_run_computed_metrics

        mock_db.return_value = {}
        result = get_run_computed_metrics("act1", "u1")
        self.assertIn("Chưa có metrics", result)

    @patch("app.agents.coach.tools.build_run_metrics_block")
    @patch("app.agents.coach.tools.get_run_metrics_from_db")
    def test_block_is_returned_when_found(self, mock_db, mock_block):
        from app.agents.coach.tools import get_run_computed_metrics

        mock_db.return_value = {"avg_cadence_spm": 172.0}
        mock_block.return_value = "Cadence: 172 spm | TSS: 55"
        result = get_run_computed_metrics("act1", "u1")
        self.assertIn("Cadence", result)

    @patch("app.agents.coach.tools.build_run_metrics_block")
    @patch("app.agents.coach.tools.get_run_metrics_from_db")
    def test_none_block_returns_fallback(self, mock_db, mock_block):
        from app.agents.coach.tools import get_run_computed_metrics

        mock_db.return_value = {"avg_cadence_spm": 172.0}
        mock_block.return_value = None
        result = get_run_computed_metrics("act1", "u1")
        self.assertIn("None", result)


# ══════════════════════════════════════════════════════════════════════════════
# 11. get_metric_trend
# ══════════════════════════════════════════════════════════════════════════════
class TestGetMetricTrend(unittest.TestCase):

    @patch("app.agents.coach.tools.get_metric_trend_data")
    def test_empty_data_returns_no_data_message(self, mock_data):
        from app.agents.coach.tools import get_metric_trend

        mock_data.return_value = []
        result = get_metric_trend("u1", "avg_cadence_spm")
        self.assertIn("Không có dữ liệu", result)

    @patch("app.agents.coach.tools.get_metric_trend_data")
    def test_with_data_formats_output(self, mock_data):
        from app.agents.coach.tools import get_metric_trend

        mock_data.return_value = [
            {"date": "2026-04-15", "value": 172.0},
            {"date": "2026-04-10", "value": 170.5},
        ]
        result = get_metric_trend("u1", "avg_cadence_spm")
        self.assertIn("avg_cadence_spm", result)
        self.assertIn("2026-04-15", result)

    @patch("app.agents.coach.tools.get_metric_trend_data")
    def test_custom_days_parameter_passed_through(self, mock_data):
        from app.agents.coach.tools import get_metric_trend

        mock_data.return_value = []
        get_metric_trend("u1", "avg_cadence_spm", days=14)
        mock_data.assert_called_once_with("u1", "avg_cadence_spm", 14)


# ══════════════════════════════════════════════════════════════════════════════
# 12. get_volume_for_week
# ══════════════════════════════════════════════════════════════════════════════
class TestGetVolumeForWeek(unittest.TestCase):

    @patch("app.agents.coach.tools.get_monthly_volume")
    def test_no_week_runs_returns_fallback_monthly(self, mock_monthly):
        from app.agents.coach.tools import get_volume_for_week

        mock_monthly.return_value = {
            "total_distance_km": 45.0,
            "total_runs": 5,
            "runs": [],
        }
        result = get_volume_for_week("u1", 2026, 15)
        self.assertIn("45.0 km", result)

    @patch("app.agents.coach.tools.get_monthly_volume")
    def test_output_contains_week_number(self, mock_monthly):
        from app.agents.coach.tools import get_volume_for_week

        mock_monthly.return_value = {
            "total_distance_km": 30.0,
            "total_runs": 3,
            "runs": [],
        }
        result = get_volume_for_week("u1", 2026, 10)
        self.assertIn("10", result)
        self.assertIn("2026", result)


# ══════════════════════════════════════════════════════════════════════════════
# 13. get_volume_summary
# ══════════════════════════════════════════════════════════════════════════════
class TestGetVolumeSummary(unittest.TestCase):

    @patch("app.agents.coach.tools.get_monthly_volume")
    def test_period_month_returns_monthly_summary(self, mock_monthly):
        from app.agents.coach.tools import get_volume_summary

        mock_monthly.return_value = {
            "total_distance_km": 120.5,
            "total_runs": 14,
            "total_moving_time_min": 660,
        }
        result = get_volume_summary("u1", "month", 2026, month=4)
        self.assertIn("120.5 km", result)
        self.assertIn("14", result)

    @patch("app.agents.coach.tools.get_yearly_volume")
    def test_period_year_returns_yearly_summary(self, mock_yearly):
        from app.agents.coach.tools import get_volume_summary

        mock_yearly.return_value = {
            "total_distance_km": 800.0,
            "total_runs": 90,
            "total_moving_time_min": 4500,
            "monthly_breakdown": {3: {"distance_km": 120.0, "runs": 12}},
        }
        result = get_volume_summary("u1", "year", 2026)
        self.assertIn("800.0 km", result)
        self.assertIn("Tháng 3", result)

    @patch("app.agents.coach.tools.get_yearly_volume")
    def test_period_year_no_breakdown_still_works(self, mock_yearly):
        from app.agents.coach.tools import get_volume_summary

        mock_yearly.return_value = {
            "total_distance_km": 500.0,
            "total_runs": 60,
            "total_moving_time_min": 3000,
            "monthly_breakdown": {},
        }
        result = get_volume_summary("u1", "year", 2025)
        self.assertIn("500.0 km", result)

"""
test_flow_run_analysis.py — Tests for app/agents/coach/flows/run_analysis.py
=============================================================================
Covers analyze_run_with_gemini():
  - Success path: returns analysis_text, updates GCS score, marks plan Completed
  - Gemini error: returns None
  - No plan found: plan status not updated
  - Empty analysis_text in response
"""
import json
import unittest
from unittest.mock import patch, MagicMock


def _make_config():
    return {
        "model_name": "models/gemini-2.0-flash",
        "system_instruction": "You are a running coach.",
        "user_profile": "Runner, age 30",
        "max_hr": 180,
        "rest_hr": 50,
        "race_date": "2026-06-01",
        "race_distance_km": 21.1,
    }


def _make_meta_data():
    return {
        "start_date_local": "2026-04-10T06:00:00",
        "splits": [
            {"km": 1, "pace": 3.2, "hr": 145},
            {"km": 2, "pace": 3.1, "hr": 148},
        ],
    }


_PATCHES = [
    "app.agents.coach.flows.run_analysis.get_primary_user_id",
    "app.agents.coach.flows.run_analysis.get_training_loads",
    "app.agents.coach.flows.run_analysis.calculate_acwr",
    "app.agents.coach.flows.run_analysis.get_weekly_volume",
    "app.agents.coach.flows.run_analysis.get_formatted_weekly_context",
    "app.agents.coach.flows.run_analysis.get_plan_for_date",
    "app.agents.coach.flows.run_analysis.calculate_training_phase",
    "app.agents.coach.flows.run_analysis.build_system_instruction",
    "app.agents.coach.flows.run_analysis.get_shared_context_block",
    "app.agents.coach.flows.run_analysis.build_universal_run_analysis_prompt",
    "app.agents.coach.flows.run_analysis.debug_log_prompt",
    "app.agents.coach.flows.run_analysis.update_run_gcs_score",
    "app.agents.coach.flows.run_analysis.save_message",
    "app.agents.coach.flows.run_analysis.update_plan_status",
    "app.agents.coach.flows.run_analysis.send_message_with_retry",
    "app.agents.coach.flows.run_analysis.client",
]


class TestAnalyzeRunWithGemini(unittest.TestCase):

    def _apply_patches(self, analysis_text="Great run!", gcs_score=8, today_plan=None):
        """Helper to apply all standard patches and return the function + key mocks."""
        mocks = {}
        patchers = []
        for target in _PATCHES:
            p = patch(target)
            m = p.start()
            patchers.append(p)
            key = target.split(".")[-1]
            mocks[key] = m

        # Configure standard returns
        mocks["get_primary_user_id"].return_value = "123456"
        mocks["get_training_loads"].return_value = {"acute_load_7d": 150, "chronic_load_28d": 140}
        mocks["calculate_acwr"].return_value = {"acwr": 1.07, "status": "Optimal"}
        mocks["get_weekly_volume"].return_value = 42.0
        mocks["get_formatted_weekly_context"].return_value = "Week context"
        mocks["get_plan_for_date"].return_value = today_plan
        mocks["calculate_training_phase"].return_value = {
            "phase": "Build", "microcycle": "Week 2", "weeks_left": 8, "taper_factor": 1.0
        }
        mocks["build_system_instruction"].return_value = "System instruction"
        mocks["get_shared_context_block"].return_value = "Shared context"
        mocks["build_universal_run_analysis_prompt"].return_value = "Full prompt"
        mocks["debug_log_prompt"].return_value = None

        # Gemini response
        response = MagicMock()
        response.text = json.dumps({"analysis_text": analysis_text, "gcs_score": gcs_score})
        chat_session = MagicMock()
        mocks["client"].chats.create.return_value = chat_session
        mocks["send_message_with_retry"].return_value = response

        return mocks, patchers

    def _stop(self, patchers):
        for p in patchers:
            p.stop()

    def test_success_returns_analysis_text(self):
        mocks, patchers = self._apply_patches(analysis_text="Great run!", gcs_score=8)
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            result = analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            self.assertEqual(result, "Great run!")
        finally:
            self._stop(patchers)

    def test_success_updates_gcs_score(self):
        mocks, patchers = self._apply_patches(gcs_score=7)
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            mocks["update_run_gcs_score"].assert_called_once_with("act_1", "123456", 7)
        finally:
            self._stop(patchers)

    def test_success_saves_message(self):
        mocks, patchers = self._apply_patches(analysis_text="Nice pace!")
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            mocks["save_message"].assert_called_once()
            call_args = mocks["save_message"].call_args[0]
            self.assertEqual(call_args[0], "123456")
            self.assertEqual(call_args[1], "model")
            self.assertIn("Nice pace!", call_args[2])
        finally:
            self._stop(patchers)

    def test_plan_found_marks_completed(self):
        today_plan = {"workout_title": "Easy Run", "description": "60min easy"}
        mocks, patchers = self._apply_patches(today_plan=today_plan)
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            mocks["update_plan_status"].assert_called_once_with("123456", "2026-04-10", "Completed")
        finally:
            self._stop(patchers)

    def test_no_plan_skips_update_plan_status(self):
        mocks, patchers = self._apply_patches(today_plan=None)
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            mocks["update_plan_status"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_gemini_exception_returns_none(self):
        mocks, patchers = self._apply_patches()
        mocks["send_message_with_retry"].side_effect = Exception("Gemini API error")
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            result = analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            self.assertIsNone(result)
        finally:
            self._stop(patchers)

    def test_gemini_exception_does_not_update_gcs(self):
        mocks, patchers = self._apply_patches()
        mocks["send_message_with_retry"].side_effect = Exception("timeout")
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            mocks["update_run_gcs_score"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_empty_analysis_text_returned_as_empty_string(self):
        mocks, patchers = self._apply_patches(analysis_text="")
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            result = analyze_run_with_gemini("act_1", "Morning 10K", "csv", _make_meta_data(), _make_config())
            self.assertEqual(result, "")
        finally:
            self._stop(patchers)

    def test_meta_data_without_start_date_uses_today(self):
        mocks, patchers = self._apply_patches(today_plan=None)
        meta = {"splits": []}  # No start_date_local
        try:
            from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
            result = analyze_run_with_gemini("act_1", "Morning 10K", "csv", meta, _make_config())
            # Should not raise; get_plan_for_date called with some date string
            mocks["get_plan_for_date"].assert_called_once()
            date_arg = mocks["get_plan_for_date"].call_args[0][1]
            self.assertRegex(date_arg, r"\d{4}-\d{2}-\d{2}")
        finally:
            self._stop(patchers)


if __name__ == "__main__":
    unittest.main()

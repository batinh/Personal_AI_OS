"""
Layer 1 – Pure Unit Tests: app/agents/coach/utils.py
=======================================================
All tests here are pure math/logic – no DB, no network, no mocks needed.
Covers: TRIMP, ACWR, Training Phase, Grade Adjusted Pace, Decoupling,
        Efficiency Factor, Weekly Context formatting.
"""
import math
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# helpers imported under test
# ─────────────────────────────────────────────────────────────────────────────
from app.agents.coach.utils import (
    calculate_acwr,
    calculate_efficiency_factor,
    calculate_grade_adjusted_pace,
    calculate_training_phase,
    calculate_trimp,
    analyze_decoupling,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. TRIMP
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculateTrimp(unittest.TestCase):
    """Bannister TRIMP formula correctness."""

    def test_zero_duration_returns_zero(self):
        result = calculate_trimp(0, 150)
        self.assertEqual(result["trimp"], 0)
        self.assertEqual(result["intensity_level"], "No Data")

    def test_zero_hr_returns_zero(self):
        result = calculate_trimp(60, 0)
        self.assertEqual(result["trimp"], 0)

    def test_easy_run_is_low_trimp(self):
        # 45 min @ 130 bpm (max=185, rest=55) → Easy/Recovery
        result = calculate_trimp(45, 130, max_hr=185, rest_hr=55)
        self.assertGreater(result["trimp"], 0)
        self.assertEqual(result["intensity_level"], "Easy/Recovery")

    def test_hard_run_exceeds_120_trimp(self):
        # 90 min @ 170 bpm → High Load
        result = calculate_trimp(90, 170, max_hr=185, rest_hr=55)
        self.assertGreater(result["trimp"], 120)
        self.assertEqual(result["intensity_level"], "High (Severe Load)")

    def test_medium_run_is_tempo(self):
        # 60 min @ 158 bpm
        result = calculate_trimp(60, 158, max_hr=185, rest_hr=55)
        self.assertGreater(result["trimp"], 70)
        self.assertLessEqual(result["trimp"], 120)
        self.assertEqual(result["intensity_level"], "Medium (Tempo/Threshold)")

    def test_hr_below_rest_hr_clamps_to_zero_trimp(self):
        # avg_hr < rest_hr → HRR should clamp to 0, TRIMP = 0
        result = calculate_trimp(60, 40, max_hr=185, rest_hr=55)
        self.assertEqual(result["trimp"], 0)

    def test_custom_hr_zones_respected(self):
        # Same inputs, different max_hr → different TRIMP
        r1 = calculate_trimp(60, 155, max_hr=185, rest_hr=55)
        r2 = calculate_trimp(60, 155, max_hr=200, rest_hr=55)
        self.assertNotEqual(r1["trimp"], r2["trimp"])


# ══════════════════════════════════════════════════════════════════════════════
# 2. ACWR
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculateAcwr(unittest.TestCase):
    """Acute:Chronic Workload Ratio zone classification."""

    def test_zero_chronic_returns_no_data(self):
        result = calculate_acwr(50, 0)
        self.assertEqual(result["acwr"], 0.0)
        self.assertIn("No Chronic", result["status"])

    def test_sweet_spot_080_to_130(self):
        # acute=100, chronic_28d=400 → avg_weekly=100 → ACWR=1.0
        result = calculate_acwr(100, 400)
        self.assertEqual(result["acwr"], 1.0)
        self.assertIn("Sweet Spot", result["status"])

    def test_undertraining_below_080(self):
        # acute=40, chronic_28d=400 → avg_weekly=100 → ACWR=0.4
        result = calculate_acwr(40, 400)
        self.assertLess(result["acwr"], 0.8)
        self.assertIn("Under-training", result["status"])

    def test_overreaching_130_to_150(self):
        # acute=140, chronic_28d=400 → avg_weekly=100 → ACWR=1.4
        result = calculate_acwr(140, 400)
        self.assertGreater(result["acwr"], 1.3)
        self.assertLessEqual(result["acwr"], 1.5)
        self.assertIn("Overreaching", result["status"])

    def test_danger_zone_above_150(self):
        # acute=200, chronic_28d=400 → avg_weekly=100 → ACWR=2.0
        result = calculate_acwr(200, 400)
        self.assertGreater(result["acwr"], 1.5)
        self.assertIn("Danger Zone", result["status"])

    def test_acwr_precision_two_decimals(self):
        result = calculate_acwr(110, 400)
        # Should be rounded to 2 decimal places
        self.assertEqual(result["acwr"], round(result["acwr"], 2))


# ══════════════════════════════════════════════════════════════════════════════
# 3. TRAINING PHASE
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculateTrainingPhase(unittest.TestCase):
    """Phase & microcycle labeling relative to race date."""

    def _future_date(self, weeks: int) -> str:
        """Return a date string N weeks from today."""
        return (datetime.now() + timedelta(weeks=weeks)).strftime("%Y-%m-%d")

    def test_no_race_date_returns_base(self):
        result = calculate_training_phase("")
        self.assertEqual(result["phase"], "Base Phase")
        self.assertEqual(result["weeks_left"], 99)

    def test_race_in_past_returns_race_week(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        result = calculate_training_phase(past)
        self.assertEqual(result["phase"], "Race Week")
        self.assertEqual(result["weeks_left"], 0)

    def test_base_phase_more_than_8_weeks(self):
        result = calculate_training_phase(self._future_date(12))
        self.assertIn("Base Phase", result["phase"])
        self.assertGreater(result["weeks_left"], 8)

    def test_build_phase_5_to_8_weeks(self):
        result = calculate_training_phase(self._future_date(6))
        self.assertIn("Build Phase", result["phase"])

    def test_peak_phase_3_to_4_weeks(self):
        result = calculate_training_phase(self._future_date(3))
        self.assertIn("Peak Phase", result["phase"])

    def test_taper_phase_1_to_2_weeks(self):
        result = calculate_training_phase(self._future_date(2))
        self.assertIn("Taper Phase", result["phase"])

    def test_cutback_week_every_4th_week(self):
        # weeks_left=5 → 5 % 4 = 1 → cutback
        result = calculate_training_phase(self._future_date(5))
        self.assertIn("Cutback", result["microcycle"])

    def test_load_week_when_not_cutback(self):
        # weeks_left=6 → 6 % 4 = 2 → Load/Progression
        result = calculate_training_phase(self._future_date(6))
        self.assertIn("Load", result["microcycle"])

    def test_invalid_date_returns_error_phase(self):
        result = calculate_training_phase("not-a-date")
        self.assertEqual(result["phase"], "Error Phase")


# ══════════════════════════════════════════════════════════════════════════════
# 4. EFFICIENCY FACTOR
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculateEfficiencyFactor(unittest.TestCase):

    def test_zero_hr_returns_zero(self):
        self.assertEqual(calculate_efficiency_factor(200, 0), 0.0)

    def test_correct_formula(self):
        # EF = 200 / 150 = 1.33
        self.assertEqual(calculate_efficiency_factor(200, 150), round(200 / 150, 2))

    def test_returns_float(self):
        self.assertIsInstance(calculate_efficiency_factor(180, 140), float)


# ══════════════════════════════════════════════════════════════════════════════
# 5. GRADE ADJUSTED PACE
# ══════════════════════════════════════════════════════════════════════════════
class TestCalculateGradeAdjustedPace(unittest.TestCase):

    def test_flat_terrain_no_change(self):
        # grade=0 → cost=1 → gap = velocity
        result = calculate_grade_adjusted_pace(3.5, 0)
        self.assertAlmostEqual(result, 3.5, places=4)

    def test_uphill_increases_pace_cost(self):
        # Uphill should produce higher effective pace cost
        flat = calculate_grade_adjusted_pace(3.5, 0)
        uphill = calculate_grade_adjusted_pace(3.5, 5)
        self.assertGreater(uphill, flat)

    def test_downhill_negative_grade_decreases_cost(self):
        flat = calculate_grade_adjusted_pace(3.5, 0)
        downhill = calculate_grade_adjusted_pace(3.5, -5)
        self.assertLess(downhill, flat)


# ══════════════════════════════════════════════════════════════════════════════
# 6. AEROBIC DECOUPLING
# ══════════════════════════════════════════════════════════════════════════════
class TestAnalyzeDecoupling(unittest.TestCase):

    def _make_df(self, v_list, hr_list):
        return pd.DataFrame({"Velocity_m_s": v_list, "HR_bpm": hr_list})

    def test_empty_df_returns_zero(self):
        self.assertEqual(analyze_decoupling(pd.DataFrame()), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(analyze_decoupling(None), 0.0)

    def test_too_few_rows_returns_zero(self):
        df = self._make_df([3.0] * 5, [140] * 5)
        self.assertEqual(analyze_decoupling(df), 0.0)

    def test_perfect_consistency_near_zero_decoupling(self):
        # Identical HR and pace in both halves → decoupling ≈ 0
        df = self._make_df([3.0] * 20, [150] * 20)
        self.assertAlmostEqual(analyze_decoupling(df), 0.0, places=1)

    def test_cardiac_drift_detected(self):
        # Second half: slower + higher HR → efficiency drops → positive decoupling
        v1 = [3.5] * 10
        hr1 = [140] * 10
        v2 = [3.2] * 10  # slower
        hr2 = [155] * 10  # higher HR
        df = self._make_df(v1 + v2, hr1 + hr2)
        decoupling = analyze_decoupling(df)
        self.assertGreater(decoupling, 0)

    def test_returns_float(self):
        df = self._make_df([3.0] * 20, [150] * 20)
        self.assertIsInstance(analyze_decoupling(df), float)


if __name__ == "__main__":
    unittest.main(verbosity=2)

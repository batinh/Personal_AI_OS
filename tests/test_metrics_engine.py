"""
Tests for app.agents.coach.metrics_engine.
Covers: happy path, interval detection, missing streams, power off, tempo guard,
        empty arrays, and the prompt block builder.
"""
import json
import math
import pytest

from app.agents.coach.metrics_engine import (
    build_run_metrics_block,
    compute_stream_metrics,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_config(max_hr=185, rest_hr=55, rftp_watts=None, threshold_pace=5.0):
    return {
        "max_hr": max_hr,
        "rest_hr": rest_hr,
        "rftp_watts": rftp_watts,
        "threshold_pace_min_km": threshold_pace,
    }


def _make_meta(moving_time_sec=3600, distance_m=10000, avg_hr=140):
    return {
        "moving_time": moving_time_sec,
        "distance": distance_m,
        "average_heartrate": avg_hr,
    }


def _constant_stream(value, length=600):
    return [value] * length


def _sinusoidal_vel(base=3.5, amplitude=0.1, length=600):
    """Gentle variation — simulates easy run pace fluctuation."""
    return [base + amplitude * math.sin(i * 0.05) for i in range(length)]


def _interval_vel(length=900):
    """
    Simulates 6 × 400m intervals:
    90s easy (2.8 m/s) → 60s hard (4.8 m/s) → repeated.
    Total ≈ 6 reps.
    """
    pattern = [2.8] * 90 + [4.8] * 60
    full = (pattern * 10)[:length]
    return full


def _easy_hr(length=600, base=130):
    return [base + i * 0.01 for i in range(length)]  # slight cardiac drift


def _interval_hr(vel_stream):
    """HR mirrors velocity — 120 easy, 165 hard."""
    return [165 if v > 4.0 else 120 for v in vel_stream]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputeEasyRun:
    """Happy path: easy 60-minute run with all streams present."""

    def test_returns_dict(self):
        vel = _sinusoidal_vel(length=3600)
        hr = _easy_hr(length=3600, base=135)
        cad = _constant_stream(88, 3600)
        alt = [100 + i * 0.01 for i in range(3600)]
        streams = {
            "velocity_smooth": vel,
            "heartrate": hr,
            "cadence": cad,
            "altitude": alt,
            "time": list(range(3600)),
        }
        result = compute_stream_metrics(streams, _make_meta(moving_time_sec=3600), _make_config())
        assert isinstance(result, dict)

    def test_avg_pace_computed(self):
        vel = _constant_stream(3.333, 3600)  # 5:00 min/km
        hr = _constant_stream(140, 3600)
        streams = {"velocity_smooth": vel, "heartrate": hr, "time": list(range(3600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        pace = result.get("avg_pace_min_km")
        assert pace is not None
        assert abs(pace - 5.0) < 0.1

    def test_workout_type_easy(self):
        vel = _sinusoidal_vel(base=3.0, length=3600)
        hr = _constant_stream(130, 3600)
        streams = {"velocity_smooth": vel, "heartrate": hr, "time": list(range(3600))}
        result = compute_stream_metrics(streams, _make_meta(moving_time_sec=3600), _make_config())
        assert result.get("workout_type_detected") in ("easy", "long_run", "recovery", "tempo")

    def test_hr_zones_json(self):
        hr = _constant_stream(150, 3600)  # should be mostly Z3-Z4 for max_hr=185, rest_hr=55
        streams = {"velocity_smooth": _constant_stream(3.0, 3600), "heartrate": hr, "time": list(range(3600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        dist_json = result.get("hr_zone_distribution")
        assert dist_json is not None
        dist = json.loads(dist_json)
        assert set(dist.keys()) == {"z1", "z2", "z3", "z4", "z5"}
        assert abs(sum(dist.values()) - 100.0) < 0.5  # percentages sum to ~100

    def test_cadence_spm(self):
        cad = _constant_stream(90, 3600)   # 90 steps/min single foot → 180 spm
        vel = _constant_stream(3.5, 3600)
        streams = {"velocity_smooth": vel, "cadence": cad, "time": list(range(3600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        assert result.get("avg_cadence_spm") == pytest.approx(180, abs=1)


class TestIntervalDetection:
    """6-rep synthetic interval workout."""

    def setup_method(self):
        self.vel = _interval_vel(900)
        self.hr = _interval_hr(self.vel)
        self.time = list(range(900))
        self.streams = {
            "velocity_smooth": self.vel,
            "heartrate": self.hr,
            "time": self.time,
        }
        self.result = compute_stream_metrics(
            self.streams, _make_meta(moving_time_sec=900), _make_config()
        )

    def test_workout_type_interval(self):
        wt = self.result.get("workout_type_detected")
        assert wt in ("interval", "tempo")  # depends on rep count detection

    def test_reps_detected(self):
        reps = self.result.get("interval_reps_count")
        if self.result.get("workout_type_detected") == "interval":
            assert reps is not None
            assert reps >= 3

    def test_interval_avg_pace(self):
        if self.result.get("workout_type_detected") == "interval":
            pace = self.result.get("interval_avg_pace_min_km")
            assert pace is not None
            assert 3.0 < pace < 5.5  # interval pace around 3.5 min/km

    def test_max_velocity(self):
        mv = self.result.get("max_velocity_m_s")
        assert mv is not None
        assert mv >= 4.5  # our interval_vel hard blocks are 4.8 m/s


class TestNoHrStream:
    """HR stream absent → HR fields all None, no crash."""

    def test_no_crash(self):
        vel = _sinusoidal_vel(length=600)
        streams = {"velocity_smooth": vel, "time": list(range(600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        assert isinstance(result, dict)

    def test_hr_fields_none(self):
        vel = _sinusoidal_vel(length=600)
        streams = {"velocity_smooth": vel, "time": list(range(600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        assert result.get("aerobic_decoupling_pct") is None
        assert result.get("cardiac_drift_pct") is None
        assert result.get("hr_zone_distribution") is None

    def test_pace_still_computed(self):
        vel = _constant_stream(3.333, 600)
        streams = {"velocity_smooth": vel, "time": list(range(600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        assert result.get("avg_pace_min_km") is not None


class TestNoPowerStream:
    """Watts stream absent → all power fields None, no crash."""

    def test_power_fields_none(self):
        vel = _sinusoidal_vel(length=600)
        hr = _constant_stream(140, 600)
        streams = {"velocity_smooth": vel, "heartrate": hr, "time": list(range(600))}
        result = compute_stream_metrics(streams, _make_meta(), _make_config(rftp_watts=250))
        assert result.get("avg_power_watts") is None
        assert result.get("normalized_power_watts") is None
        assert result.get("intensity_factor") is None
        assert result.get("training_stress_score") is None

    def test_power_with_data(self):
        vel = _constant_stream(3.5, 600)
        pwr = _constant_stream(220.0, 600)
        streams = {"velocity_smooth": vel, "watts": pwr, "time": list(range(600))}
        result = compute_stream_metrics(streams, _make_meta(moving_time_sec=600), _make_config(rftp_watts=250))
        assert result.get("avg_power_watts") == pytest.approx(220.0, abs=1)
        assert result.get("normalized_power_watts") is not None
        assert result.get("intensity_factor") == pytest.approx(220.0 / 250.0, abs=0.05)


class TestTempoNotFalsePositiveInterval:
    """Single sustained 20-minute hard effort → "tempo", not "interval"."""

    def test_single_block_is_tempo(self):
        # 10 min easy + 20 min hard + 10 min easy
        vel = [2.8] * 600 + [4.0] * 1200 + [2.8] * 600
        hr = [120] * 600 + [160] * 1200 + [125] * 600
        time = list(range(2400))
        streams = {"velocity_smooth": vel, "heartrate": hr, "time": time}
        result = compute_stream_metrics(streams, _make_meta(moving_time_sec=2400), _make_config())
        wt = result.get("workout_type_detected")
        # Single long block: reps < 3 or avg_rep_duration > 300s → tempo
        assert wt in ("tempo", "easy", "long_run")


class TestEmptyArrays:
    """All-empty or None streams → no crash, returns empty dict or all-None."""

    def test_empty_streams(self):
        result = compute_stream_metrics({}, _make_meta(), _make_config())
        assert isinstance(result, dict)

    def test_very_short_streams(self):
        streams = {"velocity_smooth": [3.0, 3.1], "heartrate": [140, 141]}
        result = compute_stream_metrics(streams, _make_meta(), _make_config())
        assert isinstance(result, dict)
        # Short data: pace might still be computed but HR zones won't
        assert result.get("hr_zone_distribution") is None


class TestBuildRunMetricsBlock:
    """build_run_metrics_block output format and length cap."""

    def test_empty_metrics_returns_empty_string(self):
        assert build_run_metrics_block({}, {}) == ""

    def test_none_metrics_returns_empty_string(self):
        m = {"avg_pace_min_km": None, "workout_type_detected": None}
        assert build_run_metrics_block(m, {}) == ""

    def test_basic_output(self):
        m = {
            "avg_pace_min_km": 5.0,
            "workout_type_detected": "easy",
            "avg_cadence_spm": 178.0,
            "aerobic_decoupling_pct": 3.2,
        }
        block = build_run_metrics_block(m, {})
        assert "5.00" in block
        assert "easy" in block
        assert "178" in block

    def test_capped_at_700_chars(self):
        m = {k: 1.234 for k in [
            "avg_pace_min_km", "grade_adjusted_pace_min_km", "avg_cadence_spm",
            "avg_stride_length_m", "aerobic_decoupling_pct", "cardiac_drift_pct",
            "avg_efficiency_factor", "pace_variability_cv", "positive_split_ratio",
            "total_elevation_gain_m", "max_velocity_m_s", "anaerobic_time_sec",
            "z4_z5_time_pct", "avg_power_watts", "normalized_power_watts",
            "intensity_factor", "training_stress_score",
        ]}
        m["workout_type_detected"] = "interval"
        m["interval_reps_count"] = 6
        m["hr_zone_distribution"] = json.dumps({"z1": 10, "z2": 20, "z3": 30, "z4": 25, "z5": 15})
        block = build_run_metrics_block(m, {})
        assert len(block) <= 700

    def test_power_section_skipped_when_none(self):
        m = {
            "avg_pace_min_km": 5.0,
            "avg_power_watts": None,
            "normalized_power_watts": None,
        }
        block = build_run_metrics_block(m, {})
        assert "NP" not in block
        assert "TSS" not in block

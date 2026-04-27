"""Tests for daily_suggestion.py — pure function, 10-rule priority chain."""
from app.agents.coach.daily_suggestion import compute_daily_suggestion, format_daily_suggestion_for_briefing


def _suggest(**kwargs):
    defaults = dict(
        readiness_score=70,
        acwr=1.0,
        recent_runs=[],
        athlete_state="healthy",
        day_of_week=1,
        days_since_last_run=1,
    )
    defaults.update(kwargs)
    return compute_daily_suggestion(**defaults)


class TestSickInjuredRule:
    def test_sick_returns_rest(self):
        s = _suggest(athlete_state="sick")
        assert s["workout_type"] == "Rest"
        assert "ốm" in s["description_vi"]

    def test_injured_returns_rest(self):
        s = _suggest(athlete_state="injured")
        assert s["workout_type"] == "Rest"
        assert "chấn thương" in s["description_vi"]


class TestAcwrCritical:
    def test_acwr_above_1_4_returns_rest(self):
        s = _suggest(acwr=1.5, athlete_state="healthy")
        assert s["workout_type"] == "Rest"
        assert "1.50" in s["description_vi"]

    def test_acwr_exactly_1_4_returns_rest(self):
        s = _suggest(acwr=1.41)
        assert s["workout_type"] == "Rest"


class TestLowReadiness:
    def test_readiness_below_40_returns_recovery(self):
        s = _suggest(readiness_score=35, acwr=1.0)
        assert s["workout_type"] == "Recovery"

    def test_readiness_none_defaults_to_65(self):
        s = _suggest(readiness_score=None, acwr=1.0, day_of_week=1)
        assert s["workout_type"] in ("Easy", "Tempo")


class TestDaysSinceLastRun:
    def test_3_days_no_run_returns_easy(self):
        s = _suggest(readiness_score=70, days_since_last_run=3)
        assert s["workout_type"] == "Easy"
        assert "ngày" in s["description_vi"]


class TestAcwrSafeMax:
    def test_acwr_above_1_3_returns_easy_short(self):
        s = _suggest(acwr=1.35, readiness_score=70)
        assert s["workout_type"] == "Easy"
        assert s.get("target_km", 10) <= 6.0


class TestModerateReadiness:
    def test_readiness_40_to_59_returns_easy_short(self):
        s = _suggest(readiness_score=50, acwr=1.0)
        assert s["workout_type"] == "Easy"


class TestWeekendLongRun:
    def test_saturday_good_readiness_returns_long_run(self):
        s = _suggest(readiness_score=70, acwr=1.0, day_of_week=5)
        assert s["workout_type"] == "LongRun"

    def test_sunday_good_readiness_returns_long_run(self):
        s = _suggest(readiness_score=75, acwr=1.0, day_of_week=6)
        assert s["workout_type"] == "LongRun"


class TestTempoRule:
    def test_excellent_readiness_no_quality_returns_tempo(self):
        s = _suggest(readiness_score=85, acwr=1.0, day_of_week=2, recent_runs=[])
        assert s["workout_type"] == "Tempo"

    def test_excellent_readiness_with_recent_quality_returns_easy(self):
        recent = [{"workout_type_detected": "tempo", "gcs_score": 8}]
        s = _suggest(readiness_score=85, acwr=1.0, day_of_week=2, recent_runs=recent)
        assert s["workout_type"] == "Easy"


class TestFallback:
    def test_good_readiness_weekday_returns_easy(self):
        s = _suggest(readiness_score=70, acwr=1.0, day_of_week=1)
        assert s["workout_type"] == "Easy"


class TestReasonField:
    def test_all_suggestions_have_reason(self):
        cases = [
            dict(athlete_state="sick"),
            dict(acwr=1.5),
            dict(readiness_score=30),
            dict(days_since_last_run=5),
            dict(acwr=1.35),
            dict(readiness_score=50),
            dict(readiness_score=70, day_of_week=5),
            dict(readiness_score=85, day_of_week=2, recent_runs=[]),
            dict(readiness_score=70, day_of_week=1),
        ]
        for kw in cases:
            s = _suggest(**kw)
            assert "reason" in s, f"Missing 'reason' for input {kw}"


class TestFormatBriefing:
    def test_format_includes_title(self):
        s = _suggest(readiness_score=70, day_of_week=5)
        text = format_daily_suggestion_for_briefing(s)
        assert "Long Run" in text or "Gợi ý" in text

    def test_format_no_plan_notice_included(self):
        s = _suggest()
        text = format_daily_suggestion_for_briefing(s)
        assert "/plan" in text

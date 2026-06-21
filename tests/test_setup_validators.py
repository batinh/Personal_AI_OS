"""Tests for app/agents/coach/setup_validators.py — all branches."""

from app.agents.coach.setup_validators import (
    validate_date,
    validate_days,
    validate_distance,
    validate_hr,
    validate_kmweek,
    validate_rest_days,
    validate_time,
)


class TestValidateDistance:
    def test_alias_fm(self):
        ok, val, _ = validate_distance("fm")
        assert ok and val == 42.2

    def test_alias_hm(self):
        ok, val, _ = validate_distance("HM")
        assert ok and val == 21.1

    def test_numeric_valid(self):
        ok, val, _ = validate_distance("10.5")
        assert ok and val == 10.5

    def test_comma_decimal(self):
        ok, val, _ = validate_distance("21,1")
        assert ok and val == 21.1

    def test_out_of_range_high(self):
        ok, val, err = validate_distance("500")
        assert not ok and val is None and err

    def test_out_of_range_low(self):
        ok, val, err = validate_distance("0.5")
        assert not ok and val is None and err

    def test_invalid_text(self):
        ok, val, err = validate_distance("marathon time")
        assert not ok and val is None and err


class TestValidateDate:
    def test_valid_dd_mm_yyyy(self):
        ok, val, _ = validate_date("01/01/2030")
        assert ok and val == "2030-01-01"

    def test_valid_iso(self):
        ok, val, _ = validate_date("2030-06-15")
        assert ok and val == "2030-06-15"

    def test_valid_dashes(self):
        ok, val, _ = validate_date("15-06-2030")
        assert ok and val == "2030-06-15"

    def test_too_soon(self):
        ok, val, err = validate_date("01/01/2000")
        assert not ok and val is None and "4 tuần" in err

    def test_invalid_format(self):
        ok, val, err = validate_date("not-a-date")
        assert not ok and val is None and err


class TestValidateTime:
    def test_h_mm_format(self):
        ok, mins, _ = validate_time("3:30", race_distance_km=42.2)
        assert ok and mins == 210

    def test_hh_mm_ss_format(self):
        ok, mins, _ = validate_time("1:45:30", race_distance_km=21.1)
        assert ok and mins == 105

    def test_plain_minutes(self):
        ok, mins, _ = validate_time("210", race_distance_km=42.2)
        assert ok and mins == 210

    def test_invalid_text_no_colon(self):
        ok, mins, err = validate_time("abc", race_distance_km=42.2)
        assert not ok and mins is None and err

    def test_invalid_text_with_colon(self):
        ok, mins, err = validate_time("xx:yy", race_distance_km=42.2)
        assert not ok and mins is None and err

    def test_pace_too_fast(self):
        ok, mins, err = validate_time("60", race_distance_km=42.2)
        assert not ok and mins is None and "quá nhanh" in err

    def test_pace_too_slow(self):
        ok, mins, err = validate_time("600", race_distance_km=42.2)
        assert not ok and mins is None and "quá chậm" in err

    def test_zero_distance_skips_pace_check(self):
        ok, mins, _ = validate_time("60", race_distance_km=0)
        assert ok and mins == 60


class TestValidateKmweek:
    def test_valid_int(self):
        ok, km, _ = validate_kmweek("50")
        assert ok and km == 50.0

    def test_valid_float(self):
        ok, km, _ = validate_kmweek("35,5")
        assert ok and km == 35.5

    def test_zero_valid(self):
        ok, km, _ = validate_kmweek("0")
        assert ok and km == 0.0

    def test_max_valid(self):
        ok, km, _ = validate_kmweek("200")
        assert ok and km == 200.0

    def test_out_of_range(self):
        ok, km, err = validate_kmweek("201")
        assert not ok and km is None and err

    def test_invalid_text(self):
        ok, km, err = validate_kmweek("abc")
        assert not ok and km is None and err


class TestValidateDays:
    def test_valid_3(self):
        ok, days, _ = validate_days("3")
        assert ok and days == 3

    def test_valid_6(self):
        ok, days, _ = validate_days("6")
        assert ok and days == 6

    def test_out_of_range_low(self):
        ok, days, err = validate_days("2")
        assert not ok and days is None and err

    def test_out_of_range_high(self):
        ok, days, err = validate_days("7")
        assert not ok and days is None and err

    def test_invalid_text(self):
        ok, days, err = validate_days("five")
        assert not ok and days is None and err


class TestValidateRestDays:
    def test_valid_monday(self):
        ok, days, _ = validate_rest_days("Monday", training_days=5)
        assert ok and 0 in days

    def test_valid_t2_t6(self):
        ok, days, _ = validate_rest_days("t2, t6", training_days=5)
        assert ok and set(days) == {0, 4}

    def test_viet_thu_hai(self):
        ok, days, _ = validate_rest_days("thứ hai", training_days=5)
        assert ok and 0 in days

    def test_empty_input(self):
        ok, days, err = validate_rest_days("", training_days=5)
        assert not ok and days is None and err

    def test_unknown_day(self):
        ok, days, err = validate_rest_days("funday", training_days=5)
        assert not ok and days is None and "Không nhận ra" in err

    def test_too_many_rest_days(self):
        ok, days, err = validate_rest_days("t2, t3, t4, t5", training_days=5)
        assert not ok and days is None and "tối đa" in err

    def test_dedup(self):
        ok, days, _ = validate_rest_days("t2, t2", training_days=5)
        assert ok and days.count(0) == 1


class TestValidateHr:
    def test_skip_keyword(self):
        ok, bpm, _ = validate_hr("/skip", kind="max")
        assert ok and bpm is None

    def test_skip_viet(self):
        ok, bpm, _ = validate_hr("bỏ qua", kind="max")
        assert ok and bpm is None

    def test_valid_max_hr(self):
        ok, bpm, _ = validate_hr("185", kind="max")
        assert ok and bpm == 185

    def test_invalid_max_hr_low(self):
        ok, bpm, err = validate_hr("90", kind="max")
        assert not ok and bpm is None and "120" in err

    def test_invalid_max_hr_high(self):
        ok, bpm, err = validate_hr("250", kind="max")
        assert not ok and bpm is None and err

    def test_valid_rest_hr(self):
        ok, bpm, _ = validate_hr("55", kind="rest")
        assert ok and bpm == 55

    def test_invalid_rest_hr(self):
        ok, bpm, err = validate_hr("20", kind="rest")
        assert not ok and bpm is None and "30" in err

    def test_invalid_text(self):
        ok, bpm, err = validate_hr("fast", kind="max")
        assert not ok and bpm is None and err

    def test_invalid_rest_text(self):
        ok, bpm, err = validate_hr("abc", kind="rest")
        assert not ok and bpm is None and "55" in err

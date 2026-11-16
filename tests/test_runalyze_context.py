"""Tests for runalyze_context.py — prompt formatting of the Runalyze daily load row."""

import json
from datetime import date

import pytest

from app.agents.coach import runalyze_context as rctx
from app.core import database

_TODAY = date(2026, 10, 24)

_ROW = {
    "date": "2026-10-24",
    "ctl": 54.0,
    "atl": 56.0,
    "tsb": -2.0,
    "marathon_shape": 31.0,
    "effective_vo2max": 36.47,
    "hrv_last": 50.0,
    "hrv_baseline": 57.57,
    "hrv_range_low": 45.68,
    "hrv_range_high": 55.09,
    "raw_json": json.dumps(
        {
            "prognosis": {
                "predictions_available": True,
                "distances": [
                    {"distance": 5, "prognosis_seconds": 1563},
                    {"distance": 10, "prognosis_seconds": 3246},
                    {"distance": 21.1, "prognosis_seconds": 7492},
                    {"distance": 42.2, "prognosis_seconds": 19208},
                    {"distance": 3, "prognosis_seconds": 911},
                ],
            },
            "planned_races": {
                "items": [
                    {
                        "name": "HCMC Marathon",
                        "date": "2027-01-10",
                        "priority": "A",
                        "days_until": 78,
                    }
                ]
            },
        }
    ),
}


class TestFormatContext:
    def test_returns_empty_when_no_row(self):
        assert rctx.format_runalyze_context(None, _TODAY) == ""

    def test_includes_load_numbers(self):
        text = rctx.format_runalyze_context(_ROW, _TODAY)
        assert "CTL 54" in text and "ATL 56" in text and "TSB -2" in text

    def test_does_not_expose_runalyze_acwr(self):
        row = {**_ROW, "acwr": 1.04}
        assert "ACWR" not in rctx.format_runalyze_context(row, _TODAY)

    @pytest.mark.parametrize(
        "tsb,label",
        [(10, "tươi"), (-2, "cân bằng"), (-15, "mệt"), (-40, "quá tải")],
    )
    def test_labels_tsb_bands(self, tsb, label):
        text = rctx.format_runalyze_context({**_ROW, "tsb": tsb}, _TODAY)
        assert label in text

    @pytest.mark.parametrize(
        "hrv,label", [(40, "thấp"), (50, "bình thường"), (60, "cao")]
    )
    def test_labels_hrv_against_normal_range(self, hrv, label):
        text = rctx.format_runalyze_context({**_ROW, "hrv_last": hrv}, _TODAY)
        assert label in text

    def test_omits_hrv_when_missing(self):
        row = {**_ROW, "hrv_last": None}
        assert "HRV" not in rctx.format_runalyze_context(row, _TODAY)

    def test_flags_stale_rows(self):
        text = rctx.format_runalyze_context({**_ROW, "date": "2026-10-22"}, _TODAY)
        assert "cũ 2 ngày" in text

    def test_no_stale_flag_for_today(self):
        assert "cũ" not in rctx.format_runalyze_context(_ROW, _TODAY)


class TestFormatPrognosis:
    def test_formats_standard_distances_in_order(self):
        text = rctx.format_runalyze_prognosis(_ROW)
        assert (
            text.index("5K") < text.index("10K") < text.index("HM") < text.index("FM")
        )
        assert "FM 5:20:08" in text and "5K 26:03" in text

    def test_skips_three_km_distance(self):
        assert "3K" not in rctx.format_runalyze_prognosis(_ROW)

    def test_empty_when_predictions_unavailable(self):
        row = {"raw_json": json.dumps({"prognosis": {"predictions_available": False}})}
        assert rctx.format_runalyze_prognosis(row) == ""

    def test_empty_when_row_or_json_missing(self):
        assert rctx.format_runalyze_prognosis(None) == ""
        assert rctx.format_runalyze_prognosis({"raw_json": None}) == ""
        assert rctx.format_runalyze_prognosis({"raw_json": "not json"}) == ""


class TestFormatRaces:
    def test_lists_planned_races_with_priority(self):
        text = rctx.format_runalyze_races(_ROW)
        assert "HCMC Marathon" in text and "2027-01-10" in text and "A" in text

    def test_empty_when_no_races(self):
        row = {"raw_json": json.dumps({"planned_races": {"items": []}})}
        assert rctx.format_runalyze_races(row) == ""


class TestDbWrapper:
    @pytest.fixture()
    def temp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.sqlite"))
        database.init_db()

    def test_returns_text_for_latest_row_within_staleness_window(self, temp_db):
        database.upsert_runalyze_daily_load(
            "u1", "2026-10-22", {"ctl": 50, "atl": 60, "tsb": -10}
        )
        text = rctx.get_runalyze_text("u1", _TODAY)
        assert "CTL 50" in text and "cũ 2 ngày" in text

    def test_returns_empty_when_nothing_stored(self, temp_db):
        assert rctx.get_runalyze_text("u1", _TODAY) == ""

    def test_returns_empty_when_row_older_than_window(self, temp_db):
        database.upsert_runalyze_daily_load("u1", "2026-10-10", {"ctl": 50})
        assert rctx.get_runalyze_text("u1", _TODAY) == ""

    def test_row_carries_date_for_staleness(self, temp_db):
        database.upsert_runalyze_daily_load("u1", "2026-10-24", {"ctl": 50})
        assert (
            database.get_runalyze_daily_load("u1", "2026-10-24")["date"] == "2026-10-24"
        )


class TestPromptWiring:
    def _block(self, **kw):
        from app.agents.coach.prompts import get_shared_context_block

        return get_shared_context_block(
            "now", "u1", "Build", "Còn 10 tuần", "1.1 (ok)", 10.0, "", **kw
        )

    def test_shared_context_omits_runalyze_line_when_empty(self):
        assert "Runalyze" not in self._block()

    def test_shared_context_includes_runalyze_line_when_given(self):
        block = self._block(runalyze_text="CTL 54 | ATL 56")
        assert "Runalyze" in block and "CTL 54 | ATL 56" in block

    def test_shared_context_unchanged_without_runalyze_text(self):
        assert self._block() == self._block(runalyze_text="")

    def test_weekly_plan_prompt_includes_prognosis_and_races_when_present(self):
        from app.agents.coach.flows.weekly_plan_generation import (
            _build_weekly_plan_prompt,
        )

        prompt = _build_weekly_plan_prompt(
            {},
            {
                "runalyze_prognosis": "Dự báo Runalyze (VO2max 36.5): FM 5:20:08",
                "runalyze_races": "HCMC 2027-01-10",
            },
        )
        assert "FM 5:20:08" in prompt and "HCMC 2027-01-10" in prompt

    def test_weekly_plan_prompt_unchanged_when_absent(self):
        from app.agents.coach.flows.weekly_plan_generation import (
            _build_weekly_plan_prompt,
        )

        assert "Runalyze" not in _build_weekly_plan_prompt({}, {})

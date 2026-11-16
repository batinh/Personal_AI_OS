"""Tests for runalyze_activities.py — Runalyze -> Strava-shaped mapping and client."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents.coach import runalyze_activities as ra
from app.agents.coach.metrics_engine import compute_stream_metrics
from app.agents.coach.runalyze_client import RunalyzeAuthError, RunalyzeError

_FIXTURE = Path(__file__).parent / "fixtures" / "runalyze_activity_detail.json"


@pytest.fixture()
def detail():
    return json.loads(_FIXTURE.read_text())["activity"]


def _item(**kw):
    base = {
        "id": 186846665,
        "title": "Tempo",
        "sport": "Running",
        "type": "Tempo Run",
        "date": "2026-06-26",
        "datetime": "2026-06-26T18:43:50+07:00",
        "distance": 7.02,
        "duration": 2597,
        "average_heart_rate": 152,
        "maximum_heart_rate": 178,
    }
    return {**base, **kw}


class TestToSummary:
    def test_converts_units_and_names(self):
        s = ra.to_summary(_item())
        assert s["id"] == 186846665 and s["name"] == "Tempo"
        assert s["distance"] == pytest.approx(7020.0)
        assert s["moving_time"] == 2597 and s["average_heartrate"] == 152

    def test_start_date_is_local_wall_clock_with_z_suffix(self):
        assert ra.to_summary(_item())["start_date_local"] == "2026-06-26T18:43:50Z"

    def test_running_maps_to_strava_run_type(self):
        assert ra.to_summary(_item())["type"] == "Run"

    def test_suffer_score_is_none(self):
        assert ra.to_summary(_item())["suffer_score"] is None

    def test_defaults_when_fields_missing(self):
        s = ra.to_summary({"id": 1, "sport": "Running", "date": "2026-01-01"})
        assert s["name"] == "Unknown Run" and s["distance"] == 0
        assert s["average_heartrate"] == 0

    def test_is_run_only_for_running_sport(self):
        assert ra.is_run(_item()) and not ra.is_run(_item(sport="Cycling"))


class TestDeriveVelocity:
    def test_constant_speed_is_exact(self):
        dist = [i * 3.0 for i in range(20)]
        assert all(
            v == pytest.approx(3.0) for v in ra.derive_velocity(dist, list(range(20)))
        )

    def test_zero_time_delta_gives_zero(self):
        assert ra.derive_velocity([0, 5], [10, 10]) == [0.0, 0.0]

    def test_output_length_matches_input(self):
        assert len(ra.derive_velocity([0.0, 1.0, 2.0], [0, 1, 2])) == 3


class TestToStravaStreams:
    def test_returns_none_without_time(self):
        assert ra.to_strava_streams(None) is None
        assert ra.to_strava_streams({"heart_rate": [1]}) is None

    def test_renames_to_strava_keys_in_key_by_type_form(self, detail):
        out = ra.to_strava_streams(detail["streams"])
        for key in (
            "time",
            "heartrate",
            "cadence",
            "watts",
            "altitude",
            "grade_smooth",
        ):
            assert "data" in out[key]

    def test_distance_converted_from_km_to_meters(self, detail):
        out = ra.to_strava_streams(detail["streams"])
        assert out["distance"]["data"][-1] == pytest.approx(
            detail["streams"]["distance"][-1] * 1000
        )

    def test_derives_velocity_stream_of_equal_length(self, detail):
        out = ra.to_strava_streams(detail["streams"])
        assert len(out["velocity_smooth"]["data"]) == len(detail["streams"]["time"])
        assert max(out["velocity_smooth"]["data"]) < 12  # m/s sanity

    def test_streams_feed_metrics_engine(self, detail):
        streams = ra.to_strava_streams(detail["streams"])
        meta = ra.to_extended_meta(detail, detail["streams"])
        m = compute_stream_metrics(streams, meta, {"max_hr": 185, "rest_hr": 55}, "t")
        assert m["avg_cadence_spm"] and 150 < m["avg_cadence_spm"] < 200
        assert m["aerobic_decoupling_pct"] is not None
        assert m["avg_power_watts"]


class TestSplitsAndMeta:
    def test_split_pace_in_m_per_s_and_hr_from_streams(self):
        streams = {"distance": [0, 0.5, 1.0, 1.5], "heart_rate": [100, 110, 150, 160]}
        out = ra.to_splits_summary(
            [
                {"index": 1, "distance": 1, "pace": 400},
                {"index": 2, "distance": 1, "pace": 250},
            ],
            streams,
        )
        assert out[0] == {"km": 1, "pace": 2.5, "hr": 105}
        assert out[1]["pace"] == 4.0 and out[1]["hr"] == 155

    def test_split_hr_zero_without_streams(self):
        assert (
            ra.to_splits_summary([{"index": 1, "distance": 1, "pace": 300}], None)[0][
                "hr"
            ]
            == 0
        )

    def test_extended_meta_has_pipeline_keys(self, detail):
        meta = ra.to_extended_meta(detail, detail["streams"])
        for key in (
            "moving_time",
            "distance",
            "average_heartrate",
            "splits",
            "best_efforts",
        ):
            assert key in meta
        assert meta["best_efforts"] == [] and meta["moving_time"] == detail["duration"]


class TestClient:
    def test_get_activity_data_returns_strava_style_tuple(self, detail):
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", return_value={"activity": detail}
        ):
            name, csv, meta, streams = ra.RunalyzeActivityClient().get_activity_data(
                "186846665"
            )
        assert name == detail["title"] and csv == ""
        assert meta["distance"] == pytest.approx(detail["distance"] * 1000)
        assert "velocity_smooth" in streams

    def test_non_run_returns_four_nones(self, detail):
        with patch.object(
            ra.RunalyzeActivityClient,
            "call_tool",
            return_value={"activity": {**detail, "sport": "Cycling"}},
        ):
            assert ra.RunalyzeActivityClient().get_activity_data(1) == (
                None,
                None,
                None,
                None,
            )

    def test_tool_failure_returns_four_nones(self):
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", side_effect=RunalyzeError("x")
        ):
            assert ra.RunalyzeActivityClient().get_activity_data(1) == (
                None,
                None,
                None,
                None,
            )

    def test_missing_streams_returns_meta_only(self, detail):
        bare = {**detail, "streams": {}}
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", return_value={"activity": bare}
        ):
            name, csv, meta, streams = ra.RunalyzeActivityClient().get_activity_data(1)
        assert name and csv is None and meta and streams is None

    def test_recent_activities_filters_non_runs(self):
        items = {"items": [_item(), _item(id=2, sport="Cycling")]}
        with patch.object(ra.RunalyzeActivityClient, "call_tool", return_value=items):
            out = ra.RunalyzeActivityClient().get_recent_activities(5)
        assert [a["id"] for a in out] == [186846665]

    def test_recent_activities_empty_on_failure(self):
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", side_effect=RunalyzeError("x")
        ):
            assert ra.RunalyzeActivityClient().get_recent_activities(5) == []

    def test_list_calls_use_extended_timeout(self):
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", return_value={"items": []}
        ) as call:
            ra.RunalyzeActivityClient().get_recent_activities(3)
        assert call.call_args.kwargs["timeout"] == ra.LIST_TIMEOUT_SEC


class TestListingAndStatus:
    def test_auth_error_propagates_from_listing(self):
        with patch.object(
            ra.RunalyzeActivityClient, "call_tool", side_effect=RunalyzeAuthError("x")
        ):
            with pytest.raises(RunalyzeAuthError):
                ra.RunalyzeActivityClient().get_recent_activities(3)

    def test_summary_carries_runalyze_source(self):
        assert ra.to_summary(_item())["source"] == "runalyze"

    def test_all_activities_walks_windows_and_concatenates_newest_first(self):
        calls = []

        def fake(self_, start, end, limit=200):
            calls.append((start, end))
            return [{"id": len(calls)}]

        with patch.object(ra.RunalyzeActivityClient, "get_activities_between", fake):
            out = ra.RunalyzeActivityClient().get_all_activities_paginated()
        assert len(calls) > 10
        assert calls[0][0] == ra.HISTORY_START
        assert out[0]["id"] == len(calls) and out[-1]["id"] == 1
        # windows are contiguous: each start is the day after the previous end
        from datetime import date, timedelta

        for (_, prev_end), (next_start, _) in zip(calls, calls[1:]):
            assert date.fromisoformat(next_start) == date.fromisoformat(
                prev_end
            ) + timedelta(days=1)

    @pytest.mark.parametrize(
        "exc,status",
        [
            (RunalyzeError("Tool x failed: Activity not found."), "not_found"),
            (RunalyzeError("boom"), "error"),
            (RunalyzeAuthError("401"), "forbidden"),
        ],
    )
    def test_detail_status_mapping(self, exc, status):
        with patch.object(ra.RunalyzeActivityClient, "call_tool", side_effect=exc):
            assert (
                ra.RunalyzeActivityClient().fetch_activity_detail_status("1")["status"]
                == status
            )

    def test_detail_status_exists(self):
        with patch.object(ra.RunalyzeActivityClient, "call_tool", return_value={}):
            assert (
                ra.RunalyzeActivityClient().fetch_activity_detail_status("1")["status"]
                == "exists"
            )


class TestGetRunsSince:
    def test_starts_at_given_date_and_ends_today(self):
        from datetime import date

        calls = []

        def fake(self_, start, end, limit=200):
            calls.append((start, end))
            return []

        with patch.object(ra.RunalyzeActivityClient, "get_activities_between", fake):
            ra.RunalyzeActivityClient().get_runs_since("2026-09-01")
        assert calls[0][0] == "2026-09-01"
        assert calls[-1][1] == date.today().isoformat()

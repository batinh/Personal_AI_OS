"""Runalyze activity source — Strava-shaped drop-in for ingest pipelines.

Strava's API no longer works, so run ingestion reads from the Runalyze MCP. This module
maps Runalyze payloads to the dict shapes the pipeline already consumes (Strava
summary dict, ``extended_meta`` and ``key_by_type`` streams), so ``metrics_engine``,
``build_activity_record`` and the DB layer stay unchanged.
"""

from datetime import date, timedelta
from typing import Any, Optional

import requests

from app.agents.coach.runalyze_client import (
    LIST_TIMEOUT_SEC,
    RunalyzeAuthError,
    RunalyzeClient,
    RunalyzeError,
)
from app.core.logging_conf import get_module_logger

logger = get_module_logger("runalyze_activities")

RUN_SPORT = "Running"
STRAVA_RUN_TYPE = "Run"  # value the pipeline's RUN_TYPES filter accepts
SOURCE_NAME = "runalyze"
HISTORY_START = "2021-01-01"  # Runalyze account history begins mid-2021
HISTORY_WINDOW_DAYS = 90  # keeps each get_activities page well under its 200 cap
NOT_FOUND_MARKER = "not found"
METERS_PER_KM = 1000.0
# Half-window (samples) for the central-difference velocity estimate.
VELOCITY_HALF_WINDOW = 2
DETAIL_STREAM_FIELDS = [
    "distance",
    "time",
    "heart_rate",
    "power_original",
    "cadence",
    "elevation_original",
    "grade",
]
# Runalyze stream name -> Strava key_by_type name.
_STREAM_RENAMES = {
    "time": "time",
    "heart_rate": "heartrate",
    "cadence": "cadence",
    "power_original": "watts",
    "elevation_original": "altitude",
    "grade": "grade_smooth",
}


# ── pure mappers ─────────────────────────────────────────────────────────────
def is_run(item: dict) -> bool:
    return item.get("sport") == RUN_SPORT


def _local_iso(item: dict) -> str:
    """Local wall-clock time with a 'Z' suffix — the format run_activities already stores."""
    return (item.get("datetime") or f"{item.get('date', '')}T00:00:00")[:19] + "Z"


def to_summary(item: dict) -> dict:
    """Runalyze list/detail item -> Strava activity-summary dict."""
    return {
        "id": item["id"],
        "name": item.get("title") or "Unknown Run",
        "type": STRAVA_RUN_TYPE if is_run(item) else item.get("sport"),
        "start_date_local": _local_iso(item),
        "distance": (item.get("distance") or 0) * METERS_PER_KM,
        "moving_time": item.get("duration") or 0,
        "elapsed_time": item.get("elapsed_time") or item.get("duration") or 0,
        "average_heartrate": item.get("average_heart_rate") or 0,
        "max_heartrate": item.get("maximum_heart_rate") or 0,
        "suffer_score": None,  # Strava-only metric
        "source": SOURCE_NAME,
    }


def derive_velocity(distance_m: list, time_s: list) -> list:
    """Central-difference speed (m/s); Runalyze has no velocity_smooth stream."""
    n = len(distance_m)
    out = []
    for i in range(n):
        lo, hi = max(0, i - VELOCITY_HALF_WINDOW), min(n - 1, i + VELOCITY_HALF_WINDOW)
        dt = time_s[hi] - time_s[lo]
        out.append(round((distance_m[hi] - distance_m[lo]) / dt, 3) if dt > 0 else 0.0)
    return out


def to_strava_streams(rz_streams: Optional[dict]) -> Optional[dict]:
    """Runalyze streams -> Strava ``key_by_type`` dict ({key: {"data": [...]}})."""
    if not rz_streams or not rz_streams.get("time"):
        return None
    out = {
        strava_key: {"data": rz_streams[rz_key]}
        for rz_key, strava_key in _STREAM_RENAMES.items()
        if rz_streams.get(rz_key) is not None
    }
    if rz_streams.get("distance"):
        distance_m = [(d or 0) * METERS_PER_KM for d in rz_streams["distance"]]
        out["distance"] = {"data": distance_m}
        out["velocity_smooth"] = {
            "data": derive_velocity(distance_m, rz_streams["time"])
        }
    return out


def _split_hr(streams: Optional[dict], start_km: float, end_km: float) -> int:
    """Mean HR over the samples whose cumulative distance falls inside a split."""
    if not streams or not streams.get("distance") or not streams.get("heart_rate"):
        return 0
    hrs = [
        hr
        for d, hr in zip(streams["distance"], streams["heart_rate"])
        if hr and start_km <= (d or 0) < end_km
    ]
    return round(sum(hrs) / len(hrs)) if hrs else 0


def to_splits_summary(splits: list, rz_streams: Optional[dict]) -> list:
    """Runalyze splits -> [{km, pace (m/s), hr}] as produced from Strava splits_metric."""
    out, start_km = [], 0.0
    for s in splits or []:
        end_km = start_km + (s.get("distance") or 0)
        pace_s_per_km = s.get("pace") or 0
        out.append(
            {
                "km": s.get("index"),
                "pace": round(METERS_PER_KM / pace_s_per_km, 3) if pace_s_per_km else 0,
                "hr": _split_hr(rz_streams, start_km, end_km),
            }
        )
        start_km = end_km
    return out


def to_extended_meta(detail: dict, rz_streams: Optional[dict]) -> dict:
    """Runalyze activity detail -> Strava ``extended_meta`` dict."""
    summary = to_summary(detail)
    return {
        "start_date_local": summary["start_date_local"],
        "moving_time": summary["moving_time"],
        "average_heartrate": summary["average_heartrate"],
        "max_heartrate": summary["max_heartrate"],
        "distance": summary["distance"],
        "suffer_score": None,
        "device_name": detail.get("source"),
        "splits": to_splits_summary(detail.get("splits"), rz_streams),
        "best_efforts": [],  # Strava-only
        "trimp_runalyze": detail.get("trimp"),
        "rpe_runalyze": detail.get("rpe"),
    }


# ── client ───────────────────────────────────────────────────────────────────
class RunalyzeActivityClient(RunalyzeClient):
    """Drop-in for the Strava client's activity methods used by harvest/webhooks."""

    def _list_runs(self, arguments: dict) -> list:
        """get_activities -> Strava-shaped run summaries.

        Auth failures propagate (callers alert); other failures yield [].
        """
        try:
            items = self.call_tool(
                "get_activities", arguments, timeout=LIST_TIMEOUT_SEC
            ).get("items", [])
        except RunalyzeAuthError:
            raise
        except (RunalyzeError, requests.RequestException, ValueError) as e:
            logger.error(f"[RUNALYZE] get_activities failed: {e}")
            return []
        return [to_summary(i) for i in items if is_run(i)]

    def get_recent_activities(self, limit: int = 10) -> list:
        """Newest-first run summaries (Strava-shaped)."""
        return self._list_runs({"limit": limit})

    def get_activities_between(
        self, start_date: str, end_date: str, limit: int = 200
    ) -> list:
        """Run summaries in an inclusive date window (YYYY-MM-DD), newest first."""
        return self._list_runs(
            {"start_date": start_date, "end_date": end_date, "limit": limit}
        )

    def get_runs_since(self, start_date: str) -> list:
        """Every run from start_date (YYYY-MM-DD) to today, newest first.

        Fetched in HISTORY_WINDOW_DAYS windows so each call stays under the list cap.
        """
        window = timedelta(days=HISTORY_WINDOW_DAYS)
        start, today = date.fromisoformat(start_date), date.today()
        runs: list = []
        while start <= today:
            end = min(start + window - timedelta(days=1), today)
            runs = (
                self.get_activities_between(start.isoformat(), end.isoformat()) + runs
            )
            start = end + timedelta(days=1)
        return runs

    def get_all_activities_paginated(self) -> list:
        """Every run since HISTORY_START."""
        return self.get_runs_since(HISTORY_START)

    def fetch_activity_detail_status(self, activity_id: Any) -> dict:
        """Existence check: {"status": "exists"|"not_found"|"forbidden"|"error"}."""
        try:
            self.call_tool("get_activity_details", {"activity_id": int(activity_id)})
            return {"status": "exists", "code": None}
        except RunalyzeAuthError:
            return {"status": "forbidden", "code": 401}
        except RunalyzeError as e:
            if NOT_FOUND_MARKER in str(e).lower():
                return {"status": "not_found", "code": None}
            return {"status": "error", "code": None}
        except (requests.RequestException, ValueError):
            return {"status": "error", "code": None}

    def get_activity_data(self, activity_id: Any):
        """Return (name, csv_data, extended_meta, key_by_type streams).

        Same 4-tuple contract as the Strava client; ``csv_data`` is always ``""``
        (no pipeline consumer uses it). Returns four Nones when not a run / on failure.
        """
        try:
            detail = self.call_tool(
                "get_activity_details",
                {
                    "activity_id": int(activity_id),
                    "include_streams": True,
                    "stream_fields": DETAIL_STREAM_FIELDS,
                },
                timeout=LIST_TIMEOUT_SEC,
            ).get("activity")
        except (RunalyzeError, requests.RequestException, ValueError) as e:
            logger.error(f"[RUNALYZE] get_activity_data({activity_id}) failed: {e}")
            return None, None, None, None
        if not detail or not is_run(detail):
            logger.info(f"[RUNALYZE] Activity {activity_id} is not a run. Skipping.")
            return None, None, None, None
        rz_streams = detail.get("streams") or {}
        meta = to_extended_meta(detail, rz_streams)
        streams = to_strava_streams(rz_streams)
        if streams is None:
            logger.warning(f"[RUNALYZE] No streams for {activity_id}; meta only.")
            return summary_name(detail), None, meta, None
        return summary_name(detail), "", meta, streams


def summary_name(detail: dict) -> str:
    return detail.get("title") or "Unknown Run"


def get_activity_client() -> RunalyzeActivityClient:
    return RunalyzeActivityClient()

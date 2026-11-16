"""Tests for runalyze_client.py + runalyze_daily_load DB round-trip (no network)."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import app.agents.coach.runalyze_client as rc
from app.core import database

_USER = "7939957821"
_DATE = "2026-10-04"

_CALC = {
    "effectiveVO2max": 39.41,
    "fitness": 49,
    "fatigue": 66,
    "performance": -17,
    "acuteChronicWorkloadRatio": 1.35,
    "marathonShape": 26,
    "restDays": 1.24,
    "monotonyValue": 41.0,
    "trainingStrain": 428.9,
    "hrvBaseline": 53.7,
    "hrvNormalRange": [42.5, 51.5],
}
_HEALTH = {
    "sleep": {"duration": 394, "quality": 78},
    "hrv": {"hrv": 51},
    "resting_heart_rate": {"heart_rate": 51},
    "weight": {"weight": 72.5},
}


@pytest.fixture(autouse=True)
def _api_token(monkeypatch):
    monkeypatch.setenv("RUNALYZE_API_TOKEN", "tok")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.sqlite"))
    database.init_db()


def _response(status=200, body=None, headers=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {"Content-Type": "application/json"}
    resp.json.return_value = body
    resp.text = text if text is not None else json.dumps(body)
    return resp


class TestApiToken:
    def test_returns_token_from_env(self):
        assert rc.get_api_token() == "tok"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("RUNALYZE_API_TOKEN", "  tok\n")
        assert rc.get_api_token() == "tok"

    def test_raises_auth_error_when_unset(self, monkeypatch):
        monkeypatch.delenv("RUNALYZE_API_TOKEN")
        with pytest.raises(rc.RunalyzeAuthError):
            rc.get_api_token()

    def test_sends_token_header_not_bearer(self):
        headers = rc.RunalyzeClient()._headers()
        assert headers["token"] == "tok"
        assert "Authorization" not in headers

    def test_maps_401_to_auth_error(self):
        client = rc.RunalyzeClient()
        with patch.object(rc.requests, "post", return_value=_response(status=401)):
            with pytest.raises(rc.RunalyzeAuthError):
                client._rpc("initialize")


class TestMcpTransport:
    def test_parses_sse_body_for_matching_rpc_id(self):
        sse = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":1}}\n\n'
        resp = _response(headers={"Content-Type": "text/event-stream"}, text=sse)
        assert rc._parse_rpc_response(resp, 2)["result"] == {"ok": 1}

    def test_raises_when_sse_lacks_matching_id(self):
        resp = _response(
            headers={"Content-Type": "text/event-stream"}, text="data: {}\n"
        )
        with pytest.raises(rc.RunalyzeError):
            rc._parse_rpc_response(resp, 9)

    def test_call_tool_decodes_text_content_json(self):
        client = rc.RunalyzeClient()
        client._session_id = "s"
        result = {"content": [{"type": "text", "text": '{"fitness": 49}'}]}
        with patch.object(client, "_rpc", return_value=result):
            assert client.call_tool("get_calculations") == {"fitness": 49}

    def test_call_tool_raises_on_tool_error(self):
        client = rc.RunalyzeClient()
        client._session_id = "s"
        with patch.object(client, "_rpc", return_value={"isError": True}):
            with pytest.raises(rc.RunalyzeError):
                client.call_tool("get_calculations")


class TestBuildMetrics:
    def test_flattens_calculations_and_health(self):
        m = rc.build_daily_load_metrics(_CALC, _HEALTH)
        assert (m["ctl"], m["atl"], m["tsb"]) == (49, 66, -17)
        assert m["hrv_range_low"] == 42.5 and m["hrv_range_high"] == 51.5
        assert m["sleep_quality"] == 78 and m["weight_kg"] == 72.5

    def test_tolerates_missing_health_sections(self):
        m = rc.build_daily_load_metrics({}, {"sleep": None})
        assert m["hrv_range_low"] is None and m["sleep_duration_min"] is None


class TestSyncRoundTrip:
    def _client_with_tools(self):
        client = rc.RunalyzeClient()
        payloads = {
            "get_calculations": _CALC,
            "get_health_metrics": _HEALTH,
            "get_planned_races": {"items": []},
            "get_prognosis": {"predictions_available": False},
        }
        client.call_tool = lambda name, args=None: payloads[name]
        return client

    def test_stores_row_and_reads_it_back(self, temp_db):
        ok = self._client_with_tools().fetch_and_store_daily_load(
            _USER, date.fromisoformat(_DATE)
        )
        row = database.get_runalyze_daily_load(_USER, _DATE)
        assert ok is True
        assert row["tsb"] == -17 and row["acwr"] == 1.35
        assert "planned_races" in json.loads(row["raw_json"])

    def test_upsert_overwrites_same_day(self, temp_db):
        database.upsert_runalyze_daily_load(_USER, _DATE, {"ctl": 1})
        database.upsert_runalyze_daily_load(_USER, _DATE, {"ctl": 2})
        assert database.get_runalyze_daily_load(_USER, _DATE)["ctl"] == 2

    def test_stale_fallback_returns_latest_within_window(self, temp_db):
        database.upsert_runalyze_daily_load(_USER, "2026-10-02", {"ctl": 7})
        assert database.get_runalyze_daily_load(_USER, _DATE) is None
        row = database.get_runalyze_daily_load(_USER, _DATE, max_stale_days=3)
        assert row["ctl"] == 7

    def test_returns_false_without_raising_on_auth_error(self, temp_db):
        client = rc.RunalyzeClient()

        def boom(*_a, **_k):
            raise rc.RunalyzeAuthError("no token")

        client.call_tool = boom
        assert client.fetch_and_store_daily_load(_USER) is False
        assert database.get_runalyze_daily_load(_USER, date.today().isoformat()) is None

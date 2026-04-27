"""Tests for garmin_client.py — mock garminconnect, circuit breaker."""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Redirect token/circuit files to a temporary directory."""
    import app.agents.coach.garmin_client as gc
    monkeypatch.setattr(gc, "_TOKEN_FILE", tmp_path / "garmin_tokens.json")
    monkeypatch.setattr(gc, "_CIRCUIT_STATE_FILE", tmp_path / "garmin_circuit.json")
    yield tmp_path


@pytest.fixture()
def gc_module():
    import app.agents.coach.garmin_client as gc
    return gc


class TestCircuitBreaker:
    def test_circuit_closed_initially(self, gc_module):
        assert not gc_module._is_circuit_open()

    def test_opens_after_3_failures(self, gc_module):
        gc_module._record_failure()
        gc_module._record_failure()
        just_opened = gc_module._record_failure()
        assert just_opened
        assert gc_module._is_circuit_open()

    def test_reset_closes_circuit(self, gc_module):
        gc_module._record_failure()
        gc_module._record_failure()
        gc_module._record_failure()
        gc_module._reset_circuit()
        assert not gc_module._is_circuit_open()

    def test_cooldown_expiry_closes_circuit(self, gc_module):
        state = {"failures": 3, "open_until": (datetime.utcnow() - timedelta(hours=1)).isoformat()}
        gc_module._save_circuit_state(state)
        assert not gc_module._is_circuit_open()


class TestFetchAndStore:
    def test_returns_false_when_circuit_open(self, gc_module, monkeypatch):
        monkeypatch.setattr(gc_module, "_is_circuit_open", lambda: True)
        client = gc_module.GarminClient()
        assert client.fetch_and_store_daily_metrics("user1") is False

    def test_returns_true_on_success(self, gc_module, monkeypatch):
        monkeypatch.setattr(gc_module, "_is_circuit_open", lambda: False)
        mock_metrics = {"training_readiness_score": 75, "resting_hr": 52}

        client = gc_module.GarminClient()
        monkeypatch.setattr(client, "_get_client", lambda: MagicMock())
        monkeypatch.setattr(client, "_collect_metrics", lambda c, d: mock_metrics)
        monkeypatch.setattr(gc_module, "upsert_garmin_daily_metrics", lambda *a: None)
        monkeypatch.setattr(gc_module, "_reset_circuit", lambda: None)

        result = client.fetch_and_store_daily_metrics("user1", date.today())
        assert result is True

    def test_records_failure_on_exception(self, gc_module, monkeypatch):
        monkeypatch.setattr(gc_module, "_is_circuit_open", lambda: False)
        failures = []

        def _bad_client():
            raise RuntimeError("network error")

        client = gc_module.GarminClient()
        monkeypatch.setattr(client, "_get_client", _bad_client)
        monkeypatch.setattr(gc_module, "_record_failure", lambda: failures.append(1) or False)

        client.fetch_and_store_daily_metrics("user1")
        assert failures


class TestGetDailyMetrics:
    def test_delegates_to_db(self, gc_module, monkeypatch):
        expected = {"training_readiness_score": 80}
        monkeypatch.setattr(gc_module, "get_garmin_daily_metrics", lambda uid, d, max_stale_days: expected)

        client = gc_module.GarminClient()
        result = client.get_daily_metrics("user1", date.today())
        assert result == expected

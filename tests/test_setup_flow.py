"""Tests for setup_flow.py — 6-step onboarding FSM."""
import json

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """Stub all DB calls so tests run without a real DB."""
    import app.agents.coach.setup_flow as sf

    sessions: dict = {}

    def _get(uid):
        return sessions.get(uid)

    def _upsert(uid, step, data, status="active"):
        sessions[uid] = {"step": step, "data": json.dumps(data), "status": status}

    def _complete(uid):
        if uid in sessions:
            sessions.pop(uid)

    def _abandon(hours):
        return 0

    monkeypatch.setattr(sf, "get_setup_session", _get)
    monkeypatch.setattr(sf, "upsert_setup_session", _upsert)
    monkeypatch.setattr(sf, "complete_setup_session", _complete)
    monkeypatch.setattr(sf, "abandon_stale_setup_sessions", _abandon)
    monkeypatch.setattr(sf, "save_config", lambda cfg: None)
    monkeypatch.setattr(sf, "load_config", lambda: {})
    yield sessions


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

class TestValidateDistance:
    def test_numeric(self):
        from app.agents.coach.setup_validators import validate_distance
        ok, val, _ = validate_distance("42.2")
        assert ok and val == 42.2

    def test_alias_hm(self):
        from app.agents.coach.setup_validators import validate_distance
        ok, val, _ = validate_distance("HM")
        assert ok and val == 21.1

    def test_alias_fm(self):
        from app.agents.coach.setup_validators import validate_distance
        ok, val, _ = validate_distance("FM")
        assert ok and val == 42.2

    def test_out_of_range(self):
        from app.agents.coach.setup_validators import validate_distance
        ok, _, err = validate_distance("250")
        assert not ok and err


class TestValidateDate:
    def test_valid_future(self):
        from app.agents.coach.setup_validators import validate_date
        ok, val, _ = validate_date("15/06/2030")
        assert ok and val == "2030-06-15"

    def test_too_soon(self):
        from app.agents.coach.setup_validators import validate_date
        ok, _, err = validate_date("01/01/2000")
        assert not ok and err

    def test_invalid_format(self):
        from app.agents.coach.setup_validators import validate_date
        ok, _, err = validate_date("not-a-date")
        assert not ok and err


class TestValidateTime:
    def test_hhmm_format(self):
        from app.agents.coach.setup_validators import validate_time
        ok, val, _ = validate_time("1:45", race_distance_km=21.1)
        assert ok and val == 105

    def test_minutes_format(self):
        from app.agents.coach.setup_validators import validate_time
        ok, val, _ = validate_time("105", race_distance_km=21.1)
        assert ok and val == 105

    def test_unreasonable_pace(self):
        from app.agents.coach.setup_validators import validate_time
        ok, _, err = validate_time("0:30", race_distance_km=21.1)
        assert not ok and err


class TestValidateKmWeek:
    def test_valid(self):
        from app.agents.coach.setup_validators import validate_kmweek
        ok, val, _ = validate_kmweek("35")
        assert ok and val == 35.0

    def test_out_of_range(self):
        from app.agents.coach.setup_validators import validate_kmweek
        ok, _, err = validate_kmweek("300")
        assert not ok and err


class TestValidateDays:
    def test_valid(self):
        from app.agents.coach.setup_validators import validate_days
        ok, val, _ = validate_days("5")
        assert ok and val == 5

    def test_too_few(self):
        from app.agents.coach.setup_validators import validate_days
        ok, _, err = validate_days("1")
        assert not ok and err


class TestValidateRestDays:
    def test_vietnamese_names(self):
        from app.agents.coach.setup_validators import validate_rest_days
        ok, val, _ = validate_rest_days("Thứ Hai, Thứ Sáu", training_days=5)
        assert ok
        assert 0 in val and 4 in val

    def test_aliases(self):
        from app.agents.coach.setup_validators import validate_rest_days
        ok, val, _ = validate_rest_days("T2, T6", training_days=5)
        assert ok and 0 in val and 4 in val

    def test_too_many_rest_days(self):
        from app.agents.coach.setup_validators import validate_rest_days
        ok, _, err = validate_rest_days("T2, T3, T4, T5, T6, T7", training_days=3)
        assert not ok and err


# ---------------------------------------------------------------------------
# Setup FSM
# ---------------------------------------------------------------------------

class TestSetupFSM:
    def test_start_setup_returns_step1_prompt(self):
        from app.agents.coach.setup_flow import start_setup
        prompt = start_setup("user1")
        assert "Bước 1" in prompt

    def test_happy_path_6_steps(self):
        from app.agents.coach.setup_flow import start_setup, advance_setup
        start_setup("user2")
        replies = [
            ("42.2", "Bước 2"),
            ("15/06/2030", "Bước 3"),
            ("4:00", "Bước 4"),
            ("40", "Bước 5"),
            ("5", "Bước 6"),
            ("T2, T6", "Thiết lập hoàn tất"),
        ]
        for user_input, expected_fragment in replies:
            resp = advance_setup("user2", user_input)
            assert expected_fragment in resp, f"Expected '{expected_fragment}' in response for input '{user_input}'"

    def test_invalid_input_repeats_same_step(self):
        from app.agents.coach.setup_flow import start_setup, advance_setup
        start_setup("user3")
        resp = advance_setup("user3", "not-a-distance")
        assert "Bước 1" in resp

    def test_is_setup_in_progress(self):
        from app.agents.coach.setup_flow import start_setup, is_setup_in_progress
        assert not is_setup_in_progress("user99")
        start_setup("user99")
        assert is_setup_in_progress("user99")

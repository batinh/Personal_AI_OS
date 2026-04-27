"""Tests for flows/weekly_plan_generation.py — schema validation, accept/reject."""
import json

import pytest

from app.agents.coach.schemas import WeeklyPlanResult, WorkoutDay


# ---------------------------------------------------------------------------
# Helper: minimal valid plan
# ---------------------------------------------------------------------------

def _make_plan(week_start: str = "2030-06-02") -> WeeklyPlanResult:
    days = []
    types = ["Easy", "Tempo", "Easy", "Rest", "Easy", "LongRun", "Rest"]
    for i, wt in enumerate(types):
        d = date_str_for_offset(week_start, i)
        days.append(WorkoutDay(
            date=d,
            workout_type=wt,
            title=f"Workout {i+1}",
            description="Training description",
            target_distance_km=8.0 if wt not in ("Rest",) else None,
            rpe_target=5 if wt not in ("Rest",) else None,
        ))
    return WeeklyPlanResult(
        week_start_date=week_start,
        week_total_km=55.0,
        training_rationale="Balanced build week with tempo and long run.",
        acwr_projection=1.05,
        days=days,
        adaptations_made=["Reduced Tuesday due to ACWR"],
    )


def date_str_for_offset(start: str, offset: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    return (d + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestWeeklyPlanResultSchema:
    def test_valid_plan_serialises(self):
        plan = _make_plan()
        assert len(plan.days) == 7
        data = json.loads(plan.model_dump_json())
        assert data["week_total_km"] == 55.0

    def test_hr_zone_out_of_range_raises(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            WorkoutDay(
                date="2030-06-02",
                workout_type="Easy",
                title="Test",
                description="Test",
                target_hr_zone=6,  # max is 5
            )

    def test_rpe_out_of_range_raises(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            WorkoutDay(
                date="2030-06-02",
                workout_type="Easy",
                title="Test",
                description="Test",
                rpe_target=11,  # max is 10
            )


# ---------------------------------------------------------------------------
# _validate_plan_constraints (logs only)
# ---------------------------------------------------------------------------

class TestValidatePlanConstraints:
    def test_no_warnings_for_valid_plan(self, caplog):
        import logging
        from app.agents.coach.flows.weekly_plan_generation import _validate_plan_constraints
        plan = _make_plan()
        with caplog.at_level(logging.WARNING, logger="weekly_plan_gen"):
            _validate_plan_constraints(plan)
        assert not caplog.records

    def test_warns_on_too_many_quality(self, caplog):
        import logging
        from app.agents.coach.flows.weekly_plan_generation import _validate_plan_constraints
        plan = _make_plan()
        for i in range(3):
            plan.days[i] = plan.days[i].model_copy(update={"workout_type": "Tempo"})
        with caplog.at_level(logging.WARNING, logger="weekly_plan_gen"):
            _validate_plan_constraints(plan)
        assert any("quality" in r.message for r in caplog.records)

    def test_warns_on_no_rest_day(self, caplog):
        import logging
        from app.agents.coach.flows.weekly_plan_generation import _validate_plan_constraints
        plan = _make_plan()
        plan.days[3] = plan.days[3].model_copy(update={"workout_type": "Easy"})
        plan.days[6] = plan.days[6].model_copy(update={"workout_type": "Easy"})
        with caplog.at_level(logging.WARNING, logger="weekly_plan_gen"):
            _validate_plan_constraints(plan)
        assert any("rest" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# accept_weekly_plan
# ---------------------------------------------------------------------------

class TestAcceptWeeklyPlan:
    def test_returns_not_found_message_when_no_pending(self, monkeypatch):
        import app.agents.coach.flows.weekly_plan_generation as wf
        monkeypatch.setattr(wf, "get_pending_weekly_plan", lambda uid, ws=None: None)
        result = wf.accept_weekly_plan("user1")
        assert "Không tìm thấy" in result

    def test_returns_confirmed_message_on_success(self, monkeypatch):
        import app.agents.coach.flows.weekly_plan_generation as wf
        plan = _make_plan()
        plan_row = {"id": 1, "ai_output": plan.model_dump_json()}
        monkeypatch.setattr(wf, "get_pending_weekly_plan", lambda uid, ws=None: plan_row)
        monkeypatch.setattr(wf, "update_weekly_plan_status", lambda *a, **kw: None)
        monkeypatch.setattr(wf, "_write_plan_to_training_plans", lambda *a: None)
        result = wf.accept_weekly_plan("user1")
        assert "xác nhận" in result


# ---------------------------------------------------------------------------
# reject_weekly_plan
# ---------------------------------------------------------------------------

class TestRejectWeeklyPlan:
    def test_returns_not_found_message_when_no_pending(self, monkeypatch):
        import app.agents.coach.flows.weekly_plan_generation as wf
        monkeypatch.setattr(wf, "get_pending_weekly_plan", lambda uid, ws=None: None)
        result = wf.reject_weekly_plan("user1", "too hard")
        assert "Không tìm thấy" in result

    def test_returns_rejected_message(self, monkeypatch):
        import app.agents.coach.flows.weekly_plan_generation as wf
        plan = _make_plan()
        plan_row = {"id": 1, "ai_output": plan.model_dump_json(), "week_start_date": "2030-06-02"}
        monkeypatch.setattr(wf, "get_pending_weekly_plan", lambda uid, ws=None: plan_row)
        monkeypatch.setattr(wf, "update_weekly_plan_status", lambda *a, **kw: None)

        from contextlib import contextmanager
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE weekly_plans (id INTEGER PRIMARY KEY, user_id TEXT, week_start_date TEXT, status TEXT, ai_output TEXT)")

        @contextmanager
        def _mock_db():
            yield conn

        import app.core.database as db_mod
        monkeypatch.setattr(db_mod, "get_db", _mock_db)
        result = wf.reject_weekly_plan("user1", "too hard")
        assert "Đã ghi nhận" in result

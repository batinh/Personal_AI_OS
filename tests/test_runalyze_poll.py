"""poll_new_runs, ingest dedupe and reconcile scoping for the Runalyze pipeline."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.agents.coach import harvest
from app.agents.coach.runalyze_client import RunalyzeAuthError

_NOW = datetime(2026, 10, 27, 12, 0, 0)


def _act(act_id, start, km=5.0, type_="Run"):
    return {
        "id": act_id,
        "name": "Run",
        "type": type_,
        "start_date_local": start,
        "distance": km * 1000,
        "moving_time": 1500,
        "average_heartrate": 140,
        "max_heartrate": 165,
        "source": "runalyze",
    }


@pytest.fixture()
def env(monkeypatch):
    """Patch the poller's collaborators; return handles."""
    client = MagicMock()
    monkeypatch.setattr(harvest, "get_activity_client", lambda: client)
    monkeypatch.setattr(harvest, "get_primary_user_id", lambda: "u1")
    monkeypatch.setattr(harvest, "init_db", lambda: None)
    monkeypatch.setattr(harvest, "load_config", lambda: {})
    monkeypatch.setattr(harvest, "has_run_activity", lambda _id: False)
    monkeypatch.setattr(harvest, "find_duplicate_run", lambda *a, **k: None)
    ingest = MagicMock()
    monkeypatch.setattr(harvest, "_ingest_one_activity", ingest)
    alerts = MagicMock()
    monkeypatch.setattr(harvest, "send_telegram_msg", alerts)
    monkeypatch.setattr(harvest, "_last_auth_alert_date", None)
    return client, ingest, alerts


class TestPollNewRuns:
    def test_recent_unseen_run_goes_through_full_workflow(self, env):
        client, ingest, _ = env
        client.get_recent_activities.return_value = [_act(1, "2026-10-27T06:00:00Z")]
        workflow = MagicMock()
        counts = harvest.poll_new_runs(workflow, now=_NOW)
        workflow.assert_called_once_with("1")
        assert counts == {"workflow": 1, "silent": 0}
        ingest.assert_not_called()

    def test_old_unseen_run_is_ingested_silently(self, env):
        client, ingest, _ = env
        client.get_recent_activities.return_value = [_act(2, "2026-09-01T06:00:00Z")]
        workflow = MagicMock()
        counts = harvest.poll_new_runs(workflow, now=_NOW)
        workflow.assert_not_called()
        ingest.assert_called_once()
        assert counts == {"workflow": 0, "silent": 1}

    def test_known_run_is_skipped(self, env, monkeypatch):
        client, ingest, _ = env
        monkeypatch.setattr(harvest, "has_run_activity", lambda _id: True)
        client.get_recent_activities.return_value = [_act(3, "2026-10-27T06:00:00Z")]
        workflow = MagicMock()
        assert harvest.poll_new_runs(workflow, now=_NOW) == {"workflow": 0, "silent": 0}
        workflow.assert_not_called()

    def test_duplicate_of_stored_run_is_skipped(self, env, monkeypatch):
        client, _, _ = env
        monkeypatch.setattr(harvest, "find_duplicate_run", lambda *a, **k: "strava-9")
        client.get_recent_activities.return_value = [_act(4, "2026-10-27T06:00:00Z")]
        workflow = MagicMock()
        harvest.poll_new_runs(workflow, now=_NOW)
        workflow.assert_not_called()

    def test_non_run_types_are_ignored(self, env):
        client, _, _ = env
        client.get_recent_activities.return_value = [
            _act(5, "2026-10-27T06:00:00Z", type_="Ride")
        ]
        workflow = MagicMock()
        harvest.poll_new_runs(workflow, now=_NOW)
        workflow.assert_not_called()

    def test_processes_oldest_first(self, env):
        client, _, _ = env
        client.get_recent_activities.return_value = [  # newest first, as returned
            _act(7, "2026-10-27T09:00:00Z"),
            _act(6, "2026-10-27T05:00:00Z"),
        ]
        order = []
        harvest.poll_new_runs(order.append, now=_NOW)
        assert order == ["6", "7"]

    def test_unparseable_start_is_treated_as_old(self, env):
        client, ingest, _ = env
        client.get_recent_activities.return_value = [_act(8, "garbage")]
        workflow = MagicMock()
        harvest.poll_new_runs(workflow, now=_NOW)
        workflow.assert_not_called()
        ingest.assert_called_once()

    def test_no_primary_user_does_nothing(self, env, monkeypatch):
        client, _, _ = env
        monkeypatch.setattr(harvest, "get_primary_user_id", lambda: None)
        assert harvest.poll_new_runs(MagicMock(), now=_NOW) == {
            "workflow": 0,
            "silent": 0,
        }
        client.get_recent_activities.assert_not_called()


class TestAuthFailureAlert:
    def test_alerts_user_once_per_day_on_auth_failure(self, env):
        client, _, alerts = env
        client.get_recent_activities.side_effect = RunalyzeAuthError("401")
        workflow = MagicMock()
        harvest.poll_new_runs(workflow, now=_NOW)
        harvest.poll_new_runs(workflow, now=_NOW)
        assert alerts.call_count == 1
        assert "RUNALYZE_API_TOKEN" in alerts.call_args.args[1]
        workflow.assert_not_called()


class TestIngestDedupe:
    def test_duplicate_run_is_not_saved_or_fetched(self, monkeypatch):
        monkeypatch.setattr(harvest, "find_duplicate_run", lambda *a, **k: "strava-1")
        save = MagicMock()
        monkeypatch.setattr(harvest, "save_run_activity", save)
        client = MagicMock()
        result = harvest._ingest_one_activity(
            _act(10, "2026-06-26T18:43:50Z", 7.02), "u1", {}, client
        )
        assert result["duplicate"] is True and result["loaded"] is False
        save.assert_not_called()
        client.get_activity_data.assert_not_called()

    def test_record_is_tagged_with_runalyze_source(self):
        record = harvest.build_activity_record(_act(11, "2026-10-01T06:00:00Z"))
        assert record["source"] == "runalyze"


class TestReconcileScope:
    def test_only_runalyze_rows_are_candidates(self, monkeypatch):
        listed = MagicMock(return_value=[])
        monkeypatch.setattr(harvest, "list_run_activity_ids_in_date_range", listed)
        harvest._reconcile("u1", MagicMock(), [_act(1, "2026-10-01T06:00:00Z")], 30)
        assert listed.call_args.kwargs["source"] == "runalyze"

    def test_removes_run_confirmed_missing_on_runalyze(self, monkeypatch):
        monkeypatch.setattr(
            harvest,
            "list_run_activity_ids_in_date_range",
            lambda *a, **k: [{"activity_id": "999", "start_date": "2026-10-01"}],
        )
        deleted, forgotten = MagicMock(), MagicMock()
        monkeypatch.setattr(harvest, "delete_run_activity", deleted)
        monkeypatch.setattr(harvest.rag_db, "forget", forgotten)
        monkeypatch.setattr(harvest, "send_telegram_msg", MagicMock())
        monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
        client = MagicMock()
        client.fetch_activity_detail_status.return_value = {"status": "not_found"}
        harvest._reconcile("u1", client, [_act(1, "2026-10-01T06:00:00Z")], 30)
        deleted.assert_called_once_with("999")

    def test_keeps_run_when_status_is_error(self, monkeypatch):
        monkeypatch.setattr(
            harvest,
            "list_run_activity_ids_in_date_range",
            lambda *a, **k: [{"activity_id": "999", "start_date": "2026-10-01"}],
        )
        deleted = MagicMock()
        monkeypatch.setattr(harvest, "delete_run_activity", deleted)
        monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
        client = MagicMock()
        client.fetch_activity_detail_status.return_value = {"status": "error"}
        harvest._reconcile("u1", client, [_act(1, "2026-10-01T06:00:00Z")], 30)
        deleted.assert_not_called()


class TestAthleteStats:
    def test_writes_json_from_db_totals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            harvest, "get_run_totals_km", lambda _u: {"recent_run_totals": 42.0}
        )
        harvest._write_athlete_stats("u1")
        assert (
            '"recent_run_totals": 42.0'
            in (tmp_path / "data" / "athlete_stats.json").read_text()
        )

    def test_writes_nothing_when_no_totals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(harvest, "get_run_totals_km", lambda _u: {})
        harvest._write_athlete_stats("u1")
        assert not (tmp_path / "data" / "athlete_stats.json").exists()


class TestBackfill:
    def _setup(self, env, runs, monkeypatch):
        client, ingest, _ = env
        client.get_runs_since.return_value = runs
        monkeypatch.setattr(harvest, "get_latest_run_date", lambda _u: "2026-06-26")
        monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
        return client, ingest

    def test_defaults_to_latest_stored_run_date(self, env, monkeypatch):
        client, _ = self._setup(env, [], monkeypatch)
        harvest.backfill_since()
        client.get_runs_since.assert_called_once_with("2026-06-26")

    def test_ingests_unseen_runs_oldest_first_and_counts(self, env, monkeypatch):
        runs = [_act(2, "2026-07-02T06:00:00Z"), _act(1, "2026-07-01T06:00:00Z")]
        _, ingest = self._setup(env, runs, monkeypatch)
        ingest.return_value = {
            "loaded": True,
            "duplicate": False,
            "memorized": True,
            "metrics": True,
        }
        counts = harvest.backfill_since()
        assert [c.args[0]["id"] for c in ingest.call_args_list] == [1, 2]
        assert counts == {
            "found": 2,
            "new": 2,
            "loaded": 2,
            "duplicates": 0,
            "memorized": 2,
            "metrics": 2,
        }

    def test_dry_run_writes_nothing(self, env, monkeypatch):
        _, ingest = self._setup(env, [_act(1, "2026-07-01T06:00:00Z")], monkeypatch)
        counts = harvest.backfill_since(dry_run=True)
        ingest.assert_not_called()
        assert counts["new"] == 1 and counts["loaded"] == 0

    def test_skips_runs_already_stored(self, env, monkeypatch):
        _, ingest = self._setup(env, [_act(1, "2026-07-01T06:00:00Z")], monkeypatch)
        monkeypatch.setattr(harvest, "has_run_activity", lambda _id: True)
        assert harvest.backfill_since()["new"] == 0
        ingest.assert_not_called()

    def test_nothing_to_do_without_stored_runs_or_since(self, env, monkeypatch):
        client, _, _ = env
        monkeypatch.setattr(harvest, "get_latest_run_date", lambda _u: None)
        assert harvest.backfill_since()["found"] == 0
        client.get_runs_since.assert_not_called()

    def test_ignores_non_run_types(self, env, monkeypatch):
        _, ingest = self._setup(
            env, [_act(1, "2026-07-01T06:00:00Z", type_="Ride")], monkeypatch
        )
        assert harvest.backfill_since()["found"] == 0
        ingest.assert_not_called()

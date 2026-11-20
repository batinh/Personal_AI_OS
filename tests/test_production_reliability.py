import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_unsigned_webhook_is_rejected(monkeypatch):
    from app.main import app

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    assert TestClient(app).post("/telegram-webhook", json={}).status_code == 403


def test_run_save_propagates_database_error():
    from app.core.database import save_run_activity

    with patch(
        "app.core.database.get_db_connection", side_effect=sqlite3.OperationalError
    ):
        with pytest.raises(sqlite3.OperationalError):
            save_run_activity("owner", {"activity_id": "123"})


def test_other_chat_is_ignored(monkeypatch):
    from app.main import app

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "100")
    client = TestClient(app, headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
    with patch("app.routers.webhooks.handle_telegram_chat") as chat:
        response = client.post(
            "/telegram-webhook", json={"message": {"chat": {"id": 200}, "text": "test"}}
        )
    assert response.status_code == 200
    chat.assert_not_called()


def test_callback_records_rpe_only_for_owner_and_valid_score(tmp_path, monkeypatch):
    from app.core import database
    from app.routers.webhooks import handle_telegram_callback

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    database.save_run_activity("owner", {"activity_id": "123"})
    with patch("app.routers.webhooks.answer_callback_query"):
        for sender, score, expected in [
            ("other", 7, None),
            ("owner", 11, None),
            ("owner", 7, 7),
        ]:
            handle_telegram_callback(
                {"id": "cb", "from": {"id": sender}, "data": f"rpe:123:{score}"}
            )
            with database.get_db() as db:
                assert (
                    db.execute("SELECT rpe_score FROM run_activities").fetchone()[
                        "rpe_score"
                    ]
                    == expected
                )


def test_retry_only_lists_pending_runs_and_preserves_source(tmp_path, monkeypatch):
    from datetime import date
    from app.core import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    for status in ("silent", "pending"):
        database.save_run_activity(
            "owner",
            {
                "activity_id": status,
                "source": "runalyze",
                "analysis_status": status,
                "start_date": date.today().isoformat(),
            },
        )
    pending = database.get_activities_needing_analysis("owner")
    assert [(r["activity_id"], r["source"]) for r in pending] == [
        ("pending", "runalyze")
    ]
    database.update_run_gcs_score("pending", "owner", 70)
    assert database.get_activities_needing_analysis("owner") == []

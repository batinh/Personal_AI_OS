"""Tests for athlete_state DB layer — state transitions and append-only audit."""

import pytest


@pytest.fixture(autouse=True)
def _in_memory_db(monkeypatch):
    """Override get_db to use an in-memory SQLite for each test."""
    import sqlite3
    from contextlib import contextmanager
    import app.core.database as db_mod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE athlete_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'healthy',
            note TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by TEXT DEFAULT 'user'
        )
    """)
    conn.commit()

    @contextmanager
    def _mock_get_db():
        yield conn

    monkeypatch.setattr(db_mod, "get_db", _mock_get_db)
    yield conn
    conn.close()


class TestAthleteState:
    def test_default_state_is_healthy(self):
        from app.core.database import get_athlete_state

        assert get_athlete_state("user1") == "healthy"

    def test_set_and_get_state(self):
        from app.core.database import set_athlete_state, get_athlete_state

        set_athlete_state("user1", "sick")
        assert get_athlete_state("user1") == "sick"

    def test_state_is_append_only(self, _in_memory_db):
        from app.core.database import set_athlete_state

        set_athlete_state("user1", "sick")
        set_athlete_state("user1", "healthy")
        rows = _in_memory_db.execute(
            "SELECT state FROM athlete_state WHERE user_id='user1' ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["state"] == "sick"
        assert rows[1]["state"] == "healthy"

    def test_latest_row_is_current_state(self):
        from app.core.database import set_athlete_state, get_athlete_state

        set_athlete_state("user1", "sick")
        set_athlete_state("user1", "injured")
        set_athlete_state("user1", "healthy")
        assert get_athlete_state("user1") == "healthy"

    def test_multi_tenant_isolation(self):
        from app.core.database import set_athlete_state, get_athlete_state

        set_athlete_state("user_a", "sick")
        set_athlete_state("user_b", "injured")
        assert get_athlete_state("user_a") == "sick"
        assert get_athlete_state("user_b") == "injured"

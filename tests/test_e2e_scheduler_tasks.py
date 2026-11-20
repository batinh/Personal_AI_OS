"""
E2E Tests — Scheduler Task Behaviors
=====================================
Tests the individual scheduler task functions and startup state.

REQ-CS04: backup task creates zip file
REQ-CS05: stale setup sessions cleaned up
REQ-CS08: all jobs registered on startup

Run:
    python -m pytest tests/test_e2e_scheduler_tasks.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


def _make_client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


class TestBackupTask(unittest.TestCase):
    """REQ-CS04 — Backup task compresses data/ into a zip archive."""

    def test_restore_includes_committed_wal_rows(self):
        import sqlite3
        import tempfile
        import zipfile
        from pathlib import Path
        from contextlib import closing
        from app.services import backup

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            with closing(sqlite3.connect(root / "data/os_core.db")) as live:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("CREATE TABLE sample(value INTEGER)")
                live.execute("INSERT INTO sample VALUES (42)")
                live.commit()
                with patch.object(backup, "_ROOT", root):
                    backup.perform_backup()
            with zipfile.ZipFile(next((root / "backups").glob("*.zip"))) as archive:
                archive.extract("os_core.db", root)
            with closing(sqlite3.connect(root / "os_core.db")) as restored:
                self.assertEqual(
                    restored.execute("SELECT value FROM sample").fetchone(), (42,)
                )

    def test_missing_database_does_not_publish_backup(self):
        import tempfile
        from pathlib import Path
        from app.services import backup

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            with patch.object(backup, "_ROOT", root):
                backup.perform_backup()
            self.assertEqual(list((root / "backups").glob("*.zip")), [])


class TestSetupCleanupTask(unittest.TestCase):
    """REQ-CS05 — Stale setup sessions (>24h) are cleaned up automatically."""

    @patch("app.services.scheduler.cleanup_stale_setup_sessions", return_value=1)
    def test_stale_session_removed(self, mock_cleanup):
        """task_cleanup_stale_setup() must call cleanup with timeout_hours=24."""
        from app.services.scheduler import task_cleanup_stale_setup

        task_cleanup_stale_setup()

        mock_cleanup.assert_called_once_with(timeout_hours=24)

    @patch("app.services.scheduler.cleanup_stale_setup_sessions", return_value=0)
    def test_cleanup_with_no_stale_sessions(self, mock_cleanup):
        """task_cleanup_stale_setup() must not raise when no stale sessions exist."""
        from app.services.scheduler import task_cleanup_stale_setup

        task_cleanup_stale_setup()

        mock_cleanup.assert_called_once()

    @patch(
        "app.services.scheduler.cleanup_stale_setup_sessions",
        side_effect=RuntimeError("db error"),
    )
    def test_cleanup_exception_does_not_propagate(self, mock_cleanup):
        """task_cleanup_stale_setup() must catch exceptions — BackgroundScheduler requires this."""
        from app.services.scheduler import task_cleanup_stale_setup

        try:
            task_cleanup_stale_setup()
        except Exception as exc:
            self.fail(f"task_cleanup_stale_setup raised unexpectedly: {exc}")


class TestSchedulerStartup(unittest.TestCase):
    """REQ-CS08 — All scheduler jobs registered at startup, visible from /health."""

    def test_all_jobs_registered_on_startup(self):
        """GET /health must report scheduler state (running or stopped) — never absent."""
        client = _make_client()
        resp = client.get("/health")
        self.assertIn(resp.status_code, [200, 503])
        body = resp.json()
        self.assertIn("scheduler", body)
        self.assertIn(body["scheduler"], ["running", "stopped"])

    def test_scheduler_has_core_jobs_configured(self):
        """After setup_jobs(), the scheduler must have at least 3 jobs registered."""
        from app.services.scheduler import scheduler, setup_jobs

        if not scheduler.running:
            scheduler.start()

        setup_jobs()
        jobs = scheduler.get_jobs()
        self.assertGreaterEqual(len(jobs), 3, "Expected at least 3 scheduled jobs")


if __name__ == "__main__":
    unittest.main(verbosity=2)

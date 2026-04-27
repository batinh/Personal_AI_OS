"""
Tests for the Log Audit feature.

Covers:
- categorize_line: pattern matching for all severity/category combos
- run_audit: file scanning, deduplication, return count
- DB functions: insert_audit_entry, get_audit_entries, update_audit_status, get_audit_stats
- API endpoints: /audit/api/entries, /audit/api/entries/{id}/acknowledge, /audit/api/run
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services.log_auditor import categorize_line, run_audit
from app.core.database import (
    insert_audit_entry,
    get_audit_entries,
    update_audit_status,
    get_audit_stats,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """In-memory isolated SQLite for DB function tests."""
    import app.core.database as db_module

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))

    # Re-run init_db against the temp file
    from app.core.database import init_db

    init_db()
    return str(db_file)


@pytest.fixture()
def auth_headers(monkeypatch):
    """Basic-auth headers — force env to match so tests are isolated from .env."""
    import base64

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    creds = base64.b64encode(b"admin:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture()
def client(tmp_db, auth_headers):
    """FastAPI test client with isolated DB."""
    from app.main import app

    return TestClient(app, raise_server_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. categorize_line — unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCategorizeLine:
    def test_traceback(self):
        result = categorize_line("Traceback (most recent call last):")
        assert result is not None
        severity, category, _ = result
        assert severity == "error"
        assert category == "crash"

    def test_exception(self):
        result = categorize_line("Exception: something went wrong")
        assert result is not None
        assert result[0] == "error"
        assert result[1] == "crash"

    def test_dns_error(self):
        result = categorize_line("NameResolutionError: getaddrinfo failed")
        assert result is not None
        assert result[0] == "error"
        assert result[1] == "network"

    def test_connection_error(self):
        result = categorize_line("ConnectionError: remote end closed")
        assert result is not None
        assert result[1] == "network"

    def test_news_scorer_json(self):
        result = categorize_line("[NEWS-SCORER] JSONDecodeError: Expecting value")
        assert result is not None
        assert result[1] == "news_scoring"
        assert result[0] == "warning"

    def test_news_agent_error(self):
        result = categorize_line("[NEWS-WATCH] Error fetching feed")
        assert result is not None
        assert result[1] == "news_agent"

    def test_performance_timeout(self):
        result = categorize_line("request timed out after 30 seconds")
        assert result is not None
        assert result[1] == "performance"

    def test_scheduler_error(self):
        result = categorize_line("[SCHEDULER] Error running job")
        assert result is not None
        assert result[1] == "scheduler"
        assert result[0] == "error"

    def test_db_error(self):
        result = categorize_line("[DB_ERROR] sqlite3.OperationalError")
        assert result is not None
        assert result[1] == "database"
        assert result[0] == "error"

    def test_improvement_hint(self):
        result = categorize_line("[IMPROVEMENT] Consider batching queries")
        assert result is not None
        assert result[1] == "improvement"
        assert result[0] == "info"

    def test_todo(self):
        result = categorize_line("# TODO: handle edge case")
        assert result is not None
        assert result[1] == "improvement"

    def test_generic_warning(self):
        result = categorize_line("2025-01-01 [WARNING] disk space low")
        assert result is not None
        assert result[0] == "warning"
        assert result[1] == "general"

    def test_generic_error(self):
        result = categorize_line("2025-01-01 [ERROR] something failed")
        assert result is not None
        assert result[0] == "error"
        assert result[1] == "general"

    def test_critical(self):
        result = categorize_line("[CRITICAL] System failure")
        assert result is not None
        assert result[0] == "error"

    def test_normal_info_line_skipped(self):
        result = categorize_line("2025-01-01 [INFO] Scheduler started")
        assert result is None

    def test_empty_line_skipped(self):
        result = categorize_line("")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. run_audit — integration tests (file scanning)
# ─────────────────────────────────────────────────────────────────────────────


class TestRunAudit:
    def test_returns_zero_when_no_log_file(self, tmp_db):
        with patch(
            "app.services.log_auditor.LOG_FILE_PATH", Path("/nonexistent/app.log")
        ):
            count = run_audit("test_user")
        assert count == 0

    def test_scans_log_and_returns_count(self, tmp_db, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text(
            "2025-01-01 [ERROR] DB failed\n"
            "2025-01-01 [INFO] normal line\n"
            "Traceback (most recent call last):\n",
            encoding="utf-8",
        )
        with patch("app.services.log_auditor.LOG_FILE_PATH", log_file):
            count = run_audit("test_user")
        assert count == 2

    def test_deduplication_on_rerun(self, tmp_db, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("2025-01-01 [ERROR] repeated error\n", encoding="utf-8")
        with patch("app.services.log_auditor.LOG_FILE_PATH", log_file):
            first = run_audit("test_user")
            second = run_audit("test_user")
        assert first == 1
        assert second == 0  # already inserted, UNIQUE constraint triggers

    def test_scans_rotated_files(self, tmp_db, tmp_path):
        (tmp_path / "app.log").write_text("[ERROR] from main log\n", encoding="utf-8")
        (tmp_path / "app.log.1").write_text(
            "[ERROR] from rotated log\n", encoding="utf-8"
        )
        main_log = tmp_path / "app.log"
        with patch("app.services.log_auditor.LOG_FILE_PATH", main_log):
            count = run_audit("test_user")
        assert count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. DB functions — unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditDbFunctions:
    def test_insert_and_get(self, tmp_db):
        ok = insert_audit_entry(
            "u1", "error", "network", "DNS failure", "raw line here"
        )
        assert ok is True
        entries = get_audit_entries("u1")
        assert len(entries) == 1
        e = entries[0]
        assert e["severity"] == "error"
        assert e["category"] == "network"
        assert e["status"] == "open"

    def test_insert_duplicate_returns_false(self, tmp_db):
        insert_audit_entry("u1", "error", "network", "DNS failure", "same raw line")
        ok2 = insert_audit_entry(
            "u1", "error", "network", "DNS failure", "same raw line"
        )
        assert ok2 is False

    def test_filter_by_status(self, tmp_db):
        insert_audit_entry("u1", "error", "general", "msg", "raw1")
        insert_audit_entry("u1", "warning", "general", "msg", "raw2")
        open_entries = get_audit_entries("u1", status="open")
        assert len(open_entries) == 2

    def test_filter_by_severity(self, tmp_db):
        insert_audit_entry("u1", "error", "general", "msg1", "raw-e")
        insert_audit_entry("u1", "warning", "general", "msg2", "raw-w")
        errors = get_audit_entries("u1", severity="error")
        assert all(e["severity"] == "error" for e in errors)
        assert len(errors) == 1

    def test_update_status(self, tmp_db):
        insert_audit_entry("u1", "error", "general", "msg", "raw-update")
        entries = get_audit_entries("u1")
        entry_id = entries[0]["id"]
        ok = update_audit_status(entry_id, "resolved")
        assert ok is True
        updated = get_audit_entries("u1", status="resolved")
        assert len(updated) == 1

    def test_get_stats(self, tmp_db):
        insert_audit_entry("u1", "error", "general", "e1", "raw-s1")
        insert_audit_entry("u1", "warning", "network", "w1", "raw-s2")
        stats = get_audit_stats("u1")
        assert stats["total"] == 2
        assert stats["by_severity"]["error"] == 1
        assert stats["by_severity"]["warning"] == 1
        assert stats["by_status"]["open"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditApi:
    def test_list_entries_empty(self, client, auth_headers, tmp_db):
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"):
            r = client.get("/audit/api/entries", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "entries" in data
        assert "stats" in data

    def test_list_entries_requires_auth(self, client):
        r = client.get("/audit/api/entries")
        assert r.status_code == 401

    def test_acknowledge(self, client, auth_headers, tmp_db):
        insert_audit_entry("u1", "error", "general", "msg", "raw-ack")
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"):
            entries_r = client.get("/audit/api/entries", headers=auth_headers)
        entry_id = entries_r.json()["entries"][0]["id"]
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"):
            r = client.post(
                f"/audit/api/entries/{entry_id}/acknowledge", headers=auth_headers
            )
        assert r.status_code == 200
        assert r.json()["status"] == "acknowledged"

    def test_resolve(self, client, auth_headers, tmp_db):
        insert_audit_entry("u1", "error", "general", "msg", "raw-resolve")
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"):
            entries_r = client.get("/audit/api/entries", headers=auth_headers)
        entry_id = entries_r.json()["entries"][0]["id"]
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"):
            r = client.post(
                f"/audit/api/entries/{entry_id}/resolve", headers=auth_headers
            )
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

    def test_run_audit_now(self, client, auth_headers, tmp_db, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("[ERROR] test error for manual run\n", encoding="utf-8")
        with patch("app.routers.audit.get_primary_user_id", return_value="u1"), patch(
            "app.routers.audit.run_audit", return_value=1
        ) as mock_run:
            r = client.post("/audit/api/run", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["new_entries"] == 1
        mock_run.assert_called_once_with("u1")

    def test_audit_page_html(self, client, auth_headers):
        r = client.get("/audit", headers=auth_headers)
        assert r.status_code == 200
        assert b"Log Audit" in r.content

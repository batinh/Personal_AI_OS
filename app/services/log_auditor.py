"""
Log Auditor — periodic log scanning and issue extraction.

Reads data/app.log, detects errors/warnings/crashes/network issues, and
stores structured findings in the audit_entries table for the web UI and
future AI-assisted analysis sessions.

Usage:
    from app.services.log_auditor import run_audit
    count = run_audit(user_id="123456")   # returns number of new entries inserted
"""

import re
from pathlib import Path
from typing import Tuple

from app.core.database import insert_audit_entry

from app.core.logging_conf import get_module_logger

logger = get_module_logger("audit")

# Absolute path to log file — must match logging_conf.LOG_FILE_PATH.
# Written to ./logs/ which is bind-mounted out of the container.
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_FILE_PATH = _BASE_DIR / "logs" / "app.log"

# ---------------------------------------------------------------------------
# Detection patterns — ordered from most specific to least specific
# Each entry: (pattern, severity, category, human_readable_message)
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[re.Pattern, str, str, str]] = [
    # Tracebacks / unhandled exceptions
    (
        re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
        "error",
        "crash",
        "Unhandled exception (Traceback)",
    ),
    (
        re.compile(r"\bException\b.*:", re.IGNORECASE),
        "error",
        "crash",
        "Exception raised",
    ),
    # Network / DNS errors
    (
        re.compile(
            r"NameResolutionError|getaddrinfo failed|Name or service not known",
            re.IGNORECASE,
        ),
        "error",
        "network",
        "DNS resolution failure",
    ),
    (
        re.compile(
            r"ConnectionError|ConnectTimeout|ReadTimeout|RemoteDisconnected",
            re.IGNORECASE,
        ),
        "error",
        "network",
        "Network connection error",
    ),
    # News scorer JSON parse failures
    (
        re.compile(
            r"\[NEWS-SCORER\].*(?:JSONDecodeError|Could not extract JSON|json)",
            re.IGNORECASE,
        ),
        "warning",
        "news_scoring",
        "News scorer JSON parse failure",
    ),
    (
        re.compile(r"\[NEWS.*\].*(?:Error|error|fail|Fail)", re.IGNORECASE),
        "warning",
        "news_agent",
        "News agent error",
    ),
    # Performance issues
    (
        re.compile(r"timeout|timed out|took \d+\.?\d* seconds|slow", re.IGNORECASE),
        "warning",
        "performance",
        "Performance or timeout issue",
    ),
    # Scheduler / task errors
    (
        re.compile(r"\[SCHEDULER\].*(?:Error|error|fail|exception)", re.IGNORECASE),
        "error",
        "scheduler",
        "Scheduler task error",
    ),
    # Database errors
    (
        re.compile(r"\[DB_ERROR\]|\[DATABASE\].*(?:Error|fail)", re.IGNORECASE),
        "error",
        "database",
        "Database error",
    ),
    # Generic improvement hints (info-level suggestions in code)
    (
        re.compile(r"\[IMPROVEMENT\]|\bTODO\b|\bFIXME\b", re.IGNORECASE),
        "info",
        "improvement",
        "Improvement hint in logs",
    ),
    # Generic WARNING lines (catch-all after specific checks)
    (re.compile(r"\[WARNING\]"), "warning", "general", "Warning"),
    # Generic ERROR / CRITICAL lines (catch-all last)
    (re.compile(r"\[ERROR\]|\[CRITICAL\]"), "error", "general", "Error"),
]


def categorize_line(raw_line: str) -> Tuple[str, str, str] | None:
    """
    Classify a log line by severity, category, and message.

    Returns (severity, category, message) tuple, or None if the line
    is not interesting (info-level routine lines are skipped).
    """
    for pattern, severity, category, message in _PATTERNS:
        if pattern.search(raw_line):
            return severity, category, message
    return None


def run_audit(user_id: str) -> int:
    """
    Scan app.log, extract actionable entries, persist new ones to DB.

    Deduplication is handled at the DB layer via UNIQUE(user_id, raw_line),
    so re-running is always safe.

    Returns:
        Number of new audit entries inserted.
    """
    if not LOG_FILE_PATH.exists():
        logger.info("[AUDIT] Log file not found yet — skipping audit.")
        return 0

    inserted = 0
    lines_scanned = 0

    try:
        # Read all rotated log files: app.log, app.log.2026-04-18, app.log.2026-04-17, …
        # TimedRotatingFileHandler uses date suffixes — sort lexicographically (YYYY-MM-DD is chronological).
        log_files = sorted(LOG_FILE_PATH.parent.glob("app.log*"))

        for log_path in log_files:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    for raw_line in f:
                        raw_line = raw_line.rstrip("\n")
                        lines_scanned += 1
                        result = categorize_line(raw_line)
                        if result:
                            severity, category, message = result
                            # Truncate very long lines to avoid bloating DB
                            stored_line = raw_line[:1000]
                            did_insert = insert_audit_entry(
                                user_id=user_id,
                                severity=severity,
                                category=category,
                                message=message,
                                raw_line=stored_line,
                            )
                            if did_insert:
                                inserted += 1
            except (OSError, PermissionError) as e:
                logger.warning(f"[AUDIT] Could not read {log_path}: {e}")

        logger.info(
            f"[AUDIT] Scan complete — {lines_scanned} lines scanned, "
            f"{inserted} new entries inserted."
        )
    except Exception as e:
        logger.error(f"[AUDIT] Unexpected error during audit run: {e}")

    return inserted

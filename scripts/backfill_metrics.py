#!/usr/bin/env python3
"""
Backfill pre-computed running science metrics for all historical activities.

Usage:
    python scripts/backfill_metrics.py
    python scripts/backfill_metrics.py --dry-run   # show counts only, write nothing

Iterates run_activity_raw rows where stream_file_path IS NOT NULL,
loads each stream file, computes metrics, and upserts into run_computed_metrics.
Rows that already have metrics are skipped unless --force is passed.
"""

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

# Allow imports from project root
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import load_config  # noqa: E402
from app.core.database import get_db_connection, init_db, upsert_run_computed_metrics  # noqa: E402
from app.agents.coach.metrics_engine import compute_stream_metrics  # noqa: E402
from app.services.stream_storage import load_activity_stream_from_file, get_stream_arrays  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_metrics")


def _fetch_candidates(conn: sqlite3.Connection, force: bool) -> list:
    """Return rows from run_activity_raw that need metrics computed.

    Metrics are stored in run_activities (avg_pace_min_km used as sentinel).
    When force=False, skip activities that already have avg_pace_min_km set.
    """
    if force:
        query = """
            SELECT r.activity_id, r.user_id, r.activity_name, r.full_meta, r.stream_file_path
            FROM run_activity_raw r
            WHERE r.stream_file_path IS NOT NULL AND r.stream_file_path != ''
            ORDER BY r.fetched_at DESC
        """
    else:
        query = """
            SELECT r.activity_id, r.user_id, r.activity_name, r.full_meta, r.stream_file_path
            FROM run_activity_raw r
            LEFT JOIN run_activities a
                ON a.activity_id = r.activity_id AND a.user_id = r.user_id
            WHERE r.stream_file_path IS NOT NULL AND r.stream_file_path != ''
              AND (a.activity_id IS NULL OR a.avg_pace_min_km IS NULL)
            ORDER BY r.fetched_at DESC
        """
    rows = conn.execute(query).fetchall()
    return rows


def backfill(dry_run: bool = False, force: bool = False) -> None:
    # Ensure all metric columns exist (idempotent migration)
    init_db()

    config = load_config()

    conn = get_db_connection()
    rows = _fetch_candidates(conn, force=force)
    conn.close()

    total = len(rows)
    logger.info(f"Found {total} activities to process (dry_run={dry_run}, force={force})")

    if dry_run or total == 0:
        return

    ok = skip = fail = 0
    for row in rows:
        activity_id = row["activity_id"]
        user_id = row["user_id"]
        activity_name = row["activity_name"] or ""
        stream_path = row["stream_file_path"]

        try:
            meta_raw = row["full_meta"]
            meta = json.loads(meta_raw) if meta_raw else {}
        except Exception:
            meta = {}

        # Load stream file
        stream_raw = load_activity_stream_from_file(stream_path)
        if not stream_raw:
            logger.warning(f"[SKIP] {activity_id}: stream file not found at {stream_path}")
            skip += 1
            continue

        arrays = get_stream_arrays(stream_raw)
        if not arrays:
            logger.warning(f"[SKIP] {activity_id}: stream file empty")
            skip += 1
            continue

        # Compute metrics
        try:
            metrics = compute_stream_metrics(arrays, meta, config, activity_name)
        except Exception as e:
            logger.error(f"[FAIL] {activity_id}: compute error — {e}")
            fail += 1
            continue

        if not metrics:
            logger.warning(f"[SKIP] {activity_id}: metrics returned empty")
            skip += 1
            continue

        # Upsert
        try:
            upsert_run_computed_metrics(activity_id, user_id, metrics)
            logger.info(f"[OK]   {activity_id} — {activity_name[:40]}")
            ok += 1
        except Exception as e:
            logger.error(f"[FAIL] {activity_id}: upsert error — {e}")
            fail += 1

    logger.info(f"\nDone — ok={ok}  skip={skip}  fail={fail}  total={total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill run_computed_metrics from stored stream files.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only, write nothing.")
    parser.add_argument("--force", action="store_true", help="Re-compute metrics even if already present.")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()

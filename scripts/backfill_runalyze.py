#!/usr/bin/env python3
"""Backfill runs from Runalyze into the coach DB (silent: no analysis, no Telegram spam).

Default window starts at the newest stored run, so after Strava stopped working this
fills the gap. Run inside the app container (needs the vector store for memories):

    docker exec airunningcoach python scripts/backfill_runalyze.py --dry-run
    docker exec airunningcoach python scripts/backfill_runalyze.py
    docker exec airunningcoach python scripts/backfill_runalyze.py --since 2026-06-26
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.coach.harvest import backfill_since  # noqa: E402
from app.core.notification import send_telegram_msg  # noqa: E402
from app.core.user_context import get_primary_user_id  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="YYYY-MM-DD (default: newest stored run)")
    parser.add_argument(
        "--dry-run", action="store_true", help="count only, write nothing"
    )
    args = parser.parse_args()

    counts = backfill_since(args.since, dry_run=args.dry_run)
    print(counts)
    if not args.dry_run and counts["found"]:
        send_telegram_msg(
            str(get_primary_user_id()),
            "✅ <b>Backfill Runalyze hoàn tất</b>\n"
            f"📋 Tìm thấy: {counts['found']} | 🆕 Mới: {counts['new']}\n"
            f"💾 Đã lưu: {counts['loaded']} | 🧠 Ký ức: {counts['memorized']} | "
            f"📐 Metrics: {counts['metrics']}",
        )


if __name__ == "__main__":
    main()

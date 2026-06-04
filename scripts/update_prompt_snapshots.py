#!/usr/bin/env python3
"""
Update prompt snapshots — re-freeze snapshot files after intentional prompt changes.

Usage:
    python scripts/update_prompt_snapshots.py

This deletes existing snapshots then re-runs the snapshot test suite with
UPDATE_PROMPT_SNAPSHOTS=1 so every snapshot is regenerated from the current
output of prompts.py. After running, REVIEW the diff in tests/snapshots/prompts/
before committing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    snapshots_dir = repo_root / "tests" / "snapshots" / "prompts"

    print(f"[update-snapshots] Repo: {repo_root}")
    print(f"[update-snapshots] Target dir: {snapshots_dir}")

    if not snapshots_dir.exists():
        snapshots_dir.mkdir(parents=True, exist_ok=True)

    # Wipe existing snapshots so we get a clean regenerate.
    removed = 0
    for f in snapshots_dir.glob("*.txt"):
        f.unlink()
        removed += 1
    print(f"[update-snapshots] Removed {removed} existing snapshot file(s).")

    env = os.environ.copy()
    env["UPDATE_PROMPT_SNAPSHOTS"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_prompts_snapshot.py",
        "-v",
        "--tb=short",
    ]
    print(f"[update-snapshots] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root, env=env)

    new_snapshots = list(snapshots_dir.glob("*.txt"))
    print(
        f"[update-snapshots] Generated {len(new_snapshots)} snapshot file(s) "
        f"at {snapshots_dir}"
    )
    print("[update-snapshots] Review with: git diff tests/snapshots/prompts/")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

"""
Snapshot tests for all prompt builders.
========================================
Freezes the byte-exact output of every public prompt builder against a
fixture input. Any change to prompts.py — intentional or accidental —
triggers a diff that must be reviewed before re-freezing snapshots.

Workflow:
  1. Modify prompts.py
  2. Run: python -m pytest tests/test_prompts_snapshot.py -v
  3. If diffs are intentional → python scripts/update_prompt_snapshots.py
  4. Commit the new snapshot files together with the prompt change.

Fixtures live in: tests/fixtures/prompts/*.json
Snapshots live in: tests/snapshots/prompts/*.txt
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "prompts"
SNAPSHOTS_DIR = Path(__file__).parent / "snapshots" / "prompts"

# Allow regenerating snapshots via env var (used by update script).
UPDATE_SNAPSHOTS = os.environ.get("UPDATE_PROMPT_SNAPSHOTS") == "1"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _assert_snapshot(test: unittest.TestCase, snapshot_name: str, actual: str) -> None:
    """Compare `actual` against the frozen snapshot file. Update if env var set."""
    snapshot_path = SNAPSHOTS_DIR / f"{snapshot_name}.txt"

    if UPDATE_SNAPSHOTS or not snapshot_path.exists():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(actual, encoding="utf-8")
        if not UPDATE_SNAPSHOTS:
            test.skipTest(
                f"Snapshot created (first run): {snapshot_path.name}. "
                "Re-run tests to verify stability."
            )
        return

    expected = snapshot_path.read_text(encoding="utf-8")
    if actual != expected:
        # Produce a focused diff hint.
        max_chars = 500
        diff_msg = (
            f"\nSnapshot mismatch for {snapshot_name}.\n"
            f"Expected ({len(expected)} chars) head:\n"
            f"  {expected[:max_chars]!r}\n"
            f"Actual ({len(actual)} chars) head:\n"
            f"  {actual[:max_chars]!r}\n\n"
            f"If this change is intentional, run:\n"
            f"  UPDATE_PROMPT_SNAPSHOTS=1 python -m pytest "
            f"tests/test_prompts_snapshot.py::{test.__class__.__name__}\n"
            f"or: python scripts/update_prompt_snapshots.py"
        )
        test.fail(diff_msg)


# ---------------------------------------------------------------------------
# Coach prompt snapshots
# ---------------------------------------------------------------------------


class TestCoachSystemInstructionSnapshots(unittest.TestCase):
    """Freeze coach system instruction across athlete profiles."""

    def test_system_instruction_baseline(self):
        from app.agents.coach.prompts import build_system_instruction

        inputs = _load_fixture("coach_profile_baseline")
        output = build_system_instruction(**inputs)
        _assert_snapshot(self, "coach_system_instruction_baseline", output)

    def test_system_instruction_taper_week(self):
        from app.agents.coach.prompts import build_system_instruction

        inputs = _load_fixture("coach_profile_taper")
        output = build_system_instruction(**inputs)
        _assert_snapshot(self, "coach_system_instruction_taper", output)

    def test_system_instruction_minimal_profile(self):
        from app.agents.coach.prompts import build_system_instruction

        inputs = _load_fixture("coach_profile_minimal")
        output = build_system_instruction(**inputs)
        _assert_snapshot(self, "coach_system_instruction_minimal", output)

    def test_core_system_instruction(self):
        from app.agents.coach.prompts import build_core_system_instruction

        inputs = _load_fixture("coach_profile_baseline")
        output = build_core_system_instruction(inputs["custom_instruction"])
        _assert_snapshot(self, "coach_core_system_instruction", output)

    def test_system_instruction_chat_format(self):
        """Freeze chat_format=True variant — CHAT_FORMAT_RULES embedded in system."""
        from app.agents.coach.prompts import build_system_instruction

        inputs = _load_fixture("coach_profile_baseline")
        output = build_system_instruction(**inputs, chat_format=True)
        _assert_snapshot(self, "coach_system_instruction_chat_format", output)

    def test_core_system_instruction_chat_format(self):
        from app.agents.coach.prompts import build_core_system_instruction

        inputs = _load_fixture("coach_profile_baseline")
        output = build_core_system_instruction(
            inputs["custom_instruction"], chat_format=True
        )
        _assert_snapshot(self, "coach_core_system_instruction_chat_format", output)


class TestCoachContextSnapshots(unittest.TestCase):
    """Freeze shared context block."""

    def test_shared_context_block(self):
        from app.agents.coach.prompts import get_shared_context_block

        inputs = _load_fixture("shared_context_baseline")
        output = get_shared_context_block(**inputs)
        _assert_snapshot(self, "coach_shared_context", output)


class TestCoachTaskPromptSnapshots(unittest.TestCase):
    """Freeze final user-turn prompts for each flow."""

    def test_chat_prompt_full(self):
        from app.agents.coach.prompts import build_chat_prompt

        inputs = _load_fixture("chat_inputs")
        output = build_chat_prompt(**inputs)
        _assert_snapshot(self, "coach_chat_prompt_full", output)

    def test_chat_prompt_fast_path_empty(self):
        from app.agents.coach.prompts import build_chat_prompt

        output = build_chat_prompt("", "", "")
        _assert_snapshot(self, "coach_chat_prompt_fast", output)

    def test_standup_prompt(self):
        from app.agents.coach.prompts import build_standup_prompt

        inputs = _load_fixture("standup_inputs")
        output = build_standup_prompt(**inputs)
        _assert_snapshot(self, "coach_standup_prompt", output)

    def test_run_analysis_prompt(self):
        from app.agents.coach.prompts import build_universal_run_analysis_prompt

        inputs = _load_fixture("run_analysis_inputs")
        output = build_universal_run_analysis_prompt(**inputs)
        _assert_snapshot(self, "coach_run_analysis_prompt", output)

    def test_weekly_reflection_prompt(self):
        from app.agents.coach.prompts import build_weekly_reflection_prompt

        inputs = _load_fixture("weekly_reflection_inputs")
        output = build_weekly_reflection_prompt(**inputs)
        _assert_snapshot(self, "coach_weekly_reflection_prompt", output)

    def test_memory_extraction_prompt(self):
        from app.agents.coach.prompts import build_memory_extraction_prompt

        inputs = _load_fixture("memory_extraction_inputs")
        output = build_memory_extraction_prompt(**inputs)
        _assert_snapshot(self, "coach_memory_extraction_prompt", output)


class TestCoachFormatRuleSnapshots(unittest.TestCase):
    """Freeze platform format rule constants."""

    def test_chat_format_rules(self):
        from app.agents.coach.prompts import CHAT_FORMAT_RULES

        _assert_snapshot(self, "coach_chat_format_rules", CHAT_FORMAT_RULES)

    def test_strava_format_rules(self):
        from app.agents.coach.prompts import STRAVA_FORMAT_RULES

        _assert_snapshot(self, "coach_strava_format_rules", STRAVA_FORMAT_RULES)

    def test_email_format_rules(self):
        from app.agents.coach.prompts import EMAIL_FORMAT_RULES

        _assert_snapshot(self, "coach_email_format_rules", EMAIL_FORMAT_RULES)

    def test_universal_format_rules(self):
        from app.agents.coach.prompts import UNIVERSAL_FORMAT_RULES

        _assert_snapshot(self, "coach_universal_format_rules", UNIVERSAL_FORMAT_RULES)


# ---------------------------------------------------------------------------
# News prompt snapshots
# ---------------------------------------------------------------------------


class TestNewsSystemInstructionSnapshots(unittest.TestCase):
    """Freeze news agent system instructions."""

    def test_legacy_news_system_instruction(self):
        from app.agents.news.prompts import build_news_system_instruction

        _assert_snapshot(
            self, "news_legacy_system_instruction", build_news_system_instruction()
        )

    def test_topic_system_instruction(self):
        from app.agents.news.prompts import build_topic_system_instruction

        _assert_snapshot(
            self, "news_topic_system_instruction", build_topic_system_instruction()
        )

    def test_on_demand_system_instruction(self):
        from app.agents.news.prompts import build_on_demand_system_instruction

        _assert_snapshot(
            self,
            "news_on_demand_system_instruction",
            build_on_demand_system_instruction(),
        )


class TestNewsPromptSnapshots(unittest.TestCase):
    """Freeze news user-turn prompts."""

    def test_topic_prompt(self):
        from app.agents.news.prompts import build_topic_prompt

        inputs = _load_fixture("news_topic_inputs")
        _assert_snapshot(self, "news_topic_prompt", build_topic_prompt(**inputs))

    def test_on_demand_prompt(self):
        from app.agents.news.prompts import build_on_demand_prompt

        inputs = _load_fixture("news_on_demand_inputs")
        _assert_snapshot(
            self, "news_on_demand_prompt", build_on_demand_prompt(**inputs)
        )

    def test_session_prompt_morning(self):
        from app.agents.news.prompts import build_session_prompt

        inputs = _load_fixture("news_session_inputs")
        output = build_session_prompt(
            session="morning",
            interest_profile=inputs["interest_profile"],
            date_str=inputs["date_str"],
            memory=inputs["memory"],
        )
        _assert_snapshot(self, "news_session_prompt_morning", output)

    def test_session_prompt_afternoon(self):
        from app.agents.news.prompts import build_session_prompt

        inputs = _load_fixture("news_session_inputs")
        output = build_session_prompt(
            session="afternoon",
            interest_profile=inputs["interest_profile"],
            date_str=inputs["date_str"],
            memory=inputs["memory"],
        )
        _assert_snapshot(self, "news_session_prompt_afternoon", output)

    def test_session_prompt_evening(self):
        from app.agents.news.prompts import build_session_prompt

        inputs = _load_fixture("news_session_inputs")
        output = build_session_prompt(
            session="evening",
            interest_profile=inputs["interest_profile"],
            date_str=inputs["date_str"],
            memory=inputs["memory"],
        )
        _assert_snapshot(self, "news_session_prompt_evening", output)

    def test_news_memory_extraction_prompt(self):
        from app.agents.news.prompts import build_memory_extraction_prompt

        inputs = _load_fixture("news_memory_inputs")
        _assert_snapshot(
            self,
            "news_memory_extraction_prompt",
            build_memory_extraction_prompt(**inputs),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

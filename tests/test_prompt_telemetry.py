"""
Unit tests for the prompt telemetry helper.

Covers:
- token estimation matches the documented heuristic
- hash is deterministic and short
- log line shape stays stable (downstream alerting greps on prefix)
- exceptions inside logging never propagate
"""

from __future__ import annotations

import unittest

from app.agents._prompt_telemetry import (
    PROMPT_VERSION,
    WARN_TOTAL_TOKENS,
    estimate_tokens,
    log_prompt_metrics,
)


class TestEstimateTokens(unittest.TestCase):
    def test_empty_string_returns_zero(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_ascii_text_within_15pct_of_chars_div_4(self):
        text = "a" * 400
        # 400 / 3.8 ≈ 105. Allow ±15%.
        tokens = estimate_tokens(text)
        self.assertGreaterEqual(tokens, 90)
        self.assertLessEqual(tokens, 120)

    def test_vietnamese_text_handled(self):
        text = "Xin chào, đây là một câu tiếng Việt có dấu." * 10
        tokens = estimate_tokens(text)
        self.assertGreater(tokens, 0)


class TestLogPromptMetrics(unittest.TestCase):
    def test_emits_info_line_under_threshold(self):
        with self.assertLogs("app.agents._prompt_telemetry", level="INFO") as cm:
            log_prompt_metrics(
                flow="test.unit", system_inst="hello", user_prompt="world"
            )
        self.assertTrue(any("[PROMPT-METRIC]" in m for m in cm.output))
        self.assertTrue(any("flow=test.unit" in m for m in cm.output))
        self.assertTrue(any(f"v={PROMPT_VERSION}" in m for m in cm.output))

    def test_warns_when_total_exceeds_budget(self):
        huge = "x" * (WARN_TOTAL_TOKENS * 5)  # ~13K tokens with our heuristic
        with self.assertLogs("app.agents._prompt_telemetry", level="WARNING") as cm:
            log_prompt_metrics(flow="test.unit", system_inst=huge, user_prompt="y")
        self.assertTrue(any("OVER_BUDGET" in m for m in cm.output))

    def test_includes_extra_fields(self):
        with self.assertLogs("app.agents._prompt_telemetry", level="INFO") as cm:
            log_prompt_metrics(
                flow="test.unit",
                system_inst="a",
                user_prompt="b",
                intent="fast",
                model="gemini-flash",
                extra={"user_id": "u123", "retry": 0},
            )
        joined = " ".join(cm.output)
        self.assertIn("intent=fast", joined)
        self.assertIn("model=gemini-flash", joined)
        self.assertIn("user_id=u123", joined)
        self.assertIn("retry=0", joined)

    def test_swallows_exceptions(self):
        """Even if logging raises, the call must not propagate."""

        class _Boom:
            def __str__(self):
                raise RuntimeError("boom")

        # Smuggle a bad value into extra. The function must not raise.
        try:
            log_prompt_metrics(
                flow="test.unit",
                system_inst="a",
                user_prompt="b",
                extra={"bad": _Boom()},
            )
        except Exception as e:  # pragma: no cover — defensive
            self.fail(f"log_prompt_metrics should swallow exceptions, raised {e!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

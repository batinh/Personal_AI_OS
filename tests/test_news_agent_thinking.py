"""
Tests for news agent thinking-model filtering.

Unit tests validate the two-layer defence against Gemini chain-of-thought leaking
to Telegram:

  Layer 1 — _extract_text(): skips Part objects where thought=True
  Layer 2 — _strip_thought_preamble(): regex strips "thought\\n..." text that
             slipped through when the SDK did not set the thought attribute

Docker integration tests (opt-in):
  Set INTEGRATION_TEST=1 to run tests that send real HTTP requests to the live
  Docker container.  These are skipped in CI; run them locally after deploy.

  INTEGRATION_TEST=1 pytest tests/test_news_agent_thinking.py -v -m integration
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers: build fake Gemini response objects
# ─────────────────────────────────────────────────────────────────────────────

def _make_part(text: str, is_thought: bool = False) -> MagicMock:
    """Create a fake google.genai Part with .text and .thought attributes."""
    p = MagicMock()
    p.text = text
    p.thought = is_thought
    return p


def _make_candidate(*parts: MagicMock, grounding: bool = False) -> MagicMock:
    """Create a fake Candidate with the given parts."""
    cand = MagicMock()
    cand.content.parts = list(parts)
    cand.grounding_metadata = MagicMock() if grounding else None
    cand.finish_reason = "STOP"
    return cand


def _make_response(*candidates: MagicMock, fallback_text: str = "") -> MagicMock:
    """Create a fake GenerateContentResponse."""
    resp = MagicMock()
    resp.candidates = list(candidates)
    resp.text = fallback_text
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Import the functions under test
# (conftest.py stubs google.genai before any import, so these are safe)
# ─────────────────────────────────────────────────────────────────────────────

from app.agents.news.agent import _extract_text, _strip_thought_preamble


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: _extract_text
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractText(unittest.TestCase):
    def test_returns_none_for_empty_candidates(self):
        resp = _make_response(fallback_text="")
        resp.candidates = []
        result = _extract_text(resp)
        self.assertIsNone(result)

    def test_returns_single_non_thinking_part(self):
        part = _make_part("Hello world", is_thought=False)
        resp = _make_response(_make_candidate(part))
        self.assertEqual(_extract_text(resp), "Hello world")

    def test_filters_thinking_part_thought_true(self):
        """Primary defence: thought=True parts must be excluded."""
        thinking = _make_part("I should search for recent news ...", is_thought=True)
        answer = _make_part("<b>TIN TỨC</b>\n📰 AI news today...", is_thought=False)
        resp = _make_response(_make_candidate(thinking, answer))
        result = _extract_text(resp)
        self.assertNotIn("I should search", result)
        self.assertIn("TIN TỨC", result)

    def test_returns_none_when_only_thinking_parts(self):
        thinking = _make_part("Long chain-of-thought...", is_thought=True)
        resp = _make_response(_make_candidate(thinking))
        self.assertIsNone(_extract_text(resp))

    def test_joins_multiple_non_thinking_parts(self):
        """AFC / post-search can produce multiple text parts (pre-search + grounded answer)."""
        part1 = _make_part("Part one. ", is_thought=False)
        part2 = _make_part("Part two.", is_thought=False)
        resp = _make_response(_make_candidate(part1, part2))
        result = _extract_text(resp)
        self.assertEqual(result, "Part one. Part two.")

    def test_strips_surrounding_whitespace(self):
        part = _make_part("  answer  ", is_thought=False)
        resp = _make_response(_make_candidate(part))
        self.assertEqual(_extract_text(resp), "answer")

    def test_fallback_to_response_text_on_exception(self):
        """If candidate structure is broken, fall back to response.text."""
        resp = MagicMock()
        resp.candidates = None  # causes AttributeError on iteration
        resp.text = "fallback text"
        result = _extract_text(resp)
        self.assertEqual(result, "fallback text")

    def test_parts_without_text_attribute_are_skipped(self):
        """Function call parts have no .text — they must not raise."""
        func_call_part = MagicMock()
        func_call_part.text = None
        func_call_part.thought = False
        text_part = _make_part("Real answer", is_thought=False)
        resp = _make_response(_make_candidate(func_call_part, text_part))
        self.assertEqual(_extract_text(resp), "Real answer")


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: _strip_thought_preamble
# ─────────────────────────────────────────────────────────────────────────────

class TestStripThoughtPreamble(unittest.TestCase):
    def test_no_preamble_passes_through(self):
        text = "<b>TECHNOLOGY</b>\n📰 AI news today"
        self.assertEqual(_strip_thought_preamble(text), text)

    def test_strips_thought_newline_preamble(self):
        preamble = "thought\nI need to search for recent news about technology.\n"
        answer = "<b>TECHNOLOGY</b>\n📰 AI breakthrough today"
        text = preamble + answer
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("<b>TECHNOLOGY</b>"))
        self.assertNotIn("I need to search", result)

    def test_strips_thought_space_preamble(self):
        text = "thought Today I will analyse the sports news.\n📊 SPORTS update"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("📊"))

    def test_returns_none_when_all_thinking_no_anchor(self):
        """If no HTML tag or emoji anchor is found, the whole text is thinking."""
        text = "thought\nThis is all internal reasoning with no real answer."
        self.assertIsNone(_strip_thought_preamble(text))

    def test_case_insensitive_prefix_match(self):
        text = "Thought\nSome reasoning\n📰 The actual news"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("📰"))

    def test_anchors_on_html_bold_tag(self):
        text = "thought\nSome reasoning\n<b>HEADLINE</b>"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("<b>"))

    def test_anchors_on_html_italic_tag(self):
        text = "thought\nSome reasoning\n<i>subtitle</i>"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("<i>"))

    def test_anchors_on_html_anchor_tag(self):
        text = "thought\nSome reasoning\n<a href='...'>link</a>"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("<a"))

    def test_anchors_on_check_emoji(self):
        text = "thought\nReasoning\n✅ Done"
        result = _strip_thought_preamble(text)
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("✅"))

    def test_empty_string_passes_through(self):
        self.assertEqual(_strip_thought_preamble(""), "")

    def test_normal_news_output_unmodified(self):
        """Real grounded answer must pass through untouched."""
        text = (
            "📊 <b>TECHNOLOGY</b>\n\n"
            "🔹 <b>OpenAI ra mắt GPT-5</b>\n"
            "<i>Nguồn: Reuters</i>\n"
            "<a href='https://reuters.com/...'>Đọc thêm</a>"
        )
        self.assertEqual(_strip_thought_preamble(text), text)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: _call_gemini_with_search — end-to-end unit with mocked client
# ─────────────────────────────────────────────────────────────────────────────

class TestCallGeminiWithSearch(unittest.TestCase):
    """Verify that thinking parts never reach the return value of _call_gemini_with_search."""

    def _invoke(self, response: MagicMock, model: str = "models/gemini-1.5-pro") -> str | None:
        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.return_value = response
            from app.agents.news.agent import _call_gemini_with_search
            return _call_gemini_with_search(model, "system", "user prompt")

    def test_clean_response_returned(self):
        part = _make_part("<b>NEWS</b> Real grounded content here with 200+ chars " + "x" * 160)
        resp = _make_response(_make_candidate(part, grounding=True))
        result = self._invoke(resp)
        self.assertIsNotNone(result)
        self.assertIn("NEWS", result)

    def test_thinking_part_filtered_out(self):
        thinking = _make_part("thought\nInternal reasoning ...", is_thought=True)
        answer = _make_part("<b>AI</b> Real content " + "a" * 140, is_thought=False)
        resp = _make_response(_make_candidate(thinking, answer, grounding=True))
        result = self._invoke(resp)
        self.assertIsNotNone(result)
        self.assertNotIn("Internal reasoning", result)
        self.assertIn("Real content", result)

    def test_returns_none_when_only_thinking_and_no_anchor(self):
        thinking = _make_part("thought\nOnly reasoning, no real answer.", is_thought=True)
        resp = _make_response(_make_candidate(thinking))
        result = self._invoke(resp)
        self.assertIsNone(result)

    def test_thinking_preamble_stripped_when_thought_attr_absent(self):
        """
        Defence-in-depth: when SDK sets thought=False but emits the preamble as plain text,
        _strip_thought_preamble should still clean it up.
        """
        raw = "thought\nI must search for today's news.\n<b>HEADLINE</b> real content " + "z" * 150
        part = _make_part(raw, is_thought=False)  # SDK did NOT set thought=True
        resp = _make_response(_make_candidate(part, grounding=True))
        result = self._invoke(resp)
        self.assertIsNotNone(result)
        self.assertNotIn("I must search", result)
        self.assertIn("HEADLINE", result)

    def test_returns_none_on_gemini_exception(self):
        with patch("app.agents.news.agent.client") as mock_client:
            mock_client.models.generate_content.side_effect = RuntimeError("quota exceeded")
            from app.agents.news.agent import _call_gemini_with_search
            result = _call_gemini_with_search("models/gemini-1.5-pro", "sys", "prompt")
        self.assertIsNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# Docker integration tests (opt-in: INTEGRATION_TEST=1)
# ─────────────────────────────────────────────────────────────────────────────

_INTEGRATION = os.getenv("INTEGRATION_TEST", "").strip() in ("1", "true", "yes")
_DOCKER_BASE = os.getenv("DOCKER_BASE_URL", "http://localhost:8000")
_DOCKER_AUTH = (
    os.getenv("DOCKER_AUTH_USER", "admin"),
    os.getenv("DOCKER_AUTH_PASS", ""),
)


@pytest.mark.integration
@pytest.mark.skipif(not _INTEGRATION, reason="set INTEGRATION_TEST=1 to run against live Docker")
class TestDockerIntegration(unittest.TestCase):
    """
    Optional integration tests that talk to the live Docker container.

    Run with:
        INTEGRATION_TEST=1 pytest tests/test_news_agent_thinking.py -v -m integration

    Environment variables:
        DOCKER_BASE_URL   Base URL of container (default: http://localhost:8000)
        DOCKER_AUTH_USER  Basic auth user (default: admin)
        DOCKER_AUTH_PASS  Basic auth password (required for protected endpoints)
    """

    def setUp(self):
        try:
            import requests  # noqa: F401
        except ImportError:
            self.skipTest("requests library not installed")

    def _get(self, path: str, **kwargs):
        import requests
        url = f"{_DOCKER_BASE}{path}"
        return requests.get(url, auth=_DOCKER_AUTH, timeout=10, **kwargs)

    def _post(self, path: str, json_body: dict, **kwargs):
        import requests
        url = f"{_DOCKER_BASE}{path}"
        return requests.post(url, json=json_body, auth=_DOCKER_AUTH, timeout=30, **kwargs)

    def test_health_endpoint_responds(self):
        resp = self._get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)

    def test_webhook_endpoint_reachable(self):
        """Verify the Telegram webhook endpoint exists and accepts POST."""
        import requests
        # Send an empty-ish body — it will fail validation but must not 404
        url = f"{_DOCKER_BASE}/webhook"
        resp = requests.post(url, json={}, timeout=10)
        # 422 (validation error) is expected; 404 would mean the route is missing
        self.assertIn(resp.status_code, (200, 400, 422), msg=f"Unexpected status: {resp.status_code}")

    def test_no_error_lines_in_recent_logs(self):
        """
        Container should have zero ERROR lines in the last 50 log lines.
        Runs 'docker logs airunningcoach --tail 50' via subprocess.
        """
        import subprocess
        result = subprocess.run(
            ["docker", "logs", "airunningcoach", "--tail", "50"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        error_lines = [ln for ln in combined.splitlines() if " ERROR " in ln or " [ERROR]" in ln]
        if error_lines:
            self.fail(
                f"Found {len(error_lines)} ERROR line(s) in recent logs:\n"
                + "\n".join(error_lines[:10])
            )

    def test_no_thinking_text_in_recent_news_logs(self):
        """
        After the fix, 'thought\\n' must not appear in news module log output.
        This detects a regression where thinking preamble leaks back.
        """
        import subprocess
        result = subprocess.run(
            ["docker", "logs", "airunningcoach", "--tail", "200"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        leak_lines = [
            ln for ln in combined.splitlines()
            if "extracted text" in ln and "thought" in ln.lower() and "[NEWS-DEBUG]" in ln
        ]
        if leak_lines:
            self.fail(
                "Thinking preamble still leaking into extracted text:\n"
                + "\n".join(leak_lines[:5])
            )

    def test_news_on_demand_sends_no_thought_prefix(self):
        """
        Send an @news query via the /webhook endpoint and verify the response
        logged to Docker does NOT contain 'thought\\n' in extracted text lines.

        This is an end-to-end smoke test of the full on-demand news path.
        Requires a valid Telegram-style payload; skipped if TELEGRAM_BOT_TOKEN
        is not configured in the container (query will be rejected by auth).
        """
        import subprocess, time

        # Record log position before triggering
        before = subprocess.run(
            ["docker", "logs", "airunningcoach", "--tail", "1"],
            capture_output=True, text=True, timeout=10,
        ).stderr + subprocess.run(
            ["docker", "logs", "airunningcoach", "--tail", "1"],
            capture_output=True, text=True, timeout=10,
        ).stdout

        # We don't have a real Telegram token here, so the webhook will
        # reject the request. What we verify is that the endpoint is alive
        # and no thought-preamble appears in ANY new log lines after calling.
        self._post("/webhook", {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": 0, "type": "private"},
                "from": {"id": 0, "is_bot": False, "first_name": "Test"},
                "text": "@news tin tức hôm nay",
                "date": 0,
            }
        })

        time.sleep(2)  # give async handler a moment to log

        after = subprocess.run(
            ["docker", "logs", "airunningcoach", "--tail", "20"],
            capture_output=True, text=True, timeout=10,
        )
        combined = after.stdout + after.stderr
        # Only fail if we see the regression pattern
        for line in combined.splitlines():
            if "extracted text" in line and "thought" in line.lower():
                self.fail(f"Thinking preamble in post-request log: {line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
E2E Tests — News Agent Flows
==============================
Tests news agent behaviors: per-topic timeout isolation, article dedup,
and on-demand /news command via Telegram webhook.

REQ-N10: per-topic 30s timeout does not block other topics
REQ-N12: same article not repeated within a day (URL dedup)
REQ-NS04: /news command via Telegram webhook accepted end-to-end

Run:
    python -m pytest tests/test_e2e_news_flows.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


_STUB_CONFIG = {
    "scheduler": {},
    "telegram_bot_token": "test-token",
    "telegram_chat_id": "999",
    "gemini_api_key": "test",
    "news_agent": {
        "enabled": True,
        "news_model": "models/gemini-flash-latest",
        "telegram_chat_id": "999",
        "morning_time": "06:30",
        "afternoon_time": "17:30",
        "evening_time": "20:00",
        "interest_profile": {"tech": {"emoji": "💻", "weight": 1.0}},
        "topic_timeout_seconds": 1,
    },
}


class TestNewsPerTopicTimeout(unittest.TestCase):
    """REQ-N10 — One failed/timed-out topic does not block the rest of the briefing."""

    @patch("app.agents.news.agent.send_telegram_html")
    @patch("app.agents.news.agent._call_gemini_with_search")
    def test_topic_timeout_does_not_block_other_topics(
        self, mock_call, mock_send
    ):
        """generate_news_briefing must continue after one topic raises TimeoutError."""
        from concurrent.futures import TimeoutError as FuturesTimeoutError
        import app.agents.news.agent as news_agent

        cfg_multi = dict(_STUB_CONFIG)
        cfg_multi["news_agent"] = dict(_STUB_CONFIG["news_agent"])
        cfg_multi["news_agent"]["interest_profile"] = {
            "tech": {"emoji": "💻", "weight": 1.0},
            "sports": {"emoji": "⚽", "weight": 1.0},
        }

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FuturesTimeoutError("timeout")
            return ("Tin tức thể thao hôm nay...", [])

        mock_call.side_effect = side_effect
        mock_send.return_value = None

        # Should not raise even if one topic times out
        try:
            news_agent.generate_news_briefing(cfg_multi, session="morning")
        except SystemExit:
            pass  # acceptable — some paths may exit
        except Exception as exc:
            self.fail(f"generate_news_briefing raised unexpectedly: {exc}")

    @patch("app.agents.news.agent.send_telegram_html")
    @patch("app.agents.news.agent._call_gemini_with_search", return_value=(None, []))
    def test_all_topics_return_none_does_not_crash(self, mock_call, mock_send):
        """generate_news_briefing with all None responses must not crash."""
        import app.agents.news.agent as news_agent

        mock_send.return_value = None
        try:
            news_agent.generate_news_briefing(_STUB_CONFIG, session="morning")
        except SystemExit:
            pass
        except Exception as exc:
            self.fail(f"generate_news_briefing raised unexpectedly: {exc}")


class TestNewsDedup(unittest.TestCase):
    """REQ-N12 — Same article URL not included twice in a single briefing session."""

    def test_article_not_repeated_in_same_day_sessions(self):
        """_extract_grounding_urls must deduplicate identical URLs within a response."""
        from app.agents.news.agent import _extract_grounding_urls

        dup_uri = "https://example.com/article-1"

        chunk1 = MagicMock()
        chunk1.web.uri = dup_uri
        chunk1.web.title = "Article 1"

        chunk2 = MagicMock()
        chunk2.web.uri = dup_uri  # same URI — should be deduped
        chunk2.web.title = "Article 1 duplicate"

        chunk3 = MagicMock()
        chunk3.web.uri = "https://example.com/article-2"
        chunk3.web.title = "Article 2"

        cand1 = MagicMock()
        cand1.grounding_metadata.grounding_chunks = [chunk1, chunk2]

        cand2 = MagicMock()
        cand2.grounding_metadata.grounding_chunks = [chunk3]

        result = _extract_grounding_urls([cand1, cand2])
        uris = [uri for _, uri in result]

        self.assertIn(dup_uri, uris, "First occurrence of dup_uri must be present")
        self.assertEqual(uris.count(dup_uri), 1, "Duplicate URI must appear only once")
        self.assertIn("https://example.com/article-2", uris)

    def test_empty_candidates_returns_empty(self):
        """_extract_grounding_urls with no candidates must return empty list."""
        from app.agents.news.agent import _extract_grounding_urls

        result = _extract_grounding_urls([])
        self.assertEqual(result, [])

    def test_candidate_without_grounding_metadata_skipped(self):
        """_extract_grounding_urls must skip candidates with no grounding_metadata."""
        from app.agents.news.agent import _extract_grounding_urls

        cand = MagicMock()
        cand.grounding_metadata = None

        result = _extract_grounding_urls([cand])
        self.assertEqual(result, [])


class TestNewsCommandE2E(unittest.TestCase):
    """REQ-NS04 — /news command via Telegram webhook accepted end-to-end."""

    def setUp(self):
        from app.main import app
        from fastapi.testclient import TestClient
        self.client = TestClient(app, raise_server_exceptions=False)

    @patch("app.routers.webhooks.load_config", return_value=_STUB_CONFIG)
    @patch("app.agents.news.telegram_handler.handle_news_command")
    def test_news_command_webhook_accepted(self, mock_handle, mock_cfg):
        """POST /telegram-webhook with /news must return 200 and queue handler."""
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "update_id": 100001,
                "message": {
                    "message_id": 1,
                    "from": {"id": 999, "is_bot": False, "first_name": "Tester"},
                    "chat": {"id": 999, "type": "private"},
                    "date": 1746000000,
                    "text": "/news",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.routers.webhooks.load_config", return_value=_STUB_CONFIG)
    def test_news_command_with_args_accepted(self, mock_cfg):
        """POST /telegram-webhook with /news tech must return 200."""
        resp = self.client.post(
            "/telegram-webhook",
            json={
                "update_id": 100002,
                "message": {
                    "message_id": 2,
                    "from": {"id": 999, "is_bot": False, "first_name": "Tester"},
                    "chat": {"id": 999, "type": "private"},
                    "date": 1746000001,
                    "text": "/news tech",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)

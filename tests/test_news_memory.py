"""
Tests for app.agents.news.memory — load/save news agent state and signal extraction.
"""
import json
import unittest
from unittest.mock import patch, MagicMock

from app.agents.news.memory import (
    load_news_memory,
    save_news_memory,
    _merge_topics,
    _parse_extraction,
    extract_and_save_signals,
    run_extract_in_background,
)


class TestMergeTopics(unittest.TestCase):
    def test_deduplicates(self):
        result = _merge_topics(["AI", "EV"], ["AI", "chip"])
        self.assertEqual(result, ["AI", "EV", "chip"])

    def test_respects_max(self):
        existing = [str(i) for i in range(20)]
        result = _merge_topics(existing, ["99"], max_items=5)
        self.assertEqual(len(result), 5)
        self.assertIn("99", result)

    def test_strips_whitespace(self):
        result = _merge_topics([], ["  AI  ", "chip"])
        self.assertIn("AI", result)


class TestParseExtraction(unittest.TestCase):
    def test_valid_json(self):
        raw = '{"liked": ["AI"], "disliked": ["politics"], "notes": "prefer short"}'
        result = _parse_extraction(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["liked"], ["AI"])
        self.assertEqual(result["disliked"], ["politics"])

    def test_json_with_markdown_fences(self):
        raw = "```json\n{\"liked\": [\"chip\"]}\n```"
        result = _parse_extraction(raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["liked"], ["chip"])

    def test_invalid_json_returns_none(self):
        result = _parse_extraction("not json at all")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = _parse_extraction("")
        self.assertIsNone(result)


class TestLoadNewsMemory(unittest.TestCase):
    @patch("app.agents.news.memory.get_news_state")
    def test_returns_defaults_when_no_state(self, mock_get):
        mock_get.return_value = None
        mem = load_news_memory("user1")
        self.assertEqual(mem["liked_topics"], [])
        self.assertEqual(mem["disliked_topics"], [])
        self.assertEqual(mem["extra_notes"], "")

    @patch("app.agents.news.memory.get_news_state")
    def test_parses_liked_topics(self, mock_get):
        def side_effect(user_id, key):
            if key == "liked_topics":
                return json.dumps(["AI", "chip"])
            return None
        mock_get.side_effect = side_effect

        mem = load_news_memory("user1")
        self.assertEqual(mem["liked_topics"], ["AI", "chip"])

    @patch("app.agents.news.memory.get_news_state")
    def test_handles_corrupt_json_gracefully(self, mock_get):
        mock_get.return_value = "not valid json"
        mem = load_news_memory("user1")
        self.assertEqual(mem["liked_topics"], [])


class TestSaveNewsMemory(unittest.TestCase):
    @patch("app.agents.news.memory.set_news_state")
    def test_delegates_to_set_news_state(self, mock_set):
        save_news_memory("user1", "liked_topics", '["AI"]')
        mock_set.assert_called_once_with("user1", "liked_topics", '["AI"]')


class TestExtractAndSaveSignals(unittest.TestCase):
    @patch("app.agents.news.memory._client")
    @patch("app.agents.news.memory.load_news_memory")
    @patch("app.agents.news.memory.save_news_memory")
    def test_saves_liked_topics(self, mock_save, mock_load, mock_client):
        mock_load.return_value = {
            "liked_topics": [],
            "disliked_topics": [],
            "extra_notes": "",
        }

        mock_response = MagicMock()
        mock_response.text = '{"liked": ["AI", "EV"], "disliked": [], "notes": ""}'
        mock_client.models.generate_content.return_value = mock_response

        extract_and_save_signals("user1", "user: tell me about AI\nassistant: ...", "model")

        calls = {call[0][1]: call[0][2] for call in mock_save.call_args_list}
        self.assertIn("liked_topics", calls)
        parsed = json.loads(calls["liked_topics"])
        self.assertIn("AI", parsed)

    @patch("app.agents.news.memory._client")
    @patch("app.agents.news.memory.load_news_memory")
    @patch("app.agents.news.memory.save_news_memory")
    def test_no_save_when_no_signals(self, mock_save, mock_load, mock_client):
        mock_load.return_value = {
            "liked_topics": [],
            "disliked_topics": [],
            "extra_notes": "",
        }

        mock_response = MagicMock()
        mock_response.text = '{"liked": [], "disliked": [], "notes": ""}'
        mock_client.models.generate_content.return_value = mock_response

        extract_and_save_signals("user1", "user: ok\nassistant: ok", "model")
        mock_save.assert_not_called()

    @patch("app.agents.news.memory._client")
    @patch("app.agents.news.memory.load_news_memory")
    @patch("app.agents.news.memory.save_news_memory")
    def test_graceful_on_api_error(self, mock_save, mock_load, mock_client):
        mock_load.return_value = {"liked_topics": [], "disliked_topics": [], "extra_notes": ""}
        mock_client.models.generate_content.side_effect = RuntimeError("API error")

        # Should not raise
        extract_and_save_signals("user1", "text", "model")
        mock_save.assert_not_called()


class TestRunExtractInBackground(unittest.TestCase):
    @patch("app.agents.news.memory.extract_and_save_signals")
    def test_spawns_thread(self, mock_extract):
        run_extract_in_background("user1", "chat", "model")
        import time
        time.sleep(0.05)  # let daemon thread start
        mock_extract.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

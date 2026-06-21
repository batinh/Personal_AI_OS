"""Tests for app/core/gemini_utils.py."""

from unittest.mock import MagicMock

from app.core.gemini_utils import extract_text, strip_thought_preamble


class TestExtractText:
    def test_non_thinking_parts(self):
        part = MagicMock()
        part.text = "Hello world"
        part.thought = False
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        assert extract_text(response) == "Hello world"

    def test_filters_out_thought_parts(self):
        thought_part = MagicMock()
        thought_part.text = "internal reasoning"
        thought_part.thought = True
        real_part = MagicMock()
        real_part.text = "visible answer"
        real_part.thought = False
        content = MagicMock()
        content.parts = [thought_part, real_part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        assert extract_text(response) == "visible answer"

    def test_empty_candidates_falls_back_to_text(self):
        response = MagicMock()
        response.candidates = []
        response.text = "fallback"
        assert extract_text(response) == "fallback"

    def test_no_candidates_attr_falls_back_to_text(self):
        response = MagicMock(spec=[])
        response.text = "fallback2"
        # candidates access raises AttributeError → exception path
        assert extract_text(response) == "fallback2"

    def test_all_whitespace_returns_none(self):
        part = MagicMock()
        part.text = "   "
        part.thought = False
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        # "   ".strip() == "" → returns None
        result = extract_text(response)
        assert result is None


class TestStripThoughtPreamble:
    def test_no_preamble_passthrough(self):
        text = "<b>Some news</b>"
        assert strip_thought_preamble(text) == text

    def test_strips_thought_preamble_with_html_anchor(self):
        text = "thoughtful\nHere is my answer <b>bold</b>"
        result = strip_thought_preamble(text)
        assert result and "thoughtful" not in result
        assert "<b>" in result

    def test_returns_none_when_all_thinking(self):
        text = "thoughts on this matter without any html anchor"
        result = strip_thought_preamble(text)
        assert result is None

    def test_emoji_anchor(self):
        text = "thoughtful\n📊 Summary here"
        result = strip_thought_preamble(text)
        assert result and "📊" in result

"""Tests for app/agents/news/prompts.py — all pure functions, no mocks needed."""
import pytest
from app.agents.news.prompts import (
    _build_interest_section,
    _build_memory_section,
    build_topic_system_instruction,
    build_topic_prompt,
    build_on_demand_system_instruction,
    build_on_demand_prompt,
    build_session_prompt,
    build_memory_extraction_prompt,
    build_news_system_instruction,
)


class TestBuildInterestSection:
    def test_empty_returns_default(self):
        result = _build_interest_section({})
        assert "công nghệ" in result

    def test_flat_format_sorted_by_weight(self):
        profile = {"Thể thao": 3, "AI": 9, "Kinh tế": 6}
        result = _build_interest_section(profile)
        ai_pos = result.index("AI")
        eco_pos = result.index("Kinh tế")
        sport_pos = result.index("Thể thao")
        assert ai_pos < eco_pos < sport_pos

    def test_flat_format_contains_weight(self):
        result = _build_interest_section({"AI": 8})
        assert "8/10" in result
        assert "AI" in result

    def test_nested_format_extracts_weight(self):
        profile = {"AI": {"weight": 7, "keywords": ["llm", "gemini"]}}
        result = _build_interest_section(profile)
        assert "7/10" in result

    def test_nested_format_missing_weight_defaults_to_5(self):
        result = _build_interest_section({"Topic": {"keywords": ["x"]}})
        assert "5/10" in result

    def test_invalid_weight_defaults_to_5(self):
        result = _build_interest_section({"Topic": "not-a-number"})
        assert "5/10" in result


class TestBuildMemorySection:
    def test_empty_memory_returns_empty_string(self):
        assert _build_memory_section({}) == ""

    def test_liked_topics_included(self):
        result = _build_memory_section({"liked_topics": ["AI", "Kinh tế"]})
        assert "AI" in result
        assert "Kinh tế" in result

    def test_disliked_topics_included(self):
        result = _build_memory_section({"disliked_topics": ["Giải trí"]})
        assert "Giải trí" in result

    def test_extra_notes_included(self):
        result = _build_memory_section({"extra_notes": "Prefer short summaries"})
        assert "Prefer short summaries" in result

    def test_extra_notes_truncated_at_200(self):
        long_note = "x" * 300
        result = _build_memory_section({"extra_notes": long_note})
        assert "x" * 200 in result
        assert "x" * 201 not in result

    def test_all_fields_combined(self):
        memory = {
            "liked_topics": ["AI"],
            "disliked_topics": ["Sport"],
            "extra_notes": "Keep it brief",
        }
        result = _build_memory_section(memory)
        assert "AI" in result
        assert "Sport" in result
        assert "Keep it brief" in result
        assert result.startswith("Sở thích học được:")

    def test_liked_topics_capped_at_10(self):
        topics = [f"topic{i}" for i in range(15)]
        result = _build_memory_section({"liked_topics": topics})
        assert "topic10" not in result
        assert "topic9" in result


class TestBuildTopicSystemInstruction:
    def test_returns_non_empty_string(self):
        result = build_topic_system_instruction()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_format_keywords(self):
        result = build_topic_system_instruction()
        assert "FORMAT" in result


class TestBuildTopicPrompt:
    def test_morning_session_context(self):
        result = build_topic_prompt("AI", "🤖", "morning", "22/04/2026")
        assert "buổi sáng" in result

    def test_afternoon_session_context(self):
        result = build_topic_prompt("AI", "🤖", "afternoon", "22/04/2026")
        assert "buổi chiều" in result

    def test_evening_session_context(self):
        result = build_topic_prompt("AI", "🤖", "evening", "22/04/2026")
        assert "buổi tối" in result

    def test_unknown_session_falls_back(self):
        result = build_topic_prompt("AI", "🤖", "unknown", "22/04/2026")
        assert "trong ngày" in result

    def test_topic_name_and_emoji_in_output(self):
        result = build_topic_prompt("Kinh tế", "📊", "morning", "22/04/2026")
        assert "Kinh tế" in result
        assert "📊" in result

    def test_date_str_in_output(self):
        result = build_topic_prompt("AI", "🤖", "morning", "15/03/2026")
        assert "15/03/2026" in result


class TestBuildOnDemandSystemInstruction:
    def test_returns_non_empty_string(self):
        result = build_on_demand_system_instruction()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_format_keywords(self):
        result = build_on_demand_system_instruction()
        assert "FORMAT" in result


class TestBuildOnDemandPrompt:
    def test_query_in_output(self):
        result = build_on_demand_prompt("ETF Việt Nam", "22/04/2026")
        assert "ETF Việt Nam" in result

    def test_date_str_in_output(self):
        result = build_on_demand_prompt("AI trends", "22/04/2026")
        assert "22/04/2026" in result

    def test_contains_search_instruction(self):
        result = build_on_demand_prompt("anything", "22/04/2026")
        assert "tìm kiếm" in result.lower() or "Tìm kiếm" in result


class TestBuildSessionPrompt:
    def test_morning_template_used(self):
        result = build_session_prompt("morning", {}, "22/04/2026", {})
        assert "SÁNG" in result or "sáng" in result

    def test_afternoon_template_used(self):
        result = build_session_prompt("afternoon", {}, "22/04/2026", {})
        assert "CHIỀU" in result or "chiều" in result

    def test_evening_template_used(self):
        result = build_session_prompt("evening", {}, "22/04/2026", {})
        assert "CUỐI NGÀY" in result or "tối" in result

    def test_unknown_session_falls_back_to_morning(self):
        result = build_session_prompt("unknown", {}, "22/04/2026", {})
        assert "SÁNG" in result or "sáng" in result

    def test_date_str_substituted(self):
        result = build_session_prompt("morning", {}, "15/03/2026", {})
        assert "15/03/2026" in result

    def test_interest_section_injected(self):
        profile = {"AI": 9}
        result = build_session_prompt("morning", profile, "22/04/2026", {})
        assert "AI" in result
        assert "9/10" in result

    def test_memory_section_injected_when_present(self):
        memory = {"liked_topics": ["Kinh tế"]}
        result = build_session_prompt("morning", {}, "22/04/2026", memory)
        assert "Kinh tế" in result

    def test_empty_memory_no_liked_block(self):
        result = build_session_prompt("morning", {}, "22/04/2026", {})
        assert "Sở thích học được" not in result


class TestBuildMemoryExtractionPrompt:
    def test_chat_text_substituted(self):
        result = build_memory_extraction_prompt("user asked about AI")
        assert "user asked about AI" in result

    def test_truncates_at_3000_chars(self):
        long_text = "x" * 4000
        result = build_memory_extraction_prompt(long_text)
        assert "x" * 3000 in result
        assert "x" * 3001 not in result

    def test_contains_json_format_hint(self):
        result = build_memory_extraction_prompt("some chat")
        assert "liked" in result
        assert "disliked" in result

    def test_short_text_not_truncated(self):
        short = "hello world"
        result = build_memory_extraction_prompt(short)
        assert short in result


class TestBuildNewsSystemInstruction:
    def test_returns_non_empty_string(self):
        result = build_news_system_instruction()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_html_format_rule(self):
        result = build_news_system_instruction()
        assert "HTML" in result

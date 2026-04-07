"""
Unit tests for app/agents/news/prompts.py
RED phase: all tests must FAIL before implementation exists.
"""
import pytest

from app.agents.news.prompts import (
    build_news_system_instruction,
    build_morning_news_prompt,
    build_afternoon_news_prompt,
)


SAMPLE_DATE = "Saturday, 05/04/2026"
SAMPLE_ARTICLES = "VnExpress: Tin tức 1\nTóm tắt tin tức 1"


# ---------------------------------------------------------------------------
# Morning prompt
# ---------------------------------------------------------------------------

def test_morning_prompt_contains_date():
    prompt = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert SAMPLE_DATE in prompt


def test_morning_prompt_contains_articles():
    prompt = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert SAMPLE_ARTICLES in prompt


def test_morning_prompt_is_vietnamese():
    prompt = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    vietnamese_markers = ["sáng", "tin", "tóm tắt", "SÁNG"]
    assert any(m.lower() in prompt.lower() for m in vietnamese_markers)


def test_morning_prompt_handles_curly_braces_in_articles():
    """Articles may contain JSON-like {} from RSS — must not raise KeyError."""
    articles_with_braces = 'Tăng trưởng GDP {2.5%} so với Q1 năm ngoái'
    prompt = build_morning_news_prompt(articles_with_braces, SAMPLE_DATE)
    assert articles_with_braces in prompt


def test_morning_prompt_handles_empty_articles():
    prompt = build_morning_news_prompt("", SAMPLE_DATE)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


# ---------------------------------------------------------------------------
# Afternoon prompt
# ---------------------------------------------------------------------------

def test_afternoon_prompt_contains_date():
    prompt = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert SAMPLE_DATE in prompt


def test_afternoon_prompt_contains_articles():
    prompt = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert SAMPLE_ARTICLES in prompt


def test_afternoon_prompt_is_vietnamese():
    prompt = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    vietnamese_markers = ["chiều", "cập nhật", "CHIỀU"]
    assert any(m.lower() in prompt.lower() for m in vietnamese_markers)


def test_afternoon_prompt_handles_curly_braces_in_articles():
    articles_with_braces = 'Lãi suất tăng {0.5%} theo quyết định của Fed'
    prompt = build_afternoon_news_prompt(articles_with_braces, SAMPLE_DATE)
    assert articles_with_braces in prompt


# ---------------------------------------------------------------------------
# Both prompts return strings
# ---------------------------------------------------------------------------

def test_morning_prompt_returns_string():
    result = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert isinstance(result, str)


def test_afternoon_prompt_returns_string():
    result = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert isinstance(result, str)


def test_morning_and_afternoon_prompts_differ():
    morning = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    afternoon = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    assert morning != afternoon


# ---------------------------------------------------------------------------
# System instruction (News Agent identity)
# ---------------------------------------------------------------------------

def test_system_instruction_returns_string():
    result = build_news_system_instruction()
    assert isinstance(result, str)
    assert len(result) > 0


def test_system_instruction_is_vietnamese():
    result = build_news_system_instruction()
    vietnamese_markers = ["tin tức", "Telegram", "tiếng Việt"]
    assert any(m.lower() in result.lower() for m in vietnamese_markers)


def test_system_instruction_has_no_coach_content():
    """News system instruction must NOT contain coach-specific terms."""
    result = build_news_system_instruction()
    coach_terms = ["Coach Dyno", "TRIMP", "ACWR", "HR Zone", "chạy bộ", "vận động viên"]
    for term in coach_terms:
        assert term not in result, f"News system instruction should not contain coach term: {term}"


def test_system_instruction_has_telegram_format_rules():
    result = build_news_system_instruction()
    assert "Markdown" in result or "HTML" in result
    assert "<b>" in result


def test_system_instruction_not_in_user_prompts():
    """User prompts should not duplicate system instruction content (persona, format rules)."""
    morning = build_morning_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    afternoon = build_afternoon_news_prompt(SAMPLE_ARTICLES, SAMPLE_DATE)
    # Format rules moved to system instruction — user prompts should not contain them
    assert "<b>" not in morning
    assert "<b>" not in afternoon

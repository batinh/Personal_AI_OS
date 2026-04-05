"""
Unit tests for app/agents/news/prompts.py
RED phase: all tests must FAIL before implementation exists.
"""
import pytest

from app.agents.news.prompts import build_morning_news_prompt, build_afternoon_news_prompt


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

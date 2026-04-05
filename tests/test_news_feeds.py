"""
Unit tests for app/agents/news/feeds.py
RED phase: all tests must FAIL before implementation exists.
"""
import pytest
from unittest.mock import patch, MagicMock
from requests.exceptions import Timeout, ConnectionError

from app.agents.news.feeds import Article, fetch_feed, fetch_all_feeds


# ---------------------------------------------------------------------------
# Article dataclass
# ---------------------------------------------------------------------------

def test_article_is_frozen():
    a = Article(title="T", summary="S", link="http://x.com", source="X", published="2026-04-05")
    with pytest.raises((AttributeError, TypeError)):
        a.title = "changed"  # type: ignore[misc]


def test_article_summary_stored_as_given():
    long_summary = "x" * 400
    a = Article(title="T", summary=long_summary, link="http://x.com", source="X", published="")
    assert a.summary == long_summary


# ---------------------------------------------------------------------------
# fetch_feed — single feed, isolated
# ---------------------------------------------------------------------------

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Article One</title>
      <link>http://example.com/1</link>
      <description>Summary of article one.</description>
      <pubDate>Sat, 05 Apr 2026 07:00:00 +0700</pubDate>
    </item>
    <item>
      <title>Article Two</title>
      <link>http://example.com/2</link>
      <description>Summary of article two.</description>
      <pubDate>Sat, 05 Apr 2026 06:00:00 +0700</pubDate>
    </item>
    <item>
      <title>Article Three</title>
      <link>http://example.com/3</link>
      <description>Summary of article three.</description>
    </item>
  </channel>
</rss>"""


def _mock_response(content: bytes, status_code: int = 200):
    mock = MagicMock()
    mock.content = content
    mock.status_code = status_code
    return mock


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_returns_articles(mock_get):
    mock_get.return_value = _mock_response(SAMPLE_RSS)
    articles = fetch_feed("http://feed.example.com", "TestSource", max_articles=5)
    assert len(articles) == 3
    assert all(isinstance(a, Article) for a in articles)


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_respects_max_articles(mock_get):
    mock_get.return_value = _mock_response(SAMPLE_RSS)
    articles = fetch_feed("http://feed.example.com", "TestSource", max_articles=2)
    assert len(articles) == 2


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_sets_source(mock_get):
    mock_get.return_value = _mock_response(SAMPLE_RSS)
    articles = fetch_feed("http://feed.example.com", "VnExpress", max_articles=5)
    assert all(a.source == "VnExpress" for a in articles)


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_uses_timeout(mock_get):
    mock_get.return_value = _mock_response(SAMPLE_RSS)
    fetch_feed("http://feed.example.com", "S", max_articles=5, timeout=10)
    call_kwargs = mock_get.call_args[1]
    assert call_kwargs.get("timeout") == 10


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_timeout_returns_empty(mock_get):
    mock_get.side_effect = Timeout()
    articles = fetch_feed("http://feed.example.com", "S", max_articles=5)
    assert articles == []


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_connection_error_returns_empty(mock_get):
    mock_get.side_effect = ConnectionError()
    articles = fetch_feed("http://feed.example.com", "S", max_articles=5)
    assert articles == []


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_malformed_xml_returns_empty(mock_get):
    mock_get.return_value = _mock_response(b"not xml at all <<<>>>")
    # feedparser is lenient; this should not raise but return empty or partial
    articles = fetch_feed("http://feed.example.com", "S", max_articles=5)
    assert isinstance(articles, list)


@patch("app.agents.news.feeds.requests.get")
def test_fetch_feed_truncates_summary_to_300(mock_get):
    long_desc = "A" * 600
    rss = f"""<rss version="2.0"><channel>
        <item><title>T</title><link>http://x.com/1</link>
        <description>{long_desc}</description></item>
    </channel></rss>""".encode()
    mock_get.return_value = _mock_response(rss)
    articles = fetch_feed("http://feed.example.com", "S", max_articles=5)
    assert len(articles[0].summary) <= 300


# ---------------------------------------------------------------------------
# fetch_all_feeds — multiple feeds with isolation
# ---------------------------------------------------------------------------

def test_fetch_all_feeds_empty_list():
    assert fetch_all_feeds([]) == []


@patch("app.agents.news.feeds.fetch_feed")
def test_fetch_all_feeds_calls_each_feed(mock_fetch):
    mock_fetch.return_value = []
    feeds = [
        {"name": "A", "url": "http://a.com/rss"},
        {"name": "B", "url": "http://b.com/rss"},
    ]
    fetch_all_feeds(feeds, max_per_feed=3)
    assert mock_fetch.call_count == 2


@patch("app.agents.news.feeds.fetch_feed")
def test_fetch_all_feeds_partial_failure_returns_others(mock_fetch):
    """If feed A fails (returns []), articles from feed B still returned."""
    article_b = Article(title="B", summary="s", link="http://b.com/1", source="B", published="")
    mock_fetch.side_effect = [[], [article_b]]
    feeds = [{"name": "A", "url": "http://a.com"}, {"name": "B", "url": "http://b.com"}]
    result = fetch_all_feeds(feeds, max_per_feed=5)
    assert result == [article_b]


@patch("app.agents.news.feeds.fetch_feed")
def test_fetch_all_feeds_concatenates_results(mock_fetch):
    a1 = Article(title="A1", summary="s", link="http://a.com/1", source="A", published="")
    a2 = Article(title="B1", summary="s", link="http://b.com/1", source="B", published="")
    mock_fetch.side_effect = [[a1], [a2]]
    feeds = [{"name": "A", "url": "http://a.com"}, {"name": "B", "url": "http://b.com"}]
    result = fetch_all_feeds(feeds, max_per_feed=5)
    assert result == [a1, a2]

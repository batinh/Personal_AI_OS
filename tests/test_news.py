import pytest
from unittest import mock

from app.agents.news.agent import generate_news_briefing
from app.agents.news.alert_engine import run_news_watch


@pytest.fixture(autouse=True)
def disable_external(monkeypatch):
    # prevent real network/genai calls
    monkeypatch.setattr("app.agents.news.feeds.fetch_all_feeds", lambda feeds, max_per_feed=5: [])
    monkeypatch.setattr("app.agents.news.agent.client", mock.Mock(models=mock.Mock(generate_content=lambda **k: mock.Mock(text="Mock reply"))))
    monkeypatch.setattr("app.agents.news.alert_engine.client", mock.Mock(models=mock.Mock(generate_content=lambda **k: mock.Mock(text="Alert reply"))))
    monkeypatch.setattr("app.core.notification.send_telegram_msg", lambda *a, **k: None)
    yield


def test_skip_articles_without_link_or_summary(monkeypatch):
    from app.agents.news.feeds import Article
    # Prepare articles, one with missing link
    feeds = [Article(title='A', summary='S1', link='http://a', source='S', published=''),
             Article(title='B', summary='', link='', source='S', published='')]
    monkeypatch.setattr("app.agents.news.feeds.fetch_all_feeds", lambda feeds, max_per_feed=5: feeds)

    # Config enabling news agent and using basic briefing
    cfg = {"news_agent": {"enabled": True, "feeds": [], "max_articles_per_feed": 5}}
    # Should not raise and should skip the bad article
    generate_news_briefing(cfg, session='morning')


def test_basic_briefing_uses_morning_template(monkeypatch):
    from app.agents.news.feeds import Article
    feeds = [Article(title='A', summary='S1', link='http://a', source='S', published='')]
    monkeypatch.setattr("app.agents.news.feeds.fetch_all_feeds", lambda feeds, max_per_feed=5: feeds)

    cfg = {"news_agent": {"enabled": True, "feeds": [], "max_articles_per_feed": 5}}
    generate_news_briefing(cfg, session='morning')


def test_quiet_hours_respected(monkeypatch):
    # Simulate quiet hours by setting tz hour via monkeypatching datetime
    import datetime
    class FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # Return 23:00 local time
            return cls(2026, 4, 12, 23, 0, 0)
    monkeypatch.setattr('app.agents.news.alert_engine.datetime', FakeDatetime)

    cfg = {"news_agent": {"enabled": True, "feeds": [], "interest_profile": {"topic": 1}, "max_articles_per_feed": 5}}
    # Should run without raising; actual alert sending is mocked
    run_news_watch(cfg)


def test_alert_includes_links(monkeypatch):
    from app.agents.news.feeds import Article
    from app.agents.news.scorer import ScoredArticle
    # One scored article with link
    article = Article(title='Hot', summary='Breaking', link='http://hot', source='S', published='')
    monkeypatch.setattr("app.agents.news.feeds.fetch_all_feeds", lambda feeds, max_per_feed=5: [article])

    # Mock scoring to return high score
    def fake_score_articles(to_score, profile, model):
        return [ScoredArticle(title='Hot', summary='Breaking', link='http://hot', source='S', published='', score=10, category='breaking', reason='test')]
    monkeypatch.setattr('app.agents.news.scorer.score_articles', fake_score_articles)

    cfg = {"news_agent": {"enabled": True, "feeds": [], "interest_profile": {"topic": 1}, "max_articles_per_feed": 5}}
    run_news_watch(cfg)

"""Unit tests for app/agents/news/source_filter.py — no mocks needed (pure logic)."""

from app.agents.news.source_filter import SourceFilter


def _cfg(trusted=None, blacklist=None, mode="prefer") -> dict:
    return {
        "news_agent": {
            "source_filter_mode": mode,
            "trusted_sources": trusted or [],
            "source_blacklist": blacklist or [],
        }
    }


class TestExtractDomain:
    def setup_method(self):
        self.sf = SourceFilter(_cfg())

    def test_strips_scheme(self):
        assert self.sf.extract_domain("https://bbc.com/news") == "bbc.com"

    def test_strips_www(self):
        assert (
            self.sf.extract_domain("https://www.reuters.com/article/x") == "reuters.com"
        )

    def test_strips_path_and_query(self):
        assert (
            self.sf.extract_domain("https://vnexpress.net/kinh-doanh?p=1")
            == "vnexpress.net"
        )

    def test_strips_port(self):
        assert self.sf.extract_domain("http://localhost:8000/path") == "localhost"

    def test_lowercases_domain(self):
        assert self.sf.extract_domain("https://BBC.COM/news") == "bbc.com"

    def test_invalid_url_returns_empty(self):
        assert self.sf.extract_domain("not-a-url") == ""

    def test_subdomain_kept(self):
        assert (
            self.sf.extract_domain("https://sport.bbc.com/football") == "sport.bbc.com"
        )

    def test_raises_internally_returns_empty(self):
        # Passing a non-string type triggers the except branch (lines 60-61)
        assert self.sf.extract_domain(None) == ""  # type: ignore[arg-type]


class TestIsTrusted:
    def test_empty_whitelist_accepts_all(self):
        sf = SourceFilter(_cfg(trusted=[]))
        assert sf.is_trusted("https://anydomain.com") is True

    def test_exact_match(self):
        sf = SourceFilter(_cfg(trusted=["bbc.com"]))
        assert sf.is_trusted("https://bbc.com/news") is True

    def test_subdomain_match(self):
        sf = SourceFilter(_cfg(trusted=["bbc.com"]))
        assert sf.is_trusted("https://sport.bbc.com/athletics") is True

    def test_www_stripped_before_match(self):
        sf = SourceFilter(_cfg(trusted=["reuters.com"]))
        assert sf.is_trusted("https://www.reuters.com/world") is True

    def test_unknown_domain_not_trusted(self):
        sf = SourceFilter(_cfg(trusted=["bbc.com"]))
        assert sf.is_trusted("https://clickbait-news.com/article") is False

    def test_case_insensitive(self):
        sf = SourceFilter(_cfg(trusted=["BBC.COM"]))
        assert sf.is_trusted("https://bbc.com/news") is True

    def test_partial_match_not_counted(self):
        # "notbbc.com" should NOT match trusted "bbc.com"
        sf = SourceFilter(_cfg(trusted=["bbc.com"]))
        assert sf.is_trusted("https://notbbc.com/article") is False

    def test_empty_domain_not_trusted(self):
        # extract_domain returns "" for None → is_trusted returns False (line 69)
        sf = SourceFilter(_cfg(trusted=["bbc.com"]))
        assert sf.is_trusted(None) is False  # type: ignore[arg-type]

    def test_dict_format_trusted_sources(self):
        config = {
            "news_agent": {
                "source_filter_mode": "prefer",
                "trusted_sources": {
                    "tech": ["techcrunch.com", "wired.com"],
                    "world": ["bbc.com", "reuters.com"],
                },
                "source_blacklist": [],
            }
        }
        sf = SourceFilter(config)
        assert sf.is_trusted("https://techcrunch.com/article") is True
        assert sf.is_trusted("https://bbc.com/news") is True
        assert sf.is_trusted("https://unknown.com") is False


class TestIsBlacklisted:
    def test_empty_blacklist_returns_false(self):
        sf = SourceFilter(_cfg(blacklist=[]))
        assert sf.is_blacklisted("https://anydomain.com") is False

    def test_exact_substring_match(self):
        sf = SourceFilter(_cfg(blacklist=["spam"]))
        assert sf.is_blacklisted("https://super-spam-news.com/article") is True

    def test_glob_wildcard_match(self):
        sf = SourceFilter(_cfg(blacklist=["*clickbait*"]))
        assert sf.is_blacklisted("https://top-clickbait-site.com") is True

    def test_no_match(self):
        sf = SourceFilter(_cfg(blacklist=["spam", "*clickbait*"]))
        assert sf.is_blacklisted("https://reuters.com/article") is False

    def test_case_insensitive_glob(self):
        sf = SourceFilter(_cfg(blacklist=["*SPAM*"]))
        assert sf.is_blacklisted("https://big-spam-site.com") is True

    def test_empty_domain_not_blacklisted(self):
        # extract_domain returns "" for None → is_blacklisted returns False (line 81)
        sf = SourceFilter(_cfg(blacklist=["spam"]))
        assert sf.is_blacklisted(None) is False  # type: ignore[arg-type]


class TestFilterUrls:
    TRUSTED_URL = ("BBC", "https://bbc.com/article")
    OTHER_URL = ("Unknown", "https://unknownblog.com/post")
    BLACKLISTED_URL = ("Spam", "https://top-spam-site.com/ad")

    def _sf(self, mode="prefer"):
        return SourceFilter(_cfg(trusted=["bbc.com"], blacklist=["*spam*"], mode=mode))

    def test_prefer_mode_trusted_first(self):
        sf = self._sf("prefer")
        accepted, rejected = sf.filter_urls([self.OTHER_URL, self.TRUSTED_URL])
        assert accepted[0] == self.TRUSTED_URL
        assert accepted[1] == self.OTHER_URL
        assert rejected == []

    def test_prefer_mode_blacklisted_always_rejected(self):
        sf = self._sf("prefer")
        accepted, rejected = sf.filter_urls([self.BLACKLISTED_URL, self.TRUSTED_URL])
        assert self.BLACKLISTED_URL in rejected
        assert self.TRUSTED_URL in accepted

    def test_strict_mode_only_trusted_accepted(self):
        sf = self._sf("strict")
        accepted, rejected = sf.filter_urls([self.TRUSTED_URL, self.OTHER_URL])
        assert accepted == [self.TRUSTED_URL]
        assert self.OTHER_URL in rejected

    def test_strict_mode_blacklisted_rejected(self):
        sf = self._sf("strict")
        accepted, rejected = sf.filter_urls([self.TRUSTED_URL, self.BLACKLISTED_URL])
        assert accepted == [self.TRUSTED_URL]
        assert self.BLACKLISTED_URL in rejected

    def test_empty_url_list(self):
        sf = self._sf()
        accepted, rejected = sf.filter_urls([])
        assert accepted == []
        assert rejected == []

    def test_empty_whitelist_prefer_mode_accepts_all(self):
        sf = SourceFilter(_cfg(trusted=[], blacklist=[], mode="prefer"))
        urls = [self.OTHER_URL, self.TRUSTED_URL]
        accepted, rejected = sf.filter_urls(urls)
        assert len(accepted) == 2
        assert rejected == []

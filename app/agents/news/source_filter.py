"""
Source quality filter for the News Agent.

Two modes (set via config news_agent.source_filter_mode):
  "prefer" (default): trusted sources sorted first; non-trusted still allowed.
  "strict": only trusted + non-blacklisted sources pass through.

If trusted_sources list is empty, all sources are accepted (no filtering).
"""

import re
from urllib.parse import urlparse

from app.core.logging_conf import get_module_logger

logger = get_module_logger("news")


class SourceFilter:
    def __init__(self, config: dict) -> None:
        news_cfg = config.get("news_agent", {})
        self._mode: str = news_cfg.get("source_filter_mode", "prefer")

        trusted_raw = news_cfg.get("trusted_sources", [])
        blacklist_raw = news_cfg.get("source_blacklist", [])

        # Accept both flat list and {category: [domains]} dict
        if isinstance(trusted_raw, dict):
            domains: list[str] = []
            for v in trusted_raw.values():
                domains.extend(v)
            self._trusted: frozenset[str] = frozenset(d.lower() for d in domains)
        else:
            self._trusted = frozenset(d.lower() for d in trusted_raw)

        self._blacklist: list[str] = [p.lower() for p in blacklist_raw]

        logger.info(
            "[NEWS-FILTER] mode=%s trusted_domains=%d blacklist_patterns=%d",
            self._mode,
            len(self._trusted),
            len(self._blacklist),
        )

    @property
    def mode(self) -> str:
        return self._mode

    def extract_domain(self, url: str) -> str:
        """Return bare domain (no scheme, no path, no www. prefix, lowercase)."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc
            if not host:
                return ""
            host = host.split(":")[0]  # strip port
            if host.startswith("www."):
                host = host[4:]
            return host.lower()
        except Exception:
            return ""

    def is_trusted(self, url: str) -> bool:
        """Return True if domain is in the trusted list (or list is empty)."""
        if not self._trusted:
            return True
        domain = self.extract_domain(url)
        if not domain:
            return False
        # Exact match or subdomain (e.g. "sport.bbc.com" is trusted via "bbc.com")
        return domain in self._trusted or any(
            domain.endswith("." + t) for t in self._trusted
        )

    def is_blacklisted(self, url: str) -> bool:
        """Return True if domain matches any blacklist glob pattern."""
        if not self._blacklist:
            return False
        domain = self.extract_domain(url)
        if not domain:
            return False
        for pattern in self._blacklist:
            if "*" in pattern:
                regex = re.escape(pattern).replace(r"\*", ".*")
                if re.search(regex, domain):
                    return True
            elif pattern in domain:
                return True
        return False

    def filter_urls(
        self, urls: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """
        Partition (title, uri) pairs into (accepted, rejected).

        prefer mode: accepted = trusted-first + other; rejected = blacklisted only.
        strict mode: accepted = trusted only; rejected = blacklisted + non-trusted.
        """
        trusted: list[tuple[str, str]] = []
        other: list[tuple[str, str]] = []
        rejected: list[tuple[str, str]] = []

        for title, uri in urls:
            domain = self.extract_domain(uri)
            if self.is_blacklisted(uri):
                logger.info(
                    "[NEWS-SOURCE-REJECTED] domain=%s reason=blacklisted", domain
                )
                rejected.append((title, uri))
            elif self.is_trusted(uri):
                logger.info("[NEWS-SOURCE-TRUSTED] domain=%s", domain)
                trusted.append((title, uri))
            else:
                logger.info(
                    "[NEWS-SOURCE-OTHER] domain=%s (not in trusted list)", domain
                )
                other.append((title, uri))

        if self._mode == "strict":
            return trusted, rejected + other
        # prefer: trusted first, then other, blacklisted always rejected
        return trusted + other, rejected

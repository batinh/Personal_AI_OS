"""
test_telegram_router.py — Tests for app/services/telegram_router.py
====================================================================
Covers route_message():
  - @news prefix → routed to news agent, prefix stripped
  - @tin prefix → routed to news agent, prefix stripped
  - Prefix case-insensitive matching
  - No prefix → routed to coach, text unchanged
  - Empty text → routed to coach
  - @news with no query → news agent, empty cleaned text
"""

import unittest

from app.services.telegram_router import route_message


class TestRouteMessage(unittest.TestCase):

    # --- News routing ---

    def test_at_news_routes_to_news(self):
        agent, text = route_message("@news gì mới hôm nay?")
        self.assertEqual(agent, "news")
        self.assertEqual(text, "gì mới hôm nay?")

    def test_at_tin_routes_to_news(self):
        agent, text = route_message("@tin có tin gì về AI không?")
        self.assertEqual(agent, "news")
        self.assertEqual(text, "có tin gì về AI không?")

    def test_prefix_case_insensitive(self):
        agent, text = route_message("@NEWS tin công nghệ hôm nay")
        self.assertEqual(agent, "news")
        self.assertEqual(text, "tin công nghệ hôm nay")

    def test_at_news_prefix_with_extra_spaces(self):
        agent, text = route_message("  @news   câu hỏi  ")
        self.assertEqual(agent, "news")
        self.assertEqual(text, "câu hỏi")

    def test_at_news_no_query_returns_empty_cleaned(self):
        agent, text = route_message("@news")
        self.assertEqual(agent, "news")
        self.assertEqual(text, "")

    # --- Coach routing (default) ---

    def test_no_prefix_routes_to_coach(self):
        agent, text = route_message("hôm nay chạy bao nhiêu km?")
        self.assertEqual(agent, "coach")
        self.assertEqual(text, "hôm nay chạy bao nhiêu km?")

    def test_empty_text_routes_to_coach(self):
        agent, text = route_message("")
        self.assertEqual(agent, "coach")
        self.assertEqual(text, "")

    def test_at_news_mid_sentence_routes_to_coach(self):
        """@news not at the start → coach."""
        agent, text = route_message("hỏi @news về tin tức")
        self.assertEqual(agent, "coach")

    def test_whitespace_only_routes_to_coach(self):
        agent, text = route_message("   ")
        self.assertEqual(agent, "coach")
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()

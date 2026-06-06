"""
Telegram Chunking Tests — _split_html_naive / _split_plain
===========================================================
These tests verify no content is lost and each chunk stays within the
Telegram 4000-char limit when splitting realistic news briefing HTML.

Run: python -m pytest tests/test_telegram_chunking.py -v
"""

import html
import re
import unittest

from app.core.notification import _split_html_naive as split_html_preserving_tags

_STRIP_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    """Strip HTML tags and unescape entities for content-equality checks."""
    return html.unescape(_STRIP_TAG_RE.sub("", text))


def _make_news_block(
    topic: str, n_items: int = 3, url: str = "https://example.com/article"
) -> str:
    """Build a realistic news topic block matching the actual news agent format."""
    lines = [f"📰 <b>{topic.upper()}</b>", ""]
    for i in range(1, n_items + 1):
        lines.append(
            f"• <b>Tin số {i}:</b> Đây là tóm tắt tin tức số {i} về chủ đề {topic}. "
            f"Nội dung chi tiết gồm nhiều thông tin quan trọng về thị trường và xu hướng. "
            f'<a href="{url}?id={i}">Đọc thêm</a>'
        )
        lines.append("")
    lines += ["📎 <b>Nguồn:</b>", f'• <a href="{url}">Nguồn chính - {topic}</a>', ""]
    return "\n".join(lines)


def _make_full_briefing(n_topics: int = 5) -> str:
    """Build a realistic 5-topic briefing (~15000+ chars)."""
    topic_names = [
        "CHẠY BỘ & THỂ THAO",
        "TÀI CHÍNH & KINH TẾ",
        "CÔNG NGHỆ",
        "SỨC KHỎE",
        "THẾ GIỚI",
    ]
    blocks = [_make_news_block(t, n_items=4) for t in topic_names[:n_topics]]
    separator = "\n\n─────\n\n"
    header = "📰 <b>TIN TỨC BUỔI SÁNG — 19/04/2026</b>"
    return header + "\n\n" + separator.join(blocks)


class TestChunkSizeBound(unittest.TestCase):
    """Every chunk must be ≤ limit characters."""

    def test_all_chunks_within_limit_short_message(self):
        text = "<b>Short</b> message under limit."
        chunks = split_html_preserving_tags(text, 4000)
        for i, c in enumerate(chunks):
            self.assertLessEqual(len(c), 4000, f"Chunk {i} exceeds limit: len={len(c)}")

    def test_all_chunks_within_limit_full_briefing(self):
        text = _make_full_briefing(5)
        self.assertGreater(
            len(text), 4000, "Test briefing too short — not exercising chunking"
        )
        chunks = split_html_preserving_tags(text, 4000)
        for i, c in enumerate(chunks):
            self.assertLessEqual(
                len(c), 4000, f"Chunk {i} exceeds 4000 chars: len={len(c)}"
            )

    def test_produces_multiple_chunks_for_large_input(self):
        text = _make_full_briefing(5)
        chunks = split_html_preserving_tags(text, 4000)
        self.assertGreater(
            len(chunks), 1, "Large briefing must produce multiple chunks"
        )


class TestContentPreservation(unittest.TestCase):
    """No content must be lost when splitting — this is the core truncation check."""

    def _assert_no_content_loss(self, original: str, limit: int = 4000):
        import re

        chunks = split_html_preserving_tags(original, limit)
        # Strip whitespace for comparison (split points consume word-boundary spaces)
        combined_flat = re.sub(r"\s+", "", _plain("".join(chunks)))
        original_flat = re.sub(r"\s+", "", _plain(original))
        self.assertEqual(
            combined_flat,
            original_flat,
            f"Content lost during chunking!\n"
            f"Original length: {len(original_flat)}\n"
            f"Joined  length: {len(combined_flat)}\n"
            f"Chunks: {len(chunks)}\n"
            f"First diff at char: {next((i for i, (a, b) in enumerate(zip(original_flat, combined_flat)) if a != b), 'end')}",
        )

    def test_no_content_loss_simple_text(self):
        text = "A" * 8000
        self._assert_no_content_loss(text, 4000)

    def test_no_content_loss_single_topic(self):
        text = _make_news_block("CHẠY BỘ", n_items=6)
        self._assert_no_content_loss(text, 4000)

    def test_no_content_loss_full_briefing(self):
        text = _make_full_briefing(5)
        self._assert_no_content_loss(text, 4000)

    def test_no_content_loss_tight_limit(self):
        """Stress test: small limit forces many chunks."""
        text = _make_news_block("TEST", n_items=4)
        self._assert_no_content_loss(text, 500)

    def test_single_chunk_for_short_message(self):
        text = "<b>Xin chào!</b> Đây là tin ngắn."
        chunks = split_html_preserving_tags(text, 4000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(_plain(chunks[0]), _plain(text))


class TestLinkPreservation(unittest.TestCase):
    """<a href> links must survive chunking — this is the direct regression test
    for the 'links all appear at end of topic' bug reported by user."""

    def test_links_not_moved_to_end(self):
        """Each chunk must have its own inline links — links must not be grouped."""
        # Build content where links appear inline after each news item
        lines = ["<b>TOPIC</b>\n"]
        for i in range(10):
            lines.append(
                f"• Tin số {i + 1}: Nội dung chi tiết tin tức số {i + 1}. "
                f'<a href="https://example.com/article{i + 1}">Đọc thêm</a>\n'
            )
        text = "\n".join(lines)
        chunks = split_html_preserving_tags(text, 600)

        # Every chunk that contains "Đọc thêm" must also contain an href
        for i, chunk in enumerate(chunks):
            if "Đọc thêm" in chunk:
                self.assertIn(
                    "href=",
                    chunk,
                    f"Chunk {i} has 'Đọc thêm' text but no href — link was separated from text!",
                )

    def test_href_link_intact_in_chunk(self):
        """A link that fits within one chunk must remain intact."""
        url = "https://vnexpress.net/tin-nong-abcdef"
        text = f'Bài viết nổi bật: <a href="{url}">Đọc thêm</a>'
        chunks = split_html_preserving_tags(text, 4000)
        self.assertEqual(len(chunks), 1)
        self.assertIn(f'href="{url}"', chunks[0])
        self.assertIn("Đọc thêm", chunks[0])


class TestTagBalance(unittest.TestCase):
    """Every chunk must have balanced HTML tags."""

    def _is_balanced(self, text: str) -> bool:
        stack = []
        for m in re.finditer(r"<(/?)([a-zA-Z0-9\-]+)[^>]*>", text):
            if m.group(1):  # closing
                if stack and stack[-1] == m.group(2).lower():
                    stack.pop()
                # if unmatched close, that's fine for Telegram (it's lenient)
            else:
                tag = m.group(2).lower()
                # self-closing or void elements don't need close
                if tag not in ("br", "hr", "img", "input", "meta", "link"):
                    stack.append(tag)
        return len(stack) == 0

    def test_chunks_have_balanced_b_tags(self):
        text = _make_full_briefing(5)
        chunks = split_html_preserving_tags(text, 4000)
        for i, chunk in enumerate(chunks):
            open_b = chunk.count("<b>")
            close_b = chunk.count("</b>")
            self.assertEqual(
                open_b,
                close_b,
                f"Chunk {i}: unbalanced <b> tags (open={open_b}, close={close_b})",
            )

    def test_chunks_have_balanced_a_tags(self):
        text = _make_full_briefing(5)
        chunks = split_html_preserving_tags(text, 4000)
        for i, chunk in enumerate(chunks):
            # Count <a ...> (not </a>)
            open_a = len(re.findall(r"<a\s", chunk))
            close_a = chunk.count("</a>")
            self.assertEqual(
                open_a,
                close_a,
                f"Chunk {i}: unbalanced <a> tags (open={open_a}, close={close_a})",
            )


class TestEdgeCases(unittest.TestCase):
    """Edge cases that have historically caused chunking bugs."""

    def test_empty_string_returns_empty_list_or_one_empty(self):
        chunks = split_html_preserving_tags("", 4000)
        # Either empty list or single empty-ish chunk is acceptable
        combined = "".join(chunks)
        self.assertEqual(combined.strip(), "")

    def test_single_token_longer_than_limit(self):
        """A single text token longer than limit must not lose content."""
        import re

        long_text = "Đây là đoạn văn rất dài: " + "A" * 5000
        chunks = split_html_preserving_tags(long_text, 1000)
        combined_flat = re.sub(r"\s+", "", _plain("".join(chunks)))
        original_flat = re.sub(r"\s+", "", _plain(long_text))
        self.assertEqual(combined_flat, original_flat)

    def test_message_at_exact_limit(self):
        """Message exactly at limit = single chunk, no content loss."""
        text = "A" * 4000
        chunks = split_html_preserving_tags(text, 4000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)

    def test_message_one_over_limit(self):
        """Message 1 char over limit must produce 2 chunks with no content loss."""
        text = "A" * 4001
        chunks = split_html_preserving_tags(text, 4000)
        self.assertEqual(len(chunks), 2)
        combined = "".join(chunks)
        self.assertEqual(combined, text)

    def test_vietnamese_text_preserved(self):
        import re

        text = "Chạy bộ 10km với nhịp tim < 150 bpm. Kết quả: tốt. " * 100
        # Escape for HTML context (simulate sanitize_md_to_tg_html output)
        safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
        chunks = split_html_preserving_tags(safe_text, 1000)
        combined_flat = re.sub(r"\s+", "", _plain("".join(chunks)))
        original_flat = re.sub(r"\s+", "", _plain(safe_text))
        self.assertEqual(combined_flat, original_flat)


class TestContentLossGuarantee(unittest.TestCase):
    """Invariant: joining all chunks and stripping HTML == stripping HTML from original.

    This is the core anti-truncation guarantee. If ANY refactor breaks this,
    these tests turn RED immediately.
    """

    LIMITS = [500, 1000, 2000, 4000, 10000]

    def _assert_no_loss(self, text: str, limit: int) -> None:
        import re

        chunks = split_html_preserving_tags(text, limit)
        combined = re.sub(r"\s+", "", _plain("".join(chunks)))
        original = re.sub(r"\s+", "", _plain(text))
        self.assertEqual(
            combined,
            original,
            f"Content lost at limit={limit}: original={len(original)} combined={len(combined)}",
        )

    def test_guarantee_plain_text_all_limits(self):
        text = "Chạy bộ 10km hôm nay rất tốt. " * 500  # ~15k chars
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self._assert_no_loss(text, limit)

    def test_guarantee_html_briefing_all_limits(self):
        text = _make_full_briefing(5)
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self._assert_no_loss(text, limit)

    def test_guarantee_oversized_single_paragraph_all_limits(self):
        big_para = "<b>Phân tích chi tiết:</b> " + (
            "Đây là nội dung phân tích dài. " * 200
        )
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self._assert_no_loss(big_para, limit)

    def test_guarantee_mixed_html_and_plain(self):
        text = (
            "## Buổi sáng hôm nay\n\n"
            + "**Cự ly:** 10km — <b>tốt</b> — HR < 150 bpm\n\n"
            + "Phân tích: "
            + "nội dung " * 300
            + "\n\n"
            + '<a href="https://example.com/run">Xem chi tiết</a>'
        )
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self._assert_no_loss(text, limit)


class TestSanitizeMdToTgHtml(unittest.TestCase):
    """sanitize_md_to_tg_html must balance all inline tags, not just <b>."""

    def setUp(self):
        from app.core.notification import sanitize_md_to_tg_html

        self.sanitize = sanitize_md_to_tg_html

    def test_balances_b_tag(self):
        result = self.sanitize("<b>unclosed bold")
        self.assertIn("</b>", result)

    def test_balances_i_tag(self):
        result = self.sanitize("<i>unclosed italic")
        self.assertIn("</i>", result)

    def test_balances_u_tag(self):
        result = self.sanitize("<u>unclosed underline")
        self.assertIn("</u>", result)

    def test_balances_s_tag(self):
        result = self.sanitize("<s>unclosed strike")
        self.assertIn("</s>", result)

    def test_balances_code_tag(self):
        result = self.sanitize("<code>unclosed code")
        self.assertIn("</code>", result)

    def test_balances_pre_tag(self):
        result = self.sanitize("<pre>unclosed pre block")
        self.assertIn("</pre>", result)

    def test_already_balanced_not_doubled(self):
        result = self.sanitize("<b>bold</b> and <i>italic</i>")
        self.assertEqual(result.count("</b>"), 1)
        self.assertEqual(result.count("</i>"), 1)

    def test_empty_string_passthrough(self):
        result = self.sanitize("")
        self.assertEqual(result, "")

    def test_none_passthrough(self):
        result = self.sanitize(None)
        self.assertIsNone(result)


class TestSendTelegramMsgRateLimit(unittest.TestCase):
    """send_telegram_msg must retry once on 429 and honour retry_after."""

    def setUp(self):
        import importlib

        self.notification = importlib.import_module("app.core.notification")

    @unittest.mock.patch("app.core.notification.time.sleep")
    @unittest.mock.patch("app.core.notification.requests.post")
    def test_retries_on_429(self, mock_post, mock_sleep):
        import unittest.mock

        rate_limit_resp = unittest.mock.MagicMock()
        rate_limit_resp.status_code = 429
        rate_limit_resp.json.return_value = {"parameters": {"retry_after": 3}}
        rate_limit_resp.text = '{"ok":false,"error_code":429}'

        ok_resp = unittest.mock.MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = '{"ok":true}'
        ok_resp.json.return_value = {"ok": True}

        mock_post.side_effect = [rate_limit_resp, ok_resp]

        with unittest.mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake"}):
            self.notification.send_telegram_msg("123", "hello")

        mock_sleep.assert_called_once_with(3)
        self.assertEqual(mock_post.call_count, 2)

    @unittest.mock.patch("app.core.notification.time.sleep")
    @unittest.mock.patch("app.core.notification.requests.post")
    def test_no_sleep_on_200(self, mock_post, mock_sleep):
        import unittest.mock

        ok_resp = unittest.mock.MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = '{"ok":true}'

        mock_post.return_value = ok_resp

        with unittest.mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake"}):
            self.notification.send_telegram_msg("123", "hello")

        mock_sleep.assert_not_called()


class TestPlainTextChunking(unittest.TestCase):
    """_split_plain: plain-text chunker must handle oversized tokens without losing content."""

    def _split(self, text: str, limit: int = 200) -> list[str]:
        from app.core.notification import _split_plain

        return _split_plain(text, limit)

    def test_oversized_single_token_is_force_split(self):
        """A single token larger than limit is force-split at limit boundary."""
        big_token = "X" * 500
        chunks = self._split(big_token, limit=200)
        for chunk in chunks:
            self.assertLessEqual(
                len(chunk), 200, f"Chunk exceeds limit: len={len(chunk)}"
            )
        self.assertEqual("".join(chunks), big_token)

    def test_no_content_loss(self):
        """All words must be present across chunks."""
        words = " ".join(f"word{i}" for i in range(200))
        chunks = self._split(words, limit=100)
        combined = " ".join(c.strip() for c in chunks)
        for i in range(200):
            self.assertIn(f"word{i}", combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)

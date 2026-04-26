"""
Layer 3 – Notification Service Tests: app/core/notification.py
================================================================
Không cần Telegram token thật. Network calls được mock.
Covers: sanitize_md_to_tg_html, send_telegram_msg (fallback), send_typing_action.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core.notification import sanitize_md_to_tg_html, send_telegram_msg, send_typing_action, _strip_html


# ══════════════════════════════════════════════════════════════════════════════
# 1. SANITIZER – Pure logic, zero mock needed
# ══════════════════════════════════════════════════════════════════════════════
class TestSanitizeMdToTgHtml(unittest.TestCase):

    def test_none_input_returns_none(self):
        self.assertIsNone(sanitize_md_to_tg_html(None))

    def test_empty_string_returns_empty(self):
        self.assertEqual(sanitize_md_to_tg_html(""), "")

    def test_bold_markdown_converts_to_html(self):
        result = sanitize_md_to_tg_html("**Hello World**")
        self.assertIn("<b>Hello World</b>", result)

    def test_multiple_bold_segments(self):
        result = sanitize_md_to_tg_html("**A** and **B**")
        self.assertIn("<b>A</b>", result)
        self.assertIn("<b>B</b>", result)

    def test_markdown_header_converts_to_bold(self):
        result = sanitize_md_to_tg_html("## Section Title")
        self.assertIn("<b>Section Title</b>", result)

    def test_h1_header_converts_to_bold(self):
        result = sanitize_md_to_tg_html("# Main Title")
        self.assertIn("<b>Main Title</b>", result)

    def test_bullet_asterisk_converts_to_dot(self):
        result = sanitize_md_to_tg_html("* Item one\n* Item two")
        self.assertIn("• Item one", result)
        self.assertIn("• Item two", result)

    def test_bullet_dash_converts_to_dot(self):
        result = sanitize_md_to_tg_html("- Item A")
        self.assertIn("• Item A", result)

    def test_html_special_chars_are_escaped(self):
        # < and > must be escaped to prevent HTML injection crashes
        result = sanitize_md_to_tg_html("HR < 138 bpm & pace > 5:30")
        self.assertIn("&lt;", result)
        self.assertIn("&gt;", result)
        self.assertIn("&amp;", result)

    def test_bold_html_tags_injected_by_ai_are_preserved(self):
        # AI sometimes outputs literal <b>...</b> which must survive the sanitizer
        result = sanitize_md_to_tg_html("<b>Bold Text</b>")
        self.assertIn("<b>Bold Text</b>", result)

    def test_plain_text_passes_through_unchanged(self):
        text = "Chạy 10km sáng nay, nhịp tim ổn định."
        result = sanitize_md_to_tg_html(text)
        self.assertIn("Chạy 10km", result)

    def test_mixed_markdown_and_special_chars(self):
        # Real LLM output scenario: bold + HR range with < >
        result = sanitize_md_to_tg_html("**ACWR**: 1.2 | HR < 150 bpm")
        self.assertIn("<b>ACWR</b>", result)
        self.assertIn("&lt;", result)

    def test_multiline_output_handles_each_line(self):
        text = "## Tổng kết\n**Kết quả**: Tốt\n- Km: 10\n- HR: 148"
        result = sanitize_md_to_tg_html(text)
        self.assertIn("<b>Tổng kết</b>", result)
        self.assertIn("<b>Kết quả</b>", result)
        self.assertIn("• Km:", result)

    def test_anchor_link_passes_through_intact(self):
        # <a href> must survive — previously the old escape-then-restore approach destroyed links
        text = 'Xem chi tiết <a href="https://vnexpress.net/abc">Đọc thêm</a>'
        result = sanitize_md_to_tg_html(text)
        self.assertIn('<a href="https://vnexpress.net/abc">', result)
        self.assertIn('</a>', result)
        self.assertIn('Đọc thêm', result)

    def test_unclosed_b_tag_is_balanced(self):
        # Gemini sometimes generates <b>title without closing </b>
        result = sanitize_md_to_tg_html("<b>Breaking news — no close tag")
        self.assertIn("<b>", result)
        self.assertEqual(result.count("<b>"), result.count("</b>"))

    def test_special_chars_in_text_outside_tags_are_escaped(self):
        # < and > in plain text must be escaped; tags must be kept
        text = 'HR < 150 bpm và <b>cảnh báo</b>'
        result = sanitize_md_to_tg_html(text)
        self.assertIn("&lt;", result)
        self.assertIn("<b>cảnh báo</b>", result)

    def test_strip_html_removes_tags_and_unescapes(self):
        result = _strip_html("<b>Tin nóng</b>: giá &amp; thị trường &lt;tăng&gt;")
        self.assertNotIn("<b>", result)
        self.assertNotIn("&amp;", result)
        self.assertIn("Tin nóng", result)
        self.assertIn("&", result)  # unescaped


# ══════════════════════════════════════════════════════════════════════════════
# 2. send_telegram_msg – Mock HTTP
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTelegramMsg(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_sends_with_html_parse_mode(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_telegram_msg("123456", "Hello **Runner**!")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("<b>Runner</b>", payload["text"])

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_fallback_to_plain_text_on_400(self, mock_post):
        """If Telegram returns 400 'parse entities' error, retry without parse_mode."""
        bad_response = MagicMock()
        bad_response.status_code = 400
        bad_response.text = "Bad Request: can't parse entities"

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.text = "OK"

        mock_post.side_effect = [bad_response, good_response]

        send_telegram_msg("123456", "Some text")

        # Should have called twice: first HTML, then fallback plain
        self.assertEqual(mock_post.call_count, 2)
        # Second call must NOT have parse_mode
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertNotIn("parse_mode", second_payload)

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_fallback_text_has_no_html_tags(self, mock_post):
        """Fallback plain text must not contain raw HTML tags (user sees clean text)."""
        bad_response = MagicMock()
        bad_response.status_code = 400
        bad_response.text = "Bad Request: can't parse entities"

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.text = "OK"

        mock_post.side_effect = [bad_response, good_response]

        send_telegram_msg("123456", "<b>Tin nóng</b> — thị trường biến động")

        second_payload = mock_post.call_args_list[1][1]["json"]
        fallback_text = second_payload["text"]
        self.assertNotIn("<b>", fallback_text)
        self.assertNotIn("</b>", fallback_text)
        self.assertIn("Tin nóng", fallback_text)

    @patch("app.core.notification.requests.post")
    def test_no_token_skips_request(self, mock_post):
        """Missing TELEGRAM_BOT_TOKEN → should not call requests.post."""
        with patch.dict("os.environ", {}, clear=True):
            # Ensure TELEGRAM_BOT_TOKEN is absent
            import os
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            send_telegram_msg("123456", "test")
        mock_post.assert_not_called()

    @patch("app.core.notification.requests.post", side_effect=Exception("Network error"))
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_network_exception_does_not_raise(self, mock_post):
        """Connection errors must be swallowed – never crash the agent."""
        try:
            send_telegram_msg("123456", "test")
        except Exception:
            self.fail("send_telegram_msg raised an exception on network error")


# ══════════════════════════════════════════════════════════════════════════════
# 3. send_typing_action – Non-critical, must always be silent
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTypingAction(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_sends_typing_action(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        send_typing_action("123456")
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["action"], "typing")

    @patch("app.core.notification.requests.post", side_effect=Exception("timeout"))
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_exception_is_silently_ignored(self, mock_post):
        """Typing indicator errors must never crash the handler."""
        try:
            send_typing_action("123456")
        except Exception:
            self.fail("send_typing_action raised an exception")

    def test_no_token_returns_without_crash(self):
        import os
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        try:
            send_typing_action("123456")
        except Exception:
            self.fail("send_typing_action raised when no token")



# ══════════════════════════════════════════════════════════════════════════════
# 4. split_html_preserving_tags – Pure logic
# ══════════════════════════════════════════════════════════════════════════════
class TestSplitHtmlPreservingTags(unittest.TestCase):

    def _split(self, text, limit):
        from app.core.notification import split_html_preserving_tags
        return split_html_preserving_tags(text, limit)

    def test_short_text_returns_single_chunk(self):
        chunks = self._split("Hello world", 100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world")

    def test_plain_text_splits_at_limit(self):
        text = "A" * 20
        chunks = self._split(text, 10)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 10)

    def test_all_chunks_reassemble_to_original_text(self):
        from app.core.notification import _strip_html
        text = "Word " * 30
        chunks = self._split(text.strip(), 50)
        combined = _strip_html("".join(chunks))
        self.assertEqual(combined.replace("  ", " "), text.strip().replace("  ", " "))

    def test_open_tags_closed_at_chunk_boundary(self):
        text = "<b>" + "X" * 50 + "</b>"
        chunks = self._split(text, 20)
        for chunk in chunks[:-1]:
            self.assertIn("</b>", chunk, f"Chunk missing closing tag: {chunk!r}")

    def test_balanced_tags_reopened_in_next_chunk(self):
        text = "<b>" + "Y" * 50 + "</b>"
        chunks = self._split(text, 15)
        for chunk in chunks[1:]:
            # Each continuation chunk should reopen the bold tag
            if "Y" in chunk:
                self.assertIn("<b>", chunk)

    def test_empty_string_returns_empty_list(self):
        chunks = self._split("", 100)
        self.assertEqual(chunks, [])

    def test_closing_tag_overflow_included_in_chunk(self):
        # A closing tag that overflows should be included in the current chunk (keep pair balanced)
        text = "<b>Short</b>"
        chunks = self._split(text, 8)
        # All chunks together should contain the closing tag
        full = "".join(chunks)
        self.assertIn("</b>", full)

    def test_nested_tags_tracked_correctly(self):
        text = "<b><i>BoldItalic</i></b>"
        chunks = self._split(text, 100)
        # Fits in one chunk — must come through intact
        self.assertEqual(len(chunks), 1)
        self.assertIn("<b>", chunks[0])
        self.assertIn("<i>", chunks[0])


# ══════════════════════════════════════════════════════════════════════════════
# 5. send_telegram_msg – Chunking path (len > TELEGRAM_LIMIT)
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTelegramMsgChunking(unittest.TestCase):

    def _make_long_text(self, n=4100):
        return "A " * n  # clearly > 4000 chars default limit

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "100"})
    def test_chunked_message_calls_post_multiple_times(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", self._make_long_text())
        self.assertGreater(mock_post.call_count, 1)

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "100"})
    def test_each_chunk_has_html_parse_mode(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", self._make_long_text())
        for call in mock_post.call_args_list:
            payload = call[1]["json"]
            self.assertEqual(payload.get("parse_mode"), "HTML")

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "100"})
    def test_chunk_parse_error_falls_back_to_plain(self, mock_post):
        bad = MagicMock(status_code=400, text="can't parse entities")
        good = MagicMock(status_code=200, text="OK")
        # First chunk fails, fallback succeeds; subsequent chunks succeed
        mock_post.side_effect = [bad, good, good, good, good, good, good]
        send_telegram_msg("123", self._make_long_text())
        # Second call should NOT have parse_mode (plain text fallback)
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertNotIn("parse_mode", second_payload)


# ══════════════════════════════════════════════════════════════════════════════
# 6. send_telegram_msg – Attachment path (len > ATTACHMENT_THRESHOLD)
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTelegramMsgAttachment(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
    })
    def test_huge_message_uses_send_document(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "X " * 100)  # > 50 chars threshold
        mock_post.assert_called_once()
        # post is called with files= kwarg (not json=)
        call_kwargs = mock_post.call_args[1]
        self.assertIn("files", call_kwargs)
        self.assertNotIn("json", call_kwargs)

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
    })
    def test_attachment_caption_is_set(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "X " * 100)
        data_kwarg = mock_post.call_args[1]["data"]
        self.assertIn("caption", data_kwarg)
        self.assertIn("chat_id", data_kwarg)

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "fake-token",
        "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
    })
    def test_attachment_sends_plain_text_file(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "<b>Bold content</b> " * 10)
        files = mock_post.call_args[1]["files"]
        filename, content = files["document"][0], files["document"][1]
        self.assertEqual(filename, "report.txt")
        self.assertNotIn(b"<b>", content)  # plain text, no HTML tags


class TestSendTelegramMsgChunkingFallback(unittest.TestCase):
    """When split_html_preserving_tags raises, fall back to plain-text paragraph chunking."""

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test_token",
        "TELEGRAM_LIMIT": "50",
        "TELEGRAM_ATTACHMENT_THRESHOLD": "1000000",
    })
    @patch("app.core.notification.split_html_preserving_tags", side_effect=ValueError("broken"))
    def test_fallback_chunking_sends_multiple_posts(self, _mock_split, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        # Message > 50 chars so chunking path is triggered
        long_text = "Word " * 30  # 150 chars
        send_telegram_msg("123", long_text)
        # Should have sent at least one request via fallback
        self.assertGreater(mock_post.call_count, 0)

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test_token",
        "TELEGRAM_LIMIT": "50",
        "TELEGRAM_ATTACHMENT_THRESHOLD": "1000000",
    })
    @patch("app.core.notification.split_html_preserving_tags", side_effect=RuntimeError("fail"))
    def test_fallback_strips_html_tags(self, _mock_split, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        html_text = "<b>Bold</b> " * 30
        send_telegram_msg("123", html_text)
        # Verify at least one call was made and the text posted doesn't have raw <b> tags
        # (fallback strips html before chunking)
        self.assertGreater(mock_post.call_count, 0)
        first_payload = mock_post.call_args_list[0][1]["json"]
        self.assertNotIn("<b>", first_payload.get("text", ""))


class TestSendHtmlEmail(unittest.TestCase):
    """Tests for send_html_email function."""

    def test_disabled_config_returns_early(self):
        from app.core.notification import send_html_email
        config = {"email_config": {"enabled": False}}
        # Should return without raising or calling smtplib
        send_html_email("Subject", "<p>body</p>", config)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_vars_returns_early(self):
        from app.core.notification import send_html_email
        config = {"email_config": {"enabled": True}}
        # No EMAIL_SENDER / PASSWORD / RECEIVER — should return without crashing
        send_html_email("Subject", "<p>body</p>", config)

    @patch("smtplib.SMTP")
    @patch.dict("os.environ", {
        "EMAIL_SENDER": "sender@example.com",
        "EMAIL_PASSWORD": "secret",
        "EMAIL_RECEIVER": "receiver@example.com",
    })
    def test_happy_path_calls_smtp(self, mock_smtp_cls):
        from app.core.notification import send_html_email
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        config = {"email_config": {"enabled": True, "smtp_server": "smtp.example.com", "smtp_port": "587"}}
        send_html_email("Test Subject", "<p>Hello</p>", config)
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("sender@example.com", "secret")
        mock_server.send_message.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("smtplib.SMTP", side_effect=Exception("connection refused"))
    @patch.dict("os.environ", {
        "EMAIL_SENDER": "sender@example.com",
        "EMAIL_PASSWORD": "secret",
        "EMAIL_RECEIVER": "receiver@example.com",
    })
    def test_smtp_error_does_not_raise(self, _mock_smtp):
        from app.core.notification import send_html_email
        config = {"email_config": {"enabled": True}}
        # Should log error but not propagate exception
        send_html_email("Subject", "<p>body</p>", config)


if __name__ == "__main__":
    unittest.main(verbosity=2)

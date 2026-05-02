"""
Layer 3 – Notification Service Tests: app/core/notification.py
================================================================
Không cần Telegram token thật. Network calls được mock.
Covers: sanitize_md_to_tg_html, send_telegram_msg (fallback), send_typing_action.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.core.notification import (
    sanitize_md_to_tg_html,
    send_telegram_msg,
    send_typing_action,
    _strip_html,
)


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
        self.assertIn("</a>", result)
        self.assertIn("Đọc thêm", result)

    def test_unclosed_b_tag_is_balanced(self):
        # Gemini sometimes generates <b>title without closing </b>
        result = sanitize_md_to_tg_html("<b>Breaking news — no close tag")
        self.assertIn("<b>", result)
        self.assertEqual(result.count("<b>"), result.count("</b>"))

    def test_special_chars_in_text_outside_tags_are_escaped(self):
        # < and > in plain text must be escaped; tags must be kept
        text = "HR < 150 bpm và <b>cảnh báo</b>"
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
    def test_sends_without_parse_mode(self, mock_post):
        """send_telegram_msg always sends plain text — no parse_mode."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_telegram_msg("123456", "Hello **Runner**!")

        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertNotIn("parse_mode", payload)
        self.assertIn("Runner", payload["text"])

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_html_input_stripped_before_send(self, mock_post):
        """HTML tags from input are stripped — plain text arrives at Telegram."""
        mock_post.return_value = MagicMock(status_code=200, text="OK")

        send_telegram_msg("123456", "<b>Tin nóng</b> — thị trường biến động")

        payload = mock_post.call_args[1]["json"]
        self.assertNotIn("<b>", payload["text"])
        self.assertNotIn("</b>", payload["text"])
        self.assertIn("Tin nóng", payload["text"])

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_400_on_plain_msg_not_retried(self, mock_post):
        """Plain text messages are never retried on 400 (no HTML parse_mode to fall back from)."""
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")

        send_telegram_msg("123456", "Some text")

        self.assertEqual(mock_post.call_count, 1)

    @patch("app.core.notification.requests.post")
    def test_no_token_skips_request(self, mock_post):
        """Missing TELEGRAM_BOT_TOKEN → should not call requests.post."""
        with patch.dict("os.environ", {}, clear=True):
            # Ensure TELEGRAM_BOT_TOKEN is absent
            import os

            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            send_telegram_msg("123456", "test")
        mock_post.assert_not_called()

    @patch(
        "app.core.notification.requests.post", side_effect=Exception("Network error")
    )
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
# 4. _split_plain and _split_html_naive – Pure chunking logic
# ══════════════════════════════════════════════════════════════════════════════
class TestSplitPlain(unittest.TestCase):

    def _split(self, text, limit):
        from app.core.notification import _split_plain
        return _split_plain(text, limit)

    def test_short_text_returns_single_chunk(self):
        chunks = self._split("Hello world", 100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Hello world")

    def test_plain_text_splits_at_word_boundary(self):
        text = "word " * 30  # 150 chars
        chunks = self._split(text.strip(), 50)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 50)

    def test_all_content_preserved(self):
        text = "A" * 8000
        chunks = self._split(text, 4000)
        self.assertEqual("".join(chunks), text)

    def test_message_at_exact_limit_is_one_chunk(self):
        text = "A" * 100
        chunks = self._split(text, 100)
        self.assertEqual(len(chunks), 1)

    def test_single_token_larger_than_limit(self):
        text = "A" * 200
        chunks = self._split(text, 100)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)


class TestSplitHtmlNaive(unittest.TestCase):

    def _split(self, text, limit):
        from app.core.notification import _split_html_naive
        return _split_html_naive(text, limit)

    def test_short_html_single_chunk(self):
        text = "<b>Hello</b> world"
        chunks = self._split(text, 100)
        self.assertEqual(len(chunks), 1)

    def test_splits_at_paragraph_boundary(self):
        para = "<b>Para</b> " + "X" * 50 + "\n\n"
        text = para * 10  # ~640 chars, split at limit=200
        chunks = self._split(text, 200)
        self.assertGreater(len(chunks), 1)

    def test_all_content_preserved(self):
        import re
        para = "Para text " * 10 + "\n\n"
        text = para * 5
        chunks = self._split(text, 100)
        combined_flat = re.sub(r'\s+', '', "".join(chunks))
        original_flat = re.sub(r'\s+', '', text)
        self.assertEqual(combined_flat, original_flat)

    def test_oversized_para_stripped_and_word_split(self):
        big_para = "<b>Big</b>: " + "W " * 300  # ~600 chars > limit
        chunks = self._split(big_para, 100)
        for c in chunks:
            self.assertLessEqual(len(c), 100)

    def test_empty_returns_empty_or_single_empty(self):
        chunks = self._split("", 100)
        self.assertEqual("".join(chunks).strip(), "")


# ══════════════════════════════════════════════════════════════════════════════
# 5. send_telegram_msg – Chunking path (len > TELEGRAM_LIMIT)
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTelegramMsgChunking(unittest.TestCase):

    def _make_long_text(self, n=4100):
        return "A " * n  # clearly > 4000 chars default limit

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "100"}
    )
    def test_chunked_message_calls_post_multiple_times(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", self._make_long_text())
        self.assertGreater(mock_post.call_count, 1)

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "100"}
    )
    def test_plain_msg_has_no_parse_mode(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", self._make_long_text())
        for call in mock_post.call_args_list:
            payload = call[1]["json"]
            self.assertNotIn("parse_mode", payload)


# ══════════════════════════════════════════════════════════════════════════════
# 6. send_telegram_msg – Attachment path (len > ATTACHMENT_THRESHOLD)
# ══════════════════════════════════════════════════════════════════════════════
class TestSendTelegramMsgAttachment(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
        },
    )
    def test_huge_message_uses_send_document(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "X " * 100)  # > 50 chars threshold
        mock_post.assert_called_once()
        # post is called with files= kwarg (not json=)
        call_kwargs = mock_post.call_args[1]
        self.assertIn("files", call_kwargs)
        self.assertNotIn("json", call_kwargs)

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
        },
    )
    def test_attachment_caption_is_set(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "X " * 100)
        data_kwarg = mock_post.call_args[1]["data"]
        self.assertIn("caption", data_kwarg)
        self.assertIn("chat_id", data_kwarg)

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {
            "TELEGRAM_BOT_TOKEN": "fake-token",
            "TELEGRAM_ATTACHMENT_THRESHOLD": "50",
        },
    )
    def test_attachment_sends_plain_text_file(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_msg("123", "<b>Bold content</b> " * 10)
        files = mock_post.call_args[1]["files"]
        filename, content = files["document"][0], files["document"][1]
        self.assertEqual(filename, "report.txt")
        self.assertNotIn(b"<b>", content)  # plain text, no HTML tags


class TestSendTelegramHtml(unittest.TestCase):
    """send_telegram_html sends with parse_mode=HTML; 400 falls back to plain."""

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_LIMIT": "100"},
    )
    def test_html_msg_uses_parse_mode_html(self, mock_post):
        from app.core.notification import send_telegram_html

        mock_post.return_value = MagicMock(status_code=200, text="OK")
        send_telegram_html("123", "<b>Hello</b> " * 30)
        for call in mock_post.call_args_list:
            self.assertEqual(call[1]["json"].get("parse_mode"), "HTML")

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_LIMIT": "1000"},
    )
    def test_html_400_falls_back_to_plain(self, mock_post):
        from app.core.notification import send_telegram_html

        bad = MagicMock(status_code=400, text="can't parse")
        good = MagicMock(status_code=200, text="OK")
        mock_post.side_effect = [bad, good]
        send_telegram_html("123", "<b>Short</b>")
        second = mock_post.call_args_list[1][1]["json"]
        self.assertNotIn("parse_mode", second)


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
    @patch.dict(
        "os.environ",
        {
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_PASSWORD": "secret",
            "EMAIL_RECEIVER": "receiver@example.com",
        },
    )
    def test_happy_path_calls_smtp(self, mock_smtp_cls):
        from app.core.notification import send_html_email

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        config = {
            "email_config": {
                "enabled": True,
                "smtp_server": "smtp.example.com",
                "smtp_port": "587",
            }
        }
        send_html_email("Test Subject", "<p>Hello</p>", config)
        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("sender@example.com", "secret")
        mock_server.send_message.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("smtplib.SMTP", side_effect=Exception("connection refused"))
    @patch.dict(
        "os.environ",
        {
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_PASSWORD": "secret",
            "EMAIL_RECEIVER": "receiver@example.com",
        },
    )
    def test_smtp_error_does_not_raise(self, _mock_smtp):
        from app.core.notification import send_html_email

        config = {"email_config": {"enabled": True}}
        # Should log error but not propagate exception
        send_html_email("Subject", "<p>body</p>", config)


if __name__ == "__main__":
    unittest.main(verbosity=2)

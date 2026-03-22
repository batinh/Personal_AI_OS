"""
Layer 3 – Notification Service Tests: app/core/notification.py
================================================================
Không cần Telegram token thật. Network calls được mock.
Covers: sanitize_md_to_tg_html, send_telegram_msg (fallback), send_typing_action.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core.notification import sanitize_md_to_tg_html, send_telegram_msg, send_typing_action


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

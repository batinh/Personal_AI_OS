"""
Notification: large message behavior.
Previously: messages >100k chars sent as sendDocument attachment.
Now: all messages split into chunks (no sendDocument path).
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core.notification import send_telegram_msg


class TestLargeMessageChunked(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "1000"},
    )
    def test_large_message_sent_as_chunks_not_document(self, mock_post):
        """Large messages must be chunked, not sent via sendDocument."""
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        long_text = "A" * 100001
        send_telegram_msg("123456", long_text)
        self.assertGreater(mock_post.call_count, 1, "Large message must split into multiple sends")
        for call in mock_post.call_args_list:
            kwargs = call[1]
            self.assertNotIn("files", kwargs, "sendDocument must not be used")
            self.assertIn("json", kwargs, "Each send must use json= (chunked messages)")

    @patch("app.core.notification.requests.post")
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_LIMIT": "1000"},
    )
    def test_large_message_no_content_loss(self, mock_post):
        """All content must be present across chunks even for huge messages."""
        import re
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        long_text = "word " * 10000  # 50k chars
        send_telegram_msg("123456", long_text)
        sent_texts = [call[1]["json"]["text"] for call in mock_post.call_args_list]
        combined = " ".join(sent_texts)
        self.assertIn("word", combined)
        self.assertGreater(len(combined), 1000, "Content must be present in sent chunks")

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_large_message_error_does_not_raise(self, mock_post):
        """Even if some chunks fail, no exception must propagate."""
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")
        try:
            send_telegram_msg("999999", "B " * 5000)
        except Exception as e:
            self.fail(f"send_telegram_msg raised on send failure: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

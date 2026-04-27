import unittest
from unittest.mock import MagicMock, patch

from app.core.notification import send_telegram_msg


class TestSendDocumentIntegration(unittest.TestCase):

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_send_document_for_large_message(self, mock_post):
        """Very large message should be sent via sendDocument with files and data args."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Build a message larger than ATTACHMENT_THRESHOLD (100000)
        long_text = "A" * 100001
        send_telegram_msg("123456", long_text)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        # requests.post called with files=..., data=...
        kwargs = call_args[1]
        self.assertIn("files", kwargs)
        self.assertIn("data", kwargs)
        self.assertEqual(kwargs["data"]["chat_id"], "123456")
        self.assertIn("document", kwargs["files"])  # ('report.txt', b'...') tuple

    @patch("app.core.notification.requests.post")
    @patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"})
    def test_send_document_handles_non_200(self, mock_post):
        """Non-200 response when uploading a document should be handled (logged) and not raise."""
        bad_response = MagicMock()
        bad_response.status_code = 500
        bad_response.text = "Server Error"
        mock_post.return_value = bad_response

        try:
            long_text = "B" * 100001
            send_telegram_msg("999999", long_text)
        except Exception as e:
            self.fail(
                f"send_telegram_msg raised an exception on document upload failure: {e}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

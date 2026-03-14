"""
Tests for get_run_full_details tool: output includes stream_file_path when present.
"""
import unittest
from unittest.mock import patch

from app.agents.coach.tools import get_run_full_details


class TestGetRunFullDetails(unittest.TestCase):
    """Test get_run_full_details formats output and includes stream file path."""

    def test_returns_not_found_message_when_no_raw(self):
        with patch("app.agents.coach.tools.get_run_activity_raw", return_value=None):
            out = get_run_full_details("999")
        self.assertIn("Không tìm thấy dữ liệu đầy đủ", out)
        self.assertIn("999", out)

    def test_includes_stream_file_path_when_present(self):
        with patch("app.agents.coach.tools.get_run_activity_raw", return_value={
            "activity_name": "Morning Run",
            "full_meta": {"distance": 10000, "average_heartrate": 150},
            "fetched_at": "2026-03-14 10:00:00",
            "stream_file_path": "streams/user1/act1.json",
            "stream_csv": "",
        }):
            out = get_run_full_details("act1")
        self.assertIn("Morning Run", out)
        self.assertIn("data/streams/user1/act1.json", out)
        self.assertIn("phân tích chi tiết", out)

    def test_no_stream_path_line_when_path_empty(self):
        with patch("app.agents.coach.tools.get_run_activity_raw", return_value={
            "activity_name": "Run",
            "full_meta": {},
            "fetched_at": "2026-03-14 10:00:00",
            "stream_file_path": "",
            "stream_csv": "",
        }):
            out = get_run_full_details("act2")
        self.assertIn("Run", out)
        self.assertNotIn("streams/", out)


if __name__ == "__main__":
    unittest.main()

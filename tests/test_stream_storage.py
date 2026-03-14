"""
Tests for stream file storage: save/load raw Strava streams under data/streams.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.stream_storage import (
    DATA_DIR,
    get_stream_arrays,
    get_stream_file_path,
    load_activity_stream_from_file,
    save_activity_stream_to_file,
)


class TestGetStreamFilePath(unittest.TestCase):
    """Test get_stream_file_path returns correct relative path."""

    def test_returns_relative_path(self):
        self.assertEqual(
            get_stream_file_path("user123", "act456"),
            "streams/user123/act456.json",
        )

    def test_handles_string_ids(self):
        self.assertEqual(
            get_stream_file_path("987654321", "1234567890"),
            "streams/987654321/1234567890.json",
        )


class TestSaveAndLoadActivityStream(unittest.TestCase):
    """Test save_activity_stream_to_file and load_activity_stream_from_file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_save_returns_relative_path(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            path = save_activity_stream_to_file(
                "u1",
                "a1",
                {"time": {"data": [0, 1, 2]}, "heartrate": {"data": [120, 121, 122]}},
            )
        self.assertEqual(path, "streams/u1/a1.json")

    def test_save_creates_file_with_expected_structure(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            save_activity_stream_to_file(
                "u1",
                "a1",
                {"time": {"data": [0, 1]}, "heartrate": {"data": [100, 101]}},
            )
        full = Path(self.tmp) / "streams" / "u1" / "a1.json"
        self.assertTrue(full.is_file())
        with open(full) as f:
            data = json.load(f)
        self.assertEqual(data["activity_id"], "a1")
        self.assertEqual(data["user_id"], "u1")
        self.assertIn("fetched_at", data)
        self.assertEqual(data["streams"]["time"]["data"], [0, 1])
        self.assertEqual(data["streams"]["heartrate"]["data"], [100, 101])

    def test_save_empty_dict_returns_none(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            path = save_activity_stream_to_file("u1", "a1", {})
        self.assertIsNone(path)

    def test_save_none_like_returns_none(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            path = save_activity_stream_to_file("u1", "a1", None)
        self.assertIsNone(path)

    def test_load_returns_payload(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            save_activity_stream_to_file(
                "u2", "a2", {"time": {"data": [0, 1, 2]}}
            )
            payload = load_activity_stream_from_file("streams/u2/a2.json")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["activity_id"], "a2")
        self.assertEqual(payload["streams"]["time"]["data"], [0, 1, 2])

    def test_load_missing_file_returns_none(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            payload = load_activity_stream_from_file("streams/nonexistent/id.json")
        self.assertIsNone(payload)

    def test_load_empty_path_returns_none(self):
        with patch("app.services.stream_storage.DATA_DIR", Path(self.tmp)):
            self.assertIsNone(load_activity_stream_from_file(""))
            self.assertIsNone(load_activity_stream_from_file("   "))


class TestGetStreamArrays(unittest.TestCase):
    """Test get_stream_arrays extracts flat arrays from payload."""

    def test_returns_flat_arrays(self):
        payload = {
            "streams": {
                "time": {"data": [0, 1, 2]},
                "heartrate": {"data": [120, 121, 122]},
            }
        }
        out = get_stream_arrays(payload)
        self.assertEqual(out["time"], [0, 1, 2])
        self.assertEqual(out["heartrate"], [120, 121, 122])

    def test_empty_streams_returns_none(self):
        self.assertIsNone(get_stream_arrays({}))
        self.assertIsNone(get_stream_arrays({"streams": {}}))
        self.assertIsNone(get_stream_arrays(None))

    def test_missing_streams_key_returns_none(self):
        self.assertIsNone(get_stream_arrays({"activity_id": "1"}))


def _rmtree(path):
    import shutil
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

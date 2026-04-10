"""
test_strava_client.py — Production tests for StravaClient.
===========================================================
Covers:
  - Token refresh and caching with expiry
  - API error handling (401, 403, 500, network errors)
  - Activity type filtering (only runs)
  - Streams fetching (success, empty, error)
  - Activity description update
  - get_recent_activities and get_athlete_stats
"""
import time
import unittest
from unittest.mock import patch, MagicMock

from app.agents.coach.strava_client import StravaClient


def _mock_token_response(token="fake-access-token", expires_at=None):
    """Build a mock response from Strava's token refresh endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": token,
        "expires_at": expires_at or (time.time() + 21600),  # 6 hours
        "refresh_token": "new-refresh-token",
    }
    return resp


class TestTokenCaching(unittest.TestCase):
    """Token should be cached in memory until near expiry."""

    @patch.dict("os.environ", {
        "STRAVA_CLIENT_ID": "123",
        "STRAVA_CLIENT_SECRET": "secret",
        "STRAVA_REFRESH_TOKEN": "refresh",
    })
    @patch("app.agents.coach.strava_client.requests.post")
    def test_first_call_refreshes_token(self, mock_post):
        mock_post.return_value = _mock_token_response("token-A")
        client = StravaClient()

        token = client.get_access_token()
        self.assertEqual(token, "token-A")
        mock_post.assert_called_once()

    @patch.dict("os.environ", {
        "STRAVA_CLIENT_ID": "123",
        "STRAVA_CLIENT_SECRET": "secret",
        "STRAVA_REFRESH_TOKEN": "refresh",
    })
    @patch("app.agents.coach.strava_client.requests.post")
    def test_second_call_uses_cache(self, mock_post):
        mock_post.return_value = _mock_token_response("token-A", time.time() + 7200)
        client = StravaClient()

        token1 = client.get_access_token()
        token2 = client.get_access_token()

        self.assertEqual(token1, "token-A")
        self.assertEqual(token2, "token-A")
        # Only one HTTP call — second was served from cache
        self.assertEqual(mock_post.call_count, 1)

    @patch.dict("os.environ", {
        "STRAVA_CLIENT_ID": "123",
        "STRAVA_CLIENT_SECRET": "secret",
        "STRAVA_REFRESH_TOKEN": "refresh",
    })
    @patch("app.agents.coach.strava_client.requests.post")
    def test_expired_token_triggers_refresh(self, mock_post):
        """Token within 60s buffer of expiry should trigger a re-refresh."""
        # First call: token expires in 30s (within 60s buffer → already "expired")
        mock_post.return_value = _mock_token_response("token-A", time.time() + 30)
        client = StravaClient()

        client.get_access_token()
        # Second call should re-refresh since 30s < 60s buffer
        mock_post.return_value = _mock_token_response("token-B", time.time() + 7200)
        token2 = client.get_access_token()

        self.assertEqual(token2, "token-B")
        self.assertEqual(mock_post.call_count, 2)

    @patch.dict("os.environ", {
        "STRAVA_CLIENT_ID": "123",
        "STRAVA_CLIENT_SECRET": "secret",
        "STRAVA_REFRESH_TOKEN": "refresh",
    })
    @patch("app.agents.coach.strava_client.requests.post")
    def test_token_refresh_failure_returns_none(self, mock_post):
        mock_post.side_effect = Exception("Network error")
        client = StravaClient()

        token = client.get_access_token()
        self.assertIsNone(token)


class TestGetActivityData(unittest.TestCase):
    """get_activity_data should only process runs and handle errors gracefully."""

    def _make_client(self):
        client = StravaClient()
        client._cached_token = "valid-token"
        client._token_expires_at = time.time() + 7200
        return client

    @patch("app.agents.coach.strava_client.requests.get")
    def test_non_run_activity_returns_none(self, mock_get):
        """Cycling, swimming etc. should be skipped."""
        activity_resp = MagicMock()
        activity_resp.status_code = 200
        activity_resp.json.return_value = {
            "name": "Morning Ride",
            "type": "Ride",
            "distance": 30000,
        }
        mock_get.return_value = activity_resp
        client = self._make_client()

        name, csv, meta, raw = client.get_activity_data("123")
        self.assertIsNone(name)
        self.assertIsNone(csv)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_api_error_returns_none_tuple(self, mock_get):
        """500/401/403 from Strava should return (None, None, None, None)."""
        error_resp = MagicMock()
        error_resp.status_code = 401
        error_resp.text = "Unauthorized"
        mock_get.return_value = error_resp
        client = self._make_client()

        name, csv, meta, raw = client.get_activity_data("123")
        self.assertIsNone(name)

    def test_no_token_returns_none_tuple(self):
        """If token refresh fails, all data methods return None."""
        client = StravaClient()
        client._cached_token = None
        client._token_expires_at = 0

        with patch("app.agents.coach.strava_client.requests.post", side_effect=Exception("down")):
            name, csv, meta, raw = client.get_activity_data("123")

        self.assertIsNone(name)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_rate_limited_returns_none_tuple(self, mock_get):
        """429 rate limit on activity fetch should return (None, None, None, None)."""
        resp = MagicMock()
        resp.status_code = 429
        mock_get.return_value = resp

        name, csv, meta, raw = self._make_client().get_activity_data("123")
        self.assertIsNone(name)
        self.assertIsNone(csv)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_502_transient_returns_none_tuple(self, mock_get):
        """502 bad gateway should return (None, None, None, None)."""
        resp = MagicMock()
        resp.status_code = 502
        mock_get.return_value = resp

        name, csv, meta, raw = self._make_client().get_activity_data("123")
        self.assertIsNone(name)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_timeout_returns_none_tuple(self, mock_get):
        """requests.exceptions.Timeout should be caught and return (None, None, None, None)."""
        import requests as req_mod
        mock_get.side_effect = req_mod.exceptions.Timeout("timed out")

        name, csv, meta, raw = self._make_client().get_activity_data("123")
        self.assertIsNone(name)
        self.assertIsNone(csv)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_no_streams_returns_meta_only(self, mock_get):
        """If streams endpoint fails, should return activity name + meta without csv."""
        activity_resp = MagicMock()
        activity_resp.status_code = 200
        activity_resp.json.return_value = {
            "name": "Easy Run",
            "type": "Run",
            "distance": 5000,
            "moving_time": 1800,
            "average_heartrate": 130,
            "max_heartrate": 150,
            "start_date_local": "2025-01-15T07:00:00",
            "suffer_score": 40,
            "device_name": "Garmin",
            "splits_metric": [],
            "laps": [],
            "best_efforts": [],
        }
        streams_resp = MagicMock()
        streams_resp.status_code = 404

        mock_get.side_effect = [activity_resp, streams_resp]
        client = self._make_client()

        name, csv, meta, raw = client.get_activity_data("123")
        self.assertEqual(name, "Easy Run")
        self.assertIsNone(csv)
        self.assertIsNotNone(meta)
        self.assertIsNone(raw)


class TestGetActivityStreamsRaw(unittest.TestCase):
    """get_activity_streams_raw error scenarios."""

    def _make_client(self):
        client = StravaClient()
        client._cached_token = "valid-token"
        client._token_expires_at = time.time() + 7200
        return client

    @patch("app.agents.coach.strava_client.requests.get")
    def test_success_returns_json(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"time": {"data": [0, 1, 2]}}
        mock_get.return_value = resp

        result = self._make_client().get_activity_streams_raw("123")
        self.assertEqual(result, {"time": {"data": [0, 1, 2]}})

    @patch("app.agents.coach.strava_client.requests.get")
    def test_error_status_returns_none(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp

        result = self._make_client().get_activity_streams_raw("123")
        self.assertIsNone(result)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_network_error_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")

        result = self._make_client().get_activity_streams_raw("123")
        self.assertIsNone(result)

    def test_no_token_returns_none(self):
        client = StravaClient()
        client._cached_token = None
        client._token_expires_at = 0

        with patch("app.agents.coach.strava_client.requests.post", side_effect=Exception("err")):
            result = client.get_activity_streams_raw("123")
        self.assertIsNone(result)


class TestUpdateActivityDescription(unittest.TestCase):
    """update_activity_description should handle success and failure."""

    def _make_client(self):
        client = StravaClient()
        client._cached_token = "valid-token"
        client._token_expires_at = time.time() + 7200
        return client

    @patch("app.agents.coach.strava_client.requests.put")
    def test_success_returns_true(self, mock_put):
        resp = MagicMock()
        resp.status_code = 200
        mock_put.return_value = resp

        result = self._make_client().update_activity_description("123", "Great run!")
        self.assertTrue(result)

    @patch("app.agents.coach.strava_client.requests.put")
    def test_failure_returns_false(self, mock_put):
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Forbidden"
        mock_put.return_value = resp

        result = self._make_client().update_activity_description("123", "text")
        self.assertFalse(result)

    @patch("app.agents.coach.strava_client.requests.put")
    def test_network_error_returns_false(self, mock_put):
        mock_put.side_effect = ConnectionError("timeout")

        result = self._make_client().update_activity_description("123", "text")
        self.assertFalse(result)


class TestGetRecentActivities(unittest.TestCase):
    """get_recent_activities error resilience."""

    def _make_client(self):
        client = StravaClient()
        client._cached_token = "valid-token"
        client._token_expires_at = time.time() + 7200
        return client

    @patch("app.agents.coach.strava_client.requests.get")
    def test_success_returns_list(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": 1}, {"id": 2}]
        mock_get.return_value = resp

        result = self._make_client().get_recent_activities(limit=5)
        self.assertEqual(len(result), 2)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_api_error_returns_empty_list(self, mock_get):
        resp = MagicMock()
        resp.status_code = 429
        mock_get.return_value = resp

        result = self._make_client().get_recent_activities()
        self.assertEqual(result, [])

    @patch("app.agents.coach.strava_client.requests.get")
    def test_network_error_returns_empty_list(self, mock_get):
        mock_get.side_effect = Exception("DNS failure")

        result = self._make_client().get_recent_activities()
        self.assertEqual(result, [])


class TestGetAthleteStats(unittest.TestCase):
    """get_athlete_stats with valid/error responses."""

    def _make_client(self):
        client = StravaClient()
        client._cached_token = "valid-token"
        client._token_expires_at = time.time() + 7200
        return client

    @patch("app.agents.coach.strava_client.requests.get")
    def test_success_returns_km_distances(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "recent_run_totals": {"distance": 50000},   # 50km
            "ytd_run_totals": {"distance": 1200000},    # 1200km
            "all_run_totals": {"distance": 5000000},    # 5000km
        }
        mock_get.return_value = resp

        result = self._make_client().get_athlete_stats("12345")
        self.assertAlmostEqual(result["recent_run_totals"], 50.0)
        self.assertAlmostEqual(result["ytd_run_totals"], 1200.0)

    @patch("app.agents.coach.strava_client.requests.get")
    def test_error_returns_none(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp

        result = self._make_client().get_athlete_stats("12345")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

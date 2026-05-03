"""Tests for gear tracking functionality (Phase 4)."""

from unittest.mock import MagicMock, patch

import pytest


class TestFetchGearStats:
    """Test GarminClient.fetch_gear_stats() for gear mileage tracking."""

    def test_fetch_gear_stats_returns_list(self):
        """GarminClient.fetch_gear_stats returns list of gear dicts."""
        from app.agents.coach.garmin_client import GarminClient

        client = GarminClient()
        mock_garmin = MagicMock()

        # Mock gear list from Garmin API
        mock_garmin.get_gear.return_value = [
            {"gearPk": "gear1", "displayName": "Asics GT-2000"},
            {"gearPk": "gear2", "displayName": "Brooks Ghost"},
        ]

        # Mock gear stats
        mock_garmin.get_gear_stats.side_effect = [
            {"totalDistance": 612000},  # 612km
            {"totalDistance": 420000},  # 420km
        ]

        with patch.object(client, "_get_client", return_value=mock_garmin):
            with patch(
                "app.agents.coach.garmin_client._is_circuit_open", return_value=False
            ):
                result = client.fetch_gear_stats("user1")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "Asics GT-2000"
        assert result[0]["total_km"] == 612.0
        assert result[1]["name"] == "Brooks Ghost"
        assert result[1]["total_km"] == 420.0

    def test_fetch_gear_stats_empty_on_no_gear(self):
        """Returns empty list when Garmin has no gear data."""
        from app.agents.coach.garmin_client import GarminClient

        client = GarminClient()
        mock_garmin = MagicMock()
        mock_garmin.get_gear.return_value = []

        with patch.object(client, "_get_client", return_value=mock_garmin):
            with patch(
                "app.agents.coach.garmin_client._is_circuit_open", return_value=False
            ):
                result = client.fetch_gear_stats("user1")

        assert result == []

    def test_fetch_gear_stats_circuit_open_returns_empty(self):
        """Returns empty list when circuit breaker is open."""
        from app.agents.coach.garmin_client import GarminClient

        client = GarminClient()

        with patch(
            "app.agents.coach.garmin_client._is_circuit_open", return_value=True
        ):
            result = client.fetch_gear_stats("user1")

        assert result == []

    def test_fetch_gear_stats_handles_missing_gear_id(self):
        """Handles gear items without gearPk gracefully."""
        from app.agents.coach.garmin_client import GarminClient

        client = GarminClient()
        mock_garmin = MagicMock()

        # Gear without gearPk but with uuid
        mock_garmin.get_gear.return_value = [
            {"uuid": "gear-uuid-1", "displayName": "Nike React"},
            {"displayName": "Unknown Shoe"},  # No ID at all
        ]

        mock_garmin.get_gear_stats.return_value = {"totalDistance": 300000}

        with patch.object(client, "_get_client", return_value=mock_garmin):
            with patch(
                "app.agents.coach.garmin_client._is_circuit_open", return_value=False
            ):
                result = client.fetch_gear_stats("user1")

        # Only gear with valid ID should be returned
        assert len(result) == 1
        assert result[0]["name"] == "Nike React"

    def test_fetch_gear_stats_handles_api_errors(self):
        """Handles Garmin API errors gracefully."""
        from app.agents.coach.garmin_client import GarminClient

        client = GarminClient()
        mock_garmin = MagicMock()
        mock_garmin.get_gear.return_value = [
            {"gearPk": "gear1", "displayName": "Asics GT-2000"}
        ]

        # Simulate API error when fetching stats
        mock_garmin.get_gear_stats.side_effect = RuntimeError("API error")

        with patch.object(client, "_get_client", return_value=mock_garmin):
            with patch(
                "app.agents.coach.garmin_client._is_circuit_open", return_value=False
            ):
                result = client.fetch_gear_stats("user1")

        # Should still return gear with 0 km
        assert len(result) == 1
        assert result[0]["total_km"] == 0.0


class TestGearCheckScheduler:
    """Test task_gear_check scheduler job."""

    def test_gear_check_sends_warning_at_threshold(self):
        """Gear mileage at warn threshold sends warning alert."""
        with patch(
            "app.services.scheduler.get_primary_user_id", return_value="123"
        ):
            with patch("app.services.scheduler.load_config") as mock_config:
                with patch(
                    "app.services.scheduler.get_garmin_client"
                ) as mock_garmin_factory:
                    with patch(
                        "app.services.scheduler.send_telegram_msg"
                    ) as mock_send:
                        mock_config.return_value = {
                            "garmin": {
                                "gear_warn_km": 550,
                                "gear_critical_km": 650,
                            }
                        }

                        mock_garmin = MagicMock()
                        mock_garmin.fetch_gear_stats.return_value = [
                            {"name": "Asics GT-2000", "total_km": 560, "gear_id": "g1"}
                        ]
                        mock_garmin_factory.return_value = mock_garmin

                        from app.services.scheduler import task_gear_check

                        task_gear_check()

                        # Should send warning message
                        mock_send.assert_called()
                        call_args = mock_send.call_args[0]
                        assert "⚠️" in call_args[1]  # Warning emoji
                        assert "560" in call_args[1]  # Mileage

    def test_gear_check_sends_critical_at_threshold(self):
        """Gear mileage at critical threshold sends urgent alert."""
        with patch(
            "app.services.scheduler.get_primary_user_id", return_value="123"
        ):
            with patch("app.services.scheduler.load_config") as mock_config:
                with patch(
                    "app.services.scheduler.get_garmin_client"
                ) as mock_garmin_factory:
                    with patch(
                        "app.services.scheduler.send_telegram_msg"
                    ) as mock_send:
                        mock_config.return_value = {
                            "garmin": {
                                "gear_warn_km": 550,
                                "gear_critical_km": 650,
                            }
                        }

                        mock_garmin = MagicMock()
                        mock_garmin.fetch_gear_stats.return_value = [
                            {"name": "Nike React", "total_km": 670, "gear_id": "g2"}
                        ]
                        mock_garmin_factory.return_value = mock_garmin

                        from app.services.scheduler import task_gear_check

                        task_gear_check()

                        # Should send critical message
                        mock_send.assert_called()
                        call_args = mock_send.call_args[0]
                        assert "🚨" in call_args[1]  # Critical emoji
                        assert "670" in call_args[1]  # Mileage

    def test_gear_check_no_alert_below_threshold(self):
        """Gear mileage below warn threshold sends no alert."""
        with patch(
            "app.services.scheduler.get_primary_user_id", return_value="123"
        ):
            with patch("app.services.scheduler.load_config") as mock_config:
                with patch(
                    "app.services.scheduler.get_garmin_client"
                ) as mock_garmin_factory:
                    with patch(
                        "app.services.scheduler.send_telegram_msg"
                    ) as mock_send:
                        mock_config.return_value = {
                            "garmin": {
                                "gear_warn_km": 550,
                                "gear_critical_km": 650,
                            }
                        }

                        mock_garmin = MagicMock()
                        mock_garmin.fetch_gear_stats.return_value = [
                            {"name": "Asics GT-2000", "total_km": 400, "gear_id": "g1"}
                        ]
                        mock_garmin_factory.return_value = mock_garmin

                        from app.services.scheduler import task_gear_check

                        task_gear_check()

                        # Should not send any message
                        mock_send.assert_not_called()

    def test_gear_check_handles_empty_gear_list(self):
        """Handles case when no gear data is available."""
        with patch(
            "app.services.scheduler.get_primary_user_id", return_value="123"
        ):
            with patch("app.services.scheduler.load_config") as mock_config:
                with patch(
                    "app.services.scheduler.get_garmin_client"
                ) as mock_garmin_factory:
                    with patch(
                        "app.services.scheduler.send_telegram_msg"
                    ) as mock_send:
                        mock_config.return_value = {
                            "garmin": {
                                "gear_warn_km": 550,
                                "gear_critical_km": 650,
                            }
                        }

                        mock_garmin = MagicMock()
                        mock_garmin.fetch_gear_stats.return_value = []
                        mock_garmin_factory.return_value = mock_garmin

                        from app.services.scheduler import task_gear_check

                        task_gear_check()

                        # Should not send any message
                        mock_send.assert_not_called()

    def test_gear_check_handles_no_user_id(self):
        """Handles case when primary user ID is not set."""
        with patch(
            "app.services.scheduler.get_primary_user_id", return_value=None
        ):
            with patch(
                "app.services.scheduler.send_telegram_msg"
            ) as mock_send:
                from app.services.scheduler import task_gear_check

                task_gear_check()

                # Should not attempt to send message
                mock_send.assert_not_called()

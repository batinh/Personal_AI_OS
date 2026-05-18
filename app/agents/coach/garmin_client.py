import concurrent.futures
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from app.core.logging_conf import get_module_logger
from app.core.database import upsert_garmin_daily_metrics, get_garmin_daily_metrics
from app.core.secrets import decrypt_garmin_credentials

logger = get_module_logger("garmin_client")

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TOKEN_FILE = _BASE_DIR / "data" / "garmin_tokens.json"
_CIRCUIT_STATE_FILE = _BASE_DIR / "data" / "garmin_circuit.json"

_OAUTH_TOKEN_FILE = _BASE_DIR / "data" / "garmin_oauth_token.json"

_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_HOURS = 24


def save_oauth_token(token_json: str) -> None:
    """Persist OAuth token exported from garminconnect (client.dumps())."""
    _OAUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OAUTH_TOKEN_FILE.write_text(token_json)
    logger.info("[GARMIN] OAuth token saved")


def load_oauth_token() -> Optional[str]:
    if _OAUTH_TOKEN_FILE.exists():
        return _OAUTH_TOKEN_FILE.read_text().strip()
    return None


def has_oauth_token() -> bool:
    return _OAUTH_TOKEN_FILE.exists() and _OAUTH_TOKEN_FILE.stat().st_size > 10


def _load_circuit_state() -> dict:
    if _CIRCUIT_STATE_FILE.exists():
        try:
            return json.loads(_CIRCUIT_STATE_FILE.read_text())
        except Exception:
            pass
    return {"failures": 0, "open_until": None}


def _save_circuit_state(state: dict) -> None:
    _CIRCUIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CIRCUIT_STATE_FILE.write_text(json.dumps(state, default=str))


def _is_circuit_open() -> bool:
    state = _load_circuit_state()
    if state.get("open_until"):
        try:
            open_until = datetime.fromisoformat(state["open_until"])
            if datetime.utcnow() < open_until:
                return True
            # cooldown expired — reset
            _save_circuit_state({"failures": 0, "open_until": None})
        except ValueError:
            pass
    return False


def _record_failure() -> bool:
    """Record a failure. Returns True if circuit just opened."""
    state = _load_circuit_state()
    state["failures"] = state.get("failures", 0) + 1
    if state["failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
        open_until = datetime.utcnow() + timedelta(hours=_CIRCUIT_COOLDOWN_HOURS)
        state["open_until"] = open_until.isoformat()
        _save_circuit_state(state)
        logger.warning(f"[GARMIN] Circuit breaker OPEN until {open_until.isoformat()}")
        return True
    _save_circuit_state(state)
    return False


def _reset_circuit() -> None:
    _save_circuit_state({"failures": 0, "open_until": None})


class GarminClient:
    """
    Thin wrapper around garminconnect for authenticated Garmin Connect access.
    Handles token persistence, MFA, circuit breaker, and 3-day fallback.
    """

    def __init__(self) -> None:
        self._client = None
        # Priority: encrypted secrets file → env vars
        creds = decrypt_garmin_credentials()
        if creds:
            self._email, self._password = creds
        else:
            self._email = os.environ.get("GARMIN_EMAIL", "")
            self._password = os.environ.get("GARMIN_PASSWORD", "")

    def _get_client(self):
        """Lazy-initialize the garminconnect client, reusing saved tokens."""
        if self._client is not None:
            return self._client

        try:
            from garminconnect import Garmin
        except ImportError:
            logger.error(
                "[GARMIN] garminconnect package not installed. Run: pip install garminconnect"
            )
            raise

        # Priority 1: OAuth token (works from any IP — no SSO needed)
        if has_oauth_token():
            token_str = load_oauth_token()
            try:
                client = Garmin(self._email or "", self._password or "")
                client.login(tokenstore=token_str)
                self._client = client
                logger.info("[GARMIN] Session restored from OAuth token (no SSO)")
                return client
            except Exception as e:
                logger.warning(f"[GARMIN] OAuth token restore failed ({e}), trying other methods")

        client = Garmin(self._email, self._password)

        # Priority 2: Legacy session tokens
        if _TOKEN_FILE.exists():
            try:
                token_data = json.loads(_TOKEN_FILE.read_text())
                client.set_tokens(**token_data)
                client.login()
                self._client = client
                logger.info("[GARMIN] Resumed session from saved tokens")
                return client
            except Exception as e:
                logger.warning(
                    f"[GARMIN] Token resume failed ({e}), falling back to full login"
                )

        # Priority 3: Full SSO login (may fail from server IPs due to Garmin blocking)
        client.login()
        self._save_tokens(client)
        self._client = client
        logger.info("[GARMIN] Full login succeeded, tokens saved")
        return client

    def _save_tokens(self, client) -> None:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            tokens = client.get_tokens()
            _TOKEN_FILE.write_text(json.dumps(tokens, default=str))
        except Exception as e:
            logger.warning(f"[GARMIN] Could not save tokens: {e}")

    def fetch_and_store_daily_metrics(
        self, user_id: str, target_date: Optional[date] = None
    ) -> bool:
        """
        Fetch daily wellness metrics from Garmin Connect and store in DB.
        Returns True on success, False on failure.
        """
        if _is_circuit_open():
            logger.info("[GARMIN] Circuit open — skipping sync, using cached values")
            return False

        if target_date is None:
            target_date = date.today()
        date_str = target_date.strftime("%Y-%m-%d")

        try:
            client = self._get_client()
            metrics = self._collect_metrics(client, target_date)
            upsert_garmin_daily_metrics(user_id, date_str, metrics)
            _reset_circuit()
            logger.info(
                f"[GARMIN] Synced metrics for {user_id}/{date_str}: readiness={metrics.get('training_readiness_score')}"
            )
            return True

        except Exception as e:
            just_opened = _record_failure()
            logger.error(f"[GARMIN] Sync failed for {user_id}/{date_str}: {e}")
            if just_opened:
                self._notify_circuit_open(user_id)
            return False

    def _collect_metrics(self, client, target_date: date) -> dict:
        date_str = target_date.strftime("%Y-%m-%d")
        metrics: dict = {}

        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        readiness_data = _safe(client.get_training_readiness, date_str)
        if readiness_data and isinstance(readiness_data, list) and readiness_data:
            metrics["training_readiness_score"] = readiness_data[0].get("score")

        hrv_data = _safe(client.get_hrv_data, date_str)
        if hrv_data:
            metrics["hrv_status"] = hrv_data.get("hrvSummary", {}).get("status")
            metrics["hrv_weekly_avg"] = hrv_data.get("hrvSummary", {}).get("weeklyAvg")
            metrics["hrv_last_night"] = hrv_data.get("hrvSummary", {}).get("lastNight")

        sleep_data = _safe(client.get_sleep_data, date_str)
        if sleep_data:
            daily = sleep_data.get("dailySleepDTO", {})
            metrics["sleep_duration_sec"] = daily.get("sleepTimeSeconds")
            metrics["deep_sleep_sec"] = daily.get("deepSleepSeconds")
            scores = sleep_data.get("sleepScores", {})
            metrics["sleep_score"] = (
                scores.get("overall", {}).get("value")
                if isinstance(scores.get("overall"), dict)
                else scores.get("overall")
            )

        battery_data = _safe(client.get_body_battery, date_str, date_str)
        if battery_data and isinstance(battery_data, list):
            charges = [
                d.get("charged", 0)
                for d in battery_data
                if d.get("charged") is not None
            ]
            if charges:
                metrics["body_battery_morning"] = max(charges)
                metrics["body_battery_evening"] = min(charges)

        hr_data = _safe(client.get_heart_rates, date_str)
        if hr_data:
            metrics["resting_hr"] = hr_data.get("restingHeartRate")

        stress_data = _safe(client.get_stress_data, date_str)
        if stress_data:
            metrics["stress_avg"] = stress_data.get("averageStressLevel")

        training_status = _safe(client.get_training_status, date_str)
        if training_status:
            if isinstance(training_status, list) and training_status:
                metrics["training_status"] = training_status[0].get(
                    "trainingLoadStatus"
                )
            elif isinstance(training_status, dict):
                metrics["training_status"] = training_status.get("trainingLoadStatus")

        return metrics

    def get_daily_metrics(
        self, user_id: str, target_date: Optional[date] = None
    ) -> Optional[dict]:
        """
        Return cached Garmin metrics for user/date (up to 3-day fallback).
        Does NOT trigger a live sync — use fetch_and_store_daily_metrics() for that.
        """
        if target_date is None:
            target_date = date.today()
        return get_garmin_daily_metrics(
            user_id, target_date.strftime("%Y-%m-%d"), max_stale_days=3
        )

    def fetch_gear_stats(self, user_id: str) -> list[dict]:
        """Fetch gear (shoe) list with total mileage from Garmin."""
        if _is_circuit_open():
            return []
        try:
            client = self._get_client()
            gear_list = client.get_gear(user_id) or []
            result = []
            for item in gear_list:
                gear_id = item.get("gearPk") or item.get("uuid")
                if gear_id:
                    try:
                        stats = client.get_gear_stats(gear_id)
                        total_km = (stats.get("totalDistance") or 0) / 1000
                    except Exception:
                        total_km = 0
                    result.append(
                        {
                            "name": item.get("displayName", "Unknown"),
                            "total_km": round(total_km, 1),
                            "gear_id": gear_id,
                        }
                    )
            return result
        except Exception as e:
            _record_failure()
            logger.error(f"[GARMIN] Failed to fetch gear stats: {e}")
            return []

    def test_connection(self, timeout_sec: int = 30) -> tuple[bool, str]:
        """Test Garmin connection with hard timeout. Uses OAuth token fast path if available."""
        # Fast path: OAuth token exists — verify without SSO
        if has_oauth_token():
            logger.info("[GARMIN] test_connection — using OAuth token fast path")
            timeout_sec = min(timeout_sec, 15)  # token path is fast; cap at 15s

        if not has_oauth_token() and not self._email:
            return False, "Chưa có OAuth token hoặc credentials. Tải token từ script local."

        logger.info(f"[GARMIN] test_connection start — has_token={has_oauth_token()} email={self._email[:3] if self._email else '(none)'}*** timeout={timeout_sec}s")

        def _do_connect() -> str:
            client = self._get_client()
            name = client.get_full_name()
            return name or "OK"

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_do_connect)
        executor.shutdown(wait=False)  # don't block on thread after timeout
        try:
            name = future.result(timeout=timeout_sec)
            logger.info(f"[GARMIN] test_connection success — user={name}")
            return True, ""
        except concurrent.futures.TimeoutError:
            logger.warning(f"[GARMIN] test_connection timed out after {timeout_sec}s — Garmin is blocking server IP")
            return False, f"Kết nối timeout sau {timeout_sec}s — Garmin đang giới hạn server IP. Dùng env vars GARMIN_EMAIL/GARMIN_PASSWORD hoặc thử lại sau."
        except Exception as e:
            logger.error(f"[GARMIN] test_connection failed: {e}")
            return False, str(e)

    def clear_tokens(self) -> None:
        """Delete all saved tokens, forcing full re-login on next use."""
        if _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()
        if _OAUTH_TOKEN_FILE.exists():
            _OAUTH_TOKEN_FILE.unlink()
        self._client = None
        logger.info("[GARMIN] All tokens cleared")

    def _notify_circuit_open(self, user_id: str) -> None:
        """Send one Telegram alert when circuit breaker opens."""
        try:
            from app.core.notification import send_telegram_msg
            from app.core.user_context import get_primary_user_id

            chat_id = get_primary_user_id()
            send_telegram_msg(
                chat_id,
                "⚠️ Garmin Connect tạm thời không kết nối được (3 lần thất bại liên tiếp).\n"
                "Hệ thống sẽ dùng dữ liệu cache trong 24h. Anh không cần làm gì.",
            )
        except Exception:
            pass


_garmin_client_instance: Optional[GarminClient] = None


def get_garmin_client() -> GarminClient:
    """Singleton accessor for GarminClient."""
    global _garmin_client_instance
    if _garmin_client_instance is None:
        _garmin_client_instance = GarminClient()
    return _garmin_client_instance

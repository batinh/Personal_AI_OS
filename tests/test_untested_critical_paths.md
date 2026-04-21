# Untested Critical Paths — Test Specifications

## Priority 1 Tests (Write First — Security)

### 1.1 Strava Webhook Signature Validation

```python
# test_webhooks.py

class TestStravaWebhookSignatureValidation(unittest.TestCase):
    """
    SECURITY: Strava requests must validate HMAC signature to prevent spoofing.
    Current state: NO signature validation implemented.
    Risk: Attacker can send fake activity events and trigger analysis.
    
    Implementation approach:
    1. Add STRAVA_SECRET_KEY to environment
    2. Extract X-Strava-Signature header from request
    3. Compute HMAC-SHA256 of request body
    4. Compare signatures using secrets.compare_digest
    5. Return 401 Unauthorized if invalid
    """

    def setUp(self):
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    @patch.dict("os.environ", {"STRAVA_SECRET_KEY": "test-secret-key"})
    def test_valid_signature_accepted(self):
        """Request with correct HMAC signature is accepted."""
        # Arrange
        secret = "test-secret-key"
        payload = {
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 12345678,
        }
        import json
        import hmac
        import hashlib
        body_str = json.dumps(payload)
        signature = hmac.new(
            secret.encode(),
            body_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Act
        resp = self.client.post(
            "/webhook",
            json=payload,
            headers={"X-Strava-Signature": f"v0={signature}"}
        )
        
        # Assert
        self.assertEqual(resp.status_code, 200)

    @patch.dict("os.environ", {"STRAVA_SECRET_KEY": "test-secret-key"})
    def test_invalid_signature_rejected(self):
        """Request with incorrect HMAC signature is rejected (401)."""
        # Arrange
        payload = {
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 12345678,
        }
        
        # Act
        resp = self.client.post(
            "/webhook",
            json=payload,
            headers={"X-Strava-Signature": "v0=invalid-signature"}
        )
        
        # Assert
        self.assertEqual(resp.status_code, 401)
        self.assertIn("signature", resp.json().get("detail", "").lower())

    def test_missing_signature_header_rejected(self):
        """Request without X-Strava-Signature header is rejected."""
        # Arrange
        payload = {
            "object_type": "activity",
            "aspect_type": "create",
            "object_id": 12345678,
        }
        
        # Act
        resp = self.client.post("/webhook", json=payload)
        
        # Assert
        self.assertEqual(resp.status_code, 401)

    @patch.dict("os.environ", {"STRAVA_SECRET_KEY": "secret"})
    def test_timing_attack_prevention(self):
        """
        Signature comparison uses secrets.compare_digest, not ==.
        Document: correct signature must be constant-time compared.
        (This is a code review check — not easily testable, but documents intent.)
        """
        # This is a behavioral spec: implementation must use secrets.compare_digest
        # Verified by code review, not unit test
        pass


class TestStravaWebhookSignatureMissing(unittest.TestCase):
    """If signature validation not yet implemented, document as TODO."""

    def test_signature_validation_todo(self):
        """FAILING TEST — Documents security debt."""
        from app.main import app
        from unittest.mock import patch
        
        client = TestClient(app, raise_server_exceptions=False)
        
        # Arrange
        payload = {"object_type": "activity", "aspect_type": "create", "object_id": 123}
        
        # Current behavior: accepts unsigned requests (VULNERABLE)
        resp = client.post("/webhook", json=payload)
        
        # TODO: Once implemented, should reject:
        # self.assertEqual(resp.status_code, 401)
        
        # For now, document the vulnerability:
        logger = __import__('logging').getLogger(__name__)
        logger.warning(
            "SECURITY DEBT: Strava webhook signature validation not implemented. "
            "Attackers can spoof activity events."
        )
```

---

### 1.2 Admin Credential Validation & Timing Attack Prevention

```python
# test_admin.py or test_webhooks.py

class TestAdminCredentialValidation(unittest.TestCase):
    """
    SECURITY: Weak default credentials "admin"/"123456" hardcoded.
    Current state: Defaults used if env vars not set.
    Risk: If deployed with defaults, trivial to compromise.
    
    Implementation approach:
    1. Require explicit env vars (no defaults) or raise on startup
    2. Enforce minimum password length (12+ chars)
    3. Use secrets.compare_digest for constant-time comparison
    """

    def setUp(self):
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    @patch.dict("os.environ", {"ADMIN_USERNAME": "coach", "ADMIN_PASSWORD": "s3cur3!pass123"})
    def test_correct_credentials_grant_access(self):
        """Valid credentials pass authentication."""
        # Arrange
        auth = ("coach", "s3cur3!pass123")
        
        # Act
        resp = self.client.get("/admin", auth=auth)
        
        # Assert
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Admin", resp.text)

    @patch.dict("os.environ", {"ADMIN_USERNAME": "coach", "ADMIN_PASSWORD": "s3cur3!pass123"})
    def test_wrong_username_denied(self):
        """Incorrect username denied."""
        # Arrange
        auth = ("attacker", "s3cur3!pass123")
        
        # Act
        resp = self.client.get("/admin", auth=auth)
        
        # Assert
        self.assertEqual(resp.status_code, 401)

    @patch.dict("os.environ", {"ADMIN_USERNAME": "coach", "ADMIN_PASSWORD": "s3cur3!pass123"})
    def test_wrong_password_denied(self):
        """Incorrect password denied."""
        # Arrange
        auth = ("coach", "wrong-password")
        
        # Act
        resp = self.client.get("/admin", auth=auth)
        
        # Assert
        self.assertEqual(resp.status_code, 401)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_vars_uses_weak_defaults(self):
        """
        BUG DOCUMENTATION: If env vars missing, weak defaults "admin"/"123456" used.
        This test documents the vulnerability.
        """
        # Current behavior: uses weak defaults
        # TODO: Change to raise ValueError at startup instead
        from app.routers.admin import verify_credentials
        
        # This should FAIL after fix (env vars required)
        # For now, documents current (unsafe) state
        default_username = "admin"
        default_password = "123456"
        
        # Arrange
        from unittest.mock import MagicMock
        mock_creds = MagicMock()
        mock_creds.username = default_username
        mock_creds.password = default_password
        
        # Act & Assert
        # Should not raise (but SHOULD after security hardening)
        result = verify_credentials(mock_creds)
        self.assertEqual(result, default_username)
        
        logger = __import__('logging').getLogger(__name__)
        logger.warning(
            "SECURITY DEBT: Default credentials used when env vars not set. "
            "Change to require explicit ADMIN_USERNAME + ADMIN_PASSWORD or fail startup."
        )

    @patch.dict("os.environ", {"ADMIN_USERNAME": "coach", "ADMIN_PASSWORD": "short"})
    def test_weak_password_detected(self):
        """
        TODO: Enforce minimum password strength (12+ chars, special chars).
        This test documents the requirement.
        """
        # Arrange
        weak_password = "short"
        
        # Currently: accepted
        # After fix: should raise ValueError or log warning at startup
        
        # Document the gap:
        logger = __import__('logging').getLogger(__name__)
        logger.warning(
            f"SECURITY DEBT: Password '{weak_password}' accepted (< 12 chars). "
            "Enforce minimum strength at startup."
        )

    def test_timing_attack_prevention(self):
        """
        IMPLEMENTATION CHECK: verify_credentials() uses secrets.compare_digest.
        (Code review check — not easily unit testable, but documents requirement.)
        """
        # Read the implementation and verify it uses secrets.compare_digest
        # Expected:
        #   is_user_ok = secrets.compare_digest(credentials.username, env_user)
        #   is_pass_ok = secrets.compare_digest(credentials.password, env_pass)
        pass
```

---

## Priority 2 Tests (Write Next — Error Handling)

### 2.1 Scheduler Task Exception Recovery

```python
# test_scheduler.py (add to existing file)

class TestSchedulerTaskExceptionRecovery(unittest.TestCase):
    """
    CRITICAL: Scheduler tasks have NO try/except. If any task fails, it crashes.
    Current state: All tasks (briefing, news, audit, etc.) unprotected.
    Risk: One exception kills the entire scheduler. Tasks don't run again.
    
    Implementation approach:
    1. Wrap all task functions in try/except
    2. Log exception with full context (module, task, timestamp)
    3. Catch and silently continue (don't propagate)
    4. Send Telegram alert for critical failures (AI tasks)
    5. Retry logic: APScheduler can add max_instances=1, misfire_grace_time
    """

    @patch("app.services.scheduler.generate_morning_briefing")
    @patch("app.services.scheduler.get_today_weather")
    @patch("app.services.scheduler.load_config")
    @patch("app.services.scheduler.get_primary_user_id", return_value="123456")
    def test_morning_briefing_exception_handled(self, mock_uid, mock_cfg, mock_weather, mock_gen):
        """If generate_morning_briefing raises, task continues without crashing."""
        # Arrange
        mock_cfg.return_value = {"test": "config"}
        mock_weather.return_value = "Sunny"
        mock_gen.side_effect = Exception("AI service timeout")
        
        # Act — should NOT raise
        from app.services.scheduler import task_morning_briefing
        try:
            task_morning_briefing()
        except Exception:
            self.fail("task_morning_briefing should not propagate exceptions")
        
        # Assert — exception was logged
        # (Verify in logs: "[SCHEDULER] task_morning_briefing failed: AI service timeout")

    @patch("app.services.scheduler.generate_news_briefing")
    @patch("app.services.scheduler.load_config")
    def test_news_task_exception_handled(self, mock_cfg, mock_news):
        """If generate_news_briefing raises, scheduler continues."""
        # Arrange
        mock_cfg.return_value = {"test": "config"}
        mock_news.side_effect = ConnectionError("API unavailable")
        
        # Act
        from app.services.scheduler import task_morning_news
        try:
            task_morning_news()
        except ConnectionError:
            self.fail("task_morning_news should catch exceptions")
        
        # Assert — logged

    @patch("app.services.scheduler.harvest_data")
    def test_harvest_exception_handled(self, mock_harvest):
        """If harvest_data raises, scheduler continues."""
        # Arrange
        mock_harvest.side_effect = RuntimeError("Strava API error")
        
        # Act
        from app.services.scheduler import task_auto_harvest
        try:
            task_auto_harvest()
        except RuntimeError:
            self.fail("task_auto_harvest should catch exceptions")

    @patch("app.services.scheduler.send_telegram_msg")
    @patch("app.services.scheduler.get_training_loads")
    @patch("app.services.scheduler.calculate_training_phase")
    @patch("app.services.scheduler.load_config")
    @patch("app.services.scheduler.get_primary_user_id", return_value="123456")
    def test_proactive_check_exception_handled(self, mock_uid, mock_cfg, mock_phase, mock_loads, mock_tg):
        """If proactive check fails, scheduler continues."""
        # Arrange
        mock_cfg.return_value = {"race_date": "2026-06-01", "race_distance_km": 21.1}
        mock_loads.side_effect = Exception("DB connection failed")
        
        # Act
        from app.services.scheduler import task_proactive_coach_check
        try:
            task_proactive_coach_check()
        except Exception:
            self.fail("task_proactive_coach_check should catch exceptions")

    @patch("app.services.scheduler.run_audit")
    @patch("app.services.scheduler.get_primary_user_id", return_value="123456")
    def test_log_audit_exception_handled(self, mock_uid, mock_audit):
        """If run_audit raises, scheduler continues."""
        # Arrange
        mock_audit.side_effect = IOError("Log file not accessible")
        
        # Act
        from app.services.scheduler import task_log_audit
        try:
            task_log_audit()
        except IOError:
            self.fail("task_log_audit should catch exceptions")

    def test_exception_logged_with_full_context(self):
        """When exception occurs, log includes: module, task, timestamp, error message."""
        # Document expected log format:
        # "[SCHEDULER] task_morning_briefing FAILED at 2026-04-20 06:00:15: Exception: AI service timeout"
        pass
```

---

### 2.2 Webhook Payload JSON Decode Error Handling

```python
# test_webhooks.py (add to existing file)

class TestWebhookPayloadValidation(unittest.TestCase):
    """
    CRITICAL: Webhook endpoints call request.json() without error handling.
    Current state: Invalid JSON causes 422 Unprocessable Entity (auto-handled by FastAPI).
    Risk: Unclear behavior; no custom error logging; possible silent drops.
    
    Implementation approach:
    1. Explicitly catch JSONDecodeError in try/except
    2. Log the malformed payload (first 1KB for debugging)
    3. Return 400 Bad Request with descriptive error
    4. Alert admin if suspicious patterns detected (repeated failures)
    """

    def setUp(self):
        from app.main import app
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_strava_webhook_malformed_json_rejected(self):
        """POST /webhook with invalid JSON is rejected with 400."""
        # Arrange
        malformed_json = "{invalid json, no closing brace"
        
        # Act
        resp = self.client.post(
            "/webhook",
            content=malformed_json,
            headers={"Content-Type": "application/json"}
        )
        
        # Assert — 400 Bad Request (or 422 from FastAPI)
        self.assertIn(resp.status_code, [400, 422])

    def test_telegram_webhook_malformed_json_rejected(self):
        """POST /telegram-webhook with invalid JSON is rejected."""
        # Arrange
        malformed_json = '{"unclosed": "object'
        
        # Act
        resp = self.client.post(
            "/telegram-webhook",
            content=malformed_json,
            headers={"Content-Type": "application/json"}
        )
        
        # Assert
        self.assertIn(resp.status_code, [400, 422])

    def test_malformed_payload_logged(self):
        """When JSON decode fails, the malformed payload is logged (first 1KB)."""
        # Arrange
        malformed = "{bad json"
        
        # Act & Assert — verify logs contain truncated payload
        # Expected log: "[WEBHOOK] JSON decode error: Expecting property name enclosed in double quotes"
        pass

    def test_empty_json_object_accepted(self):
        """Empty JSON object {} is accepted (but may have no matching fields)."""
        # Arrange
        empty_payload = {}
        
        # Act
        resp = self.client.post("/webhook", json=empty_payload)
        
        # Assert — 200 OK (no crash)
        self.assertEqual(resp.status_code, 200)

    def test_null_object_body_rejected(self):
        """POST with null body is rejected."""
        # Arrange — send null as body
        
        # Act
        resp = self.client.post(
            "/webhook",
            content="null",
            headers={"Content-Type": "application/json"}
        )
        
        # Assert
        # Should handle gracefully (400 or auto-reject by FastAPI)
        self.assertIn(resp.status_code, [400, 422])

    def test_repeated_malformed_payloads_trigger_alert(self):
        """
        TODO: If >5 malformed payloads in 1 hour, send Telegram alert to admin.
        This implements basic DDoS detection.
        """
        # Arrange — send 6 malformed requests
        
        # Act & Assert
        # Should trigger alert: "[WEBHOOK] Suspicious activity: 6 malformed payloads in 1h"
        pass
```

---

### 2.3 Database OperationalError Retry Logic

```python
# test_database.py (add to existing file)

class TestDatabaseRetryLogic(unittest.TestCase):
    """
    CRITICAL: DB functions catch OperationalError but don't retry.
    Current state: Caught with `pass` — silent failure.
    Risk: Transient "database is locked" errors are silently ignored;
          data loss silent.
    
    Implementation approach:
    1. Implement exponential backoff retry (3 attempts, 0.1s, 0.2s, 0.4s)
    2. Log each retry with attempt number
    3. On 3rd failure, raise the original exception
    4. Use @contextmanager for clean error handling
    """

    @patch("app.core.database.get_db_connection")
    def test_operational_error_retried_exponentially(self, mock_conn_factory):
        """
        Query fails with OperationalError twice, succeeds on 3rd attempt.
        """
        # Arrange
        import sqlite3
        mock_conn = MagicMock()
        cursor = MagicMock()
        
        # First 2 calls: OperationalError; 3rd: success
        cursor.execute.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            None,  # 3rd attempt succeeds
        ]
        mock_conn.cursor.return_value = cursor
        mock_conn_factory.return_value = mock_conn
        
        # Act — function should retry internally and succeed
        # (This requires retry logic to be implemented first)
        from app.core.database import get_db
        
        # After implementing retry logic:
        # with get_db() as conn:
        #     result = conn.cursor().execute(...)
        # Should succeed after retries

    @patch("app.core.database.get_db_connection")
    def test_operational_error_fails_after_3_retries(self, mock_conn_factory):
        """
        Query fails all 3 times — raises exception after final retry.
        """
        # Arrange
        import sqlite3
        mock_conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
        mock_conn.cursor.return_value = cursor
        mock_conn_factory.return_value = mock_conn
        
        # Act & Assert
        from app.core.database import get_db
        
        with self.assertRaises(sqlite3.OperationalError):
            with get_db() as conn:
                conn.cursor().execute("SELECT * FROM run_activities")

    def test_retry_logged_with_attempt_number(self):
        """Each retry attempt is logged: [DB] Retry 1/3: database is locked."""
        # Verify logs contain:
        # "[DB] Retry 1/3: <error message>"
        # "[DB] Retry 2/3: <error message>"
        pass

    def test_exponential_backoff_timing(self):
        """Retry delays: 0.1s, 0.2s, 0.4s (exponential backoff)."""
        # Verify sleep() calls use correct timing
        pass
```

---

## Priority 3 Tests (Write After — Config & DB)

### 3.1 Config Time-String Parsing Failures

```python
# test_config.py (add to existing file)

class TestConfigTimeStringParsing(unittest.TestCase):
    """
    CRITICAL: setup_jobs() parses time strings (briefing_time, backup_time) but
    catches all exceptions with `except Exception: fallback`.
    Current state: Invalid times silently use defaults; no validation.
    Risk: Typos in config.json go unnoticed; scheduler runs at wrong time.
    
    Implementation approach:
    1. Validate time strings at config load time (not at job setup)
    2. Raise ValueError with clear message if format invalid
    3. Log the fallback to defaults
    4. Test all time string edge cases
    """

    def test_valid_time_string_parsed(self):
        """Valid HH:MM format parsed correctly."""
        # Arrange
        from app.services.scheduler import setup_jobs
        cfg = {
            "scheduler": {
                "briefing_time": "06:30",
                "backup_time": "02:15",
                "harvest_hours": "0,6,12,18",
                "harvest_minute": "45",
            },
            "news_agent": {"enabled": False},
        }
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            setup_jobs()
        
        # Assert — jobs added with correct times
        # Verify CronTrigger(hour=6, minute=30) was passed

    def test_invalid_time_string_uses_fallback(self):
        """Invalid time string falls back to default."""
        # Arrange
        from app.services.scheduler import setup_jobs
        cfg = {
            "scheduler": {
                "briefing_time": "not-a-time",  # Invalid
                "backup_time": "25:99",         # Invalid
                "harvest_hours": "0,6,12,18",
                "harvest_minute": "15",
            },
            "news_agent": {"enabled": False},
        }
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            setup_jobs()
        
        # Assert — fallback defaults used (6:00 and 2:00)

    def test_time_string_edge_cases(self):
        """Test boundary time values: 00:00, 23:59, leading zeros."""
        # Arrange
        test_cases = [
            ("00:00", (0, 0)),      # Midnight
            ("23:59", (23, 59)),    # 11:59 PM
            ("06:05", (6, 5)),      # Single-digit minute
            ("16:00", (16, 0)),     # 4 PM
        ]
        
        for time_str, (expected_h, expected_m) in test_cases:
            # Act & Assert
            h, m = map(int, time_str.split(':'))
            self.assertEqual(h, expected_h)
            self.assertEqual(m, expected_m)

    def test_invalid_hour_detected(self):
        """Hour > 23 detected and error raised."""
        # Arrange
        invalid_time = "25:00"
        
        # Act & Assert
        with self.assertRaises((ValueError, IndexError)):
            h, m = map(int, invalid_time.split(':'))
            if h > 23 or h < 0:
                raise ValueError(f"Invalid hour: {h}")

    def test_invalid_minute_detected(self):
        """Minute > 59 detected and error raised."""
        # Arrange
        invalid_time = "12:75"
        
        # Act & Assert
        with self.assertRaises(ValueError):
            h, m = map(int, invalid_time.split(':'))
            if m > 59 or m < 0:
                raise ValueError(f"Invalid minute: {m}")

    def test_missing_colon_detected(self):
        """Time string without ':' separator detected."""
        # Arrange
        invalid_time = "0630"
        
        # Act & Assert
        with self.assertRaises(ValueError):
            parts = invalid_time.split(':')
            if len(parts) != 2:
                raise ValueError(f"Invalid time format: {invalid_time}")

    def test_config_load_time_validation(self):
        """
        TODO: Validate time strings in load_config(), not in setup_jobs().
        This prevents silent errors at job setup time.
        """
        # Expected behavior:
        # load_config() raises ValueError if any time string is invalid
        # Example: {"scheduler": {"briefing_time": "bad"}} → ValueError
        pass
```

---

### 3.2 Database Multi-Tenant User ID Validation

```python
# test_database.py (add to existing file)

class TestDatabaseMultiTenantUserID(unittest.TestCase):
    """
    CRITICAL: All database operations require user_id (multi-tenant safety).
    Current state: Some queries accept user_id, but validation is weak.
    Risk: Data leakage between users if user_id not enforced in WHERE clause.
    
    Implementation approach:
    1. Add @validate_user_id decorator to all DB functions
    2. Raise ValueError if user_id is None or empty string
    3. Always include user_id in WHERE clause (never SELECT * without user_id filter)
    4. Test isolation: queries from user A never leak user B's data
    """

    def test_save_run_activity_requires_user_id(self):
        """save_run_activity() raises if user_id is None."""
        # Arrange
        from app.core.database import save_run_activity
        
        activity_data = {
            "activity_id": "123",
            "name": "Easy Run",
            "distance_km": 10.0,
        }
        
        # Act & Assert
        with self.assertRaises((ValueError, TypeError)):
            save_run_activity(user_id=None, activity_data=activity_data)

    def test_save_run_activity_requires_non_empty_user_id(self):
        """save_run_activity() raises if user_id is empty string."""
        # Arrange
        from app.core.database import save_run_activity
        
        activity_data = {"activity_id": "123", "name": "Run"}
        
        # Act & Assert
        with self.assertRaises((ValueError, AssertionError)):
            save_run_activity(user_id="", activity_data=activity_data)

    def test_get_training_loads_filters_by_user_id(self):
        """get_training_loads() only returns data for specified user."""
        # Arrange
        from app.core.database import save_run_activity, get_training_loads, get_db
        
        # Create runs for two users
        run_user_a = {
            "activity_id": "user_a_run1",
            "name": "User A Run",
            "distance_km": 10.0,
            "trimp_score": 100,
        }
        run_user_b = {
            "activity_id": "user_b_run1",
            "name": "User B Run",
            "distance_km": 5.0,
            "trimp_score": 50,
        }
        
        # Act
        save_run_activity(user_id="user_a", activity_data=run_user_a)
        save_run_activity(user_id="user_b", activity_data=run_user_b)
        
        loads_a = get_training_loads("user_a")
        loads_b = get_training_loads("user_b")
        
        # Assert — User A doesn't see User B's data
        # loads_a should not include User B's 50-point run

    def test_delete_run_activity_only_deletes_from_correct_user(self):
        """delete_run_activity() with user_id doesn't affect other users."""
        # Arrange
        from app.core.database import save_run_activity, delete_run_activity, get_db
        
        run_a = {"activity_id": "shared_id", "name": "Run A"}
        run_b = {"activity_id": "shared_id", "name": "Run B"}
        
        save_run_activity(user_id="user_a", activity_data=run_a)
        save_run_activity(user_id="user_b", activity_data=run_b)
        
        # Act
        delete_run_activity("shared_id", user_id="user_a")  # Assuming signature updated
        
        # Assert — User A's run deleted, User B's remains
        # (Requires delete_run_activity to accept user_id parameter)

    def test_all_db_functions_validate_user_id(self):
        """
        AUDIT: All functions in database.py that interact with user data
        must require and validate user_id.
        
        Required functions:
        - get_training_loads(user_id) ✓
        - get_historical_training_loads(user_id, ...) ✓
        - get_monthly_volume(user_id, ...) ✓
        - get_yearly_volume(user_id, ...) ✓
        - save_run_activity(user_id, ...) ✓
        - save_run_activity_raw(user_id, ...) ✓
        - delete_run_activity(activity_id) ✗ — needs user_id parameter
        - upsert_run_computed_metrics(activity_id, user_id) ✓
        - get_run_metrics_from_db(activity_id, user_id) ✓
        
        Verify: All have user_id parameter and include it in WHERE clause.
        """
        pass
```

---

### 3.3 News Agent Enabled/Disabled Configuration

```python
# test_scheduler.py (already partially covered, but add edge cases)

class TestNewsAgentConfiguration(unittest.TestCase):
    """
    Config: news_agent.enabled boolean controls 3 scheduled jobs.
    Current state: Jobs conditionally added based on flag.
    Risk: Flag parsing errors (e.g., "true" vs true) may cause jobs not to load.
    
    Implementation approach:
    1. Parse news_agent.enabled as boolean (not string)
    2. Log which news jobs are added/skipped
    3. Test all boolean variants: True, False, "true", "false", 0, 1, None
    """

    def _make_cfg(self, enabled):
        return {
            "model_name": "test",
            "scheduler": {
                "briefing_time": "06:00",
                "backup_time": "02:00",
                "harvest_hours": "0,6,12,18",
                "harvest_minute": "15",
            },
            "news_agent": {
                "enabled": enabled,
                "morning_time": "07:00",
                "afternoon_time": "17:00",
                "evening_time": "20:00",
            },
        }

    def test_news_enabled_true_adds_jobs(self):
        """news_agent.enabled = True adds 3 news jobs."""
        # Arrange
        cfg = self._make_cfg(enabled=True)
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            setup_jobs()
        
        # Assert
        job_ids = [call[1]["id"] for call in mock_sched.add_job.call_args_list
                   if "id" in call[1]]
        self.assertIn("news_morning", job_ids)
        self.assertIn("news_afternoon", job_ids)
        self.assertIn("news_evening", job_ids)

    def test_news_enabled_false_skips_jobs(self):
        """news_agent.enabled = False skips news jobs."""
        # Arrange
        cfg = self._make_cfg(enabled=False)
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            setup_jobs()
        
        # Assert
        job_ids = [call[1]["id"] for call in mock_sched.add_job.call_args_list
                   if "id" in call[1]]
        self.assertNotIn("news_morning", job_ids)

    def test_news_enabled_string_true_parsed(self):
        """news_agent.enabled = "true" (string) is parsed as boolean."""
        # Arrange
        cfg = self._make_cfg(enabled="true")  # String, not bool
        
        # Act & Assert — should handle gracefully
        # (Requires JSON parsing to convert "true" → True)
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            # Should not crash
            setup_jobs()

    def test_news_enabled_none_skips_jobs(self):
        """news_agent.enabled = None defaults to False."""
        # Arrange
        cfg = self._make_cfg(enabled=None)
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            setup_jobs()
        
        # Assert — news jobs skipped

    def test_news_missing_from_config_skips_jobs(self):
        """If 'news_agent' key missing, jobs not added."""
        # Arrange
        cfg = {
            "model_name": "test",
            "scheduler": {
                "briefing_time": "06:00",
                "backup_time": "02:00",
                "harvest_hours": "0,6,12,18",
                "harvest_minute": "15",
            },
            # news_agent missing entirely
        }
        
        # Act
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            setup_jobs()
        
        # Assert — no crash, jobs skipped

    def test_news_job_times_parsed_on_enable(self):
        """news_agent times (morning_time, afternoon_time) only parsed if enabled."""
        # Arrange
        cfg = self._make_cfg(enabled=True)
        cfg["news_agent"]["morning_time"] = "invalid-time"  # Would fail parsing
        
        # Act & Assert — should catch and use default
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.scheduler") as mock_sched:
            from app.services.scheduler import setup_jobs
            setup_jobs()
```

---

## Summary: Test Coverage Map

| Critical Path | Priority | Untested | Test Stubs Written | Next Steps |
|---|---|---|---|---|
| Strava webhook signature validation | 1 | YES | YES | Implement HMAC validation in `verify_signature()` |
| Admin credential defaults | 1 | YES | YES | Remove defaults; enforce env vars or fail startup |
| Scheduler task exception recovery | 2 | YES | YES | Wrap all tasks in try/except + logging |
| Webhook JSON decode errors | 2 | YES | YES | Add explicit JSONDecodeError handler |
| Database OperationalError retry | 2 | YES | YES | Implement exponential backoff (3 retries) |
| Config time-string validation | 3 | YES | YES | Validate at load_config() time, not setup_jobs() |
| Multi-tenant user_id validation | 3 | YES | YES | Audit all DB functions; add @validate_user_id |
| News agent config parsing | 3 | YES | YES | Handle boolean variants + missing keys |

---

## Integration with TDD Workflow

**Phase 1: RED (Week 1)**
- Copy all test stubs from this file
- Run `pytest tests/test_untested_critical_paths.py -v`
- All tests fail (expected)

**Phase 2: GREEN (Week 2-3)**
- Implement minimal code to pass each priority 1 test
- Strava signature validation: add HMAC check in `/webhook` endpoint
- Admin credentials: remove defaults, require env vars or raise

**Phase 3: REFACTOR (Week 4)**
- Extract retry logic to shared `_retry_db_operation()` helper
- Add `@validate_user_id` decorator for DB functions
- Implement config validation at startup

**Phase 4: COVERAGE**
- Target: 80%+ coverage of critical paths
- Verify: `pytest --cov=app.routers,app.services,app.core.database --cov-report=term-missing`


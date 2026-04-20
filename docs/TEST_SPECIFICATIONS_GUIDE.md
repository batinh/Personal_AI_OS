# Test Specifications Guide — Critical Paths

**Document**: `/tests/test_untested_critical_paths.md`

This file contains 45 concrete test stubs for 8 untested critical paths across the application. All tests follow the **AAA pattern** (Arrange-Act-Assert) and the project's existing test style.

---

## Quick Start

### 1. Review the Specifications
```bash
# Read the full spec document
cat tests/test_untested_critical_paths.md

# View test count by priority
grep "^### " tests/test_untested_critical_paths.md | wc -l  # 8 specs
grep "def test_" tests/test_untested_critical_paths.md | wc -l  # 45 tests
```

### 2. Copy Tests Into Actual Test Files

Tests are organized by module. Copy test classes to their target files:

| Test Stub | Target File | Action |
|-----------|-------------|--------|
| `TestStravaWebhookSignatureValidation` | `tests/test_webhooks.py` | Copy & implement HMAC validation |
| `TestAdminCredentialValidation` | `tests/test_admin.py` or new file | Copy & remove weak defaults |
| `TestSchedulerTaskExceptionRecovery` | `tests/test_scheduler.py` | Copy & wrap tasks in try/except |
| `TestWebhookPayloadValidation` | `tests/test_webhooks.py` | Copy & add JSONDecodeError handler |
| `TestDatabaseRetryLogic` | `tests/test_database.py` | Copy & implement exponential backoff |
| `TestConfigTimeStringParsing` | `tests/test_config.py` | Copy & validate at load time |
| `TestDatabaseMultiTenantUserID` | `tests/test_database.py` | Copy & audit all DB functions |
| `TestNewsAgentConfiguration` | `tests/test_scheduler.py` | Copy & handle boolean variants |

### 3. Run Tests to Verify They Fail (RED Phase)

```bash
# Copy one test class at a time
cp tests/test_untested_critical_paths.md /tmp/spec.md

# Edit tests/test_webhooks.py, add the TestStravaWebhookSignatureValidation class
# Then run:
python -m pytest tests/test_webhooks.py::TestStravaWebhookSignatureValidation -v

# Expected: All tests FAIL (because feature not implemented yet)
# FAILED tests/test_webhooks.py::TestStravaWebhookSignatureValidation::test_valid_signature_accepted
```

### 4. Implement Minimal Code (GREEN Phase)

For each failing test, write the minimal implementation to make it pass.

**Example 1: Strava Signature Validation**

```python
# app/routers/webhooks.py — add to strava_event()

import hmac
import hashlib
import secrets

@router.post("/webhook")
async def strava_event(request: Request, background_tasks: BackgroundTasks):
    # Extract signature from header
    signature_header = request.headers.get("X-Strava-Signature", "")
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature")
    
    # Get request body
    body = await request.body()
    
    # Compute expected signature
    secret = os.getenv("STRAVA_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="STRAVA_SECRET_KEY not configured")
    
    expected_sig = "v0=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    # Compare with constant-time comparison
    if not secrets.compare_digest(signature_header, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Continue processing...
    data = json.loads(body)
    ...
```

**Example 2: Scheduler Task Exception Recovery**

```python
# app/services/scheduler.py — wrap task_morning_briefing()

def task_morning_briefing():
    """[ORCHESTRATOR] Morning briefing with exception recovery."""
    try:
        chat_id = get_primary_user_id()
        if not chat_id:
            logger.warning("[SCHEDULER] TELEGRAM_CHAT_ID not found for briefing.")
            return
        
        logger.info("[SCHEDULER] Triggering morning briefing...")
        config = load_config()
        weather_info = get_today_weather()
        generate_morning_briefing(config, weather_info)
    except Exception as e:
        logger.error(f"[SCHEDULER] task_morning_briefing FAILED: {e}", exc_info=True)
        # Don't propagate — BackgroundScheduler will continue
```

### 5. Verify Tests Pass (GREEN Phase)

```bash
python -m pytest tests/test_webhooks.py::TestStravaWebhookSignatureValidation -v

# Expected: All tests PASS
# PASSED tests/test_webhooks.py::TestStravaWebhookSignatureValidation::test_valid_signature_accepted
```

### 6. Run Full Test Suite (Smoke & Unit)

```bash
# Quick smoke test (< 2s)
python -m pytest tests/test_smoke.py -v

# Full unit + integration
python -m pytest tests/ -q

# With coverage
python -m pytest tests/ --cov=app --cov-report=html
```

---

## Priority Levels & Implementation Order

### Priority 1: Security (Week 1)
**Deadline: ASAP** — These are security vulnerabilities

1. **Strava Webhook Signature Validation** (5 tests)
   - Risk: Attackers can send fake activity events
   - Implementation time: 2-3 hours
   - Files: `app/routers/webhooks.py` (add `verify_signature()`)

2. **Admin Credential Validation** (4 tests)
   - Risk: Default credentials "admin"/"123456" trivial to compromise
   - Implementation time: 1 hour
   - Files: `app/routers/admin.py` (remove defaults, require env vars)

### Priority 2: Error Handling (Week 2)
**Deadline: Before merge to main** — These prevent silent failures

3. **Scheduler Task Exception Recovery** (6 tests)
   - Risk: One exception kills entire scheduler
   - Implementation time: 2 hours
   - Files: `app/services/scheduler.py` (wrap all tasks)

4. **Webhook Payload JSON Decode Errors** (6 tests)
   - Risk: Malformed payloads crash webhook endpoints
   - Implementation time: 1-2 hours
   - Files: `app/routers/webhooks.py` (add JSONDecodeError handler)

5. **Database OperationalError Retry Logic** (6 tests)
   - Risk: Transient DB errors silently fail; data loss silent
   - Implementation time: 2-3 hours
   - Files: `app/core/database.py` (implement exponential backoff)

### Priority 3: Config & DB (Week 3-4)
**Deadline: Code cleanup phase** — These prevent configuration errors

6. **Config Time-String Parsing** (6 tests)
   - Risk: Typos in config.json cause scheduler jobs to use wrong times
   - Implementation time: 1-2 hours
   - Files: `app/core/config.py` (validate at load time)

7. **Multi-Tenant User ID Validation** (7 tests)
   - Risk: Data leakage between users if user_id not enforced
   - Implementation time: 2-3 hours
   - Files: `app/core/database.py` (add @validate_user_id decorator)

8. **News Agent Configuration** (9 tests)
   - Risk: Config parsing errors cause silent failures
   - Implementation time: 1 hour
   - Files: `app/services/scheduler.py` (handle boolean variants)

---

## Test Structure

Each test follows the **AAA Pattern**:

```python
def test_example():
    # Arrange — Set up test data and mocks
    mock_service = MagicMock()
    mock_service.call.return_value = "success"
    
    # Act — Execute the code under test
    result = function_under_test(mock_service)
    
    # Assert — Verify the result
    self.assertEqual(result, "success")
    mock_service.call.assert_called_once()
```

---

## Mocking Patterns

All tests use `unittest.mock.patch` and `MagicMock` (already used in project):

```python
# Mock environment variables
@patch.dict("os.environ", {"VAR": "value"})
def test_something(self):
    ...

# Mock function
@patch("app.services.scheduler.generate_morning_briefing")
def test_something(self, mock_gen):
    mock_gen.return_value = "success"
    ...

# Mock exception
mock_service.side_effect = ValueError("Error")
```

---

## Coverage Target

After implementing all tests:

```bash
pytest tests/ --cov=app --cov-report=term-missing

# Expected:
# app/routers/webhooks.py       95%
# app/routers/admin.py          90%
# app/services/scheduler.py     92%
# app/core/database.py          88%
# app/core/config.py            85%
# ─────────────────────────────
# TOTAL                         89%
```

---

## File Locations

- **Test Specifications**: `/home/tinhn/repo/Personal_AI_OS/tests/test_untested_critical_paths.md`
- **This Guide**: `/home/tinhn/repo/Personal_AI_OS/docs/TEST_SPECIFICATIONS_GUIDE.md`
- **Existing Tests**: `/home/tinhn/repo/Personal_AI_OS/tests/test_*.py`
- **Modules Under Test**:
  - `/home/tinhn/repo/Personal_AI_OS/app/routers/webhooks.py`
  - `/home/tinhn/repo/Personal_AI_OS/app/routers/admin.py`
  - `/home/tinhn/repo/Personal_AI_OS/app/services/scheduler.py`
  - `/home/tinhn/repo/Personal_AI_OS/app/core/database.py`
  - `/home/tinhn/repo/Personal_AI_OS/app/core/config.py`

---

## Next Steps

1. **This week**: Copy Priority 1 tests and implement Strava signature validation
2. **Next week**: Implement admin credential validation + Priority 2 error handling
3. **Week 3**: Priority 3 config & database tests
4. **Week 4**: Refactor, optimize, verify 80%+ coverage

---

## Questions?

Refer back to the test specifications file for:
- Risk analysis for each path
- Implementation approach
- Edge cases to handle
- Expected log format
- Code review checks

All 45 tests are self-contained and can run independently.

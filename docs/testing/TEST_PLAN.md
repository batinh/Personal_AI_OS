# Test Plan — Personal AI OS

**Project:** Personal AI OS (Coach Dyno)  
**Version:** 2.0.0  
**Date:** 2026-03-23  
**Status:** Active

---

## 1. Test Objectives

| # | Objective |
|---|---|
| O1 | Verify all HTTP webhook endpoints correctly route events to background tasks |
| O2 | Verify Strava token caching avoids redundant refresh calls |
| O3 | Verify config auto-init, caching, and corruption resilience protect system stability |
| O4 | Verify cron harvest and manual sync correctly filter, deduplicate, and persist activities |
| O5 | Verify AI agent flows (chat, briefing, reflection, memory extraction) handle LLM errors gracefully |
| O6 | Verify database CRUD operations maintain data integrity and user isolation |
| O7 | Verify notification layer sanitizes HTML and handles Telegram API failures |
| O8 | Verify sports science calculations (TRIMP, ACWR, EF, Decoupling) are mathematically correct |

---

## 2. Test Schedule

| Phase | Description | Tests | Status |
|---|---|---|---|
| **Phase 1** | Foundation: DB, utils, streams, notifications | `test_database.py`, `test_utils.py`, `test_stream_storage.py`, `test_notification.py` | ✅ Complete |
| **Phase 2** | Agent flows: chat, briefing, reflection, memory | `test_agent.py`, `test_tools.py`, `test_tools_get_run_full_details.py` | ✅ Complete (partial failures) |
| **Phase 3** | Production flows: webhooks, strava client, config, harvest | `test_webhooks.py`, `test_strava_client.py`, `test_config.py`, `test_harvest.py` | ✅ Complete |
| **Phase 4** | Remaining gaps: admin, scheduler, concurrency | TBD | 🔲 Planned |

---

## 3. Test Environment

### Local Development
```
OS: Linux (Debian) on Lenovo T440 home lab
Python: 3.11
pytest: 9.0.2
Working directory: /home/tinhn/repo/Personal_AI_OS
```

### Docker (Production)
```
Image: python:3.11-slim (via Dockerfile)
Working directory: /app (inside container)
Volume mount: .:/app (entire project root)
DB path: /app/data/os_core.db
Config path: /app/data/config.json
```

### Key Environment Variables for Tests
```bash
# Automatically mocked in tests via @patch.dict — no real values needed
TELEGRAM_BOT_TOKEN=fake-token
TELEGRAM_CHAT_ID=12345
STRAVA_CLIENT_ID=123
STRAVA_CLIENT_SECRET=secret
STRAVA_REFRESH_TOKEN=refresh
STRAVA_ATHLETE_ID=99999
VERIFY_TOKEN=my-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=123456
GOOGLE_API_KEY=fake-key
```

---

## 4. Entry and Exit Criteria

### Entry Criteria (start testing a feature)
- Feature code is complete and pushed to branch
- All imports resolve without errors (`python -c "import app.main"`)
- Existing baseline tests still pass (no new failures in other modules)

### Exit Criteria (feature is testable / shippable)
- All tests for the feature PASS
- No regression in baseline tests
- Edge cases documented and tested (error paths, empty inputs, API failures)
- `python -m pytest tests/ -q` exits with code 0 on passing tests

---

## 5. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Strava API token expires mid-test | Low | Medium | Token caching tested; all tests use mocked `requests` |
| ChromaDB ONNX model download fails | Low | High | Stubbed in `conftest.py` — never downloads in tests |
| SQLite "database is locked" under concurrent test | Low | Medium | Each test uses isolated temp DB file |
| Gemini API returns unexpected JSON structure | Medium | High | Agent flows test invalid JSON paths explicitly |
| Config file deleted in production | Low | High | Auto-init from example tested in `test_config.py` |
| Duplicate Strava webhook events | Medium | Medium | Documented in `test_webhooks.py::TestDuplicateWebhookResilience` |

---

## 6. Test Deliverables

| Deliverable | Location | Description |
|---|---|---|
| Test Strategy | `docs/testing/TEST_STRATEGY.md` | Philosophy, tools, mocking strategy |
| Test Plan | `docs/testing/TEST_PLAN.md` | This document |
| Test Specs | `docs/testing/TEST_SPECS.md` | Full catalog of all test cases |
| Execution Report | `docs/testing/TEST_EXECUTION_REPORT.md` | Latest run results with known failures |
| Delivery Checklist | `docs/testing/DELIVERY_CHECKLIST.md` | Which tests to run per change type |

---

## 7. Roles and Responsibilities

| Role | Responsibility |
|---|---|
| Developer | Write unit tests alongside new features; run delivery checklist before pushing |
| AI Architect (Copilot) | Design integration tests; maintain test strategy; update execution report after changes |
| System Owner | Review test results before deploying to production Docker |

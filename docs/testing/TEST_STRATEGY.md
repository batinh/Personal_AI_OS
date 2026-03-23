# Test Strategy — Personal AI OS

**Project:** Personal AI OS (Coach Dyno)  
**Version:** 2.0.0  
**Last Updated:** 2026-03-23  
**Author:** QA Architect / Copilot

---

## 1. Purpose

This document defines the overall testing philosophy, scope, approach, and tooling for the Personal AI OS project. It serves as the single source of truth for all testing activities across the development lifecycle.

---

## 2. System Under Test

Personal AI OS is a **modular monolith AI agent system** running on FastAPI that:

- Ingests Strava running activity data via webhook
- Analyzes activities using Google Gemini AI
- Delivers personalized coaching via Telegram
- Persists structured data in SQLite and semantic memory in ChromaDB
- Runs scheduled background tasks (harvest, briefing, reflection, backup)

### External Dependencies (require mocking in tests)

| Dependency | Type | Risk |
|---|---|---|
| Google Gemini API | AI/LLM | 429 rate limit, 503 unavailable |
| Strava API | REST | 401 token expiry, 403 revoked, 429 rate limit |
| Telegram Bot API | REST | Network failure, HTML parse errors |
| OpenWeatherMap API | REST | Timeout, 401 invalid key |
| ChromaDB + ONNX | Local process | Initialization cost, disk corruption |
| SQLite | Local file | Lock contention (WAL mode active) |
| SMTP Server | Network | Connection refused, auth failure |

---

## 3. Test Scope

### 3.1 In Scope

| Area | Coverage Goal |
|---|---|
| HTTP Endpoints | All routes: POST/GET /webhook, /telegram-webhook, /admin/*, /dashboard |
| Background Flows | Strava workflow, delete cleanup, cron harvest, manual sync |
| Agent Flows | Morning briefing, weekly reflection, memory extraction, chat |
| Database Layer | All CRUD operations, UPSERT semantics, data isolation per user |
| Config Management | Load/save, caching, auto-init, corruption resilience |
| StravaClient | Token lifecycle, all API methods, error handling |
| Utilities | TRIMP, ACWR, efficiency factor, decoupling analysis |
| Notifications | Telegram HTML sanitization, SMTP, retry logic |
| Stream Storage | File I/O, path resolution, error handling |

### 3.2 Out of Scope

| Area | Reason |
|---|---|
| Admin UI HTML rendering | Template-level testing requires browser; covered by manual testing |
| ChromaDB vector search quality | Embedding model output is non-deterministic |
| Gemini AI response quality | LLM output quality is non-deterministic |
| SMTP delivery confirmation | End-to-end email delivery depends on external mail servers |
| Docker container build | Infrastructure concern, covered by deployment scripts |

---

## 4. Test Levels and Types

### 4.1 Test Pyramid

```
         ┌─────────────────────────────┐
         │     E2E / Manual Tests       │  ← Admin UI, Full production flow
         │        (few, slow)           │
         ├─────────────────────────────┤
         │     Integration Tests        │  ← HTTP endpoints, workflow orchestration
         │   (test_webhooks, agent)     │
         ├─────────────────────────────┤
         │       Service Tests          │  ← StravaClient, notifications, harvest
         │ (test_strava, notification)  │
         ├─────────────────────────────┤
         │      Unit Tests              │  ← DB CRUD, config, utils, stream storage
         │  (fast, many, isolated)      │
         └─────────────────────────────┘
```

### 4.2 Test Types Used

| Type | Description | Files |
|---|---|---|
| **Unit** | Single function, fully isolated, mocked I/O | `test_utils.py`, `test_config.py`, `test_stream_storage.py` |
| **Integration** | Multiple components wired together, external services mocked | `test_webhooks.py`, `test_agent.py`, `test_harvest.py` |
| **Component** | Single service with real DB (temp file), mocked network | `test_database.py`, `test_strava_client.py`, `test_notification.py` |
| **Contract** | Verify interface contracts between layers | Covered within integration tests |
| **Regression** | Full suite run on every change | `python -m pytest tests/` |

---

## 5. Testing Approach

### 5.1 Core Principles

1. **No external calls in tests** — All HTTP calls, file operations, and LLM calls must be mocked
2. **Deterministic results** — Tests must produce same result on every run, on any machine
3. **Fast feedback** — Full suite must complete in under 30 seconds
4. **Isolated state** — Each test class gets a fresh DB (via `_TempDbMixin`), no shared state between tests
5. **Zone compliance** — Test code in English (ZONE 1); test fixture data may include Vietnamese strings to test real payloads

### 5.2 Mocking Strategy

| Component | Strategy | Location |
|---|---|---|
| `google.genai` | `sys.modules` stub (prevents API init) | `conftest.py` |
| `chromadb` | `sys.modules` stub (prevents ONNX download) | `conftest.py` |
| `rag_memory` module | `sys.modules` replacement with `MagicMock` | `conftest.py` |
| `requests.post/get/put` | `@patch("module.requests.post")` per test | Individual tests |
| `database.DB_PATH` | `patch.object(database, "DB_PATH", tmp_file)` | `_TempDbMixin` |
| `os.environ` | `@patch.dict("os.environ", {...})` | Individual tests |
| `StravaClient` | `patch("module.StravaClient", return_value=mock_client)` | Integration tests |
| Gemini `client` | Module-level `_FAKE_GEMINI_CLIENT = MagicMock()` | `test_agent.py` |

### 5.3 Test Data Philosophy

- Use minimal, realistic data that mirrors real Strava/Telegram payloads
- Vietnamese strings in fixture data are intentional (test Zone 2 content handling)
- Activity IDs use realistic large integers (e.g., `12345678`)
- HR values within valid physiological range (60–200 bpm)
- Edge cases explicitly named: `test_*_returns_none`, `test_*_does_not_crash`

---

## 6. Tools and Infrastructure

| Tool | Version | Purpose |
|---|---|---|
| `pytest` | ≥ 7.0.0 | Test runner, discovery, reporting |
| `pytest-cov` | ≥ 4.0.0 | Code coverage measurement |
| `unittest.mock` | stdlib | Mocking, patching |
| `fastapi.testclient` | (FastAPI stdlib) | HTTP endpoint testing without server |
| `tempfile` | stdlib | Isolated temp DB files |

### Run Commands

```bash
# Full suite (regression)
python -m pytest tests/ -v

# Single file
python -m pytest tests/test_webhooks.py -v

# Single test
python -m pytest tests/test_webhooks.py::TestStravaWebhookVerification::test_valid_token_returns_challenge -v

# Coverage report
python -m pytest tests/ --cov=app --cov-report=html

# Fast pass (no verbose, just result)
python -m pytest tests/ -q
```

---

## 7. Known Test Gaps (Backlog)

| ID | Area | Description | Priority |
|---|---|---|---|
| T1-DB | Database | 14 pre-existing failures in test_agent.py + test_database.py due to import path issues after agent refactor | High |
| T2-E2E | End-to-end | No full production flow test (Strava webhook → Gemini → Telegram) with real message assertions | Medium |
| T3-Admin | Admin routes | No tests for /admin, /admin/save, /admin/toggle, /admin/test-email | Medium |
| T4-Dashboard | Dashboard | No tests for /dashboard rendering | Low |
| T5-Scheduler | Scheduler | No tests for cron schedule setup, reload, task_morning_briefing, task_weekly_reflection | Medium |
| T6-UserContext | User Context | No tests for get_primary_user_id() fallback logic | Low |
| T7-Concurrent | Concurrency | No stress test for concurrent webhook + harvest (SQLite lock contention) | Medium |

---

## 8. Quality Gates

### Pre-commit (Developer)
- [ ] New tests added for all new logic
- [ ] No new test failures introduced
- [ ] Code follows ZONE rules (English source, Vietnamese user-facing)

### Pre-delivery (CI Gate)
- [ ] `python -m pytest tests/ -q` exits with code 0 on all passing tests
- [ ] No regression from baseline (see `TEST_EXECUTION_REPORT.md`)
- [ ] All tests run in under 30 seconds

### Post-deployment (Production Verification)
- [ ] `/webhook` GET responds with challenge (Strava subscription verification)
- [ ] `/telegram-webhook` POST processes `/standup` command
- [ ] Admin UI loads at `/admin`
- [ ] Scheduler logs show cron jobs registered

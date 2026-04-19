# Architecture Refactoring Plan — Personal_AI_OS

**Date**: 2026-04-19  
**Auditor**: Principal Systems Architect (Claude Sonnet 4.6)  
**Scope**: Full codebase audit across 5 pillars  
**Status**: APPROVED — pending implementation

---

## Executive Summary

5 critical findings across the production codebase:

1. **CRITICAL**: `ThreadPoolExecutor` inside async FastAPI handlers blocks the event loop under any concurrent load
2. **CRITICAL**: Zero Gemini API timeouts — one slow call hangs the entire scheduler indefinitely
3. **HIGH**: Swallowed exceptions in 30+ locations — silent failures in backup, RAG, audit paths
4. **HIGH**: No graceful SIGTERM handling — Docker `docker stop` force-kills active tasks
5. **HIGH**: Coach agent is a 730-line god module — mixed concerns, untestable in isolation

---

## Pillar 1: Architecture & Folder Structure

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| HIGH | Coach agent god module (730+ lines, 6 mixed concerns: analysis, memory, briefing, chat, LLM calls, retry) | `app/agents/coach/agent.py` | 1–730 |
| HIGH | WebhookBackgroundTasks lost on container crash — no persistence guarantee | `app/routers/webhooks.py` | 150 |
| MEDIUM | 3 redundant prompt builders for news agent (DRY violation) | `app/agents/news/prompts.py` | — |
| MEDIUM | Copy-paste flow pattern repeated in 3 coach flows (config load, Gemini call, Telegram send) | `app/agents/coach/flows/` | — |

### Detail

**Coach Agent God Module**  
`app/agents/coach/agent.py` handles: run analysis, morning briefing, weekly reflection, chat, memory extraction, and message retry — all in one file. Any change to one function risks breaking others. Testing any single responsibility requires mocking the entire module.

**BackgroundTasks Data Loss**  
Strava webhook events use FastAPI `BackgroundTasks` (in-memory). On container crash mid-processing, the event is silently dropped. Strava does retry, but timing is unpredictable.

**Three News Prompt Builders**  
`build_news_system_instruction()`, `build_topic_system_instruction()`, `build_on_demand_system_instruction()` — each implements slightly different URL injection rules. A bug fix in one doesn't propagate to the others.

---

## Pillar 2: Performance & Bottlenecks

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | `ThreadPoolExecutor` inside async context blocks entire event loop | `app/agents/news/agent.py` | 23 |
| CRITICAL | No Gemini API timeout — slow call hangs scheduler indefinitely | All agent files | — |
| HIGH | Single-thread BackgroundScheduler: one long job blocks all subsequent cron jobs | `app/services/scheduler.py` | — |
| MEDIUM | Config cache (60s TTL) causes stale state when admin updates settings | `app/core/config.py` | 16 |
| MEDIUM | HTML chunking is O(n²) for large messages with deeply nested tags | `app/core/notification.py` | 77 |

### Detail

**ThreadPoolExecutor Blocking**  
```python
# app/agents/news/agent.py:23
with ThreadPoolExecutor(max_workers=_MAX_TOPIC_WORKERS) as executor:
    futures = [executor.submit(call_topic_gemini, topic) for topic in topics]
```
FastAPI is async. Spawning threads inside an async function doesn't yield the event loop — it blocks. Under 5+ concurrent users, event loop stalls waiting for all thread pool futures. Fix: use `asyncio.gather()` with async Gemini calls.

**No Gemini Timeout**  
All `client.models.generate_content()` calls have no `timeout` parameter. During Gemini API degradation, calls can hang for minutes. Since scheduler tasks are sync functions, a hung task blocks the entire scheduler thread — morning briefing blocking afternoon news is a real scenario.

**Scheduler Single Thread**  
APScheduler default: one thread for all jobs. If `task_morning_briefing()` takes 35 minutes, `task_news_briefing()` at 06:30 queues and waits. No timeout, no preemption, no alert.

---

## Pillar 3: Error Handling & Logging

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | 30+ `except Exception: pass` — silently swallows failures | Multiple | — |
| HIGH | No SIGTERM handler — Docker `stop` force-kills active Gemini calls after 10s | `app/main.py` | — |
| HIGH | Missing env var validation at startup — token/key errors discovered at first use | `app/core/user_context.py` | 10 |
| HIGH | 50-line in-memory log buffer — logs lost on container crash | `app/core/logging_conf.py` | 7 |

### Detail

**Swallowed Exceptions**  
Critical paths with silent failures:
- `app/services/backup.py:36` — backup fails, no log, workflow continues
- `app/core/notification.py:333` — typing action fails, no log
- `app/routers/webhooks.py:116` — RAG memorize fails, logged but not surfaced to caller

**No SIGTERM Handler**  
`docker stop` sends SIGTERM; container has 10s before SIGKILL. Currently: no signal handler → active Gemini call interrupted mid-response → partial database write possible. Scheduler tasks not cancelled cleanly.

**No Startup Validation**  
`GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` not validated on boot. A misconfigured deploy runs fine until the first scheduled job fails silently at 06:00.

---

## Pillar 4: Security

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | Telegram payload logged — token exposure risk if full payload logged | `app/core/notification.py` | 197 |
| HIGH | No webhook rate limiting — 10K fake Strava events → OOM | `app/routers/webhooks.py` | 143 |
| HIGH | No Strava HMAC signature verification — any caller can trigger analysis | `app/routers/webhooks.py` | 161 |
| MEDIUM | Admin UI missing CSRF protection | `app/routers/admin.py` | — |
| MEDIUM | `data/config.json` world-readable; contains internal settings | `data/config.json` | — |

### Detail

**Strava HMAC Missing**  
Strava signs webhook events with HMAC-SHA256. The current implementation only checks `hub.verify_token` on subscription verification — not on event delivery. Any HTTP client that knows the endpoint can trigger a full run analysis.

**Webhook Rate Limiting**  
No request throttling on `/webhook`. An attacker (or a Strava bug) can flood the endpoint → each event spawns a BackgroundTask → memory exhaustion. Fix: add `slowapi` rate limiting (5 req/min per IP).

---

## Pillar 5: Code Smells & Tech Debt

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | Scheduler deadlock: any job >N minutes blocks all subsequent cron jobs | `app/services/scheduler.py` | — |
| HIGH | Shared state counter not thread-safe under parallel sends | `app/core/state.py` | — |
| HIGH | `_inject_links_by_article()` has 5+ nesting levels | `app/agents/news/agent.py` | 147 |
| MEDIUM | 7+ magic numbers hardcoded without rationale | Multiple | — |
| MEDIUM | Background extraction threads dropped on SIGTERM (`daemon=True`) | `app/agents/news/memory.py` | 142 |

### Detail

**Magic Numbers**

| Value | Location | Should be |
|-------|----------|-----------|
| `maxlen=50` | `logging_conf.py:7` | `LOG_BUFFER_SIZE = 50` |
| `_CONFIG_CACHE_TTL = 60` | `config.py:16` | `CONFIG_CACHE_TTL_SECONDS = 60` |
| `_MAX_TOPIC_WORKERS = 4` | `news/agent.py:49` | `NEWS_MAX_PARALLEL_TOPICS = 4` |
| `4000` | `notification.py:190` | `TELEGRAM_MESSAGE_LIMIT = 4000` |

**Background Thread Data Loss**  
```python
# app/agents/news/memory.py:142
t = threading.Thread(target=extract_and_save_signals, daemon=True)
t.start()
```
`daemon=True` threads are killed immediately on shutdown — no wait, no graceful save. Memory extraction silently dropped.

---

## Refactoring Plan

### Safety Protocol (apply to every phase)

Before each change:
1. Write failing test that documents expected behavior
2. Implement change
3. Run `python -m pytest tests/test_smoke.py -v` (must pass)
4. Run `python -m pytest tests/ -q` (must pass)
5. Run `bash scripts/pre-deploy-check.sh`
6. Deploy: `bash scripts/deploy-t440.sh`
7. Monitor 24h: `bash scripts/fetch-logs.sh --live -l ERROR`
8. Rollback if needed: `bash scripts/rollback-t440.sh`

One change per commit. Never batch phases.

---

### Phase 1 — Critical Fixes (Week 1, ~7h)

**Risk**: LOW — all changes are additive or tighten existing behavior without altering logic.

#### P1.1 — Add Gemini Timeout + Retry (2h)

**Files**: `app/agents/news/agent.py`, `app/agents/coach/agent.py`  
**Change**: Add `timeout=30` to all `client.models.generate_content()` calls. Wrap in retry with exponential backoff (max 3 attempts).  
**Test**: Mock Gemini to raise `TimeoutError`; assert retry fires and logs warning.  
**Side effect risk**: None — adds protection without changing happy-path behavior.

```python
# Pattern to apply everywhere:
response = client.models.generate_content(
    model=model,
    contents=contents,
    config=config,
    # ADD:
    timeout=30,
)
```

#### P1.2 — Startup Environment Validation (1h)

**File**: `app/main.py` (lifespan startup block)  
**Change**: Validate required env vars exist and have correct format before app starts.  
**Test**: Set env var to empty string; assert startup raises `RuntimeError` with clear message.  
**Side effect risk**: None — only adds a fast-fail check at boot. Normal deploys unaffected.

```python
REQUIRED_ENV_VARS = [
    ("GOOGLE_API_KEY", lambda v: len(v) > 20),
    ("TELEGRAM_BOT_TOKEN", lambda v: ":" in v),
    ("TELEGRAM_CHAT_ID", lambda v: v.lstrip("-").isdigit()),
]
```

#### P1.3 — SIGTERM Graceful Shutdown (1h)

**File**: `app/main.py`  
**Change**: Register `signal.SIGTERM` handler in lifespan startup. Handler sets a shutdown event; scheduler checks it before starting new jobs.  
**Test**: Send SIGTERM to process; assert scheduler stops within 5s.  
**Side effect risk**: None — only adds handler, doesn't change normal operation.

#### P1.4 — Fix Swallowed Exceptions in Critical Paths (2h)

**Files**: `app/services/backup.py`, `app/routers/webhooks.py`, `app/services/log_auditor.py`  
**Change**: Replace `except Exception: pass` with `except Exception as e: logger.error("[MODULE] ...", exc_info=True)`. Do NOT change control flow.  
**Test**: Mock to raise exception; assert error is logged with stack trace.  
**Side effect risk**: Zero — only adds logging, doesn't change what code does next.

#### P1.5 — Webhook Rate Limiting (1h)

**File**: `app/routers/webhooks.py`  
**Change**: Add `slowapi` rate limiter: 10 req/min per IP on `/webhook` endpoint.  
**Test**: Send 11 requests in 60s; assert 12th returns 429.  
**Side effect risk**: None for normal Strava traffic (1–5 events/day).

---

### Phase 2 — Architecture (Week 2-3, ~26h)

**Risk**: MEDIUM — changes concurrency model and module boundaries. Each sub-item deployed independently.

#### P2.1 — Replace ThreadPoolExecutor with asyncio.gather (4h)

**File**: `app/agents/news/agent.py`  
**Change**: Make `call_topic_gemini()` async. Replace `ThreadPoolExecutor` with `asyncio.gather(*[call_topic_gemini(t) for t in topics])`.  
**Test**: Mock Gemini; assert all topics processed; assert no thread pool created.  
**Side effect risk**: MEDIUM. Concurrency model changes. Requires Gemini SDK to support async (verify first). Deploy to T440 and monitor scheduler for 48h.

#### P2.2 — Split Coach Agent into 4 Modules (8h)

**Files**: Extract from `app/agents/coach/agent.py` into:
- `app/agents/coach/analysis.py` — `analyze_run_with_gemini()`
- `app/agents/coach/briefing.py` — `generate_morning_briefing()`, `generate_weekly_reflection()`
- `app/agents/coach/memory.py` — `extract_implicit_memory()`
- `app/agents/coach/chat.py` — `handle_telegram_chat()`
- `app/agents/coach/agent.py` — thin orchestrator importing from above

**Test**: `test_smoke.py` import assertions must pass. All existing tests must pass unchanged.  
**Side effect risk**: LOW if done as pure moves (no logic changes). Import paths change — update all callers.

#### P2.3 — Unify News Prompt Builders (4h)

**File**: `app/agents/news/prompts.py`  
**Change**: Single `build_system_instruction(mode: Literal["briefing", "topic", "on_demand"])` with shared base + mode-specific additions.  
**Test**: Assert all 3 modes produce output containing required URL injection rules.  
**Side effect risk**: LOW — prompt content change only. Monitor first news briefing for quality.

#### P2.4 — Database-Backed Task Queue (8h)

**Files**: New `app/services/task_queue.py`, `app/core/database.py` (new table), `app/routers/webhooks.py`  
**Change**: Add `pending_tasks` table. Webhook writes event to DB instead of BackgroundTasks. Scheduler polls every 10s and processes pending tasks.  
**Test**: Integration test: write event to DB, assert processed within 15s.  
**Side effect risk**: HIGH. Critical path change. Deploy last in Phase 2. Keep BackgroundTasks as fallback for 1 week.

#### P2.5 — Request ID Propagation (2h)

**File**: `app/core/logging_conf.py`  
**Change**: Add `contextvars.ContextVar("request_id")`. Set on each request via middleware. Include in all log records.  
**Test**: Assert log output includes `request_id` field.  
**Side effect risk**: None — additive only.

---

### Phase 3 — Code Quality (Week 3-4, ~12h)

**Risk**: LOW — no behavior changes, only structural cleanup.

#### P3.1 — Extract Shared Coach Flow Pattern (4h)

**Files**: `app/agents/coach/flows/`  
**Change**: Extract `run_coach_flow(prompt_builder, user_id, config)` helper that handles: config load → Gemini call → Telegram send → error handling. Each flow calls this helper.  
**Test**: Mock Gemini; assert all 3 flows use shared error handling path.

#### P3.2 — Replace Magic Numbers with Named Constants (2h)

**Files**: `app/core/logging_conf.py`, `app/core/config.py`, `app/agents/news/agent.py`, `app/core/notification.py`  
**Change**: Extract all magic numbers to named constants at module top.

#### P3.3 — Structured JSON Logging (4h)

**File**: `app/core/logging_conf.py`  
**Change**: Add `structlog` formatter that outputs JSON when `LOG_FORMAT=json` env var set. Default stays plain-text (no breaking change).  
**Test**: Set `LOG_FORMAT=json`; assert log output is valid JSON with `level`, `message`, `timestamp` keys.

#### P3.4 — Strava HMAC Signature Verification (2h)

**File**: `app/routers/webhooks.py`  
**Change**: Verify `X-Hub-Signature` header on POST events using `STRAVA_WEBHOOK_SECRET`. Return 403 if missing or invalid.  
**Test**: Send request without header; assert 403. Send with valid header; assert 200.  
**Side effect risk**: LOW — Strava always sends signature. Verify env var is set before deploying.

---

### Phase 4 — Scalability (Post-MVP, no timeline)

| Change | Effort | Priority |
|--------|--------|----------|
| Batch Gemini API calls for multi-topic news (reduce cost 20-30%) | M | P3 |
| Redis cache for frequently accessed config + user data | L | P3 |
| Circuit breaker for Strava/Gemini API (graceful degradation) | M | P3 |
| Scheduler job timeout at APScheduler level | S | P2 |
| PostgreSQL migration (only if multi-user required) | L | P3 |

---

## Implementation Checklist

### Phase 1
- [ ] P1.1 Gemini timeout + retry
- [ ] P1.2 Startup env validation
- [ ] P1.3 SIGTERM signal handler
- [ ] P1.4 Fix swallowed exceptions
- [ ] P1.5 Webhook rate limiting

### Phase 2
- [ ] P2.1 asyncio.gather for news topics
- [ ] P2.2 Split coach agent into 4 modules
- [ ] P2.3 Unify news prompt builders
- [ ] P2.4 Database-backed task queue
- [ ] P2.5 Request ID in logs

### Phase 3
- [ ] P3.1 Shared coach flow helper
- [ ] P3.2 Named constants for magic numbers
- [ ] P3.3 Structured JSON logging
- [ ] P3.4 Strava HMAC verification

---

## Risk Matrix

| Change | Rollback Risk | Monitoring Signal | Rollback Trigger |
|--------|--------------|-------------------|-----------------|
| P1.1 Gemini timeout | None | `[TIMEOUT]` log entries | >5 timeouts/day |
| P1.2 Env validation | None (additive) | App fails to start | Any startup error |
| P1.3 SIGTERM handler | None | Clean shutdown logs | Handler not called |
| P2.1 asyncio.gather | Medium | News briefing quality | Missing topics in output |
| P2.2 Coach split | Low | Smoke tests pass | Any import error |
| P2.4 Task queue | High | Webhook processing time | Events not processed |

---

## Key Architecture Decisions (Open)

### ADR-001: BackgroundTasks vs Persistent Queue
**Decision needed**: Keep in-memory BackgroundTasks (simple, data loss risk) or add DB-backed queue (reliable, more code).  
**Recommendation**: DB-backed queue using existing SQLite `pending_tasks` table. No new dependencies.

### ADR-002: Scheduler Timeout Strategy
**Decision needed**: APScheduler job timeout at framework level, or application-level timeout per function.  
**Recommendation**: Wrap each scheduler task in `asyncio.wait_for(coro, timeout=1800)` (30 min max).

### ADR-003: Logging Format
**Decision needed**: Plain text (current, readable) vs JSON (structured, parseable).  
**Recommendation**: JSON behind env var flag. Default stays plain text until ELK/Loki is set up.

---

*Generated from full codebase audit. Update this file as items are completed.*

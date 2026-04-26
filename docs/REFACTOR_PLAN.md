# Architecture Refactoring Plan — Personal_AI_OS (v2)

**Date**: 2026-04-20 (v3)
**Auditors**:
- v1 — Principal Systems Architect (Claude Sonnet 4.6)
- v2 — Independent review + extension (Claude Opus 4.7)
- v3 — 3-agent orchestration: architect + code-reviewer + tdd-guide (Claude Opus 4.7, 2026-04-20)

**Scope**: Full codebase audit across 5 pillars + verification of v1/v2 findings + deep security/error-handling review + TDD gap analysis.
**Status**: APPROVED — pending implementation. **Stability, predictability, documentation, and zero negative side effects are non-negotiable.**

---

## Executive Summary

5 critical findings across the production codebase (verified against current code 2026-04-19):

1. **CRITICAL**: `ThreadPoolExecutor` inside async FastAPI handlers blocks the event loop under any concurrent load
2. **CRITICAL**: Zero Gemini API timeouts — one slow call hangs the entire scheduler indefinitely
3. **HIGH**: Swallowed/under-logged exceptions in critical paths — silent failures in backup, RAG, audit
4. **HIGH**: No graceful SIGTERM handling — Docker `docker stop` force-kills active tasks
5. **HIGH**: Coach agent is a 730-line god module — mixed concerns, untestable in isolation

**v2 additions** (gaps Sonnet missed):

6. **MEDIUM**: No baseline metrics — improvements cannot be measured or verified
7. **MEDIUM**: SQLite per-query connection (no pool); lock contention risk under load
8. **MEDIUM**: Test infrastructure not pinned in `requirements.txt` (relies on system pytest)
9. **MEDIUM**: Docker runs as root, no `tini` for PID 1 — SIGTERM doesn't propagate cleanly
10. **MEDIUM**: No log scrubbing filter — single bad `logger.info(config)` leaks tokens

**v3 additions** (3-agent orchestration, 2026-04-20):

11. **CRITICAL**: Admin credentials default to `"admin"/"123456"` if env vars unset — hardcoded in routers
12. **CRITICAL**: ALL scheduler tasks (`task_morning_briefing`, `task_auto_harvest`, etc.) have ZERO exception handling — one crash silently kills the job, no recovery
13. **HIGH**: `analyze_run_with_gemini()` defined in BOTH `agent.py` and `flows/run_analysis.py` — DRY violation, bug fixes don't propagate
14. **HIGH**: `send_message_with_retry()` defined in BOTH `agent.py` and `utils.py` — same issue
15. **HIGH**: No timeout on Strava/Telegram HTTP calls (`requests.get/post` in strava_client, notification.py) — hangs indefinitely on API degradation
16. **MEDIUM**: Config cache `_config_cache = {}` invalidation not thread-safe — race condition under concurrent requests
17. **MEDIUM**: `await request.json()` in both webhook endpoints not wrapped in `JSONDecodeError` catch — returns 500 instead of 400
18. **MEDIUM**: Partial failure rollback incomplete in `harvest._ingest_one_activity()` — step 5 (RAG) runs even if step 3 (fetch detail) failed, creating DB/RAG inconsistency
19. **LOW**: Timezone string `os.getenv("TZ", "Asia/Ho_Chi_Minh")` duplicated 5+ times across modules — should be `get_timezone()` in `app/core/timezone_utils.py`

**Test gap (TDD agent)**: 45 concrete test stubs generated for 8 untested critical paths → `tests/test_untested_critical_paths.md`

---

## Verification Matrix (v1 findings re-checked against current code)

| v1 Finding | File:Line | Status |
|------------|-----------|--------|
| ThreadPoolExecutor blocks event loop | `app/agents/news/agent.py:510` | **CONFIRMED** |
| No Gemini timeout | `app/agents/news/agent.py:336` and all `generate_content()` callers | **CONFIRMED** |
| Coach god module | `app/agents/coach/agent.py` (730 lines) | **CONFIRMED** (partial mitigation: prompts/tools already extracted) |
| BackgroundTasks data loss | `app/routers/webhooks.py:151,155` | **CONFIRMED** |
| 30+ swallowed exceptions | multiple | **PARTIAL** — some now log; ~15 still silent |
| No SIGTERM handler | `app/main.py:27–58` | **CONFIRMED** |
| No startup env validation | `app/main.py:31–46` | **CONFIRMED** |
| No webhook rate limit | `app/routers/webhooks.py:142` | **CONFIRMED** |
| No Strava HMAC verification | `app/routers/webhooks.py:160–163` | **CONFIRMED** (only `hub.verify_token` on GET) |
| Daemon threads dropped | `app/agents/news/memory.py:147` | **CONFIRMED** |
| Magic numbers | multiple | **CONFIRMED** |

**Verdict**: 95% accurate. All critical/high findings are real production issues.

---

## Pillar 1: Architecture & Folder Structure

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| HIGH | Coach agent god module (730+ lines, 6 mixed concerns) | `app/agents/coach/agent.py` | 1–730 |
| HIGH | WebhookBackgroundTasks lost on container crash — no persistence guarantee | `app/routers/webhooks.py` | 150 |
| MEDIUM | 3 redundant prompt builders for news agent (DRY violation) | `app/agents/news/prompts.py` | — |
| MEDIUM | Copy-paste flow pattern repeated in 3 coach flows | `app/agents/coach/flows/` | — |
| HIGH **(v3)** | `analyze_run_with_gemini()` defined in both `agent.py:192` and `flows/run_analysis.py:35` | `app/agents/coach/agent.py` | 192 |
| HIGH **(v3)** | `send_message_with_retry()` defined in both `agent.py:54` and `utils.py:358` | `app/agents/coach/agent.py` | 54 |
| MEDIUM **(v3)** | Partial-failure rollback incomplete in `_ingest_one_activity()` — RAG writes even when fetch fails | `app/agents/coach/harvest.py` | 57 |

**Coach Agent God Module** — Handles run analysis, morning briefing, weekly reflection, chat, memory extraction, and message retry — all in one file. Any change risks breaking unrelated flows. Untestable in isolation.

**BackgroundTasks Data Loss** — Strava webhook events use FastAPI in-memory `BackgroundTasks`. On container crash mid-processing, the event is silently dropped. Strava retry timing is unpredictable.

**Three News Prompt Builders** — `build_news_system_instruction()`, `build_topic_system_instruction()`, `build_on_demand_system_instruction()` each implement slightly different URL injection rules. A bug fix in one doesn't propagate.

---

## Pillar 2: Performance & Bottlenecks

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | `ThreadPoolExecutor` inside async context blocks event loop | `app/agents/news/agent.py` | 510 |
| CRITICAL | No Gemini API timeout — slow call hangs scheduler indefinitely | All agent files | — |
| HIGH | Single-thread BackgroundScheduler: one long job blocks all subsequent cron jobs | `app/services/scheduler.py` | — |
| HIGH **(v2)** | SQLite connection opened per query; no pool, lock contention risk | `app/core/database.py` | 20 |
| HIGH **(v3)** | No timeout on `requests.get/post` in Strava client and notification.py — hangs indefinitely | `app/agents/coach/strava_client.py:48,70` | 48 |
| MEDIUM | Config cache (60s TTL) causes stale state when admin updates settings | `app/core/config.py` | 16 |
| MEDIUM **(v3)** | `_config_cache = {}` invalidation not thread-safe — race condition under concurrent load | `app/core/config.py` | 18 |
| MEDIUM | HTML chunking is O(n²) for large messages with deeply nested tags | `app/core/notification.py` | 77 |

**ThreadPoolExecutor Blocking** — `app/agents/news/agent.py:510`:
```python
with ThreadPoolExecutor(max_workers=_MAX_TOPIC_WORKERS) as executor:
    futures = [executor.submit(call_topic_gemini, topic) for topic in topics]
```
Spawning threads inside async doesn't yield the event loop. Under 5+ concurrent users, event loop stalls. Fix: `asyncio.gather()` with `client.aio.models.generate_content()` (async API confirmed available in `google-genai>=0.3`).

**No Gemini Timeout** — Any call hangs indefinitely during API degradation. Scheduler thread blocked → morning briefing blocking afternoon news is a real scenario.

**Scheduler Single Thread** — APScheduler default. If `task_morning_briefing()` takes 35 minutes, `task_news_briefing()` queues. No timeout, no preemption, no alert.

**SQLite Connection Per Query (v2)** — `app/core/database.py:20` opens a fresh connection for each query. Under concurrent webhook + scheduler load, "database is locked" errors likely. WAL mode is set per-connection (correct), but no pool.

---

## Pillar 3: Error Handling & Logging

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | ~15 `except Exception: pass` — silently swallows failures | Multiple | — |
| CRITICAL **(v3)** | ALL scheduler tasks (8 functions) have ZERO try/except — one crash silently kills the job | `app/services/scheduler.py` | 29,49,58,78,85,92,102,149 |
| HIGH | No SIGTERM handler — Docker `stop` force-kills active Gemini calls after 10s | `app/main.py` | — |
| HIGH | Missing env var validation at startup | `app/core/user_context.py` | 10 |
| HIGH | 50-line in-memory log buffer — logs lost on container crash | `app/core/logging_conf.py` | 7 |
| HIGH **(v3)** | Scheduler `setup_jobs()` bare `except Exception: pass` on time parsing — silently uses wrong schedule | `app/services/scheduler.py` | 172,178,198 |
| MEDIUM **(v2)** | No baseline metrics — improvements unverifiable | — | — |
| MEDIUM **(v2)** | No log scrubbing filter — risk of secret leak via misplaced log | `app/core/logging_conf.py` | — |
| MEDIUM **(v3)** | `await request.json()` not wrapped — `JSONDecodeError` returns 500, not 400 | `app/routers/webhooks.py` | 144,168 |

**Swallowed Exceptions** — Critical paths with silent failures: `app/services/backup.py:36`, `app/core/notification.py:333`, `app/routers/webhooks.py:116` (logged but not surfaced).

**No SIGTERM Handler** — `docker stop` sends SIGTERM; container has 10s before SIGKILL. Currently no signal handler → active Gemini call interrupted mid-response → partial DB write possible.

**No Startup Validation** — `GOOGLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` not validated on boot. Misconfigured deploy runs fine until 06:00 first scheduled job fails silently.

**No Baseline (v2)** — Without measured baseline (`error_rate_pct`, `p99_latency`, `timeout_count_per_day`), every refactor is shipped blind. Cannot prove improvement, cannot detect regression.

---

## Pillar 4: Security

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | Telegram payload risk — token exposure if full payload accidentally logged | `app/core/notification.py` | 197 |
| HIGH | No webhook rate limiting — flood → BackgroundTask explosion → OOM | `app/routers/webhooks.py` | 143 |
| HIGH | No Strava HMAC signature verification on POST events | `app/routers/webhooks.py` | 161 |
| CRITICAL **(v3)** | Admin credentials default to hardcoded `"admin"/"123456"` if env vars unset | `app/routers/admin.py:28`, `console.py:46`, `audit.py:31` | 28 |
| HIGH **(v3)** | No input schema validation on Strava webhook payload — `object_id` not validated as positive int | `app/routers/webhooks.py` | 146 |
| MEDIUM | Admin UI missing CSRF protection | `app/routers/admin.py` | — |
| MEDIUM | `data/config.json` world-readable; contains internal settings | `data/config.json` | — |
| MEDIUM **(v2)** | Docker runs as root; no `tini` for PID 1 SIGTERM forwarding | `Dockerfile` | — |

**Strava HMAC Missing** — Only `hub.verify_token` checked on subscription verification. POST event delivery accepts ANY caller — full run analysis can be triggered by anyone who knows the URL.

**Webhook Rate Limiting** — No throttling on `/webhook`. Flood → each event spawns BackgroundTask → memory exhaustion. Fix: `slowapi` 10 req/min per IP.

**Docker Hardening (v2)** — Dockerfile has no `USER appuser` (runs as root inside container). No `tini` as PID 1 — SIGTERM doesn't propagate cleanly to Python child, zombies possible.

---

## Pillar 5: Code Smells & Tech Debt

### Findings

| Severity | Finding | File | Line |
|----------|---------|------|------|
| CRITICAL | Scheduler deadlock: any job >N minutes blocks all subsequent cron jobs | `app/services/scheduler.py` | — |
| HIGH | Shared state counter not thread-safe under parallel sends | `app/core/state.py` | 16–21 |
| HIGH | `_inject_links_by_article()` has 5+ nesting levels | `app/agents/news/agent.py` | 147 |
| MEDIUM | 7+ magic numbers hardcoded without rationale | Multiple | — |
| MEDIUM | Background extraction threads dropped on SIGTERM (`daemon=True`) | `app/agents/news/memory.py` | 142 |
| MEDIUM **(v2)** | `requirements.txt` missing pinned `pytest`, `pytest-cov`, `pytest-asyncio` | `requirements.txt` | — |
| MEDIUM **(v3)** | Missing type hints on public functions — `split_html_preserving_tags`, `memorize`, etc. | `app/core/notification.py:77`, `app/agents/news/agent.py:125` | — |
| LOW **(v3)** | Timezone `os.getenv("TZ","Asia/Ho_Chi_Minh")` copy-pasted 5+ times — extract to `timezone_utils.py` | Multiple | — |

**Magic Numbers**

| Value | Location | Should be |
|-------|----------|-----------|
| `maxlen=50` | `logging_conf.py:7` | `LOG_BUFFER_SIZE = 50` |
| `_CONFIG_CACHE_TTL = 60` | `config.py:16` | `CONFIG_CACHE_TTL_SECONDS = 60` (public) |
| `_MAX_TOPIC_WORKERS = 4` | `news/agent.py:49` | `NEWS_MAX_PARALLEL_TOPICS = 4` |
| `4000` | `notification.py:190` | `TELEGRAM_MESSAGE_LIMIT = 4000` |

**Background Thread Data Loss** — `daemon=True` threads killed immediately on shutdown. Memory extraction silently dropped.

---

## Refactoring Plan

### Safety Protocol (apply to every phase — v2 hardened)

Before each change:
1. Write failing test that documents expected behavior
2. Implement change (single P-item per commit, never batch)
3. `python -m pytest tests/test_smoke.py -v` (must pass)
4. `python -m pytest tests/ -q` (must pass)
5. `bash scripts/pre-deploy-check.sh`
6. **Snapshot rollback image**: `docker commit airunningcoach airunningcoach:pre-PX.Y`
7. Deploy: `bash scripts/deploy-t440.sh`
8. Monitor 24h: `bash scripts/fetch-logs.sh --live -l ERROR`
9. **Compare to baseline**: rollback if `error_rate > baseline+15%` OR `p99_latency > 10s` sustained 1h
10. Rollback if needed: `bash scripts/rollback-t440.sh`
11. Update `docs/RUNBOOK.md` (troubleshooting) and close entry in `docs/ISSUES.md`
12. Update `CLAUDE.md` File Map if new module created

**One change per commit. Never batch phases.**

**SDK Parameter Audit** (mandatory for any phase touching third-party SDK calls):
- Before adding a numeric parameter to an SDK call, verify the unit from source:
  `python3 -c "import pathlib, sys; [print((pathlib.Path(p)/'google'/'genai'/'types.py').read_text()) for p in sys.path if (pathlib.Path(p)/'google'/'genai'/'types.py').exists()][:1]"`
- Confirm: seconds vs milliseconds vs bytes — from source, not just docs.
- After adding, run `python -m pytest tests/test_sdk_contracts.py -v` to gate the value.

---

### Phase 0 — Baseline + Hardening (NEW, ~4h)

**Risk**: ZERO — purely additive, no behavior change.

#### P0.1 — Pin test dependencies (15min)

**File**: `requirements.txt`
**Change**: Add `pytest>=8.0`, `pytest-cov>=5.0`, `pytest-asyncio>=0.23` with version pins.
**Why**: Currently relies on system pytest — non-reproducible CI/local mismatches.
**Side effect risk**: None.

#### P0.2 — Latency + error counters (1.5h)

**Files**: `app/core/metrics.py` (NEW), `app/agents/news/agent.py`, `app/agents/coach/agent.py`, `app/routers/health.py`
**Change**: Wrap all `generate_content()` calls in a timer; expose `/health/metrics` (text plain) with `gemini_call_count`, `gemini_error_count`, `gemini_latency_p50/p99`. In-memory counters only, no Prometheus dependency.
**Test**: Mock Gemini; assert metrics increment correctly.
**Side effect risk**: None — passive observation.

#### P0.3 — Docker hardening (1h)

**File**: `Dockerfile`
**Change**:
- Add `RUN useradd --uid 1000 appuser && chown -R appuser /app`
- Add `USER appuser`
- Install `tini` and set `ENTRYPOINT ["/usr/bin/tini", "--"]`
- Increase HEALTHCHECK timeout from 10s to 30s (Gemini cold start)
**Test**: `docker compose up --build`; assert container runs, health passes, `docker stop` exits cleanly within 5s.
**Side effect risk**: LOW — must verify file permissions on `data/`, `logs/` volumes. Test in dev first.

#### P0.4 — Capture 24h baseline (passive)

**Action**: Deploy P0.1–P0.3, run 24h, collect:
- `error_rate_pct` (errors / total log lines)
- `gemini_p50` / `gemini_p99` latency (ms)
- `timeout_count_per_day`
- `webhook_request_count_per_hour`

#### P0.5 — Document baselines (30min)

**File**: `docs/RUNBOOK.md`
**Change**: Add `## Baseline Metrics (2026-04-20)` section with the numbers from P0.4. Used as regression tripwire for all subsequent phases.

#### P0.6 — Harden Admin Credentials (30min) **(NEW v3 — CRITICAL)**

**Files**: `app/routers/admin.py:28`, `app/routers/console.py:46`, `app/routers/audit.py:31`
**Change**: Remove hardcoded `"admin"/"123456"` defaults. Fail-fast at app startup if `ADMIN_USERNAME` or `ADMIN_PASSWORD` not set (min 12 chars).
```python
# BEFORE (insecure)
env_user = os.getenv("ADMIN_USERNAME", "admin")
env_pass = os.getenv("ADMIN_PASSWORD", "123456")
# AFTER
env_user = os.getenv("ADMIN_USERNAME")
env_pass = os.getenv("ADMIN_PASSWORD")
if not env_user or not env_pass or len(env_pass) < 12:
    raise ValueError("ADMIN_USERNAME and ADMIN_PASSWORD (min 12 chars) must be set")
```
**Test**: `test_admin_credentials.py` — missing env → ValueError; correct creds → 200; wrong creds → 401.
**Side effect risk**: NONE for correctly deployed instances. **Must set env vars before deploying.**

#### P0.7 — Wrap ALL Scheduler Tasks in try/except (1h) **(NEW v3 — CRITICAL)**

**File**: `app/services/scheduler.py`
**Change**: 8 task functions (`task_morning_briefing`, `task_auto_harvest`, `task_weekly_reflection`, `task_morning_news`, `task_afternoon_news`, `task_evening_news`, `task_proactive_coach_check`, `task_log_audit`) have ZERO exception handling. Wrap each in:
```python
def task_morning_briefing():
    try:
        # existing code
    except Exception as e:
        logger.error("[SCHEDULER] task_morning_briefing failed: %s", e, exc_info=True)
```
Also fix `setup_jobs()` bare `except Exception: pass` at lines 172, 178, 198 — add `logger.warning()` with the malformed value.
**Test**: Mock task body to raise; assert error logged, scheduler keeps running.
**Side effect risk**: ZERO — only adds logging + prevents silent crash propagation.

---

### Phase 1 — Critical Fixes (Week 1, ~7h)

**Risk**: LOW — additive or tightening, no logic change.

#### P1.1 — Add Gemini Timeout + Retry (2h)

**Files**: `app/agents/news/agent.py`, `app/agents/coach/agent.py`
**Change**: Use SDK `timeout=30` parameter on `client.models.generate_content()` calls (NOT a custom sync wrapper — keeps async-compatible for P2.1). Wrap in retry with exponential backoff (max 3 attempts).
**Test**: Mock to raise `TimeoutError`; assert retry fires + log warning.
**Side effect risk**: None — additive protection.

```python
response = client.models.generate_content(
    model=model,
    contents=contents,
    config=config,
    timeout=30,  # ADD
)
```

#### P1.2 — Startup Environment Validation (1h)

**Files**: `app/core/config.py` (module load), `app/main.py` (lifespan)
**Change**: Validate required env vars at module import time (fail-fast, not at lifespan). Belt-and-suspenders: also re-check at lifespan startup.
**Test**: Empty env var → `RuntimeError` with clear message at import.
**Side effect risk**: None.

```python
REQUIRED_ENV_VARS = [
    ("GOOGLE_API_KEY", lambda v: len(v) > 20),
    ("TELEGRAM_BOT_TOKEN", lambda v: ":" in v),
    ("TELEGRAM_CHAT_ID", lambda v: v.lstrip("-").isdigit()),
]
```

#### P1.3 — SIGTERM Graceful Shutdown (1h)

**File**: `app/main.py`
**Change**: Register `signal.SIGTERM` handler in lifespan startup. Handler sets shutdown event; scheduler checks before starting new jobs.
**Test**: SIGTERM → scheduler stops within 5s; in-flight jobs allowed up to 25s to drain.
**Side effect risk**: None.

#### P1.4 — Fix Swallowed Exceptions in Critical Paths (2h)

**Files**: `app/services/backup.py`, `app/routers/webhooks.py`, `app/services/log_auditor.py`, `app/core/notification.py`
**Change**: Replace `except Exception: pass` with `except Exception: logger.error("[MODULE] ...", exc_info=True)`. Do NOT change control flow.
**Test**: Mock to raise exception; assert error logged with stack trace.
**Side effect risk**: ZERO — only adds logging.

#### P1.5 — Webhook Rate Limiting (1h)

**File**: `app/routers/webhooks.py`
**Change**: Add `slowapi` rate limiter: 10 req/min per IP on `/webhook`.
**Test**: 11 requests in 60s → 12th returns 429.
**Side effect risk**: None for normal Strava traffic (1–5 events/day).

#### P1.6 — Add HTTP Timeouts on All External Calls (1h) **(NEW v3)**

**Files**: `app/agents/coach/strava_client.py`, `app/core/notification.py`
**Change**: Every `requests.get()` and `requests.post()` call missing `timeout=` parameter. Add `timeout=10` (Strava) and `timeout=15` (Telegram, higher for file uploads).
**Test**: Mock `requests` to raise `Timeout`; assert caller logs error and returns gracefully.
**Side effect risk**: LOW — if a legitimate call takes >10s (unlikely), it now fails fast with a retry.

#### P1.7 — Strava Webhook Pydantic Schema + JSONDecodeError (1h) **(NEW v3)**

**File**: `app/routers/webhooks.py`
**Change**:
1. Add `StravaWebhook(BaseModel)` with `object_id: int = Field(..., gt=0)`, `object_type: str`.
2. Wrap `await request.json()` in `try/except json.JSONDecodeError` → return 400.
**Test**: Invalid JSON → 400. `object_id=-1` → 422. Valid → 200.
**Side effect risk**: LOW — validates what Strava actually sends; no legitimate events should fail.

---

### Phase 2 — Architecture (Week 2-3, ~26h)

**Risk**: MEDIUM — changes concurrency model and module boundaries. Each item deployed independently.

#### P2.1 — Replace ThreadPoolExecutor with asyncio.gather (4h)

**File**: `app/agents/news/agent.py`
**Pre-req**: P1.1 done (timeouts in place); confirm `client.aio.models.generate_content()` exists in installed `google-genai` version.
**Change**: Make `call_topic_gemini()` async. Replace `ThreadPoolExecutor` with `asyncio.gather(*[call_topic_gemini(t) for t in topics])`.
**Test**: Mock; assert all topics processed; assert no thread pool created.
**Side effect risk**: MEDIUM. Concurrency model changes. Deploy + monitor scheduler 48h.
**Rollback trigger**: Missing topics in news output OR scheduler latency increase >20%.

#### P2.1b — Eliminate Duplicate Function Definitions (2h) **(NEW v3)**

**Files**: `app/agents/coach/agent.py`, `app/agents/coach/flows/run_analysis.py`, `app/agents/coach/utils.py`
**Change**:
- `analyze_run_with_gemini()`: canonical location → `flows/run_analysis.py`. Remove from `agent.py`. Update all callers to import from `flows.run_analysis`.
- `send_message_with_retry()`: canonical location → `utils.py`. Remove from `agent.py`. Verify all callers already use `utils`.
**Test**: Smoke test imports pass. Existing tests pass unchanged.
**Side effect risk**: LOW — pure import path change. Do NOT change any logic during this move.

#### P2.2 — Split Coach Agent into 4 Modules (8h)

**Extract from** `app/agents/coach/agent.py` into:
- `app/agents/coach/analysis.py` — `analyze_run_with_gemini()`
- `app/agents/coach/briefing.py` — `generate_morning_briefing()`, `generate_weekly_reflection()`
- `app/agents/coach/memory.py` — `extract_implicit_memory()`
- `app/agents/coach/chat.py` — `handle_telegram_chat()`
- `app/agents/coach/agent.py` — thin orchestrator importing from above

**Test**: `test_smoke.py` import assertions pass. All existing tests pass unchanged.
**Side effect risk**: LOW if pure moves (no logic changes). Update all import paths.
**Doc**: Update `CLAUDE.md` File Map; create `docs/features/coach-split.md`.

#### P2.3 — Unify News Prompt Builders (4h)

**File**: `app/agents/news/prompts.py`
**Change**: Single `build_system_instruction(mode: Literal["briefing", "topic", "on_demand"])` with shared base + mode-specific additions.
**Test**: All 3 modes produce output containing required URL injection rules.
**Side effect risk**: LOW — content change. Monitor first news briefing for quality.

#### P2.4 — Database-Backed Task Queue (8h)

**Files**: `app/services/task_queue.py` (NEW), `app/core/database.py` (new table), `app/routers/webhooks.py`
**Change**: Add `pending_tasks` table (`id UUID PRIMARY KEY, type, payload, status, created_at`). Webhook writes to DB; scheduler polls every 10s.
**Rollout** (3 stages, ~1 week each):
1. **Shadow mode** (config flag `use_database_queue=false`): write to BOTH DB and BackgroundTasks; only BackgroundTasks processes. Verify DB writes succeed, no duplicates.
2. **Cutover** (`use_database_queue=true`): DB processes; BackgroundTasks logs warning but doesn't process.
3. **Cleanup**: remove BackgroundTasks code after 1 week stable.
**Test**: Integration test — write event, assert processed within 15s. Crash mid-process → resume after restart.
**Side effect risk**: HIGH. Critical path. Strict 3-stage rollout. UUID `task_id` prevents duplicates.
**Doc**: `docs/features/db-task-queue.md` design doc + RUNBOOK queue ops section.

#### P2.5 — Request ID Propagation (2h)

**File**: `app/core/logging_conf.py`
**Change**: `contextvars.ContextVar("request_id")`. Set on each request via middleware. Include in all log records.
**Test**: Log output includes `request_id` field.
**Side effect risk**: None — additive.

---

### Phase 3 — Code Quality (Week 3-4, ~14h)

**Risk**: LOW — no behavior changes, only structural cleanup.

#### P3.1 — Extract Shared Coach Flow Pattern (4h)

**Files**: `app/agents/coach/flows/`
**Change**: Extract `run_coach_flow(prompt_builder, user_id, config)` helper handling: config load → Gemini call → Telegram send → error handling. Each flow calls helper.
**Test**: Mock Gemini; assert all 3 flows use shared error path.

#### P3.2 — Replace Magic Numbers with Named Constants (2h)

**Files**: `app/core/logging_conf.py`, `app/core/config.py`, `app/agents/news/agent.py`, `app/core/notification.py`
**Change**: Extract magic numbers to named constants at module top.

#### P3.3 — Structured JSON Logging (4h)

**File**: `app/core/logging_conf.py`
**Change**: Add `structlog` formatter outputting JSON when `LOG_FORMAT=json`. Default stays plain-text (no breaking change to `fetch-logs.sh`).
**Test**: `LOG_FORMAT=json` → valid JSON with `level`, `message`, `timestamp`.

#### P3.4 — Strava HMAC Signature Verification (2h)

**File**: `app/routers/webhooks.py`
**Change**: Verify `X-Hub-Signature` on POST using `STRAVA_WEBHOOK_SECRET`. 403 if missing/invalid.
**Test**: No header → 403. Valid header → 200.
**Side effect risk**: LOW — verify env var set BEFORE deploying.

#### P3.5 — Log Scrubbing Filter (NEW, 1h)

**File**: `app/core/logging_conf.py`
**Change**: Add `logging.Filter` that strips patterns: `KEY=…`, `Bearer …`, `:password@…`, `token: …` from log records before emission.
**Test**: `logger.info("Bearer abc123")` → log output shows `Bearer ***`.
**Side effect risk**: None — defensive only.

#### P3.6 — Lock-Protected Counters (NEW, 1h)

**File**: `app/core/state.py`
**Change**: Wrap `+=` increments in `threading.Lock()`. Document as scalability limit.
**Test**: 1000 parallel increments → final count == 1000.
**Side effect risk**: None — micro-overhead only.

---

### Phase 4 — Scalability (Post-MVP, no timeline)

| Change | Effort | Priority | Note |
|--------|--------|----------|------|
| Batch Gemini API calls for multi-topic news (cost -20-30%) | M | P3 | After Phase 2.1 stabilizes |
| Redis cache for config + user data | L | P3 | Only if cache miss latency proven bottleneck |
| Circuit breaker for Strava/Gemini API | M | P3 | Only if Phase 1 timeouts insufficient |
| Scheduler job timeout at APScheduler level | S | P2 | Complement to per-task `asyncio.wait_for` |
| PostgreSQL migration | L | P3 | Only if multi-user required (>100 active users) |

---

## "Do Not Do" List (v2)

| Tempting Change | Why Not | Defer To |
|----------------|---------|----------|
| SQLite → PostgreSQL mid-refactor | Breaking change, no staging env, untestable rollback | Phase 4, only if multi-user |
| Add Redis in Phase 1 | New infra dependency, deploy risk, unproven need | Phase 4 |
| JSON logging by default | Breaks current `fetch-logs.sh` parsing | Phase 3.3 (env-flagged) |
| Circuit breaker before timeouts | Premature; P1.1 timeouts may resolve issue | After Phase 1 monitoring |
| Combine multiple P-items in one commit | Rollback nightmare, can't isolate regression | Strict 1 commit per item |
| Remove `BackgroundTasks` immediately at P2.4 cutover | No safe rollback path | 3-stage rollout (shadow → cutover → cleanup) |
| Hardcode timeout in APScheduler globally | Limits flexibility per task type | Per-task `asyncio.wait_for(coro, timeout=N)` |
| Async-ify all sync code at once | Massive blast radius, scheduler thread model breaks | One module at a time, starting with news (P2.1) |
| Add `pytest --cov-fail-under=80` in Phase 0 | Coverage gate fires on existing untested code → blocks all PRs | Add gate AFTER Phase 1 brings coverage up |

---

## Documentation Per Phase (v2 — explicit)

| Phase | Required Doc Updates |
|-------|---------------------|
| P0 | `requirements.txt`, `Dockerfile`, `docs/RUNBOOK.md` (baseline metrics) |
| P1.1 | `docs/RUNBOOK.md` "Gemini timeout behavior", `docs/ISSUES.md` close P1.1 |
| P1.2 | `docs/RUNBOOK.md` "Required env vars", `config.example.json` |
| P1.3 | `docs/RUNBOOK.md` "SIGTERM/shutdown behavior" |
| P1.4 | `docs/ISSUES.md` close per fix |
| P1.5 | `docs/RUNBOOK.md` "Webhook rate limit" |
| P2.1 | `CLAUDE.md` File Map (no change), `docs/features/news-async.md` |
| P2.2 | `CLAUDE.md` File Map (4 new modules), `docs/features/coach-split.md` |
| P2.3 | `docs/features/news-prompt-unify.md` |
| P2.4 | `docs/features/db-task-queue.md` (full design), `docs/RUNBOOK.md` queue ops |
| P2.5 | `docs/RUNBOOK.md` "request_id usage in logs" |
| P3.x | `docs/RUNBOOK.md` "structured logging toggle", "log scrubbing" |

---

## Risk Matrix (v2)

| Change | Rollback Risk | Monitoring Signal | Rollback Trigger |
|--------|--------------|-------------------|------------------|
| P0.* | None | Baseline numbers stable | Health endpoint failure |
| P1.1 Gemini timeout | None | `[TIMEOUT]` log entries | >baseline+5/day |
| P1.2 Env validation | None (additive) | App fails to start | Any startup error in dev |
| P1.3 SIGTERM handler | None | Clean shutdown logs | Handler not called on stop |
| P1.4 Logging fixes | None | New error patterns surface | N/A — logging only |
| P1.5 Rate limit | None | 429 responses to Strava | >0 legitimate Strava 429s |
| P2.1 asyncio.gather | Medium | News briefing quality, scheduler latency | Missing topics OR latency +20% |
| P2.2 Coach split | Low | Smoke tests, import errors | Any import failure post-deploy |
| P2.3 Prompt unify | Low | News briefing content | Quality regression |
| P2.4 Task queue (Stage 1 shadow) | Low | DB write success rate | Write failures >1% |
| P2.4 Task queue (Stage 2 cutover) | High | Webhook processing time | Events not processed within 30s |
| P2.5 Request ID | None | Log format | N/A |
| P3.1 Flow helper | Low | Smoke tests | Import errors |
| P3.2 Constants | None | N/A | N/A |
| P3.3 JSON logging (off) | None | N/A | N/A |
| P3.4 HMAC verify | Medium | 403 on Strava POST | Strava events 403'd (env var wrong) |
| P3.5 Log scrub | None | Test logs scrubbed | Over-scrubbing (false positives) |
| P3.6 Counter locks | None | N/A | N/A |

---

## Implementation Checklist

### Phase 0 (NEW)
- [ ] P0.1 Pin pytest deps in requirements.txt
- [ ] P0.2 Latency + error counters + `/health/metrics`
- [ ] P0.3 Dockerfile USER appuser + tini
- [ ] P0.4 Capture 24h baseline
- [ ] P0.5 Document baseline in RUNBOOK.md
- [ ] P0.6 Harden admin credentials — remove "admin"/"123456" defaults **(v3 CRITICAL)**
- [x] P0.7 Wrap all 8 scheduler tasks in try/except **(v3 CRITICAL)** — all tasks had try/except; T3 tests verify (2026-04-21)

### Phase 1
- [x] P1.1 Gemini timeout + retry — 120000ms coach, 30000ms news/memory; SSL retry; ADR-011 (2026-04-21)
- [ ] P1.2 Startup env validation
- [ ] P1.3 SIGTERM signal handler
- [ ] P1.4 Fix swallowed exceptions
- [ ] P1.5 Webhook rate limiting
- [ ] P1.6 Add HTTP timeouts on Strava/Telegram external calls **(v3)**
- [x] P1.7 Strava webhook Pydantic schema + JSONDecodeError handling **(v3)** — null-body guard added to telegram_event (2026-04-21)

### Phase 2
- [ ] P2.1 asyncio.gather for news topics
- [x] P2.1b Eliminate duplicate `send_message_with_retry` **(v3)** — removed inline copy from coach/agent.py; canonical in utils.py; ADR-010 (2026-04-21)
- [ ] P2.2 Split coach agent into 4 modules
- [ ] P2.3 Unify news prompt builders
- [ ] P2.4 Database-backed task queue (3 stages)
- [ ] P2.5 Request ID in logs

### Phase 3
- [ ] P3.1 Shared coach flow helper — partial: `build_agent_context()` centralises context assembly (TD-003, 2026-04-26); full flow wrapper (`run_coach_flow()`) not yet implemented
- [ ] P3.2 Named constants for magic numbers
- [ ] P3.3 Structured JSON logging (flag-gated)
- [ ] P3.4 Strava HMAC verification
- [ ] P3.5 Log scrubbing filter (NEW)
- [ ] P3.6 Lock-protected counters (NEW)
- [x] P3.7 Add `threading.Lock()` to config cache invalidation **(v3)** — done; thread-safety test added (2026-04-21)
- [x] P3.8 Extract `get_local_tz()` to `app/core/timezone_utils.py` **(v3)** — 14 duplicate calls → 1 canonical; smoke test added (2026-04-21)
- [ ] P3.9 Add return type hints to all public agent/service functions **(v3)**

### Test Coverage (v3 — TDD Agent)
- [ ] T1 Strava webhook signature validation tests (`tests/test_webhooks.py`)
- [ ] T2 Admin credential validation tests (`tests/test_admin.py`)
- [x] T3 Scheduler task exception recovery tests (`tests/test_scheduler.py`) — 8 tasks × 1 test each (2026-04-21)
- [x] T4 Webhook JSON decode error tests (`tests/test_webhooks.py`) — Pydantic 422 + telegram null-body (2026-04-21)
- [ ] T5 Database OperationalError retry tests — deferred; requires retry impl first (WAL+busy_timeout mitigates)
- [x] T6 Config thread-safety tests (`tests/test_config.py`) — 20-thread concurrent read + interleaved save/load (2026-04-21)
- [x] T7 Multi-tenant run activity isolation tests (`tests/test_database.py`) — training loads, mileage, logs (2026-04-21)
- Reference: `tests/test_untested_critical_paths.md` — 45 concrete test stubs ready to implement

**Coverage milestone (2026-04-26):** 83% total — exceeds the 80% minimum from ADR-009. Total suite: 812 passed, 5 skipped (T1/T2/T5 deferred). Per ADR-009, `pytest --cov-fail-under=80` gate can now be enabled.

---

## Estimated Effort (v2)

| Phase | Effort | Calendar |
|-------|--------|----------|
| Phase 0 | 4h + 1.5h (v3) = **5.5h** | Week 0 (3 days incl. baseline capture) |
| Phase 1 | 7h + 2h (v3) = **9h** | Week 1 |
| Phase 2 | 26h + 2h (v3) = **28h** | Week 2–3 |
| Phase 3 | 14h + 3h (v3) = **17h** | Week 3–4 |
| Tests (v3) | **8h** | Parallel to Phase 1–2 |
| **Total** | **67.5h** | **~8.5 working days, deployed across 4–5 weeks** |

---

## Architecture Decision Records (v2)

### ADR-001: BackgroundTasks vs Persistent Queue
**Decision**: DB-backed queue using existing SQLite `pending_tasks` table. No new dependencies.
**Rollout**: 3-stage (shadow → cutover → cleanup) with config flag `use_database_queue`.

### ADR-002: Scheduler Timeout Strategy
**Decision**: Per-task `asyncio.wait_for(coro, timeout=1800)` (30 min max) — application-level. APScheduler-level timeout deferred to Phase 4.

### ADR-003: Logging Format
**Decision**: JSON behind `LOG_FORMAT=json` env flag. Default stays plain text until ELK/Loki is set up.

### ADR-004: Async migration scope (NEW)
**Decision**: Convert one module at a time. Start with news agent (P2.1). Coach agent stays sync until news is stable for 2 weeks. Scheduler tasks remain sync (`def`, not `async def`) — APScheduler thread pool model.

### ADR-005: Metrics backend (NEW)
**Decision**: In-memory counters exposed via `/health/metrics` (text plain). Prometheus deferred to Phase 4 — adds dependency for unproven need.

### ADR-006: Database (NEW)
**Decision**: SQLite stays. Postgres migration ONLY if multi-user (>100 active users) becomes a requirement. Until then, SQLite + WAL + connection pooling (Phase 4 item) is sufficient.

### ADR-007: Credential Hardening Strategy (v3)
**Decision**: Fail-fast at startup if admin credentials not set (no defaults). This means any deployment that was relying on the `"admin"/"123456"` defaults will break — intentionally. Set `ADMIN_USERNAME` + `ADMIN_PASSWORD` in `.env` before deploying P0.6.
**Why not soft warning?**: A soft warning doesn't prevent exposure. Fail-fast is the only safe option.

### ADR-008: Scheduler Exception Strategy (v3)
**Decision**: Each task wraps its body in `try/except Exception: logger.error(...)`. Exceptions are NOT re-raised (APScheduler would remove the job from the schedule if an exception propagates). Recovery is via logging + next scheduled invocation. No dead-letter queue needed at this scale.

### ADR-009: Test Coverage Gate Timing (v3)
**Decision**: `pytest --cov-fail-under=80` gate added AFTER Phase 1 test work completes (T1–T5), NOT in Phase 0. Adding it now would block all PRs due to existing coverage gaps.

### ADR-010: Duplicate Function Elimination Approach (v3)
**Decision**: Move, don't refactor. When eliminating `analyze_run_with_gemini()` and `send_message_with_retry()` duplicates, perform PURE MOVES with zero logic changes. The consolidation is a separate commit from any logic improvement.

### ADR-011: SDK Parameter Units Must Be Verified From Source (Post-Incident 2026-04-21)
**Incident**: During P1.1 (Gemini timeout), `HttpOptions(timeout=30)` was added thinking 30 = seconds. The `google-genai` SDK uses **milliseconds**. Result: 30ms timeout → `X-Server-Timeout: 1` → Google API rejected all calls with `400 INVALID_ARGUMENT: Manually set deadline 1s is too short`. Broke Morning Briefing, News Agent, and News Memory — silently until logs were reviewed.

**Root cause**: Standard Python convention is seconds (`requests`, `httpx`, `asyncio`). The `google-genai` SDK breaks this convention without prominent documentation.

**Affected files** (all introduced in the same refactor batch):
- `app/agents/coach/agent.py`: `timeout=120` → fixed to `timeout=120000` (2 min)
- `app/agents/news/agent.py`: `timeout=30` → fixed to `timeout=30000` (30s)
- `app/agents/news/memory.py`: `timeout=30` → fixed to `timeout=30000` (30s)

**Decision**: Before adding any numeric parameter to a third-party SDK call:
1. Read the field definition from source (`inspect` or filesystem read of `.py` file)
2. Confirm the unit explicitly
3. Add the value with a `# Ns in ms` comment to make the unit visible
4. Add/update `tests/test_sdk_contracts.py` to assert the value is in the safe range

**Test gate added**: `tests/test_sdk_contracts.py` — 4 tests that statically audit all `HttpOptions(timeout=N)` literals in the codebase and assert `N >= 10_000` (minimum safe: 10s = 10_000ms). Also added to `test_smoke.py` as `TestSDKContracts`.

---

## Rollback Reference

```bash
# Per-phase rollback
bash scripts/rollback-t440.sh                        # restore last backup image
docker tag airunningcoach:pre-PX.Y airunningcoach:latest && docker compose up -d  # specific phase rollback

# Verify baseline post-rollback
curl http://localhost:8000/health/metrics
bash scripts/fetch-logs.sh -l ERROR --since 1h
```

---

*v2 generated 2026-04-19 by Opus 4.7 reviewing Sonnet 4.6's v1 plan.*
*v3 generated 2026-04-20 by 3-agent orchestration (architect + code-reviewer + tdd-guide, all Opus 4.7). 19 total findings added; 4 new ADRs; 10 new phase items; 45 TDD stubs in `tests/test_untested_critical_paths.md`.*
*Always run `python -m pytest tests/test_smoke.py -v` before any deploy.*

# Changelog — Personal AI OS

All notable changes to this project are documented here.
Format: `[Date] type: description` — most recent first.

---

## 2026-04-26

### Added
- **News Agent v1.0 — 4 PRD gaps closed** (`fd40751`):
  - **NFR-1**: Per-topic timeout via `as_completed(timeout=topic_timeout_s)` — timed-out topics log WARNING and are skipped; other topics unaffected
  - **FR-3.6**: `timezone` field added to `config.example.json` under `news_agent`
  - `max_topic_workers`, `topic_timeout_seconds`, `ondemand_rate_limit_per_hour` now config-driven at runtime (module constants remain as fallback defaults)
  - `TestIsLateTrigger` — 10 unit tests for `_is_late_trigger` covering midnight crossing, all 3 sessions, custom `skip_minutes`, and invalid time string edge cases
- **PRD v1.4** (`703ca40`): requirements doc updated — all 5 DEFs marked fixed with proof, full DoD checklist ticked, suite count recorded (810 passed)

### Changed
- Test suite: **810 passed, 5 skipped, 0 failures** (up from 436)

---

## 2026-04-25

### Added
- **News Agent v1.0 — DEF proof tests** (`71ef59d`):
  - `TestCallGeminiWithSearchGroundingGate` — 5 tests proving DEF-001 (`thinking_budget=0` enforced), DEF-005 (grounding gate rejects ungrounded responses), exception safety, and WARNING log on reject
  - Root cause documented: `conftest.py` stubs `google.genai.types` as MagicMock; patch `app.agents.news.agent.types` to assert on `ThinkingConfig` constructor call

### Fixed
- DEF-001 proof test `test_thinking_budget_zero_passed` — patched `app.agents.news.agent.types` instead of `client`; asserts `mock_types.ThinkingConfig.assert_called_once_with(thinking_budget=0)`

---

## 2026-04-24

### Added
- **News Agent v1.0 — core implementation** (`6f7c9ce`, `8dd8cac`):
  - **DEF-001**: `thinking_budget=0` in `GenerateContentConfig` — prevents LLM thought text from leaking to Telegram
  - **DEF-002 + DEF-005**: `_call_knowledge_only()` deleted entirely — grounding gate is sole LLM call path; ERR-001 sent when all topics fail
  - **DEF-003**: `_DOC_THEM_RE` regex strips LLM-authored `<a href>Đọc thêm</a>` links; sources block built exclusively from `grounding_chunks[].web.uri`
  - **DEF-004**: Auto-resolved — grounding gate eliminates training-data path, so stale 2024 dates cannot appear
  - Rate limiting: in-memory sliding window counter `_check_rate_limit(chat_id, limit)` — 10 req/hour default, config-driven
  - Error message constants `ERR_001`–`ERR_007` in `telegram_handler.py` — tests import constants, never assert on literal strings
  - Late-trigger skip: `_is_late_trigger(session, config)` in `scheduler.py` — skips briefing if cron fires >30 min late
  - Structured logs: every LLM call logs `grounding_used`, `source_count`, `latency_ms`; briefing completion logs `topics_attempted`, `topics_succeeded`, `chars_sent`
  - News Agent PRD v1.3 created: `docs/features/news-agent-requirements.md` (requirements, test strategy, error catalog, NFR testability matrix, architect notes)
- **Test expansion**: `test_news_agent_helpers.py`, `test_news_agent_flows.py`, `test_news_telegram.py` — 150+ new tests covering all FRs, DEFs, user stories

---

## 2026-04-23

### Added
- **Metrics Coverage UI** (`4ea273e`): `GET /admin/metrics/coverage` endpoint + Console tab showing per-module test counts

### Fixed
- `switchTab` duplicate declaration breaking tab navigation in console (`4c7d817`)

---

## 2026-04-21

### Fixed
- **ISS-012**: Gemini "thoughtful\n..." preamble leaking into Telegram news briefing (`1890a20`)
  - Root cause: `_strip_thought_preamble` regex did not match the `thoughtful\n` variant used by Gemini 2.5 Flash
  - Fix: broadened regex to cover all known variants; `thinking_budget=0` added as defense-in-depth (DEF-001)
- Admin credential lazy-load: admin username/password now read from env at request time, not module load — fixes startup crash when env vars not set during testing

### Changed
- **Gemini SDK `HttpOptions.timeout` unit** (`6e9c1a9`): corrected from seconds to milliseconds — `timeout=30000` (30s). Previous `timeout=30` was 30ms, causing `X-Server-Timeout:1 → 400` rejections
  - `tests/test_sdk_contracts.py` added as a regression gate on this unit convention
- Reliability: Gemini retry now covers SSL/timeout errors in `utils.py` (`f79ffd4`)

---

## 2026-04-22

### Refactored
- **Architecture plan** (`8a1397f`): extracted `get_local_tz()` to `app/core/timezone_utils.py` (P3.8); added config threading lock (P3.7); eliminated duplicate `send_message_with_retry` from `agent.py`
- `REFACTOR_PLAN.md` checklist updated with completed items (`fea3184`)

---

## 2026-04-09 (2)

### Added
- **News Agent Telegram Commands** — `/news [morning|afternoon|watch|help]` now handled via `app/agents/news/telegram_handler.py`:
  - Lazy imports prevent circular deps; all user-facing messages in Vietnamese (Zone 2)
  - `/news` or `/news morning` → morning digest; `/news afternoon` → afternoon digest; `/news watch` → immediate breaking-news scan; `/news help` → command list
  - Silently sends disabled message if `news_agent.enabled = false`
  - Wired into `app/routers/webhooks.py` between `/standup` and AI chat fallback
- **News Agent Settings UI Tab** — new "📰 News Agent" tab in `/console`:
  - Enable/disable toggle + optional Telegram chat ID override
  - Schedule section: `morning_time`, `afternoon_time`, `watch_interval_minutes`, `max_articles_per_feed`
  - Thresholds section: `alert_threshold`, `digest_threshold`, `topic_cooldown_hours` (live range sliders)
  - RSS feeds table: editable rows (name / URL / category) with add/remove; serialized to JSON on submit
  - Interest profile table: editable rows (category / keywords / weight) with add/remove; serialized to JSON on submit
- **`POST /console/save-news`** endpoint in `app/routers/console.py`: parses form data, `json.loads()` feeds + interest profile, calls `save_config()` + `reload_scheduler()`
- 19 new tests in `tests/test_news_telegram.py`; full suite now **329 passing**

---

## 2026-04-09

### Added
- **Log Audit System** — periodic log scanning with web UI and AI-analysis-ready storage:
  - `app/services/log_auditor.py`: pattern-matching engine scanning `data/app.log*` (all rotated files); detects crash/traceback, network, news_scoring, performance, scheduler, database, improvement, and general error/warning patterns
  - `app/routers/audit.py`: REST API — `GET /audit` (HTML), `GET /audit/api/entries` (JSON with filters), `POST /audit/api/entries/{id}/acknowledge`, `POST /audit/api/entries/{id}/resolve`, `POST /audit/api/run` (manual trigger)
  - `templates/audit.html`: dark Bootstrap dashboard with stats cards, severity/category/status filters, and one-click acknowledge/resolve
  - `audit_entries` table in SQLite: dedup via `UNIQUE(user_id, raw_line)` — idempotent re-runs
  - `task_log_audit()` registered in scheduler (`IntervalTrigger(hours=6)`)
  - `app/core/logging_conf.py`: added `RotatingFileHandler` (10MB × 3 backups) for persistent log storage
  - 32 new tests; full suite now 310 passing

### Changed
- **Default Gemini model** updated to `models/gemini-flash-latest` everywhere (coach agent, news agent, alert engine, console router, config.example.json) — alias always tracks the newest flash model automatically
- **Model selector UI** (`console.html`, `admin.html`): removed deprecated gemini-2.x / gemini-1.5 optgroups; kept gemini-3.1 group + new "Aliases" group (`gemini-flash-latest`, `gemini-pro-latest`)
- **`CLAUDE.md`**: added T440 deploy commands (`bash scripts/deploy-t440.sh`) for session persistence

---

## 2026-04-08 (2)

### Added
- **Agentic News Observer** — full event-driven upgrade of the news agent:
  - `scorer.py`: batch Gemini scoring against a 4-category interest profile (technology/sports_running/it_workforce/economics_politics), each with configurable weight and keywords; result cached in `news_article_scores` table
  - `alert_engine.py`: `run_news_watch()` cycle (default 30 min), scores all new articles, sends breaking alert via Telegram for any article scoring ≥ `alert_threshold` (default 7); cool-down enforces max 3 alerts/category per 2-hour window
  - **26 RSS feeds** across 5 categories: general (3), technology (10), automotive (4), sports (3), it_workforce (1), economics (5)
  - Two new DB tables: `news_alert_log` (cool-down tracking) and `news_article_scores` (score cache)
  - `build_alert_prompt()` and `build_categorized_digest_prompt()` added to `prompts.py`
  - Morning/afternoon digest now filters by `digest_threshold` (default 4) and skips already-alerted links
  - `task_news_watch()` registered in `scheduler.py` on `IntervalTrigger`
- **config.example.json**: added `watch_interval_minutes`, `alert_threshold`, `digest_threshold`, `topic_cooldown_hours`, `interest_profile`, and all 26 feed entries with `category` field
- Test suite expanded to 273 passing (28 news-specific tests, 100% pass rate)

---

## 2026-04-08

### Added
- **LTHR / rFTP structured config fields**: `lthr_bpm` and `rftp_watts` added to `config.example.json` and the Admin UI Sports Science section
  - `lthr_bpm > 0` → activates Joe Friel 7-zone HR model in `build_system_instruction()`
  - `rftp_watts > 0` → activates Stryd 6-zone power zones
  - Both set to `0` → falls back to Karvonen 5-zone HR model (unchanged behavior)

### Changed
- **`user_profile` convention**: now identity-only (name, gear, target race). Zone tables are no longer embedded here — they are computed dynamically from `lthr_bpm` / `rftp_watts` structured fields
- **Gemini model selectors** updated in both `templates/admin.html` and `templates/console.html`:
  - Full `<optgroup>` grouping: Gemini 3.1 / 2.5 / 2.0 / 1.5
  - Added: `gemini-3.1-flash`, `gemini-3.1-flash-preview`, `gemini-3.1-pro-preview`

### Fixed
- **`scripts/deploy-t440.sh` log error check**: `[: 0\n0: integer expression expected` — SSH returned two lines from `grep -c`, breaking the arithmetic comparison. Fixed with `tr -d '[:space:]'` + `grep -oE '^[0-9]+'` to extract a clean integer.

---

## 2026-04-07

### Added
- **News Agent** (`app/agents/news/`): Daily RSS news digest via Gemini
  - Sources: VnExpress, Tuổi Trẻ, BBC Vietnamese
  - Morning summary at `07:00`, afternoon update at `17:00` via Telegram
  - 24-hour deduplication against articles already sent
  - Per-agent `system_instruction` (separate from Coach Dyno's persona)
  - Option B Telegram routing: uses `news_agent.telegram_chat_id` if configured, falls back to primary chat
  - 39 tests (`test_news_feeds.py`, `test_news_prompts.py`, `test_news_agent.py`) — 100% pass rate
- **Docker `HEALTHCHECK`** (`Dockerfile`): Auto-restarts unhealthy containers
  - Uses `urllib.request` (stdlib) — no curl dependency in `python:3.11-slim`
  - Interval: 60s, timeout: 10s, start-period: 30s, retries: 3
- **Pre-deploy check script** (`scripts/pre-deploy-check.sh`): Run before deploy to catch issues early
  - Runs full pytest suite
  - Validates `load_config()` without requiring env vars
  - Checks docker compose syntax (skips gracefully if Docker unavailable)
  - Non-blocking warning for uncommitted changes in `app/` or `tests/`
- **Gemini 2.5 Pro** option added to Console model selector (`templates/console.html`)
- **Automated deploy script** (`scripts/deploy-t440.sh`): push → SSH pull → rebuild → health check → e2e tests
  - Smoke tests: `/health`, `/console`, `/admin`, `/webhook` (GET), scheduler running, recent log errors

### Fixed
- All 14 pre-existing test failures resolved (273/273 now pass):
  - `test_agent.py`: Updated `@patch` paths to reference `flows/` submodules
  - `test_database.py`: Fixed schema drift (`stream_file_path`, dedup semantics)
- `GET /webhook` E2E expectation corrected to 200 (Strava verification endpoint)
- `on_event` deprecation resolved — migrated to FastAPI `lifespan` context manager (`app/main.py`)

### Changed
- News agent prompts refactored: `build_morning_news_system_instruction()` and `build_afternoon_news_system_instruction()` use `.replace()` for safe RSS content injection (avoids `KeyError` on `{}` in feed content)

---

## 2026-03-23

### Added
- **Unified Console** (`/console`): merges admin, dashboard, and memory view into a single tabbed UI
  - Tabs: Overview, Training Log, Memory, Settings, System
- **`/health` endpoint**: returns `{"status", "db", "config", "scheduler"}` — used by Docker health check
- **Legacy redirects**: `GET /admin` → `/console?tab=settings`, `GET /dashboard` → `/console?tab=overview`
- 59 new tests across `test_agent.py`, `test_database.py`, stream storage, tools — total 216

---

## 2026-03-22

### Changed
- Agent flows extracted from `agent.py` into `flows/` submodules: `run_analysis.py`, `morning_briefing.py`, `weekly_reflection.py`, `memory_extraction.py`
- Thin orchestrator pattern: `agent.py` only routes — all logic lives in flows

---

## 2026-03-14

### Added
- `build_agent_context()` factory in `utils.py` — single context builder for all flows (no duplicate context construction)
- Exponential backoff (`send_with_retry`) for Gemini API calls

---

## 2026-03-10

### Added
- Initial 8-layer Lego Prompt Engine (`app/agents/coach/prompts.py`)
- 4-tier memory architecture: working memory → active facts (SQLite) → archived facts (SQLite) → episodic RAG (ChromaDB)
- Sports science engine: TRIMP, ACWR, Efficiency Factor, Aerobic Decoupling, Training Phase calculator
- Strava webhook ingestion: real-time activity parsing with stream storage
- Morning briefing cron with weather awareness
- Weekly reflection with RAG memory injection
- Autonomous implicit memory extraction from chat history
- SQLite WAL mode, connection context manager
- 157 tests, 132 passing

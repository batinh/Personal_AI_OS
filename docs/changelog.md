# Changelog — Personal AI OS

All notable changes to this project are documented here.
Format: `[Date] type: description` — most recent first.

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

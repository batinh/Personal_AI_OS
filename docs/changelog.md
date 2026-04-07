# Changelog — Personal AI OS

All notable changes to this project are documented here.
Format: `[Date] type: description` — most recent first.

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

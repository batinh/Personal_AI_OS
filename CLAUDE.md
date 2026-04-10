# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run full test suite (must pass: 0 failures)
python -m pytest tests/ -q

# Run a single test module
python -m pytest tests/test_webhooks.py -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=html

# Run full stack (requires .env + data/config.json)
docker compose up --build

# Deploy (run on T440 — this IS the server)
bash scripts/deploy-t440.sh              # git pull + rebuild + health check
bash scripts/deploy-t440.sh --skip-pull # rebuild only (skip git pull)
# health: http://localhost:8000/health

# Local dev server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Before every commit, mentally run `docs/pragmatic_review_checklist.md`. For new features, read `docs/feature_design_template.md` first.

## First-time setup on a new machine (e.g. T440)

```bash
git clone git@github.com:batinh/Personal_AI_OS.git ~/repo/Personal_AI_OS
cd ~/repo/Personal_AI_OS

# Sync global Claude Code config (hooks, rules) from old machine + install memory files
RPI5_HOST=tinhn@<rpi5-ip> bash scripts/setup-claude-t440.sh

# Copy .env (secrets — never in git)
scp -P 22 tinhn@<rpi5-ip>:~/repo/Personal_AI_OS/.env .env

# Copy runtime data config
scp -P 22 tinhn@<rpi5-ip>:~/repo/Personal_AI_OS/data/config.json data/config.json
```

## File Map — Read Only What You Need

| Task | Read |
|------|------|
| Coach flow bug | `app/agents/coach/agent.py` + affected `flows/` module |
| Prompt change | `app/agents/coach/prompts.py` |
| DB schema / query | `app/core/database.py` + `docs/database_design.md` |
| News agent | `app/agents/news/agent.py`, `feeds.py`, `prompts.py`, `telegram_handler.py` |
| News alerts | `app/agents/news/alert_engine.py` |
| Scheduler job | `app/services/scheduler.py` |
| Webhook / Strava | `app/routers/webhooks.py` + `app/agents/coach/strava_client.py` |
| Memory / RAG | `app/services/rag_memory.py` + `flows/memory_extraction.py` |
| Config / settings | `app/core/config.py` + `config.example.json` |
| Log audit | `app/services/log_auditor.py` + `app/routers/audit.py` |
| Test failures | `tests/conftest.py` + `tests/test_<module>.py` |

Run targeted tests first, full suite only before commit:
```bash
python -m pytest tests/test_<module>.py   # fast feedback on affected module
python -m pytest tests/                   # gate before commit (329 must pass)
```

## Language Zones — STRICTLY ENFORCED

| Zone | Scope | Language |
|------|-------|----------|
| **1** | Source code, DB schemas, logs, git commits, docstrings | English only |
| **2** | AI prompts output, Telegram/Strava/email messages, UI text | Vietnamese only |
| **3** | Prompt builder functions (Python logic = English, injected f-strings = Vietnamese) | Mixed — keep boundary |

> ❌ `def tinhTRIMP()` (Zone 1 violation) ❌ English Telegram message (Zone 2 violation)

## Architecture

```
routers/ → agents/coach/ → core/        ← dependency direction (never import upward)

agents/coach/
  agent.py          thin orchestrator — delegates to flows/, handles Telegram chat
  flows/            one module per flow: run_analysis, morning_briefing,
                    weekly_reflection, memory_extraction
  prompts.py        8-layer Lego Prompt Engine — see docs/architecture.md
  utils.py          build_agent_context() — SINGLE factory for all flow context
  tools.py          Gemini AFC tools — top-level imports only, no local imports

agents/news/
  agent.py          news briefing orchestrator (morning/afternoon digests)
  alert_engine.py   event-driven breaking-news alerts (runs on interval via scheduler)
  scorer.py         Gemini-based article scoring for alert threshold decisions

services/
  scheduler.py      APScheduler (BackgroundScheduler) cron jobs
  log_auditor.py    log file scanning → audit_entries DB table → /audit dashboard
  backup.py         data backup tasks
  weather.py        weather API integration for morning briefing context

core/
  user_context.py   get_primary_user_id() — canonical way to get user from env
  database.py       all DB access; schema initialized via init_db()
  config.py         load_config() / save_config() with 60s TTL cache;
                    auto-inits data/config.json from config.example.json on first boot
```

**Non-obvious rules:**
- All `scheduler.py` tasks must be `def`, not `async def` — `BackgroundScheduler` is a thread pool
- All file paths use `Path(__file__).resolve().parent...` — never relative (Docker WORKDIR=/app breaks them)
- `data/config.json` is gitignored; auto-initialized from `config.example.json` on first boot via `_EXAMPLE_CONFIG_PATH` in `config.py`
- Use `build_agent_context()` in every flow — never duplicate context building
- Primary user ID: always use `get_primary_user_id()` from `app.core.user_context`, not `os.getenv("TELEGRAM_CHAT_ID")` directly in new code
- Tool routing in `agent.py`: `_select_tools_for_message()` routes to read-only vs write tool set based on intent keywords — add Vietnamese write-intent keywords to `_WRITE_INTENT_KEYWORDS` when needed
- `ENABLE_MEMORY_DEBUG=true` env var enables verbose memory extraction logging in `extract_implicit_memory()`

## Telegram Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/sync [N\|month]` | `webhooks.py` | Manual Strava sync (default 3 activities) |
| `/standup` | `webhooks.py` | Trigger morning briefing immediately |
| `/clear` / `/reset` | `agent.py` | Clear short-term chat history |
| `/reflect` / `/reflection` | `agent.py` | Trigger memory extraction + weekly reflection (test/admin mode) |
| `/news [morning\|afternoon\|watch\|help]` | `telegram_handler.py` | News digest / breaking-news scan |

News commands are disabled silently when `news_agent.enabled = false` in config.
Patch target for tests: `app.agents.news.telegram_handler.handle_news_command` (lazy import inside webhook handler).

## Database

See `docs/database_design.md` for full schema.

**Critical gotchas:**
- Every table **must** have `user_id` — multi-tenant schema, no exceptions
- Access rows as `row["column"]`, not `row[0]` — `sqlite3.Row` has no `.get()`
- WAL mode set per connection via `PRAGMA journal_mode=WAL`
- Memory dedup uses `MAX(rowid)` subquery — `status='active'` filter goes on outer WHERE, not subquery

## Prompts

See `app/agents/coach/prompts.py` for the full 8-layer system.

**Critical gotchas:**
- Use `.replace()` not f-strings when injecting user data containing `{}` — avoids `KeyError`
- Format rules are platform-specific: HTML only for Telegram/email, plain-text only for Strava
- Patch paths in tests must target **where the symbol is imported**, not defined:
  - ✅ `@patch("app.agents.coach.tools.calculate_acwr")`
  - ❌ `@patch("app.agents.coach.utils.calculate_acwr")`

## Coaching Science

See `docs/coaching_constants.md` for TRIMP formula, ACWR thresholds, taper schedule, 15% rule, and GCS rubric.

## Conventions

- Logging tags: `logger.info("[MODULE] message")` — e.g. `[TOOL-USE]`, `[SCHEDULER]`, `[DB_ERROR]`
- Git commits: `type: description` (English, Zone 1)
- Multi-role review lens: Running Coach · AI Expert · SW Architect · System Architect · Prompt Engineer · DB Architect
- Roadmap lives in `README.md` — update it in-place, never create a new one

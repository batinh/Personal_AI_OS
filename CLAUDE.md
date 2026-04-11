# CLAUDE.md

## Commands
```bash
python -m pytest tests/ -q                      # full suite (gate before commit — 0 failures)
python -m pytest tests/test_<module>.py -v     # targeted (run this first)
python -m pytest tests/ --cov=app --cov-report=html
docker compose up --build
bash scripts/deploy-t440.sh              # T440: git pull + rebuild + health check
bash scripts/deploy-t440.sh --skip-pull # rebuild only
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Before commit: `docs/pragmatic_review_checklist.md`. New feature: `docs/feature_design_template.md`. Bug or feature: `docs/ISSUES.md`.

## Feature Design Doc Convention
- **Every new feature** (≥2 files changed): create `docs/features/{feature-slug}.md` using `docs/feature_design_template.md`
- **Slug**: lowercase English, hyphens — e.g. `news-agent-overhaul`, `telegram-routing`
- **Before code**: design doc first, then implement — link from the issue in `docs/ISSUES.md`

## File Map
| Task | Read |
|------|------|
| Coach flow bug | `app/agents/coach/agent.py` + affected `flows/` |
| Prompt change | `app/agents/coach/prompts.py` |
| DB schema/query | `app/core/database.py` + `docs/database_design.md` |
| News agent | `app/agents/news/agent.py`, `feeds.py`, `prompts.py`, `telegram_handler.py` |
| News alerts | `app/agents/news/alert_engine.py` |
| Scheduler | `app/services/scheduler.py` |
| Webhook/Strava | `app/routers/webhooks.py` + `app/agents/coach/strava_client.py` |
| Memory/RAG | `app/services/rag_memory.py` + `flows/memory_extraction.py` |
| Config | `app/core/config.py` + `config.example.json` |
| Log audit | `app/services/log_auditor.py` + `app/routers/audit.py` |
| Tests | `tests/conftest.py` + `tests/test_<module>.py` |
| Issue tracking | `docs/ISSUES.md` |

## Language Zones — STRICTLY ENFORCED
| Zone | Scope | Language |
|------|-------|----------|
| **1** | Code, DB, logs, git commits, docstrings | English |
| **2** | AI output, Telegram/Strava/email messages | Vietnamese |
| **3** | Prompt builders: Python logic=English, injected f-strings=Vietnamese | Mixed |

> ❌ `def tinhTRIMP()` (Zone 1) ❌ English Telegram message (Zone 2)

## Non-obvious Rules
- Scheduler tasks: `def` not `async def` — BackgroundScheduler is a thread pool
- File paths: `Path(__file__).resolve().parent...` — never relative (Docker breaks them)
- Context building: always `build_agent_context()` — never duplicate
- User ID: `get_primary_user_id()` from `app.core.user_context` — not `os.getenv` directly
- Tool routing: add Vietnamese write-intent keywords to `_WRITE_INTENT_KEYWORDS` in `agent.py`
- `ENABLE_MEMORY_DEBUG=true` enables verbose memory extraction logging
- `data/config.json` gitignored; auto-initialized from `config.example.json` on first boot

## Database
- Every table **must** have `user_id` — multi-tenant, no exceptions
- `row["column"]` not `row[0]` — `sqlite3.Row` has no `.get()`
- WAL mode: `PRAGMA journal_mode=WAL` per connection
- Memory dedup: `MAX(rowid)` subquery — `status='active'` on outer WHERE, not subquery

## Prompts (`app/agents/coach/prompts.py`)
- `.replace()` not f-strings for user data with `{}` — avoids `KeyError`
- Format rules: HTML for Telegram/email only, plain-text for Strava only
- Patch target: where symbol is **imported**, not defined:
  - ✅ `@patch("app.agents.coach.tools.calculate_acwr")`
  - ❌ `@patch("app.agents.coach.utils.calculate_acwr")`

## Conventions
- Logging: `logger.info("[MODULE] message")` — e.g. `[TOOL-USE]`, `[SCHEDULER]`, `[DB_ERROR]`
- Git commits: `type: description` (English, Zone 1)
- Coaching science: `docs/coaching_constants.md` (TRIMP, ACWR, taper, GCS)
- Roadmap: update `README.md` in-place, never create a new one
- Responses: be concise — no trailing summaries, no restating what was just done
- Bug or feature found (by user or AI): add to `docs/ISSUES.md` immediately — Open table first, detail section at bottom. Move to Closed with commit hash when done.

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

# Local dev server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Before every commit, mentally run `docs/pragmatic_review_checklist.md`. For new features, read `docs/feature_design_template.md` first.

## File Map — Read Only What You Need

| Task | Read |
|------|------|
| Coach flow bug | `app/agents/coach/agent.py` + affected `flows/` module |
| Prompt change | `app/agents/coach/prompts.py` |
| DB schema / query | `app/core/database.py` + `docs/database_design.md` |
| News agent | `app/agents/news/agent.py`, `feeds.py`, `prompts.py` |
| Scheduler job | `app/services/scheduler.py` |
| Webhook / Strava | `app/routers/webhooks.py` + `app/agents/coach/strava_client.py` |
| Memory / RAG | `app/services/rag_memory.py` + `flows/memory_extraction.py` |
| Config / settings | `app/core/config.py` + `config.example.json` |
| Test failures | `tests/conftest.py` + `tests/test_<module>.py` |

Run targeted tests first, full suite only before commit:
```bash
python -m pytest tests/test_<module>.py   # fast feedback on affected module
python -m pytest tests/                   # gate before commit (273 must pass)
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
```

**Non-obvious rules:**
- All `scheduler.py` tasks must be `def`, not `async def` — `BackgroundScheduler` is a thread pool
- All file paths use `Path(__file__).resolve().parent...` — never relative (Docker WORKDIR=/app breaks them)
- `data/config.json` is gitignored; auto-initialized from `config.example.json` on first boot
- Use `build_agent_context()` in every flow — never duplicate context building

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
- GCS rubric is grounded: Motor = cadence vs 175 spm, Frame = decoupling vs 5%, Fuel = pace vs race target
- Patch paths in tests must target **where the symbol is imported**, not defined:
  - ✅ `@patch("app.agents.coach.tools.calculate_acwr")`
  - ❌ `@patch("app.agents.coach.utils.calculate_acwr")`

## Coaching Science (non-obvious constants)

- **TRIMP male**: `0.64 × e^(1.92 × HRR)` **female**: `0.86 × e^(1.67 × HRR)` — gender comes from `config["gender"]`
- **ACWR sweet spot**: 0.8–1.3 | > 1.3 caution | > 1.5 danger
- **Taper** (inject via `taper_factor`): week −3 = 75%, week −2 = 50%, race week = 25% — never increase during taper
- **15% Rule**: weekly volume increases must not exceed 15%

## Conventions

- Logging tags: `logger.info("[MODULE] message")` — e.g. `[TOOL-USE]`, `[SCHEDULER]`, `[DB_ERROR]`
- Git commits: `type: description` (English, Zone 1)
- Multi-role review lens: Running Coach · AI Expert · SW Architect · System Architect · Prompt Engineer · DB Architect
- Roadmap lives in `README.md` — update it in-place, never create a new one

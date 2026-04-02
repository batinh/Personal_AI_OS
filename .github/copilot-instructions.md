# Personal AI OS — Copilot Instructions

## Build & Test

```bash
python -m pytest tests/ -q          # Must pass: 228 tests, 0 warnings
docker compose up --build           # Run full stack (requires .env + data/config.json)
```

**Before every commit**: run `docs/pragmatic_review_checklist.md` mentally.
**New feature**: read `docs/feature_design_template.md` first.

---

## Language Zones — STRICTLY ENFORCED

| Zone | What | Language |
|------|------|----------|
| **1** | Source code, DB schemas, logs, git commits, docstrings | English only |
| **2** | AI prompts, Telegram/Strava/email messages, UI text | Vietnamese only |
| **3** | Prompt builder functions (Python logic = English, injected f-strings = Vietnamese) | Mixed — keep boundary |

> ❌ `def tinhTRIMP()` (Zone 1 violation) &nbsp;&nbsp; ❌ English Telegram message (Zone 2 violation)

---

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

---

## Database

See `docs/database_design.md` for full schema.

**Critical gotchas:**
- Every table **must** have `user_id` — multi-tenant, no exceptions
- Access rows as `row["column"]`, not `row[0]` — `sqlite3.Row` has no `.get()`
- WAL mode set per connection via `PRAGMA journal_mode=WAL`
- Memory dedup uses `MAX(rowid)` subquery — `status='active'` filter goes on outer WHERE, not subquery

---

## Prompts

See `app/agents/coach/prompts.py` for the full 8-layer system.

**Critical gotchas:**
- Use `.replace()` not f-strings when injecting user data containing `{}` — avoids `KeyError`
- Format rules are platform-specific: HTML only for Telegram/email, plain-text only for Strava
- GCS rubric is grounded (not vibes): Motor = cadence vs 175 spm, Frame = decoupling vs 5%, Fuel = pace vs race target
- Patch paths in tests must target **where the symbol is imported**, not defined:
  - ✅ `@patch("app.agents.coach.tools.calculate_acwr")`
  - ❌ `@patch("app.agents.coach.utils.calculate_acwr")`

---

## Coaching Science (non-obvious constants)

- **TRIMP male**: `0.64 × e^(1.92 × HRR)` &nbsp; **female**: `0.86 × e^(1.67 × HRR)` — gender comes from `config["gender"]`
- **ACWR sweet spot**: 0.8–1.3 &nbsp;|&nbsp; > 1.3 caution &nbsp;|&nbsp; > 1.5 danger
- **Taper** (inject via `taper_factor`): week −3 = 75%, week −2 = 50%, race week = 25% — never increase during taper
- **15% Rule**: weekly volume increases must not exceed 15%

---

## Conventions

- Logging tags: `logger.info("[MODULE] message")` — e.g. `[TOOL-USE]`, `[SCHEDULER]`, `[DB_ERROR]`
- Git commits: `type: description` + trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
- Multi-role review lens: Running Coach · AI Expert · SW Architect · System Architect · Prompt Engineer · DB Architect
- Roadmap lives in `README.md` — update it in-place, never create a new one

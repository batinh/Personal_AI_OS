# GitHub Copilot Instructions — Personal AI OS (Coach Dyno)

## Project Overview

**Personal AI OS** is a modular-monolith AI running coach built on FastAPI + Google Gemini.
It ingests Strava activity data, processes it through a multi-flow agent system (Coach Dyno),
and delivers personalized coaching via Telegram, Strava descriptions, and email.

Key components: FastAPI app · Gemini AFC agent · SQLite (WAL) · ChromaDB (RAG) · APScheduler · Docker + Nginx Proxy Manager.

---

## Multi-Role Perspective

When reviewing or generating code, always consider **all** of these lenses simultaneously:

1. **Running Coach** — coaching science accuracy (TRIMP, ACWR, HR zones, periodization, GCS)
2. **AI/Agentic Systems Expert** — prompt quality, tool routing, memory architecture, LLM safety
3. **Software Architect** — modular monolith integrity, flow separation, dependency direction
4. **System Architect** — Docker volume mounts, scheduler threading, API resilience
5. **Prompt Engineer** — Lego-layer prompt design, few-shot examples, format rules
6. **Database Architect** — SQLite WAL concurrency, multi-tenant user_id, idempotent writes

---

## Language Demarcation (ZONE Rules) — STRICTLY ENFORCED

| Zone | Scope | Language |
|------|-------|----------|
| **ZONE 1** (Developer-facing) | Source code, function/variable/class names, DB schemas, system logs, git commits, docstrings | **100% English** |
| **ZONE 2** (User-facing) | AI prompts/personas, Telegram messages, Strava descriptions, UI text, email content, DB data records | **Vietnamese** |
| **ZONE 3** (Transition) | Python variables in template/prompt builders are English; f-string injected content is Vietnamese | Mixed — enforce boundary |

> **Never mix zones.** A function named `tinhTRIMP()` violates Zone 1. An English Telegram message violates Zone 2.

---

## Architecture

```
app/
├── main.py                     # FastAPI app — lifespan context manager, /health, router registration
├── core/
│   ├── database.py             # All SQLite CRUD — single source of truth for data layer
│   ├── config.py               # TTL-cached JSON config loader + auto-init from example
│   ├── schemas.py              # Pydantic models (RunAnalysisResult, MemoryItem, etc.)
│   ├── user_context.py         # get_primary_user_id() — single source for user identity
│   └── logging_conf.py         # Structured logging setup
├── agents/coach/
│   ├── agent.py                # Thin orchestrator — routes to flows, handles Telegram chat
│   ├── flows/                  # One module per flow: run_analysis, morning_briefing,
│   │   │                       #   weekly_reflection, memory_extraction
│   ├── prompts.py              # 8-layer Lego Prompt Engine
│   ├── tools.py                # Gemini AFC tool definitions (top-level imports only)
│   ├── utils.py                # TRIMP, ACWR, HR zones, pace zones, AgentContext builder
│   ├── strava_client.py        # OAuth2 Strava API with token caching
│   └── harvest.py              # 3-tier data ingestion: SQLite → File → ChromaDB
├── routers/
│   ├── webhooks.py             # Strava webhook + Telegram webhook handlers
│   └── console.py              # Unified admin/dashboard UI (/console)
└── services/
    ├── scheduler.py            # APScheduler BackgroundScheduler — all tasks are sync def
    ├── rag_memory.py           # ChromaDB RAG wrapper (memorize/recall/forget)
    ├── weather.py              # OpenWeatherMap integration
    └── backup.py               # Scheduled DB backup
```

### Key Architectural Rules

- **Dependency direction**: `routers` → `agents` → `core`. Never import upward.
- **No circular imports**: `tools.py` imports from `utils.py` and `core/` at top level — never locally.
- **Data injection pattern**: Pre-compute all metrics before sending to LLM. Never rely on tool calls for data that can be pre-fetched.
- **Thin orchestrator**: `agent.py` delegates to flow modules. Business logic lives in flows, not agent.py.
- **All scheduler tasks are `def` (not `async def`)**: `BackgroundScheduler` runs in thread pool.
- **`build_agent_context()`** is the single factory for all flow context — never duplicate context building.

---

## Coding Standards

### Naming
- Files/modules: `snake_case` (e.g., `run_analysis.py`)
- Classes: `PascalCase` (e.g., `AgentContext`, `RagMemory`)
- Functions/variables: `snake_case` (e.g., `calculate_trimp`, `user_id`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `CHAT_FORMAT_RULES`, `DEFAULT_REFLECTION_TASK`)
- No abbreviations unless domain-standard (TRIMP, ACWR, GCS, HR, RAF)

### Functions
- Every public function gets a docstring (English, Zone 1)
- Max function length: ~40 lines. Extract helpers if longer.
- Use `logger.info/warning/error` with `[MODULE]` prefix tags (e.g., `[TOOL-USE]`, `[SCHEDULER]`, `[DB_ERROR]`)

### Error Handling
- All DB operations wrapped in `try/except/finally` with `conn.close()` in finally
- Gemini API calls use `send_message_with_retry()` (exponential backoff: 1s/2s/4s)
- Tool errors: graceful degradation — never expose technical errors to users

### Imports
- Standard lib → Third-party → Internal (blank line between groups)
- No local/inline imports except for documented circular-import workarounds (must be commented)

---

## Database Rules

- **Every table MUST have a `user_id` column** — multi-tenant architecture, no exceptions
- **WAL mode** enabled on every connection via `PRAGMA journal_mode=WAL`
- **Idempotent writes**: use `INSERT OR REPLACE` or `MAX(rowid)` dedup pattern for memories
- **`get_db_connection()`** returns `sqlite3.Row` rows — access with `row["column"]` not `row[0]`
- Schema migrations live inside `init_db()` — add `ALTER TABLE` guards with `PRAGMA table_info()`
- Never use `SELECT *` in production queries — always name columns explicitly

---

## Prompt Architecture (8-Layer Lego System)

```
Layer 1: build_system_instruction()     — Immutable coach persona, HR/pace zones, CoT rules, GCS rubric
Layer 2: get_shared_context_block()     — Dynamic: time, phase, ACWR, weekly limits
Layer 3: DEFAULT_ANALYSIS_REQUIREMENTS — Domain-specific evaluation criteria
Layer 4: Platform format rules          — CHAT_FORMAT_RULES / STRAVA_FORMAT_RULES / EMAIL_FORMAT_RULES
Layer 5: Task builders                  — build_standup_prompt(), build_chat_prompt(), etc.
Layer 6: DEFAULT_REFLECTION_TASK        — Weekly self-reflection with dual-horizon
Layer 7: WEATHER_INSTRUCTION            — Weather-aware safety block
Layer 8: MEMORY_EXTRACTION_PROMPT       — Background state machine (few-shot examples)
```

**Rules for prompt changes:**
- Never use f-strings where user data contains `{}` — use `.replace()` to avoid `KeyError`
- Every new prompt section must have a `[SECTION_NAME]` header in Vietnamese (Zone 2)
- Format rules are platform-specific — don't mix HTML into Strava prompts
- GCS rubric is grounded: Motor=cadence/175spm, Frame=decoupling/5%, Fuel=pace vs race target

---

## Coaching Science Constants

| Metric | Safe Range | Action |
|--------|-----------|--------|
| ACWR | 0.8 – 1.3 | Sweet spot |
| ACWR > 1.3 | Overreaching | Caution, consider deload |
| ACWR > 1.5 | Danger Zone | Immediate recovery required |
| Cadence | ≥ 175 spm | Below 165 = overstriding risk |
| Aerobic Decoupling | < 5% | Above 5% = cardiac drift |
| Weekly volume increase | ≤ 15% | 15% Rule — never exceed |

**Taper protocol** (inject via `taper_factor`):
- Week -3: 75% of peak volume
- Week -2: 50% of peak volume  
- Week -1 (race week): 25% of peak volume

**TRIMP coefficients (Bannister):**
- Male: `weight = 0.64 × e^(1.92 × HRR)`
- Female: `weight = 0.86 × e^(1.67 × HRR)`

---

## Testing

- **Runner**: `pytest` — run with `python -m pytest tests/ -q`
- **All 228 tests must pass** before any commit
- **`tests/conftest.py`**: Session-level stubs for `google.genai`, `chromadb`, `app.services.rag_memory` — prevents ONNX download and API init
- **Patch paths**: Target the module where the symbol is **imported**, not where it's defined
  - ✅ `@patch("app.agents.coach.tools.calculate_acwr")`
  - ❌ `@patch("app.agents.coach.utils.calculate_acwr")`
- **No real API calls** in tests — always mock `google.genai` and Strava client
- New features require corresponding tests in `tests/test_<module>.py`

---

## Git Commit Convention

Format: `<type>: <short description>`

| Type | Usage |
|------|-------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructure, no behavior change |
| `docs` | Documentation only |
| `test` | Test additions/fixes |
| `chore` | Build, deps, config |

Always add trailer:
```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

---

## Config & Environment

- **`data/config.json`** — runtime config (gitignored). Auto-initialized from `config.example.json` on first boot.
- **`.env`** — secrets and infra settings (gitignored). See `.env.example` for template.
- **Key config fields**: `system_instruction`, `user_profile`, `max_hr`, `rest_hr`, `race_date`, `race_distance_km`, `threshold_pace_per_km`, `gender`, `model_name`, `scheduler`, `email_config`
- **Absolute paths**: All file paths computed via `Path(__file__).resolve().parent...` — never relative paths (Docker WORKDIR=/app breaks them)

---

## Docker

- Single volume mount: `.:/app` — the entire repo is the app directory
- Data persisted outside container: `data/` (SQLite, config, streams), `logs/`
- ChromaDB cache: `./data/chroma_cache:/root/.cache/chroma`
- Timezone forced: `TZ=Asia/Ho_Chi_Minh`
- Gitignored: `*.db-shm`, `*.db-wal`, `data/config.json`, `data/*.db`

---

## Key Files to Read Before Modifying

| Change type | Read first |
|-------------|-----------|
| Any code change | `docs/pragmatic_review_checklist.md` |
| New feature | `docs/feature_design_template.md` |
| DB schema change | `docs/database_design.md` |
| Agent/prompt change | `app/agents/coach/prompts.py` (full file) |
| Flow change | `app/agents/coach/utils.py` — `build_agent_context()` |
| Test change | `tests/conftest.py` — understand stub strategy |

---

## Roadmap Reference

See `README.md` → Roadmap section. **Do not create a new roadmap** — always continue from the existing one.
When completing items, check them off in README.md directly.

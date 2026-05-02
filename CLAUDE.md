# CLAUDE.md

**Cursor:** see also **AGENTS.md** and **`.cursor/rules/personal-ai-os.mdc`** for the same conventions in Cursor.

## Commands
```bash
python -m pytest tests/test_smoke.py -v        # smoke only — run FIRST (< 2s)
python -m pytest tests/test_e2e_local.py -v   # E2E: HTTP flows without Docker — run after refactor
python -m pytest tests/ -q                      # full suite (gate before commit — 0 failures)
python -m pytest tests/test_<module>.py -v     # targeted module
python -m pytest tests/test_telegram_chunking.py -v  # after any notification.py change
python -m pytest tests/ --cov=app --cov-report=html
docker compose up --build
bash scripts/pre-deploy-check.sh         # local gate: pytest + config + compose syntax
bash scripts/deploy-t440.sh              # T440: backup image + git pull + rebuild + health check
bash scripts/deploy-t440.sh --skip-pull # rebuild only (no git pull)
bash scripts/rollback-t440.sh            # restore airunningcoach:backup image
bash scripts/install-hooks.sh           # one-time: install pre-commit hooks (smoke + ruff + black)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Before commit: `docs/DELIVERY_CHECKLIST.md` (typed by change type). Pragmatic review: `docs/pragmatic_review_checklist.md`. New feature: `docs/feature_design_template.md`. Bug or feature: `docs/ISSUES.md`.

## Docker Log Debug Toolkit
`scripts/fetch-logs.sh` — fetch và filter log từ container `airunningcoach`.

App logs are written to `./logs/app.log` (bind-mounted: `./logs/:/app/logs`).
Daily rotation at midnight → `app.log.2026-04-18`, 30-day retention. Persists across container restarts.

```bash
# Tổng quan nhanh (count by level + last errors)
bash scripts/fetch-logs.sh --summary

# Lỗi trong 1 giờ qua
bash scripts/fetch-logs.sh -l ERROR --since 1h

# Log module cụ thể (news / coach / scheduler / webhook / ...)
bash scripts/fetch-logs.sh -m news -n 200

# Kết hợp: lỗi của news agent
bash scripts/fetch-logs.sh -l ERROR,WARNING -m news --since 2h

# Live tail toàn bộ
bash scripts/fetch-logs.sh --live

# Live tail filtered
bash scripts/fetch-logs.sh --live -l ERROR -m scheduler

# Khi container down — đọc từ ./logs/app.log* trên host
bash scripts/fetch-logs.sh --file --summary
bash scripts/fetch-logs.sh --file -l ERROR -m news
```

**Quy trình debug khi nhận báo lỗi:**
```
1. bash scripts/fetch-logs.sh --summary          # xác định level + module nào nhiều lỗi
2. bash scripts/fetch-logs.sh -l ERROR -m <mod> --since 1h   # xem chi tiết
3. Đọc code module bị lỗi → fix → chạy tests
4. bash scripts/deploy-t440.sh --skip-pull       # rebuild + verify
```

**Fallback thủ công** (khi container down):
```bash
tail -n 100 ./logs/app.log | grep -i error
cat ./logs/app.log* | grep -i error | tail -50
```

## Dev → Test → Deploy Workflow
Every code change must pass this gate before deploying to T440:

```
1. SMOKE  python -m pytest tests/test_smoke.py -v
          Catches ImportError and missing symbols before any logic runs.
          Run this first — fastest feedback (< 2s).

2. SANITY python -m pytest tests/test_sanity_flows.py -v
          58 flow-level regression tests: morning briefing guards, daily suggestion
          all branches, scheduler wrappers, Telegram command routing, news briefing,
          Strava webhooks, health endpoint, notification pipeline, agentic loop.
          Run after any agent/flow change to catch real user-facing regressions.
          No Docker required — mocks all I/O (Gemini, DB, Telegram). ~5s.

3. E2E    python -m pytest tests/test_e2e_local.py -v
          28 HTTP-level tests: health, Strava webhook flow, Telegram routing,
          scheduler startup, timezone utils, config concurrency.
          Run after any refactor to prove end-to-end paths still work.
          No Docker required — uses FastAPI TestClient.

4. UNIT   python -m pytest tests/ -q
          Full suite: unit + integration tests. 0 failures required.

5. DEPLOY bash scripts/pre-deploy-check.sh
          Runs: pytest suite + config loads + docker compose syntax.

6. T440   bash scripts/deploy-t440.sh
          git pull + docker rebuild + health check (90s timeout) +
          E2E curl smoke tests (/health, /console, /admin, /webhook,
          scheduler running, no errors in last 50 log lines).
```

**When to add a smoke test**: whenever a new public symbol is exported from a module
(new function in `prompts.py`, new class in a service, new handler). Add an import
assertion in `tests/test_smoke.py` under the matching class.

**Common failure patterns**:
- `ImportError` on smoke → function added to `agent.py` import but not implemented in `prompts.py`
- `pre-deploy-check` fails config → missing key in `config.example.json` or `.env`
- `deploy-t440` health timeout → container crash, check: `docker logs airunningcoach --tail 50`

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
- **`HttpOptions.timeout` is MILLISECONDS** (not seconds). `timeout=30000` = 30s. Incident: `timeout=30` → 30ms → X-Server-Timeout:1 → 400 rejected (2026-04-21). Always write `# Ns in ms` comment. Gate: `tests/test_sdk_contracts.py`.
- SDK numeric params: verify unit from source before using — Python convention (seconds) ≠ google-genai convention (milliseconds).
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

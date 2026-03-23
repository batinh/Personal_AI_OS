# Delivery Checklist — Personal AI OS

> **Purpose:** For every code change, this checklist defines the minimum set of tests to run before pushing to `main`. Follow the column matching what you changed.

---

## Quick Reference: Change → Tests

| What You Changed | Minimum Tests to Run | Full Regression? |
|---|---|---|
| Database schema / queries | `test_database`, `test_database_run_activity_raw` | Recommended |
| Config load/save/cache | `test_config` | No |
| Strava webhook endpoint | `test_webhooks` | Recommended |
| Telegram webhook / commands | `test_webhooks` | Recommended |
| StravaClient methods | `test_strava_client` | No |
| harvest_data() or execute_manual_sync() | `test_harvest` | No |
| Agent flows (chat, briefing, reflection, memory) | `test_agent`, `test_tools` | Recommended |
| Notification (Telegram, email) | `test_notification` | No |
| Sports science utils (TRIMP, ACWR, EF) | `test_utils` | No |
| Stream storage (file I/O) | `test_stream_storage` | No |
| Tool functions (tools.py) | `test_tools`, `test_tools_get_run_full_details` | No |
| Prompts only (prompts.py) | `test_agent` (smoke test) | No |
| Scheduler / cron setup | `test_webhooks` (smoke: /standup command) | No |
| Docker / deployment config | Full regression + manual Docker test | Yes |
| Any refactor touching multiple modules | **Full regression** | **Yes** |

---

## Detailed Checklists by Change Type

---

### 🔧 Change Type 1: Database Changes
*Applies to: `app/core/database.py`, SQL schema, migrations*

```bash
# Required
python -m pytest tests/test_database.py tests/test_database_run_activity_raw.py -v

# Also run if DB functions are called from agent flows
python -m pytest tests/test_agent.py tests/test_harvest.py -v

# Full regression (recommended for schema changes)
python -m pytest tests/ -q
```

**Manual verification:**
- [ ] `init_db()` runs without error on fresh database
- [ ] Existing data survives schema migration (test with production DB backup)
- [ ] UPSERT semantics: duplicate inserts don't create duplicate rows

---

### 🌐 Change Type 2: HTTP Endpoint Changes
*Applies to: `app/routers/webhooks.py`, `app/routers/admin.py`, `app/routers/dashboard.py`*

```bash
# Required
python -m pytest tests/test_webhooks.py -v

# If changing webhook-triggered flows
python -m pytest tests/test_strava_client.py tests/test_harvest.py -v
```

**Manual verification:**
- [ ] `GET /webhook?hub.verify_token=...&hub.challenge=...` returns challenge
- [ ] `POST /webhook` with create event starts background task
- [ ] `POST /telegram-webhook` with `/standup` sends Telegram message
- [ ] Admin UI loads at `/admin` with HTTP Basic auth

---

### 🤖 Change Type 3: AI Agent / Prompt Changes
*Applies to: `app/agents/coach/agent.py`, `app/agents/coach/flows/*.py`, `app/agents/coach/prompts.py`*

```bash
# Required
python -m pytest tests/test_agent.py tests/test_tools.py -v

# If touching utils used by flows
python -m pytest tests/test_utils.py -v
```

**Manual verification (production test):**
- [ ] Send a Telegram message and verify response is in Vietnamese
- [ ] Verify GCS score appears in analysis (not placeholder)
- [ ] Send `/standup` and verify briefing includes weather + ACWR status

---

### 🔌 Change Type 4: Strava Integration Changes
*Applies to: `app/agents/coach/strava_client.py`, `app/agents/coach/harvest.py`*

```bash
# Required
python -m pytest tests/test_strava_client.py tests/test_harvest.py -v

# If these changes affect webhook flow
python -m pytest tests/test_webhooks.py -v
```

**Manual verification:**
- [ ] `/sync 1` successfully syncs 1 activity (check logs for "Processed CSV")
- [ ] Token is cached (second request within 6h should show no auth call in logs)
- [ ] Non-run activities (Ride, Swim) are skipped in harvest

---

### ⚙️ Change Type 5: Config / Environment Changes
*Applies to: `app/core/config.py`, `data/config.json`, `config.example.json`, `.env`*

```bash
# Required
python -m pytest tests/test_config.py -v
```

**Manual verification:**
- [ ] Admin UI `/admin/save` saves config and reloads scheduler
- [ ] Deleting `data/config.json` → restart → auto-restored from example with WARNING log
- [ ] Config cache respects 60s TTL

---

### 📢 Change Type 6: Notification Changes
*Applies to: `app/core/notification.py`*

```bash
# Required
python -m pytest tests/test_notification.py -v
```

**Manual verification:**
- [ ] Test email via Admin UI → `/admin/test-email`
- [ ] Telegram message with `<b>bold</b>` renders correctly (not as raw HTML)

---

### 🧪 Change Type 7: Full Regression (Pre-release / Refactor)

Run before any merge to `main` involving multiple files:

```bash
python -m pytest tests/ -q
```

**Expected baseline (as of 2026-03-23 / commit 3750a5f):**

```
202 passed, 14 failed
```

**Gate:** Any new failure beyond the 14 known pre-existing failures = **BLOCK — do not push**.

**Known pre-existing failures (do not count as new regressions):**
```
test_agent.py::TestGenerateMorningBriefing::*                 (2 tests)
test_agent.py::TestExtractImplicitMemory::*                   (4 tests, 1 pass)
test_agent.py::TestGenerateWeeklyReflection::*                (2 tests)
test_database.py::TestRunActivities::test_gcs_placeholder_*  (1 test)
test_database.py::TestChatHistory::test_save/load*            (2 tests)
test_database.py::TestCoreMemory::test_inactive_overrides*    (1 test)
test_database_run_activity_raw.py::*stream_file_path*         (2 tests)
```

---

## Hotfix Protocol

When doing an **urgent hotfix** to production:

1. Fix the bug
2. Run targeted tests for the changed module only
3. Run `python -m pytest tests/ -q` — verify count doesn't increase beyond 14 failures
4. Push with commit message: `hotfix: <description>`
5. Update `TEST_EXECUTION_REPORT.md` trend table

---

## Post-Deployment Smoke Tests

After deploying to Docker / production, manually verify:

```bash
# 1. Strava webhook subscription verification
curl "https://your-domain.com/webhook?hub.verify_token=YOUR_TOKEN&hub.challenge=hello123"
# Expected: {"hub.challenge": "hello123"}

# 2. Telegram /standup command
# Send "/standup" to your bot → should receive morning briefing within 30s

# 3. Admin UI
# Open https://your-domain.com/admin → should load with Basic Auth prompt

# 4. Check Docker logs for startup diagnostics
docker logs <container_name> 2>&1 | head -20
# Expected lines:
# [STARTUP] DB path     : /app/data/os_core.db (exists: True)
# [STARTUP] Config path : /app/data/config.json (exists: True)
# [STARTUP] Config loaded. Model: models/gemini-2.0-flash
# ✅ System Ready. Scheduler Active.
```

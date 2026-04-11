# Personal AI OS — Coach Dyno

> An autonomous AI coaching agent that ingests your Strava data, applies sports science, and delivers personalized training guidance across Telegram, Strava, and Email — running 24/7 on a home lab.

[![Tests](https://img.shields.io/badge/tests-273%20passed-brightgreen?style=flat-square)](./docs/testing/TEST_EXECUTION_REPORT.md)
[![Python](https://img.shields.io/badge/python-3.11-blue?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-latest-009688?style=flat-square)](https://fastapi.tiangolo.com/)
[![Gemini](https://img.shields.io/badge/AI-Gemini%202.0%20Flash-4285F4?style=flat-square)](https://ai.google.dev/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square)](./docker-compose.yml)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square)](#license)

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Scheduled Tasks](#scheduled-tasks)
- [Testing](#testing)
- [Deployment](#deployment)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Overview

**Personal AI OS** is a self-hosted, agentic AI system built on FastAPI. Its first incarnation — **Coach Dyno** — acts as a personal running coach. It automatically ingests training data from Strava, computes sports science metrics (TRIMP, ACWR, Efficiency Factor, Cardiac Drift), reasons over them with Google Gemini, and delivers coaching output via Telegram, Strava activity descriptions, and email.

The system is designed as a **Modular Monolith** with a clear 5-layer architecture, making it extensible to other agent domains (work assistant, finance tracker) without architectural rewrites.

**Key design principles:**
- **Proactive, not reactive** — the system acts on schedules and events, not just user commands
- **Data-first reasoning** — AI reads from the database as the Single Source of Truth, eliminating hallucination
- **Lego Prompt Engine** — 8-layer modular prompt system where Task, Analysis, Structure, and Format are independently configurable via the Admin UI
- **Production-resilient** — exponential backoff on LLM errors, SQLite WAL mode, token caching, idempotent writes

---

## Key Features

| Feature | Description |
|---|---|
| **Strava Webhook Ingest** | Real-time activity ingestion. Parses splits, laps, HR streams and saves to SQLite + JSON data lake |
| **AI Run Analysis** | Gemini analyzes pace strategy, biomechanics, cardiac drift, and GCS (Goal Confidence Score 0–100%) |
| **Omni-channel Output** | Single analysis → HTML for Telegram, plain-text for Strava description, rich HTML for email |
| **Morning Briefing** | Daily standup: ACWR safety check, today's plan, weather advisory, injury memory injection |
| **Weekly Reflection** | Sunday cron: reviews the week's compliance, GCS trend, and sets next week's target volume |
| **Autonomous Memory** | Extracts implicit facts from chat history (injuries, goals, gear) into a persistent Core Memory |
| **RAG Long-term Memory** | ChromaDB semantic search over all historical run analyses and reflections |
| **Sports Science Engine** | Pure Python TRIMP, ACWR, Efficiency Factor, Aerobic Decoupling, Training Phase calculator |
| **Admin Dashboard** | Web UI to configure AI persona, sports parameters, scheduler times, and email settings |
| **Manual Sync** | `/sync` command to backfill historical activities with RAG gap detection |
| **News Briefings** | Daily news digest via RSS feeds (VnExpress, Tuổi Trẻ, BBC Vietnamese) — morning summary at 07:00, afternoon update at 17:00, delivered via Telegram |

---

## Architecture

### System Layers

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Edge & Triggers                                   │
│  Strava Webhook │ Telegram Webhook │ APScheduler Cron       │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Cognitive Core (FastAPI + BackgroundTasks)        │
│  ┌─────────────┐  ┌──────────────────────────────────────┐  │
│  │ Coach Agent │  │ Memory Manager (extract_implicit_    │  │
│  │ - chat      │  │ memory, insert_memory, RAG recall)   │  │
│  │ - analyze   │  └──────────────────────────────────────┘  │
│  │ - briefing  │  ┌──────────────────────────────────────┐  │
│  │ - reflect   │  │ Lego Prompt Engine (8 layers)        │  │
│  └─────────────┘  │ System │ Context │ Task │ Analysis   │  │
│                   │ Structure │ Format │ Weather │ Memory │  │
│                   └──────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Memory                                            │
│  Working Memory (chat session) │ Core Memory (SQLite)      │
│  Episodic Memory (ChromaDB RAG) │ Stream Data Lake (JSON)  │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: Domain Services                                   │
│  Sports Science │ Weather API │ Notification │ Scheduler   │
├─────────────────────────────────────────────────────────────┤
│  Layer 5: Infrastructure                                    │
│  SQLite (WAL mode) │ ChromaDB 0.4.24 │ JSON Files          │
│  Nginx Proxy Manager │ DuckDNS │ Docker Compose            │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow: Activity Ingestion

```
Strava completes run
      │
      ▼
POST /webhook (Strava)
      │
      ▼
BackgroundTask: run_strava_workflow()
      │
      ├─ 1. Fetch activity detail + streams (StravaClient)
      ├─ 2. Save to SQLite (save_run_activity) ← data integrity first
      ├─ 3. Save raw streams to JSON file (stream_storage)
      ├─ 4. Gemini analysis (analyze_run_with_gemini)
      ├─ 5. Embed analysis into ChromaDB RAG (rag_db.memorize)
      └─ 6. Fan-out notifications:
             ├─ Telegram (HTML format)
             ├─ Strava description update (plain-text)
             └─ Email (rich HTML)
```

### Prompt Architecture (8-Layer Lego Engine)

| Layer | Module | Purpose | Configurable |
|---|---|---|---|
| 1 | `build_system_instruction` | AI persona, tool discipline | ✅ Admin UI |
| 2 | `get_shared_context_block` | ACWR, phase, weekly limits | Dynamic |
| 3 | `DEFAULT_ANALYSIS_TASK` | Analysis objectives | ✅ Admin UI |
| 4 | `DEFAULT_ANALYSIS_REQUIREMENTS` | Evaluation criteria | ✅ Admin UI |
| 5 | `DEFAULT_REPORT_STRUCTURE` | Output schema template | ✅ Admin UI |
| 6 | `CHAT_FORMAT_RULES` | Telegram HTML rules | Fixed |
| 7 | `WEATHER_INSTRUCTION` | Weather safety advisory | Dynamic |
| 8 | `MEMORY_EXTRACTION_PROMPT` | Implicit memory extraction | Fixed |

---

## Project Structure

```
Personal_AI_OS/
├── app/
│   ├── main.py                    # FastAPI app, startup/shutdown lifecycle
│   ├── core/
│   │   ├── config.py              # Config load/save with TTL cache + auto-init
│   │   ├── database.py            # All SQLite CRUD (WAL mode, context manager)
│   │   ├── notification.py        # Telegram, Email, typing indicator
│   │   ├── user_context.py        # Primary user ID resolution
│   │   └── state.py               # Service pause/resume state
│   ├── agents/coach/
│   │   ├── agent.py               # Thin orchestrator, handle_telegram_chat
│   │   ├── prompts.py             # 8-layer Lego Prompt Engine
│   │   ├── tools.py               # Gemini AFC tools (set_plan, search_memory…)
│   │   ├── utils.py               # TRIMP, ACWR, AgentContext, send_with_retry
│   │   ├── strava_client.py       # Strava API client (token caching)
│   │   ├── harvest.py             # Cron harvest + manual sync flow
│   │   └── flows/
│   │       ├── run_analysis.py    # analyze_run_with_gemini()
│   │       ├── morning_briefing.py # generate_morning_briefing()
│   │       ├── weekly_reflection.py # generate_weekly_reflection()
│   │       └── memory_extraction.py # extract_implicit_memory()
│   ├── agents/news/
│   │   ├── agent.py               # News orchestrator: fetch → dedup → summarize → send
│   │   ├── feeds.py               # RSS feed fetcher (feedparser, per-feed isolation)
│   │   └── prompts.py             # Morning/afternoon prompt builders
│   ├── routers/
│   │   ├── console.py             # Unified control console (settings + metrics + memory + system)
│   │   ├── webhooks.py            # POST/GET /webhook, POST /telegram-webhook
│   │   ├── admin.py               # Legacy — redirects to /console
│   │   └── dashboard.py           # Legacy — redirects to /console
│   └── services/
│       ├── scheduler.py           # APScheduler cron jobs
│       ├── rag_memory.py          # ChromaDB wrapper (memorize/recall/forget)
│       ├── stream_storage.py      # JSON stream file I/O
│       ├── weather.py             # OpenWeatherMap integration
│       └── backup.py              # Daily DB + config backup
├── tests/                         # 216 tests, 100% pass rate
│   ├── conftest.py                # Session-level stubs (google.genai, chromadb)
│   ├── test_webhooks.py           # HTTP endpoint tests (18)
│   ├── test_strava_client.py      # StravaClient unit tests (20)
│   ├── test_config.py             # Config management tests (8)
│   ├── test_harvest.py            # Harvest + sync flow tests (13)
│   ├── test_agent.py              # Agent flow integration tests (20)
│   ├── test_database.py           # DB CRUD tests (38)
│   ├── test_utils.py              # Sports science math tests (37)
│   └── test_notification.py       # Notification tests (20)
├── docs/
│   ├── testing/                   # Full test documentation suite
│   └── architecture.md
├── templates/                     # Jinja2 HTML templates (admin, dashboard)
├── data/                          # Runtime data (gitignored)
│   ├── os_core.db                 # SQLite database
│   ├── config.json                # Active configuration
│   └── streams/                   # Raw Strava JSON stream files
├── infra/                         # Nginx + DuckDNS config
├── scripts/
│   └── deploy-t440.sh             # Automated deploy: push → SSH pull → rebuild → e2e test
├── config.example.json            # Config template (copy to data/config.json)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (recommended for production)
- A Strava account with API access
- A Telegram Bot (via [@BotFather](https://t.me/botfather))
- Google Gemini API key ([Google AI Studio](https://aistudio.google.com/))
- OpenWeatherMap API key (free tier)

### Local Development

```bash
# 1. Clone the repository
git clone https://github.com/batinh/Personal_AI_OS.git
cd Personal_AI_OS

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing

# 3. Set up environment variables
# Create .env file with your keys (see Environment Variables section below)

# 4. Set up configuration
cp config.example.json data/config.json
# Edit data/config.json with your AI persona and sports parameters

# 5. Run the application
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The app auto-initializes the SQLite database and scheduler on startup. Check the logs for:
```
[STARTUP] DB path     : /path/to/data/os_core.db (exists: True)
[STARTUP] Config loaded. Model: models/gemini-2.0-flash
✅ System Ready. Scheduler Active.
```

### Environment Variables

Create a `.env` file in the project root:

```env
# Google Gemini AI
GOOGLE_API_KEY=your_gemini_api_key

# Strava API (OAuth)
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REFRESH_TOKEN=your_refresh_token
STRAVA_ATHLETE_ID=your_athlete_id

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_personal_chat_id

# Strava Webhook Verification
VERIFY_TOKEN=your_chosen_secret_string

# Admin Dashboard
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_secure_password

# Weather Service
OPENWEATHER_API_KEY=your_openweather_key
OPENWEATHER_CITY=Ho Chi Minh City
OPENWEATHER_COUNTRY_CODE=VN

# Dynamic DNS (optional, for home lab)
DUCKDNS_TOKEN=your_duckdns_token
DUCKDNS_SUB_DOMAIN=your_subdomain

# Timezone
TZ=Asia/Ho_Chi_Minh

# ChromaDB (Docker path)
CHROMADB_CACHE_DIR=/app/data/chroma_cache
```

---

## Configuration

Runtime configuration is managed via `data/config.json` and the Console at `/console?tab=settings`.

**If `data/config.json` is missing**, the system auto-copies `config.example.json` on startup and logs a warning — the system will run with example defaults until you update via the Admin UI.

Key configuration fields:

| Field | Description |
|---|---|
| `system_instruction` | AI persona and coaching style |
| `user_profile` | Athlete profile injected into every prompt |
| `task_description` | Analysis task template |
| `analysis_requirements` | What the AI should evaluate |
| `report_structure` | Output format template |
| `max_hr` / `rest_hr` | HR parameters for TRIMP calculation |
| `race_date` | Target race date (drives Training Phase logic) |
| `model_name` | Gemini model (default: `models/gemini-2.0-flash`) |
| `scheduler.briefing_time` | Daily briefing cron time (default: `06:00`) |
| `scheduler.harvest_hours` | Hours to auto-harvest Strava (default: `0,6,12,18`) |
| `email_config` | SMTP settings for email notifications |
| `news_agent.enabled` | Enable/disable news briefings |
| `news_agent.morning_time` | Morning news cron time (default: `07:00`) |
| `news_agent.afternoon_time` | Afternoon news cron time (default: `17:00`) |
| `news_agent.telegram_chat_id` | Telegram target for news (empty = same chat as coach) |
| `news_agent.feeds` | List of RSS feed sources (`name` + `url`) |

---

## API Reference

### Strava Webhook

| Method | Path | Description |
|---|---|---|
| `GET` | `/webhook` | Strava subscription verification handshake |
| `POST` | `/webhook` | Receive Strava events (activity create/delete) |

**Strava Webhook Payload (create):**
```json
{
  "object_type": "activity",
  "aspect_type": "create",
  "object_id": 12345678
}
```

### Telegram Webhook

| Method | Path | Description |
|---|---|---|
| `POST` | `/telegram-webhook` | Receive Telegram messages and commands |

**Supported Commands:**

| Command | Description |
|---|---|
| `/sync` | Sync 3 most recent Strava activities |
| `/sync 10` | Sync last 10 activities |
| `/sync month` | Sync last 30 days (up to 50 activities) |
| `/standup` | Trigger morning briefing immediately |
| `/clear` or `/reset` | Clear conversation history |
| Any text | AI coaching chat |

### System

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Health check (DB, config, scheduler status) |

### Console (Unified Control Panel)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/console` | HTTP Basic | Unified console: overview, training log, memory, settings, system |
| `POST` | `/console/save` | HTTP Basic | Save configuration + reload scheduler |
| `GET` | `/console/test-email` | HTTP Basic | Send test email |
| `POST` | `/console/toggle` | HTTP Basic | Pause/resume AI service |

### Legacy Redirects

| Method | Path | Redirects To |
|---|---|---|
| `GET` | `/admin` | `/console?tab=settings` |
| `GET` | `/dashboard` | `/console?tab=overview` |

---

## Scheduled Tasks

All cron times are configured via the Admin UI (stored in `data/config.json`).

| Job | Default Schedule | Description |
|---|---|---|
| **Morning Briefing** | Daily at `06:00` | Fetches weather, checks ACWR, delivers today's plan via Telegram |
| **Morning News** | Daily at `07:00` | RSS news digest (VnExpress, Tuổi Trẻ, BBC Viet) — summarized by Gemini, sent via Telegram |
| **Afternoon News** | Daily at `17:00` | Afternoon news update with deduplication against morning's articles |
| **Auto Harvest** | `00:15`, `06:15`, `12:15`, `18:15` | Syncs last 10 Strava activities to SQLite |
| **Weekly Reflection** | Sundays at `20:00` | Reviews the week, sets next week's target volume, saves to RAG |
| **Daily Backup** | Daily at `02:00` | Archives `os_core.db` + `config.json` to `backups/` |

---

## Testing

```bash
# Run full test suite
python -m pytest tests/ -v

# Run with coverage report
python -m pytest tests/ --cov=app --cov-report=html

# Run specific module
python -m pytest tests/test_webhooks.py -v

# Quick pass/fail check
python -m pytest tests/ -q
```

**Current status: 267 passed / 0 failed**

| Module | Tests | Coverage |
|---|---|---|
| `test_webhooks.py` | 18 | HTTP endpoints, Strava/Telegram routing, workflow orchestration |
| `test_strava_client.py` | 20 | Token caching, API error handling, all methods |
| `test_config.py` | 8 | Load/save, TTL cache, auto-init, corrupted file resilience |
| `test_harvest.py` | 13 | Cron harvest, manual sync, RAG gap detection, rate limiting |
| `test_agent.py` | 20 | Telegram chat, morning briefing, weekly reflection, memory extraction |
| `test_database.py` | 38 | All CRUD operations, user isolation, ACWR calculations |
| `test_utils.py` | 37 | TRIMP, ACWR, Efficiency Factor, Decoupling, Training Phase |
| `test_notification.py` | 20 | Telegram HTML sanitization, SMTP, retry logic |
| `test_stream_storage.py` | 12 | File I/O, path resolution, error handling |
| `test_tools.py` | 24 | All AI tool functions |
| `test_news_feeds.py` | 14 | RSS fetch, per-feed isolation, timeout, malformed XML |
| `test_news_prompts.py` | 11 | Prompt builders, curly brace safety, Vietnamese zone compliance |
| `test_news_agent.py` | 14 | Orchestrator, Telegram routing (Option B), dedup, truncation, error handling |

See [`docs/testing/`](./docs/testing/) for full test strategy, specs, and delivery checklist.

---

## Deployment

### Docker Compose (Recommended)

```bash
# 1. Ensure .env and data/config.json are in place
# 2. Start all services
docker compose up -d

# 3. Verify startup logs
docker logs airunningcoach --tail 20

# 4. Register Strava webhook subscription (one-time)
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "callback_url=https://your-domain.com/webhook" \
  -d "verify_token=YOUR_VERIFY_TOKEN"

# 5. Register Telegram webhook (one-time)
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://your-domain.com/telegram-webhook"
```

The `docker-compose.yml` includes:
- **`ai-coach`** — the FastAPI application on port 8000
- **`nginx-proxy`** — Nginx Proxy Manager for HTTPS termination
- **`duckdns`** — Dynamic DNS updater for home lab public access

### Volume Mounts

The container mounts the entire project root (`.:/app`), so `data/config.json`, `data/os_core.db`, and stream files persist across container restarts automatically.

```yaml
volumes:
  - .:/app                          # Source code + data + config
  - ./data/chroma_cache:/root/.cache/chroma  # ChromaDB ONNX model cache
  - ./logs/:/app/logs               # Application logs
```

### Automated Deploy (RPi5 → T440)

For the home lab setup where code is edited on RPi5 and deployed to T440:

```bash
# Full deploy: push → SSH pull → rebuild → health check → e2e tests
./scripts/deploy-t440.sh

# Deploy only (code already pushed)
./scripts/deploy-t440.sh --skip-push
```

Prerequisites: SSH key auth to T440, `tinhn` user in Docker group.

### Post-Deployment Smoke Test

```bash
# 1. Health check
curl http://your-host:8000/health
# Expected: {"status":"healthy","db":"ok","config":"ok","scheduler":"running"}

# 2. Verify Strava webhook endpoint
curl "https://your-domain.com/webhook?hub.verify_token=YOUR_TOKEN&hub.challenge=test"
# Expected: {"hub.challenge": "test"}

# 3. Check Console UI
# Open: https://your-domain.com/console
```

---

## Roadmap

### ✅ Completed

- [x] Strava webhook ingestion with stream parsing
- [x] Gemini AI run analysis with GCS scoring
- [x] Omni-channel output (Telegram, Strava, Email)
- [x] Morning briefing with weather awareness
- [x] Weekly reflection with RAG memory injection
- [x] Autonomous implicit memory extraction
- [x] SQLite WAL mode + connection context manager
- [x] 4-layer memory system (working, active facts, archive, episodic RAG)
- [x] Modular agent refactor (flows/ architecture)
- [x] 267-test production test suite
- [x] Admin dashboard with dynamic scheduler configuration
- [x] News Agent — RSS feed digest via Gemini, morning + afternoon Telegram delivery, 24h dedup

### 🚧 In Progress / Planned

- [ ] **Race Day Forecast** — 5-day weather forecast injection during Taper week for race strategy planning
- [ ] **HRV/Resting HR Integration** — event-driven training adjustment from Garmin/Apple Health overnight signals
- [ ] **FastAPI Lifespan Migration** — replace deprecated `@app.on_event` with `lifespan` context manager
- [x] **Health Endpoint** — `/health` for Docker health check and uptime monitoring
- [x] **Unified Console** — `/console` merges admin, dashboard, and memory view into a single tabbed UI
- [x] **Automated Deploy Script** — `scripts/deploy-t440.sh` for RPi5→T440 SSH deploy with health check + e2e tests
- [ ] **Multi-Agent Expansion** — Work Agent and Finance Agent sharing the same memory infrastructure

### 🔮 Future: SaaS Multi-Tenant

The current architecture is single-tenant (one `TELEGRAM_CHAT_ID` per deployment). Multi-tenant migration path:

- [ ] Identity router: manage multiple Telegram + Strava ID pairs
- [ ] All DB tables already have `user_id` column — schema is ready
- [ ] ChromaDB metadata filtering by `user_id` already implemented
- [ ] Replace `get_primary_user_id()` with request-scoped user resolution

---

## Contributing

This is a personal project but contributions are welcome.

### AI assistants (Claude Code & Cursor)

| Tool | Where to read |
|------|----------------|
| **Claude Code** | [CLAUDE.md](CLAUDE.md), [.claudeignore](.claudeignore) |
| **Cursor** | [AGENTS.md](AGENTS.md), [.cursor/rules/personal-ai-os.mdc](.cursor/rules/personal-ai-os.mdc), [.cursorignore](.cursorignore) |

`CLAUDE.md` is the canonical playbook; `AGENTS.md` points Cursor at the same gates and conventions. Keep **`.cursorignore`** aligned with **`.claudeignore`** when you add ignore patterns.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Follow the [zone conventions](./Rules.txt):
   - **ZONE 1**: Source code in English (functions, variables, docstrings)
   - **ZONE 2**: User-facing AI output in Vietnamese
   - **ZONE 3**: Transition layer — Python logic in English, f-string templates in Vietnamese
4. Add tests for new functionality (see [`docs/testing/DELIVERY_CHECKLIST.md`](./docs/testing/DELIVERY_CHECKLIST.md))
5. Run `python -m pytest tests/ -q` — ensure all tests pass, 0 new failures
6. Submit a pull request

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

*Built on a Lenovo T440 home lab, developed on RPi5. Powered by Google Gemini 2.0 Flash.*
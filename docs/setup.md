# Setup Guide — Personal AI OS

This guide covers everything needed to go from zero to a running instance.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.11-slim for Docker |
| Docker & Compose | v2+ | `docker compose` (not `docker-compose`) |
| Strava account | — | With Developer App created |
| Telegram Bot | — | Created via [@BotFather](https://t.me/botfather) |
| Google Gemini API key | — | [Google AI Studio](https://aistudio.google.com/) |
| OpenWeatherMap key | — | Free tier sufficient |

---

## 1. Clone and Install

```bash
git clone https://github.com/batinh/Personal_AI_OS.git
cd Personal_AI_OS

# Runtime dependencies
pip install -r requirements.txt

# Dev / test dependencies
pip install -r requirements-dev.txt
```

---

## 2. Environment Variables

Create a `.env` file in the project root. **Never commit this file.**

```env
# ── AI ──────────────────────────────────────────────────────────────
GOOGLE_API_KEY=your_gemini_api_key

# ── Strava ──────────────────────────────────────────────────────────
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REFRESH_TOKEN=your_refresh_token
STRAVA_ATHLETE_ID=your_athlete_id

# ── Telegram ────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_personal_chat_id

# ── Strava Webhook Verification ────────────────────────────────────
VERIFY_TOKEN=choose_any_secret_string

# ── Admin UI ────────────────────────────────────────────────────────
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_secure_password

# ── Weather ─────────────────────────────────────────────────────────
OPENWEATHER_API_KEY=your_openweather_key
OPENWEATHER_CITY=Ho Chi Minh City
OPENWEATHER_COUNTRY_CODE=VN

# ── Dynamic DNS (optional, for home lab) ───────────────────────────
DUCKDNS_TOKEN=your_duckdns_token
DUCKDNS_SUB_DOMAIN=your_subdomain

# ── Timezone ────────────────────────────────────────────────────────
TZ=Asia/Ho_Chi_Minh

# ── ChromaDB (Docker path) ──────────────────────────────────────────
CHROMADB_CACHE_DIR=/app/data/chroma_cache
```

### How to get each credential

**Google Gemini API key:**
1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Create an API key → copy to `GOOGLE_API_KEY`

**Strava credentials:**
1. [Strava Developers](https://www.strava.com/settings/api) → create application
2. Note `Client ID` and `Client Secret`
3. To get a `REFRESH_TOKEN` (OAuth): run the one-time auth flow described in [Strava docs](https://developers.strava.com/docs/getting-started/)
4. `STRAVA_ATHLETE_ID` is your numeric Strava user ID (visible in your profile URL)

**Telegram Bot:**
1. Message [@BotFather](https://t.me/botfather) → `/newbot`
2. Copy the bot token → `TELEGRAM_BOT_TOKEN`
3. Start a chat with your bot, then visit:
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
4. Note `chat.id` from the response → `TELEGRAM_CHAT_ID`

---

## 3. Runtime Configuration

Copy the example config and edit it via the Admin UI:

```bash
cp config.example.json data/config.json
```

On first startup, if `data/config.json` is missing, the system **auto-copies** `config.example.json` with a warning. You can then edit via `/console?tab=settings`.

Key fields to configure after first boot:

| Field | What to set |
|---|---|
| `system_instruction` | Your coach's personality and coaching style |
| `user_profile` | Your athlete profile (age, FTP, target race, gear) |
| `max_hr` / `rest_hr` | Your HR parameters for TRIMP calculation |
| `race_date` | Target race date (ISO format: `YYYY-MM-DD`) |
| `race_distance_km` | Target race distance |
| `model_name` | Gemini model (default: `models/gemini-2.0-flash`) |
| `email_config` | SMTP settings for email notifications |
| `news_agent.feeds` | List of RSS feed URLs for news briefings |

---

## 4. Local Development

```bash
# Start the server with hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify startup in logs:
```
[STARTUP] DB path     : .../data/os_core.db (exists: True)
[STARTUP] Config path : .../data/config.json (exists: True)
[STARTUP] Config loaded. Model: models/gemini-2.0-flash
✅ System Ready. Scheduler Active.
```

Open the console at: `http://localhost:8000/console` (Basic Auth with `ADMIN_USERNAME`/`ADMIN_PASSWORD`)

---

## 5. Docker Compose (Production)

```bash
# Build and start all services in background
docker compose up --build -d

# Tail logs
docker logs airunningcoach -f

# Health check
curl http://localhost:8000/health
# → {"status":"healthy","db":"ok","config":"ok","scheduler":"running"}
```

### Volume layout

```
./              → /app          (source code + data + config)
./data/chroma_cache → /root/.cache/chroma  (ChromaDB ONNX model cache)
./logs/         → /app/logs     (application logs)
```

The container has a `HEALTHCHECK` directive — Docker will restart the container if `/health` becomes unreachable for 3 consecutive checks (1-minute interval).

---

## 6. One-Time Webhook Registration

These steps are performed once per deployment URL change.

### Strava Webhook

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -d "client_id=$STRAVA_CLIENT_ID" \
  -d "client_secret=$STRAVA_CLIENT_SECRET" \
  -d "callback_url=https://your-domain.com/webhook" \
  -d "verify_token=$VERIFY_TOKEN"
```

Verify it worked:
```bash
curl "https://your-domain.com/webhook?hub.verify_token=$VERIFY_TOKEN&hub.challenge=test"
# → {"hub.challenge": "test"}
```

### Telegram Webhook

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://your-domain.com/telegram-webhook"
```

---

## 7. Running Tests

```bash
# Full suite (must pass: 273/273)
python -m pytest tests/ -q

# With coverage report
python -m pytest tests/ --cov=app --cov-report=html
# Open htmlcov/index.html

# Pre-deploy check (tests + config validation + docker syntax)
./scripts/pre-deploy-check.sh
```

---

## 8. Home Lab Deploy (RPi5 → T440)

For the setup where code is edited on RPi5 and deployed to T440:

```bash
# Full automated deploy: push → SSH pull → rebuild → health check → e2e tests
./scripts/deploy-t440.sh

# Deploy only (skip git push — code already pushed)
./scripts/deploy-t440.sh --skip-push
```

Prerequisites:
- SSH key auth: `ssh -p 8922 tinhn@192.168.1.89` (no password prompt)
- T440 user in Docker group: `sudo usermod -aG docker tinhn`

---

## 9. Troubleshooting

### App won't start

```bash
docker logs airunningcoach --tail 50
```

Common causes:
- Missing `.env` file → `GOOGLE_API_KEY` not set
- `data/config.json` corrupted → delete and restart (auto-restored from example)
- Port 8000 already in use → `lsof -i :8000`

### Scheduler not running

Check health endpoint:
```bash
curl http://localhost:8000/health
# "scheduler": "stopped" → restart container
```

APScheduler will stop if an unhandled exception occurs in a job. Check Docker logs for `[SCHEDULER]` error lines.

### Strava webhook not receiving events

1. Verify subscription exists: `GET https://www.strava.com/api/v3/push_subscriptions?client_id=...&client_secret=...`
2. Verify your domain resolves and the `/webhook` endpoint returns 200 for GET requests
3. Check DuckDNS is updating (if using dynamic IP)

### ChromaDB errors on first start

The first startup downloads ONNX model files to `data/chroma_cache/`. This can take 1–2 minutes on a slow connection. The Docker volume mount ensures they persist across restarts.

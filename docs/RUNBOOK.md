# Runbook — Personal AI OS

Operational procedures for the home lab deployment (RPi5 dev → T440 production).

---

## Deployment

### Standard deploy (RPi5 → T440)

```bash
# Full: push code → SSH pull → rebuild → health check → e2e tests
./scripts/deploy-t440.sh

# If code is already pushed
./scripts/deploy-t440.sh --skip-push
```

The script:
1. Pushes current branch to origin (unless `--skip-push`)
2. SSHs into T440 (`-p 8922 tinhn@192.168.1.89`)
3. Runs `git pull --ff-only` in `~/repo/Personal_AI_OS`
4. Runs `docker compose up --build -d`
5. Polls `/health` every 5s for up to 90s
6. Runs smoke tests: `/health`, `/console`, `/admin`, `/webhook` (GET), scheduler status, recent log errors
7. Exits 0 on full pass, 1 on any failure

### Pre-deploy check (run locally first)

```bash
./scripts/pre-deploy-check.sh
```

Checks pytest suite, config loads, and docker compose syntax. Must exit 0 before deploying.

### Manual deploy steps

If the script fails:

```bash
# 1. Push from RPi5
git push origin main

# 2. SSH into T440
ssh -p 8922 tinhn@192.168.1.89

# 3. On T440
cd ~/repo/Personal_AI_OS
git pull --ff-only
docker compose up --build -d

# 4. Verify
curl http://localhost:8000/health
docker logs airunningcoach --tail 20
```

---

## Health Monitoring

### Health endpoint

```bash
curl http://192.168.1.89:8000/health
```

Expected response:
```json
{"status":"healthy","db":"ok","config":"ok","scheduler":"running"}
```

| Field | Healthy value | Action if not |
|---|---|---|
| `status` | `"healthy"` | Check `db` and `config` fields |
| `db` | `"ok"` | Verify `data/os_core.db` exists inside container |
| `config` | `"ok"` | Verify `data/config.json` exists inside container |
| `scheduler` | `"running"` | Check logs for unhandled job exceptions |

### Docker health status

```bash
docker ps
# CONTAINER STATUS column should show "(healthy)"
```

If `(unhealthy)`: the container's HEALTHCHECK failed 3 times — Docker may restart it automatically. Check:
```bash
docker inspect airunningcoach | grep -A5 Health
```

### Smoke test suite (manual)

```bash
# Run the E2E smoke tests from the deploy script
T440="http://192.168.1.89:8000"
curl -s -o /dev/null -w "%{http_code}" "$T440/health"     # expect 200
curl -s -o /dev/null -w "%{http_code}" "$T440/webhook"    # expect 200 (Strava GET)
curl -s -o /dev/null -w "%{http_code}" "$T440/console"    # expect 401 (auth required)
curl -s "$T440/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['scheduler'])"  # expect running
```

---

## Common Issues and Fixes

### Container won't start

```bash
docker logs airunningcoach --tail 50
```

**`GOOGLE_API_KEY not set`** → verify `.env` file exists and has the key  
**`Config is EMPTY`** → delete `data/config.json` and restart (auto-restored from example)  
**Port conflict** → `lsof -i :8000` on T440  

### Scheduler stopped

```bash
docker logs airunningcoach --tail 100 | grep -i "scheduler\|error\|exception"
```

APScheduler stops if a job raises an unhandled exception. Restart to recover:
```bash
docker compose restart
```

To prevent recurrence, identify the failing job from logs and fix the underlying cause.

### Strava webhook not delivering events

1. Verify subscription exists:
```bash
curl "https://www.strava.com/api/v3/push_subscriptions?client_id=$STRAVA_CLIENT_ID&client_secret=$STRAVA_CLIENT_SECRET"
```

2. Verify DuckDNS resolves to current T440 IP:
```bash
nslookup your-subdomain.duckdns.org
```

3. Verify Strava can reach the endpoint:
```bash
curl "https://your-domain.com/webhook?hub.verify_token=$VERIFY_TOKEN&hub.challenge=probe"
# → {"hub.challenge": "probe"}
```

### ChromaDB errors on startup

First startup downloads ONNX model files (~50MB). If the Docker volume wasn't mounted:
```bash
docker exec airunningcoach ls /root/.cache/chroma/onnx_models/
```

If empty, let it download once — this is normal. Subsequent starts are fast.

### git pull fails on T440 (non-fast-forward)

```bash
ssh -p 8922 tinhn@192.168.1.89 "cd ~/repo/Personal_AI_OS && git status"
```

If there are local modifications on T440 (should not happen — T440 is deploy-only):
```bash
ssh -p 8922 tinhn@192.168.1.89 "cd ~/repo/Personal_AI_OS && git stash && git pull --ff-only"
```

---

## Rollback

### Roll back to the previous commit

```bash
# On RPi5: find the commit to roll back to
git log --oneline -10

# Push the previous commit as a new deploy
git revert HEAD --no-edit
git push origin main
./scripts/deploy-t440.sh --skip-push
```

Avoid `git push --force` to `main` — it rewrites shared history.

### Emergency: restart from last known good state

```bash
ssh -p 8922 tinhn@192.168.1.89
cd ~/repo/Personal_AI_OS

# Find last good commit
git log --oneline -10

# Check out that commit (detached HEAD)
git checkout <commit-hash>

# Rebuild
docker compose up --build -d

# Verify
curl http://localhost:8000/health
```

---

## Backup and Restore

### Automatic daily backup

The `backup.py` service runs daily at `02:00` and archives:
- `data/os_core.db` → `backups/os_core_YYYYMMDD.db`
- `data/config.json` → `backups/config_YYYYMMDD.json`

Check backups:
```bash
docker exec airunningcoach ls /app/backups/
```

### Manual backup

```bash
ssh -p 8922 tinhn@192.168.1.89
cd ~/repo/Personal_AI_OS
cp data/os_core.db data/os_core_backup_$(date +%Y%m%d).db
cp data/config.json data/config_backup_$(date +%Y%m%d).json
```

### Restore database

```bash
# Stop the container
docker compose stop

# Replace the DB
cp data/os_core_backup_YYYYMMDD.db data/os_core.db

# Restart
docker compose up -d
```

---

## Container Reference

| Container | Image | Port | Purpose |
|---|---|---|---|
| `airunningcoach` | `personal_ai_os-ai-coach` | 8000 | FastAPI application |
| `nginx-proxy` | `jc21/nginx-proxy-manager` | 80, 443, 81 | HTTPS termination + reverse proxy |
| `duckdns` | `linuxserver/duckdns` | — | Dynamic DNS updater |

```bash
# All containers
docker compose ps

# App logs
docker logs airunningcoach -f

# Nginx logs
docker logs nginx-proxy -f
```

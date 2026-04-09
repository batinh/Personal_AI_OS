#!/usr/bin/env bash
# deploy-t440.sh — Deploy locally on T440 (T440 is the primary dev/deploy machine).
#
# Usage:
#   ./scripts/deploy-t440.sh             # git pull + rebuild + health check
#   ./scripts/deploy-t440.sh --skip-pull # rebuild only (skip git pull)
#
# Prerequisites:
#   - Running on T440 directly
#   - User in docker group: sudo usermod -aG docker tinhn

set -euo pipefail

# --- Config ---
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="http://localhost:8000"
HEALTH_ENDPOINT="${BASE_URL}/health"
HEALTH_TIMEOUT=90
HEALTH_INTERVAL=5

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# --- Step 0: Parse args ---
SKIP_PULL=false
for arg in "$@"; do
    case "$arg" in
        --skip-pull) SKIP_PULL=true ;;
    esac
done

# --- Step 1: Pull latest code ---
if [ "$SKIP_PULL" = false ]; then
    log "Pulling latest code..."
    git -C "$REPO_DIR" pull --ff-only || fail "git pull failed"
    log "Pull complete"
else
    log "Skipping pull (--skip-pull)"
fi

# --- Step 2: Rebuild and restart containers ---
log "Rebuilding and restarting containers..."
cd "$REPO_DIR"
docker compose up --build -d || fail "docker compose up failed"

# --- Step 3: Wait for health check ---
log "Waiting for health check at $HEALTH_ENDPOINT ..."
ELAPSED=0
while [ "$ELAPSED" -lt "$HEALTH_TIMEOUT" ]; do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_ENDPOINT" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        log "Health check passed (HTTP $HTTP_CODE)"
        break
    fi
    sleep "$HEALTH_INTERVAL"
    ELAPSED=$((ELAPSED + HEALTH_INTERVAL))
    warn "Waiting... ($ELAPSED/${HEALTH_TIMEOUT}s, last HTTP: $HTTP_CODE)"
done

if [ "$ELAPSED" -ge "$HEALTH_TIMEOUT" ]; then
    fail "Health check timed out after ${HEALTH_TIMEOUT}s"
fi

# --- Step 4: Basic E2E smoke test ---
log "Running E2E smoke tests..."
PASS=0
TOTAL=0

run_test() {
    local name="$1"
    local url="$2"
    local expect_code="$3"
    TOTAL=$((TOTAL + 1))
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo "000")
    if [ "$CODE" = "$expect_code" ]; then
        echo -e "  ${GREEN}PASS${NC} $name (HTTP $CODE)"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC} $name (expected $expect_code, got $CODE)"
    fi
}

run_test "GET /health"           "$BASE_URL/health"    "200"
run_test "GET /console (auth)"   "$BASE_URL/console"   "401"
run_test "GET /admin (auth)"     "$BASE_URL/admin"     "401"
run_test "GET /webhook (Strava)" "$BASE_URL/webhook"   "200"

# Scheduler check
HEALTH_BODY=$(curl -s "$HEALTH_ENDPOINT" 2>/dev/null)
TOTAL=$((TOTAL + 1))
if echo "$HEALTH_BODY" | grep -q '"scheduler":"running"'; then
    echo -e "  ${GREEN}PASS${NC} Scheduler is running"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}FAIL${NC} Scheduler not running"
fi

# Docker logs error check (last 50 lines)
TOTAL=$((TOTAL + 1))
ERROR_COUNT=$(docker logs airunningcoach --tail 50 2>&1 | grep -ci 'error\|traceback\|exception' || echo "0")
if [ "${ERROR_COUNT:-0}" -eq 0 ]; then
    echo -e "  ${GREEN}PASS${NC} No errors in recent logs"
    PASS=$((PASS + 1))
else
    echo -e "  ${YELLOW}WARN${NC} Found $ERROR_COUNT error(s) in recent logs — check: docker logs airunningcoach --tail 50"
fi

# --- Summary ---
echo ""
if [ "$PASS" -eq "$TOTAL" ]; then
    log "All tests passed ($PASS/$TOTAL)"
else
    warn "Some tests failed ($PASS/$TOTAL)"
    exit 1
fi

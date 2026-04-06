#!/usr/bin/env bash
# deploy-t440.sh — Push code from RPi5 and deploy on T440 via SSH.
#
# Usage:
#   ./scripts/deploy-t440.sh          # push current branch + deploy
#   ./scripts/deploy-t440.sh --skip-push  # deploy only (code already pushed)
#
# Prerequisites:
#   - SSH key auth: ssh -p 8922 tinhn@192.168.1.89 (no password)
#   - T440 user in docker group: sudo usermod -aG docker tinhn

set -euo pipefail

# --- Config ---
T440_HOST="tinhn@192.168.1.89"
T440_PORT="8922"
T440_REPO="~/repo/Personal_AI_OS"
T440_URL="http://192.168.1.89:8000"
HEALTH_ENDPOINT="${T440_URL}/health"
HEALTH_TIMEOUT=90      # seconds to wait for healthy status
HEALTH_INTERVAL=5      # seconds between health checks

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEPLOY]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

ssh_t440() {
    ssh -p "$T440_PORT" -o ConnectTimeout=10 "$T440_HOST" "$@"
}

# --- Step 0: Parse args ---
SKIP_PUSH=false
for arg in "$@"; do
    case "$arg" in
        --skip-push) SKIP_PUSH=true ;;
    esac
done

# --- Step 1: Push code from RPi5 ---
if [ "$SKIP_PUSH" = false ]; then
    log "Pushing code to origin..."
    BRANCH=$(git -C "$(dirname "$0")/.." rev-parse --abbrev-ref HEAD)
    git -C "$(dirname "$0")/.." push origin "$BRANCH"
    log "Pushed branch: $BRANCH"
else
    log "Skipping push (--skip-push)"
fi

# --- Step 2: SSH into T440 — pull + rebuild ---
log "Connecting to T440..."
ssh_t440 "echo 'SSH OK'" || fail "Cannot SSH into T440"

log "Pulling latest code on T440..."
ssh_t440 "cd $T440_REPO && git pull --ff-only" || fail "git pull failed on T440"

log "Rebuilding and restarting containers on T440..."
ssh_t440 "cd $T440_REPO && docker compose up --build -d" || fail "docker compose up failed"

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

run_test "GET /health"           "$T440_URL/health"    "200"
run_test "GET /console (auth)"   "$T440_URL/console"   "401"
run_test "GET /admin (auth)"     "$T440_URL/admin"     "401"

# Test news agent config is loaded
HEALTH_BODY=$(curl -s "$HEALTH_ENDPOINT" 2>/dev/null)
if echo "$HEALTH_BODY" | grep -q '"scheduler":"running"'; then
    echo -e "  ${GREEN}PASS${NC} Scheduler is running"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}FAIL${NC} Scheduler not running"
fi
TOTAL=$((TOTAL + 1))

# --- Summary ---
echo ""
if [ "$PASS" -eq "$TOTAL" ]; then
    log "All tests passed ($PASS/$TOTAL)"
else
    warn "Some tests failed ($PASS/$TOTAL)"
    exit 1
fi

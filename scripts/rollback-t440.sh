#!/usr/bin/env bash
# rollback-t440.sh — Roll back to the previous Docker image on T440.
#
# Usage:
#   bash scripts/rollback-t440.sh                  # auto-detect last backup image
#   bash scripts/rollback-t440.sh airunningcoach:backup  # explicit image tag
#
# How it works:
#   deploy-t440.sh tags the running image as airunningcoach:backup BEFORE rebuilding.
#   This script restores that backup by:
#     1. Stopping the broken container
#     2. Starting a new container from the backup image
#     3. Health-checking the restored container
#
# Rollback window: only the LAST successful deploy is kept as backup.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="http://localhost:8000"
HEALTH_ENDPOINT="${BASE_URL}/health"
HEALTH_TIMEOUT=60
HEALTH_INTERVAL=5
BACKUP_TAG="${1:-airunningcoach:backup}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[ROLLBACK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

cd "$REPO_DIR"

# --- Check backup image exists ---
log "Looking for backup image: $BACKUP_TAG"
if ! docker image inspect "$BACKUP_TAG" &>/dev/null; then
    fail "Backup image '$BACKUP_TAG' not found. Cannot rollback."
fi

BACKUP_ID=$(docker image inspect "$BACKUP_TAG" --format '{{.Id}}' | cut -c8-19)
log "Found backup image: $BACKUP_ID"

# --- Stop current container ---
log "Stopping current container..."
docker compose down --remove-orphans 2>/dev/null || true

# --- Tag backup as the primary image ---
log "Restoring backup image as ai-coach..."
# Extract the built image name from compose config
SERVICE_IMAGE=$(docker compose config --format json 2>/dev/null | python3 -c "import sys,json; cfg=json.load(sys.stdin); print(cfg['services']['ai-coach'].get('image','airunningcoach'))" 2>/dev/null || echo "airunningcoach")
docker tag "$BACKUP_TAG" "${SERVICE_IMAGE}:latest" 2>/dev/null || true

# Bring up with the restored image (no rebuild)
log "Restarting containers from backup image..."
docker compose up -d --no-build || fail "docker compose up failed after rollback"

# --- Health check ---
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
    fail "Health check timed out after ${HEALTH_TIMEOUT}s — rollback may have failed"
fi

# --- Audit log ---
DEPLOY_LOG="$REPO_DIR/deployments.log"
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "$(date '+%Y-%m-%d %H:%M:%S') | $COMMIT | ${USER:-unknown} | ROLLBACK to $BACKUP_TAG | SUCCESS" >> "$DEPLOY_LOG"

log "Rollback complete — system restored to: $BACKUP_TAG"
log "Audit entry appended to: deployments.log"
warn "Remember to investigate why the previous deploy failed before re-deploying."

#!/usr/bin/env bash
# pre-deploy-check.sh — Run before deploy to catch issues early.
#
# Usage:
#   ./scripts/pre-deploy-check.sh          # full check (tests + lint)
#   ./scripts/pre-deploy-check.sh --quick  # tests only (skip lint)
#
# Exit codes: 0 = all pass, 1 = failures found

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
TOTAL=0

log()  { echo -e "${GREEN}[CHECK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }

check() {
    local name="$1"
    shift
    TOTAL=$((TOTAL + 1))
    if "$@" > /dev/null 2>&1; then
        echo -e "  ${GREEN}PASS${NC} $name"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC} $name"
    fi
}

QUICK=false
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
    esac
done

cd "$REPO_ROOT"

echo ""
log "Pre-deploy checks for Personal AI OS"
echo "  ================================================"

# --- 1. Unit tests ---
log "Running test suite..."
TOTAL=$((TOTAL + 1))
if python -m pytest tests/ -q --tb=short 2>&1 | tail -3; then
    PASS=$((PASS + 1))
    echo -e "  ${GREEN}PASS${NC} Test suite"
else
    echo -e "  ${RED}FAIL${NC} Test suite"
fi

# --- 2. Config validation ---
check "Config loads" python -c "from app.core.config import load_config; c = load_config(); assert c is not None"

# --- 3. Docker compose syntax ---
TOTAL=$((TOTAL + 1))
if command -v docker &>/dev/null && docker info &>/dev/null; then
    if docker compose config --quiet 2>/dev/null; then
        echo -e "  ${GREEN}PASS${NC} Docker compose config valid"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC} Docker compose config invalid"
    fi
else
    echo -e "  ${YELLOW}SKIP${NC} Docker compose check (docker not available)"
    PASS=$((PASS + 1))  # Don't fail for missing docker on dev machines
fi

# --- 4. Uncommitted changes warning (non-blocking) ---
if ! git diff --quiet HEAD -- app/ tests/ 2>/dev/null; then
    echo -e "  ${YELLOW}WARN${NC} Uncommitted changes in app/ or tests/ — commit before deploy"
fi

echo ""
echo "  ================================================"
if [ "$PASS" -eq "$TOTAL" ]; then
    log "All checks passed ($PASS/$TOTAL) — safe to deploy"
    exit 0
else
    warn "Some checks failed ($PASS/$TOTAL) — fix before deploying"
    exit 1
fi

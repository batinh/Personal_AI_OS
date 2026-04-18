#!/usr/bin/env bash
# install-hooks.sh — Install git pre-commit hooks for this project.
#
# Run once after cloning:
#   bash scripts/install-hooks.sh
#
# What the pre-commit hook enforces:
#   1. Smoke tests pass (catches ImportError in < 2s)
#   2. ruff lint passes
#   3. black formatting check

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[HOOKS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

if [ ! -d "$HOOKS_DIR" ]; then
    warn "No .git/hooks directory found — are you in the repo root?"
    exit 1
fi

# ---------- pre-commit hook ----------
cat > "$HOOKS_DIR/pre-commit" << 'HOOK'
#!/usr/bin/env bash
# Auto-installed by scripts/install-hooks.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

fail() { echo -e "${RED}[PRE-COMMIT FAIL]${NC} $*"; exit 1; }
ok()   { echo -e "${GREEN}[PRE-COMMIT PASS]${NC} $*"; }

# 1. Smoke tests — catches ImportError / missing symbols in < 2s
echo "[pre-commit] Running smoke tests..."
python -m pytest tests/test_smoke.py -q --tb=short 2>&1 || fail "Smoke tests failed. Fix ImportError or missing symbols before committing."
ok "Smoke tests"

# 2. ruff — lint check
if command -v ruff &>/dev/null; then
    echo "[pre-commit] Running ruff lint..."
    ruff check app/ tests/ --quiet 2>&1 || fail "ruff found lint errors. Run: ruff check app/ tests/ --fix"
    ok "ruff lint"
else
    echo "[pre-commit] SKIP ruff (not installed — run: pip install ruff)"
fi

# 3. black — format check
if command -v black &>/dev/null; then
    echo "[pre-commit] Running black format check..."
    black app/ tests/ --check --quiet 2>&1 || fail "black found formatting issues. Run: black app/ tests/"
    ok "black format"
else
    echo "[pre-commit] SKIP black (not installed — run: pip install black)"
fi

echo "[pre-commit] All checks passed ✓"
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
log "Installed: .git/hooks/pre-commit"

# ---------- post-commit hook (issue auto-link) ----------
cat > "$HOOKS_DIR/post-commit" << 'HOOK'
#!/usr/bin/env bash
# Auto-installed by scripts/install-hooks.sh
# Detects "Closes ISS-NNN" or "Fixes ISS-NNN" in commit message and
# prints a reminder to move the issue row in docs/ISSUES.md.
set -euo pipefail

COMMIT_MSG=$(git log -1 --pretty=%B)
ISSUE_REF=$(echo "$COMMIT_MSG" | grep -oiE "(Closes|Fixes|Refs) ISS-[0-9]+" | head -1 || true)

if [ -n "$ISSUE_REF" ]; then
    ISSUE_ID=$(echo "$ISSUE_REF" | grep -oE "ISS-[0-9]+")
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  📋 ISSUE REMINDER                                  │"
    echo "  │  Commit references: $ISSUE_ID                        "
    echo "  │  → Move row from Open → Closed in docs/ISSUES.md   │"
    echo "  │  → Add commit hash: $(git rev-parse --short HEAD)   │"
    echo "  └─────────────────────────────────────────────────────┘"
    echo ""
fi
HOOK

chmod +x "$HOOKS_DIR/post-commit"
log "Installed: .git/hooks/post-commit"

echo ""
log "Done. Hooks installed in $HOOKS_DIR"
log "Tools needed: ruff, black — install with: pip install -r requirements-dev.txt"

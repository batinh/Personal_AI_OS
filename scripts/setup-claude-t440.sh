#!/usr/bin/env bash
# setup-claude-t440.sh — Set up Claude Code global config on T440.
#
# Run this ONCE after pulling the repo on T440 to mirror the Claude Code
# environment from the previous machine.
#
# What it does:
#   1. Copies global ~/.claude/settings.json from RPi5 (hooks, env vars)
#   2. Copies ~/.claude/rules/ from RPi5 (coding rules)
#   3. Places project memory files into ~/.claude/projects/.../memory/
#
# Usage (run from T440):
#   bash scripts/setup-claude-t440.sh
#
# Prerequisites:
#   - SSH access to RPi5: ssh -p 22 tinhn@<RPI5_IP>

set -euo pipefail

RPI5_HOST="${RPI5_HOST:-tinhn@192.168.1.X}"   # set RPI5_HOST env var or edit this line
CLAUDE_DIR="$HOME/.claude"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MEMORY_SRC="$REPO_DIR/.claude/memory"
MEMORY_DEST="$CLAUDE_DIR/projects/-home-tinhn-repo-Personal-AI-OS/memory"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
log()  { echo -e "${GREEN}[SETUP]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

# --- Step 1: Copy global ~/.claude config from RPi5 ---
if [ "$RPI5_HOST" = "tinhn@192.168.1.X" ]; then
    warn "RPI5_HOST not set. Skipping global config sync."
    warn "Set it with: RPI5_HOST=tinhn@<ip> bash scripts/setup-claude-t440.sh"
else
    log "Syncing ~/.claude/settings.json from RPi5..."
    rsync -av "$RPI5_HOST:~/.claude/settings.json" "$CLAUDE_DIR/settings.json"

    log "Syncing ~/.claude/rules/ from RPi5..."
    rsync -av --delete "$RPI5_HOST:~/.claude/rules/" "$CLAUDE_DIR/rules/"

    log "Syncing project .claude/settings.local.json from RPi5..."
    rsync -av "$RPI5_HOST:~/repo/Personal_AI_OS/.claude/settings.local.json" "$REPO_DIR/.claude/settings.local.json"

    log "Global config synced."
fi

# --- Step 2: Install project memory files ---
log "Installing project memory files..."
mkdir -p "$MEMORY_DEST"
cp "$MEMORY_SRC/MEMORY.md" "$MEMORY_DEST/MEMORY.md"
cp "$MEMORY_SRC/reference_deploy.md" "$MEMORY_DEST/reference_deploy.md"
cp "$MEMORY_SRC/reference_audit_log.md" "$MEMORY_DEST/reference_audit_log.md"
log "Memory files installed to $MEMORY_DEST"

echo ""
log "Setup complete. Install Claude Code if not already done:"
log "  curl -fsSL https://claude.ai/install.sh | sh"
log "Then open the project: claude /home/tinhn/repo/Personal_AI_OS"

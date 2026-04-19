#!/usr/bin/env bash
# fetch-logs.sh — Fetch and filter Docker logs from airunningcoach for debugging.
#
# Usage:
#   ./scripts/fetch-logs.sh                      # last 100 lines, all levels
#   ./scripts/fetch-logs.sh -n 200               # last N lines
#   ./scripts/fetch-logs.sh -l ERROR             # errors only
#   ./scripts/fetch-logs.sh -l ERROR,WARNING     # multiple levels (comma-separated)
#   ./scripts/fetch-logs.sh -m news              # filter by module/domain keyword
#   ./scripts/fetch-logs.sh -l ERROR -m coach    # combine: coach errors only
#   ./scripts/fetch-logs.sh --since 30m          # last 30 minutes
#   ./scripts/fetch-logs.sh --since 1h           # last 1 hour
#   ./scripts/fetch-logs.sh --live               # live tail (Ctrl+C to stop)
#   ./scripts/fetch-logs.sh --summary            # count by level + list last errors
#   ./scripts/fetch-logs.sh --file               # read from ./logs/app.log* (container down fallback)
#
# Module keywords map to [LOG-TAG] prefixes in app logs:
#   news, coach, strava, scheduler, webhook, database,
#   memory, notification, weather, backup, audit, admin
#
# Examples for Claude Code debugging sessions:
#   bash scripts/fetch-logs.sh -l ERROR --since 1h
#   bash scripts/fetch-logs.sh -m news -n 200
#   bash scripts/fetch-logs.sh --summary
#   bash scripts/fetch-logs.sh --file -l ERROR   # read from bind-mount when container is down

set -uo pipefail

CONTAINER="airunningcoach"

# Bind-mounted log directory (./logs/ on host → /app/logs in container)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(dirname "$SCRIPT_DIR")/logs"
LOG_FILE="$LOG_DIR/app.log"

# --- Colors ---
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GREEN='\033[0;32m'
BOLD='\033[1m'
NC='\033[0m'

# --- Defaults ---
TAIL_LINES=100
LEVEL_FILTER=""
MODULE_FILTER=""
SINCE_ARG=""
LIVE=false
SUMMARY=false
FILE_MODE=false

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n)          TAIL_LINES="$2"; shift 2 ;;
        -l|--level)  LEVEL_FILTER="$2"; shift 2 ;;
        -m|--module) MODULE_FILTER="$2"; shift 2 ;;
        --since)     SINCE_ARG="$2"; shift 2 ;;
        --live)      LIVE=true; shift ;;
        --summary)   SUMMARY=true; shift ;;
        --file)      FILE_MODE=true; shift ;;
        -h|--help)
            sed -n '2,32p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# --- Build grep pattern ---
build_grep_pattern() {
    local level_pat="" module_pat=""

    if [[ -n "$LEVEL_FILTER" ]]; then
        level_pat=$(echo "$LEVEL_FILTER" | tr ',' '|' | tr '[:lower:]' '[:upper:]')
    fi

    if [[ -n "$MODULE_FILTER" ]]; then
        module_pat=$(echo "$MODULE_FILTER" | tr '[:lower:]' '[:upper:]')
    fi

    if [[ -n "$level_pat" && -n "$module_pat" ]]; then
        echo "($level_pat).*\[$module_pat|\[$module_pat.*($level_pat)"
    elif [[ -n "$level_pat" ]]; then
        echo "$level_pat"
    elif [[ -n "$module_pat" ]]; then
        echo "\[$module_pat"
    else
        echo ""
    fi
}

# --- Colorize output ---
colorize() {
    sed \
        -e "s/\[ERROR\]/$(printf "${RED}")[ERROR]$(printf "${NC}")/g" \
        -e "s/\[CRITICAL\]/$(printf "${RED}${BOLD}")[CRITICAL]$(printf "${NC}")/g" \
        -e "s/\[WARNING\]/$(printf "${YELLOW}")[WARNING]$(printf "${NC}")/g" \
        -e "s/\[INFO\]/$(printf "${GREEN}")[INFO]$(printf "${NC}")/g" \
        -e "s/\[DEBUG\]/$(printf "${CYAN}")[DEBUG]$(printf "${NC}")/g"
}

# --- print_summary <raw_content> ---
print_summary() {
    local raw="$1"
    echo -e "${BOLD}--- Count by level ---${NC}"
    for lvl in CRITICAL ERROR WARNING INFO DEBUG; do
        local count
        count=$(echo "$raw" | grep -c "\[$lvl\]" 2>/dev/null || true)
        local color
        case "$lvl" in
            CRITICAL|ERROR) color="$RED" ;;
            WARNING)        color="$YELLOW" ;;
            INFO)           color="$GREEN" ;;
            *)              color="$CYAN" ;;
        esac
        printf "  ${color}%-12s${NC} %d\n" "$lvl" "$count"
    done
    echo ""
    echo -e "${BOLD}--- Last 10 ERRORs / WARNINGs ---${NC}"
    echo "$raw" | grep -E "\[ERROR\]|\[CRITICAL\]|\[WARNING\]" | tail -10 | colorize
    echo ""
    echo -e "${BOLD}--- Last 5 lines ---${NC}"
    echo "$raw" | tail -5 | colorize
}

# ---------------------------------------------------------------------------
# FILE MODE — read from bind-mounted ./logs/app.log* (works when container is down)
# ---------------------------------------------------------------------------
if [[ "$FILE_MODE" == true ]]; then
    if [[ ! -f "$LOG_FILE" ]]; then
        echo -e "${RED}[ERROR]${NC} Log file not found: $LOG_FILE" >&2
        echo "Ensure ./logs/ is bind-mounted and the container has written at least one log line." >&2
        exit 1
    fi

    # All rotated files sorted chronologically (YYYY-MM-DD suffix is lexicographic = chronological)
    mapfile -t LOG_FILES < <(ls -1 "$LOG_DIR"/app.log* 2>/dev/null | sort)
    PAT=$(build_grep_pattern)

    echo -e "${BOLD}=== [FILE] ${LOG_DIR}/app.log* | tail=${TAIL_LINES}${MODULE_FILTER:+ | module=$MODULE_FILTER}${LEVEL_FILTER:+ | level=$LEVEL_FILTER} ===${NC}"
    echo ""

    RAW=$(cat "${LOG_FILES[@]}" 2>/dev/null | tail -n "$TAIL_LINES")

    if [[ "$SUMMARY" == true ]]; then
        print_summary "$RAW"
    elif [[ -n "$PAT" ]]; then
        echo "$RAW" | grep -iE "$PAT" | colorize
    else
        echo "$RAW" | colorize
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# DOCKER MODE — read from running container (default)
# ---------------------------------------------------------------------------

# If container is missing, auto-fallback to --file mode
if ! docker inspect "$CONTAINER" &>/dev/null; then
    echo -e "${YELLOW}[WARN]${NC} Container '$CONTAINER' not found — falling back to --file mode." >&2
    exec "$0" --file -n "$TAIL_LINES" \
        ${LEVEL_FILTER:+-l "$LEVEL_FILTER"} \
        ${MODULE_FILTER:+-m "$MODULE_FILTER"} \
        ${SUMMARY:+--summary}
fi

STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null)
if [[ "$STATUS" != "running" ]]; then
    echo -e "${YELLOW}[WARN]${NC} Container '$CONTAINER' is $STATUS (not running)." >&2
    echo "Showing last logs anyway..." >&2
fi

# --- Build docker logs command ---
DOCKER_ARGS=()
if [[ -n "$SINCE_ARG" ]]; then
    DOCKER_ARGS+=("--since" "$SINCE_ARG")
else
    DOCKER_ARGS+=("--tail" "$TAIL_LINES")
fi

# --- SUMMARY mode ---
if [[ "$SUMMARY" == true ]]; then
    echo -e "${BOLD}=== Log Summary: $CONTAINER (last $TAIL_LINES lines) ===${NC}"
    echo ""
    RAW=$(docker logs "${DOCKER_ARGS[@]}" "$CONTAINER" 2>&1)
    print_summary "$RAW"
    exit 0
fi

# --- LIVE tail mode ---
if [[ "$LIVE" == true ]]; then
    PAT=$(build_grep_pattern)
    echo -e "${BOLD}[LIVE]${NC} Tailing '$CONTAINER'${MODULE_FILTER:+ | module=$MODULE_FILTER}${LEVEL_FILTER:+ | level=$LEVEL_FILTER} — Ctrl+C to stop"
    echo ""
    if [[ -n "$PAT" ]]; then
        docker logs -f "$CONTAINER" 2>&1 | grep -iE "$PAT" | colorize
    else
        docker logs -f "$CONTAINER" 2>&1 | colorize
    fi
    exit 0
fi

# --- Normal fetch + filter ---
PAT=$(build_grep_pattern)
HEADER="${CONTAINER}"
[[ -n "$SINCE_ARG" ]]     && HEADER+=" | since=${SINCE_ARG}"  || HEADER+=" | tail=${TAIL_LINES}"
[[ -n "$MODULE_FILTER" ]] && HEADER+=" | module=${MODULE_FILTER}"
[[ -n "$LEVEL_FILTER" ]]  && HEADER+=" | level=${LEVEL_FILTER}"

echo -e "${BOLD}=== $HEADER ===${NC}"
echo ""

if [[ -n "$PAT" ]]; then
    docker logs "${DOCKER_ARGS[@]}" "$CONTAINER" 2>&1 | grep -iE "$PAT" | colorize
else
    docker logs "${DOCKER_ARGS[@]}" "$CONTAINER" 2>&1 | colorize
fi

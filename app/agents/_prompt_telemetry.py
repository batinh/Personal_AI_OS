"""
Prompt telemetry — visibility into prompt size, version, and structure.

Why this module exists:
- Prompts grow over time; without metrics we discover token-budget issues
  via cost dashboards, not at the source.
- A regression in AI quality is often correlated with a prompt change, but
  log lines don't show which version of the prompt was used.

What this module provides:
- `log_prompt_metrics()` — one-line call before every `generate_content`
  invocation. Logs size, intent, and a hash of the prompt content.
- `estimate_tokens()` — heuristic token count (~4 chars/token, English avg).
  Vietnamese skews slightly higher (~3.5 chars/token); the estimate stays
  within ±15% of the real count which is enough for budget alerts.
- `PROMPT_VERSION` — module-level constant; bump manually when prompts
  change in a way worth correlating with output behavior.

The telemetry is best-effort: any exception inside logging is swallowed so
runtime paths never fail because of observability code.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bump this string when prompts change in a way you want to correlate with
# AI output regressions. Format: YYYY.MM.DD-N where N is the daily counter.
# Changelog (most recent first):
#   2026.06.04-2 — Phase 3 (CHAT_FORMAT_RULES → system inst) + Phase 4 (CoT
#                  hardened: reasoning kept internal, must not leak into reply).
#   2026.06.04-1 — Phase 1 baseline: telemetry instrumentation only, no prompt
#                  content changes from prior production.
PROMPT_VERSION = "2026.06.04-2"

# Tokens-per-character heuristic. Vietnamese sits around 3.5, English around 4.
# We use 3.8 as a safe middle ground — overestimates a touch, biased toward
# triggering budget alerts a hair early rather than missing them.
_CHARS_PER_TOKEN = 3.8

# Alert threshold; logged at WARNING above this. Sized for current standard-path
# (~5K) plus headroom for ~3K of conversation history.
WARN_TOTAL_TOKENS = 8000


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. Use for alerts, not billing."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN)


def _short_hash(text: str) -> str:
    if not text:
        return "0" * 8
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]


def log_prompt_metrics(
    flow: str,
    system_inst: str,
    user_prompt: str,
    *,
    intent: str = "n/a",
    model: str = "n/a",
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a single line with prompt size + identifying hashes.

    Failure-mode contract: never raises. Logging code must not break a flow.

    Args:
        flow: short identifier of the calling flow (e.g. "coach.chat",
            "coach.standup", "news.topic.morning").
        system_inst: the rendered system instruction text.
        user_prompt: the rendered user-turn prompt text.
        intent: optional intent classification ("fast", "standard", ...).
        model: optional model identifier.
        extra: optional dict of extra fields; serialized as k=v pairs.
    """
    try:
        sys_tokens = estimate_tokens(system_inst)
        user_tokens = estimate_tokens(user_prompt)
        total = sys_tokens + user_tokens

        extras_str = ""
        if extra:
            extras_str = " " + " ".join(f"{k}={v}" for k, v in extra.items())

        line = (
            f"[PROMPT-METRIC] flow={flow} intent={intent} model={model} "
            f"v={PROMPT_VERSION} sys={sys_tokens}t user={user_tokens}t "
            f"total={total}t sys_hash={_short_hash(system_inst)} "
            f"user_hash={_short_hash(user_prompt)}{extras_str}"
        )

        if total >= WARN_TOTAL_TOKENS:
            logger.warning("%s OVER_BUDGET (>%d)", line, WARN_TOTAL_TOKENS)
        else:
            logger.info(line)
    except Exception as e:  # noqa: BLE001 — telemetry must not break flows
        logger.debug("[PROMPT-METRIC] telemetry failed: %s", e)

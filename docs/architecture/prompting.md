# Prompting Architecture

> Source files: `app/agents/coach/prompts.py`, `app/agents/news/prompts.py`,
> `app/agents/_prompt_telemetry.py`, `tests/test_prompts_snapshot.py`.

## Why this document exists

Prompts are code paths that don't get caught by type checks or unit tests in
the usual way. A one-character change in a prompt can degrade AI output for
weeks before anyone notices. This document is the operating manual: where
prompts live, how to change them safely, and how to verify a change didn't
regress production behavior.

## Coach prompts — 8-layer architecture

`app/agents/coach/prompts.py` is organized top-to-bottom by abstraction layer.
Each layer has one job; cross-layer references happen only downward
(L5 task builders call L1 system instructions, never the other way).

| Layer | Symbol | Responsibility |
|------:|--------|----------------|
| Constants | `_COACH_IDENTITY`, `_COACH_SIGNATURE` | Persona + footer string, single source of truth. |
| L1 | `build_system_instruction()` | Full ~3.5K-token brain (HR zones, GCS rubric, taper, tool discipline, internal CoT). |
| L1 | `build_core_system_instruction()` | ~300-token lightweight brain for the fast chat path. |
| L2 | `get_shared_context_block()` | Runtime data (time, ACWR, phase, weekly target) injected per call. |
| L3 | `DEFAULT_ANALYSIS_*`, `DEFAULT_REFLECTION_*` | Domain report structures (run analysis, weekly reflection). |
| L4 | `CHAT_FORMAT_RULES`, `STRAVA_FORMAT_RULES`, `EMAIL_FORMAT_RULES`, `UNIVERSAL_FORMAT_RULES` | Platform-specific output rules. |
| L5 | `build_chat_prompt`, `build_standup_prompt`, `build_universal_run_analysis_prompt`, `build_weekly_reflection_prompt` | Final user-turn prompt assembly. |
| L6 | `build_weekly_reflection_prompt` | Sunday-night dual-horizon reflection. |
| L7 | `WEATHER_INSTRUCTION` | Weather-aware safety overlay. |
| L8 | `build_memory_extraction_prompt` | State-aware autonomous memory manager with 5 few-shot examples. |

### Chat format consolidation (Phase 3)

`CHAT_FORMAT_RULES` is embedded inside `build_system_instruction()` /
`build_core_system_instruction()` when callers pass `chat_format=True`. The
user-turn builders (`build_chat_prompt`, `build_standup_prompt`,
`build_weekly_reflection_prompt`) no longer append it — keeping rule definition
in one place. Strava/email/universal paths leave `chat_format=False` and
inject their own format rules into the user turn via
`build_universal_run_analysis_prompt(..., format_rules=...)`.

### Chain-of-thought hygiene (Phase 4)

The safety reasoning block (`[KỶ LUẬT LẬP LUẬN AN TOÀN]`) instructs the model
to perform the ACWR → Phase → Action reasoning **internally** and to suppress
the visible "ACWR hiện tại là X" preface that previously leaked into Telegram
replies. If you see that preface reappear in production logs, the system
instruction has regressed — bump `PROMPT_VERSION` after fixing.

## News prompts

`app/agents/news/prompts.py` exposes 5 builders:

| Builder | Purpose |
|---------|---------|
| `build_topic_system_instruction()` / `build_topic_prompt()` | Per-topic parallel briefings (primary path). |
| `build_on_demand_system_instruction()` / `build_on_demand_prompt()` | Ad-hoc user search queries. |
| `build_session_prompt()` / `build_news_system_instruction()` | Legacy single-call briefing, kept as fallback when topic list is empty. |
| `build_memory_extraction_prompt()` | Extract preference signals from chat. 5 few-shot examples added in Phase 5. |

Session templates are generated at module load from `_SESSION_BASE_TEMPLATE` +
`_SESSION_OVERRIDES` so morning/afternoon/evening share one skeleton.

## Telemetry contract

Every Gemini call passes through `log_prompt_metrics()` from
`app/agents/_prompt_telemetry.py`. The log line shape is grep-stable:

```
[PROMPT-METRIC] flow=coach.chat intent=standard model=models/gemini-2.0-flash
  v=2026.06.04-2 sys=2847t user=412t total=3259t sys_hash=a1b2c3d4 user_hash=e5f6...
```

When `total >= 8000` tokens, the line is emitted at WARNING level with
`OVER_BUDGET` suffix.

`PROMPT_VERSION` is bumped manually whenever prompt content changes in a way
worth correlating with output behavior. Format: `YYYY.MM.DD-N`. The changelog
lives inline in `_prompt_telemetry.py`.

## Snapshot-test safety net

`tests/test_prompts_snapshot.py` freezes byte-exact output of every public
prompt builder against JSON fixtures (`tests/fixtures/prompts/`). Snapshot
files (`tests/snapshots/prompts/*.txt`) are git-tracked.

### Workflow

1. Edit `prompts.py`.
2. Run `python -m pytest tests/test_prompts_snapshot.py -v`.
3. If a snapshot mismatch is **intentional**, eyeball the diff hint and run
   `python scripts/update_prompt_snapshots.py` to re-freeze.
4. Commit the regenerated snapshot files in the same PR as the prompt change.
5. Bump `PROMPT_VERSION` if the change is significant.

### Eval helper for memory extraction

`scripts/eval_news_memory_extraction.py` renders the news memory-extraction
prompt against a list of chat samples and prints the parsed JSON. Run with
`--dry-run` to see the prompt only, or set `GEMINI_API_KEY` to call the model
and validate `{liked, disliked, notes}` keys. Does not write to any DB.

### When to add a new snapshot

Whenever you add a new public prompt builder, also add:
- A fixture JSON under `tests/fixtures/prompts/`.
- A snapshot test class method in `tests/test_prompts_snapshot.py`.
- Run `update_prompt_snapshots.py` once to seed the file.

## Quick reference — file edits

| Change | Edit | Verify |
|--------|------|--------|
| Persona / signature | `_COACH_IDENTITY` / `_COACH_SIGNATURE` constants | Snapshot byte-exact (if pure rename) or update. |
| HR/Power zone wording | `build_system_instruction` body | Update snapshots; manual chat smoke test. |
| Telegram format rules | `CHAT_FORMAT_RULES` constant | Update snapshots; run `test_telegram_chunking.py`. |
| Strava-only output | `STRAVA_FORMAT_RULES` | Update snapshots; manual Strava webhook smoke. |
| Memory extraction logic | `MEMORY_EXTRACTION_PROMPT` | Update snapshots; eval on historical chat samples. |
| News topic shape | `_TOPIC_SYSTEM_INSTRUCTION` / `build_topic_prompt` | Update snapshots; manual /news smoke. |

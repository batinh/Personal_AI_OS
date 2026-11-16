# Runalyze MCP Ingestion

Issue: ISS-023 · Status: load sync + coach context done; activity ingestion replaces Strava (poll + backfill).

## Why
Garmin/Strava give raw activity and wellness data. Runalyze computes CTL/ATL/TSB, ACWR,
marathon shape, effective VO2max and HRV baseline/normal range, plus race prognosis. Runalyze is
the proposed source of truth for load metrics; Garmin keeps readiness, Strava keeps streams.

## Auth (resolved)
`https://runalyze.com/mcp` accepts a **Personal API Token** (Premium required), created at
`https://runalyze.com/settings/personal-api`. Set `RUNALYZE_API_TOKEN` in `.env` (never in repo/config.json).

Findings from live probing (2026-10-04 → 2026-10-24):
- The token must be sent in the **`token` header**. `Authorization: Bearer <token>` returns
  `401 Invalid authentication token` for personal tokens (Bearer is OAuth-only), despite the help guide.
- For ~3 weeks `initialize` with the `token` header failed with `A valid session id is REQUIRED`
  (server-side bug on Runalyze); it works since 2026-10-24 and returns an `Mcp-Session-Id`.
- OAuth is not needed: the server offers only `authorization_code`, and the pre-registered `34_claude`
  app has no localhost redirect URI.
- The REST API (`/api/v1`, same `token` header) has activities, HRV, sleep, mental and equipment but
  **no** CTL/ATL/TSB, ACWR, races or prognosis, so it cannot replace the MCP.
- Locally recomputing load from per-activity TRIMP is only approximate (CTL −0.6%, ATL −9%, ACWR zone
  flips), so the MCP values are the source of truth.

## Components
| Piece | File |
|-------|------|
| MCP JSON-RPC client (personal token) | `app/agents/coach/runalyze_client.py` |
| Table + upsert/get | `app/core/database.py` (`runalyze_daily_load`, `_RUNALYZE_METRIC_COLUMNS`) |
| Daily job 05:55 (after Garmin 05:45) | `app/services/scheduler.py` (`task_runalyze_sync`) |
| Tests | `tests/test_runalyze_client.py` |

## Data
MCP tools used: `get_calculations`, `get_health_metrics`, `get_planned_races`, `get_prognosis`.
Flattened metrics are columns; the full payloads (incl. races, prognosis) live in `raw_json`.
One row per `(user_id, date)`, auto-migrating like `garmin_daily_metrics`.

## Failure behaviour
Missing/expired credentials → `RunalyzeAuthError`, logged as `[RUNALYZE] Auth failed — rerun auth script`,
sync returns False. Network/MCP errors are logged and swallowed at the job boundary only.

## Coach integration (phase 4)
| Surface | Change |
|---------|--------|
| `get_shared_context_block(runalyze_text=…)` | One labelled line: CTL/ATL/TSB (+band), HRV vs normal range, marathon shape, effective VO2max. Omitted when empty, so prompts without data are byte-identical. |
| `build_agent_context()` | Fills `AgentContext.runalyze_text`; also passed by morning briefing, chat (non-fast intent) and weekly reflection. |
| Weekly plan prompt | Adds Runalyze prognosis (5K/10K/HM/FM) and planned goal races when present. |
| `runalyze_context.py` | Pure formatters + `get_runalyze_text()`; data older than 3 days is dropped, 1-3 days old is flagged `(cũ N ngày)`. |

**ACWR conflict decision:** Runalyze ACWR (ATL/CTL, TRIMP-EMA) is deliberately not shown to the model.
Safety rules keep using the app's own ACWR (7d/28d), so two competing ACWR values never reach a prompt.
Run-analysis/Strava prompts are untouched (no Runalyze line).

`PROMPT_VERSION` bumped to `2026.10.24-1` (snapshots unchanged). The later Strava removal reworded `UNIVERSAL_FORMAT_RULES`,
deleted `STRAVA_FORMAT_RULES` and its snapshot, and bumped to `2026.10.27-1`.

The Strava push endpoints (`GET/POST /webhook`) were removed: unauthenticated, they could trigger paid LLM analysis.

## Activity ingestion (replaces the dead Strava API)
Strava's API no longer works, so runs now come from Runalyze through `RunalyzeActivityClient`
(`app/agents/coach/runalyze_activities.py`), a drop-in for the old client's activity methods.
It maps Runalyze payloads to the Strava shapes the pipeline already used, so `metrics_engine`,
`build_activity_record` and the tables are unchanged.

| Strava | Runalyze mapping |
|--------|------------------|
| summary dict | `get_activities` item: km→m, `datetime`→local wall-clock + `Z`, sport `Running`→`Run`, `suffer_score` None |
| `velocity_smooth` | derived from `distance`/`time` (central difference, half-window 2) |
| `heartrate`/`cadence`/`watts`/`altitude`/`grade_smooth` | `heart_rate`/`cadence`/`power_original`/`elevation_original`/`grade` |
| `splits_metric` | `splits` (pace s/km → m/s; HR averaged from streams) |
| `best_efforts`, description write-back | not available (empty / dropped) |

**Validation:** on a run present in both systems (2026-06-26, 7.02 km) every metric from
`compute_stream_metrics` matched Strava within 3% except max velocity (+5%) and anaerobic time
(−5%), which come from Strava's proprietary velocity smoothing. HR, cadence and power streams were identical.

**Trigger:** Runalyze has no push webhook. `task_poll_new_runs` (every 15 min, 05:00-22:59) lists the
5 newest runs. Unseen runs younger than 48 h go through the full workflow (analysis + Telegram + email);
older ones are ingested silently. Auth failure → one Telegram alert per day.

**Identity & dedupe:** Runalyze ids differ from Strava ids. `run_activities.source` ('strava' for legacy rows,
'runalyze' for new) and `find_duplicate_run` (same user, start ±5 min, distance ±3%) prevent counting a run twice.
Reconcile only considers `source='runalyze'` rows, so legacy Strava history can never be deleted by it.

**Backfill:** `docker exec airunningcoach python scripts/backfill_runalyze.py [--since YYYY-MM-DD] [--dry-run]`
(default: from the newest stored run). Dry-run on 2026-10-27 found 61 runs since the last stored one, 60 new.

**Latency:** `get_activities` takes 10-30 s on Runalyze's side; list/detail calls use a 90 s timeout.

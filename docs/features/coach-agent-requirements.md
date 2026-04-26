# Coach Agent — Product Requirements Document

**Version:** 1.0  
**Status:** Implementation Complete — v1.0 Shipped  
**Owner:** Tinh Nguyen  
**Last Updated:** 2026-04-26

---

## Revision History

| Version | Date       | Author | Changes |
|---------|------------|--------|---------|
| 0.1     | 2026-03-10 | Tinh   | Initial draft — core prompt engine, sports science |
| 0.2     | 2026-03-22 | Tinh   | Flows extracted to flows/ submodules |
| 0.3     | 2026-04-08 | Tinh   | LTHR / rFTP / Stryd zones added; user_profile convention |
| 1.0     | 2026-04-26 | Tinh   | Multi-stakeholder review; all gaps documented; DoD ticked |

---

## 1. Executive Summary

Coach Dyno is a personal AI running coach that lives in Telegram. It ingests Strava activities in real time, computes running-science metrics, maintains a 4-tier memory system, and delivers proactive coaching via scheduled briefings and interactive chat. The value proposition: Tinh gets elite-coach-level feedback (zone analysis, aerobic decoupling, ACWR-gated volume advice, race-phase periodisation) without a human coach fee, available 24/7 in Vietnamese.

---

## 2. Stakeholder Review Passes

### Pass 1 — Customer / Product Owner

**Reviewer persona:** Product manager who evaluates business value and user adoption risk.

| Question | Verdict |
|---|---|
| Is the core value proposition clear and differentiated? | ✅ Real-time post-run analysis with science-backed metrics is rare in consumer apps |
| Is the daily usage loop strong enough to retain the user? | ✅ Morning briefing + auto Strava analysis = passive touchpoints even on rest days |
| What is the biggest adoption risk? | ⚠️ Onboarding friction: `lthr_bpm`, `rftp_watts`, `threshold_pace_per_km` require lab tests or race results; most users won't configure them |
| Are there hard blockers? | ⚠️ `os.getenv("TELEGRAM_CHAT_ID")` called directly in 3 flows — single-tenant only, blocks multi-user expansion |
| Missing for v1.0? | `NICE-TO-HAVE`: in-app onboarding guide for physiology fields; current UI has no tooltips |

**PO verdict:** Ship v1.0. Log multi-tenant and onboarding gaps as ISS-013, ISS-014.

---

### Pass 2 — End User (Tinh Nguyen)

**Reviewer persona:** Amateur trail/road runner, HCM City, VN50 training cycle, uses Garmin + Strava, communicates in Vietnamese.

| User Story | Delivered? |
|---|---|
| After every run Strava auto-pushes data → I get a detailed analysis in Strava Description within minutes | ✅ Webhook → `analyze_run_with_gemini` → structured JSON → Strava description update |
| Every morning I see a briefing: today's plan, ACWR, weather, active injuries | ✅ `task_morning_briefing` at configurable `briefing_time` (default 06:30) |
| I can chat `/standup`, `/reflect`, `/sync`, `/clear` in Vietnamese | ✅ All commands handled in `handle_telegram_chat` |
| When my load is dangerous (ACWR > 1.5) I get a proactive Telegram alert at noon | ✅ `task_proactive_coach_check` daily at 12:00 VN |
| Every Sunday evening the coach summarises my week and sets next week's target | ✅ `task_weekly_reflection` Sunday 20:00 VN → RAG memory + Telegram |
| I can ask about injuries from months ago and the coach recalls them | ✅ ChromaDB RAG with `search_long_term_memory` tool |
| The coach adjusts my zone thresholds when I give it my LTHR or Stryd rFTP | ✅ `lthr_bpm` → Joe Friel 7-zone; `rftp_watts` → Stryd 6-zone; both in Admin UI |

**User pain points found during review:**
- `get_total_run_stats` reads from `data/athlete_stats.json` which can be days stale — tool docstring warns but user experience is poor
- No `/plan` Telegram command to view the week's planned workouts in one shot
- Memory extraction runs synchronously inside `handle_telegram_chat` — adds latency on every standard-path message

**User verdict:** Happy with v1.0. Plan `/week` command and async memory extraction for v1.1.

---

### Pass 3 — Architect

**Reviewer persona:** Senior backend engineer focused on correctness, testability, and operational safety.

| Area | Finding | Severity |
|---|---|---|
| Hidden dependency | `os.getenv("TELEGRAM_CHAT_ID")` called directly in `analyze_run_with_gemini`, `generate_morning_briefing`, `generate_weekly_reflection` instead of receiving `chat_id` as parameter | MEDIUM (works, but blocks multi-user and unit testing without env stub) |
| Default model | All 4 flows default to `"models/gemini-2.0-flash"` — stale, should be `"models/gemini-flash-latest"` | LOW (alias exists, no breakage) |
| `get_weekly_volume` missing from `utils.py` | Imported from `app.core.database` inside `build_agent_context` with an inner import — not at module top | LOW (works but non-standard) |
| `HttpOptions.timeout` | `client = genai.Client()` — no timeout set in flows; `agent.py` sets 120000ms correctly but flows create their own `genai.Client()` without timeout | MEDIUM (flows could hang on Gemini 503) |
| `_is_degenerate_response` | Only checks `not text or not text.strip()` — extremely empty responses only; does not catch 1-character or very-short junk responses | LOW (acceptable for v1.0) |
| Memory extraction latency | Called synchronously in `handle_telegram_chat` after standard-path response — blocks Telegram ACK | MEDIUM (perceived latency; BackgroundTasks would fix) |
| `calculate_training_phase` | Reads `TZ` from `os.getenv` directly — should use `get_local_tz()` consistently | LOW |
| Agent context duplication | `build_agent_context()` helper exists but `run_analysis.py` and `morning_briefing.py` build context manually instead of using it — DRY violation | MEDIUM |

**Architecture verdict:** Acceptable for v1.0. Log MEDIUM items as known technical debt.

---

## 3. Problem Statement

Tinh trains for trail/road races (VN50, half marathons) without a human coach. Training without feedback leads to injury risk (ACWR spikes) and inefficient training (wrong zones, ignoring aerobic decoupling). Existing apps (Strava, Garmin Connect) show raw stats but don't synthesise them into actionable coaching advice tailored to a specific race goal, phase, and physiology.

---

## 4. Goals

| ID | Goal | Success Metric |
|---|---|---|
| G-1 | Post-run analysis delivered automatically | Analysis in Strava description within 3 minutes of activity upload |
| G-2 | Morning briefing daily | Telegram message at configured time every day |
| G-3 | Proactive overtraining prevention | Alert sent when ACWR > 1.3 within 60 minutes of crossing threshold (noon check) |
| G-4 | Long-term memory recall | Coach correctly references injury or pattern from >4 weeks ago when asked |
| G-5 | Race periodisation awareness | Taper factor applied correctly in last 3 weeks before race |
| G-6 | Tool-augmented chat | AI uses correct tool (read vs write) based on message intent within 1 round-trip |

---

## 5. Functional Requirements

### FR-1 — Strava Webhook Ingest

**FR-1.1** When a Strava `activity.create` event arrives, ingest the activity if it is a run type (`Run`, `TrailRun`, `VirtualRun`).

**FR-1.2** Compute and persist TRIMP (Bannister method, gender-aware) from `moving_time`, `average_heartrate`, `max_hr`, `rest_hr`.

**FR-1.3** Fetch extended activity detail (splits, laps, device) and stream data from Strava API; store raw JSON and stream file locally.

**FR-1.4** Compute running-science metrics from stream (`compute_stream_metrics`) and persist to `run_computed_metrics` table.

**FR-1.5** Memorise a plain-text activity summary in ChromaDB RAG under domain `"coach"`.

**FR-1.6** Trigger `analyze_run_with_gemini` and write the analysis to Strava activity description via API.

**AC [UNIT]:**
```gherkin
Given a Strava activity event with type "Run"
When the webhook handler processes it
Then save_run_activity is called with correct distance_km and trimp_score
And upsert_run_computed_metrics is called when stream data is available
And rag_db.memorize is called with a Vietnamese-language content block
```

---

### FR-2 — Run Analysis (Gemini)

**FR-2.1** Build a structured prompt using: system instruction (persona + HR zones + GCS rubric), shared context (date, phase, ACWR, weekly volume), splits, plan context, pre-computed metrics block.

**FR-2.2** Call Gemini with `response_schema=RunAnalysisResult` (structured JSON output enforced).

**FR-2.3** Extract `analysis_text` and `gcs_score` from response; persist GCS to DB.

**FR-2.4** If plan exists for the run date, mark it `Completed`.

**FR-2.5** Allow AI to call `update_todays_plan` and `set_actual_weekly_target` during analysis.

**FR-2.6** Output must use STRAVA platform format (plain text, no HTML tags).

**AC [INTEGRATION]:**
```gherkin
Given a run activity with splits and stored metrics
When analyze_run_with_gemini is called
Then response.text is parsed as JSON with analysis_text field
And update_run_gcs_score is called with a float between 0 and 10
And Strava description is updated via API
```

---

### FR-3 — Morning Briefing

**FR-3.1** Daily scheduled briefing at `scheduler.briefing_time` (default `06:30`, configurable).

**FR-3.2** Briefing includes: today's plan, weather (injected as string), ACWR, active memories, last 5 chat messages for continuity.

**FR-3.3** Deliver briefing to `TELEGRAM_CHAT_ID` via `send_telegram_msg`.

**FR-3.4** Allow AI to call planning tools: `update_todays_plan`, `set_actual_weekly_target`, `search_long_term_memory`, `set_workout_plan`, `get_volume_for_week`, `get_volume_summary`, `get_metric_trend`.

**FR-3.5** Save briefing text as model message in `chat_history`.

**AC [UNIT]:**
```gherkin
Given the scheduler fires task_morning_briefing
When TELEGRAM_CHAT_ID is configured
Then send_telegram_msg is called with a non-empty string
And save_message is called with role "model"
```

---

### FR-4 — Interactive Chat

**FR-4.1** Process Telegram messages routed to the coach via `handle_telegram_chat`.

**FR-4.2** Classify intent as `fast` or `standard` via `_classify_intent()`.
- Fast: greetings, 1–2 word messages matching `_FAST_EXACT` frozenset → max 512 output tokens, `build_core_system_instruction()`
- Standard: training/planning keywords, long messages → max 1200 output tokens, `build_system_instruction()`

**FR-4.3** Fast path triggers one retry on full standard context if response is empty (`_is_degenerate_response`).

**FR-4.4** Write-intent detection: if message matches `_WRITE_INTENT_KEYWORDS` (Vietnamese + English), add write tools (`update_todays_plan`, `set_workout_plan`, `set_actual_weekly_target`). Otherwise read-only tools only.

**FR-4.5** After every standard-path response, run `extract_implicit_memory` to persist newly observed facts.

**FR-4.6** Support commands: `/clear` (clear history + confirm), `/sync [N|month|all]` (harvest Strava), `/standup` (morning briefing on demand), `/reflect` (weekly reflection on demand).

**FR-4.7** Typing indicator sent before every response (`send_telegram_chat_action`).

**FR-4.8** All responses in Vietnamese (Zone 2).

**AC [UNIT]:**
```gherkin
Given a Telegram message "/clear"
When handle_telegram_chat processes it
Then load_history_for_gemini returns empty list after the call
And a Vietnamese confirmation message is sent

Given a Telegram message containing "chấn thương" (injury keyword)
When _classify_intent is called
Then it returns "standard"

Given a short greeting "xin chào"
When _classify_intent is called
Then it returns "fast"
```

---

### FR-5 — Weekly Reflection

**FR-5.1** Cron trigger every Sunday at 20:00 VN (`task_weekly_reflection`).

**FR-5.2** Inject: recent runs (last 14 days), active memories, ACWR, phase, weekly decision context.

**FR-5.3** Allow AI to call: `set_actual_weekly_target`, `get_volume_for_week`, `get_volume_summary`, `get_metric_trend`.

**FR-5.4** Inject reflection text into ChromaDB RAG with `doc_id = "reflection_{user_id}_{YYYY-MM-DD}"`.

**FR-5.5** Send reflection to Telegram.

**FR-5.6** Save as model message in `chat_history`.

**AC [UNIT]:**
```gherkin
Given task_weekly_reflection fires on Sunday
When Gemini returns a reflection text
Then rag_db.memorize is called with doc_id matching "reflection_<uid>_<date>"
And send_telegram_msg is called with the reflection text
```

---

### FR-6 — Memory Extraction

**FR-6.1** After every standard-path chat, analyse last 30 messages with `extract_implicit_memory`.

**FR-6.2** Use `MemoryExtractionResult` Pydantic schema (structured output) to enforce valid categories.

**FR-6.3** Supported domains: `coach`, `general`. Supported categories: `main_goal`, `injury_status`, `physiological_metrics`, `gear_preference`, `race_strategy`, `training_preference`, `general_lifestyle`, `other`.

**FR-6.4** Status can be `active` or `inactive` — inactive facts overwrite older contradictory facts.

**FR-6.5** Existing memories injected into extraction prompt for cross-domain deduplication.

**FR-6.6** Debug output enabled via `ENABLE_MEMORY_DEBUG=true`.

**AC [UNIT]:**
```gherkin
Given chat history containing "tôi bị đau đầu gối" (knee pain)
When extract_implicit_memory runs
Then insert_memory is called with category="injury_status" and status="active"

Given an existing memory "knee pain" and new chat says "gối tôi đã khỏi"
When extract_implicit_memory runs
Then insert_memory is called with status="inactive"
```

---

### FR-7 — Proactive Coaching Alerts

**FR-7.1** Daily at 12:00 VN: check ACWR from DB.

**FR-7.2** If ACWR > 1.5: send danger-zone alert in Vietnamese.

**FR-7.3** If 1.3 < ACWR ≤ 1.5: send caution alert.

**FR-7.4** If 1 ≤ weeks_left ≤ 3: send taper reminder with correct reduction percentage (25% / 50% / 75%).

**FR-7.5** Multiple alerts can fire in one check (load + taper simultaneously).

**AC [UNIT]:**
```gherkin
Given ACWR = 1.6 and 2 weeks to race
When task_proactive_coach_check fires
Then send_telegram_msg is called twice
And first message contains "CẢNH BÁO TẢI TRỌNG CAO"
And second message contains "Taper" and "50%"
```

---

### FR-8 — Training Periodisation Engine

**FR-8.1** `calculate_training_phase(race_date, race_distance_km)` returns: phase name, weeks_left, microcycle type, taper_factor.

**FR-8.2** Phase boundaries scale by race distance:
- Full marathon (≥42km): Taper 3w / Peak 5w / Build 8w
- Half marathon (≥21km): Taper 2w / Peak 4w / Build 6w
- 10K (≥10km): Taper 1w / Peak 3w / Build 4w
- 5K (<10km): Taper 1w / Peak 2w / Build 3w

**FR-8.3** Taper volume factor: Race week = 0.25, Week −2 = 0.50, Week −3 = 0.75, otherwise 1.0.

**FR-8.4** Microcycle alternates 3 Load : 1 Cutback; taper weeks always Cutback.

**FR-8.5** `taper_factor` is injected into `build_system_instruction()` to constrain AI volume recommendations.

**AC [UNIT]:**
```gherkin
Given race_date is 6 days from today and race_distance_km = 21.1
When calculate_training_phase is called
Then phase == "Race Week" and taper_factor == 0.0

Given race_date is 10 days from today and race_distance_km = 42.2
When calculate_training_phase is called
Then phase contains "Taper Phase" and taper_factor == 0.50
```

---

### FR-9 — Sports Science Engine (Running Metrics)

**FR-9.1** `compute_stream_metrics(streams, meta, config, activity_name)` computes 4 metric groups from Strava stream arrays — never raises, returns `None` for each missing field.

**FR-9.2** Group A — Aerobic Base:
- `hr_zone_distribution` (JSON: % time per zone)
- `time_in_hr_zones_sec` (JSON: seconds per zone)
- `aerobic_decoupling_pct` (Pa:HR efficiency drop)
- `cardiac_drift_pct` (HR drift first→second half)
- `avg_efficiency_factor` (pace-to-HR ratio)

**FR-9.3** Group B — Mechanics:
- `avg_cadence_spm` (steps per minute × 2)
- `avg_stride_length_m`

**FR-9.4** Group C — Pace/Effort:
- `avg_pace_min_km`
- `pace_variability_cv` (coefficient of variation)
- `positive_split_ratio` (second half slowdown)
- `time_in_pace_zones_pct` (JSON, requires `threshold_pace_min_km`)

**FR-9.5** Group D — Elevation:
- `total_elevation_gain_m`
- `grade_adjusted_pace_min_km` (Minetti formula)

**FR-9.6** Group E — Physiological:
- `training_stress_score` (TSS — requires `rftp_watts` or pace threshold)
- `workout_type_detected` (heuristic: Easy / Interval / Tempo / Long Run)
- `interval_count` (number of detected effort blocks)

**FR-9.7** Metrics retrievable via `get_run_computed_metrics` tool in interactive chat.

**AC [UNIT]:**
```gherkin
Given a stream with velocity_smooth and heartrate arrays of equal length ≥ 10
When compute_stream_metrics is called
Then aerobic_decoupling_pct is a float
And avg_efficiency_factor is a positive float

Given a stream with no heartrate data
When compute_stream_metrics is called
Then aerobic_decoupling_pct is None (no exception raised)
```

---

### FR-10 — Weekly Volume Intelligence

**FR-10.1** `gather_weekly_decision_inputs(user_id, week_start_date)` returns 5 inputs:
1. Historical average volume (4-week average)
2. Safe volume limit (historical × 1.15 — 15% rule)
3. Safe TRIMP remaining (chronic × 1.3 − acute)
4. Standard plan goal (from `weekly_targets` table)
5. Actual target confirmed by AI

**FR-10.2** `set_actual_weekly_target` tool enforces `week_start_date` is a Monday; rejects with error string otherwise.

**FR-10.3** `get_formatted_weekly_context()` formats all 5 inputs into Vietnamese for prompt injection.

**FR-10.4** Weekly decision context injected into morning briefing, weekly reflection, and interactive chat (standard path).

**AC [UNIT]:**
```gherkin
Given historical avg = 40km
When gather_weekly_decision_inputs is called
Then safe_volume_limit = 46.0 km (40 × 1.15)

Given week_start_date = "2026-04-27" (a Monday)
When set_actual_weekly_target is called
Then upsert_weekly_target is called with week_start_date

Given week_start_date = "2026-04-26" (a Sunday)
When set_actual_weekly_target is called
Then the return string contains "Thất bại"
```

---

### FR-11 — Zone Models

**FR-11.1** HR zones: Karvonen 5-zone (default), activated when `lthr_bpm = 0`.

**FR-11.2** HR zones: Joe Friel 7-zone, activated when `lthr_bpm > 0`. Uses `lthr_bpm` as anchor.

**FR-11.3** Power zones: Stryd 6-zone, activated when `rftp_watts > 0`. Uses `rftp_watts` as anchor.

**FR-11.4** Pace zones: Jack Daniels 6-zone, activated when `threshold_pace_per_km > 0`.

**FR-11.5** All zone tables formatted into compact string blocks and injected into system instruction.

**FR-11.6** Zone model label (`JOE FRIEL — LTHR {bpm}` vs `KARVONEN — HRR`) included in system instruction header.

**AC [UNIT]:**
```gherkin
Given lthr_bpm = 160
When build_agent_context is called
Then system_inst contains "JOE FRIEL"
And hr_zones_text contains "ZONE1" through "ZONE5C"

Given lthr_bpm = 0 and max_hr = 185, rest_hr = 55
When build_agent_context is called
Then system_inst contains "KARVONEN"
And hr_zones_text contains "zone1" through "zone5"
```

---

### FR-12 — Strava Data Sync

**FR-12.1** Auto-harvest: cron at `0,6,12,18:15` VN — fetches 10 most recent activities, skips RAG fetch if memory already exists.

**FR-12.2** Manual sync `/sync N` — fetches last N activities.

**FR-12.3** Manual sync `/sync month` — fetches last 30 days.

**FR-12.4** Full sync `/sync all` — paginates entire Strava history.

**FR-12.5** Reconcile: after `/sync`, detect activities deleted on Strava and remove from DB + RAG (cap: 10 verifications per sync, configurable via `SYNC_RECONCILE_CAP`).

**FR-12.6** Progress updates sent to Telegram during `/sync all` every 25 activities.

**FR-12.7** `build_activity_record` is single source of truth for TRIMP calculation — used by webhook and all sync paths.

---

### FR-13 — Long-Term Memory (RAG)

**FR-13.1** ChromaDB RAG under domain `"coach"` stores: run analyses, weekly reflections.

**FR-13.2** `search_long_term_memory(query)` tool returns up to 3 relevant snippets.

**FR-13.3** `rag_db.forget(doc_id)` removes reconciled activities from RAG.

**FR-13.4** RAG gateway in `_ingest_one_activity` — skips expensive Strava detail fetch if doc already in ChromaDB.

---

## 6. Non-Functional Requirements

| ID | Requirement | Target | Testable? |
|---|---|---|---|
| NFR-1 | Gemini API timeout | 120 000 ms (120s) — `HttpOptions(timeout=120000)` in `agent.py` | ✅ `test_sdk_contracts.py` |
| NFR-2 | Retry resilience | 3 retries on 503/429/SSL/MALFORMED_RESPONSE with exponential backoff (1s, 2s, 4s) | ✅ `test_agent.py::TestSendMessageWithRetry` |
| NFR-3 | Run analysis latency | < 3 minutes post-webhook (network + Gemini) | Manual (no automated SLA test) |
| NFR-4 | Morning briefing delivery | Within 60s of scheduled time | Manual |
| NFR-5 | Chat response latency | Standard path < 8s P95 | Manual |
| NFR-6 | Memory extraction safety | JSON parse error must not surface to user | ✅ `test_agent.py::TestExtractImplicitMemory` |
| NFR-7 | Tool routing correctness | Write tools only added on write-intent keywords | ✅ `test_agent.py::TestClassifyIntent` |
| NFR-8 | Zone computation correctness | LTHR zones use Joe Friel boundaries exactly | ✅ `tests/test_tools_get_run_full_details.py`, `test_agent.py` |
| NFR-9 | Scheduler task type | All tasks `def` (not `async def`) — BackgroundScheduler is thread pool | Enforced by code convention |
| NFR-10 | Database multi-tenancy | Every SQL query includes `WHERE user_id = ?` | ✅ `test_database.py` |
| NFR-11 | File path safety | All paths use `Path(__file__).resolve().parent...` | Code convention |
| NFR-12 | Zone 1/2/3 compliance | All code English, all Telegram output Vietnamese | Code review |

---

## 7. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Entry Points                                                      │
│  Strava Webhook  │  Telegram Webhook  │  APScheduler Cron         │
└──────┬───────────┴──────────┬──────────┴──────────┬───────────────┘
       │                      │                     │
       ▼                      ▼                     ▼
┌──────────────────────────────────────────────────────────────────┐
│  flows/                                                           │
│  run_analysis.py   morning_briefing.py   weekly_reflection.py    │
│  memory_extraction.py                                             │
│                 agent.py (router + chat)                          │
└──────┬──────────────────────────────────────────┬────────────────┘
       │                                          │
       ▼                                          ▼
┌─────────────┐   ┌────────────────┐   ┌─────────────────────────┐
│ metrics_    │   │   prompts.py   │   │   utils.py              │
│ engine.py   │   │ 8-layer Lego   │   │ TRIMP / ACWR / zones /  │
│ (pure math) │   │ prompt builder │   │ build_agent_context()   │
└─────────────┘   └────────────────┘   └─────────────────────────┘
       │                                          │
       ▼                                          ▼
┌──────────────────┐   ┌────────────────────────────────────────┐
│  harvest.py      │   │  core/                                 │
│  Strava sync     │   │  database.py  │  rag_memory.py         │
│  + reconcile     │   │  SQLite WAL   │  ChromaDB              │
└──────────────────┘   └────────────────────────────────────────┘
```

**Dependency direction:** `flows/ → agents/coach/ → core/` (never upward)

**Tool routing logic:**
- `_TOOLS_READ_ONLY`: `check_training_status`, `get_recent_workouts`, `get_run_full_details`, `search_long_term_memory`, `get_run_stream_csv`, `get_run_computed_metrics`, `get_metric_trend`, `get_volume_for_week`, `get_volume_summary`, `get_total_run_stats`
- `_TOOLS_WRITE` (added on write-intent): `update_todays_plan`, `set_workout_plan`, `set_actual_weekly_target`

---

## 8. Data Model

### SQLite Tables (all require `user_id`)

| Table | Purpose | Key Columns |
|---|---|---|
| `run_activities` | Per-activity: distance, TRIMP, HR | `activity_id`, `user_id`, `distance_km`, `trimp_score`, `gcs_score` |
| `run_activities_raw` | Full Strava JSON + stream file path | `activity_id`, `user_id`, `full_meta`, `stream_file_path` |
| `run_computed_metrics` | 15+ derived metrics from stream | `activity_id`, `user_id`, `aerobic_decoupling_pct`, `avg_cadence_spm`, ... |
| `daily_training_plans` | Per-day workout plans | `user_id`, `date`, `workout_title`, `description`, `status` |
| `weekly_targets` | Weekly volume targets | `user_id`, `week_start_date`, `standard_target_km`, `actual_target_km`, `reasoning` |
| `chat_history` | Message history for Gemini context | `user_id`, `role`, `parts`, `created_at` |
| `core_memory` | Implicit memory facts | `user_id`, `domain`, `category`, `fact`, `status` |
| `users` | User profile | `user_id`, `name`, `max_hr`, `rest_hr` |

### ChromaDB Collections

| Collection | Domain | Purpose |
|---|---|---|
| `coach` | coach | Run analyses, weekly reflections |

---

## 9. Config Schema

Minimal required fields in `data/config.json`:

```json
{
  "model_name": "models/gemini-flash-latest",
  "max_hr": 185,
  "rest_hr": 55,
  "gender": "male",
  "race_date": "YYYY-MM-DD",
  "race_distance_km": 21.1,
  "lthr_bpm": 0,
  "rftp_watts": 0,
  "threshold_pace_per_km": 0,
  "system_instruction": "",
  "user_profile": "",
  "task_description": "",
  "analysis_requirements": "",
  "report_structure": "",
  "output_format": "",
  "scheduler": {
    "briefing_time": "06:30",
    "harvest_hours": "0,6,12,18",
    "harvest_minute": 15
  }
}
```

**Config notes:**
- `lthr_bpm > 0` → activates Joe Friel 7-zone HR model (overrides Karvonen)
- `rftp_watts > 0` → activates Stryd 6-zone power zones
- `threshold_pace_per_km > 0` → activates Jack Daniels 6-zone pace model
- `model_name` defaults to `models/gemini-2.0-flash` in individual flows (stale — use alias in config)

---

## 10. Known Defects / Technical Debt

| ID | Description | Severity | Status |
|---|---|---|---|
| TD-001 | `os.getenv("TELEGRAM_CHAT_ID")` called directly in 3 flows instead of injected via `get_primary_user_id()` | MEDIUM | **Fixed** (agent.py:173,266,530) |
| TD-002 | All 4 flows default to `"models/gemini-2.0-flash"` — stale default, should be `gemini-flash-latest` | LOW | **Fixed** (agent.py + all flows/) |
| TD-003 | `build_agent_context()` not used in `run_analysis.py` and `morning_briefing.py` — they build context manually | MEDIUM | **Fixed** (flows/ refactored, tests updated) |
| TD-004 | Gemini `genai.Client()` created without `HttpOptions(timeout=...)` in individual flows (only `agent.py` sets it) | MEDIUM | **Fixed** (all flows/ + memory_extraction.py) |
| TD-005 | Memory extraction runs synchronously in `handle_telegram_chat` — adds latency | MEDIUM | **Fixed** (daemon thread, agent.py) |
| TD-006 | `get_total_run_stats` reads from stale `data/athlete_stats.json` cache | LOW | Documented in docstring |
| TD-007 | `/plan` Telegram command missing — no single-shot weekly plan view | LOW | **Fixed** (`/plan` + `/schedule` aliases, agent.py) |
| TD-008 | `calculate_training_phase` reads TZ via `os.getenv` instead of `get_local_tz()` | LOW | **Fixed** (utils.py) |

---

## 11. Definition of Done

- [x] **FR-1 (Webhook ingest)**: Webhook → TRIMP → stream metrics → RAG — 15 tests in `test_harvest.py`
- [x] **FR-2 (Run analysis)**: Structured JSON output, GCS score, Strava update — 9 tests in `test_flow_run_analysis.py`
- [x] **FR-3 (Morning briefing)**: Cron + tools + Telegram — 3 tests in `test_agent.py::TestGenerateMorningBriefing`
- [x] **FR-4 (Interactive chat)**: Fast/standard path, tool routing, commands — 27 tests in `test_agent.py::TestHandleTelegramChat + TestClassifyIntent + TestFoldVietnamese`
- [x] **FR-5 (Weekly reflection)**: RAG memorize + Telegram — 2 tests in `test_agent.py::TestGenerateWeeklyReflection`
- [x] **FR-6 (Memory extraction)**: Structured output, category enforcement, status — 5 tests in `test_agent.py::TestExtractImplicitMemory`
- [x] **FR-7 (Proactive alerts)**: ACWR thresholds + taper reminders — covered in `test_scheduler.py`
- [x] **FR-8 (Periodisation)**: Phase/taper boundaries for all 4 race distances — covered in `test_agent.py::TestBuildSystemInstruction`
- [x] **FR-9 (Metrics engine)**: All 5 metric groups, missing data → None — 22 tests in `test_metrics_engine.py`
- [x] **FR-10 (Volume intelligence)**: 15% rule, safe TRIMP, Monday validation — covered in utils tests
- [x] **FR-11 (Zone models)**: LTHR/Karvonen/Stryd/Daniels — covered in `test_agent.py::TestBuildSystemInstruction`
- [x] **FR-12 (Strava sync)**: Auto-harvest, manual sync, reconcile — 15 tests in `test_harvest.py`
- [x] **FR-13 (RAG)**: Memorize + recall + forget — covered in harvest and agent tests
- [x] **Full suite passing**: 810 passed, 0 failures (as of 2026-04-26)
- [x] **Pre-deploy check**: `bash scripts/pre-deploy-check.sh` → PASS
- [x] **Zone 1/2/3 compliance**: All new code reviewed

---

## 12. Test File Map

| Test File | Module Under Test | # Tests | Notes |
|---|---|---|---|
| `test_agent.py` | `agent.py`, all flows | 35 | Chat, briefing, reflection, memory, prompts |
| `test_flow_run_analysis.py` | `flows/run_analysis.py` | 9 | Structured output, GCS, plan status |
| `test_harvest.py` | `harvest.py` | 15 | Webhook ingest, sync, reconcile |
| `test_metrics_engine.py` | `metrics_engine.py` | 22 | All metric groups, edge cases |
| `test_tools_get_run_full_details.py` | `tools.py` | 3 | Tool output format |
| `test_scheduler.py` | `scheduler.py` | ~50 | Proactive check, job setup |
| `test_database.py` | `core/database.py` | ~60 | All DB ops including run_computed_metrics |

---

## 13. Architect Notes

### 8-Layer Lego Prompt Architecture (prompts.py)

The system instruction is composed from 8 stackable layers, always assembled by `build_system_instruction()`:

1. **Identity + Persona** — Coach Dyno core character (Vietnamese, empathetic, science-first)
2. **Zone Tables** — HR/power/pace zones based on physiology config
3. **GCS 4-Pillar Rubric** — 4-pillar scoring: Aerobic Base 30% + Speed Capacity 30% + Health/Injury Risk 25% + Freshness/Recovery 15% (omitted from fast path via `build_core_system_instruction()`)
4. **Training Rules** — tool discipline, taper volume constraints
5. **Platform Formatters** — CHAT/STRAVA/EMAIL/UNIVERSAL output rules
6. **Taper Enforcement** — `taper_factor` injected to cap AI volume recommendations
7. **Weather Layer** — appended only to morning briefing prompt
8. **Memory Layer** — active memories injected into standup and reflection prompts

**Key constraint:** `build_core_system_instruction()` (fast path) omits layers 3–6 to stay within 512 output token budget.

### Context Building (utils.py)

`build_agent_context()` is the canonical factory for `AgentContext` dataclass. It is used by `agent.py` but not yet by all flows (TD-003). The dataclass carries:
- `user_id`, `now`, `phase_text`, `countdown_text`, `acwr_text`
- `actual_volume`, `weekly_decision_context`
- `system_inst`, `shared_context`
- `hr_zones_text`, `pace_zones_text`, `taper_factor`

### Retry Strategy (utils.py)

`send_message_with_retry` is the canonical Gemini call wrapper used by ALL flows. Retryable: `503`, `429`, `Unavailable`, `timed out`, `ssl/SSL/handshake`, `MALFORMED_RESPONSE`. Non-retryable errors fail immediately (no sleep penalty).

### Multi-Tenant Readiness

Current architecture is single-tenant: `get_primary_user_id()` returns `TELEGRAM_CHAT_ID` from env. Multi-user expansion requires:
1. Remove all `os.getenv("TELEGRAM_CHAT_ID")` calls from flows — pass `chat_id` as parameter
2. Route by `user_id` at webhook entry point
3. Replace `get_primary_user_id()` with per-request context

### Critical SDK Contract (test_sdk_contracts.py)

`HttpOptions.timeout` is **milliseconds** in google-genai SDK, not seconds. Gate test exists to prevent regression.

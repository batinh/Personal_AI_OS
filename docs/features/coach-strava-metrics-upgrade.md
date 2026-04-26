# Feature Design: Coach Strava Metrics Upgrade

**Slug:** `coach-strava-metrics-upgrade`
**Branch:** `feat/coach-strava-improvement`
**Status:** Implemented — merged to main (`a720bc8`)

---

## Summary

Strava stream data (`data/streams/{user_id}/{activity_id}.json`) and full activity detail (`run_activity_raw`) are fetched and saved but not used in coach analysis flows. This feature computes a full set of running science metrics from that data, stores them in DB, and injects them into LLM prompts — replacing the raw CSV approach.

---

## Requirements

1. **Replace raw CSV in LLM prompt** with pre-computed metrics block. Raw stream CSV only exposed via tool on explicit user request.
2. **Full metrics set** covering aerobic, cadence, pace, elevation, power (optional Stryd), and **interval/sprint detection**.
3. **New local tools** for stream access, metric trends, and volume queries (weekly/monthly/yearly).
4. **Wire pipeline** so metrics are computed at webhook/sync time, before Gemini analysis.

---

## Metrics Catalogue

### Group A — Aerobic Base
| Column | Source | Notes |
|--------|--------|-------|
| `aerobic_decoupling_pct` | HR + velocity | <5% = good aerobic base |
| `cardiac_drift_pct` | HR first/second half | Independent of pace |
| `avg_efficiency_factor` | avg(velocity/HR)×1000 | Trend over weeks |
| `hr_zone_distribution` | heartrate (Karvonen) | JSON `{"z1":12,"z2":45,...}` |
| `time_in_hr_zones_sec` | heartrate | JSON, seconds per zone |

### Group B — Cadence / Mechanics
| Column | Source | Notes |
|--------|--------|-------|
| `avg_cadence_spm` | cadence | Target 175–180 spm |
| `avg_stride_length_m` | velocity/(cadence/60) | Technique indicator |

### Group C — Pace / Effort
| Column | Source | Notes |
|--------|--------|-------|
| `avg_pace_min_km` | velocity_smooth | Replaces simple avg_speed |
| `pace_variability_cv` | stddev/mean velocity | Lower = more even effort |
| `positive_split_ratio` | first vs second half velocity | >1.05 = positive split |
| `time_in_pace_zones_pct` | velocity | JSON: easy/tempo/race/fast % |

### Group D — Elevation / Grade
| Column | Source | Notes |
|--------|--------|-------|
| `total_elevation_gain_m` | altitude cumsum | |
| `grade_adjusted_pace_min_km` | GAP: pace × (1+0.033×grade%) | Flat-equivalent effort |

### Group E — Power (Stryd only, all nullable)
| Column | Source | Notes |
|--------|--------|-------|
| `avg_power_watts` | watts | |
| `normalized_power_watts` | 30s rolling mean^4 → ^0.25 | NP formula |
| `intensity_factor` | NP / rFTP | IF |
| `training_stress_score` | (time_min × IF² / 36) × 100 | TSS |

### Group F — Interval / Sprint (auto-detected)
| Column | Source | Notes |
|--------|--------|-------|
| `workout_type_detected` | velocity pattern + name | easy/tempo/interval/long/sprint/recovery |
| `interval_reps_count` | velocity detection | Hard effort blocks > 30s |
| `interval_avg_pace_min_km` | mean pace of hard segments | |
| `interval_pace_consistency_pct` | stddev rep paces / mean | 100% = perfectly even |
| `interval_avg_hr_bpm` | mean HR during hard segments | |
| `recovery_hr_quality_bpm` | HR drop after each rep | bpm/min — higher = better recovery |
| `max_velocity_m_s` | max(velocity_smooth) | Peak speed |
| `anaerobic_time_sec` | time at velocity > 95th pct | |
| `z4_z5_time_pct` | from hr_zone_distribution | % hard zone time |

**Interval detection algorithm:**
```
1. Smooth velocity (5-point moving avg)
2. hard_threshold = global_avg_velocity × 1.15
3. Hard effort = contiguous blocks where velocity > threshold for > 30s
4. Guard: reps < 3 and avg rep duration > 300s → "tempo" not "interval"
5. Guard: z4_z5_time < 10% → "easy" or "long_run"
```

---

## Implementation Phases

### Phase 1: DB Schema (`app/core/database.py`)

Add 25 columns to `run_activities` using proven `ALTER TABLE ... ADD COLUMN` migration pattern.
Add functions:
- `upsert_run_computed_metrics(activity_id, user_id, metrics: dict) -> bool`
- `get_run_metrics_from_db(activity_id, user_id) -> dict`
- `get_metric_trend_data(user_id, metric_name, days) -> list[dict]`
- `get_monthly_volume(user_id, year, month) -> dict`
- `get_yearly_volume(user_id, year) -> dict`

Add index: `CREATE INDEX IF NOT EXISTS idx_run_activities_user_date ON run_activities(user_id, start_date)`

### Phase 2: `app/agents/coach/metrics_engine.py` (NEW FILE)

Pure Python + numpy, no DB/Gemini imports.

```python
def compute_stream_metrics(
    streams: dict,          # key_by_type dict
    meta: dict,             # extended_meta (splits, laps, moving_time, avg_hr)
    config: dict,           # max_hr, rest_hr, rftp_watts, threshold_pace_min_km
    activity_name: str = "" # hint for workout_type_detected
) -> dict:
    """Returns flat dict of all metrics. Missing streams → field = None (never raise)."""
```

Internal helpers (private, `_` prefix):
- `_extract_arrays(streams)` — reuse `get_stream_arrays` from stream_storage
- `_hr_zones(hr, max_hr, rest_hr)`
- `_aerobic_decoupling(hr, vel)`
- `_efficiency_factor(vel, hr)`
- `_cadence_stats(cadence, vel)`
- `_pace_stats(vel)`
- `_elevation_stats(altitude, distance, grade)`
- `_power_stats(power, rftp_watts, moving_time_min)` — guard: return None dict if no power data
- `_detect_intervals(vel, hr, time)` — core interval/sprint detection

### Phase 3: `app/routers/webhooks.py` — Wire Pipeline

```
# BEFORE
get_activity_data() → save stream → save_run_activity_raw → analyze_run_with_gemini(csv_data)

# AFTER
get_activity_data()
  → save stream
  → save_run_activity_raw
  → compute_stream_metrics(stream_raw, meta_data, config, act_name)   [NEW]
  → upsert_run_computed_metrics(activity_id, chat_id, metrics)         [NEW]
  → analyze_run_with_gemini(activity_id, act_name, meta_data, config)  [csv_data REMOVED]
```

**Critical**: Also check `app/agents/coach/harvest.py` — grep for `analyze_run_with_gemini` to find all call sites.

### Phase 4: `app/agents/coach/flows/run_analysis.py`

- Remove `csv_data: str` param from `analyze_run_with_gemini()`
- Load computed metrics from DB: `get_run_metrics_from_db(activity_id, user_id_str)`
- Add `build_run_metrics_block(metrics: dict, config: dict) -> str` (cap at 700 chars)
- Pass `metrics_block=` instead of `csv_data=` to prompt builder

### Phase 5: `app/agents/coach/prompts.py`

- Swap `csv_data: str = ""` → `metrics_block: str = ""` in `build_universal_run_analysis_prompt`
- Add interval analysis guidance to system instruction:
  - `workout_type_detected = "interval"` → analyze rep consistency + recovery quality
  - `workout_type_detected = "sprint"` → focus max velocity + anaerobic capacity

### Phase 6: `app/agents/coach/tools.py` — 5 New Tools

```python
def get_run_stream_csv(activity_id: str) -> str:
    """[TOOL] Raw stream CSV — ONLY when user explicitly requests per-second data. Heavy response."""

def get_run_computed_metrics(activity_id: str) -> str:
    """[TOOL] Pre-computed metrics for a run (zones, cadence, decoupling, interval stats)."""

def get_metric_trend(user_id: str, metric_name: str, days: int = 28) -> str:
    """[TOOL] Trend of a metric over N days. E.g. 'aerobic_decoupling_pct', 'avg_cadence_spm'."""

def get_volume_for_week(user_id: str, week_start_date: str) -> str:
    """[TOOL] Total km, run count, avg pace for a specific week (YYYY-MM-DD Monday)."""

def get_volume_summary(user_id: str, period: str, year: int, month: int = 0) -> str:
    """[TOOL] Volume summary for period='month'|'year'. Returns km, runs, longest, best pace."""
```

Register new tools in chat sessions:
- `run_analysis.py` → tools 2, 3, 4, 5
- `morning_briefing.py` → tools 3, 4, 5
- `weekly_reflection.py` → tools 3, 4, 5
- `agent.py` (interactive) → all 5

### Phase 7: `scripts/backfill_metrics.py` (NEW)

One-time script: iterate all `run_activity_raw` where `stream_file_path IS NOT NULL`, compute + upsert metrics.

### Phase 8: Tests

`tests/test_metrics_engine.py` (new):
- `test_compute_easy_run()` — happy path
- `test_interval_detection()` — 6-rep synthetic velocity data
- `test_no_hr_stream()` — HR absent → HR fields None, no crash
- `test_no_power_stream()` — watts absent → power fields None
- `test_tempo_not_false_positive_interval()` — sustained effort ≠ interval
- `test_empty_arrays()` — empty lists → no crash

`tests/test_smoke.py` additions:
```python
from app.agents.coach.metrics_engine import compute_stream_metrics
from app.agents.coach.tools import (
    get_run_stream_csv, get_run_computed_metrics,
    get_metric_trend, get_volume_for_week, get_volume_summary,
)
```

---

## Files Affected

| File | Action |
|------|--------|
| `app/core/database.py` | +25 columns, +5 query functions, +1 index |
| `app/agents/coach/metrics_engine.py` | NEW |
| `app/routers/webhooks.py` | Wire metrics pipeline, remove csv_data |
| `app/agents/coach/flows/run_analysis.py` | Remove csv_data param, inject metrics |
| `app/agents/coach/prompts.py` | Swap csv_data → metrics_block |
| `app/agents/coach/flows/morning_briefing.py` | Register new tools |
| `app/agents/coach/flows/weekly_reflection.py` | Register new tools |
| `app/agents/coach/agent.py` | Register new tools |
| `app/agents/coach/tools.py` | +5 new tools |
| `tests/test_metrics_engine.py` | NEW |
| `tests/test_smoke.py` | +import assertions |
| `scripts/backfill_metrics.py` | NEW |

---

## Risks

| Severity | Risk | Mitigation |
|----------|------|-----------|
| HIGH | `analyze_run_with_gemini` signature change — grep ALL call sites before touching | Run `grep -r "analyze_run_with_gemini"` before Phase 3 |
| HIGH | Interval detection false positives (tempo = interval) | Guard: reps<3 + avg_rep_duration>300s → "tempo" |
| MEDIUM | Power fields always None for non-Stryd → LLM sees empty | Skip power section entirely in metrics_block when all None |
| MEDIUM | Old activities without computed metrics → tool returns nulls | `get_run_computed_metrics` falls back to `get_run_full_details` if all NULL |
| MEDIUM | Prompt length increase | Cap `build_run_metrics_block` at 700 chars |
| LOW | Monthly/yearly volume queries slow | Add `idx_run_activities_user_date` index |

---

## Implementation Order

```
Phase 1 (DB) → Phase 2 (metrics_engine) → Phase 8 (tests for engine)
→ Phase 3 (webhook wiring) → Phase 4 (run_analysis) → Phase 5 (prompts)
→ Phase 6 (tools + register) → Phase 7 (backfill) → Phase 8 (smoke tests)
```

Run smoke test after each phase: `python -m pytest tests/test_smoke.py -v`

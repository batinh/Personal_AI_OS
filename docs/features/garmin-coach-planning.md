# Feature Design: Garmin-Integrated Adaptive Coach Planning

**Slug:** `garmin-coach-planning`
**Status:** In Progress — OAuth Token Auth Implemented (2026-05-10)
**Version:** 3.1
**Date:** 2026-05-10
**Author:** Tinh Nguyen
**Related:** `docs/features/COACH_DYNO_PRD.md`, `docs/features/coach-agent-requirements.md`

---

## 1. Executive Summary

Coach Dyno hiện tại phân tích từng buổi chạy rất tốt, nhưng chưa **lập kế hoạch** như một HLV thật sự. Tính năng này biến Coach Dyno thành một Adaptive Planning Engine — giống Garmin Coach — với khả năng:

1. **Onboarding setup wizard** — thu thập thông tin lần đầu qua 6 bước Telegram (Phase -1)
2. **Thu thập dữ liệu thể trạng hàng ngày** từ Garmin Connect (HRV, Sleep, Readiness, Body Battery)
3. **Tự động sinh giáo án 7 ngày** mỗi Chủ Nhật, có xét đến phase, ACWR, thể trạng Garmin, lịch sử chạy
4. **Daily suggestion fallback** — khi chưa có giáo án active, morning briefing đề xuất bài tập dựa trên rule (Phase 3.5)
5. **Vòng lặp phản hồi RPE** để AI hiệu chỉnh cường độ tuần sau
6. **Quản lý trạng thái VĐV** (Healthy / Sick / Injured / Tapering) để tránh tập khi chưa hồi phục
7. **Tự động điều chỉnh lịch** khi bỏ lỡ bài tập

**v3.0 changes from v2.0:**
- English slash commands as primary; Vietnamese as aliases (reverts v2.0 which had Vietnamese primary)
- Added Phase -1: Setup/Onboarding wizard (6-step FSM via Telegram)
- Added Phase 3.5: Daily Suggestion fallback (pure-function, no LLM, shown in morning briefing when no active plan)
- Added `setup_sessions` DB table
- Added `app/agents/coach/setup_flow.py`, `setup_validators.py`, `daily_suggestion.py` to file map
- Expanded config with `setup.*` and `daily_suggestion.*` fields

**v2.0 changes from v1.0:** Added pending-plan state management, scheduler job sequencing, Garmin circuit breaker, readiness amplification, taper science rules, Telegram callback handler, `training_plans` schema extension.

---

## 2. Garmin Connect — Data Catalog (Nền tảng)

### 2.1 Dữ liệu lấy được ngay (Daily Sync)

| Category | Method | Key Fields | Use in Planning |
|---|---|---|---|
| **Training Readiness** | `get_training_readiness()` | `score` (0–100), `feedback_phrase` | Primary gate: see Section 3.1 |
| **HRV Status** | `get_hrv_data(date)` | `weeklyAvg`, `lastNight`, `status` (BALANCED/LOW/UNBALANCED) | LOW/UNBALANCED → reduce intensity |
| **Sleep** | `get_sleep_data(date)` | `sleepTimeSeconds`, `deepSleepSeconds`, `sleepScores.overall` (0–100) | Sleep < 6h → downgrade intensity 1 level |
| **Body Battery** | `get_body_battery(date)` | `charged` (0–100), `drained` | < 30 morning → swap Hard → Easy |
| **Resting HR** | `get_heart_rates(date)` | `restingHeartRate` | +10% above 7d baseline → flag fatigue |
| **Stress** | `get_stress_data(date)` | `averageStressLevel` (0–100) | > 50 daily avg → flag |
| **SpO2** | `get_spo2_data(date)` | `averageSpO2`, `lowestSpO2` | < 95% → flag (altitude or illness) |

### 2.2 Dữ liệu lấy được (Per-Activity, bổ sung Strava)

| Category | Method | Key Fields | Use |
|---|---|---|---|
| **Garmin Activity** | `get_activity_details(id)` | Lap data, pace zones, cadence stream | Cross-validate với Strava data |
| **Training Status** | `get_training_status(date)` | `trainingLoadStatus` (DETRAINING/MAINTAINING/PRODUCTIVE/OVERREACHING) | Context for weekly plan rationale |
| **Lactate Threshold** | `get_lactate_threshold()` | `heartRateThreshold`, `thresholdPace` | Auto-populate `lthr_bpm` if not set manually |
| **Race Predictions** | `get_race_predictions()` | `5K`, `10K`, `halfMarathon`, `marathon` (predicted time) | Validate race goal feasibility |

### 2.3 Dữ liệu nền tảng tương lai (v2+)

| Category | Method | Use in v2 |
|---|---|---|
| **VO2max** | `get_max_metrics()` | Long-term aerobic capacity trending |
| **Gear / Shoes** | `get_gear()`, `get_gear_stats(id)` | Shoe mileage alert (Phase 4) |
| **Body Weight** | `get_body_composition()` | Adjust TRIMP calculation |
| **Blood Pressure** | `get_blood_pressure()` | Health flag for high-intensity days |
| **Personal Records** | `get_personal_record()` | Race time validation |
| **Endurance Score** | `get_endurance_score()` | Phase progression metric |

### 2.4 Auth Strategy (Critical)

```
Auth method:    Mobile SSO flow (garminconnect library v0.2.x+)
Token storage:  data/garmin_tokens.json (gitignored)
Session policy: Load token file → try resume_login() → full login only on TokenExpired
Rate limit:     ONCE per day max (5:45 AM cron) — never poll within same day
MFA handling:   Async callback-based: send Telegram prompt → user replies code → resume sync
                If user doesn't reply within 10 min → log warning, skip today's sync
Circuit breaker: After 3 consecutive 429/auth failures → skip for 24h, notify user once
Fallback:       If Garmin unavailable → use last known values from DB (up to 3 days stale OK)
```

**Risk:** Garmin's unofficial API can be blocked (429). Mitigation: persistent token, daily-only sync, exponential backoff, circuit breaker pattern.

---

## 3. Garmin Coach — Training Science Rules

These rules encode how Garmin Coach actually behaves. They MUST be implemented as hard constraints in the prompt (not just soft suggestions).

### 3.1 Readiness Gating — Both Downside AND Upside

| Score Range | Action | Workout Type Impact |
|---|---|---|
| **80–100** (Excellent) | **Amplify**: allow extra km (+10%) or add 2nd quality session | Interval/LongRun volume can increase |
| **60–79** (Good) | Full plan executes as designed | No changes |
| **50–59** (Moderate) | Reduce: swap Interval → Tempo if present | Max 1 hard session per week |
| **40–49** (Low) | Reduce: all Hard sessions → Easy Run | No quality work |
| **0–39** (Poor) | Force: Rest Day or Recovery Run only | Intervals forbidden |

> **Critical:** v1.0 only restricted downward. Garmin also amplifies on good days — this drives optimal progression.

### 3.2 Quality Session Distribution (Weekly Constraint)

- **Max 2 quality sessions per week** (Tempo, Interval, or Race Pace)
- **Minimum 1 rest day per week** (never 7 consecutive running days)
- **Long Run always on Saturday or Sunday** (not mid-week)
- **No back-to-back hard sessions** (must have Easy/Rest between quality days)
- **Typical pattern**: Easy–Quality–Easy–Quality–Easy–LongRun–Rest

> Guard in plan generation: if AI proposes 3+ quality sessions or back-to-back hard days, constraint must reject and revise.

### 3.3 Volume Progression Rules (10% Rule)

| Week Type | Volume Rule |
|---|---|
| Load Weeks 1–3 | +5–10% from previous week; ceiling: +15% if ACWR < 1.2 |
| **Recovery Week (Week 4)** | **-25 to -30% of Load Week 3** (deload cycle) |
| After deload | Resume from Load Week 3 level, not from deload level |

- ACWR sweet spot: **0.8–1.3** (gate computed before AI call)
- ACWR > 1.3 → cap at `actual_volume_7d × 1.0` (no increase)
- ACWR > 1.4 → cap at `actual_volume_7d × 0.85` (forced reduction)

### 3.4 Periodization Phases

Automatically calculated from `race_date` and `race_distance_km` (already implemented in `calculate_training_phase()`):

| Race Distance | Base | Build | Peak | Taper |
|---|---|---|---|---|
| Marathon (42km) | 12–16w | 8–10w | 5–6w | **3w** |
| Half Marathon (21km) | 8–12w | 6–8w | 4w | **2w** |
| 10K | 6–8w | 4–6w | 3w | **1w** |

> `calculate_training_phase()` in `utils.py` already handles this correctly — no changes needed here.

### 3.5 Tapering Rules (Critical for Race Prep)

| Countdown | Volume | Intensity | Quality Sessions |
|---|---|---|---|
| **Week -3** (21–15 days) | 75% of peak week | Full intensity | 2 quality sessions |
| **Week -2** (14–8 days) | 50% of peak week | Full intensity | 1 quality session (last hard session) |
| **Race Week** (≤7 days) | 25% of peak week | Reduced — Easy only | 0 quality sessions |
| **Race Day** | Race distance | Race pace | THE RACE |

**Additional taper constraints:**
- **Last hard session**: must be 6–8 days before race (week -2 window only)
- **Minimum 2 days between quality sessions** in taper (no Tempo on Day -5 + Interval on Day -3)
- **Intensity stays HIGH** in Week -3; only volume drops (intensity reduction begins Week -2)

### 3.6 In-Week Reschedule Triggers

| Trigger | System Action |
|---|---|
| Missed workout (not completed by 23:00) | Defer once: move to next available easy day; reduce weekly target -5% |
| HRV drops < 50% of 7-day average | Downgrade today's session: Interval → Easy; Tempo → Easy |
| Sleep last night < 6 hours | Downgrade intensity 1 level (Tempo → Easy; Interval → Tempo) |
| Resting HR +10% above 7d baseline | Flag fatigue; swap Hard → Easy + mobility |
| RPE > 8 on previous Easy run | Warn overtraining; suggest reducing next session intensity |
| athlete_state = sick/injured | Pause all plan generation; notify user |

---

## 4. AI Weekly Plan Generation — Input Requirements

AI cần đủ 4 nhóm input sau để sinh giáo án chất lượng:

### 4.1 Static Config (từ `data/config.json`)
```json
{
  "race_date": "YYYY-MM-DD",
  "race_distance_km": 21.1,
  "race_target_time_min": 105,
  "max_hr": 185,
  "rest_hr": 55,
  "lthr_bpm": 160,
  "rftp_watts": 0,
  "threshold_pace_per_km": "5:20",
  "user_profile": "...",
  "gender": "male"
}
```

### 4.2 Current State (DB + Garmin, tính tại thời điểm sinh kế hoạch)

| Field | Source | Notes |
|---|---|---|
| `athlete_state` | `athlete_state` table (latest row) | healthy / sick / injured / tapering / race_week |
| `acwr_current` | Calculated from `run_activities` | Gate: > 1.4 → max volume -30% |
| `training_readiness_score` | `garmin_daily_metrics` | 0–100 (today or latest ≤3 days) |
| `hrv_status` | `garmin_daily_metrics` | BALANCED / LOW / UNBALANCED |
| `hrv_last_night` | `garmin_daily_metrics` | Compared to `hrv_weekly_avg` |
| `sleep_score_avg_7d` | `garmin_daily_metrics` | Average last 7 days |
| `sleep_last_night_hours` | `garmin_daily_metrics` | sleep_duration_sec / 3600 |
| `body_battery_morning` | `garmin_daily_metrics` | Today's morning value |
| `resting_hr_7d_avg` | `garmin_daily_metrics` | Baseline for fatigue detection |
| `actual_volume_7d` | `run_activities` | Total km last 7 days |
| `active_injuries` | `core_memory` WHERE category='injury_status' AND status='active' | |
| `rpe_last_3_runs` | `run_activities.rpe_score` | Perceived effort recent trend |
| `garmin_training_status` | `garmin_daily_metrics` | DETRAINING / MAINTAINING / PRODUCTIVE / OVERREACHING |

### 4.3 Historical Context

| Field | Source | Notes |
|---|---|---|
| `last_week_plan_vs_actual` | `weekly_plans` (accepted) + `run_activities` | Completion rate % |
| `gcs_scores_last_5` | `run_activities.gcs_score` | Quality trend |
| `phase_text` | `calculate_training_phase()` | Base / Build / Peak / Taper |
| `countdown_days` | From `race_date` | Số ngày còn lại |
| `weekly_target_prev` | `weekly_targets` | Target tuần trước |
| `recovery_week_due` | week_number % 4 == 0 | Cutback week flag |

### 4.4 Computed Constraints (Hard-Coded Before AI Call)

| Constraint | Rule |
|---|---|
| `max_weekly_km` | See Section 3.3 volume rules |
| `max_quality_sessions` | 2 (always; may be 1 during taper) |
| `forced_rest_days_min` | 1 (always) |
| `readiness_gate` | See Section 3.1 thresholds |
| `taper_volume_pct` | 75% / 50% / 25% based on countdown |
| `last_hard_session_limit` | If countdown ≤ 8 → no more hard sessions |
| `race_week_override` | countdown ≤ 7 → Easy + Rest only (race on race day) |

---

## 5. AI Output Schema (WeeklyPlanResult)

```python
# app/agents/coach/schemas.py

class WorkoutDay(BaseModel):
    date: str                          # "YYYY-MM-DD"
    workout_type: Literal[
        "Easy", "Tempo", "Interval",
        "LongRun", "Recovery", "Rest", "CrossTraining"
    ]
    title: str                         # Vietnamese, e.g. "Chạy nhẹ dưỡng sức"
    description: str                   # Vietnamese, 2-4 câu coaching cue
    target_distance_km: Optional[float]
    target_duration_min: Optional[int]
    target_pace_range: Optional[str]   # e.g. "5:30–5:50/km"
    target_hr_zone: Optional[int]      # 1–5
    target_hr_range: Optional[str]     # e.g. "130–145 bpm"
    rpe_target: Optional[int]          # 1–10
    nutrition_alert: Optional[str]     # Non-null only for LongRun > 15km

class WeeklyPlanResult(BaseModel):
    week_start_date: str               # Monday "YYYY-MM-DD"
    week_total_km: float
    training_rationale: str            # Vietnamese, 3–4 câu giải thích logic tuần
    acwr_projection: float             # Projected ACWR after this week
    days: List[WorkoutDay]             # Exactly 7 entries (Mon–Sun)
    adaptations_made: List[str]        # Những gì AI đã điều chỉnh so với ideal, Vietnamese
    recovery_warning: Optional[str]    # If significant injury/fatigue risk detected
```

---

## 6. Database Schema

### 6.1 NEW — `garmin_daily_metrics` table

```sql
CREATE TABLE IF NOT EXISTS garmin_daily_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,                    -- YYYY-MM-DD
    training_readiness_score INTEGER,      -- 0–100
    hrv_status TEXT,                       -- BALANCED/LOW/UNBALANCED/NONE
    hrv_weekly_avg REAL,
    hrv_last_night REAL,
    sleep_score INTEGER,                   -- 0–100
    sleep_duration_sec INTEGER,
    deep_sleep_sec INTEGER,
    body_battery_morning INTEGER,          -- 0–100
    body_battery_evening INTEGER,
    resting_hr INTEGER,
    stress_avg INTEGER,                    -- 0–100
    spo2_avg REAL,
    training_status TEXT,                  -- DETRAINING/MAINTAINING/PRODUCTIVE/OVERREACHING
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, date)
);
CREATE INDEX IF NOT EXISTS idx_garmin_daily_user_date
    ON garmin_daily_metrics(user_id, date);
```

### 6.2 NEW — `athlete_state` table (append-only audit trail)

```sql
CREATE TABLE IF NOT EXISTS athlete_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'healthy',  -- healthy/sick/injured/tapering/race_week
    note TEXT,                               -- e.g. "Flu, chỉ nghỉ ngơi"
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT DEFAULT 'user'           -- user / system
    -- NOTE: Append-only. Query: SELECT * ORDER BY updated_at DESC LIMIT 1
);
-- No UNIQUE constraint — we keep full history (audit trail)
```

> **Design decision:** Append-only (not update-in-place). Latest row = current state. Prevents race conditions from concurrent scheduler + user writes. Full history for debugging.

### 6.3 NEW — `weekly_plans` table (pending/accepted/rejected staging)

```sql
CREATE TABLE IF NOT EXISTS weekly_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    week_start_date TEXT NOT NULL,          -- Monday YYYY-MM-DD
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ai_output TEXT NOT NULL,                -- serialized WeeklyPlanResult JSON
    status TEXT DEFAULT 'pending',          -- pending / accepted / rejected / expired
    rejected_reason TEXT,
    UNIQUE(user_id, week_start_date)        -- one plan per user per week
    -- ON CONFLICT: replace (re-generation overwrites previous pending)
);
```

> **Why needed:** `/accept` and `/reject` commands need a source of truth for "what plan was I shown?". Without this table, users can accept ghost plans or the system can lose the generated JSON on restart.

### 6.4 NEW — `setup_sessions` table (onboarding FSM state)

```sql
CREATE TABLE IF NOT EXISTS setup_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 1,        -- 1–6 (current wizard step)
    data TEXT NOT NULL DEFAULT '{}',        -- JSON: collected answers so far
    status TEXT NOT NULL DEFAULT 'active',  -- active / completed / abandoned
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)                         -- one active setup per user
    -- ON CONFLICT REPLACE: re-/setup starts over
);
CREATE INDEX IF NOT EXISTS idx_setup_sessions_user
    ON setup_sessions(user_id, status);
```

> **Design rationale:** Setup state lives in DB, not in-memory. Survives container restarts mid-wizard. `ON CONFLICT REPLACE` means re-typing `/setup` starts fresh (abandons any prior in-progress session). Stale sessions (no update in 24h) auto-expire via scheduler cleanup.

### 6.5 EXTEND — `training_plans` table

The existing `training_plans` table has only `(user_id, date, workout_title, description, status)`. Add columns to support full `WorkoutDay` schema:

```sql
ALTER TABLE training_plans ADD COLUMN workout_type TEXT;
ALTER TABLE training_plans ADD COLUMN target_distance_km REAL;
ALTER TABLE training_plans ADD COLUMN target_duration_min INTEGER;
ALTER TABLE training_plans ADD COLUMN target_pace_range TEXT;
ALTER TABLE training_plans ADD COLUMN target_hr_zone INTEGER;
ALTER TABLE training_plans ADD COLUMN target_hr_range TEXT;
ALTER TABLE training_plans ADD COLUMN rpe_target INTEGER;
ALTER TABLE training_plans ADD COLUMN nutrition_alert TEXT;
ALTER TABLE training_plans ADD COLUMN readiness_gated_from TEXT;   -- original type if readiness swapped
ALTER TABLE training_plans ADD COLUMN actual_distance_km REAL;
ALTER TABLE training_plans ADD COLUMN actual_rpe_score INTEGER;
ALTER TABLE training_plans ADD COLUMN skipped_reason TEXT;
ALTER TABLE training_plans ADD COLUMN weekly_plan_id INTEGER;      -- FK to weekly_plans.id
ALTER TABLE training_plans ADD COLUMN created_at TIMESTAMP;
ALTER TABLE training_plans ADD COLUMN updated_at TIMESTAMP;
```

> Migration strategy: All ALTER TABLE ADD COLUMN with no NOT NULL constraint are safe on existing data (columns default to NULL). Run idempotently in `init_db()` using `try/except OperationalError` (column already exists).

### 6.6 EXTEND — `run_activities` table

```sql
ALTER TABLE run_activities ADD COLUMN rpe_score INTEGER;           -- 1–10 (user input after activity)
```

---

## 7. Telegram UX Design

### 7.1 Setup Wizard — 6-Step Onboarding Flow

The setup wizard is a linear FSM over Telegram. Each step prompts the user, validates the reply, stores the answer, and advances to the next step. All prompts are Vietnamese (Zone 2).

```
Step 1: race_distance_km
  Prompt: "🏁 Bước 1/6: Anh đang luyện tập cho cự ly nào?
  → Nhập số km (ví dụ: 10, 21.1, 42.2) hoặc 5K/10K/HM/FM"
  Validator: validate_distance() — accepts numeric or known aliases

Step 2: race_date
  Prompt: "📅 Bước 2/6: Ngày thi đấu của anh là khi nào?
  → Nhập định dạng DD/MM/YYYY (ví dụ: 15/06/2026)"
  Validator: validate_date() — must be ≥ 4 weeks from today

Step 3: race_target_time_min
  Prompt: "⏱ Bước 3/6: Mục tiêu hoàn thành là bao lâu?
  → Nhập H:MM (ví dụ: 1:45) hoặc MM phút (ví dụ: 105)"
  Validator: validate_time() — reasonable for given distance

Step 4: current_weekly_km
  Prompt: "📊 Bước 4/6: Hiện tại anh chạy khoảng bao nhiêu km mỗi tuần?
  → Nhập số km (ví dụ: 30)"
  Validator: validate_kmweek() — 0–200 km range

Step 5: training_days_per_week
  Prompt: "🗓 Bước 5/6: Anh muốn tập mấy ngày mỗi tuần?
  → Nhập số 3–6"
  Validator: validate_days() — integer 3–6

Step 6: preferred_rest_days
  Prompt: "😴 Bước 6/6: Anh thường nghỉ vào ngày nào trong tuần?
  → Nhập tên ngày (ví dụ: Thứ Hai, Thứ Sáu) hoặc số (1=T2, 7=CN)"
  Validator: validate_rest_days() — 1–3 days, not conflicting with training days
```

**Optional health data (asked only if no Garmin linked):**
```
Step 7 (conditional): max_hr
  Prompt: "❤️ Nếu biết, anh cho tôi biết nhịp tim tối đa (HR max)?
  → Nhập số bpm (ví dụ: 185) hoặc /skip để bỏ qua"
```

**Completion message (Vietnamese):**
```
✅ Thiết lập hoàn tất!

📋 Tóm tắt:
• Cự ly: Half Marathon (21.1km)
• Ngày đua: 15/06/2026 (còn 7 tuần)
• Mục tiêu: 1:45:00
• Khối lượng hiện tại: 35km/tuần
• Ngày tập: 5 ngày/tuần
• Ngày nghỉ: Thứ Hai, Thứ Sáu

🤖 Giáo án tuần đầu tiên sẽ được tạo vào Chủ Nhật 20:30.
Dùng /status để xem trạng thái, /plan để tạo ngay.
```

**Re-setup behavior:**
- `/setup` when a plan is active → confirm prompt: "Thiết lập lại sẽ hủy giáo án hiện tại. Anh có chắc không? (yes/no)"
- On confirmation → mark current `weekly_plans` status=`expired`, start new setup session
- Config is written only on step 6 completion (not incrementally) via append-log

### 7.2 Setup FSM Implementation

```python
# app/agents/coach/setup_flow.py

SETUP_STEPS = [
    {"num": 1, "prompt": "...", "key": "race_distance_km",      "validator": validate_distance},
    {"num": 2, "prompt": "...", "key": "race_date",             "validator": validate_date},
    {"num": 3, "prompt": "...", "key": "race_target_time_min",  "validator": validate_time},
    {"num": 4, "prompt": "...", "key": "current_weekly_km",     "validator": validate_kmweek},
    {"num": 5, "prompt": "...", "key": "training_days_per_week","validator": validate_days},
    {"num": 6, "prompt": "...", "key": "preferred_rest_days",   "validator": validate_rest_days},
]

def start_setup(user_id: str) -> str:
    """Create or replace setup_sessions row for user; return step 1 prompt."""

def advance_setup(user_id: str, user_reply: str) -> str:
    """Validate reply for current step, store answer, advance step or finalize.
    Returns next prompt on success, error message on validation failure (stays on same step).
    """

def finalize_setup(user_id: str, collected_data: dict) -> None:
    """Write collected data to config via append-log, mark session completed."""

def is_setup_in_progress(user_id: str) -> bool:
    """True if user has an active setup session. Used by agent.py to route messages."""

def cleanup_stale_setup_sessions() -> None:
    """Mark sessions with updated_at > 24h ago as abandoned. Run daily."""
```

### 7.3 New Helper: `send_inline_keyboard_menu()` in `notification.py`

```python
def send_inline_keyboard_menu(
    chat_id: str,
    text: str,
    buttons: list[list[dict]],  # [[{"text": "1", "callback_data": "rpe:act123:1"}, ...]]
) -> None:
    """Send Telegram message with inline keyboard for button responses."""
```

Needed for RPE collection and plan accept/reject buttons.

### 7.4 New Handler: `callback_query` in `webhooks.py`

Telegram distinguishes:
- `message` → user typed text → `handle_telegram_chat()`
- `callback_query` → user pressed inline button → NEW `handle_telegram_callback()`

```python
# webhooks.py: route callback_query to handler
async def handle_telegram_update(payload: dict):
    if "callback_query" in payload:
        await handle_telegram_callback(payload["callback_query"])
    elif "message" in payload:
        handle_telegram_chat(...)

def handle_telegram_callback(callback_query: dict):
    callback_data = callback_query["data"]  # e.g. "rpe:activity_id:8"
    user_id = str(callback_query["from"]["id"])

    if callback_data.startswith("rpe:"):
        _, activity_id, score = callback_data.split(":")
        # store RPE to DB
        # answer callback (Telegram toast confirmation)
    elif callback_data == "plan:accept":
        # same as /accept
    elif callback_data == "plan:reject":
        # prompt for reason
```

> **Critical:** After handling, always call `answerCallbackQuery(callback_query_id)` to clear the loading spinner.

### 7.5 Command Reference (English Primary, Vietnamese Aliases)

Per CLAUDE.md Zone 1 rule: code, DB, git commits = English. Zone 2 rule: user-facing messages = Vietnamese. Slash commands are command identifiers that live in code (Zone 1) → **English is primary**. Vietnamese aliases are convenience shortcuts for the user.

| English Command (Primary) | Vietnamese Alias | Description |
|---|---|---|
| `/setup` | — | Start coach setup wizard (onboarding) |
| `/accept` | `/chap` | Accept generated weekly plan |
| `/reject <reason>` | `/bochap <lý do>` | Reject plan, trigger regeneration |
| `/sick` | `/om` | Mark athlete as sick |
| `/recover` | `/khoe` | Mark athlete as recovered |
| `/plan` | `/kehoach` | Request manual plan generation |
| `/status` | — | Show current plan + athlete state + readiness |

Both English and Vietnamese aliases recognized in `agent.py`. English command is shown in help and confirmation messages; Vietnamese alias is shown in Telegram prompts for convenience.

```python
# app/agents/coach/agent.py
COMMAND_ALIASES = {
    "/chap":    "/accept",
    "/bochap":  "/reject",
    "/om":      "/sick",
    "/khoe":    "/recover",
    "/kehoach": "/plan",
}

def resolve_command(text: str) -> str:
    """Normalize Vietnamese aliases to English primary command."""
    first_token = text.split()[0].lower()
    return COMMAND_ALIASES.get(first_token, first_token)
```

### 7.6 Weekly Plan Preview Format (Compact, < 3500 chars)

```
🗓 Giáo án tuần 02/05–08/05 (Còn 6 tuần đến HM)

📅 Thứ Hai: Easy 8km | Zone2 | 5:40/km | RPE5
   Khởi động tuần mới, giữ nhịp aerobic.

📅 Thứ Ba: Nghỉ

📅 Thứ Tư: Tempo 10km | Zone3-4 | 5:00/km | RPE7
   Bài ngưỡng, chú ý giữ pace ổn định.

📅 Thứ Năm: Easy 6km | Zone2 | 5:50/km | RPE4

📅 Thứ Sáu: Nghỉ

📅 Thứ Bảy: Long Run 18km | Zone2 | 5:45/km | RPE6
   ⚡ Mang 2 gói gel + 500ml điện giải

📅 Chủ Nhật: Easy 5km phục hồi | Zone1 | 6:00/km | RPE3

📊 Tổng: 47km | ACWR dự kiến: 1.08
⚡ Điều chỉnh: HRV thấp hôm qua → đổi Interval → Tempo thứ Tư.

[✅ Dùng giáo án này] [❌ Hủy]
Hoặc: /reject <lý do>
```

> Test: `assert len(preview) < 3500` — must fit in one Telegram message.

---

## 8. Daily Suggestion Fallback (No Active Plan)

When no accepted plan exists for the current week, the morning briefing shows a rule-based daily suggestion instead of a plan summary. This mirrors Garmin's "Daily Suggested Workout" feature — no LLM call, no user confirmation needed, purely deterministic.

### 8.1 Decision Logic

```python
# app/agents/coach/daily_suggestion.py

READINESS_THRESHOLDS = {"excellent": 80, "good": 60, "moderate": 50, "low": 40}
ACWR_SAFE_MAX = 1.3
ACWR_CRITICAL = 1.4

def compute_daily_suggestion(
    readiness_score: int | None,    # None if Garmin unavailable
    acwr: float | None,             # None if < 4 weeks of data
    recent_runs: list[dict],        # last 7 days from run_activities
    athlete_state: str,             # healthy/sick/injured/tapering
    day_of_week: int,               # 0=Mon, 6=Sun
    days_since_last_run: int,
) -> dict:
    """Pure function. No I/O, no LLM, deterministic.
    Returns: {type, title_vi, description_vi, target_km, target_pace_zone, rpe_target}
    """
```

**Rule priority (evaluated top-down, first match wins):**

| Priority | Condition | Suggestion |
|---|---|---|
| 1 | `athlete_state` = sick or injured | Rest — Nghỉ ngơi hoàn toàn |
| 2 | `acwr` > 1.4 (critical overreach) | Rest — Nguy cơ quá tải |
| 3 | `readiness_score` < 40 (poor) | Recovery Run — 20–30 min Z1 |
| 4 | `days_since_last_run` ≥ 3 | Easy Run — Duy trì thói quen |
| 5 | `acwr` > 1.3 (moderate overreach) | Easy Run — Không tăng khối lượng |
| 6 | `readiness_score` 40–59 (low/moderate) | Easy Run — Z2 nhẹ |
| 7 | `day_of_week` in [5, 6] (weekend) + `readiness_score` ≥ 60 | Long Run — Bài cuối tuần |
| 8 | `readiness_score` ≥ 80 (excellent) + not back-to-back | Tempo or Interval — Ngày tốt để chất lượng |
| 9 | `readiness_score` 60–79 (good) | Easy or Tempo based on recent pattern |
| 10 | fallback | Easy Run — Z2, thoải mái |

### 8.2 Integration into Morning Briefing

```python
# app/agents/coach/flows/morning_briefing.py

def build_morning_briefing(user_id: str, config: dict) -> str:
    has_active_plan = _has_active_plan_this_week(user_id)

    if has_active_plan:
        # existing: show today's planned workout from training_plans
        ...
    else:
        # NEW: show daily suggestion
        suggestion = compute_daily_suggestion(
            readiness_score=garmin_data.get("training_readiness_score"),
            acwr=calculate_acwr(user_id),
            recent_runs=get_recent_runs(user_id, days=7),
            athlete_state=get_athlete_state(user_id),
            day_of_week=datetime.today().weekday(),
            days_since_last_run=_days_since_last_run(user_id),
        )
        # format and include in briefing message (Vietnamese)
        return _format_briefing_with_suggestion(suggestion, garmin_data)
```

**Format in morning briefing (Vietnamese, no setup required):**
```
🌅 Chào buổi sáng! Hôm nay (Thứ Tư)

📊 Thể trạng: Readiness 72 | HRV Balanced | Ngủ 7.5h

💡 Gợi ý hôm nay (chưa có giáo án):
   Tempo Run nhẹ — 6–8km @ Z3
   Ngày tốt để chạy ngưỡng, readiness đang ổn.

ℹ️ Chưa có giáo án tuần này. Dùng /plan để tạo giáo án AI ngay.
```

> Daily suggestion is informational only — it does NOT write to `training_plans`. It is a nudge, not a commitment.

### 8.3 Guard: Suggest Setup if Not Configured

If `race_date` or `race_distance_km` missing from config, morning briefing shows:
```
ℹ️ Anh chưa thiết lập mục tiêu đua. Dùng /setup để bắt đầu.
```

---

## 9. Scheduler Design

### 9.1 New Jobs + Timing

| Job | Time | Depends On | Risk |
|---|---|---|---|
| `task_garmin_sync` | **5:45 AM** daily | none | Garmin API down |
| `task_morning_briefing` | 6:00 AM daily | garmin_sync written to DB | needs 15min buffer |
| `task_weekly_plan_generation` | Sunday 20:30 | weekly_reflection done | 30-min buffer |
| `task_auto_reschedule` | 23:00 daily | run activities synced | 1h after webhook window |
| `task_gear_check` | Monday 7:00 AM | garmin_sync | weekly |
| `task_cleanup_stale_setup` | 3:00 AM daily | none | cleanup stale setup sessions (24h timeout) |

> **Timing rationale:** Garmin sync at 5:45 (not 6:00) gives 15 minutes for data to be written before briefing reads it. Plan gen at 20:30 (not 20:00) gives 30 minutes after weekly_reflection. Setup cleanup at 3:00 AM to avoid overlap with briefing.

### 9.2 Job Deduplication Guard

```python
def task_weekly_plan_generation():
    existing = db.query(
        "SELECT id FROM weekly_plans WHERE user_id=? AND week_start_date=? AND status IN ('pending','accepted')",
        (user_id, current_week_monday())
    )
    if existing:
        logger.info("[SCHEDULER] Plan already generated this week, skipping.")
        return
    # Generate plan...
```

### 9.3 Circuit Breaker for Garmin API

```python
# In GarminClient:
# After 3 consecutive failures (429 or auth error) → set circuit_open = True for 24h
# On circuit_open: skip sync, use last known DB values
# Notify user once via Telegram when circuit opens
```

State tracked in `garmin_daily_metrics` table: if last 3 rows have `training_readiness_score IS NULL`, skip today's sync attempt.

### 9.4 Async MFA Handling

When Garmin requires MFA:
1. Send Telegram prompt: "Garmin cần xác thực MFA. Anh gửi code 6 số đây."
2. Set a flag in DB: `garmin_mfa_pending = True`
3. Return from sync job immediately (do NOT block)
4. When user sends 6-digit code to Telegram → trigger `resume_garmin_auth(code)`
5. 10-minute timeout: if no code → log warning, clear flag

---

## 10. Phased Implementation Plan

### Phase -1 — Setup / Onboarding (3–4 ngày)

**Goal:** User có thể cấu hình mục tiêu đua qua wizard 6 bước trên Telegram trước khi bất kỳ phase nào chạy. This is a prerequisite for plan generation.

| ID | Task | File | Effort |
|---|---|---|---|
| P-1.1 | `setup_sessions` table schema + index | `app/core/database.py` | 0.5h |
| P-1.2 | `validate_distance()`, `validate_date()`, `validate_time()`, `validate_kmweek()`, `validate_days()`, `validate_rest_days()` | `app/agents/coach/setup_validators.py` (NEW) | 2h |
| P-1.3 | `start_setup()`, `advance_setup()`, `finalize_setup()`, `is_setup_in_progress()` FSM | `app/agents/coach/setup_flow.py` (NEW) | 3h |
| P-1.4 | `/setup` command routing in `agent.py`; check `is_setup_in_progress()` before routing to normal flow | `app/agents/coach/agent.py` | 1h |
| P-1.5 | `COMMAND_ALIASES` dict for Vietnamese → English normalization (`resolve_command()`) | `app/agents/coach/agent.py` | 0.5h |
| P-1.6 | Re-setup guard: confirm prompt + invalidate pending/accepted plans | `app/agents/coach/setup_flow.py` | 1h |
| P-1.7 | `save_config()` with append-log immutable pattern (new config fields from setup) | `app/core/config.py` | 1.5h |
| P-1.8 | Stale session cleanup: `cleanup_stale_setup_sessions()` (24h timeout) | `app/agents/coach/setup_flow.py` + `scheduler.py` | 1h |
| P-1.9 | "Not configured" guard in morning briefing: show `/setup` prompt if no race_date | `app/agents/coach/flows/morning_briefing.py` | 0.5h |
| P-1.10 | Smoke test import assertions for new symbols | `tests/test_smoke.py` | 0.5h |
| P-1.11 | Tests: 6 happy-path steps, validator edge cases, re-setup invalidation, 24h timeout, Vietnamese alias normalization | `tests/test_setup_flow.py` (NEW) | 4h |

**DoD:** `/setup` → 6-step Telegram wizard → config written → completion message with race summary. `/setup` mid-plan → confirm + invalidate. Vietnamese aliases resolve to English in all downstream code.

---

### Phase 0 — Garmin Client Foundation (4–5 ngày)

**Goal:** Module Garmin chạy ổn định, sync 1 lần/ngày, fallback khi API lỗi.

| ID | Task | File | Effort |
|---|---|---|---|
| P0.1 | `GarminClient` class — auth, token persistence, `resume_login()` | `app/agents/coach/garmin_client.py` | 3h |
| P0.2 | `garmin_daily_metrics` table schema + index | `app/core/database.py` (init_db migration) | 1h |
| P0.3 | `fetch_and_store_daily_metrics(user_id, date)` function | `garmin_client.py` | 2h |
| P0.4 | Scheduler job `task_garmin_sync` — 5:45 AM daily | `app/services/scheduler.py` | 1h |
| P0.5 | 3-day fallback: return last known values if Garmin unavailable | `app/core/database.py` (query helper) | 1h |
| P0.6 | Env vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD` | `config.example.json`, `.env.example` | 0.5h |
| P0.7 | Circuit breaker: skip sync after 3 failures, notify user | `garmin_client.py` | 1.5h |
| P0.8 | Async MFA flow: Telegram prompt → wait for code → resume | `garmin_client.py` + `agent.py` | 2h |
| P0.9 | `weekly_plans` staging table (pending/accepted/rejected) | `app/core/database.py` | 1h |
| P0.10 | `training_plans` schema extension (ALTER TABLE, 14 new columns) | `app/core/database.py` | 1.5h |
| P0.11 | `run_activities.rpe_score` column migration | `app/core/database.py` | 0.5h |
| P0.12 | Tests: mock garminconnect (5 scenarios), DB write, fallback, circuit breaker | `tests/test_garmin_client.py` | 3h |

**DoD:** `task_garmin_sync` chạy lúc 5:45am, ghi 1 row vào `garmin_daily_metrics`, không crash khi Garmin API timeout. Circuit breaker ngăn repeated failures.

---

### Phase 1 — Athlete State Machine (2–3 ngày)

**Goal:** Hệ thống biết trạng thái VĐV, tạm dừng plan khi ốm/chấn thương.

| ID | Task | File | Effort |
|---|---|---|---|
| P1.1 | `athlete_state` table + `get_athlete_state()` / `set_athlete_state()` helpers | `app/core/database.py` | 1h |
| P1.2 | `/sick` command (+ `/om` alias) → set state=sick | `app/agents/coach/agent.py` | 1h |
| P1.3 | `/recover` command (+ `/khoe` alias) → set state=healthy | `app/agents/coach/agent.py` | 0.5h |
| P1.4 | Auto-transition: taper state when countdown ≤ 21 days | `app/services/scheduler.py` (daily check) | 1h |
| P1.5 | Guard in plan generation: sick/injured → skip + Telegram notification | `flows/weekly_plan_generation.py` | 0.5h |
| P1.6 | State + readiness exposed in `build_agent_context()` output | `app/agents/coach/utils.py` | 1h |
| P1.7 | Tests: state transitions, guard logic, append-only audit trail | `tests/test_athlete_state.py` | 1.5h |

**DoD:** `/sick` → state=sick → plan generation skipped next Sunday → Telegram thông báo. `/recover` → state=healthy → plan resumes.

---

### Phase 2 — AI Weekly Plan Generation (6–8 ngày)

**Goal:** AI sinh giáo án 7 ngày có structured output, user approve/reject qua Telegram.

| ID | Task | File | Effort |
|---|---|---|---|
| P2.1 | `WeeklyPlanResult` + `WorkoutDay` Pydantic schemas | `app/agents/coach/schemas.py` (NEW) | 1h |
| P2.2 | `build_weekly_plan_prompt()` — assembles all 4 input groups + 3.1–3.5 constraints | `app/agents/coach/prompts.py` | 4h |
| P2.3 | `generate_weekly_plan(user_id, config)` flow + retry on schema error | `app/agents/coach/flows/weekly_plan_generation.py` (NEW) | 4h |
| P2.4 | `task_weekly_plan_generation` Sunday cron (20:30) + dedup guard | `app/services/scheduler.py` | 1.5h |
| P2.5 | Telegram preview: compact format (< 3500 chars) + inline buttons (✅/❌) | `app/agents/coach/flows/weekly_plan_generation.py` | 2h |
| P2.6 | `/accept` command (`/chap` alias) → validate pending plan exists → write 7 rows to `training_plans` | `app/agents/coach/agent.py` | 1.5h |
| P2.7 | `/reject <reason>` command (`/bochap <lý do>` alias) → store reason → trigger regeneration | `app/agents/coach/agent.py` | 1h |
| P2.8 | Inline button `plan:accept` / `plan:reject` via `callback_query` handler | `app/routers/webhooks.py` | 2h |
| P2.9 | `race_target_time_min` new config field | `config.example.json` | 0.5h |
| P2.10 | Smoke test import assertions | `tests/test_smoke.py` | 0.5h |
| P2.11 | Tests: prompt builder, schema validation, acwr constraint, sick guard, char limit, /accept flow | `tests/test_flow_weekly_plan.py` (NEW) | 5h |

**Telegram UX flow:** See Section 7.6.

**DoD:** Sunday cron → Gemini generates valid `WeeklyPlanResult` JSON → compact Telegram preview → `/accept` → 7 rows in `training_plans` (all WorkoutDay columns populated).

---

### Phase 3 — Adaptive Feedback Loop (4–5 ngày)

**Goal:** Vòng lặp RPE + auto-reschedule để giáo án tự thích ứng.

| ID | Task | File | Effort |
|---|---|---|---|
| P3.1 | `send_inline_keyboard_menu()` helper (Telegram inline keyboard) | `app/core/notification.py` | 1.5h |
| P3.2 | `handle_telegram_callback()` dispatcher + `answerCallbackQuery` | `app/routers/webhooks.py` | 2h |
| P3.3 | RPE callback route: `rpe:<activity_id>:<score>` → save to `run_activities.rpe_score` | `app/routers/webhooks.py` | 1h |
| P3.4 | After run analysis: send RPE inline keyboard (10 buttons, embedded in analysis message) | `app/agents/coach/flows/run_analysis.py` | 1.5h |
| P3.5 | RPE overtraining alert: RPE > 8 on Easy run → warning | `app/agents/coach/flows/run_analysis.py` | 0.5h |
| P3.6 | Morning briefing: include Garmin readiness score + readiness-based workout adaptation suggestion | `app/agents/coach/flows/morning_briefing.py` | 1.5h |
| P3.7 | Auto-reschedule: 23:00 daily check — incomplete hard session + readiness < 30 → defer logic | `app/services/scheduler.py` | 2.5h |
| P3.8 | Nutrition alert: 20:00 evening check — if tomorrow is LongRun > 15km → notify | `app/services/scheduler.py` | 1.5h |
| P3.9 | `build_agent_context()` includes Garmin readiness + HRV + sleep data | `app/agents/coach/utils.py` | 1.5h |
| P3.10 | Tests | `tests/test_rpe_flow.py`, `tests/test_reschedule.py` | 4h |

**Reschedule defer logic (explicitly defined):**
- If today's plan is Hard AND not completed AND readiness < 30 → move to next available day that has no plan AND is not a rest day in the upcoming week
- If today's plan is Easy AND not completed → skip (don't reschedule Easy runs)
- If no available day to reschedule → reduce weekly target by -5% and notify user
- Never reschedule past the race date

**DoD:** After every run analysis, Telegram sends RPE keyboard. User bấm → lưu DB. RPE > 8 on Easy → overtraining warning. Morning briefing shows readiness + adapts workout suggestion.

---

### Phase 3.5 — Daily Suggestion Fallback (2 ngày)

**Goal:** Khi chưa có giáo án active, morning briefing vẫn đưa ra gợi ý bài tập dựa trên rule — không cần LLM, không cần user confirm.

| ID | Task | File | Effort |
|---|---|---|---|
| P3.5.1 | `compute_daily_suggestion()` pure function (10-rule priority chain) | `app/agents/coach/daily_suggestion.py` (NEW) | 3h |
| P3.5.2 | `_has_active_plan_this_week()` helper | `app/agents/coach/flows/morning_briefing.py` | 0.5h |
| P3.5.3 | Integrate daily suggestion into morning briefing (no-plan branch) | `app/agents/coach/flows/morning_briefing.py` | 1h |
| P3.5.4 | "Not configured" guard: show `/setup` prompt if race_date missing | `app/agents/coach/flows/morning_briefing.py` | 0.5h |
| P3.5.5 | Smoke test import assertions for `compute_daily_suggestion` | `tests/test_smoke.py` | 0.25h |
| P3.5.6 | Tests: all 10 rule conditions, ACWR edge cases, `None` readiness fallback, state=sick, athlete_state guard | `tests/test_daily_suggestion.py` (NEW) | 3h |

**DoD:** Morning briefing with no active plan shows rule-based suggestion in Vietnamese. suggestion does NOT write to DB. State=sick → always shows Rest. ACWR > 1.4 → always shows Rest regardless of readiness.

---

### Phase 4 — Gear Tracker (1–2 ngày)

**Goal:** Alert khi giày vượt threshold mileage.

| ID | Task | File | Effort |
|---|---|---|---|
| P4.1 | `fetch_gear_stats(user_id)` — get shoe list + mileage from Garmin | `app/agents/coach/garmin_client.py` | 1h |
| P4.2 | Weekly Monday check: if any shoe > 550km → warn; > 650km → urgent | `app/services/scheduler.py` | 1h |
| P4.3 | Telegram alert: "Asics GT-2000 đã chạy 612km, sắp cần thay!" | `app/core/notification.py` (uses existing send_telegram_msg) | 0.5h |
| P4.4 | Tests | `tests/test_gear_tracker.py` | 1h |

**DoD:** Monday 7am check → Telegram cảnh báo nếu giày > threshold.

---

## 11. New Config Fields

```json
{
  "race_target_time_min": 105,
  "garmin": {
    "enabled": false,
    "sync_time": "05:45",
    "token_file": "data/garmin_tokens.json",
    "readiness_easy_threshold": 50,
    "readiness_no_quality_threshold": 40,
    "readiness_amplify_threshold": 80,
    "gear_warn_km": 550,
    "gear_critical_km": 650,
    "circuit_breaker_failures": 3,
    "mfa_timeout_min": 10
  },
  "weekly_plan": {
    "generation_day": "Sunday",
    "generation_time": "20:30",
    "auto_accept": false,
    "require_confirmation": true,
    "preview_expire_hours": 48,
    "max_quality_sessions_per_week": 2
  },
  "setup": {
    "session_timeout_hours": 24,
    "require_setup_before_plan": true
  },
  "daily_suggestion": {
    "enabled": true,
    "show_when_no_plan": true,
    "acwr_critical_threshold": 1.4,
    "acwr_caution_threshold": 1.3,
    "readiness_rest_threshold": 40,
    "readiness_excellent_threshold": 80
  }
}
```

---

## 12. New Files Summary

| File | Type | Purpose |
|---|---|---|
| `app/agents/coach/setup_validators.py` | NEW | validate_distance, validate_date, validate_time, validate_kmweek, validate_days, validate_rest_days |
| `app/agents/coach/setup_flow.py` | NEW | Setup FSM: start_setup, advance_setup, finalize_setup, is_setup_in_progress, cleanup_stale |
| `app/agents/coach/daily_suggestion.py` | NEW | compute_daily_suggestion() — pure rule-based function, no LLM |
| `app/agents/coach/garmin_client.py` | NEW | GarminClient: auth, token, daily sync, circuit breaker, async MFA |
| `app/agents/coach/schemas.py` | NEW | WeeklyPlanResult, WorkoutDay Pydantic models |
| `app/agents/coach/flows/weekly_plan_generation.py` | NEW | Generate + preview weekly plan, handle /accept / /reject |
| `tests/test_setup_flow.py` | NEW | Setup wizard: 6 steps, validators, re-setup, timeout, alias normalization |
| `tests/test_daily_suggestion.py` | NEW | All 10 rule conditions, ACWR edge cases, None readiness fallback |
| `tests/test_garmin_client.py` | NEW | Garmin auth, sync, fallback, circuit breaker (5 scenarios) |
| `tests/test_athlete_state.py` | NEW | State machine transitions, append-only audit trail |
| `tests/test_flow_weekly_plan.py` | NEW | Plan generation: inputs, schema validation, /accept flow, char limit |
| `tests/test_rpe_flow.py` | NEW | RPE collection, callback handling, overtraining alert |
| `tests/test_reschedule.py` | NEW | Auto-reschedule defer logic, edge cases |
| `tests/test_gear_tracker.py` | NEW | Gear fetch, mileage threshold |

---

## 13. Dependencies

```
garminconnect>=0.2.19    # Add to requirements.txt
```

No new infrastructure (SQLite, ChromaDB, Telegram — all existing).

---

## 14. Implementation Checklist (Track Here)

### Phase -1 — Setup / Onboarding
- [ ] P-1.1 `setup_sessions` table schema + index
- [ ] P-1.2 All 6 validators in `setup_validators.py`
- [ ] P-1.3 `start_setup()`, `advance_setup()`, `finalize_setup()`, `is_setup_in_progress()` in `setup_flow.py`
- [ ] P-1.4 `/setup` command routing + setup intercept in `agent.py`
- [ ] P-1.5 `COMMAND_ALIASES` dict + `resolve_command()` in `agent.py`
- [ ] P-1.6 Re-setup guard + plan invalidation
- [ ] P-1.7 `save_config()` with append-log pattern in `config.py`
- [ ] P-1.8 Stale session cleanup + scheduler job at 3:00 AM
- [ ] P-1.9 "Not configured" guard in morning briefing
- [ ] P-1.10 Smoke test assertions for new symbols
- [ ] P-1.11 Tests: `tests/test_setup_flow.py`

### Phase 0 — Garmin Client Foundation
- [ ] P0.1 `GarminClient` class + token persistence
- [ ] P0.2 `garmin_daily_metrics` table + index
- [ ] P0.3 `fetch_and_store_daily_metrics()` function
- [ ] P0.4 `task_garmin_sync` 5:45 AM cron job
- [ ] P0.5 3-day fallback for stale Garmin data
- [ ] P0.6 `GARMIN_EMAIL`, `GARMIN_PASSWORD` env vars
- [ ] P0.7 Circuit breaker: skip after 3 failures, notify user
- [ ] P0.8 Async MFA flow: Telegram prompt → code → resume
- [ ] P0.9 `weekly_plans` staging table
- [ ] P0.10 `training_plans` schema extension (14 new columns)
- [ ] P0.11 `run_activities.rpe_score` column migration
- [ ] P0.12 Tests: `tests/test_garmin_client.py` (5 mock scenarios)

### Phase 1 — Athlete State Machine
- [ ] P1.1 `athlete_state` table + `get_athlete_state()` / `set_athlete_state()`
- [ ] P1.2 `/sick` command (+ `/om` alias)
- [ ] P1.3 `/recover` command (+ `/khoe` alias)
- [ ] P1.4 Auto-taper state transition (≤21 days)
- [ ] P1.5 Plan generation guard for sick/injured
- [ ] P1.6 State + readiness in `build_agent_context()`
- [ ] P1.7 Tests: `tests/test_athlete_state.py`

### Phase 2 — AI Weekly Plan Generation
- [ ] P2.1 `WeeklyPlanResult` + `WorkoutDay` schemas in `schemas.py`
- [ ] P2.2 `build_weekly_plan_prompt()` (all 4 input groups + Section 3 constraints)
- [ ] P2.3 `generate_weekly_plan()` flow + schema retry
- [ ] P2.4 `task_weekly_plan_generation` Sunday 20:30 + dedup guard
- [ ] P2.5 Telegram compact preview (< 3500 chars) + inline buttons
- [ ] P2.6 `/accept` + `/chap` → validate pending → write 7 rows to `training_plans`
- [ ] P2.7 `/reject <reason>` + `/bochap <lý do>` → store + regenerate
- [ ] P2.8 `callback_query` handler for `plan:accept` / `plan:reject` buttons
- [ ] P2.9 `race_target_time_min` config field
- [ ] P2.10 Smoke test import assertions
- [ ] P2.11 Tests: `tests/test_flow_weekly_plan.py`

### Phase 3 — Adaptive Feedback Loop
- [ ] P3.1 `send_inline_keyboard_menu()` in `notification.py`
- [ ] P3.2 `handle_telegram_callback()` dispatcher + `answerCallbackQuery`
- [ ] P3.3 RPE callback route: store score in `run_activities`
- [ ] P3.4 RPE keyboard sent with run analysis message
- [ ] P3.5 RPE overtraining alert (RPE > 8 on Easy run)
- [ ] P3.6 Morning briefing: readiness score + workout adaptation
- [ ] P3.7 Auto-reschedule 23:00 daily job (defer logic defined)
- [ ] P3.8 Nutrition alert evening check (LongRun > 15km tomorrow)
- [ ] P3.9 `build_agent_context()` + Garmin readiness/HRV/sleep
- [ ] P3.10 Tests: `tests/test_rpe_flow.py`, `tests/test_reschedule.py`

### Phase 3.5 — Daily Suggestion Fallback
- [ ] P3.5.1 `compute_daily_suggestion()` pure function in `daily_suggestion.py`
- [ ] P3.5.2 `_has_active_plan_this_week()` helper in `morning_briefing.py`
- [ ] P3.5.3 Daily suggestion integrated into morning briefing (no-plan branch)
- [ ] P3.5.4 "Not configured" guard (show `/setup` if no race_date)
- [ ] P3.5.5 Smoke test assertions for `compute_daily_suggestion`
- [ ] P3.5.6 Tests: `tests/test_daily_suggestion.py` (all 10 rules + edge cases)

### Phase 4 — Gear Tracker
- [ ] P4.1 `fetch_gear_stats()` in GarminClient
- [ ] P4.2 Weekly Monday gear check cron
- [ ] P4.3 Telegram shoe mileage alert
- [ ] P4.4 Tests: `tests/test_gear_tracker.py`

---

## 15. Estimated Effort

| Phase | Effort | Calendar | Risk |
|---|---|---|---|
| Phase -1 (Setup/Onboarding) | ~15h | 3–4 days | Low-Medium (wizard UX polish) |
| Phase 0 (Garmin Foundation) | ~18h | 4–5 days | High (Garmin auth quirks, MFA) |
| Phase 1 (Athlete State) | ~6.5h | 2–3 days | Low |
| Phase 2 (Weekly Plan Gen) | ~24h | 6–8 days | High (prompt tuning, schema retry) |
| Phase 3 (Feedback Loop) | ~18h | 4–5 days | Medium (Telegram callbacks) |
| Phase 3.5 (Daily Suggestion) | ~8h | 2 days | Low (pure function, deterministic) |
| Phase 4 (Gear Tracker) | ~3.5h | 1–2 days | Low |
| **Total** | **~93h** | **~22–29 days** | |

**Recommended execution order:** Phase -1 → P0 → P1 → P2 → P3 → P3.5 → P4

> Phase 3.5 can run in parallel with Phase 3 if capacity allows — it has no dependency on P3.

---

## 16. Risks & Mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| Garmin blocks unofficial API (429) | Medium | Circuit breaker: skip 24h after 3 failures; persistent token; daily-only sync |
| Garmin changes auth flow | Low-Medium | Pin `garminconnect` version; async MFA flow; manual fallback via `/garmin_reauth` |
| AI generates invalid `WeeklyPlanResult` JSON | Low | Strict Pydantic schema + `response_schema` in Gemini; retry once with stricter prompt |
| User doesn't confirm plan (no /accept) | Medium | 48h expiry → status='expired'; morning briefing reminds; can `/plan` to regenerate |
| User abandons setup wizard mid-flow | Medium | 24h timeout → mark abandoned; `/setup` again starts fresh; no partial config written |
| Re-setup while plan is active corrupts data | Low-Medium | Confirmation gate; invalidate pending/accepted plans atomically before writing new config |
| RPE callback_query not handled (silent button failure) | High (if unaddressed) | Add callback_query route in Phase 3.2; test with mock Telegram payload |
| Scheduler race conditions (sync + briefing at same time) | Medium | Garmin sync at 5:45 AM (15min before briefing); job dedup guards |
| training_plans migration on production DB | Low | ALTER TABLE ADD COLUMN with NULL default is safe on existing rows |
| Prompt length exceeds Gemini token limit | Medium | Cap prompt at 2000 tokens (computed context block); exclude raw stream CSV |
| Daily suggestion not setup-guarded | Low | "Not configured" check before computing suggestion; fall back to `/setup` prompt |

---

## 17. Definition of Done

### Phase -1
- [ ] `/setup` starts wizard; completes in ≤6 messages
- [ ] All 6 validators reject invalid input with clear Vietnamese error and retry prompt
- [ ] Vietnamese aliases (`/chap`, `/om`, `/khoe`, etc.) resolve to English command in all code paths
- [ ] Re-setup with active plan → confirmation gate → plan invalidated

### Phase 0
- [ ] Garmin sync running daily at 5:45am, data visible in `garmin_daily_metrics`
- [ ] Circuit breaker prevents repeated API hammering after 3 failures

### Phase 1
- [ ] `/sick` pauses plan generation; `/recover` resumes it

### Phase 2
- [ ] Sunday 20:30 → Telegram compact plan preview (< 3500 chars) with inline buttons
- [ ] `/accept` → `training_plans` populated for full week (all 7 days, all WorkoutDay columns)
- [ ] `/reject <reason>` → AI regenerates with feedback incorporated
- [ ] Plan deduplication: no duplicate plans for same week

### Phase 3
- [ ] RPE keyboard appears after every run analysis (embedded in analysis message)
- [ ] RPE callback handled silently; confirmation toast shown; DB saved
- [ ] Morning briefing includes Garmin readiness score + workout adaptation
- [ ] Readiness > 80 → briefing suggests optional volume amplification
- [ ] Taper rules enforced: correct 75/50/25 volume; last hard session ≤ Week -2
- [ ] Max 2 quality sessions per week enforced by plan generation

### Phase 3.5
- [ ] Morning briefing with no active plan shows rule-based suggestion in Vietnamese
- [ ] Suggestion does NOT write to `training_plans`
- [ ] state=sick → always shows Rest regardless of readiness
- [ ] ACWR > 1.4 → always shows Rest regardless of readiness
- [ ] No race_date in config → shows `/setup` prompt instead

### All Phases
- [ ] All existing tests pass (0 regressions)
- [ ] New test modules: 80%+ coverage on new modules
- [ ] Smoke test assertions for new public symbols
- [ ] Pre-deploy check passes

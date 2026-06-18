# app/agents/coach/tools.py

import os
import pytz
import json
from datetime import datetime
from app.core.database import (
    get_training_loads,
    get_recent_runs_log,
    get_run_activity_raw,
    update_daily_plan,
    get_weekly_target,
    upsert_weekly_target,
    get_run_metrics_from_db,
    get_metric_trend_data,
    get_monthly_volume,
    get_yearly_volume,
)
from app.services.rag_memory import rag_db
from app.services.stream_storage import (
    load_activity_stream_from_file,
    get_stream_arrays,
)
from app.agents.coach.utils import calculate_acwr
from app.agents.coach.metrics_engine import build_run_metrics_block

from app.core.logging_conf import get_module_logger

logger = get_module_logger("coach")


def update_todays_plan(user_id: str, workout_title: str, description: str) -> str:
    """
    [TOOL] Modify, update, or cancel TODAY's training plan (current date only).
    Use when the athlete requests a same-day schedule change or the AI proactively adjusts
    based on readiness, fatigue, or weather.
    To cancel: pass workout_title='Cancel' and description=''.
    ONLY for today. For any future date use set_workout_plan() instead.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now_str = datetime.now(tz).strftime("%Y-%m-%d")
    logger.info(f"[TOOL-USE] 🤖 AI automatically changed today's plan: {workout_title}")
    return update_daily_plan(
        str(user_id), now_str, workout_title, description, status="Pending"
    )


def check_training_status(user_id: str) -> str:
    """
    [TOOL] Check the current injury risk index (ACWR) and training load (TRIMP).
    Use when the athlete asks about form, readiness, injury risk, fatigue level,
    or whether they are safe to train hard today.
    Returns: ACWR ratio, status classification (Caution <0.8 | Optimal 0.8-1.3 | Overreaching >1.3),
    acute load (7-day TRIMP), chronic load (28-day TRIMP).
    """
    logger.info(f"[TOOL-USE] 🤖 AI checking fitness status for User {user_id}")
    loads = get_training_loads(user_id)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
    return f"ACWR: {acwr_data['acwr']} ({acwr_data['status']}) | Acute Load: {loads.get('acute_load_7d')} | Chronic: {loads.get('chronic_load_28d')}"


def get_recent_workouts(user_id: str) -> str:
    """
    [TOOL] Retrieve a summary list of the athlete's 10 most recent running workouts.
    Use for questions like "what have I been doing lately?", "show my recent runs", or
    to find an activity_id before calling get_run_full_details() or get_run_computed_metrics().
    Returns per-run: date, distance, pace, HR (no stream data, no splits).
    For full splits/lap data of one specific run, use get_run_full_details(activity_id).
    """
    logger.info(f"[TOOL-USE] 🤖 AI fetching 10 recent workouts for {user_id}")
    return get_recent_runs_log(user_id, limit=10)


def get_run_full_details(activity_id: str) -> str:
    """
    [TOOL] Get full stored data for a run (splits, laps, device, stream summary).
    Use when the athlete asks for details of a specific run by ID or when you need
    splits/laps/device info that was saved at sync or webhook time.
    """
    logger.info(
        f"[TOOL-USE] 🤖 AI fetching full run details for activity {activity_id}"
    )
    raw = get_run_activity_raw(str(activity_id))
    if not raw:
        return f"Không tìm thấy dữ liệu đầy đủ cho bài chạy {activity_id}. Chỉ có thể có bản tóm tắt trong danh sách bài chạy gần đây."
    meta = raw.get("full_meta") or {}
    lines = [
        f"Bài chạy: {raw.get('activity_name', 'N/A')}",
        f"Lấy lúc: {raw.get('fetched_at', 'N/A')}",
    ]
    if meta.get("start_date_local"):
        lines.append(f"Thời gian: {meta['start_date_local']}")
    if meta.get("distance"):
        lines.append(f"Quãng đường: {meta['distance']/1000:.2f} km")
    if meta.get("moving_time"):
        lines.append(f"Thời gian chạy: {meta['moving_time']//60} phút")
    if meta.get("average_heartrate"):
        lines.append(f"HR TB: {meta['average_heartrate']} bpm")
    if meta.get("device_name"):
        lines.append(f"Thiết bị: {meta['device_name']}")
    if meta.get("splits"):
        parts = []
        for s in (meta["splits"] or [])[:10]:
            pace = s.get("pace")
            pace_str = f"{1000/(pace*60):.1f} min/km" if pace and pace > 0 else "N/A"
            parts.append(f"{s.get('km', '?')}km @ {pace_str}")
        lines.append("Splits: " + "; ".join(parts))
    if meta.get("laps"):
        parts = []
        for lap in (meta["laps"] or [])[:5]:
            dist_m = lap.get("distance") or 0
            parts.append(f"{lap.get('lap_name', 'Lap')} {dist_m/1000:.2f}km")
        if parts:
            lines.append("Laps: " + "; ".join(parts))
    stream_path = raw.get("stream_file_path") or ""
    if stream_path:
        lines.append(
            f"(Đã lưu stream raw: data/{stream_path} — có thể load lại để phân tích chi tiết hoặc re-analyze theo đoạn.)"
        )
    return "\n".join(lines)


def search_long_term_memory(query: str) -> str:
    """
    [TOOL] Search long-term memory (ChromaDB vector store) for past coaching advice,
    recurring injury patterns, historical run summaries, or previous AI recommendations
    from weeks or months ago that are no longer in the recent workouts list.
    Use when the athlete references something from the distant past: "last time I had knee pain",
    "what did you recommend about hills before?", "my marathon training last year".
    query: a natural-language phrase describing what to look for (e.g. "knee pain ITB 2024").
    Returns up to 3 relevant memory snippets. If nothing found, use get_recent_workouts() instead.
    """
    logger.info(f"[TOOL-USE] 🤖 AI querying RAG memory: '{query}'")
    try:
        results = rag_db.recall(query=query, domain="coach", n_results=3)
        if not results or not results.get("documents") or not results["documents"][0]:
            return "Không tìm thấy ký ức nào liên quan."
        docs = results["documents"][0]
        return "\n".join([f"- Ký ức: {doc}" for doc in docs])
    except Exception as e:
        return f"Lỗi truy xuất ký ức: {e}"


def get_total_run_stats(user_id: str) -> str:
    """
    [TOOL] Retrieve total running distance statistics (last 4 weeks, YTD, all-time).
    WARNING: reads from a static cache file (athlete_stats.json) that may be hours/days stale.
    Use only for rough all-time or YTD totals when recency does not matter.
    For current month/week volume use get_volume_summary() or get_volume_for_week() instead.
    """
    logger.info(f"[TOOL-USE] 🤖 AI checking total mileage for {user_id}")
    try:
        with open("data/athlete_stats.json", "r") as f:
            stats = json.load(f)
        return f"Volume 4 tuần: {stats.get('recent_run_totals', 0):.1f}km | YTD: {stats.get('ytd_run_totals', 0):.1f}km"
    except Exception:
        return "Chưa có dữ liệu thống kê tổng km."


def set_workout_plan(
    user_id: str, target_date: str, workout_title: str, description: str
) -> str:
    """
    [TOOL] Create or overwrite the training plan for a SPECIFIC FUTURE DATE (YYYY-MM-DD).
    Use when the athlete asks to schedule or change a workout for tomorrow, next week, or any
    named future date. Overwrites any existing plan for that date.
    ONLY for dates other than today. For today's plan use update_todays_plan() instead.
    target_date: ISO date string, e.g. '2026-04-15'. Must not be today's date.
    """
    logger.info(f"[TOOL-USE] 🤖 AI setting Plan for {target_date}: {workout_title}")
    return update_daily_plan(
        str(user_id), target_date, workout_title, description, status="Pending"
    )


def get_run_stream_csv(activity_id: str) -> str:
    """
    [TOOL] Load the raw second-by-second GPS stream for a specific run as a text table.
    Use ONLY when the athlete needs raw time-series analysis that aggregated metrics cannot
    answer — e.g. "show me exactly when HR spiked", "graph pace vs HR over time".
    Returns columns: t(s) | vel(m/s) | HR(bpm) | cad(spm), capped at ~200 rows (sampled).
    For pre-computed aggregated metrics (pace, aerobic decoupling, TSS, etc.) use
    get_run_computed_metrics() instead — it is cheaper and faster.
    Requires the run to have a stored stream file; not all activities have one.
    """
    logger.info(f"[TOOL-USE] 🤖 AI loading raw stream for activity {activity_id}")
    raw = get_run_activity_raw(str(activity_id))
    if not raw or not raw.get("stream_file_path"):
        return f"Không tìm thấy file stream cho bài chạy {activity_id}. Chỉ có metrics đã tính sẵn."
    payload = load_activity_stream_from_file(raw["stream_file_path"])
    if not payload:
        return f"Không thể đọc file stream cho bài chạy {activity_id}."
    arrays = get_stream_arrays(payload)
    if not arrays:
        return f"File stream rỗng cho bài chạy {activity_id}."
    time_arr = arrays.get("time", [])
    vel_arr = arrays.get("velocity_smooth", [])
    hr_arr = arrays.get("heartrate", [])
    cad_arr = arrays.get("cadence", [])
    n = len(time_arr) or len(vel_arr)
    if n == 0:
        return "Stream không có dữ liệu."
    lines = ["t(s)\tvel(m/s)\tHR\tcad(spm)"]
    step = max(1, n // 200)  # cap at ~200 rows to avoid token waste
    for i in range(0, n, step):
        t = time_arr[i] if i < len(time_arr) else ""
        v = f"{vel_arr[i]:.2f}" if i < len(vel_arr) else ""
        h = int(hr_arr[i]) if i < len(hr_arr) else ""
        c = int(cad_arr[i] * 2) if i < len(cad_arr) else ""
        lines.append(f"{t}\t{v}\t{h}\t{c}")
    return "\n".join(lines)


def get_run_computed_metrics(activity_id: str, user_id: str) -> str:
    """
    [TOOL] Return pre-computed running science metrics for a specific past activity.
    Use to REVIEW or COMPARE a run's quality: aerobic decoupling, cadence, TSS, pace zones,
    HR zones, efficiency factor, stride length, interval/tempo detection, elevation gain.
    Prefer this over get_run_stream_csv() — it is aggregated, token-efficient, and always available.
    get_run_stream_csv() gives raw time-series; this gives the derived science summary.
    Returns 'not found' if the activity has not been processed by the metrics engine yet.
    """
    logger.info(f"[TOOL-USE] 🤖 AI loading computed metrics for activity {activity_id}")
    metrics = get_run_metrics_from_db(str(activity_id), str(user_id))
    if not metrics:
        return f"Chưa có metrics tính sẵn cho bài chạy {activity_id}."
    block = build_run_metrics_block(metrics, {})
    return block or f"Metrics cho bài chạy {activity_id} đều là None."


def get_metric_trend(user_id: str, metric_name: str, days: int = 28) -> str:
    """
    [TOOL] Get trend data for a single running metric over the last N days.
    Use for questions like "is my cadence improving?", "how has aerobic decoupling changed?",
    "show pace variability over the last month".
    metric_name must be one of the stored column names (exact match required):
      avg_pace_min_km            — average pace in min/km
      aerobic_decoupling_pct     — cardiac drift (aerobic efficiency)
      avg_cadence_spm            — steps per minute (stride rate)
      avg_efficiency_factor      — pace-to-HR ratio (aerobic fitness proxy)
      training_stress_score      — TSS (overall session load)
      grade_adjusted_pace_min_km — elevation-normalized pace
      cardiac_drift_pct          — HR drift from first to second half
      positive_split_ratio       — second-half slowdown ratio (>1 = positive split)
      pace_variability_cv        — coefficient of variation of pace (consistency)
    days: default 28, pass a different int for shorter/longer windows.
    """
    logger.info(
        f"[TOOL-USE] 🤖 AI fetching metric trend: {metric_name} ({days}d) for {user_id}"
    )
    rows = get_metric_trend_data(str(user_id), metric_name, days)
    if not rows:
        return f"Không có dữ liệu cho metric '{metric_name}' trong {days} ngày qua."
    lines = [f"Xu hướng '{metric_name}' ({days} ngày):"]
    for row in rows:
        lines.append(f"  {row.get('date', '?')}: {row.get('value', 'N/A')}")
    return "\n".join(lines)


def get_volume_for_week(user_id: str, year: int, week_number: int) -> str:
    """
    [TOOL] Get total running volume for a SPECIFIC ISO week (week_number 1-53) of a given year.
    Use when the athlete names a specific week: "week 15", "week 3 of 2026", "last week's total".
    week_number: ISO week number 1-53 (Monday-based).
    Returns: total km, number of runs, total time in minutes for that week.
    For monthly or yearly summaries use get_volume_summary() instead.
    For trend across many weeks use get_metric_trend().
    """
    logger.info(f"[TOOL-USE] 🤖 AI fetching volume for {year} W{week_number}")
    from datetime import date, timedelta

    # ISO week: find the Monday of the given week
    jan4 = date(year, 1, 4)
    week_start = (
        jan4 + timedelta(weeks=week_number - 1) - timedelta(days=jan4.weekday())
    )
    week_end = week_start + timedelta(days=7)
    data = get_monthly_volume(str(user_id), week_start.year, week_start.month)
    # Filter to exact week range from raw data
    rows = data.get("runs", [])
    week_runs = [
        r
        for r in rows
        if week_start.isoformat() <= r.get("date", "") < week_end.isoformat()
    ]
    if not week_runs:
        # Fallback: use monthly data as approximation
        return (
            f"Tuần {week_number}/{year} ({week_start} - {week_end - timedelta(days=1)}): "
            f"Không tìm thấy dữ liệu chi tiết. Tổng tháng {week_start.month}/{year}: "
            f"{data.get('total_distance_km', 0):.1f} km / {data.get('total_runs', 0)} bài."
        )
    total_km = sum(r.get("distance_km", 0) for r in week_runs)
    total_min = sum(r.get("moving_time_min", 0) for r in week_runs)
    return (
        f"Tuần {week_number}/{year} ({week_start} → {week_end - timedelta(days=1)}): "
        f"{total_km:.1f} km | {len(week_runs)} bài | {int(total_min)} phút"
    )


def get_volume_summary(user_id: str, period: str, year: int, month: int = 0) -> str:
    """
    [TOOL] Get running volume summary for a calendar month or full year.
    Use for questions like "how much did I run in April?", "what was my 2025 total?",
    "give me a monthly breakdown for this year".
    period: 'month' → returns one month's totals | 'year' → returns full year + monthly breakdown.
    month: 1-12, required when period='month' (ignored for 'year').
    Examples: period='month', year=2026, month=4 → April 2026 summary.
              period='year', year=2025 → full 2025 breakdown by month.
    For a single named week (e.g. "week 15") use get_volume_for_week() instead.
    """
    logger.info(
        f"[TOOL-USE] 🤖 AI fetching volume summary: {period} {year}/{month or ''} for {user_id}"
    )
    if period == "month" and month:
        data = get_monthly_volume(str(user_id), year, month)
        return (
            f"Tháng {month}/{year}: {data.get('total_distance_km', 0):.1f} km | "
            f"{data.get('total_runs', 0)} bài | {int(data.get('total_moving_time_min', 0))} phút"
        )
    data = get_yearly_volume(str(user_id), year)
    lines = [
        f"Năm {year}: {data.get('total_distance_km', 0):.1f} km | "
        f"{data.get('total_runs', 0)} bài | {int(data.get('total_moving_time_min', 0))} phút"
    ]
    breakdown = data.get("monthly_breakdown", {})
    for m_num in sorted(breakdown.keys()):
        v = breakdown[m_num]
        lines.append(
            f"  Tháng {m_num}: {v.get('distance_km', 0):.1f} km / {v.get('runs', 0)} bài"
        )
    return "\n".join(lines)


def save_bulk_workout_plan(user_id: str, plan_json: str) -> str:
    """
    [TOOL] Save a multi-day training schedule in one call.
    Use when the athlete shares a structured training plan (multiple days/weeks) and asks to
    save it to the database. Call ONCE with the full plan rather than set_workout_plan per day.
    plan_json: JSON array of objects, each with keys:
      date (YYYY-MM-DD, required)
      workout_title (str, required)
      description (str, optional)
      workout_type (str, optional) — e.g. "Easy", "Tempo", "LongRun", "Rest", "Interval", "Recovery"
      target_distance_km (float, optional) — target distance in km
      target_pace_range (str, optional) — e.g. "4:10-4:20/km"
      target_hr_zone (int, optional) — 1-5
      rpe_target (int, optional) — 1-10
    Example: '[{"date":"2026-05-01","workout_title":"Easy Run","description":"6km Zone 2","workout_type":"Easy","target_distance_km":6.0}]'
    Returns a summary of how many days were saved and any errors.
    """
    logger.info(f"[TOOL-USE] 🤖 AI saving bulk plan for user {user_id}")
    try:
        entries = json.loads(plan_json)
    except Exception as e:
        return f"❌ plan_json không hợp lệ (JSON parse error): {e}"

    if not isinstance(entries, list) or not entries:
        return "❌ plan_json phải là mảng JSON không rỗng."

    saved, errors = 0, []
    for entry in entries:
        date = entry.get("date", "")
        title = entry.get("workout_title", "")
        desc = entry.get("description", "")
        if not date or not title:
            errors.append(f"Bỏ qua entry thiếu date/title: {entry}")
            continue
        result = update_daily_plan(
            str(user_id),
            date,
            title,
            desc,
            status="Pending",
            workout_type=entry.get("workout_type"),
            target_distance_km=entry.get("target_distance_km"),
            target_pace_range=entry.get("target_pace_range"),
            target_hr_zone=entry.get("target_hr_zone"),
            rpe_target=entry.get("rpe_target"),
        )
        if result.startswith("✅"):
            saved += 1
        else:
            errors.append(result)

    summary = f"✅ Đã lưu {saved}/{len(entries)} ngày vào lịch tập."
    if errors:
        summary += f"\n⚠️ {len(errors)} lỗi: " + "; ".join(errors[:3])
    return summary


def set_actual_weekly_target(
    user_id: str, week_start_date: str, actual_target_km: float, reasoning: str
) -> str:
    """
    [TOOL] Confirm and save the AI-recommended weekly volume target (in km) for a given week.
    Call AFTER analyzing all 4 inputs: historical volume, safe volume ceiling, safe TRIMP ceiling,
    and the standard training plan. The result is the conservative minimum of those 4.
    week_start_date: YYYY-MM-DD, MUST be a Monday (validation enforced — wrong day will be rejected).
    actual_target_km: the final recommended volume in km (float).
    reasoning: short explanation of why this target was chosen (stored for athlete review).
    Returns success confirmation or an error string if the date is not a Monday or DB write fails.
    """
    logger.info(
        f"[TOOL-USE] 🤖 AI setting weekly target {week_start_date} for {user_id}: {actual_target_km}km. Reason: {reasoning}"
    )

    # Retrieve existing standard_target (if any) to prevent overwriting with 0
    current_data = get_weekly_target(user_id, week_start_date)
    standard_target = (
        current_data["standard_target_km"]
        if current_data and current_data["standard_target_km"]
        else actual_target_km
    )

    success = upsert_weekly_target(
        user_id, week_start_date, standard_target, actual_target_km, reasoning
    )
    if success:
        return f"Thành công: Đã chốt target tuần {week_start_date} là {actual_target_km}km."
    return "Thất bại: Lỗi hệ thống khi lưu target."

# app/agents/coach/tools.py

import os
import logging
import pytz
import json
from datetime import datetime
from app.core.database import (
    get_training_loads, get_recent_runs_log,
    get_run_activity_raw,
    update_daily_plan
)
from app.services.rag_memory import rag_db

logger = logging.getLogger("AI_COACH")

def update_todays_plan(user_id: str, workout_title: str, description: str) -> str:
    """
    [TOOL] Modify, update, or cancel today's training plan.
    Used when the athlete requests a schedule change or AI proactively adjusts based on readiness.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now_str = datetime.now(tz).strftime('%Y-%m-%d')
    logger.info(f"[TOOL-USE] 🤖 AI automatically changed today's plan: {workout_title}")
    return update_daily_plan(str(user_id), now_str, workout_title, description, status="Pending")

def check_training_status(user_id: str) -> str:
    """
    [TOOL] Check the current injury risk index (ACWR) and training load (TRIMP).
    """
    from app.agents.coach.utils import calculate_acwr # Import locally to avoid circular import
    logger.info(f"[TOOL-USE] 🤖 AI checking fitness status for User {user_id}")
    loads = get_training_loads(user_id)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    return f"ACWR: {acwr_data['acwr']} ({acwr_data['status']}) | Acute Load: {loads.get('acute_load_7d')} | Chronic: {loads.get('chronic_load_28d')}"

def get_recent_workouts(user_id: str) -> str:
    """
    [TOOL] Retrieve the list of the athlete's 10 most recent running workouts.
    """
    logger.info(f"[TOOL-USE] 🤖 AI fetching 10 recent workouts for {user_id}")
    return get_recent_runs_log(user_id, limit=10)


def get_run_full_details(activity_id: str) -> str:
    """
    [TOOL] Get full stored data for a run (splits, laps, device, stream summary).
    Use when the athlete asks for details of a specific run by ID or when you need
    splits/laps/device info that was saved at sync or webhook time.
    """
    logger.info(f"[TOOL-USE] 🤖 AI fetching full run details for activity {activity_id}")
    raw = get_run_activity_raw(str(activity_id))
    if not raw:
        return f"Không tìm thấy dữ liệu đầy đủ cho bài chạy {activity_id}. Chỉ có thể có bản tóm tắt trong danh sách bài chạy gần đây."
    meta = raw.get("full_meta") or {}
    lines = [f"Bài chạy: {raw.get('activity_name', 'N/A')}", f"Lấy lúc: {raw.get('fetched_at', 'N/A')}"]
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
        lines.append(f"(Đã lưu stream raw: data/{stream_path} — có thể load lại để phân tích chi tiết hoặc re-analyze theo đoạn.)")
    return "\n".join(lines)

def search_long_term_memory(query: str) -> str:
    """
    [TOOL] Search long-term memory (ChromaDB) for past runs or previous advice.
    """
    logger.info(f"[TOOL-USE] 🤖 AI querying RAG memory: '{query}'")
    try:
        results = rag_db.recall(query=query, domain="coach", n_results=3)
        if not results or not results.get('documents') or not results['documents'][0]:
            return "Không tìm thấy ký ức nào liên quan."
        docs = results['documents'][0]
        return "\n".join([f"- Ký ức: {doc}" for doc in docs])
    except Exception as e:
        return f"Lỗi truy xuất ký ức: {e}"

def get_total_run_stats(user_id: str) -> str:
    """
    [TOOL] Retrieve total running distance statistics (last 4 weeks, YTD, all-time).
    """
    logger.info(f"[TOOL-USE] 🤖 AI checking total mileage for {user_id}")
    try:
        with open("data/athlete_stats.json", "r") as f:
            stats = json.load(f)
        return f"Volume 4 tuần: {stats.get('recent_run_totals', 0):.1f}km | YTD: {stats.get('ytd_run_totals', 0):.1f}km"
    except Exception as e:
        return "Chưa có dữ liệu thống kê tổng km."

def set_workout_plan(user_id: str, target_date: str, workout_title: str, description: str) -> str:
    """
    [TOOL] Update or insert a new training plan for a specific future date (YYYY-MM-DD).
    """
    logger.info(f"[TOOL-USE] 🤖 AI setting Plan for {target_date}: {workout_title}")
    return update_daily_plan(str(user_id), target_date, workout_title, description, status="Pending")

def set_actual_weekly_target(user_id: str, week_start_date: str, actual_target_km: float, reasoning: str) -> str:
    """
    [TOOL] Confirm the actual target weekly volume (in km).
    Use this tool after analyzing 4 metrics (History, Safe Volume, Safe TRIMP, Standard Plan) to protect athlete from injury.
    - week_start_date: YYYY-MM-DD format (Must be Monday of the target week).
    """
    from app.core.database import get_weekly_target, upsert_weekly_target
    logger.info(f"[TOOL-USE] 🤖 AI setting weekly target {week_start_date} for {user_id}: {actual_target_km}km. Reason: {reasoning}")
    
    # Retrieve existing standard_target (if any) to prevent overwriting with 0
    current_data = get_weekly_target(user_id, week_start_date)
    standard_target = current_data["standard_target_km"] if current_data and current_data["standard_target_km"] else actual_target_km
    
    success = upsert_weekly_target(user_id, week_start_date, standard_target, actual_target_km, reasoning)
    if success:
        return f"Thành công: Đã chốt target tuần {week_start_date} là {actual_target_km}km."
    return "Thất bại: Lỗi hệ thống khi lưu target."
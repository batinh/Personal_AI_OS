# app/agents/coach/tools.py

import os
import logging
import pytz
import json
from datetime import datetime
from app.core.database import (
    get_training_loads, get_recent_runs_log, 
    update_daily_plan
)
from app.services.rag_memory import rag_db

logger = logging.getLogger("AI_COACH")

def update_todays_plan(user_id: str, workout_title: str, description: str) -> str:
    """
    [TOOL] Thay đổi, cập nhật hoặc hủy bỏ giáo án của ngày hôm nay.
    Dùng khi VĐV yêu cầu đổi lịch hoặc AI chủ động điều chỉnh theo thể trạng.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now_str = datetime.now(tz).strftime('%Y-%m-%d')
    logger.info(f"[TOOL-USE] 🤖 AI tự động thay đổi lịch hôm nay: {workout_title}")
    return update_daily_plan(str(user_id), now_str, workout_title, description, status="Pending")

def check_training_status(user_id: str) -> str:
    """
    [TOOL] Kiểm tra chỉ số chấn thương (ACWR) và tải trọng tập luyện (TRIMP) hiện tại.
    """
    from app.agents.coach.utils import calculate_acwr # Import tại chỗ để tránh circular import
    logger.info(f"[TOOL-USE] 🤖 AI kiểm tra thể lực cho User {user_id}")
    loads = get_training_loads(user_id)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    return f"ACWR: {acwr_data['acwr']} ({acwr_data['status']}) | Acute Load: {loads.get('acute_load_7d')} | Chronic: {loads.get('chronic_load_28d')}"

def get_recent_workouts(user_id: str) -> str:
    """
    [TOOL] Lấy danh sách 5 bài tập chạy bộ gần nhất của vận động viên.
    """
    logger.info(f"[TOOL-USE] 🤖 AI truy xuất 5 bài tập gần nhất cho {user_id}")
    return get_recent_runs_log(user_id, limit=5)

def search_long_term_memory(query: str) -> str:
    """
    [TOOL] Tìm kiếm trí nhớ dài hạn (ChromaDB) về các bài chạy cũ hoặc lời khuyên quá khứ.
    """
    logger.info(f"[TOOL-USE] 🤖 AI truy xuất trí nhớ RAG: '{query}'")
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
    [TOOL] Lấy thống kê tổng quãng đường chạy (4 tuần qua, năm nay, toàn thời gian).
    """
    logger.info(f"[TOOL-USE] 🤖 AI kiểm tra tổng mileage cho {user_id}")
    try:
        with open("data/athlete_stats.json", "r") as f:
            stats = json.load(f)
        return f"Volume 4 tuần: {stats.get('recent_run_totals', 0):.1f}km | YTD: {stats.get('ytd_run_totals', 0):.1f}km"
    except Exception as e:
        return "Chưa có dữ liệu thống kê tổng km."

def set_workout_plan(user_id: str, target_date: str, workout_title: str, description: str) -> str:
    """
    [TOOL] Cập nhật hoặc thêm mới giáo án tập luyện cho một ngày cụ thể (YYYY-MM-DD).
    """
    logger.info(f"[TOOL-USE] 🤖 AI thiết lập Plan ngày {target_date}: {workout_title}")
    return update_daily_plan(str(user_id), target_date, workout_title, description, status="Pending")

def set_actual_weekly_target(user_id: str, week_start_date: str, actual_target_km: float, reasoning: str) -> str:
    """
    [TOOL] Chốt khối lượng (km) mục tiêu thực tế của tuần.
    Sử dụng tool này sau khi phân tích 4 chỉ số (Lịch sử, Safe Volume, Safe TRIMP, Standard Plan) để bảo vệ VĐV khỏi chấn thương.
    - week_start_date: Định dạng YYYY-MM-DD (Ngày Thứ 2 của tuần đó).
    """
    from app.core.database import get_weekly_target, upsert_weekly_target
    logger.info(f"[TOOL-USE] 🤖 AI chốt target tuần {week_start_date} cho {user_id}: {actual_target_km}km. Lý do: {reasoning}")
    
    # Lấy standard_target hiện tại (nếu có) để không bị ghi đè thành 0
    current_data = get_weekly_target(user_id, week_start_date)
    standard_target = current_data["standard_target_km"] if current_data and current_data["standard_target_km"] else actual_target_km
    
    success = upsert_weekly_target(user_id, week_start_date, standard_target, actual_target_km, reasoning)
    if success:
        return f"Thành công: Đã chốt target tuần {week_start_date} là {actual_target_km}km."
    return "Thất bại: Lỗi hệ thống khi lưu target."
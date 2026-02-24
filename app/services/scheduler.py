from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import os
import json
import logging
import uuid
from datetime import datetime, timedelta # Thêm timedelta để tính ngày

# Nhập thư viện AI và các hàm tính toán
from google import genai
from google.genai import types

from app.core.config import load_config
from app.core.notification import send_telegram_msg
from app.core.database import (
    get_training_loads, 
    get_runs_in_last_days, 
    load_history_for_gemini,
    get_plan_for_date,
    update_daily_plan,
    get_weekly_volume  # [FIX 1] Import hàm tính volume tuần
)
from app.agents.coach.utils import (
    calculate_acwr, 
    calculate_training_phase, 
    debug_log_prompt, 
    gather_weekly_decision_inputs, 
    get_formatted_weekly_context
)
from app.services.rag_memory import rag_db
from app.agents.coach.harvest import harvest_data
from app.services.backup import perform_backup
from app.agents.coach.tools import update_todays_plan, set_actual_weekly_target
from app.agents.coach.prompts import STANDUP_PROMPT_TEMPLATE

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
scheduler = AsyncIOScheduler()
client = genai.Client()

# ==========================================
# ☀️ LUỒNG BÁO CÁO SÁNG (THE MORNING STANDUP)
# ==========================================
async def task_morning_briefing():
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id: 
        logger.warning("[SCHEDULER] Không tìm thấy TELEGRAM_CHAT_ID để gửi báo cáo.")
        return

    config = load_config()
    now = datetime.now(TZ_VN)
    user_id_str = str(chat_id)
    
    # 1. Thu thập dữ liệu
    # Lấy dữ liệu tải trọng (ACWR)
    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
    
    # [FIX 2] Định nghĩa actual_volume để tránh NameError
    actual_volume = get_weekly_volume(user_id_str, now)
    
    # Lấy thông tin Phase tập luyện
    phase_info = calculate_training_phase(config.get("race_date", ""))
    
    # Lấy giáo án hôm nay (Sử dụng Multi-tenant signature)
    today_plan = get_plan_for_date(user_id_str, now.strftime('%Y-%m-%d'))
    plan_context = f"{today_plan['workout_title']}: {today_plan['description']}" if today_plan else "Chạy tự do."

    # Lấy context tuần (Đã refactor DRY)
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # 2. [REFACTOR] Format Prompt từ Template
    prompt = STANDUP_PROMPT_TEMPLATE.format(
        chat_id=user_id_str,
        now_display_str=now.strftime('%A, %d/%m/%Y'),
        phase=phase_info["phase"],
        microcycle=phase_info["microcycle"],
        acwr=acwr_data['acwr'],
        acwr_status=acwr_data['status'],
        actual_volume=actual_volume,  # Đã có giá trị sau khi FIX 2
        recent_7_days_log=get_runs_in_last_days(user_id_str, days=7),
        chat_context="...", # Có thể tích hợp thêm lịch sử chat ngắn tại đây
        plan_context=plan_context,
        weekly_decision_context=weekly_decision_context
    )

    debug_log_prompt("DEBUG STANDUP PROMPT", prompt)

    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=config.get("system_instruction", ""),
                tools=[update_todays_plan, set_actual_weekly_target]
            )
        )
        response = chat_session.send_message(prompt)
        if response.text: 
            send_telegram_msg(chat_id, response.text)
    except Exception as e:
        logger.error(f"[SCHEDULER] Standup Error: {e}")

# ==========================================
# CÁC JOB KHÁC & QUẢN LÝ SCHEDULER (GIỮ NGUYÊN)
# ==========================================
async def task_auto_harvest():
    """Tự động đồng bộ Strava mỗi 6 tiếng"""
    logger.info("[SCHEDULER] Auto-harvesting...")
    harvest_data()

def setup_jobs():
    """Đọc cấu hình và thiết lập lịch chạy"""
    config = load_config()
    sched_cfg = config.get("scheduler", {})
    
    brief_time = sched_cfg.get("briefing_time", "06:00")
    try: bh, bm = map(int, brief_time.split(':'))
    except: bh, bm = 6, 0
    
    backup_time = sched_cfg.get("backup_time", "02:00")
    try: bkh, bkm = map(int, backup_time.split(':'))
    except: bkh, bkm = 2, 0
    
    harv_hours = sched_cfg.get("harvest_hours", "0,6,12,18")
    harv_min = str(sched_cfg.get("harvest_minute", "15"))

    scheduler.add_job(task_morning_briefing, CronTrigger(hour=bh, minute=bm, timezone=TZ_VN), id='briefing', replace_existing=True)
    scheduler.add_job(perform_backup, CronTrigger(hour=bkh, minute=bkm, timezone=TZ_VN), id='backup', replace_existing=True)
    scheduler.add_job(task_auto_harvest, CronTrigger(hour=harv_hours, minute=harv_min, timezone=TZ_VN), id='harvest', replace_existing=True)
    
    logger.info(f"[SCHEDULER] Đã nạp lịch: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m)")

def start_scheduler():
    """Khởi động bộ lập lịch lần đầu tiên"""
    setup_jobs()
    scheduler.start()

def reload_scheduler():
    """Gọi từ Admin UI để cập nhật lịch ngay lập tức"""
    setup_jobs()
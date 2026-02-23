from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import os
import json
import logging
import uuid
from datetime import datetime

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
    update_daily_plan
)
from app.agents.coach.utils import calculate_acwr, calculate_training_phase
from app.services.rag_memory import rag_db
from app.agents.coach.harvest import harvest_data
from app.services.backup import perform_backup
from app.agents.coach.agent import update_todays_plan

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone('Asia/Ho_Chi_Minh')
scheduler = AsyncIOScheduler()
client = genai.Client()

# ==========================================
# ☀️ LUỒNG BÁO CÁO SÁNG (THE MORNING STANDUP)
# ==========================================
async def task_morning_briefing():
    """[HOLISTIC STANDUP] Đọc Database, Đánh giá ACWR, Sửa Plan nếu cần và Báo cáo"""
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id: return

    logger.info("[SCHEDULER] Đang kích hoạt Intelligent Morning Standup...")
    config = load_config()
    
    now = datetime.now(TZ_VN)
    now_date_str = now.strftime('%Y-%m-%d')
    now_display_str = now.strftime('%A, %d/%m/%Y')

    # 1. THU THẬP "BỆNH ÁN" TỪ SQLITE VÀ RAG
    loads = get_training_loads(chat_id)
    acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
    recent_7_days_log = get_runs_in_last_days(chat_id, days=7)
    
    raw_chat = load_history_for_gemini(chat_id, limit=6)
    chat_context = "\n".join([f"{msg['role'].upper()}: {msg['parts'][0]}" for msg in raw_chat]) if raw_chat else "Không có tâm sự gì gần đây."
    
    # Đọc giáo án SQLite mặc định của ngày hôm nay
    today_plan = get_plan_for_date(now_date_str)
    if today_plan:
        plan_context = f"Tên bài: {today_plan['title']} | Chi tiết: {today_plan['description']}"
    else:
        plan_context = "Hôm nay chạy tự do, chưa có giáo án."
        
    system_instruction = config.get("system_instruction", "You are Coach Dyno.")
    
    # [CẬP NHẬT KIẾN TRÚC] Tính toán Phase Deterministic bằng Python
    race_date_str = config.get("race_date", "")
    current_phase = calculate_training_phase(race_date_str)
    
    # 2. KIẾN TẠO PROMPT (TRUYỀN DỮ LIỆU ĐA CHIỀU)
    prompt = f"""
    [DAILY STANDUP - {now_display_str}]
    
    1. THỂ TRẠNG & TIẾN ĐỘ:
    - Giai đoạn tập luyện hiện tại: BẮT BUỘC ÁP DỤNG '{current_phase}'
    - ACWR: {acwr_data['acwr']} ({acwr_data['status']})
    
    2. LỊCH SỬ THỰC THI (7 ngày):
    {recent_7_days_log}
    
    3. TÂM LÝ (Chat gần nhất):
    {chat_context}
    
    4. GIÁO ÁN MẶC ĐỊNH HÔM NAY:
    {plan_context}
    
    [NHIỆM VỤ CỦA BẠN]
    - Hãy rà soát xem [GIÁO ÁN MẶC ĐỊNH] có an toàn với [THỂ TRẠNG] và [TÂM LÝ] hiện tại không.
    - NẾU ACWR > 1.5 HOẶC VĐV đang đau mỏi (dựa vào Chat): BẮT BUỘC SỬ DỤNG TOOL `update_todays_plan` để ghi đè giáo án hôm nay thành "Nghỉ ngơi" hoặc "Phục hồi nhẹ".
    - NẾU mọi thứ ổn định: Không cần dùng Tool, chỉ cần gửi lời chúc năng lượng và nhắc nhở thực hiện giáo án.
    - Viết một tin nhắn gửi VĐV cực kỳ ngắn gọn, sắc bén như một HLV thực thụ.
    """
    
    # [CẬP NHẬT KIẾN TRÚC] Log Debug Observability
    if os.getenv("DEBUG_PROMPTS", "false").lower() == "true":
        logger.info(f"\n========== [DEBUG MORNING STANDUP PROMPT] ==========\n{prompt}\n====================================================")
        
    try:
        model_name = config.get("model_name", "models/gemini-2.5-flash")
        
        # Gọi AI và cấp quyền dùng Tool sửa lịch
        chat_session = client.chats.create(
            model=model_name,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
                tools=[update_todays_plan]
            )
        )
        
        response = chat_session.send_message(prompt)
        
        if response.text:
            send_telegram_msg(chat_id, response.text)
            
            # Khắc sâu lời dặn dò vào ChromaDB
            rag_db.memorize(
                doc_id=f"standup_{uuid.uuid4().hex[:8]}", 
                content=f"Sáng {now_date_str}, Coach báo cáo: {response.text}", 
                domain="coach", 
                extra_meta={"user_id": chat_id, "type": "daily_standup"}
            )
            logger.info("[SCHEDULER] Standup hoàn tất xuất sắc.")
            
    except Exception as e:
        logger.error(f"[SCHEDULER] Standup Error: {e}")
        send_telegram_msg(chat_id, f"☀️ Coach nghẽn mạng API. ACWR sáng nay của anh là {acwr_data['acwr']}. Hãy lắng nghe cơ thể nhé!")

# ==========================================
# CÁC JOB KHÁC & QUẢN LÝ SCHEDULER
# ==========================================
async def task_auto_harvest():
    """Tự động đồng bộ Strava mỗi 6 tiếng"""
    logger.info("[SCHEDULER] Auto-harvesting...")
    harvest_data()

def setup_jobs():
    """Đọc cấu hình và thiết lập lịch chạy (có thể gọi lại để reload)"""
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
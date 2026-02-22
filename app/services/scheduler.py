from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import os
import json
import logging
from datetime import datetime
from app.core.notification import send_telegram_msg
from app.agents.coach.harvest import harvest_data
from app.services.backup import perform_backup
from app.core.database import get_training_loads, get_runs_in_last_days, load_history_for_gemini
from app.services.rag_memory import rag_db
import uuid

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone('Asia/Ho_Chi_Minh')
scheduler = AsyncIOScheduler()

async def task_morning_briefing():
    """[HOLISTIC STANDUP] Báo cáo đa chiều: Thể chất, Kỷ luật, Tâm lý và Mục tiêu"""
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id: return

    config = load_config()
    now = datetime.now(TZ_VN)
    now_str = now.strftime('%A, %d/%m/%Y')

    # 1. CHIỀU THỂ CHẤT (ACWR & TRIMP)
    loads = get_training_loads(chat_id)
    acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
    
    # 2. CHIỀU THỰC THI (Đúng 7 ngày thực tế)
    recent_7_days_log = get_runs_in_last_days(chat_id, days=7)
    
    # 3. CHIỀU TÂM LÝ (Lịch sử chat gần nhất trên Telegram)
    # Lấy 6 tin nhắn gần nhất để AI biết mood của VĐV hôm qua
    raw_chat = load_history_for_gemini(chat_id, limit=6)
    chat_context = "\n".join([f"{msg['role'].upper()}: {msg['parts'][0]}" for msg in raw_chat]) if raw_chat else "Không có cuộc trò chuyện nào gần đây."

    # 4. CHIỀU MỤC TIÊU & NGOẠI CẢNH (Race Day & Plan cũ)
    race_date_str = config.get("race_date", "")
    current_goal = config.get("current_goal", "Duy trì")
    countdown_text = "Không có giải đấu."
    if race_date_str:
        try:
            r_date = datetime.strptime(race_date_str, "%Y-%m-%d").replace(tzinfo=TZ_VN)
            days_left = (r_date - now).days
            countdown_text = f"Còn {days_left} ngày đến giải."
        except: pass

    past_plan = "Chưa có kế hoạch nào."
    try:
        recall_results = rag_db.recall(query="Kế hoạch tập luyện Coach Dyno giao", domain="coach", n_results=1)
        if recall_results and recall_results.get('documents') and recall_results['documents'][0]:
            past_plan = recall_results['documents'][0][0]
    except: pass

    # THE HOLISTIC PROMPT
    prompt = f"""
    Bạn là Coach Dyno, HLV AI chuyên nghiệp. Hãy viết "Daily Standup" gửi VĐV.
    Bạn phải đánh giá TOÀN DIỆN dựa trên 4 khía cạnh sau:

    [1. NGOẠI CẢNH & MỤC TIÊU]
    - Hôm nay: {now_str}. {countdown_text}
    - Mục tiêu: {current_goal}.
    - Plan bạn đã giao gần nhất: {past_plan}

    [2. THỰC THI (Trong 7 ngày qua)]
    {recent_7_days_log}

    [3. THỂ CHẤT HIỆN TẠI]
    - ACWR: {acwr_data['acwr']} ({acwr_data['status']})
    
    [4. TÂM LÝ & TÌNH TRẠNG (Lịch sử trò chuyện gần nhất)]
    {chat_context}

    [YÊU CẦU LẬP KẾ HOẠCH HÔM NAY]
    1. Tổng hợp: VĐV có đang bám sát mục tiêu và plan cũ không? Nếu đoạn chat gần nhất VĐV báo đau/mệt, PHẢI phản hồi lại sự kiện đó.
    2. Chỉ định hôm nay: Dựa vào sự giao thoa giữa Thể chất (ACWR) và Tâm lý (Chat), hãy đưa ra bài tập hôm nay (VD: Chạy bài gì, pace bao nhiêu, hay phải nghỉ). 
    3. Trình bày cực kỳ ngắn gọn, sắc bén như một HLV thực thụ (dưới 150 chữ).
    """

    try:
        model_name = config.get("model_name", "models/gemini-2.5-flash")
        response = client.models.generate_content(
            model=model_name, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.7)
        )
        
        if response.text:
            new_plan_text = response.text
            send_telegram_msg(chat_id, new_plan_text)
            
            # Memorize kế hoạch mới
            rag_db.memorize(
                doc_id=f"plan_{uuid.uuid4().hex[:8]}", 
                content=f"Ngày {now_str}, Plan: {new_plan_text}", 
                domain="coach", extra_meta={"user_id": chat_id, "type": "daily_plan"}
            )
    except Exception as e:
        logger.error(f"[SCHEDULER] Lỗi: {e}")

async def task_auto_harvest():
    """Tự động đồng bộ Strava mỗi 6 tiếng"""
    logger.info("[SCHEDULER] Auto-harvesting...")
    harvest_data()

# ... (Giữ nguyên các import và các hàm task_morning_briefing, task_auto_harvest, perform_backup) ...

from app.core.config import load_config

def setup_jobs():
    """Đọc cấu hình và thiết lập lịch chạy (có thể gọi lại để reload)"""
    config = load_config()
    sched_cfg = config.get("scheduler", {})
    
    # 1. Lịch Briefing (Mặc định 06:00)
    brief_time = sched_cfg.get("briefing_time", "06:00")
    try: bh, bm = map(int, brief_time.split(':'))
    except: bh, bm = 6, 0
    
    # 2. Lịch Backup (Mặc định 02:00)
    backup_time = sched_cfg.get("backup_time", "02:00")
    try: bkh, bkm = map(int, backup_time.split(':'))
    except: bkh, bkm = 2, 0
    
    # 3. Lịch Harvest (Mặc định chạy các khung giờ 0,6,12,18 phút 15)
    harv_hours = sched_cfg.get("harvest_hours", "0,6,12,18")
    harv_min = str(sched_cfg.get("harvest_minute", "15"))

    # replace_existing=True giúp đè lịch mới lên lịch cũ nếu cùng ID
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
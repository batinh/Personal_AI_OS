from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import os
from app.core.user_context import get_primary_user_id
import json
import logging
import uuid
from datetime import datetime, timedelta

from app.core.config import load_config
from app.core.notification import send_telegram_msg
from app.core.database import (
    get_training_loads, 
    get_runs_in_last_days, 
    load_history_for_gemini,
    get_plan_for_date,
    update_daily_plan,
    get_weekly_volume
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

# [REFACTOR] Import builder
from app.agents.coach.prompts import build_system_instruction, get_shared_context_block, build_standup_prompt
from app.agents.coach.agent import generate_weekly_reflection, generate_morning_briefing, extract_implicit_memory
from app.services.weather import get_today_weather

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
scheduler = AsyncIOScheduler()

# ==========================================
# ☀️ THE MORNING STANDUP FLOW
# ==========================================
async def task_morning_briefing():
    """
    [ORCHESTRATOR] Cron trigger for the morning briefing.
    Fetches external data (Weather) and delegates reasoning to the Coach Agent.
    """
    chat_id = get_primary_user_id()
    if not chat_id: 
        logger.warning("[SCHEDULER] TELEGRAM_CHAT_ID not found for briefing.")
        return

    logger.info("[SCHEDULER] Triggering 06:00 AM briefing...")
    config = load_config()
    
    # 1. Fetch raw weather data (Data Injection Pattern)
    weather_info = get_today_weather()
    
    # 2. Handover to the Brain
    generate_morning_briefing(config, weather_data=weather_info)

# ==========================================
# OTHER JOBS & SCHEDULER MANAGEMENT
# ==========================================
async def task_auto_harvest():
    """Auto-sync Strava every specified interval."""
    logger.info("[SCHEDULER] Auto-harvesting...")
    harvest_data()

def setup_jobs():
    """Read config and setup scheduled jobs."""
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
    # [NEW] Weekly Reflection Job (Sunday at 20:00)
    scheduler.add_job(
        task_weekly_reflection, 
        CronTrigger(day_of_week='sun', hour=20, minute=0, timezone=TZ_VN), 
        id='weekly_reflection', 
        replace_existing=True
    )
    logger.info(f"[SCHEDULER] Loaded jobs: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m)")

async def task_weekly_reflection():
    """
    [ORCHESTRATOR] Cron trigger for Weekly Reflection & Background Memory Extraction.
    """
    logger.info("[SCHEDULER] Triggering Weekly Reflection...")
    cfg = load_config()
    chat_id = get_primary_user_id()
    
    if chat_id:
        user_id_str = str(chat_id)
        
        # BƯỚC 1: LƯU BỘ NHỚ (AI Lần 1 - Trích xuất)
        logger.info("[SCHEDULER] Step 1: Triggering implicit memory extraction...")
        extract_implicit_memory(user_id_str)
        
    # BƯỚC 2: DÙNG BỘ NHỚ (AI Lần 2 - Viết báo cáo & Plan)
    logger.info("[SCHEDULER] Step 2: Generating and sending Weekly Reflection...")
    generate_weekly_reflection(cfg)

def start_scheduler():
    """Start the scheduler for the first time."""
    setup_jobs()
    scheduler.start()

def reload_scheduler():
    """Called from Admin UI to instantly reload jobs."""
    setup_jobs()
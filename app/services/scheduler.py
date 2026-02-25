from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import os
import json
import logging
import uuid
from datetime import datetime, timedelta

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

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
scheduler = AsyncIOScheduler()
client = genai.Client()

# ==========================================
# ☀️ THE MORNING STANDUP FLOW
# ==========================================
async def task_morning_briefing():
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id: 
        logger.warning("[SCHEDULER] TELEGRAM_CHAT_ID not found for briefing.")
        return

    config = load_config()
    now = datetime.now(TZ_VN)
    user_id_str = str(chat_id)
    
    # 1. Gather data
    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
    actual_volume = get_weekly_volume(user_id_str, now)
    
    race_date_str = config.get("race_date", "")
    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race." if race_date_str else "Duy trì thể lực."
    
    today_plan = get_plan_for_date(user_id_str, now.strftime('%Y-%m-%d'))
    plan_context = f"{today_plan['workout_title']}: {today_plan['description']}" if today_plan else "Chạy tự do."
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # Fetch 5 recent messages for AI context
    raw_history = load_history_for_gemini(user_id_str, limit=5)
    chat_context = "Không có tương tác trò chuyện nào gần đây."
    if raw_history:
        chat_context_lines = []
        # Reverse to read in chronological order
        for msg in reversed(raw_history): 
            sender = "User" if msg["role"] == "user" else "Coach Dyno"
            text = msg["parts"][0][:150] + "..." if len(msg["parts"][0]) > 150 else msg["parts"][0]
            chat_context_lines.append(f"{sender}: {text}")
        chat_context = "\n".join(chat_context_lines)

    # 2. BUILD PROMPT (Lego Architecture)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
    )
    
    shared_context = get_shared_context_block(
        now.strftime('%A, %d/%m/%Y'), user_id_str, phase_text, countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})", 
        actual_volume, weekly_decision_context
    )

    prompt = build_standup_prompt(
        shared_context, 
        get_runs_in_last_days(user_id_str, days=7), 
        plan_context, 
        chat_context # Inject short-term memory into Standup
    )

    debug_log_prompt("DEBUG STANDUP PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                tools=[update_todays_plan, set_actual_weekly_target]
            )
        )
        response = chat_session.send_message(prompt)
        if response.text: 
            send_telegram_msg(chat_id, response.text)
    except Exception as e:
        logger.error(f"[SCHEDULER] Standup Error: {e}")

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
    
    logger.info(f"[SCHEDULER] Loaded jobs: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m)")

def start_scheduler():
    """Start the scheduler for the first time."""
    setup_jobs()
    scheduler.start()

def reload_scheduler():
    """Called from Admin UI to instantly reload jobs."""
    setup_jobs()
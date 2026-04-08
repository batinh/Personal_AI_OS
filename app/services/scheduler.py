from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import os
import logging

from app.core.user_context import get_primary_user_id
from app.core.config import load_config
from app.agents.coach.harvest import harvest_data
from app.services.backup import perform_backup
from app.agents.coach.agent import generate_weekly_reflection, generate_morning_briefing, extract_implicit_memory
from app.services.weather import get_today_weather
from app.agents.news.agent import generate_news_briefing
from app.agents.news.alert_engine import run_news_watch

logger = logging.getLogger("AI_COACH")
TZ_VN = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
# BackgroundScheduler runs jobs in a thread pool — all task functions must be regular def.
scheduler = BackgroundScheduler()


# ==========================================
# ☀️ MORNING BRIEFING
# ==========================================
def task_morning_briefing():
    """
    [ORCHESTRATOR] Cron trigger for the morning briefing.
    Fetches weather then delegates reasoning to the sync Coach Agent.
    Runs in BackgroundScheduler thread pool.
    """
    chat_id = get_primary_user_id()
    if not chat_id:
        logger.warning("[SCHEDULER] TELEGRAM_CHAT_ID not found for briefing.")
        return

    logger.info("[SCHEDULER] Triggering morning briefing...")
    config = load_config()
    weather_info = get_today_weather()
    generate_morning_briefing(config, weather_info)


# ==========================================
# 🔄 AUTO HARVEST
# ==========================================
def task_auto_harvest():
    """Auto-sync Strava every specified interval. Runs in BackgroundScheduler thread pool."""
    logger.info("[SCHEDULER] Auto-harvesting...")
    harvest_data()


# ==========================================
# 🪞 WEEKLY REFLECTION
# ==========================================
def task_weekly_reflection():
    """
    [ORCHESTRATOR] Cron trigger for Weekly Reflection & Background Memory Extraction.
    Runs in BackgroundScheduler thread pool.
    """
    logger.info("[SCHEDULER] Triggering Weekly Reflection...")
    cfg = load_config()
    chat_id = get_primary_user_id()

    if chat_id:
        logger.info("[SCHEDULER] Step 1: Triggering implicit memory extraction...")
        extract_implicit_memory(str(chat_id))

    logger.info("[SCHEDULER] Step 2: Generating and sending Weekly Reflection...")
    generate_weekly_reflection(cfg)


# ==========================================
# 📰 NEWS BRIEFINGS
# ==========================================
def task_morning_news():
    """Morning news briefing via RSS + Gemini. Must be regular def (BackgroundScheduler thread pool)."""
    logger.info("[SCHEDULER] Triggering morning news briefing...")
    config = load_config()
    generate_news_briefing(config, session="morning")


def task_afternoon_news():
    """Afternoon news update via RSS + Gemini. Must be regular def (BackgroundScheduler thread pool)."""
    logger.info("[SCHEDULER] Triggering afternoon news briefing...")
    config = load_config()
    generate_news_briefing(config, session="afternoon")


# ==========================================
# 📍 CONTINUOUS NEWS WATCH (ALERT ENGINE)
# ==========================================
def task_news_watch():
    """Continuous news watch — scores articles and sends breaking alerts. Must be regular def."""
    logger.info("[SCHEDULER] Running news watch cycle...")
    config = load_config()
    run_news_watch(config)


# ==========================================
# ⚙️ SCHEDULER MANAGEMENT
# ==========================================
def setup_jobs():
    """Read config and set up scheduled cron jobs."""
    config = load_config()
    sched_cfg = config.get("scheduler", {})

    brief_time = sched_cfg.get("briefing_time", "06:00")
    try:
        bh, bm = map(int, brief_time.split(':'))
    except Exception:
        bh, bm = 6, 0

    backup_time = sched_cfg.get("backup_time", "02:00")
    try:
        bkh, bkm = map(int, backup_time.split(':'))
    except Exception:
        bkh, bkm = 2, 0

    harv_hours = sched_cfg.get("harvest_hours", "0,6,12,18")
    harv_min = str(sched_cfg.get("harvest_minute", "15"))

    scheduler.add_job(task_morning_briefing, CronTrigger(hour=bh, minute=bm, timezone=TZ_VN), id='briefing', replace_existing=True)
    scheduler.add_job(perform_backup, CronTrigger(hour=bkh, minute=bkm, timezone=TZ_VN), id='backup', replace_existing=True)
    scheduler.add_job(task_auto_harvest, CronTrigger(hour=harv_hours, minute=harv_min, timezone=TZ_VN), id='harvest', replace_existing=True)
    scheduler.add_job(task_weekly_reflection, CronTrigger(day_of_week='sun', hour=20, minute=0, timezone=TZ_VN), id='weekly_reflection', replace_existing=True)

    # News Agent jobs (only if enabled in config)
    news_cfg = config.get("news_agent", {})
    if news_cfg.get("enabled", False):
        try:
            nh, nm = map(int, news_cfg.get("morning_time", "07:00").split(":"))
            ah, am = map(int, news_cfg.get("afternoon_time", "17:00").split(":"))
        except Exception:
            nh, nm = 7, 0
            ah, am = 17, 0

        # Add scheduled briefing jobs (morning + afternoon)
        scheduler.add_job(task_morning_news, CronTrigger(hour=nh, minute=nm, timezone=TZ_VN), id='news_morning', replace_existing=True)
        scheduler.add_job(task_afternoon_news, CronTrigger(hour=ah, minute=am, timezone=TZ_VN), id='news_afternoon', replace_existing=True)

        # Add continuous news watch job (interval-based for breaking alerts)
        watch_interval = int(news_cfg.get("watch_interval_minutes", 30))
        scheduler.add_job(task_news_watch, IntervalTrigger(minutes=watch_interval, timezone=TZ_VN), id='news_watch', replace_existing=True)

        logger.info(f"[SCHEDULER] News jobs loaded: Morning({nh}:{nm:02d}), Afternoon({ah}:{am:02d}), Watch(every {watch_interval}m)")

    logger.info(f"[SCHEDULER] Jobs loaded: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m), Reflection(Sun 20:00)")


def start_scheduler():
    """Start the scheduler for the first time."""
    setup_jobs()
    scheduler.start()


def reload_scheduler():
    """Called from Admin UI to instantly reload jobs with updated config."""
    setup_jobs()

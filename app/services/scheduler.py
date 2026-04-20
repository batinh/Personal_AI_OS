from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import os

from app.core.user_context import get_primary_user_id
from app.core.config import load_config
from app.core.database import get_training_loads
from app.core.notification import send_telegram_msg
from app.agents.coach.harvest import harvest_data
from app.agents.coach.utils import calculate_training_phase
from app.services.backup import perform_backup
from app.agents.coach.agent import generate_weekly_reflection, generate_morning_briefing, extract_implicit_memory
from app.services.weather import get_today_weather
from app.agents.news.agent import generate_news_briefing
from app.services.log_auditor import run_audit

from app.core.logging_conf import get_module_logger
logger = get_module_logger("scheduler")
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
    try:
        chat_id = get_primary_user_id()
        if not chat_id:
            logger.warning("[SCHEDULER] TELEGRAM_CHAT_ID not found for briefing.")
            return

        logger.info("[SCHEDULER] Triggering morning briefing...")
        config = load_config()
        weather_info = get_today_weather()
        generate_morning_briefing(config, weather_info)
    except Exception as e:
        logger.error("[SCHEDULER] task_morning_briefing failed: %s", e, exc_info=True)


# ==========================================
# 🔄 AUTO HARVEST
# ==========================================
def task_auto_harvest():
    """Auto-sync Strava every specified interval. Runs in BackgroundScheduler thread pool."""
    try:
        logger.info("[SCHEDULER] Auto-harvesting...")
        harvest_data()
    except Exception as e:
        logger.error("[SCHEDULER] task_auto_harvest failed: %s", e, exc_info=True)


# ==========================================
# 🪞 WEEKLY REFLECTION
# ==========================================
def task_weekly_reflection():
    """
    [ORCHESTRATOR] Cron trigger for Weekly Reflection & Background Memory Extraction.
    Runs in BackgroundScheduler thread pool.
    """
    try:
        logger.info("[SCHEDULER] Triggering Weekly Reflection...")
        cfg = load_config()
        chat_id = get_primary_user_id()

        if chat_id:
            logger.info("[SCHEDULER] Step 1: Triggering implicit memory extraction...")
            extract_implicit_memory(str(chat_id))

        logger.info("[SCHEDULER] Step 2: Generating and sending Weekly Reflection...")
        generate_weekly_reflection(cfg)
    except Exception as e:
        logger.error("[SCHEDULER] task_weekly_reflection failed: %s", e, exc_info=True)


# ==========================================
# 📰 NEWS BRIEFINGS
# ==========================================
def task_morning_news():
    """Morning news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        logger.info("[SCHEDULER] Triggering morning news briefing...")
        config = load_config()
        generate_news_briefing(config, session="morning")
    except Exception as e:
        logger.error("[SCHEDULER] task_morning_news failed: %s", e, exc_info=True)


def task_afternoon_news():
    """Afternoon news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        logger.info("[SCHEDULER] Triggering afternoon news briefing...")
        config = load_config()
        generate_news_briefing(config, session="afternoon")
    except Exception as e:
        logger.error("[SCHEDULER] task_afternoon_news failed: %s", e, exc_info=True)


def task_evening_news():
    """Evening news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        logger.info("[SCHEDULER] Triggering evening news briefing...")
        config = load_config()
        generate_news_briefing(config, session="evening")
    except Exception as e:
        logger.error("[SCHEDULER] task_evening_news failed: %s", e, exc_info=True)


# ==========================================
# 🏃 PROACTIVE COACHING ALERTS
# ==========================================
def task_proactive_coach_check():
    """
    Daily proactive coaching check. Sends alerts for high training load or
    upcoming race week taper reminders. Must be regular def (BackgroundScheduler thread pool).
    """
    try:
        chat_id = get_primary_user_id()
        if not chat_id:
            return

        config = load_config()
        loads = get_training_loads(str(chat_id))
        acwr = loads.get("acwr", 0.0)
        race_distance_km = float(config.get("race_distance_km", 21.1))
        phase_info = calculate_training_phase(config.get("race_date", ""), race_distance_km)
        weeks_left = phase_info.get("weeks_left", 99)

        alerts = []

        if acwr > 1.5:
            alerts.append(
                f"🚨 <b>CẢNH BÁO TẢI TRỌNG CAO</b>\n"
                f"ACWR hiện tại: <b>{acwr:.2f}</b> (ngưỡng nguy hiểm > 1.5)\n"
                f"Khuyến nghị: Giảm khối lượng ngay hôm nay. Bài Easy hoặc nghỉ hoàn toàn."
            )
        elif acwr > 1.3:
            alerts.append(
                f"⚠️ <b>Tải trọng tăng cao</b>\n"
                f"ACWR: <b>{acwr:.2f}</b> (ngưỡng thận trọng > 1.3)\n"
                f"Khuyến nghị: Không tăng thêm khối lượng tuần này."
            )

        if 1 <= weeks_left <= 3:
            taper_label = {1: "Race Week", 2: "Tuần −2", 3: "Tuần −3"}.get(weeks_left, "")
            taper_pct = {1: "25%", 2: "50%", 3: "75%"}.get(weeks_left, "")
            alerts.append(
                f"🏁 <b>Nhắc nhở Taper — {taper_label}</b>\n"
                f"Còn {weeks_left} tuần đến ngày đua. Giảm khối lượng xuống còn {taper_pct} so với peak."
            )

        for msg in alerts:
            send_telegram_msg(str(chat_id), msg)
            logger.info(f"[SCHEDULER] Proactive alert sent: ACWR={acwr:.2f}, weeks_left={weeks_left}")
    except Exception as e:
        logger.error("[SCHEDULER] task_proactive_coach_check failed: %s", e, exc_info=True)


# ==========================================
# 🔍 LOG AUDIT
# ==========================================
def task_log_audit():
    """Scan app.log for errors/warnings, persist findings to audit_entries table. Regular def (thread pool)."""
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping log audit.")
            return
        logger.info("[SCHEDULER] Running log audit...")
        count = run_audit(user_id)
        if count:
            logger.info(f"[SCHEDULER] Log audit complete: {count} new entries inserted.")
    except Exception as e:
        logger.error("[SCHEDULER] task_log_audit failed: %s", e, exc_info=True)


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
    except (ValueError, AttributeError):
        logger.warning("[SCHEDULER] Invalid briefing_time '%s'; defaulting to 06:00", brief_time)
        bh, bm = 6, 0

    backup_time = sched_cfg.get("backup_time", "02:00")
    try:
        bkh, bkm = map(int, backup_time.split(':'))
    except (ValueError, AttributeError):
        logger.warning("[SCHEDULER] Invalid backup_time '%s'; defaulting to 02:00", backup_time)
        bkh, bkm = 2, 0

    harv_hours = sched_cfg.get("harvest_hours", "0,6,12,18")
    harv_min = str(sched_cfg.get("harvest_minute", "15"))

    scheduler.add_job(task_morning_briefing, CronTrigger(hour=bh, minute=bm, timezone=TZ_VN), id='briefing', replace_existing=True)
    scheduler.add_job(perform_backup, CronTrigger(hour=bkh, minute=bkm, timezone=TZ_VN), id='backup', replace_existing=True)
    scheduler.add_job(task_auto_harvest, CronTrigger(hour=harv_hours, minute=harv_min, timezone=TZ_VN), id='harvest', replace_existing=True)
    scheduler.add_job(task_weekly_reflection, CronTrigger(day_of_week='sun', hour=20, minute=0, timezone=TZ_VN), id='weekly_reflection', replace_existing=True)
    scheduler.add_job(task_proactive_coach_check, CronTrigger(hour=12, minute=0, timezone=TZ_VN), id='proactive_check', replace_existing=True)
    scheduler.add_job(task_log_audit, IntervalTrigger(hours=6, timezone=TZ_VN), id='log_audit', replace_existing=True)

    # News Agent jobs (only if enabled in config)
    news_cfg = config.get("news_agent", {})
    if news_cfg.get("enabled", False):
        try:
            nh, nm = map(int, news_cfg.get("morning_time", "06:30").split(":"))
            ah, am = map(int, news_cfg.get("afternoon_time", "17:30").split(":"))
            eh, em = map(int, news_cfg.get("evening_time", "20:00").split(":"))
        except (ValueError, AttributeError) as e:
            logger.warning("[SCHEDULER] Invalid news time config: %s; using defaults 06:30/17:30/20:00", e)
            nh, nm = 6, 30
            ah, am = 17, 30
            eh, em = 20, 0

        scheduler.add_job(task_morning_news, CronTrigger(hour=nh, minute=nm, timezone=TZ_VN), id='news_morning', replace_existing=True)
        scheduler.add_job(task_afternoon_news, CronTrigger(hour=ah, minute=am, timezone=TZ_VN), id='news_afternoon', replace_existing=True)
        scheduler.add_job(task_evening_news, CronTrigger(hour=eh, minute=em, timezone=TZ_VN), id='news_evening', replace_existing=True)

        logger.info(
            f"[SCHEDULER] News jobs loaded: Morning({nh}:{nm:02d}), "
            f"Afternoon({ah}:{am:02d}), Evening({eh}:{em:02d})"
        )

    logger.info(f"[SCHEDULER] Jobs loaded: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m), Reflection(Sun 20:00)")


def start_scheduler():
    """Start the scheduler for the first time."""
    setup_jobs()
    scheduler.start()


def reload_scheduler():
    """Called from Admin UI to instantly reload jobs with updated config."""
    setup_jobs()

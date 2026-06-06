from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
import os
from datetime import datetime

from app.core.user_context import get_primary_user_id
from app.core.config import load_config
from app.core.database import (
    get_training_loads,
    get_activities_needing_analysis,
    get_run_activity_raw,
)
from app.core.notification import send_telegram_msg
from app.agents.coach.harvest import harvest_data
from app.agents.coach.utils import calculate_training_phase
from app.services.backup import perform_backup
from app.agents.coach.agent import (
    generate_weekly_reflection,
    generate_morning_briefing,
    extract_implicit_memory,
    analyze_run_with_gemini,
)
from app.services.weather import get_today_weather
from app.agents.news.agent import generate_news_briefing
from app.services.log_auditor import run_audit
from app.agents.coach.garmin_client import get_garmin_client
from app.agents.coach.setup_flow import cleanup_stale_setup_sessions
from app.agents.coach.flows.weekly_plan_generation import generate_weekly_plan

from app.core.logging_conf import get_module_logger

logger = get_module_logger("scheduler")
TZ_VN = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))


_SESSION_DEFAULTS = {
    "morning": "06:30",
    "afternoon": "17:30",
    "evening": "20:00",
}


def _is_late_trigger(session: str, config: dict) -> bool:
    """Return True if now is more than skip_minutes past the scheduled session time.

    Fails open (returns False) on invalid config so jobs still run.
    """
    news_cfg = config.get("news_agent", {})
    time_str = news_cfg.get(f"{session}_time", _SESSION_DEFAULTS.get(session, "06:30"))
    skip_minutes = int(news_cfg.get("late_trigger_skip_minutes", 30))

    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return False

    now = datetime.now(TZ_VN)
    diff = (now.hour * 60 + now.minute) - (h * 60 + m)
    return diff > skip_minutes


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
def _is_session_enabled(config: dict, session: str) -> bool:
    """Return True if the given news session is enabled (defaults True if key absent)."""
    return config.get("news_agent", {}).get("sessions", {}).get(session, True)


def task_morning_news():
    """Morning news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        config = load_config()
        if not _is_session_enabled(config, "morning"):
            logger.info(
                "[SCHEDULER] Morning news session disabled in config. Skipping."
            )
            return
        logger.info("[SCHEDULER] Triggering morning news briefing...")
        generate_news_briefing(config, session="morning")
    except Exception as e:
        logger.error("[SCHEDULER] task_morning_news failed: %s", e, exc_info=True)


def task_afternoon_news():
    """Afternoon news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        config = load_config()
        if not _is_session_enabled(config, "afternoon"):
            logger.info(
                "[SCHEDULER] Afternoon news session disabled in config. Skipping."
            )
            return
        logger.info("[SCHEDULER] Triggering afternoon news briefing...")
        generate_news_briefing(config, session="afternoon")
    except Exception as e:
        logger.error("[SCHEDULER] task_afternoon_news failed: %s", e, exc_info=True)


def task_evening_news():
    """Evening news briefing via Gemini+search. Must be regular def (BackgroundScheduler thread pool)."""
    try:
        config = load_config()
        if not _is_session_enabled(config, "evening"):
            logger.info(
                "[SCHEDULER] Evening news session disabled in config. Skipping."
            )
            return
        logger.info("[SCHEDULER] Triggering evening news briefing...")
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
        phase_info = calculate_training_phase(
            config.get("race_date", ""), race_distance_km
        )
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
            taper_label = {1: "Race Week", 2: "Tuần −2", 3: "Tuần −3"}.get(
                weeks_left, ""
            )
            taper_pct = {1: "25%", 2: "50%", 3: "75%"}.get(weeks_left, "")
            alerts.append(
                f"🏁 <b>Nhắc nhở Taper — {taper_label}</b>\n"
                f"Còn {weeks_left} tuần đến ngày đua. Giảm khối lượng xuống còn {taper_pct} so với peak."
            )

        for msg in alerts:
            send_telegram_msg(str(chat_id), msg)
            logger.info(
                f"[SCHEDULER] Proactive alert sent: ACWR={acwr:.2f}, weeks_left={weeks_left}"
            )
    except Exception as e:
        logger.error(
            "[SCHEDULER] task_proactive_coach_check failed: %s", e, exc_info=True
        )


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
            logger.info(
                f"[SCHEDULER] Log audit complete: {count} new entries inserted."
            )
    except Exception as e:
        logger.error("[SCHEDULER] task_log_audit failed: %s", e, exc_info=True)


# ==========================================
# ⌚ GARMIN SYNC
# ==========================================
def task_garmin_sync():
    """Sync Garmin daily metrics. Runs in BackgroundScheduler thread pool."""
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping Garmin sync.")
            return
        logger.info("[SCHEDULER] Syncing Garmin daily metrics...")
        garmin = get_garmin_client()
        garmin.fetch_and_store_daily_metrics(user_id)
    except Exception as e:
        logger.error("[SCHEDULER] task_garmin_sync failed: %s", e, exc_info=True)


# ==========================================
# 📋 WEEKLY PLAN GENERATION
# ==========================================
def task_weekly_plan_generation():
    """Generate weekly training plan on Sunday evening. Runs in BackgroundScheduler thread pool."""
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning(
                "[SCHEDULER] No primary user ID. Skipping weekly plan generation."
            )
            return
        logger.info("[SCHEDULER] Generating weekly training plan...")
        config = load_config()
        generate_weekly_plan(user_id, config)
    except Exception as e:
        logger.error(
            "[SCHEDULER] task_weekly_plan_generation failed: %s", e, exc_info=True
        )


# ==========================================
# 🧹 CLEANUP STALE SETUP SESSIONS
# ==========================================
def task_cleanup_stale_setup():
    """Abandon setup sessions stale for >24h. Runs in BackgroundScheduler thread pool."""
    try:
        logger.info("[SCHEDULER] Cleaning up stale setup sessions...")
        count = cleanup_stale_setup_sessions(timeout_hours=24)
        if count:
            logger.info(f"[SCHEDULER] Cleaned up {count} stale setup sessions.")
    except Exception as e:
        logger.error(
            "[SCHEDULER] task_cleanup_stale_setup failed: %s", e, exc_info=True
        )


# ==========================================
# 🔄 AUTO-RESCHEDULE (INCOMPLETE HARD SESSIONS)
# ==========================================
_HARD_WORKOUT_KEYWORDS = (
    "interval",
    "tempo",
    "race pace",
    "tốc độ",
    "cường độ cao",
    "threshold",
)


def task_auto_reschedule():
    """23:00 daily — defer incomplete hard sessions if readiness low.

    If today's training_plans entry is a hard workout AND not completed AND readiness < 30,
    defer to next available day. Runs in BackgroundScheduler thread pool.
    """
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping auto-reschedule.")
            return

        from datetime import date, timedelta
        from app.core.database import get_db

        logger.info("[SCHEDULER] Running auto-reschedule check...")

        with get_db() as conn:
            c = conn.cursor()

            today_str = date.today().isoformat()
            c.execute(
                """SELECT workout_title, status FROM training_plans
                   WHERE user_id = ? AND date = ?""",
                (user_id, today_str),
            )
            plan_row = c.fetchone()

            if not plan_row:
                logger.info(
                    "[SCHEDULER] No training plan for today. Skipping reschedule."
                )
                return

            workout_title = plan_row["workout_title"] or ""
            status = plan_row["status"] or "pending"

            is_hard = any(kw in workout_title.lower() for kw in _HARD_WORKOUT_KEYWORDS)
            if not (is_hard and status.lower() != "completed"):
                logger.info(
                    f"[SCHEDULER] Today's plan '{workout_title}' ({status}) — no reschedule needed."
                )
                return

            # Check readiness from garmin_daily_metrics
            c.execute(
                """SELECT training_readiness_score FROM garmin_daily_metrics
                   WHERE user_id = ? AND date = ? ORDER BY date DESC LIMIT 1""",
                (user_id, today_str),
            )
            readiness_row = c.fetchone()
            readiness_score = (
                readiness_row["training_readiness_score"] if readiness_row else None
            )

            if readiness_score is None or readiness_score >= 30:
                logger.info(
                    f"[SCHEDULER] Readiness {readiness_score} >= 30. No reschedule needed."
                )
                return

            logger.info(
                f"[SCHEDULER] Readiness {readiness_score} < 30. Deferring '{workout_title}'..."
            )

            # Find next available day (no plan yet)
            for offset in range(1, 8):
                future_date = (date.today() + timedelta(days=offset)).isoformat()
                c.execute(
                    """SELECT date FROM training_plans WHERE user_id = ? AND date = ?""",
                    (user_id, future_date),
                )
                if not c.fetchone():
                    c.execute(
                        """UPDATE training_plans SET date = ?
                           WHERE user_id = ? AND date = ?""",
                        (future_date, user_id, today_str),
                    )
                    conn.commit()

                    chat_id = get_primary_user_id()
                    send_telegram_msg(
                        str(chat_id),
                        f"📅 Giáo án điều chỉnh: Thể trạng hôm nay thấp (readiness {readiness_score}%). "
                        f"Bài '{workout_title}' đã dời sang {future_date}. Hôm nay tập Easy hoặc nghỉ.",
                    )
                    logger.info(
                        f"[SCHEDULER] Deferred '{workout_title}' from {today_str} to {future_date}."
                    )
                    return

            # No available day found — reduce weekly target by 5%
            logger.warning(
                "[SCHEDULER] No available day to reschedule. Reducing weekly target by 5%."
            )
            week_start = (
                date.today() - timedelta(days=date.today().weekday())
            ).isoformat()
            c.execute(
                """SELECT actual_target_km FROM user_weekly_targets
                   WHERE user_id = ? ORDER BY week_start_date DESC LIMIT 1""",
                (user_id,),
            )
            target_row = c.fetchone()
            if target_row and target_row["actual_target_km"]:
                new_target = target_row["actual_target_km"] * 0.95
                c.execute(
                    """INSERT INTO user_weekly_targets (user_id, week_start_date, standard_target_km, actual_target_km)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, week_start_date) DO UPDATE SET actual_target_km=excluded.actual_target_km""",
                    (user_id, week_start, new_target, new_target),
                )
                conn.commit()

            chat_id = get_primary_user_id()
            send_telegram_msg(
                str(chat_id),
                "⚠️ Không tìm được ngày phù hợp để dời bài tập. Giảm mục tiêu tuần này -5%.",
            )

    except Exception as e:
        logger.error("[SCHEDULER] task_auto_reschedule failed: %s", e, exc_info=True)


# ==========================================
# 🥗 NUTRITION ALERT (LONG RUN PREP)
# ==========================================
def task_nutrition_alert():
    """20:00 daily — if tomorrow has LongRun > 15km, send nutrition prep alert.

    Runs in BackgroundScheduler thread pool.
    """
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping nutrition alert.")
            return

        from datetime import date, timedelta
        from app.core.database import get_db

        logger.info("[SCHEDULER] Running nutrition alert check...")

        tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

        with get_db() as conn:
            c = conn.cursor()

            # Check tomorrow's training plan
            c.execute(
                """SELECT target_distance_km FROM training_plans
                   WHERE user_id = ? AND date = ?""",
                (user_id, tomorrow_str),
            )
            plan_row = c.fetchone()

            if not plan_row:
                logger.info(
                    "[SCHEDULER] No training plan for tomorrow. Skipping nutrition alert."
                )
                return

            distance_km = plan_row.get("target_distance_km")

            # Only alert for LongRun > 15km
            if distance_km is None or distance_km <= 15:
                logger.info(
                    f"[SCHEDULER] Tomorrow's distance {distance_km}km <= 15km. No alert."
                )
                return

            logger.info(
                f"[SCHEDULER] Tomorrow has LongRun {distance_km}km. Sending nutrition alert..."
            )

            chat_id = get_primary_user_id()
            send_telegram_msg(
                str(chat_id),
                f"⚡ Ngày mai có Long Run {distance_km:.1f}km. Hãy chuẩn bị: "
                f"2 gói gel hoặc điểm bổ sung năng lượng, 500ml nước điện giải.",
            )

    except Exception as e:
        logger.error("[SCHEDULER] task_nutrition_alert failed: %s", e, exc_info=True)


# ==========================================
# 👟 GEAR TRACKER (SHOE MILEAGE CHECK)
# ==========================================
def task_gear_check():
    """Weekly Monday check: alert if shoe mileage exceeds threshold. Runs in BackgroundScheduler thread pool."""
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping gear check.")
            return

        config = load_config()
        gear_cfg = config.get("garmin", {})
        warn_threshold_km = float(gear_cfg.get("gear_warn_km", 550))
        critical_threshold_km = float(gear_cfg.get("gear_critical_km", 650))

        logger.info("[SCHEDULER] Running gear mileage check...")
        garmin = get_garmin_client()
        gear_list = garmin.fetch_gear_stats(user_id)

        if not gear_list:
            logger.info("[SCHEDULER] No gear data available from Garmin.")
            return

        chat_id = get_primary_user_id()
        for gear in gear_list:
            gear_name = gear.get("name", "Unknown")
            total_km = gear.get("total_km", 0)

            if total_km >= critical_threshold_km:
                msg = (
                    f"🚨 <b>GIÀY CẦN THAY NGAY</b>\n"
                    f"{gear_name}: {total_km:.1f}km (ngưỡng tới hạn: {critical_threshold_km}km)\n"
                    f"Khuyến nghị: Thay giày ngay, giày cũ có thể gây chấn thương."
                )
                send_telegram_msg(str(chat_id), msg)
                logger.info(
                    f"[SCHEDULER] Critical gear alert: {gear_name} at {total_km}km"
                )

            elif total_km >= warn_threshold_km:
                msg = (
                    f"⚠️ <b>GIÀY SẮP HẾT DÙNG</b>\n"
                    f"{gear_name}: {total_km:.1f}km (ngưỡng cảnh báo: {warn_threshold_km}km)\n"
                    f"Khuyến nghị: Chuẩn bị thay giày trong vài tuần tới."
                )
                send_telegram_msg(str(chat_id), msg)
                logger.info(f"[SCHEDULER] Gear warning: {gear_name} at {total_km}km")

    except Exception as e:
        logger.error("[SCHEDULER] task_gear_check failed: %s", e, exc_info=True)


# ==========================================
# 🔁 RETRY FAILED GEMINI ANALYSES
# ==========================================
def task_retry_pending_analyses():
    """Every 2h — re-run Gemini analysis for runs where gcs_score IS NULL (webhook timed out).

    Sends Telegram notification on success. Runs in BackgroundScheduler thread pool.
    """
    try:
        user_id = str(get_primary_user_id())
        if not user_id or user_id == "None":
            logger.warning("[SCHEDULER] No primary user ID. Skipping retry analysis.")
            return

        config = load_config()
        pending = get_activities_needing_analysis(user_id, days_back=3)

        if not pending:
            logger.info("[SCHEDULER] No pending analyses found.")
            return

        logger.info(f"[SCHEDULER] Retrying analysis for {len(pending)} activities...")

        for act in pending:
            activity_id = str(act["activity_id"])
            act_name = act.get("name", "Unknown Run")

            raw = get_run_activity_raw(activity_id)
            if not raw:
                logger.warning(
                    f"[SCHEDULER] No raw data for activity {activity_id}. Skipping."
                )
                continue

            meta_data = raw.get("full_meta", {})
            logger.info(f"[SCHEDULER] Retrying analysis: {act_name} ({activity_id})")

            analysis_text = analyze_run_with_gemini(
                activity_id, act_name, meta_data, config
            )
            if analysis_text:
                telegram_msg = (
                    f"🔁 <b>Phân tích bài chạy (retry):</b> {act_name}\n\n"
                    f"{analysis_text}\n\n"
                    f"🔗 Xem trên Strava: https://www.strava.com/activities/{activity_id}"
                )
                send_telegram_msg(user_id, telegram_msg)
                logger.info(f"[SCHEDULER] Retry analysis sent for {activity_id}")
            else:
                logger.warning(
                    f"[SCHEDULER] Retry analysis still failed for {activity_id}"
                )

    except Exception as e:
        logger.error(
            "[SCHEDULER] task_retry_pending_analyses failed: %s", e, exc_info=True
        )


# ==========================================
# ⚙️ SCHEDULER MANAGEMENT
# ==========================================
def setup_jobs():
    """Read config and set up scheduled cron jobs."""
    config = load_config()
    sched_cfg = config.get("scheduler", {})

    brief_time = sched_cfg.get("briefing_time", "06:00")
    try:
        bh, bm = map(int, brief_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(
            "[SCHEDULER] Invalid briefing_time '%s'; defaulting to 06:00", brief_time
        )
        bh, bm = 6, 0

    backup_time = sched_cfg.get("backup_time", "02:00")
    try:
        bkh, bkm = map(int, backup_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(
            "[SCHEDULER] Invalid backup_time '%s'; defaulting to 02:00", backup_time
        )
        bkh, bkm = 2, 0

    harv_hours = sched_cfg.get("harvest_hours", "0,6,12,18")
    harv_min = str(sched_cfg.get("harvest_minute", "15"))

    scheduler.add_job(
        task_morning_briefing,
        CronTrigger(hour=bh, minute=bm, timezone=TZ_VN),
        id="briefing",
        replace_existing=True,
    )
    scheduler.add_job(
        perform_backup,
        CronTrigger(hour=bkh, minute=bkm, timezone=TZ_VN),
        id="backup",
        replace_existing=True,
    )
    scheduler.add_job(
        task_auto_harvest,
        CronTrigger(hour=harv_hours, minute=harv_min, timezone=TZ_VN),
        id="harvest",
        replace_existing=True,
    )
    scheduler.add_job(
        task_weekly_reflection,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=TZ_VN),
        id="weekly_reflection",
        replace_existing=True,
    )
    scheduler.add_job(
        task_proactive_coach_check,
        CronTrigger(hour=12, minute=0, timezone=TZ_VN),
        id="proactive_check",
        replace_existing=True,
    )
    scheduler.add_job(
        task_log_audit,
        IntervalTrigger(hours=6, timezone=TZ_VN),
        id="log_audit",
        replace_existing=True,
    )
    scheduler.add_job(
        task_garmin_sync,
        CronTrigger(hour=5, minute=45, timezone=TZ_VN),
        id="garmin_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        task_weekly_plan_generation,
        CronTrigger(day_of_week="sun", hour=20, minute=30, timezone=TZ_VN),
        id="weekly_plan_gen",
        replace_existing=True,
    )
    scheduler.add_job(
        task_cleanup_stale_setup,
        CronTrigger(hour=3, minute=0, timezone=TZ_VN),
        id="cleanup_stale_setup",
        replace_existing=True,
    )
    scheduler.add_job(
        task_gear_check,
        CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=TZ_VN),
        id="gear_check",
        replace_existing=True,
    )
    scheduler.add_job(
        task_auto_reschedule,
        CronTrigger(hour=23, minute=0, timezone=TZ_VN),
        id="auto_reschedule",
        replace_existing=True,
    )
    scheduler.add_job(
        task_nutrition_alert,
        CronTrigger(hour=20, minute=0, timezone=TZ_VN),
        id="nutrition_alert",
        replace_existing=True,
    )
    scheduler.add_job(
        task_retry_pending_analyses,
        IntervalTrigger(hours=2, timezone=TZ_VN),
        id="retry_pending_analyses",
        replace_existing=True,
    )

    # News Agent jobs (only if enabled in config)
    news_cfg = config.get("news_agent", {})
    if news_cfg.get("enabled", False):
        try:
            nh, nm = map(int, news_cfg.get("morning_time", "06:30").split(":"))
            ah, am = map(int, news_cfg.get("afternoon_time", "17:30").split(":"))
            eh, em = map(int, news_cfg.get("evening_time", "20:00").split(":"))
        except (ValueError, AttributeError) as e:
            logger.warning(
                "[SCHEDULER] Invalid news time config: %s; using defaults 06:30/17:30/20:00",
                e,
            )
            nh, nm = 6, 30
            ah, am = 17, 30
            eh, em = 20, 0

        scheduler.add_job(
            task_morning_news,
            CronTrigger(hour=nh, minute=nm, timezone=TZ_VN),
            id="news_morning",
            replace_existing=True,
        )
        scheduler.add_job(
            task_afternoon_news,
            CronTrigger(hour=ah, minute=am, timezone=TZ_VN),
            id="news_afternoon",
            replace_existing=True,
        )
        scheduler.add_job(
            task_evening_news,
            CronTrigger(hour=eh, minute=em, timezone=TZ_VN),
            id="news_evening",
            replace_existing=True,
        )

        logger.info(
            f"[SCHEDULER] News jobs loaded: Morning({nh}:{nm:02d}), "
            f"Afternoon({ah}:{am:02d}), Evening({eh}:{em:02d})"
        )

    logger.info(
        f"[SCHEDULER] Jobs loaded: Briefing({bh}:{bm}), Backup({bkh}:{bkm}), Harvest({harv_hours}h:{harv_min}m), Reflection(Sun 20:00)"
    )


def start_scheduler():
    """Start the scheduler for the first time."""
    setup_jobs()
    scheduler.start()


def reload_scheduler():
    """Called from Admin UI to instantly reload jobs with updated config."""
    setup_jobs()

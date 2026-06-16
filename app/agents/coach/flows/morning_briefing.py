from app.core.user_context import get_primary_user_id
from datetime import datetime, timedelta
from app.core.timezone_utils import get_local_tz

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.database import (
    save_message,
    load_history_for_gemini,
    get_plan_for_date,
    get_runs_in_last_days,
    get_all_active_memories,
    get_training_loads,
    get_weekly_volume,
    has_active_plan_this_week,
    get_athlete_state,
    get_garmin_daily_metrics,
    get_pending_weekly_plan,
)
from app.agents.coach.utils import (
    calculate_acwr,
    calculate_training_phase,
    debug_log_prompt,
    get_formatted_weekly_context,
    send_message_with_retry,
)
from app.agents.coach.prompts import (
    build_system_instruction,
    get_shared_context_block,
    build_standup_prompt,
)
from app.agents.coach.tools import (
    update_todays_plan,
    set_actual_weekly_target,
    search_long_term_memory,
    set_workout_plan,
    get_volume_for_week,
    get_volume_summary,
    get_metric_trend,
)
from app.agents.coach.daily_suggestion import (
    compute_daily_suggestion,
    format_daily_suggestion_for_briefing,
)

from app.core.logging_conf import get_module_logger
from app.agents._prompt_telemetry import log_prompt_metrics

logger = get_module_logger("coach")
client = genai.Client()


def _has_active_plan_this_week(user_id: str) -> bool:
    """Check if user has an accepted weekly plan for the current week."""
    tz = get_local_tz()
    now = datetime.now(tz)
    # Calculate current week's Monday
    days_since_monday = now.weekday()  # 0 = Monday, 6 = Sunday
    week_start = (now - timedelta(days=days_since_monday)).strftime("%Y-%m-%d")
    return has_active_plan_this_week(user_id, week_start)


def _get_recent_runs(user_id: str, days: int = 7) -> list:
    """Get recent run activities from database for analysis."""
    try:
        from app.core.database import get_db
        import sqlite3

        with get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute(
                """
                SELECT date, distance_km, workout_type_detected, gcs_score, rpe_score
                FROM run_activities
                WHERE user_id = ? AND date >= ?
                ORDER BY date DESC
                """,
                (user_id, since_date),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"[MORNING_BRIEFING] Failed to get recent runs: {e}")
        return []


def _days_since_last_run(user_id: str) -> int:
    """Calculate days since last run activity."""
    try:
        from app.core.database import get_db
        import sqlite3

        with get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT date FROM run_activities WHERE user_id = ? ORDER BY date DESC LIMIT 1",
                (user_id,),
            )
            row = cursor.fetchone()

            if not row:
                return 999  # No runs recorded

            last_run_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            today = datetime.now().date()
            delta = today - last_run_date
            return delta.days
    except Exception as e:
        logger.warning(f"[MORNING_BRIEFING] Failed to get days_since_last_run: {e}")
        return 0


def generate_morning_briefing(config: dict, weather_data: str = "N/A"):
    """
    [BRAIN] Unified flow to generate the morning briefing.
    Integrates weather awareness and training plans.
    Can be triggered by Scheduler (Cron) or Telegram Webhook.
    """
    logger.info("[COACH AGENT] Starting Morning Briefing reasoning flow...")
    tz = get_local_tz()
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # 1. Gather Data (Data Injection Pattern)
    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
    actual_volume = get_weekly_volume(user_id_str, now)

    race_date_str = config.get("race_date", "")
    race_distance_km = float(config.get("race_distance_km", 21.1))
    phase_info = calculate_training_phase(race_date_str, race_distance_km)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = (
        f"Còn {phase_info['weeks_left']} tuần đến Race."
        if race_date_str
        else "Duy trì thể lực."
    )

    # Check if there's an active plan for this week
    has_active_plan = _has_active_plan_this_week(user_id_str)

    today_plan = get_plan_for_date(user_id_str, now.strftime("%Y-%m-%d"))
    plan_context = (
        f"{today_plan['workout_title']}: {today_plan['description']}"
        if today_plan
        else "Chạy tự do."
    )
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # Fetch short-term memory (last 5 interactions) to maintain conversation context
    raw_history = load_history_for_gemini(user_id_str, limit=5)
    chat_context = "Không có tương tác trò chuyện nào gần đây."
    if raw_history:
        chat_context_lines = []
        for msg in reversed(raw_history):
            sender = "User" if msg["role"] == "user" else "Coach Dyno"
            text = (
                msg["parts"][0][:150] + "..."
                if len(msg["parts"][0]) > 150
                else msg["parts"][0]
            )
            chat_context_lines.append(f"{sender}: {text}")
        chat_context = "\n".join(chat_context_lines)

    # 2. Build Instruction & Prompt (Lego Architecture)
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    taper_factor = phase_info.get("taper_factor", 1.0)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""),
        config.get("user_profile", ""),
        max_hr,
        rest_hr,
        gender,
        "",
        "",
        taper_factor,
        chat_format=True,
    )

    shared_context = get_shared_context_block(
        now.strftime("%A, %d/%m/%Y"),
        user_id_str,
        phase_text,
        countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})",
        actual_volume,
        weekly_decision_context,
    )

    # [ARCHITECTURE UPDATE] Fetch existing active memories globally (Cross-Domain Deduplication)
    memories = get_all_active_memories(user_id_str)

    if memories:
        memory_lines = []
        for m in memories:
            # Format: - [INJURY_STATUS]: Has right knee pain
            memory_lines.append(f"- [{m['category'].upper()}]: {m['fact']}")
        active_memories_text = "\n".join(memory_lines)
    else:
        active_memories_text = "Hệ thống chưa ghi nhận trạng thái đặc biệt nào gần đây."

    logger.info(f"[COACH AGENT] Injected {len(memories)} active memories into prompt.")

    prompt = build_standup_prompt(
        shared_context=shared_context,
        weather_data=weather_data,
        recent_logs=get_runs_in_last_days(user_id_str, days=7),
        today_plan=plan_context,
        chat_context=chat_context,
        active_memories=active_memories_text,
    )

    debug_log_prompt(
        "DEBUG STANDUP PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}"
    )

    # 3. Execution (Resilience Pattern)
    # Phase 3.5: Check if no active plan → show daily suggestion instead of LLM call
    if not has_active_plan:
        try:
            # Check if race_date is configured; if not, show setup prompt
            if not race_date_str:
                setup_prompt = (
                    "ℹ️ Anh chưa thiết lập mục tiêu đua. Dùng /setup để bắt đầu."
                )
                if chat_id:
                    send_telegram_msg(chat_id, setup_prompt)
                    save_message(
                        user_id_str, "model", f"[MORNING BRIEFING] {setup_prompt}"
                    )
                logger.info(
                    "[MORNING_BRIEFING] Race date not configured, showing setup prompt"
                )
                return

            # Check for pending plan (not yet accepted)
            days_since_monday = now.weekday()
            week_start = (now - timedelta(days=days_since_monday)).strftime("%Y-%m-%d")
            pending_plan = get_pending_weekly_plan(user_id_str, week_start)

            # Compute daily suggestion (pure function, no LLM)
            garmin_data = get_garmin_daily_metrics(
                user_id_str, now.strftime("%Y-%m-%d")
            )
            readiness_score = (
                garmin_data.get("training_readiness_score") if garmin_data else None
            )
            athlete_state = get_athlete_state(user_id_str)
            recent_runs = _get_recent_runs(user_id_str, days=7)
            days_since_last = _days_since_last_run(user_id_str)

            suggestion = compute_daily_suggestion(
                readiness_score=readiness_score,
                acwr=acwr_data.get("acwr"),
                recent_runs=recent_runs,
                athlete_state=athlete_state,
                day_of_week=now.weekday(),
                days_since_last_run=days_since_last,
                today_plan=today_plan,
            )

            # Format suggestion for briefing
            suggestion_text = format_daily_suggestion_for_briefing(
                suggestion, garmin_data, has_pending_plan=bool(pending_plan)
            )
            reply = (
                f"🌅 Chào buổi sáng! Hôm nay ({now.strftime('%A')})\n\n"
                + suggestion_text
            )

            if chat_id:
                send_telegram_msg(chat_id, reply)
                save_message(user_id_str, "model", f"[MORNING BRIEFING] {reply}")
            logger.info("[MORNING_BRIEFING] Showed daily suggestion (no active plan)")
            return

        except Exception as e:
            logger.error(f"[MORNING_BRIEFING] Daily suggestion error: {e}")
            # Fall through to LLM-based briefing on error

    # Standard LLM-based briefing (when active plan exists or daily suggestion fails)
    log_prompt_metrics(
        flow="coach.flows.morning_briefing",
        system_inst=system_inst,
        user_prompt=prompt,
        model=config.get("model_name", "models/gemini-2.0-flash"),
    )
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                # [FIX BUG] Cung cấp đầy đủ các Tool mà System Prompt yêu cầu để tránh KeyError
                tools=[
                    update_todays_plan,
                    set_actual_weekly_target,
                    search_long_term_memory,
                    set_workout_plan,
                    get_volume_for_week,
                    get_volume_summary,
                    get_metric_trend,
                ],
            ),
        )
        response = send_message_with_retry(chat_session, prompt)
        reply = response.text or "⚠️ Coach Dyno không thể Briefing lúc này."

        if chat_id:
            send_telegram_msg(chat_id, reply)
            save_message(user_id_str, "model", f"[MORNING BRIEFING] {reply}")
    except Exception as e:
        logger.error(f"[COACH AGENT] Morning Briefing Error: {e}")

import os
from app.core.user_context import get_primary_user_id
import logging
import pytz
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.database import (
    save_message, load_history_for_gemini,
    get_plan_for_date, get_runs_in_last_days, get_all_active_memories,
)
from app.agents.coach.utils import (
    calculate_acwr, calculate_training_phase, debug_log_prompt,
    get_formatted_weekly_context, send_message_with_retry, build_agent_context,
)
from app.agents.coach.prompts import (
    build_system_instruction, get_shared_context_block, build_standup_prompt,
)
from app.agents.coach.tools import (
    update_todays_plan, set_actual_weekly_target,
    search_long_term_memory, set_workout_plan,
    get_volume_for_week, get_volume_summary, get_metric_trend,
)
from app.core.database import get_training_loads, get_weekly_volume

logger = logging.getLogger("AI_COACH")
client = genai.Client()


def generate_morning_briefing(config: dict, weather_data: str = "N/A"):
    """
    [BRAIN] Unified flow to generate the morning briefing.
    Integrates weather awareness and training plans.
    Can be triggered by Scheduler (Cron) or Telegram Webhook.
    """
    logger.info("[COACH AGENT] Starting Morning Briefing reasoning flow...")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # 1. Gather Data (Data Injection Pattern)
    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
    actual_volume = get_weekly_volume(user_id_str, now)

    race_date_str = config.get("race_date", "")
    race_distance_km = float(config.get("race_distance_km", 21.1))
    phase_info = calculate_training_phase(race_date_str, race_distance_km)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race." if race_date_str else "Duy trì thể lực."

    today_plan = get_plan_for_date(user_id_str, now.strftime('%Y-%m-%d'))
    plan_context = f"{today_plan['workout_title']}: {today_plan['description']}" if today_plan else "Chạy tự do."
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # Fetch short-term memory (last 5 interactions) to maintain conversation context
    raw_history = load_history_for_gemini(user_id_str, limit=5)
    chat_context = "Không có tương tác trò chuyện nào gần đây."
    if raw_history:
        chat_context_lines = []
        for msg in reversed(raw_history):
            sender = "User" if msg["role"] == "user" else "Coach Dyno"
            text = msg["parts"][0][:150] + "..." if len(msg["parts"][0]) > 150 else msg["parts"][0]
            chat_context_lines.append(f"{sender}: {text}")
        chat_context = "\n".join(chat_context_lines)

    # 2. Build Instruction & Prompt (Lego Architecture)
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    taper_factor = phase_info.get("taper_factor", 1.0)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        max_hr, rest_hr, gender, "", "", taper_factor,
    )

    shared_context = get_shared_context_block(
        now.strftime('%A, %d/%m/%Y'), user_id_str, phase_text, countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})",
        actual_volume, weekly_decision_context
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
        active_memories=active_memories_text
    )

    debug_log_prompt("DEBUG STANDUP PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    # 3. Execution (Resilience Pattern)
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
                ]
            )
        )
        response = send_message_with_retry(chat_session, prompt)
        reply = response.text or "⚠️ Coach Dyno không thể Briefing lúc này."

        if chat_id:
            send_telegram_msg(chat_id, reply)
            save_message(user_id_str, "model", f"[MORNING BRIEFING] {reply}")
    except Exception as e:
        logger.error(f"[COACH AGENT] Morning Briefing Error: {e}")

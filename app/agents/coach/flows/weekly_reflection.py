import os
from app.core.user_context import get_primary_user_id
from datetime import datetime, timedelta

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.database import (
    save_message, get_recent_runs_log, get_all_active_memories,
)
from app.agents.coach.utils import (
    calculate_acwr, calculate_training_phase, debug_log_prompt,
    get_formatted_weekly_context, send_message_with_retry, build_agent_context,
)
from app.core.timezone_utils import get_local_tz
from app.agents.coach.prompts import (
    build_system_instruction, get_shared_context_block, build_weekly_reflection_prompt,
)
from app.agents.coach.tools import (
    set_actual_weekly_target,
    get_volume_for_week, get_volume_summary, get_metric_trend,
)
from app.services.rag_memory import rag_db
from app.core.database import get_training_loads, get_weekly_volume

from app.core.logging_conf import get_module_logger
logger = get_module_logger("coach")
client = genai.Client(http_options=types.HttpOptions(timeout=120000))  # 120s in ms


def generate_weekly_reflection(config: dict):
    """
    Cron-triggered flow to analyze the past week, set goals for the next week,
    and inject the reflection into long-term RAG memory.
    Strictly follows Data Injection (no tool calling for data gathering).
    """
    logger.info("[COACH AGENT] Generating Weekly Self-Reflection...")
    tz = get_local_tz()
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # 1. Gather Context (Data Injection Pattern)
    race_date_str = config.get("race_date", "")
    race_distance_km = float(config.get("race_distance_km", 21.1))
    phase_info = calculate_training_phase(race_date_str, race_distance_km)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến ngày đua." if race_date_str else "Duy trì thể lực."

    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    actual_volume = get_weekly_volume(user_id_str, now)
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # Fetch recent runs directly from DB (No AI Tool needed)
    recent_logs = get_recent_runs_log(user_id_str)

    # [ARCHITECTURE UPDATE] Fetch existing active memories globally (Cross-Domain Deduplication)
    memories = get_all_active_memories(user_id_str)

    if memories:
        memory_lines = [f"- [{m['category'].upper()}]: {m['fact']}" for m in memories]
        active_memories_text = "\n".join(memory_lines)
    else:
        active_memories_text = "Hệ thống chưa ghi nhận trạng thái đặc biệt nào gần đây."

    # Calculate Next Monday's date for target setting
    next_monday = now + timedelta(days=(7 - now.weekday()))
    next_monday_str = next_monday.strftime('%Y-%m-%d')

    # 2. Build Prompt using Lego blocks
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    taper_factor = phase_info.get("taper_factor", 1.0)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        max_hr, rest_hr, gender, "", "", taper_factor,
    )

    shared_context = get_shared_context_block(
        now.strftime('%A, %Y-%m-%d %H:%M'), user_id_str, phase_text, countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})",
        actual_volume, weekly_decision_context
    )

    # [NEW FIX] Inject active_memories_text into the builder
    prompt = build_weekly_reflection_prompt(shared_context, recent_logs, next_monday_str, active_memories=active_memories_text)
    debug_log_prompt("DEBUG WEEKLY REFLECTION", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    # 3. Call Gemini with Action Tool allowed
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                temperature=0.7,
                tools=[
                    set_actual_weekly_target,  # Crucial: Let AI act on its reflection
                    get_volume_for_week,
                    get_volume_summary,
                    get_metric_trend,
                ]
            )
        )

        # Re-use Resilience Pattern
        response = send_message_with_retry(chat_session, prompt)
        reflection_text = response.text or "⚠️ Coach Dyno encountered an error generating the reflection."

    # 4. Inject Memory into RAG (Long-term autonomous memory)
        week_str = now.strftime('%Y-%m-%d')
        # [MULTI-TENANT] Make doc_id globally unique
        memory_doc_id = f"reflection_{user_id_str}_{week_str}"

        # [ZONE 3] String template for Weekly Reflection
        memory_content = (
            f"[TỔNG KẾT TUẦN {week_str}]\n"
            f"{reflection_text}"
        )

        try:
            logger.info(f"[COACH AGENT] Memorizing reflection for week {week_str}...")
            rag_db.memorize(
                doc_id=memory_doc_id,
                content=memory_content,
                domain="coach",
                extra_meta={
                    "user_id": str(user_id_str),
                    "type": "weekly_reflection"
                }
            )
        except Exception as e:
            logger.error(f"[COACH AGENT] Failed to save reflection to RAG: {e}")

        # 5. Save and Notify
        save_message(user_id_str, "model", f"[WEEKLY REFLECTION]\n{reflection_text}")
        if chat_id:
            send_telegram_msg(chat_id, reflection_text)

    except Exception as e:
        logger.error(f"[COACH AGENT] Weekly Reflection Error: {e}")

from app.core.user_context import get_primary_user_id

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.database import (
    save_message, load_history_for_gemini,
    get_plan_for_date, get_runs_in_last_days, get_all_active_memories,
)
from app.agents.coach.utils import (
    debug_log_prompt, send_message_with_retry, build_agent_context,
)
from app.agents.coach.prompts import build_standup_prompt
from app.agents.coach.tools import (
    update_todays_plan, set_actual_weekly_target,
    search_long_term_memory, set_workout_plan,
    get_volume_for_week, get_volume_summary, get_metric_trend,
)

from app.core.logging_conf import get_module_logger
logger = get_module_logger("coach")
client = genai.Client(http_options=types.HttpOptions(timeout=120000))  # 120s in ms


def generate_morning_briefing(config: dict, weather_data: str = "N/A"):
    """
    [BRAIN] Unified flow to generate the morning briefing.
    Integrates weather awareness and training plans.
    Can be triggered by Scheduler (Cron) or Telegram Webhook.
    """
    logger.info("[COACH AGENT] Starting Morning Briefing reasoning flow...")
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # 1. Gather shared context via canonical factory
    ctx = build_agent_context(user_id_str, config)

    # 2. Gather flow-specific data
    today_plan = get_plan_for_date(user_id_str, ctx.now.strftime('%Y-%m-%d'))
    plan_context = f"{today_plan['workout_title']}: {today_plan['description']}" if today_plan else "Chạy tự do."

    raw_history = load_history_for_gemini(user_id_str, limit=5)
    chat_context = "Không có tương tác trò chuyện nào gần đây."
    if raw_history:
        chat_context_lines = []
        for msg in reversed(raw_history):
            sender = "User" if msg["role"] == "user" else "Coach Dyno"
            text = msg["parts"][0][:150] + "..." if len(msg["parts"][0]) > 150 else msg["parts"][0]
            chat_context_lines.append(f"{sender}: {text}")
        chat_context = "\n".join(chat_context_lines)

    memories = get_all_active_memories(user_id_str)
    if memories:
        active_memories_text = "\n".join(f"- [{m['category'].upper()}]: {m['fact']}" for m in memories)
    else:
        active_memories_text = "Hệ thống chưa ghi nhận trạng thái đặc biệt nào gần đây."

    logger.info(f"[COACH AGENT] Injected {len(memories)} active memories into prompt.")

    # 3. Build prompt
    prompt = build_standup_prompt(
        shared_context=ctx.shared_context,
        weather_data=weather_data,
        recent_logs=get_runs_in_last_days(user_id_str, days=7),
        today_plan=plan_context,
        chat_context=chat_context,
        active_memories=active_memories_text
    )

    debug_log_prompt("DEBUG STANDUP PROMPT", f"[SYSTEM]:\n{ctx.system_inst}\n[USER]:\n{prompt}")

    # 4. Execution (Resilience Pattern)
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            config=types.GenerateContentConfig(
                system_instruction=ctx.system_inst,
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

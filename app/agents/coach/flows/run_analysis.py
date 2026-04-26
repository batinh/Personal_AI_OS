from app.core.user_context import get_primary_user_id
import json
from datetime import datetime

from google import genai
from google.genai import types

from app.core.database import (
    get_plan_for_date, update_run_gcs_score,
    save_message, update_plan_status,
    get_run_metrics_from_db,
)
from app.agents.coach.utils import (
    debug_log_prompt, send_message_with_retry, build_agent_context,
)
from app.agents.coach.prompts import (
    build_universal_run_analysis_prompt,
    DEFAULT_ANALYSIS_TASK, DEFAULT_ANALYSIS_REQUIREMENTS,
    DEFAULT_REPORT_STRUCTURE, UNIVERSAL_FORMAT_RULES,
)
from app.agents.coach.tools import update_todays_plan, set_actual_weekly_target
from app.agents.coach.metrics_engine import build_run_metrics_block
from app.core.schemas import RunAnalysisResult
from app.core.timezone_utils import get_local_tz

from app.core.logging_conf import get_module_logger
logger = get_module_logger("coach")
client = genai.Client(http_options=types.HttpOptions(timeout=120000))  # 120s in ms


def analyze_run_with_gemini(activity_id: str, activity_name: str, meta_data: dict, config: dict):
    logger.info(f"[COACH AGENT] Analyzing run: {activity_name}")
    tz = get_local_tz()
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # 1. Gather shared context via canonical factory
    ctx = build_agent_context(user_id_str, config, now=now)

    # 2. Gather run-specific data
    start_date_raw = meta_data.get("start_date_local", "")
    run_date_str = start_date_raw[:10] if start_date_raw else now.strftime('%Y-%m-%d')

    today_plan = get_plan_for_date(user_id_str, run_date_str)
    plan_context = f"Tên bài: {today_plan['workout_title']}\nChi tiết: {today_plan['description']}" if today_plan else "Chạy tự do."

    meta_text = "\n".join([f"Km {s['km']}: {s['pace']:.2f} m/s | HR {int(s['hr'])}" for s in meta_data.get('splits', [])])

    raw_metrics = get_run_metrics_from_db(activity_id, user_id_str)
    metrics_block = build_run_metrics_block(raw_metrics, config)

    # 3. Build prompt
    prompt = build_universal_run_analysis_prompt(
        shared_context=ctx.shared_context,
        run_name=activity_name,
        meta_text=meta_text,
        today_plan=plan_context,
        task_desc=config.get("task_description", DEFAULT_ANALYSIS_TASK),
        analysis_req=config.get("analysis_requirements", DEFAULT_ANALYSIS_REQUIREMENTS),
        report_structure=config.get("report_structure", DEFAULT_REPORT_STRUCTURE),
        format_rules=config.get("output_format", UNIVERSAL_FORMAT_RULES),
        metrics_block=metrics_block,
    )

    debug_log_prompt("DEBUG STRAVA PROMPT", f"[SYSTEM]:\n{ctx.system_inst}\n[USER]:\n{prompt}")

    # 4. Call Gemini with Native Schema
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            config=types.GenerateContentConfig(
                system_instruction=ctx.system_inst,
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult,
                tools=[update_todays_plan, set_actual_weekly_target]
            )
        )
        response = send_message_with_retry(chat_session, prompt)
        if not response.text:
            logger.warning("[RUN-ANALYSIS] Gemini returned empty response (MALFORMED_RESPONSE or blocked)")
            return None
        result = json.loads(response.text)

        analysis_text = result.get("analysis_text", "")
        update_run_gcs_score(activity_id, user_id_str, result.get("gcs_score", 0))

        if chat_id:
            save_message(user_id_str, "model", f"[ANALYSIS] {activity_name}: {analysis_text}")
            if today_plan:
                update_plan_status(user_id_str, run_date_str, "Completed")

        return analysis_text
    except Exception as e:
        logger.error(f"[COACH AGENT] Analysis Error: {e}")
        return None

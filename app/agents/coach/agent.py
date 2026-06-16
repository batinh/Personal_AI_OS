import os
import json
import re
import unicodedata
import pytz
import time
from datetime import datetime, timedelta

from google import genai
from google.genai import types

# [REFACTOR] Removed Pydantic models from here to comply with Single Responsibility Principle

from app.core.notification import send_telegram_msg, send_typing_action
from app.core.gemini_utils import extract_text as _extract_gemini_text
from app.core.database import (
    save_message,
    load_history_for_gemini,
    clear_history,
    get_training_loads,
    get_recent_runs_log,
    update_run_gcs_score,
    get_upcoming_plans,
    get_plan_for_date,
    update_plan_status,
    get_weekly_volume,
    get_runs_in_last_days,
    insert_memory,
    get_all_active_memories,
    get_run_metrics_from_db,
    get_athlete_state,
    set_athlete_state,
    has_active_plan_this_week,
)
from app.agents.coach.setup_flow import is_setup_in_progress, advance_setup, start_setup
from app.agents.coach.flows.weekly_plan_generation import (
    generate_weekly_plan,
)
from app.agents.coach.daily_suggestion import (
    compute_daily_suggestion,
    format_daily_suggestion_for_briefing,
)
from app.agents.coach.utils import (
    calculate_acwr,
    calculate_training_phase,
    debug_log_prompt,
    get_formatted_weekly_context,
)
from app.services.rag_memory import rag_db

# [REFACTOR] Import builder functions
from app.agents.coach.prompts import (
    build_system_instruction,
    build_core_system_instruction,
    get_shared_context_block,
    build_universal_run_analysis_prompt,
    build_chat_prompt,
    DEFAULT_ANALYSIS_TASK,
    DEFAULT_ANALYSIS_REQUIREMENTS,
    DEFAULT_REPORT_STRUCTURE,
    UNIVERSAL_FORMAT_RULES,
    build_weekly_reflection_prompt,
    build_standup_prompt,
    build_memory_extraction_prompt,
)
from app.agents.coach.tools import (
    update_todays_plan,
    check_training_status,
    get_recent_workouts,
    search_long_term_memory,
    set_workout_plan,
    set_actual_weekly_target,
    save_bulk_workout_plan,
    get_run_full_details,
    get_run_stream_csv,
    get_run_computed_metrics,
    get_metric_trend,
    get_volume_for_week,
    get_volume_summary,
)
from app.agents.coach.metrics_engine import build_run_metrics_block
from app.agents._prompt_telemetry import log_prompt_metrics

# [ARCHITECTURE UPDATE] Import Strict Data Contracts
from app.core.schemas import RunAnalysisResult, MemoryExtractionResult

from app.core.logging_conf import get_module_logger

logger = get_module_logger("coach")
client = genai.Client(http_options=types.HttpOptions(timeout=120000))  # 120s in ms


# ==========================================
# 🛡️ RESILIENCE PATTERN: EXPONENTIAL BACKOFF
# ==========================================
def send_message_with_retry(chat_session, message, max_retries=3):
    """
    Wrapper to call Gemini API with an exponential backoff retry mechanism
    when the Google Server is overloaded.
    Gracefully handles 503 (Unavailable) and 429 (Too Many Requests) errors.
    """
    for attempt in range(max_retries):
        try:
            return chat_session.send_message(message)
        except Exception as e:
            error_msg = str(e)
            _RETRYABLE = (
                "503",
                "504",
                "429",
                "Unavailable",
                "DEADLINE_EXCEEDED",
                "timed out",
                "timeout",
                "ssl",
                "SSL",
                "handshake",
            )
            if any(token in error_msg for token in _RETRYABLE):
                if attempt < max_retries - 1:
                    _server_error = any(
                        t in error_msg
                        for t in ("503", "504", "DEADLINE_EXCEEDED", "Unavailable")
                    )
                    wait_time = min(
                        5 * (2**attempt) if _server_error else 2**attempt, 60
                    )
                    logger.warning(
                        f"[API RESILIENCE] Transient error ({error_msg[:80]}). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        "[API RESILIENCE] Max retries reached. Last error: %s",
                        error_msg[:120],
                    )
                    raise e
            else:
                # Non-retryable error (e.g., invalid API Key) — fail immediately
                raise e


# ==========================================
# 🤖 AGENTIC TOOL-CALL LOOP
# ==========================================
def _run_agentic_loop(chat_session, initial_message: str, max_rounds: int = 5) -> str:
    """
    Send a message and execute any tool calls Gemini requests, feeding results back
    until a final text reply is produced.  Returns the accumulated text reply.

    Without this loop, function_call parts are silently discarded and tools never run.
    """
    message = initial_message
    accumulated_text: list[str] = []

    for round_num in range(max_rounds):
        response = send_message_with_retry(chat_session, message)

        # Collect text parts from this turn
        turn_text = _extract_gemini_text(response)
        if turn_text:
            accumulated_text.append(turn_text)

        # Check for function calls in the response
        candidates = response.candidates or []
        parts = getattr(candidates[0].content, "parts", []) if candidates else []
        fn_calls = [p for p in parts if getattr(p, "function_call", None)]

        if not fn_calls:
            # No tool calls → conversation complete
            break

        # Execute each tool and build function_response parts
        response_parts = []
        for part in fn_calls:
            fc = part.function_call
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}
            logger.info(
                f"[AGENTIC] Executing tool '{fn_name}' args={list(fn_args.keys())}"
            )

            fn_callable = _TOOL_DISPATCH.get(fn_name)
            if fn_callable is None:
                tool_result = f"❌ Unknown tool: {fn_name}"
                logger.error(f"[AGENTIC] Tool '{fn_name}' not in dispatch map")
            else:
                try:
                    tool_result = fn_callable(**fn_args)
                except Exception as exc:
                    tool_result = f"❌ Tool error ({fn_name}): {exc}"
                    logger.error(f"[AGENTIC] Tool '{fn_name}' raised: {exc}")

            logger.info(f"[AGENTIC] Tool '{fn_name}' result: {str(tool_result)[:120]}")
            response_parts.append(
                types.Part.from_function_response(
                    name=fn_name, response={"result": tool_result}
                )
            )

        # Feed all function results back to Gemini in a single turn
        message = response_parts  # type: ignore[assignment]

    return "\n".join(accumulated_text) if accumulated_text else ""


# ==========================================
# 🚀 PERFORMANCE: TOOL ROUTING BY INTENT
# ==========================================
# Read-only tools: used for informational queries (no state changes)
_TOOLS_READ_ONLY = [
    check_training_status,
    get_recent_workouts,
    get_run_full_details,
    get_run_computed_metrics,
    get_run_stream_csv,
    get_metric_trend,
    get_volume_for_week,
    get_volume_summary,
    search_long_term_memory,
]

# Write tools: required when user wants to change schedule or weekly targets
_TOOLS_WRITE = [
    set_workout_plan,
    set_actual_weekly_target,
    save_bulk_workout_plan,
    update_todays_plan,
]

# Keywords indicating the user wants to modify state (schedule/target changes)
_WRITE_INTENT_KEYWORDS = [
    "đổi",
    "thay",
    "hủy",
    "nghỉ",
    "bận",
    "giảm",
    "tăng",
    "chốt",
    "lên lịch",
    "lưu",
    "lập",
    "tạo",
    "kế hoạch",
    "giáo án",
    "lịch tập",
    "set",
    "update",
    "change",
    "cancel",
    "reschedule",
    "target",
    "save",
    "schedule",
]

# Dispatch map: Gemini tool name → Python callable (for agentic tool-call loop)
_TOOL_DISPATCH: dict = {
    "update_todays_plan": update_todays_plan,
    "check_training_status": check_training_status,
    "get_recent_workouts": get_recent_workouts,
    "search_long_term_memory": search_long_term_memory,
    "set_workout_plan": set_workout_plan,
    "set_actual_weekly_target": set_actual_weekly_target,
    "save_bulk_workout_plan": save_bulk_workout_plan,
    "get_run_full_details": get_run_full_details,
    "get_run_stream_csv": get_run_stream_csv,
    "get_run_computed_metrics": get_run_computed_metrics,
    "get_metric_trend": get_metric_trend,
    "get_volume_for_week": get_volume_for_week,
    "get_volume_summary": get_volume_summary,
}

COMMAND_ALIASES = {
    "/chap": "/accept",
    "/tu_choi": "/reject",
    "/om": "/sick",
    "/benh": "/sick",
    "/khoe": "/recover",
    "/binh_thuong": "/recover",
    "/ke_hoach": "/plan",
    "/thiet_lap": "/setup",
}


def resolve_command(text: str) -> str:
    """Normalize Vietnamese command aliases to canonical commands."""
    stripped = text.strip().lower()
    return COMMAND_ALIASES.get(stripped, text)


def _select_tools_for_message(text: str) -> list:
    """
    Route to the minimal tool set needed for this message.
    Read-only queries get fewer tools → smaller context → faster Gemini response.
    """
    text_lower = text.lower()
    needs_write = any(kw in text_lower for kw in _WRITE_INTENT_KEYWORDS)
    if needs_write:
        return _TOOLS_READ_ONLY + _TOOLS_WRITE
    return _TOOLS_READ_ONLY


# ==========================================
# ⚡ PERFORMANCE: FAST vs STANDARD PATH
# ==========================================

# Whitelist: only these exact messages go to the fast (no-context) path.
# Everything else defaults to "standard" — wrong routing to fast breaks UX.
_FAST_EXACT = frozenset(
    {
        "hi",
        "hello",
        "ok",
        "oke",
        "okay",
        "thanks",
        "thank you",
        "cảm ơn",
        "cam on",
        "chào",
        "chao",
        "xin chào",
        "xin chao",
        "được",
        "duoc",
        "tốt",
        "tot",
        "👍",
        "🙏",
        "😊",
        "✅",
    }
)

_STANDARD_KEYWORDS = (
    # Vietnamese — training/planning (có dấu; _fold covers no-dấu variants automatically)
    "phân tích",
    "kế hoạch",
    "lịch",
    "lịch trình",
    "giáo án",
    "đổi",
    "thay",
    "hủy",
    "nghỉ",
    "mục tiêu",
    "tuần",
    "tháng",
    "race",
    "giải",
    "tốc độ",
    "cường độ",
    "tập",
    "chạy",
    "bài",
    "buổi",
    "ngày mai",
    "hôm nay",
    "tuần tới",
    "tuần sau",
    "tuần trước",
    "tuần này",
    "tuần qua",
    "hôm qua",
    "tổng kết",
    "nhớ lại",
    "lịch sử",
    "thống kê",
    "bài chạy",
    "acwr",
    "gcs",
    "pace",
    "power",
    "zone",
    "km",
    # English
    "analyze",
    "plan",
    "schedule",
    "target",
    "training",
    "last week",
    "this week",
    "next week",
    "recap",
    "history",
    "how many",
    "how much",
    "total",
    "weekly",
    "workout",
)


def _classify_intent(text: str) -> str:
    """
    Route to fast (greeting-only) or standard (full context) path.
    Default is "standard" — wrong fast routing breaks UX, wrong standard only costs latency.
    Fast is reserved for exact single-token greetings/acks.
    """
    if text.strip().lower() in _FAST_EXACT:
        return "fast"
    if len(text) > 80:
        return "standard"
    if _text_matches_keyword_list(text, _STANDARD_KEYWORDS):
        return "standard"
    # Unknown short message → standard (safe default)
    return "standard"


def _fold_vietnamese_ascii(text: str) -> str:
    """Lowercase and strip Vietnamese (and similar) combining marks for keyword matching."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _text_matches_keyword_list(text: str, keywords: tuple) -> bool:
    """Match in original lowercase and in diacritic-folded form (covers VI no-dấu + EN)."""
    lowered = text.lower() if isinstance(text, str) else ""
    folded = _fold_vietnamese_ascii(lowered)
    for kw in keywords:
        if not kw or not isinstance(kw, str):
            continue
        kl = kw.lower()
        if kl in lowered:
            return True
        kf = _fold_vietnamese_ascii(kl)
        if kf and kf in folded:
            return True
    return False


def _is_degenerate_response(text: str | None) -> bool:
    """Return True when the model returned nothing (thought-only or empty output)."""
    return not text or not text.strip()


# --- FLOW 1: RUN ANALYSIS ---
def analyze_run_with_gemini(
    activity_id: str, activity_name: str, meta_data: dict, config: dict
):
    logger.info(f"[COACH AGENT] Analyzing run: {activity_name}")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    user_id_str = str(chat_id)

    # 1. Prepare Context data
    start_date_raw = meta_data.get("start_date_local", "")
    run_date_str = start_date_raw[:10] if start_date_raw else now.strftime("%Y-%m-%d")
    race_date_str = config.get("race_date", "")

    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = (
        f"Còn {phase_info['weeks_left']} tuần đến ngày đua."
        if race_date_str
        else "Duy trì thể lực."
    )

    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
    actual_volume = get_weekly_volume(user_id_str, now)
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    today_plan = get_plan_for_date(user_id_str, run_date_str)
    plan_context = (
        f"Tên bài: {today_plan['workout_title']}\nChi tiết: {today_plan['description']}"
        if today_plan
        else "Chạy tự do."
    )
    # 2. BUILD PROMPT (Lego Architecture)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""),
        config.get("user_profile", ""),
        int(config.get("max_hr", 185)),
        int(config.get("rest_hr", 55)),
    )

    shared_context = get_shared_context_block(
        now.strftime("%Y-%m-%d %H:%M"),
        user_id_str,
        phase_text,
        countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})",
        actual_volume,
        weekly_decision_context,
    )

    meta_text = "\n".join(
        [
            f"Km {s['km']}: {s['pace']:.2f} m/s | HR {int(s['hr'])}"
            for s in meta_data.get("splits", [])
        ]
    )

    # Load pre-computed metrics block (computed at webhook time, stored in DB)
    raw_metrics = get_run_metrics_from_db(activity_id, user_id_str)
    metrics_block = build_run_metrics_block(raw_metrics, config)

    # USE OMNICHANNEL BUILDER, OUTPUT FORMAT FOR STRAVA
    prompt = build_universal_run_analysis_prompt(
        shared_context=shared_context,
        run_name=activity_name,
        meta_text=meta_text,
        today_plan=plan_context,
        # Fetching from Admin Config
        task_desc=config.get("task_description", DEFAULT_ANALYSIS_TASK),
        analysis_req=config.get("analysis_requirements", DEFAULT_ANALYSIS_REQUIREMENTS),
        report_structure=config.get("report_structure", DEFAULT_REPORT_STRUCTURE),
        format_rules=config.get("output_format", UNIVERSAL_FORMAT_RULES),
        metrics_block=metrics_block,
    )

    debug_log_prompt(
        "DEBUG STRAVA PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}"
    )
    log_prompt_metrics(
        flow="coach.run_analysis",
        system_inst=system_inst,
        user_prompt=prompt,
        model=config.get("model_name", "models/gemini-2.0-flash"),
    )

    # 3. Call Gemini with Native Schema
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,  # Explicit System Instruction separation
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult,
                tools=[
                    update_todays_plan,
                    set_actual_weekly_target,
                ],  # Grant AI permission to adjust schedule post-run
            ),
        )
        response = send_message_with_retry(chat_session, prompt)
        if not response.text:
            logger.warning(
                "[RUN-ANALYSIS] Gemini returned empty response (MALFORMED_RESPONSE or blocked)"
            )
            return None
        result = json.loads(response.text)

        analysis_text = result.get("analysis_text", "")
        update_run_gcs_score(activity_id, user_id_str, result.get("gcs_score", 0))

        if chat_id:
            save_message(
                user_id_str, "model", f"[ANALYSIS] {activity_name}: {analysis_text}"
            )
            if today_plan:
                update_plan_status(user_id_str, run_date_str, "Completed")

        return analysis_text
    except Exception as e:
        logger.error(f"[COACH AGENT] Analysis Error: {e}")
        return None


# --- FLOW 2: MORNING BRIEFING (STANDUP) ---
def generate_morning_briefing(config: dict, weather_data: str = "N/A"):
    """
    [BRAIN] Unified flow to generate the morning briefing.
    Integrates weather awareness and training plans.
    Can be triggered by Scheduler (Cron) or Telegram Webhook.
    """
    logger.info("[COACH AGENT] Starting Morning Briefing reasoning flow...")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    user_id_str = str(chat_id)

    # 1. Gather Data (Data Injection Pattern)
    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
    actual_volume = get_weekly_volume(user_id_str, now)

    race_date_str = config.get("race_date", "")

    # Guard 1: no race_date → prompt user to run /setup
    if not race_date_str:
        if chat_id:
            send_telegram_msg(
                chat_id,
                "⚙️ Chào buổi sáng! Bạn chưa cấu hình thông tin tập luyện. Hãy gõ /setup để bắt đầu nhé!",
            )
        return

    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = (
        f"Còn {phase_info['weeks_left']} tuần đến Race."
        if race_date_str
        else "Duy trì thể lực."
    )

    today_plan = get_plan_for_date(user_id_str, now.strftime("%Y-%m-%d"))
    plan_context = (
        f"{today_plan['workout_title']}: {today_plan['description']}"
        if today_plan
        else "Chạy tự do."
    )
    weekly_decision_context = get_formatted_weekly_context(user_id_str)

    # Guard 2: no active plan this week → send daily suggestion instead of full AI briefing
    if not has_active_plan_this_week(user_id_str):
        loads = get_training_loads(user_id_str)
        from app.agents.coach.utils import calculate_acwr as _calc_acwr

        acwr_data = _calc_acwr(
            loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
        )
        state = get_athlete_state(user_id_str) or "healthy"
        suggestion = compute_daily_suggestion(
            readiness_score=70,
            acwr=float(acwr_data.get("acwr", 0)),
            recent_runs=[],
            athlete_state=state,
            day_of_week=now.weekday(),
        )
        reply = format_daily_suggestion_for_briefing(suggestion)
        if chat_id:
            send_telegram_msg(chat_id, reply)
            save_message(
                user_id_str, "model", f"[MORNING BRIEFING - SUGGESTION] {reply}"
            )
        return

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
    system_inst = build_system_instruction(
        config.get("system_instruction", ""),
        config.get("user_profile", ""),
        int(config.get("max_hr", 185)),
        int(config.get("rest_hr", 55)),
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
    log_prompt_metrics(
        flow="coach.standup",
        system_inst=system_inst,
        user_prompt=prompt,
        model=config.get("model_name", "models/gemini-2.0-flash"),
    )

    # 3. Execution (Resilience Pattern)
    # 3. Execution (Resilience Pattern)
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                # [PERF] Slim tool set for morning briefing — read-only is sufficient
                tools=[
                    check_training_status,
                    search_long_term_memory,
                ],
            ),
        )
        response = send_message_with_retry(chat_session, prompt)
        reply = (
            _extract_gemini_text(response)
            or "⚠️ Coach Dyno không thể Briefing lúc này."
        )

        if chat_id:
            send_telegram_msg(chat_id, reply)
            save_message(user_id_str, "model", f"[MORNING BRIEFING] {reply}")
    except Exception as e:
        logger.error(f"[COACH AGENT] Morning Briefing Error: {e}")


def handle_telegram_chat(chat_id: str, text: str, config: dict):
    chat_id = str(chat_id)

    # [UX - INSTANT FEEDBACK] Send typing indicator immediately before any processing
    send_typing_action(chat_id)

    # [0] Normalize Vietnamese command aliases
    text = resolve_command(text)

    # [1] Handle Memory Reset
    if text.strip().lower() in ["/clear", "/reset"]:
        clear_history(chat_id)
        send_telegram_msg(chat_id, "🧹 Đã xóa sạch ký ức ngắn hạn.")
        return

    # [2a] /brief → dedicated morning briefing flow (avoids generic chat verbosity)
    if text.strip().lower() in ["/brief", "/standup"]:
        logger.info(f"[WEBHOOK] /brief triggered by {chat_id}")
        from app.core.config import load_config

        generate_morning_briefing(load_config())
        return

    # [2b] Handle Manual Reflection Trigger (TESTING/ADMIN MODE)
    # Hỗ trợ cả 2 cách gõ lệnh để tránh user gõ nhầm
    if text in ["/reflect", "/reflection", "/refect"]:
        send_telegram_msg(
            chat_id,
            "⚙️ [TEST MODE] Đang kích hoạt luồng Phân tích Ký ức & Tổng kết tuần. Quá trình này sẽ mất khoảng 15-30 giây...",
        )

        try:
            logger.info(f"[WEBHOOK] Manual reflection triggered by {chat_id}")

            # BƯỚC 1: LƯU BỘ NHỚ (Extraction MUST run first)
            logger.info("[WEBHOOK] Step 1: Extracting implicit memory...")
            extract_implicit_memory(chat_id)

            # BƯỚC 2: VIẾT BÁO CÁO (Reflection uses the newly extracted memory)
            logger.info("[WEBHOOK] Step 2: Generating weekly reflection...")
            generate_weekly_reflection(config)

        except Exception as e:
            logger.error(f"[WEBHOOK] Error during manual reflection: {e}")
            send_telegram_msg(
                chat_id,
                "❌ Có lỗi xảy ra trong quá trình chạy Reflection. Vui lòng check log.",
            )

        return

    # [2c] /setup → start 6-step onboarding wizard
    if text.strip().lower() == "/setup":
        reply = start_setup(chat_id)
        send_telegram_msg(chat_id, reply)
        return

    # [2d] Setup FSM intercept — if wizard is in progress, route all messages to it
    if is_setup_in_progress(chat_id):
        reply = advance_setup(chat_id, text)
        send_telegram_msg(chat_id, reply)
        return

    # [2e] /sick — mark athlete as sick, force rest
    if text.strip().lower() in ["/sick", "/om"]:
        set_athlete_state(
            chat_id, "sick", note="User reported illness via /sick command"
        )
        send_telegram_msg(
            chat_id,
            "🤒 Đã ghi nhận trạng thái ốm. Hôm nay nghỉ hoàn toàn nhé. Uống nhiều nước, ngủ đủ giấc!",
        )
        return

    # [2f] /recover — mark athlete as recovered
    if text.strip().lower() in ["/recover", "/khoe"]:
        set_athlete_state(
            chat_id, "recovered", note="User reported recovery via /recover command"
        )
        send_telegram_msg(
            chat_id,
            "💪 Tuyệt vời! Đã ghi nhận trạng thái hồi phục. Bắt đầu lại nhẹ nhàng nhé!",
        )
        return

    # [2g] /accept — no longer needed; plans are auto-saved on generation
    if text.strip().lower() in ["/accept", "/chap"]:
        send_telegram_msg(
            chat_id,
            "✅ Giáo án được lưu tự động khi tạo. Không cần xác nhận.\n"
            "Dùng /plan để xem lịch tập hoặc chat với coach để điều chỉnh.",
        )
        return

    # [2h] /reject — redirect to chat-based adjustment
    if text.strip().lower().startswith("/reject"):
        send_telegram_msg(
            chat_id,
            "ℹ️ Chat trực tiếp với coach để điều chỉnh giáo án.\n"
            'Ví dụ: "Thứ Tư đổi thành nghỉ", "Giảm quãng đường Long Run xuống 18km"',
        )
        return

    # [2i] /plan — show accepted plan, re-surface pending plan, or generate on demand
    if text.strip().lower() in ["/plan", "/ke_hoach"]:
        send_telegram_msg(chat_id, "⏳ Đang kiểm tra kế hoạch...")

        # Step 1: accepted plan rows exist in training_plans → show schedule
        upcoming = get_upcoming_plans(chat_id, limit_days=7)
        if upcoming:
            send_telegram_msg(chat_id, f"📅 Lịch tập tuần này:\n{upcoming}")
            return

        # Step 2: no plan at all → generate on demand
        send_telegram_msg(chat_id, "📋 Chưa có giáo án. Đang tạo kế hoạch tuần này...")
        try:
            from app.core.config import load_config as _load_config

            _config = _load_config()
            _result = generate_weekly_plan(chat_id, _config)
            if _result is None:
                send_telegram_msg(
                    chat_id,
                    "⚠️ Không thể tạo giáo án lúc này.\n"
                    "Có thể do đã tồn tại kế hoạch hoặc trạng thái VĐV không cho phép.\n"
                    "Thử lại sau hoặc dùng /recover nếu đang bị chấn thương.",
                )
        except Exception as _e:
            logger.error(f"[CHAT] On-demand plan generation failed: {_e}")
            send_telegram_msg(chat_id, "❌ Lỗi khi tạo giáo án. Vui lòng thử lại sau.")
        return

    # 1. Calculate Context (fast vs standard path)
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    phase_info = calculate_training_phase(config.get("race_date", ""))
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race."

    intent = _classify_intent(text)
    logger.info(f"[CHAT] Intent classified as '{intent}' for message: '{text[:50]}'")

    # Always compute local context (weekly volume, decision context, active memories).
    # These are local facts and must not be skipped even for 'fast' conversational intents —
    # skipping causes the agent to lack factual grounding and misroute tools.
    try:
        actual_volume = get_weekly_volume(chat_id)
    except Exception as e:
        logger.warning(f"[CHAT] Failed to get weekly volume: {e}")
        actual_volume = 0.0

    try:
        weekly_decision_context = get_formatted_weekly_context(chat_id)
    except Exception as e:
        logger.warning(f"[CHAT] Failed to get weekly decision context: {e}")
        weekly_decision_context = ""

    # Fetch active memories only for non-fast intents to avoid expensive RAG calls for trivial chat
    active_memories_text = ""
    if intent != "fast":
        try:
            memories = get_all_active_memories(chat_id)
            memory_lines = [
                f"- [{m['category'].upper()}]: {m['fact']}" for m in memories
            ]
            active_memories_text = (
                "\n".join(memory_lines)
                if memory_lines
                else "Chưa có ghi nhận đặc biệt."
            )
        except Exception as e:
            logger.warning(f"[CHAT] Failed to fetch active memories: {e}")
            active_memories_text = "Chưa có ghi nhận đặc biệt."

    # Note: 'fast' intent will still use a slim system instruction to save tokens, but
    # local facts are always injected so the AI never loses access to up-to-date user state.
    # 2. BUILD PROMPT (Lego Architecture)
    # Fast path uses core-only system prompt (identity + psychology, ~300 tokens).
    # Standard path uses full system prompt (zones, GCS rubric, tool discipline, ~2000 tokens).
    if intent == "fast":
        system_inst = build_core_system_instruction(
            config.get("system_instruction", ""),
            chat_format=True,
        )
    else:
        system_inst = build_system_instruction(
            config.get("system_instruction", ""),
            config.get("user_profile", ""),
            int(config.get("max_hr", 185)),
            int(config.get("rest_hr", 55)),
            chat_format=True,
        )

    if intent != "fast":
        shared_context = get_shared_context_block(
            datetime.now(tz).strftime("%A, %Y-%m-%d %H:%M:%S"),
            chat_id,
            phase_text,
            countdown_text,
            "ACWR đang tính (Dùng tool check_training_status nếu cần)",
            actual_volume,
            weekly_decision_context,
        )
        today_plan_row = get_plan_for_date(str(chat_id), datetime.now(tz).strftime("%Y-%m-%d"))
        today_plan_text = (
            f"{today_plan_row['workout_title']}: {today_plan_row['description']}"
            if today_plan_row
            else ""
        )
        task_prompt = build_chat_prompt(
            shared_context,
            get_upcoming_plans(chat_id, limit_days=7),
            active_memories_text,
            today_plan_text=today_plan_text,
        )
    else:
        # Fast path: skip heavy context — casual chat doesn't need training block
        task_prompt = build_chat_prompt("", "", "")

    debug_log_prompt(
        "DEBUG CHAT INPUT",
        f"[SYSTEM]:\n{system_inst}\n[TASK_CONTEXT]:\n{task_prompt}\n[USER TEXT]: {text}",
    )
    log_prompt_metrics(
        flow="coach.chat",
        system_inst=system_inst,
        user_prompt=f"{task_prompt}\n{text}",
        intent=intent,
        model=config.get("model_name", "models/gemini-2.0-flash"),
    )

    try:
        # [PERF] Use limit=5 for regular chat. Reduces prompt token size and Gemini latency.
        # Deep history queries are handled via search_long_term_memory tool instead.
        raw_history = load_history_for_gemini(chat_id, limit=5)
        formatted_history = [
            {"role": m["role"], "parts": [{"text": m["parts"][0]}]} for m in raw_history
        ]

        # Inject implicit Context at the end of History to enforce current rules
        current_turn_text = (
            f"[SYSTEM CONTEXT UPDATE]\n{task_prompt}\n\n[USER MESSAGE]\n{text}"
        )
        if formatted_history:
            formatted_history.append(
                {"role": "user", "parts": [{"text": current_turn_text}]}
            )
        else:
            formatted_history = [
                {"role": "user", "parts": [{"text": current_turn_text}]}
            ]

        # [PERF] Route to minimal tool set based on message intent
        selected_tools = _select_tools_for_message(text)
        logger.info(
            f"[CHAT] Tool routing: {'WRITE' if len(selected_tools) > len(_TOOLS_READ_ONLY) else 'READ-ONLY'} ({len(selected_tools)} tools) for message: '{text[:50]}'"
        )

        # Fast path caps output for greetings; standard path needs room for full answers
        max_tokens = 512 if intent == "fast" else 2000
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            history=formatted_history[:-1],  # Pass previous history
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                max_output_tokens=max_tokens,
                tools=selected_tools,
            ),
        )
        reply = _run_agentic_loop(
            chat_session, formatted_history[-1]["parts"][0]["text"]
        )

        # Phase 4: degenerate-response gate — retry once with full standard context
        if _is_degenerate_response(reply) and intent == "fast":
            logger.warning(
                "[CHAT] Degenerate response on fast path — retrying with standard context."
            )
            standard_inst = build_system_instruction(
                config.get("system_instruction", ""),
                config.get("user_profile", ""),
                int(config.get("max_hr", 185)),
                int(config.get("rest_hr", 55)),
                chat_format=True,
            )
            log_prompt_metrics(
                flow="coach.chat.retry",
                system_inst=standard_inst,
                user_prompt=f"{task_prompt}\n{text}",
                intent="standard",
                model=config.get("model_name", "models/gemini-2.0-flash"),
            )
            retry_session = client.chats.create(
                model=config.get("model_name", "models/gemini-2.0-flash"),
                history=formatted_history[:-1],
                config=types.GenerateContentConfig(
                    system_instruction=standard_inst,
                    max_output_tokens=1200,
                    tools=selected_tools,
                ),
            )
            reply = _run_agentic_loop(
                retry_session, formatted_history[-1]["parts"][0]["text"]
            )

        if not reply:
            logger.error(
                "[CHAT] Both fast and retry paths returned empty — sending fallback."
            )
            reply = "⚠️ Coach Dyno không thể trả lời lúc này. Bạn thử hỏi lại theo cách khác nhé!"

        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)
    except Exception as e:
        logger.error(f"[TELEGRAM] Chat Error: {e}")
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(
            chat_id,
            "⚠️ Google AI Server đang bảo trì hoặc quá tải. Hãy thử lại sau một chút nhé!",
        )


# --- FLOW 3: WEEKLY SELF-REFLECTION (CRONJOB) ---
def generate_weekly_reflection(config: dict):
    """
    Cron-triggered flow to analyze the past week, set goals for the next week,
    and inject the reflection into long-term RAG memory.
    Strictly follows Data Injection (no tool calling for data gathering).
    """
    logger.info("[COACH AGENT] Generating Weekly Self-Reflection...")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    user_id_str = str(chat_id)

    # 1. Gather Context (Data Injection Pattern)
    race_date_str = config.get("race_date", "")
    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = (
        f"Còn {phase_info['weeks_left']} tuần đến ngày đua."
        if race_date_str
        else "Duy trì thể lực."
    )

    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
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
    next_monday_str = next_monday.strftime("%Y-%m-%d")

    # 2. Build Prompt using Lego blocks
    system_inst = build_system_instruction(
        config.get("system_instruction", ""),
        config.get("user_profile", ""),
        int(config.get("max_hr", 185)),
        int(config.get("rest_hr", 55)),
        chat_format=True,
    )

    shared_context = get_shared_context_block(
        now.strftime("%A, %Y-%m-%d %H:%M"),
        user_id_str,
        phase_text,
        countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})",
        actual_volume,
        weekly_decision_context,
    )

    # [NEW FIX] Inject active_memories_text into the builder
    prompt = build_weekly_reflection_prompt(
        shared_context,
        recent_logs,
        next_monday_str,
        active_memories=active_memories_text,
    )
    debug_log_prompt(
        "DEBUG WEEKLY REFLECTION", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}"
    )
    log_prompt_metrics(
        flow="coach.weekly_reflection",
        system_inst=system_inst,
        user_prompt=prompt,
        model=config.get("model_name", "models/gemini-2.0-flash"),
    )

    # 3. Call Gemini with Action Tool allowed
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                temperature=0.7,
                tools=[
                    set_actual_weekly_target
                ],  # Crucial: Let AI act on its reflection
            ),
        )

        # Re-use Resilience Pattern
        response = send_message_with_retry(chat_session, prompt)
        reflection_text = (
            _extract_gemini_text(response)
            or "⚠️ Coach Dyno encountered an error generating the reflection."
        )

        # 4. Inject Memory into RAG (Long-term autonomous memory)
        week_str = now.strftime("%Y-%m-%d")
        # [MULTI-TENANT] Make doc_id globally unique
        memory_doc_id = f"reflection_{user_id_str}_{week_str}"

        # [ZONE 3] String template for Weekly Reflection
        memory_content = f"[TỔNG KẾT TUẦN {week_str}]\n" f"{reflection_text}"

        try:
            logger.info(f"[COACH AGENT] Memorizing reflection for week {week_str}...")
            rag_db.memorize(
                doc_id=memory_doc_id,
                content=memory_content,
                domain="coach",
                extra_meta={"user_id": str(user_id_str), "type": "weekly_reflection"},
            )
        except Exception as e:
            logger.error(f"[COACH AGENT] Failed to save reflection to RAG: {e}")

        # 5. Save and Notify
        save_message(user_id_str, "model", f"[WEEKLY REFLECTION]\n{reflection_text}")
        if chat_id:
            send_telegram_msg(chat_id, reflection_text)

    except Exception as e:
        logger.error(f"[COACH AGENT] Weekly Reflection Error: {e}")


# --- FLOW 3: AUTONOMOUS MEMORY MANAGER ---
def extract_implicit_memory(user_id_str: str):
    """
    [BRAIN] Analyzes recent chats to extract or mutate implicit memory states.
    Uses Structured Outputs via Pydantic to strictly enforce Categories.
    """
    # [FEATURE FLAG] Fetch debug flag from environment variables (default is False)
    debug_mode = os.getenv("ENABLE_MEMORY_DEBUG", "false").lower() == "true"

    logger.info(f"[MEMORY] Starting extraction for user: {user_id_str}")

    raw_history = load_history_for_gemini(user_id_str, limit=15)
    if not raw_history:
        if debug_mode:
            logger.info("[MEMORY DEBUG] History is empty.")
        return

    chat_history_text = "\n".join(
        [
            f"{'User' if m['role']=='user' else 'AI'}: {m['parts'][0]}"
            for m in reversed(raw_history)
        ]
    )

    # [NEW] Fetch existing active memories globally (Cross-Domain Deduplication)
    memories = get_all_active_memories(user_id_str)

    if memories:
        existing_text = "\n".join(
            [f"- [{m['category'].upper()}]: {m['fact']}" for m in memories]
        )
    else:
        existing_text = "No existing states recorded."

    # Build the state-aware prompt
    prompt = build_memory_extraction_prompt(chat_history_text, existing_text)

    try:
        from app.core.config import load_config

        cfg = load_config()

        log_prompt_metrics(
            flow="coach.memory_extraction",
            system_inst="",
            user_prompt=prompt,
            model=cfg.get("model_name", "models/gemini-2.0-flash"),
        )
        chat_session = client.chats.create(
            model=cfg.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                temperature=0.2,  # Lowered temperature to minimize hallucinations
                response_mime_type="application/json",
                response_schema=MemoryExtractionResult,  # [ARCHITECTURE UPDATE] Strict Pydantic Enforcement
            ),
        )
        response = send_message_with_retry(chat_session, prompt)

        raw_text = response.text if response and response.text else "EMPTY_RESPONSE"

        if debug_mode:
            logger.info(f"[MEMORY DEBUG] Raw AI Response: {raw_text}")

        cleaned_text = re.sub(r"```json\n|\n```|```", "", raw_text).strip()

        if debug_mode:
            logger.info(f"[MEMORY DEBUG] Cleaned Text for JSON: {cleaned_text}")

        extracted_data = json.loads(cleaned_text)

        # Extract the 'items' list mapped from the Pydantic wrapper
        extracted_facts = extracted_data.get("items", [])

        valid_count = 0
        for i, item in enumerate(extracted_facts):
            if debug_mode:
                logger.info(f"[MEMORY DEBUG] Inspecting item {i}: {item}")

            if isinstance(item, dict):
                # Enforced by Schema, guaranteed to match Enum
                domain = item.get("domain", "general")
                category = item.get("category", "other")
                fact = item.get("fact")
                status = item.get("status", "active")  # Parse dynamic status

                if fact:
                    try:
                        if debug_mode:
                            logger.info(
                                f"[MEMORY DEBUG] Attempting DB insert for {user_id_str} | Category: {category} | Status: {status}"
                            )
                        insert_memory(user_id_str, domain, category, fact, status)
                        valid_count += 1
                    except Exception as db_err:
                        logger.error(f"[MEMORY] DB Insert failed: {db_err}")

        # Always output the final summary
        logger.info(f"[MEMORY] Success. Mutated {valid_count} states in core_memory.")

    except json.JSONDecodeError as e:
        logger.error(f"[MEMORY] JSON Parse Error: {e}")
    except Exception as e:
        import traceback

        logger.error(f"[MEMORY] CRITICAL ERROR: {str(e)}")
        if debug_mode:
            logger.error(traceback.format_exc())

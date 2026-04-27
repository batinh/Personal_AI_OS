import os
import json
import re
import threading
import unicodedata
import uuid
import time
from datetime import datetime, timedelta

from google import genai
from google.genai import types
# [REFACTOR] Removed Pydantic models from here to comply with Single Responsibility Principle

from app.core.notification import send_telegram_msg, send_typing_action
from app.core.gemini_utils import extract_text as _extract_gemini_text
from app.core.database import (
    save_message, load_history_for_gemini, clear_history,
    get_training_loads, get_recent_runs_log, update_run_gcs_score,
    update_daily_plan, get_upcoming_plans,
    get_plan_for_date, update_plan_status, get_weekly_volume, get_runs_in_last_days,
    insert_memory, get_all_active_memories, get_weekly_target, # [ARCHITECTURE UPDATE] Using global deduplication
    get_run_metrics_from_db,
    get_athlete_state, set_athlete_state, has_active_plan_this_week,
)
from app.agents.coach.utils import calculate_trimp, calculate_acwr, calculate_training_phase, debug_log_prompt, get_formatted_weekly_context, send_message_with_retry
from app.services.rag_memory import rag_db
from app.core.timezone_utils import get_local_tz
from app.core.user_context import get_primary_user_id

# [REFACTOR] Import builder functions
from app.agents.coach.prompts import (
    build_system_instruction, build_core_system_instruction, get_shared_context_block, build_universal_run_analysis_prompt,build_chat_prompt,
    DEFAULT_ANALYSIS_TASK, DEFAULT_ANALYSIS_REQUIREMENTS, DEFAULT_REPORT_STRUCTURE, UNIVERSAL_FORMAT_RULES,CHAT_FORMAT_RULES,
    build_weekly_reflection_prompt,
    build_standup_prompt,
    build_memory_extraction_prompt
)
from app.agents.coach.tools import (
    update_todays_plan, check_training_status, get_recent_workouts,
    search_long_term_memory, get_total_run_stats, set_workout_plan, set_actual_weekly_target,
    get_run_full_details,
    get_run_stream_csv, get_run_computed_metrics, get_metric_trend,
    get_volume_for_week, get_volume_summary,
)
from app.agents.coach.metrics_engine import build_run_metrics_block
from app.agents.coach.setup_flow import is_setup_in_progress, advance_setup, start_setup
from app.agents.coach.flows.weekly_plan_generation import accept_weekly_plan, reject_weekly_plan, generate_weekly_plan
from app.agents.coach.daily_suggestion import compute_daily_suggestion, format_daily_suggestion_for_briefing

# [ARCHITECTURE UPDATE] Import Strict Data Contracts
from app.core.schemas import RunAnalysisResult, MemoryExtractionResult

from app.core.logging_conf import get_module_logger
logger = get_module_logger("coach")
client = genai.Client(http_options=types.HttpOptions(timeout=120000))  # 120s in ms

# ==========================================
# 🛡️ RESILIENCE PATTERN: EXPONENTIAL BACKOFF
# ==========================================
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
    update_todays_plan,
]

# Keywords indicating the user wants to modify state (schedule/target changes)
_WRITE_INTENT_KEYWORDS = [
    "đổi", "thay", "hủy", "nghỉ", "bận", "giảm", "tăng", "chốt", "lên lịch",
    "set", "update", "change", "cancel", "reschedule", "target"
]

COMMAND_ALIASES = {
    "/chap": "/accept",
    "/bochap": "/reject",
    "/om": "/sick",
    "/khoe": "/recover",
    "/kehoach": "/plan",
    "/setup_coach": "/setup",
}


def resolve_command(text: str) -> str:
    """Normalize Vietnamese command aliases to canonical English commands."""
    stripped = text.strip().lower().split()[0] if text.strip() else text
    canonical = COMMAND_ALIASES.get(stripped)
    if canonical is None:
        return text
    rest = text.strip()[len(stripped):].strip()
    return f"{canonical} {rest}".strip() if rest else canonical


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
_FAST_EXACT = frozenset({
    "hi", "hello", "ok", "oke", "okay", "thanks", "thank you",
    "cảm ơn", "cam on", "chào", "chao", "xin chào", "xin chao",
    "được", "duoc", "tốt", "tot", "👍", "🙏", "😊", "✅",
})

_STANDARD_KEYWORDS = (
    # Vietnamese — training/planning (có dấu; _fold covers no-dấu variants automatically)
    "phân tích", "kế hoạch", "lịch", "lịch trình", "giáo án",
    "đổi", "thay", "hủy", "nghỉ", "mục tiêu",
    "tuần", "tháng", "race", "giải", "tốc độ", "cường độ",
    "tập", "chạy", "bài", "buổi",
    "ngày mai", "hôm nay", "tuần tới", "tuần sau", "tuần trước",
    "tuần này", "tuần qua", "hôm qua",
    "tổng kết", "nhớ lại", "lịch sử", "thống kê", "bài chạy",
    "acwr", "gcs", "pace", "power", "zone", "km",
    # English
    "analyze", "plan", "schedule", "target", "training",
    "last week", "this week", "next week", "recap", "history",
    "how many", "how much", "total", "weekly", "workout",
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
def analyze_run_with_gemini(activity_id: str, activity_name: str, meta_data: dict, config: dict):
    logger.info(f"[COACH AGENT] Analyzing run: {activity_name}")
    tz = get_local_tz()
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)
    
    # 1. Prepare Context data
    start_date_raw = meta_data.get("start_date_local", "")
    run_date_str = start_date_raw[:10] if start_date_raw else now.strftime('%Y-%m-%d')
    race_date_str = config.get("race_date", "")
    
    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến ngày đua." if race_date_str else "Duy trì thể lực."

    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    actual_volume = get_weekly_volume(user_id_str, now)
    weekly_decision_context = get_formatted_weekly_context(user_id_str)
    
    today_plan = get_plan_for_date(user_id_str, run_date_str)
    plan_context = f"Tên bài: {today_plan['workout_title']}\nChi tiết: {today_plan['description']}" if today_plan else "Chạy tự do."
    # 2. BUILD PROMPT (Lego Architecture)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
    )
    
    shared_context = get_shared_context_block(
        now.strftime('%Y-%m-%d %H:%M'), user_id_str, phase_text, countdown_text,
        f"{acwr_data['acwr']} ({acwr_data['status']})", 
        actual_volume, weekly_decision_context
    )

    meta_text = "\n".join([f"Km {s['km']}: {s['pace']:.2f} m/s | HR {int(s['hr'])}" for s in meta_data.get('splits', [])])

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

    debug_log_prompt("DEBUG STRAVA PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    # 3. Call Gemini with Native Schema
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst, # Explicit System Instruction separation
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult,
                tools=[update_todays_plan, set_actual_weekly_target] # Grant AI permission to adjust schedule post-run
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
            if today_plan: update_plan_status(user_id_str, run_date_str, "Completed")
        
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
    tz = get_local_tz()
    now = datetime.now(tz)
    chat_id = get_primary_user_id()
    user_id_str = str(chat_id)

    # [GUARD] No race configured → prompt user to run /setup
    if not config.get("race_date"):
        send_telegram_msg(
            chat_id,
            "☀️ Chào buổi sáng!\n\n⚙️ Chưa có thông tin luyện tập. Dùng /setup để thiết lập mục tiêu và giáo án cá nhân nhé."
        )
        return

    # [FALLBACK] No active plan this week → show rule-based daily suggestion instead of AI briefing
    if not has_active_plan_this_week(user_id_str):
        try:
            loads = get_training_loads(user_id_str)
            acwr_data = calculate_acwr(loads.get('acute_load_7d', 0), loads.get('chronic_load_28d', 0))
            recent_runs = get_runs_in_last_days(user_id_str, days=7)
            athlete_state = get_athlete_state(user_id_str)
            last_run_days = 1
            if recent_runs:
                from datetime import date as _date
                last_date_str = recent_runs[0].get("activity_date", "")
                if last_date_str:
                    try:
                        last_date = _date.fromisoformat(last_date_str[:10])
                        last_run_days = (_date.today() - last_date).days
                    except Exception:
                        pass
            suggestion = compute_daily_suggestion(
                readiness_score=None,
                acwr=acwr_data.get('acwr'),
                recent_runs=recent_runs,
                athlete_state=athlete_state,
                day_of_week=now.weekday(),
                days_since_last_run=last_run_days,
            )
            msg = f"☀️ Chào buổi sáng!\n\n{format_daily_suggestion_for_briefing(suggestion)}"
            send_telegram_msg(chat_id, msg)
        except Exception as e:
            logger.error(f"[COACH AGENT] Daily suggestion fallback error: {e}")
            send_telegram_msg(chat_id, "☀️ Chào buổi sáng! Dùng /plan để tạo giáo án tuần này nhé.")
        return

    # 1. Gather Data (Data Injection Pattern)
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
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
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
    # 3. Execution (Resilience Pattern)
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                # [PERF] Slim tool set for morning briefing — read-only is sufficient
                tools=[
                    check_training_status,
                    search_long_term_memory,
                ]
            )
        )
        response = send_message_with_retry(chat_session, prompt)
        reply = _extract_gemini_text(response) or "⚠️ Coach Dyno không thể Briefing lúc này."
        
        if chat_id:
            send_telegram_msg(chat_id, reply)
            save_message(user_id_str, "model", f"[MORNING BRIEFING] {reply}")
    except Exception as e:
        logger.error(f"[COACH AGENT] Morning Briefing Error: {e}")

def handle_telegram_chat(chat_id: str, text: str, config: dict):
    chat_id = str(chat_id)

    # [UX - INSTANT FEEDBACK] Send typing indicator immediately before any processing
    send_typing_action(chat_id)

    # [0] Normalize Vietnamese command aliases before any routing
    text = resolve_command(text)

    # [0a] Setup FSM intercept — if user is mid-setup, route all input to the wizard
    if is_setup_in_progress(chat_id):
        if text.strip().lower() not in ("/setup", "/clear", "/reset"):
            reply = advance_setup(chat_id, text)
            send_telegram_msg(chat_id, reply)
            return

    # [1] Handle Memory Reset
    if text.strip().lower() in ["/clear", "/reset"]:
        clear_history(chat_id)
        send_telegram_msg(chat_id, "🧹 Đã xóa sạch ký ức ngắn hạn.")
        return
        
    # [1b] /setup → start/restart onboarding wizard
    if text.strip().lower() == "/setup":
        logger.info(f"[WEBHOOK] /setup triggered by {chat_id}")
        reply = start_setup(chat_id)
        send_telegram_msg(chat_id, reply)
        return

    # [1c] /sick and /recover → athlete state transitions
    if text.strip().lower() == "/sick":
        set_athlete_state(chat_id, "sick")
        send_telegram_msg(chat_id, "😷 Đã ghi nhận: anh đang bị ốm. Hệ thống sẽ gợi ý nghỉ ngơi hoàn toàn cho đến khi anh báo khỏe lại (/recover).")
        return

    if text.strip().lower() == "/recover":
        set_athlete_state(chat_id, "healthy")
        send_telegram_msg(chat_id, "✅ Đã cập nhật: trạng thái khoẻ. Tiếp tục chế độ tập luyện bình thường.")
        return

    # [1d] /accept → accept pending weekly plan
    if text.strip().lower() == "/accept":
        logger.info(f"[WEBHOOK] /accept triggered by {chat_id}")
        reply = accept_weekly_plan(chat_id)
        send_telegram_msg(chat_id, reply)
        return

    # [1e] /reject [reason] → reject pending weekly plan
    if text.strip().lower().startswith("/reject"):
        logger.info(f"[WEBHOOK] /reject triggered by {chat_id}")
        reason = text.strip()[len("/reject"):].strip()
        reply = reject_weekly_plan(chat_id, reason)
        send_telegram_msg(chat_id, reply)
        return

    # [2a] /brief → dedicated morning briefing flow (avoids generic chat verbosity)
    if text.strip().lower() in ["/brief", "/standup"]:
        logger.info(f"[WEBHOOK] /brief triggered by {chat_id}")
        from app.core.config import load_config
        generate_morning_briefing(load_config())
        return

    # [2c] /plan → generate AI weekly plan or show existing schedule
    if text.strip().lower() in ["/plan", "/schedule"]:
        logger.info(f"[WEBHOOK] /plan triggered by {chat_id}")
        from app.core.config import load_config as _load_cfg
        cfg = _load_cfg()
        if not cfg.get("race_date"):
            send_telegram_msg(chat_id, "⚙️ Chưa có thông tin giáo án. Dùng /setup để thiết lập mục tiêu trước nhé.")
            return
        send_telegram_msg(chat_id, "⏳ Đang tạo giáo án tuần... (~15s)")
        result = generate_weekly_plan(chat_id, cfg)
        if result is None:
            plan_text = get_upcoming_plans(chat_id, limit_days=7)
            send_telegram_msg(chat_id, f"📅 Giáo án 7 ngày tới:\n\n{plan_text}")
        return

    # [2b] Handle Manual Reflection Trigger (TESTING/ADMIN MODE)
    # Hỗ trợ cả 2 cách gõ lệnh để tránh user gõ nhầm
    if text in ["/reflect", "/reflection", "/refect"]:
        send_telegram_msg(chat_id, "⚙️ [TEST MODE] Đang kích hoạt luồng Phân tích Ký ức & Tổng kết tuần. Quá trình này sẽ mất khoảng 15-30 giây...")
        
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
            send_telegram_msg(chat_id, "❌ Có lỗi xảy ra trong quá trình chạy Reflection. Vui lòng check log.")
    
        return

    # 1. Calculate Context (fast vs standard path)
    tz = get_local_tz()
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
    if intent != 'fast':
        try:
            memories = get_all_active_memories(chat_id)
            memory_lines = [f"- [{m['category'].upper()}]: {m['fact']}" for m in memories]
            active_memories_text = "\n".join(memory_lines) if memory_lines else "Chưa có ghi nhận đặc biệt."
        except Exception as e:
            logger.warning(f"[CHAT] Failed to fetch active memories: {e}")
            active_memories_text = "Chưa có ghi nhận đặc biệt."

    # Note: 'fast' intent will still use a slim system instruction to save tokens, but
    # local facts are always injected so the AI never loses access to up-to-date user state.
    # 2. BUILD PROMPT (Lego Architecture)
    # Fast path uses core-only system prompt (identity + psychology, ~300 tokens).
    # Standard path uses full system prompt (zones, GCS rubric, tool discipline, ~2000 tokens).
    if intent == "fast":
        system_inst = build_core_system_instruction(config.get("system_instruction", ""))
    else:
        system_inst = build_system_instruction(
            config.get("system_instruction", ""), config.get("user_profile", ""),
            int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
        )

    if intent != 'fast':
        shared_context = get_shared_context_block(
            datetime.now(tz).strftime('%A, %Y-%m-%d %H:%M:%S'), chat_id, phase_text, countdown_text,
            "ACWR đang tính (Dùng tool check_training_status nếu cần)",
            actual_volume, weekly_decision_context
        )
        task_prompt = build_chat_prompt(
            shared_context, get_upcoming_plans(chat_id, limit_days=7), active_memories_text
        )
    else:
        # Fast path: skip heavy context — casual chat doesn't need training block
        task_prompt = build_chat_prompt("", "", "")

    debug_log_prompt("DEBUG CHAT INPUT", f"[SYSTEM]:\n{system_inst}\n[TASK_CONTEXT]:\n{task_prompt}\n[USER TEXT]: {text}")

    try:
        # [PERF] Use limit=5 for regular chat. Reduces prompt token size and Gemini latency.
        # Deep history queries are handled via search_long_term_memory tool instead.
        raw_history = load_history_for_gemini(chat_id, limit=5)
        formatted_history = [{"role": m["role"], "parts": [{"text": m["parts"][0]}]} for m in raw_history]

        # Inject implicit Context at the end of History to enforce current rules
        current_turn_text = f"[SYSTEM CONTEXT UPDATE]\n{task_prompt}\n\n[USER MESSAGE]\n{text}"
        if formatted_history:
            formatted_history.append({"role": "user", "parts": [{"text": current_turn_text}]})
        else:
            formatted_history = [{"role": "user", "parts": [{"text": current_turn_text}]}]

        # [PERF] Route to minimal tool set based on message intent
        selected_tools = _select_tools_for_message(text)
        logger.info(f"[CHAT] Tool routing: {'WRITE' if len(selected_tools) > len(_TOOLS_READ_ONLY) else 'READ-ONLY'} ({len(selected_tools)} tools) for message: '{text[:50]}'")

        # Fast path caps output to keep greetings/simple Q&A concise
        max_tokens = 512 if intent == "fast" else 1200
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-flash-latest"),
            history=formatted_history[:-1], # Pass previous history
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                max_output_tokens=max_tokens,
                tools=selected_tools,
            )
        )
        response = send_message_with_retry(chat_session, formatted_history[-1]["parts"][0]["text"])
        reply = _extract_gemini_text(response)

        # Phase 4: degenerate-response gate — retry once with full standard context
        if _is_degenerate_response(reply) and intent == "fast":
            logger.warning("[CHAT] Degenerate response on fast path — retrying with standard context.")
            standard_inst = build_system_instruction(
                config.get("system_instruction", ""), config.get("user_profile", ""),
                int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
            )
            retry_session = client.chats.create(
                model=config.get("model_name", "models/gemini-flash-latest"),
                history=formatted_history[:-1],
                config=types.GenerateContentConfig(
                    system_instruction=standard_inst,
                    max_output_tokens=1200,
                    tools=selected_tools,
                )
            )
            retry_response = send_message_with_retry(retry_session, formatted_history[-1]["parts"][0]["text"])
            reply = _extract_gemini_text(retry_response)

        if not reply:
            logger.error("[CHAT] Both fast and retry paths returned empty — sending fallback.")
            reply = "⚠️ Coach Dyno không thể trả lời lúc này. Bạn thử hỏi lại theo cách khác nhé!"

        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)

        if intent != "fast":
            threading.Thread(target=extract_implicit_memory, args=(chat_id,), daemon=True).start()
    except Exception as e:
        logger.error(f"[TELEGRAM] Chat Error: {e}")
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(chat_id, "⚠️ Google AI Server đang bảo trì hoặc quá tải. Hãy thử lại sau một chút nhé!")

# --- FLOW 3: WEEKLY SELF-REFLECTION (CRONJOB) ---
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
    phase_info = calculate_training_phase(race_date_str)
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
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
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
                tools=[set_actual_weekly_target] # Crucial: Let AI act on its reflection
            )
        )
        
        # Re-use Resilience Pattern
        response = send_message_with_retry(chat_session, prompt)
        reflection_text = _extract_gemini_text(response) or "⚠️ Coach Dyno encountered an error generating the reflection."

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

    chat_history_text = "\n".join([f"{'User' if m['role']=='user' else 'AI'}: {m['parts'][0]}" for m in reversed(raw_history)])
    
    # [NEW] Fetch existing active memories globally (Cross-Domain Deduplication)
    memories = get_all_active_memories(user_id_str)
    
    if memories:
        existing_text = "\n".join([f"- [{m['category'].upper()}]: {m['fact']}" for m in memories])
    else:
        existing_text = "No existing states recorded."

    # Build the state-aware prompt
    prompt = build_memory_extraction_prompt(chat_history_text, existing_text)
    
    try:
        from app.core.config import load_config
        cfg = load_config()
        
        chat_session = client.chats.create(
            model=cfg.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                temperature=0.2, # Lowered temperature to minimize hallucinations
                response_mime_type="application/json",
                response_schema=MemoryExtractionResult # [ARCHITECTURE UPDATE] Strict Pydantic Enforcement
            )
        )
        response = send_message_with_retry(chat_session, prompt)
        
        raw_text = response.text if response and response.text else "EMPTY_RESPONSE"
        
        if debug_mode:
            logger.info(f"[MEMORY DEBUG] Raw AI Response: {raw_text}")

        cleaned_text = re.sub(r'```json\n|\n```|```', '', raw_text).strip()
        
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
                status = item.get("status", "active") # Parse dynamic status
                
                if fact:
                    try:
                        if debug_mode:
                            logger.info(f"[MEMORY DEBUG] Attempting DB insert for {user_id_str} | Category: {category} | Status: {status}")
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
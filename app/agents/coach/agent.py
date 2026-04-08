import os
import logging
import pytz
import time
import threading
from datetime import datetime

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg, send_typing_action
from app.core.database import (
    save_message, load_history_for_gemini, clear_history,
    get_upcoming_plans, get_weekly_volume, get_training_loads, get_weekly_target,
    get_all_active_memories,
)
from app.agents.coach.utils import (
    calculate_training_phase, debug_log_prompt, get_formatted_weekly_context,
    send_message_with_retry,
)
from app.agents.coach.prompts import (
    build_system_instruction, get_shared_context_block, build_chat_prompt,
    CHAT_FORMAT_RULES,
)
from app.agents.coach.tools import (
    update_todays_plan, check_training_status, get_recent_workouts,
    search_long_term_memory, get_total_run_stats, set_workout_plan, set_actual_weekly_target,
    get_run_full_details
)

# [REFACTOR] Delegate to flow modules
from app.agents.coach.flows.run_analysis import analyze_run_with_gemini
from app.agents.coach.flows.morning_briefing import generate_morning_briefing
from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
from app.agents.coach.flows.memory_extraction import extract_implicit_memory

logger = logging.getLogger("AI_COACH")
client = genai.Client()

__all__ = [
    "analyze_run_with_gemini",
    "generate_morning_briefing",
    "generate_weekly_reflection",
    "extract_implicit_memory",
]

# ==========================================
# 🚀 PERFORMANCE: TOOL ROUTING BY INTENT
# ==========================================
# Read-only tools: used for informational queries (no state changes)
_TOOLS_READ_ONLY = [
    check_training_status,
    get_recent_workouts,
    get_run_full_details,
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
# ⚡ PERFORMANCE: INTENT CLASSIFICATION
# ==========================================
_FAST_PATH_MAX_LEN = 60

# Keywords that force standard path (analysis / write required)
_ANALYSIS_KEYWORDS = [
    "phân tích", "đánh giá", "bài chạy", "bài tập", "buổi chạy", "buổi tập",
    "chạy", "km", "pace", "acwr", "hr ", "tốc độ", "mệt", "đau", "chấn thương",
    "kế hoạch", "giáo án", "tuần", "tháng", "reflect", "review", "analyze",
    "report", "training", "marathon", "race", "thi đấu", "chuẩn bị", "lịch",
]


def _classify_intent(text: str) -> str:
    """
    Classify message as 'fast' or 'standard'.
    Fast: short conversational messages with no analysis or write intent.
    Standard: analysis requests, schedule changes, anything needing full context.
    """
    if len(text) > _FAST_PATH_MAX_LEN:
        return "standard"
    text_lower = text.lower()
    if any(kw in text_lower for kw in _WRITE_INTENT_KEYWORDS):
        return "standard"
    if any(kw in text_lower for kw in _ANALYSIS_KEYWORDS):
        return "standard"
    return "fast"


def _bg_extract_memory(chat_id: str) -> None:
    """Background thread target: extract implicit memories after N conversation turns."""
    try:
        logger.info(f"[CHAT-BG] Triggering background memory extraction for {chat_id}")
        extract_implicit_memory(chat_id)
    except Exception as e:
        logger.error(f"[CHAT-BG] Memory extraction error: {e}")


def _handle_fast_chat(chat_id: str, text: str, config: dict) -> None:
    """
    Lightweight handler for short conversational messages.
    Skips full context build, tools, and long history for faster response.
    """
    minimal_system = (
        "Bạn là Coach Dyno — HLV chạy bộ AI cá nhân. "
        "Phản hồi ngắn gọn, tự nhiên, thân thiện bằng tiếng Việt. "
        "Không cần phân tích sâu cho tin nhắn ngắn này."
    )
    try:
        raw_history = load_history_for_gemini(chat_id, limit=3)
        formatted_history = [{"role": m["role"], "parts": [{"text": m["parts"][0]}]} for m in raw_history]
        if formatted_history:
            formatted_history.append({"role": "user", "parts": [{"text": text}]})
        else:
            formatted_history = [{"role": "user", "parts": [{"text": text}]}]

        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            history=formatted_history[:-1],
            config=types.GenerateContentConfig(system_instruction=minimal_system),
        )
        response = send_message_with_retry(chat_session, text)
        reply = response.text or "⚠️ Coach Dyno không thể trả lời lúc này."

        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)
        logger.info(f"[CHAT-FAST] Fast path used for: '{text[:40]}'")
    except Exception as e:
        logger.error(f"[TELEGRAM] Fast Chat Error: {e}")
        send_telegram_msg(chat_id, "⚠️ Google AI Server đang bảo trì hoặc quá tải. Hãy thử lại sau một chút nhé!")


def handle_telegram_chat(chat_id: str, text: str, config: dict):
    chat_id = str(chat_id)

    # [UX - INSTANT FEEDBACK] Send typing indicator immediately before any processing
    send_typing_action(chat_id)

    # [1] Handle Memory Reset
    if text.strip().lower() in ["/clear", "/reset"]:
        clear_history(chat_id)
        send_telegram_msg(chat_id, "🧹 Đã xóa sạch ký ức ngắn hạn.")
        return
        
    # [2A] Fast path: short conversational messages bypass full context build
    if _classify_intent(text) == "fast":
        _handle_fast_chat(chat_id, text, config)
        return

    # [2B] Handle Manual Reflection Trigger (TESTING/ADMIN MODE)
    # Hỗ trợ cả 2 cách gõ lệnh để tránh user gõ nhầm
    if text in ["/reflect", "/reflection"]:
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

    # 1. Calculate Context
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    race_distance_km = float(config.get("race_distance_km", 21.1))
    phase_info = calculate_training_phase(config.get("race_date", ""), race_distance_km)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race."
    actual_volume = get_weekly_volume(chat_id)
    weekly_decision_context = get_formatted_weekly_context(chat_id)

    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    taper_factor = phase_info.get("taper_factor", 1.0)

    # [MEMORY] Inject active memories so coach knows current injuries/state
    memories = get_all_active_memories(chat_id)
    if memories:
        active_memories_text = "\n".join(f"- [{m['category'].upper()}]: {m['fact']}" for m in memories)
    else:
        active_memories_text = ""
    logger.info(f"[CHAT] Injected {len(memories)} active memories into chat prompt.")

    # 2. BUILD PROMPT (Lego Architecture)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        max_hr, rest_hr, gender, "", "", taper_factor,
    )

    shared_context = get_shared_context_block(
        datetime.now(tz).strftime('%A, %Y-%m-%d %H:%M:%S'), chat_id, phase_text, countdown_text,
        "ACWR đang tính (Dùng tool check_training_status nếu cần)", # Offload computation
        actual_volume, weekly_decision_context
    )

    task_prompt = build_chat_prompt(shared_context, get_upcoming_plans(chat_id, limit_days=7), active_memories_text)

    debug_log_prompt("DEBUG CHAT INPUT", f"[SYSTEM]:\n{system_inst}\n[TASK_CONTEXT]:\n{task_prompt}\n[USER TEXT]: {text}")
    
    try:
        # [PERF] Use limit=10 for regular chat (was 20). Reduces prompt token size and Gemini latency.
        # Deep history queries are handled via search_long_term_memory tool instead.
        raw_history = load_history_for_gemini(chat_id, limit=10)
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

        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            history=formatted_history[:-1], # Pass previous history
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                tools=selected_tools
            )
        )
        response = send_message_with_retry(chat_session, formatted_history[-1]["parts"][0]["text"])
        reply = response.text or "⚠️ Coach Dyno không thể trả lời lúc này."
        
        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)

        # [BACKGROUND] Trigger memory extraction every ~5 exchanges (10 stored messages)
        if (len(raw_history) + 2) % 10 == 0:
            threading.Thread(target=_bg_extract_memory, args=(chat_id,), daemon=True).start()
    except Exception as e:
        logger.error(f"[TELEGRAM] Chat Error: {e}")
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(chat_id, "⚠️ Google AI Server đang bảo trì hoặc quá tải. Hãy thử lại sau một chút nhé!")

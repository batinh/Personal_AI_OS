import os
import json
import re
import logging
import pytz
import uuid
import time
from datetime import datetime, timedelta

from google import genai
from google.genai import types
# [REFACTOR] Removed Pydantic models from here to comply with Single Responsibility Principle

from app.core.notification import send_telegram_msg, send_typing_action
from app.core.database import (
    save_message, load_history_for_gemini, clear_history,
    get_training_loads, get_recent_runs_log, update_run_gcs_score, 
    update_daily_plan, get_upcoming_plans, 
    get_plan_for_date, update_plan_status, get_weekly_volume, get_runs_in_last_days,
    insert_memory, get_all_active_memories # [ARCHITECTURE UPDATE] Using global deduplication
)
from app.agents.coach.utils import calculate_trimp, calculate_acwr, calculate_training_phase, debug_log_prompt, get_formatted_weekly_context
from app.services.rag_memory import rag_db

# [REFACTOR] Import builder functions
from app.agents.coach.prompts import (
    build_system_instruction, get_shared_context_block, build_universal_run_analysis_prompt,build_chat_prompt,
    DEFAULT_ANALYSIS_TASK, DEFAULT_ANALYSIS_REQUIREMENTS, DEFAULT_REPORT_STRUCTURE, UNIVERSAL_FORMAT_RULES,CHAT_FORMAT_RULES,
    build_weekly_reflection_prompt,
    build_standup_prompt,
    build_memory_extraction_prompt
)
from app.agents.coach.tools import (
    update_todays_plan, check_training_status, get_recent_workouts,
    search_long_term_memory, get_total_run_stats, set_workout_plan, set_actual_weekly_target,
    get_run_full_details
)

# [ARCHITECTURE UPDATE] Import Strict Data Contracts
from app.core.schemas import RunAnalysisResult, MemoryExtractionResult

logger = logging.getLogger("AI_COACH")
client = genai.Client()

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
            if "503" in error_msg or "429" in error_msg or "Unavailable" in error_msg:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Wait 1s, 2s, 4s...
                    logger.warning(f"[API RESILIENCE] Google Server overloaded (503/429). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    logger.error("[API RESILIENCE] Max retries reached. Google Server is completely down.")
                    raise e
            else:
                # If it is a different error (e.g., invalid API Key), raise immediately without retrying
                raise e

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

# --- FLOW 1: RUN ANALYSIS ---
def analyze_run_with_gemini(activity_id: str, activity_name: str, csv_data: str, meta_data: dict, config: dict):
    logger.info(f"[COACH AGENT] Analyzing run: {activity_name}")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
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
    
    # [HOTFIX]: Return csv_data and task_description from config    
    # USE OMNICHANNEL BUILDER, OUTPUT FORMAT FOR STRAVA
    prompt = build_universal_run_analysis_prompt(
        shared_context=shared_context, 
        run_name=activity_name, 
        meta_text=meta_text, 
        today_plan=plan_context,
        # Fetching from Admin Config
        task_desc=config.get("task_description", DEFAULT_ANALYSIS_TASK),
        analysis_req=config.get("analysis_requirements", DEFAULT_ANALYSIS_REQUIREMENTS),
        report_structure=config.get("report_structure", DEFAULT_REPORT_STRUCTURE), # Added missing variable!
        format_rules=config.get("output_format", UNIVERSAL_FORMAT_RULES),
        csv_data=csv_data
    )

    debug_log_prompt("DEBUG STRAVA PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    # 3. Call Gemini with Native Schema
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst, # Explicit System Instruction separation
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult,
                tools=[update_todays_plan, set_actual_weekly_target] # Grant AI permission to adjust schedule post-run
            )
        )
        response = send_message_with_retry(chat_session, prompt)
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
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    user_id_str = str(chat_id)

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
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                # [FIX BUG] Cung cấp đầy đủ các Tool mà System Prompt yêu cầu để tránh KeyError
                tools=[
                    update_todays_plan, 
                    set_actual_weekly_target, 
                    search_long_term_memory, 
                    set_workout_plan
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

def handle_telegram_chat(chat_id: str, text: str, config: dict):
    chat_id = str(chat_id)

    # [UX - INSTANT FEEDBACK] Send typing indicator immediately before any processing
    send_typing_action(chat_id)

    # [1] Handle Memory Reset
    if text.strip().lower() in ["/clear", "/reset"]:
        clear_history(chat_id)
        send_telegram_msg(chat_id, "🧹 Đã xóa sạch ký ức ngắn hạn.")
        return
        
    # [2] Handle Manual Reflection Trigger (TESTING/ADMIN MODE)
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
    phase_info = calculate_training_phase(config.get("race_date", ""))
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race."
    actual_volume = get_weekly_volume(chat_id)
    weekly_decision_context = get_formatted_weekly_context(chat_id)

    # 2. BUILD PROMPT (Lego Architecture)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
    )
    
    shared_context = get_shared_context_block(
        datetime.now(tz).strftime('%A, %Y-%m-%d %H:%M:%S'), chat_id, phase_text, countdown_text,
        "ACWR đang tính (Dùng tool check_training_status nếu cần)", # Offload computation
        actual_volume, weekly_decision_context
    )
    
    task_prompt = build_chat_prompt(shared_context, get_upcoming_plans(chat_id, limit_days=7))

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
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
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
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                temperature=0.7,
                tools=[set_actual_weekly_target] # Crucial: Let AI act on its reflection
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

# --- FLOW 3: AUTONOMOUS MEMORY MANAGER ---
def extract_implicit_memory(user_id_str: str):
    """
    [BRAIN] Analyzes recent chats to extract or mutate implicit memory states.
    Uses Structured Outputs via Pydantic to strictly enforce Categories.
    """
    # [FEATURE FLAG] Fetch debug flag from environment variables (default is False)
    debug_mode = os.getenv("ENABLE_MEMORY_DEBUG", "false").lower() == "true"
    
    logger.info(f"[MEMORY] Starting extraction for user: {user_id_str}")
    
    raw_history = load_history_for_gemini(user_id_str, limit=30)
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
import os
import json
import logging
import pytz
import uuid
import time
from datetime import datetime

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.core.notification import send_telegram_msg
from app.core.database import (
    save_message, load_history_for_gemini, clear_history,
    get_training_loads, get_recent_runs_log, update_run_gcs_score, 
    update_daily_plan, get_upcoming_plans, 
    get_plan_for_date, update_plan_status, get_weekly_volume
)
from app.agents.coach.utils import calculate_trimp, calculate_acwr, calculate_training_phase, debug_log_prompt, get_formatted_weekly_context
from app.services.rag_memory import rag_db
# [REFACTOR] Import các template prompt
from app.agents.coach.prompts import ANALYSIS_SYSTEM_INSTRUCTION, ANALYSIS_USER_PROMPT, CHAT_PERSONA_TEMPLATE
from app.agents.coach.tools import (
    update_todays_plan, check_training_status, get_recent_workouts,
    search_long_term_memory, get_total_run_stats, set_workout_plan, set_actual_weekly_target
)

logger = logging.getLogger("AI_COACH")
client = genai.Client()

class RunAnalysisResult(BaseModel):
    analysis_text: str = Field(description="Bài phân tích chi tiết theo format yêu cầu.")
    gcs_score: int = Field(description="Điểm tự tin hoàn thành mục tiêu (0-100).")
# --- LUỒNG 1: PHÂN TÍCH BÀI CHẠY ---
def analyze_run_with_gemini(activity_id: str, activity_name: str, csv_data: str, meta_data: dict, config: dict):
    logger.info(f"[COACH AGENT] Analyzing run: {activity_name}")
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    # 1. Chuẩn bị dữ liệu Context
    start_date_raw = meta_data.get("start_date_local", "")
    run_date_str = start_date_raw[:10] if start_date_raw else now.strftime('%Y-%m-%d')
    race_date_str = config.get("race_date", "")
    
    phase_info = calculate_training_phase(race_date_str)
    phase_text = phase_info["phase"]
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến ngày đua." if race_date_str else "Duy trì thể lực."

    loads = get_training_loads(str(chat_id))
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    
    today_plan = get_plan_for_date(str(chat_id), run_date_str)
    plan_context = f"Tên bài: {today_plan['title']}\nChi tiết: {today_plan['description']}" if today_plan else "Chạy tự do."

    # 2. [REFACTOR] Format Prompt từ Template
    full_instruction = ANALYSIS_SYSTEM_INSTRUCTION.format(
        system_instruction=config.get("system_instruction", ""),
        user_profile=config.get("user_profile", ""),
        max_hr=config.get("max_hr", 185),
        rest_hr=config.get("rest_hr", 55),
        run_date_str=run_date_str,
        phase=phase_text,
        countdown_text=countdown_text,
        acwr=acwr_data['acwr'],
        acwr_status=acwr_data['status']
    )

    meta_text = "\n".join([f"Km {s['km']}: {s['pace']:.2f} m/s | HR {int(s['hr'])}" for s in meta_data.get('splits', [])])
    
    prompt = ANALYSIS_USER_PROMPT.format(
        activity_name=activity_name,
        plan_context=plan_context,
        meta_text=meta_text,
        task_description=config.get("task_description", "Analyze this run."),
        output_format=config.get("output_format", "Output JSON."),
        csv_data=csv_data
    )

    debug_log_prompt("DEBUG PROMPT ANALYSIS", prompt)

    # 3. Gọi Gemini với Native Schema (Sửa lỗi Warning)
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=full_instruction,
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult
            )
        )
        response = chat_session.send_message(prompt)
        result = json.loads(response.text)
        
        analysis_text = result.get("analysis_text", "")
        update_run_gcs_score(activity_id, str(chat_id), result.get("gcs_score", 0))
        
        if chat_id:
            save_message(str(chat_id), "model", f"[ANALYSIS] {activity_name}: {analysis_text}")
            if today_plan: update_plan_status(str(chat_id), run_date_str, "Completed")
        
        return analysis_text
    except Exception as e:
        logger.error(f"[COACH AGENT] Analysis Error: {e}")
        return None

# --- LUỒNG 2: CHAT TELEGRAM ---
def handle_telegram_chat(chat_id: str, text: str, config: dict):
    chat_id = str(chat_id)
    if text.strip().lower() in ["/clear", "/reset"]:
        clear_history(chat_id)
        send_telegram_msg(chat_id, "🧹 Đã xóa sạch ký ức ngắn hạn.")
        return

    # 1. Tính toán bối cảnh
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    phase_info = calculate_training_phase(config.get("race_date", ""))
    actual_volume = get_weekly_volume(chat_id)
    # ==========================================================
    # [THÊM MỚI SPRINT A] TÍNH TOÁN QUỸ KHỐI LƯỢNG TUẦN
    # ==========================================================
    # Gọi hàm đã refactor DRY từ utils.py để lấy chuỗi 4 dữ kiện
    weekly_decision_context = get_formatted_weekly_context(str(chat_id))

    # 2. [REFACTOR] Format Persona từ Template
    full_persona = CHAT_PERSONA_TEMPLATE.format(
        system_instruction=config.get("system_instruction", ""),
        now_str=datetime.now(tz).strftime('%A, %Y-%m-%d %H:%M:%S'),
        countdown_text=f"Còn {phase_info['weeks_left']} tuần đến Race.",
        phase_text=phase_info["phase"],
        microcycle_text=phase_info["microcycle"],
        chat_id=chat_id,
        actual_volume=actual_volume,
        weekly_decision_context=weekly_decision_context,
        current_plans=get_upcoming_plans(str(chat_id), limit_days=7),
        user_profile=config.get("user_profile", "")
    )

    debug_log_prompt("DEBUG CHAT INPUT", f"[PERSONA]:\n{full_persona}\n[TEXT]: {text}")
    
    try:
        raw_history = load_history_for_gemini(chat_id, limit=20)
        formatted_history = [{"role": m["role"], "parts": [{"text": m["parts"][0]}]} for m in raw_history]
        
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            history=formatted_history,
            config=types.GenerateContentConfig(
                system_instruction=full_persona,
                tools=[check_training_status, get_recent_workouts, search_long_term_memory, set_workout_plan, set_actual_weekly_target]
            )
        )
        response = chat_session.send_message(text)
        reply = response.text or "⚠️ Coach Dyno không thể trả lời lúc này."
        
        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)
    except Exception as e:
        logger.error(f"[TELEGRAM] Chat Error: {e}")
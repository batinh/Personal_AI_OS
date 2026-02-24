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

# [REFACTOR] Import các builder functions
from app.agents.coach.prompts import (
    build_system_instruction, get_shared_context_block, build_universal_run_analysis_prompt,build_chat_prompt,
    DEFAULT_ANALYSIS_TASK, DEFAULT_ANALYSIS_REQUIREMENTS, DEFAULT_REPORT_STRUCTURE, UNIVERSAL_FORMAT_RULES
)
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
    user_id_str = str(chat_id)
    
    # 1. Chuẩn bị dữ liệu Context
    start_date_raw = meta_data.get("start_date_local", "")
    run_date_str = start_date_raw[:10] if start_date_raw else now.strftime('%Y-%m-%d')
    race_date_str = config.get("race_date", "")
    
    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến ngày đua." if race_date_str else "Duy trì thể lực."

    loads = get_training_loads(user_id_str)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    actual_volume = loads.get('avg_weekly_mileage', 0)
    weekly_decision_context = get_formatted_weekly_context(user_id_str)
    
    today_plan = get_plan_for_date(user_id_str, run_date_str)
    plan_context = f"Tên bài: {today_plan['title']}\nChi tiết: {today_plan['description']}" if today_plan else "Chạy tự do."

    # 2. XÂY DỰNG PROMPT (Kiến trúc Lego)
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
    
    # [BẢN VÁ]: Trả lại csv_data và task_description từ config    
    # SỬ DỤNG OMNICHANNEL BUILDER, XUẤT FORMAT CHO STRAVA
    prompt = build_universal_run_analysis_prompt(
        shared_context=shared_context, 
        run_name=activity_name, 
        meta_text=meta_text, 
        today_plan=plan_context,
        # Bắt đầu lấy từ Config Admin
        task_desc=config.get("task_description", DEFAULT_ANALYSIS_TASK),
        analysis_req=config.get("analysis_requirements", DEFAULT_ANALYSIS_REQUIREMENTS),
        report_structure=config.get("report_structure", DEFAULT_REPORT_STRUCTURE), # Biến mới thêm!
        format_rules=config.get("output_format", UNIVERSAL_FORMAT_RULES),
        csv_data=csv_data
    )

    debug_log_prompt("DEBUG STRAVA PROMPT", f"[SYSTEM]:\n{system_inst}\n[USER]:\n{prompt}")

    # 3. Gọi Gemini với Native Schema
    try:
        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                system_instruction=system_inst, # Tách System rõ ràng
                temperature=0.7,
                response_mime_type="application/json",
                response_schema=RunAnalysisResult,
                tools=[update_todays_plan, set_actual_weekly_target] # Cho AI quyền sửa lịch sau bài chạy
            )
        )
        response = chat_session.send_message(prompt)
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
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến Race."
    actual_volume = get_weekly_volume(chat_id)
    weekly_decision_context = get_formatted_weekly_context(chat_id)

    # 2. XÂY DỰNG PROMPT (Kiến trúc Lego)
    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
    )
    
    shared_context = get_shared_context_block(
        datetime.now(tz).strftime('%A, %Y-%m-%d %H:%M:%S'), chat_id, phase_text, countdown_text,
        "ACWR đang tính (Dùng tool check_training_status nếu cần)", # Nhẹ tải
        actual_volume, weekly_decision_context
    )
    
    task_prompt = build_chat_prompt(shared_context, get_upcoming_plans(chat_id, limit_days=7))

    debug_log_prompt("DEBUG CHAT INPUT", f"[SYSTEM]:\n{system_inst}\n[TASK_CONTEXT]:\n{task_prompt}\n[USER TEXT]: {text}")
    
    try:
        raw_history = load_history_for_gemini(chat_id, limit=20)
        formatted_history = [{"role": m["role"], "parts": [{"text": m["parts"][0]}]} for m in raw_history]
        
        # Tiêm Context ngầm vào cuối History để AI nhớ luật chơi hiện tại
        current_turn_text = f"[SYSTEM CONTEXT UPDATE]\n{task_prompt}\n\n[USER MESSAGE]\n{text}"
        if formatted_history:
            formatted_history.append({"role": "user", "parts": [{"text": current_turn_text}]})
        else:
            formatted_history = [{"role": "user", "parts": [{"text": current_turn_text}]}]

        chat_session = client.chats.create(
            model=config.get("model_name", "models/gemini-2.0-flash"),
            history=formatted_history[:-1], # Truyền history cũ
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                tools=[check_training_status, get_recent_workouts, search_long_term_memory, set_workout_plan, set_actual_weekly_target, update_todays_plan]
            )
        )
        response = chat_session.send_message(formatted_history[-1]["parts"][0]["text"])
        reply = response.text or "⚠️ Coach Dyno không thể trả lời lúc này."
        
        save_message(chat_id, "user", text)
        save_message(chat_id, "model", reply)
        send_telegram_msg(chat_id, reply)
    except Exception as e:
        logger.error(f"[TELEGRAM] Chat Error: {e}")
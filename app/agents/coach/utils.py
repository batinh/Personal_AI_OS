import numpy as np
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
import math
import os
import pandas as pd

logger = logging.getLogger(__name__)

def calculate_trimp(duration_minutes: float, avg_hr: float, max_hr: int = 185, rest_hr: int = 55) -> dict:
    """
    Calculate Training Impulse (TRIMP) using Bannister's method.
    Returns a dictionary containing TRIMP score and evaluated intensity zone.
    """
    if avg_hr == 0 or duration_minutes == 0:
        return {"trimp": 0, "intensity_level": "No Data"}
        
    try:
        # Calculate Heart Rate Reserve (HRR)
        hrr = (avg_hr - rest_hr) / (max_hr - rest_hr)
        hrr = max(0, hrr) # Ensure HRR is not negative
        
        # Bannister's formula for males (y-factor = 1.92)
        weight = 0.64 * np.exp(1.92 * hrr)
        trimp = duration_minutes * hrr * weight
        trimp_rounded = round(trimp, 2)
        
        # Evaluate intensity zone based on TRIMP thresholds
        intensity = "Easy/Recovery"
        if trimp_rounded > 120:
            intensity = "High (Severe Load)"
        elif trimp_rounded > 70:
            intensity = "Medium (Tempo/Threshold)"
            
        return {"trimp": trimp_rounded, "intensity_level": intensity}
    except Exception as e:
        logger.error(f"[UTILS] TRIMP calculation error: {e}")
        return {"trimp": 0, "intensity_level": "Error"}

def calculate_acwr(acute_load_7d: float, chronic_load_28d: float) -> dict:
    """
    Calculate Acute-to-Chronic Workload Ratio (ACWR).
    - Acute Load: Total load of the last 7 days (km or TRIMP).
    - Chronic Load: Average weekly load over the last 28 days.
    """
    if chronic_load_28d == 0:
        return {"acwr": 0.0, "status": "No Chronic Data"}
        
    avg_weekly_chronic = chronic_load_28d / 4
    if avg_weekly_chronic == 0:
        return {"acwr": 0.0, "status": "No Chronic Data"}
        
    acwr = round(acute_load_7d / avg_weekly_chronic, 2)
    
    # Evaluate injury risk based on ACWR Sweet Spot (0.8 - 1.3)
    status = "Sweet Spot (Optimal)"
    if acwr < 0.8:
        status = "Under-training (Losing fitness)"
    elif acwr > 1.5:
        status = "Danger Zone (High Injury Risk - Need Recovery)"
    elif acwr > 1.3:
        status = "Overreaching (Caution needed)"
        
    return {"acwr": acwr, "status": status}

def calculate_efficiency_factor(avg_speed_mpm: float, avg_hr: float) -> float:
    """
    Calculate Efficiency Factor (EF).
    Formula: EF = Speed (meters/min) / Average HR
    Indicates cardiovascular efficiency.
    """
    if avg_hr == 0: return 0.0
    return round(avg_speed_mpm / avg_hr, 2)

def calculate_grade_adjusted_pace(velocity_ms: float, grade_pct: float) -> float:
    """
    Calculate Grade Adjusted Pace (GAP) using Minetti's simplified formula.
    Running cost is heavily dependent on the incline (grade percentage).
    """
    # AI Coach estimation logic: 
    # Every 1% incline roughly equals a 2-3 seconds/km pace penalty (Rule of thumb)
    cost = 1 + (grade_pct * 0.045) 
    gap_velocity = velocity_ms * cost
    return gap_velocity

def analyze_decoupling(df: pd.DataFrame) -> float:
    """
    Analyze Aerobic Decoupling (Pa:HR) by splitting the run into two halves.
    If the Efficiency Factor (EF) of the second half drops by more than 5% 
    compared to the first half, it indicates Cardiovascular Drift (poor aerobic endurance).
    """
    if df is None or df.empty or len(df) < 10:
        return 0.0
        
    half_point = len(df) // 2
    first_half = df.iloc[:half_point]
    second_half = df.iloc[half_point:]
    
    # Calculate EF for each half (Speed converted to meters/minute)
    ef1 = calculate_efficiency_factor(first_half['Velocity_m_s'].mean() * 60, first_half['HR_bpm'].mean())
    ef2 = calculate_efficiency_factor(second_half['Velocity_m_s'].mean() * 60, second_half['HR_bpm'].mean())
    
    decoupling = 0.0
    if ef1 > 0:
        # Calculate percentage drop in efficiency
        decoupling = ((ef1 - ef2) / ef1) * 100
        
    return round(decoupling, 2)

def calculate_training_phase(race_date_str: str, timezone_str: str = os.getenv("TZ", "Asia/Ho_Chi_Minh")) -> dict:
    """
    Calculate current Training Phase and Microcycle based on the upcoming race date.
    Returns a dictionary of raw data to feed into the Agentic Reasoning context.
    """
    if not race_date_str:
        return {"phase": "Base Phase", "weeks_left": 99, "microcycle": "Load"}
        
    try:
        tz = pytz.timezone(timezone_str)
        today = datetime.now(tz).date()
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
        
        days_left = (race_date - today).days
        if days_left <= 0:
            return {"phase": "Race Week", "weeks_left": 0, "microcycle": "Race"}
            
        # Round up to get whole weeks
        weeks_left = math.ceil(days_left / 7.0)
        
        # 1. Determine Macrocycle Phase
        if weeks_left <= 2: phase_name = "Taper Phase"
        elif weeks_left <= 4: phase_name = "Peak Phase"
        elif weeks_left <= 8: phase_name = "Build Phase"
        else: phase_name = "Base Phase"
        
        # 2. Determine Microcycle (Using 3 Load : 1 Cutback progression rule)
        is_cutback = (weeks_left % 4 == 1) or (weeks_left <= 2)
        microcycle_type = "Cutback / Recovery Week" if is_cutback else "Load / Progression Week"
        
        return {
            "phase": f"{phase_name} (Còn {weeks_left} tuần)", # Kept Vietnamese for AI prompt string injection
            "weeks_left": weeks_left,
            "microcycle": microcycle_type
        }
    except Exception as e:
        logger.error(f"[UTILS] Phase calculation error: {e}")
        return {"phase": "Error Phase", "weeks_left": 99, "microcycle": "Load"}

def debug_log_prompt(title: str, content: str):
    """
    Standardize the logging of AI Prompts.
    Only active when DEBUG_PROMPTS=true in the environment variables.
    Keeps the Agent and Scheduler modules clean from excessive logging logic.
    """
    if os.getenv("DEBUG_PROMPTS", "false").lower() == "true":
        logger.info(f"\n========== [{title}] ==========\n{content}\n==============================================")

# =====================================================================
# SPRINT A: WEEKLY VOLUME INTELLIGENCE (Decision Support Data)
# =====================================================================

def gather_weekly_decision_inputs(user_id: str, week_start_date: str) -> dict:
    """
    Consolidate inputs required for the AI to make weekly volume decisions.
    Fetches both TRIMP loads and Mileage from the database.
    """
    from app.core.database import get_training_loads, get_weekly_target
    
    # Fetch TRIMP and Mileage simultaneously
    loads = get_training_loads(user_id)
    
    historical_avg_volume = loads.get("avg_weekly_mileage", 0)
    # The 15% Rule: Do not increase weekly volume by more than 15% to prevent injury
    safe_volume_limit = round(historical_avg_volume * 1.15, 1) if historical_avg_volume > 0 else 30.0 

    chronic_load = loads.get("chronic_load_28d", 0)
    current_acute = loads.get("acute_load_7d", 0)
    max_safe_acute = chronic_load * 1.3
    safe_trimp_remaining = round(max_safe_acute - current_acute, 1)
    if safe_trimp_remaining < 0: safe_trimp_remaining = 0

    db_target = get_weekly_target(user_id, week_start_date)

    return {
        "1_historical_avg_volume": historical_avg_volume,
        "2_safe_volume_limit": safe_volume_limit,
        "3_safe_trimp_remaining": safe_trimp_remaining,
        "4_standard_plan_goal": db_target["standard_target_km"] if db_target else None,
        "5_actual_target_km": db_target["actual_target_km"] if db_target else None
    }

def get_formatted_weekly_context(user_id: str) -> str:
    """
    Format the weekly volume context into a string block for AI Prompts.
    Shared across both Scheduler (Morning Standup) and Agent (Telegram Chat) flows.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    
    # Calculate the Monday of the current week
    monday = now - timedelta(days=now.weekday())
    week_start_str = monday.strftime('%Y-%m-%d')
    
    # Gather quantitative data
    decision_inputs = gather_weekly_decision_inputs(user_id, week_start_str)
    
    # Return formatted block (Zone 3: String template remains in Vietnamese for the LLM Persona)
    return f"""
    - Lịch sử Volume (TB 4 tuần): {decision_inputs.get('1_historical_avg_volume', 0)} km
    - Safe Volume (Giới hạn cơ học): {decision_inputs.get('2_safe_volume_limit', 0)} km
    - Safe TRIMP (Giới hạn tim mạch): {decision_inputs.get('3_safe_trimp_remaining', 0)}
    - Standard Plan (Mục tiêu gốc): {decision_inputs.get('4_standard_plan_goal') or 'Chưa có'} km
    - Target thực tế đang chốt: {decision_inputs.get('5_actual_target_km') or 'Chưa chốt'} km
    """

# =====================================================================
# RESILIENCE: EXPONENTIAL BACKOFF FOR GEMINI API
# =====================================================================

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
                raise e

# =====================================================================
# AGENT CONTEXT: SINGLE SOURCE OF TRUTH FOR ALL FLOWS
# =====================================================================

@dataclass
class AgentContext:
    """Encapsulates all computed context needed by every agent flow."""
    user_id: str
    now: datetime
    phase_text: str
    countdown_text: str
    acwr_text: str
    actual_volume: float
    weekly_decision_context: str
    system_inst: str
    shared_context: str


def build_agent_context(user_id: str, config: dict, now: datetime = None) -> AgentContext:
    """Single Source of Truth for gathering and formatting agent context for all flows."""
    from app.core.database import get_training_loads, get_weekly_volume
    from app.agents.coach.prompts import build_system_instruction, get_shared_context_block

    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    if now is None:
        now = datetime.now(tz)

    race_date_str = config.get("race_date", "")
    phase_info = calculate_training_phase(race_date_str)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    countdown_text = f"Còn {phase_info['weeks_left']} tuần đến ngày đua." if race_date_str else "Duy trì thể lực."

    loads = get_training_loads(user_id)
    acwr_data = calculate_acwr(loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0))
    acwr_text = f"{acwr_data['acwr']} ({acwr_data['status']})"
    actual_volume = get_weekly_volume(user_id, now)
    weekly_decision_context = get_formatted_weekly_context(user_id)

    system_inst = build_system_instruction(
        config.get("system_instruction", ""), config.get("user_profile", ""),
        int(config.get("max_hr", 185)), int(config.get("rest_hr", 55))
    )
    shared_context = get_shared_context_block(
        now.strftime('%A, %d/%m/%Y'), user_id, phase_text, countdown_text,
        acwr_text, actual_volume, weekly_decision_context
    )

    return AgentContext(
        user_id=user_id,
        now=now,
        phase_text=phase_text,
        countdown_text=countdown_text,
        acwr_text=acwr_text,
        actual_volume=actual_volume,
        weekly_decision_context=weekly_decision_context,
        system_inst=system_inst,
        shared_context=shared_context,
    )
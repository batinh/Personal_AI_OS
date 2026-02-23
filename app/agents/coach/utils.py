import numpy as np
import logging
from datetime import datetime
import pytz
import math
import os

logger = logging.getLogger(__name__)

def calculate_trimp(duration_minutes: float, avg_hr: float, max_hr: int = 185, rest_hr: int = 55) -> dict:
    """
    Calculate Training Impulse (TRIMP) using Bannister's method.
    Returns a dictionary containing TRIMP score and evaluated intensity.
    """
    if avg_hr == 0 or duration_minutes == 0:
        return {"trimp": 0, "intensity_level": "No Data"}
        
    try:
        # Calculate Heart Rate Reserve (HRR)
        hrr = (avg_hr - rest_hr) / (max_hr - rest_hr)
        hrr = max(0, hrr) # Ensure HRR is not negative
        
        # Bannister's formula for males
        weight = 0.64 * np.exp(1.92 * hrr)
        trimp = duration_minutes * hrr * weight
        trimp_rounded = round(trimp, 2)
        
        # Evaluate intensity zone
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
    Acute: Last 7 days load (km or TRIMP).
    Chronic: Last 28 days load (average per week).
    """
    if chronic_load_28d == 0:
        return {"acwr": 0.0, "status": "No Chronic Data"}
        
    avg_weekly_chronic = chronic_load_28d / 4
    if avg_weekly_chronic == 0:
        return {"acwr": 0.0, "status": "No Chronic Data"}
        
    acwr = round(acute_load_7d / avg_weekly_chronic, 2)
    
    status = "Sweet Spot (Optimal)"
    if acwr < 0.8:
        status = "Under-training (Losing fitness)"
    elif acwr > 1.5:
        status = "Danger Zone (High Injury Risk - Need Recovery)"
    elif acwr > 1.3:
        status = "Overreaching (Caution needed)"
        
    return {"acwr": acwr, "status": status}

def calculate_efficiency_factor(avg_speed_mpm: float, avg_hr: float) -> float:
    """Efficiency Factor (EF) = Speed (meters/min) / HR"""
    if avg_hr == 0: return 0.0
    return round(avg_speed_mpm / avg_hr, 2)

def calculate_grade_adjusted_pace(velocity_ms, grade_pct):
    """
    Công thức đơn giản hóa của Minetti để tính GAP.
    Cost of running phụ thuộc vào độ dốc.
    """
    # Đây là logic phức tạp, AI Coach có thể ước lượng:
    # Mỗi 1% dốc tương đương giảm pace khoảng 2-3 giây/km (quy tắc ngón tay cái)
    cost = 1 + (grade_pct * 0.045) # Ước lượng
    gap_velocity = velocity_ms * cost
    return gap_velocity

def analyze_decoupling(df):
    """
    Chia bài chạy làm 2 nửa, so sánh tỷ lệ EF (Efficiency Factor).
    Nếu nửa sau kém hơn nửa đầu > 5% => Decoupling (Chưa đủ bền).
    """
    half_point = len(df) // 2
    first_half = df.iloc[:half_point]
    second_half = df.iloc[half_point:]
    
    ef1 = calculate_efficiency_factor(first_half['Velocity_m_s'].mean() * 60, first_half['HR_bpm'].mean())
    ef2 = calculate_efficiency_factor(second_half['Velocity_m_s'].mean() * 60, second_half['HR_bpm'].mean())
    
    decoupling = 0
    if ef1 > 0:
        decoupling = (ef1 - ef2) / ef1 * 100
        
    return round(decoupling, 2)

import math
from datetime import datetime
import pytz

def calculate_training_phase(race_date_str: str, timezone_str: str = os.getenv("TZ", "Asia/Ho_Chi_Minh")) -> dict:
    """
    [REFACTORED] Tính toán Phase và Microcycle. 
    Trả về Dictionary thuần dữ liệu để AI lập luận (Agentic Reasoning).
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
            
        # Làm tròn lên để ra số tuần chẵn
        weeks_left = math.ceil(days_left / 7.0)
        
        # 1. Xác định Phase
        if weeks_left <= 2: phase_name = "Taper Phase"
        elif weeks_left <= 4: phase_name = "Peak Phase"
        elif weeks_left <= 8: phase_name = "Build Phase"
        else: phase_name = "Base Phase"
        
        # 2. Xác định Microcycle (Quy tắc 3 Load : 1 Cutback)
        is_cutback = (weeks_left % 4 == 1) or (weeks_left <= 2)
        microcycle_type = "Cutback / Recovery Week" if is_cutback else "Load / Progression Week"
        
        return {
            "phase": f"{phase_name} (Còn {weeks_left} tuần)",
            "weeks_left": weeks_left,
            "microcycle": microcycle_type
        }
    except Exception as e:
        return {"phase": "Error Phase", "weeks_left": 99, "microcycle": "Load"}
def debug_log_prompt(title: str, content: str):
    """
    [REFACTOR] Hàm chuẩn hóa việc in log Prompt.
    Chỉ kích hoạt khi môi trường có bật DEBUG_PROMPTS=true.
    Giúp code ở tầng Agent và Scheduler sạch sẽ hơn.
    """
    if os.getenv("DEBUG_PROMPTS", "false").lower() == "true":
        logger.info(f"\n========== [{title}] ==========\n{content}\n==============================================")
# =====================================================================
# SPRINT A: WEEKLY VOLUME INTELLIGENCE (Hỗ trợ AI ra quyết định)
# =====================================================================

def gather_weekly_decision_inputs(user_id: str, week_start_date: str) -> dict:
    """
    [REFACTORED - DRY] Thu thập dữ liệu từ hàm get_training_loads đã nâng cấp.
    """
    from app.core.database import get_training_loads, get_weekly_target
    
    # Một mũi tên trúng 2 đích: Lấy cả TRIMP và Mileage
    loads = get_training_loads(user_id)
    
    historical_avg_volume = loads.get("avg_weekly_mileage", 0)
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
    [REFACTOR - DRY] Gom logic tính ngày và format chuỗi Context Tuần.
    Hàm này được dùng chung cho cả luồng Scheduler (Sáng) và Agent Chat (Chiều).
    """
    import os
    import pytz
    from datetime import datetime, timedelta
    
    # Lấy múi giờ chuẩn của hệ thống (Đã chuẩn hóa từ bước trước)
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)
    
    # Tính ngày Thứ 2 của tuần này
    monday = now - timedelta(days=now.weekday())
    week_start_str = monday.strftime('%Y-%m-%d')
    
    # Thu thập data
    decision_inputs = gather_weekly_decision_inputs(user_id, week_start_str)
    
    # Format thành chuỗi Text cho Prompt
    return f"""
    - Lịch sử Volume (TB 4 tuần): {decision_inputs.get('1_historical_avg_volume', 0)} km
    - Safe Volume (Giới hạn cơ học): {decision_inputs.get('2_safe_volume_limit', 0)} km
    - Safe TRIMP (Giới hạn tim mạch): {decision_inputs.get('3_safe_trimp_remaining', 0)}
    - Standard Plan (Mục tiêu gốc): {decision_inputs.get('4_standard_plan_goal') or 'Chưa có'} km
    - Target thực tế đang chốt: {decision_inputs.get('5_actual_target_km') or 'Chưa chốt'} km
    """
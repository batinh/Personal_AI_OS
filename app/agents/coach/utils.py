import numpy as np
import logging
from datetime import datetime
import pytz
import math

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

def calculate_training_phase(race_date_str: str, timezone_str: str = "Asia/Ho_Chi_Minh") -> str:
    """
    Tính toán chính xác Training Phase dựa trên số tuần lịch đếm ngược đến Race Date.
    - Tuần 1-2 (0-14 ngày): Taper Phase (Nhả khối lượng)
    - Tuần 3-4 (15-28 ngày): Peak Phase (Đỉnh điểm, cường độ cao nhất)
    - Tuần 5-8 (29-56 ngày): Build Phase (Tích lũy khối lượng)
    - Tuần 9+ (>56 ngày): Base Phase (Xây nền tảng Aerobic)
    """
    if not race_date_str:
        return "Base Phase (Chưa có mục tiêu cụ thể)"
        
    try:
        tz = pytz.timezone(timezone_str)
        today = datetime.now(tz).date()
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
        
        days_left = (race_date - today).days
        
        if days_left <= 0:
            return "Race Week (Tuần thi đấu)"
            
        # Làm tròn lên để ra số tuần chẵn (VD: 34 ngày = 4.85 -> Tuần 5)
        weeks_left = math.ceil(days_left / 7.0)
        
        if weeks_left <= 2:
            return f"Taper Phase (Còn {weeks_left} tuần)"
        elif weeks_left <= 4:
            return f"Peak Phase (Còn {weeks_left} tuần)"
        elif weeks_left <= 8:
            return f"Build Phase (Còn {weeks_left} tuần)"
        else:
            return f"Base Phase (Còn {weeks_left} tuần)"
    except Exception as e:
        return "Base Phase (Lỗi tính toán ngày)"
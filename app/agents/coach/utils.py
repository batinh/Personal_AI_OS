import numpy as np
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
import math
import os
import pandas as pd

from app.core.logging_conf import get_module_logger
from app.core.timezone_utils import get_local_tz

logger = get_module_logger("coach")


def calculate_trimp(
    duration_minutes: float,
    avg_hr: float,
    max_hr: int = 185,
    rest_hr: int = 55,
    gender: str = "male",
) -> dict:
    """
    Calculate Training Impulse (TRIMP) using Bannister's method.
    Male:   TRIMP = duration × HRR × 0.64 × e^(1.92 × HRR)
    Female: TRIMP = duration × HRR × 0.86 × e^(1.67 × HRR)
    """
    if avg_hr == 0 or duration_minutes == 0:
        return {"trimp": 0, "intensity_level": "No Data"}
    try:
        hrr = max(0.0, (avg_hr - rest_hr) / (max_hr - rest_hr))
        # Bannister coefficients by gender
        if gender == "female":
            coeff, exp_factor = 0.86, 1.67
        else:  # male (default)
            coeff, exp_factor = 0.64, 1.92
        weight = coeff * np.exp(exp_factor * hrr)
        trimp = duration_minutes * hrr * weight
        trimp_rounded = round(trimp, 2)
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

    # Thresholds per Hulin et al. 2016: 1.3–1.5 is acceptable build range,
    # not a warning zone. Only flag caution at >1.5, danger at >1.7.
    if acwr < 0.8:
        status = "Under-training (Losing fitness)"
    elif acwr <= 1.3:
        status = "Sweet Spot (Optimal)"
    elif acwr <= 1.5:
        status = "Build Phase (Acceptable)"
    elif acwr <= 1.7:
        status = "Overreaching (Caution needed)"
    else:
        status = "Danger Zone (High Injury Risk - Need Recovery)"

    return {"acwr": acwr, "status": status}


def calculate_efficiency_factor(avg_speed_mpm: float, avg_hr: float) -> float:
    """
    Calculate Efficiency Factor (EF).
    Formula: EF = Speed (meters/min) / Average HR
    Indicates cardiovascular efficiency.
    """
    if avg_hr == 0:
        return 0.0
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
    ef1 = calculate_efficiency_factor(
        first_half["Velocity_m_s"].mean() * 60, first_half["HR_bpm"].mean()
    )
    ef2 = calculate_efficiency_factor(
        second_half["Velocity_m_s"].mean() * 60, second_half["HR_bpm"].mean()
    )

    decoupling = 0.0
    if ef1 > 0:
        # Calculate percentage drop in efficiency
        decoupling = ((ef1 - ef2) / ef1) * 100

    return round(decoupling, 2)


def calculate_hr_zones(max_hr: int, rest_hr: int) -> dict:
    """
    Calculate 5 Karvonen HR Zones based on Heart Rate Reserve (HRR).
    Each zone uses % of HRR + resting HR as the floor.
    """
    hrr = max_hr - rest_hr
    return {
        "zone1": {
            "name": "Active Recovery",
            "min": round(rest_hr + 0.50 * hrr),
            "max": round(rest_hr + 0.60 * hrr),
        },
        "zone2": {
            "name": "Aerobic Base",
            "min": round(rest_hr + 0.60 * hrr),
            "max": round(rest_hr + 0.70 * hrr),
        },
        "zone3": {
            "name": "Aerobic Tempo",
            "min": round(rest_hr + 0.70 * hrr),
            "max": round(rest_hr + 0.80 * hrr),
        },
        "zone4": {
            "name": "Threshold/Lactate",
            "min": round(rest_hr + 0.80 * hrr),
            "max": round(rest_hr + 0.90 * hrr),
        },
        "zone5": {
            "name": "VO2max / Speed",
            "min": round(rest_hr + 0.90 * hrr),
            "max": max_hr,
        },
    }


def calculate_lthr_zones(lthr_bpm: int) -> dict:
    """
    Calculate 7 Joe Friel HR Zones based on Lactate Threshold Heart Rate (LTHR).
    Boundaries: Z1<70%, Z2 70-85%, Z3 85-90%, Z4 90-95%, Z5a 95-98%, Z5b 98-102%, Z5c >102% LTHR.
    """
    lthr = lthr_bpm
    return {
        "zone1": {
            "name": "Recovery",
            "min": 0,
            "max": round(lthr * 0.70),
            "pct": "<70% LTHR",
        },
        "zone2": {
            "name": "Aerobic",
            "min": round(lthr * 0.70),
            "max": round(lthr * 0.85),
            "pct": "70-85% LTHR",
        },
        "zone3": {
            "name": "Tempo",
            "min": round(lthr * 0.85),
            "max": round(lthr * 0.90),
            "pct": "85-90% LTHR",
        },
        "zone4": {
            "name": "Sub-Threshold",
            "min": round(lthr * 0.90),
            "max": round(lthr * 0.95),
            "pct": "90-95% LTHR",
        },
        "zone5a": {
            "name": "VO2max (5a)",
            "min": round(lthr * 0.95),
            "max": round(lthr * 0.98),
            "pct": "95-98% LTHR",
        },
        "zone5b": {
            "name": "Anaerobic (5b)",
            "min": round(lthr * 0.98),
            "max": round(lthr * 1.02),
            "pct": "98-102% LTHR",
        },
        "zone5c": {
            "name": "Max Speed (5c)",
            "min": round(lthr * 1.02),
            "max": 9999,
            "pct": ">102% LTHR",
        },
    }


def format_hr_zones_for_prompt(zones: dict) -> str:
    """Format Karvonen HR zones dict into a compact string block for prompt injection."""
    lines = []
    for k, v in zones.items():
        lines.append(f"  {k.upper()} ({v['name']}): {v['min']}–{v['max']} bpm")
    return "\n".join(lines)


def format_lthr_zones_for_prompt(zones: dict) -> str:
    """Format Joe Friel LTHR zones dict into a compact string block for prompt injection."""
    lines = []
    for k, v in zones.items():
        max_str = f">{v['min']} bpm" if v["max"] >= 9999 else f"{v['max']} bpm"
        lines.append(f"  {k.upper()} ({v['name']}): {v['min']}–{max_str}  ({v['pct']})")
    return "\n".join(lines)


def calculate_power_zones(rftp_watts: int) -> dict:
    """
    Calculate 6 Stryd Power Zones based on Running FTP (rFTP).
    Boundaries: Z1<60%, Z2 60-75%, Z3 75-88%, Z4 88-93%, Z5 93-110%, Z6 >110% rFTP.
    """
    r = rftp_watts
    return {
        "zone1": {
            "name": "Recovery",
            "min": 0,
            "max": round(r * 0.60),
            "pct": "<60% rFTP",
        },
        "zone2": {
            "name": "Easy / Aerobic",
            "min": round(r * 0.60),
            "max": round(r * 0.75),
            "pct": "60-75% rFTP",
        },
        "zone3": {
            "name": "Tempo / LT",
            "min": round(r * 0.75),
            "max": round(r * 0.88),
            "pct": "75-88% rFTP",
        },
        "zone4": {
            "name": "Sub-Threshold",
            "min": round(r * 0.88),
            "max": round(r * 0.93),
            "pct": "88-93% rFTP",
        },
        "zone5": {
            "name": "VO2max",
            "min": round(r * 0.93),
            "max": round(r * 1.10),
            "pct": "93-110% rFTP",
        },
        "zone6": {
            "name": "Anaerobic / Max",
            "min": round(r * 1.10),
            "max": 9999,
            "pct": ">110% rFTP",
        },
    }


def format_power_zones_for_prompt(zones: dict) -> str:
    """Format Stryd power zones dict into a compact string block for prompt injection."""
    lines = []
    for v in zones.values():
        max_str = f">{v['min']}W" if v["max"] >= 9999 else f"{v['max']}W"
        lines.append(f"  {v['name']} ({v['pct']}): {v['min']}–{max_str}")
    return "\n".join(lines)


def calculate_pace_zones(threshold_pace_sec_per_km: int) -> dict:
    """
    Calculate Pace Zones from Lactate Threshold Pace (LT2/T-pace).
    Based on Jack Daniels' Running Formula velocity percentages.
    threshold_pace_sec_per_km: e.g. 310 = 5:10/km (seconds per km)
    """
    t = threshold_pace_sec_per_km

    def fmt(sec: float) -> str:
        s = int(round(sec))
        return f"{s // 60}:{s % 60:02d}/km"

    return {
        "recovery": {
            "name": "Recovery (Zone 1)",
            "range": f"{fmt(t * 1.35)}–{fmt(t * 1.25)}",
        },
        "easy": {"name": "Easy / Zone 2", "range": f"{fmt(t * 1.25)}–{fmt(t * 1.15)}"},
        "marathon": {
            "name": "Marathon Pace (MP)",
            "range": f"{fmt(t * 1.10)}–{fmt(t * 1.05)}",
        },
        "threshold": {
            "name": "Lactate Threshold (LT)",
            "range": f"{fmt(t * 1.05)}–{fmt(t * 0.97)}",
        },
        "interval": {
            "name": "VO2max Interval (I)",
            "range": f"{fmt(t * 0.97)}–{fmt(t * 0.90)}",
        },
        "race": {
            "name": "Race / Speed (R)",
            "range": f"{fmt(t * 0.90)}–{fmt(t * 0.85)}",
        },
    }


def format_pace_zones_for_prompt(zones: dict) -> str:
    """Format pace zones dict into a compact string block for prompt injection."""
    lines = []
    for v in zones.values():
        lines.append(f"  {v['name']}: {v['range']}")
    return "\n".join(lines)


def calculate_training_phase(
    race_date_str: str,
    race_distance_km: float = 21.1,
    timezone_str: str = os.getenv("TZ", "Asia/Ho_Chi_Minh"),
) -> dict:
    """
    Calculate current Training Phase, Microcycle, and Taper Factor
    based on the upcoming race date AND race distance.

    Phase boundaries by race distance (evidence-based):
    - Full Marathon (42km+): Taper=3w, Peak=5w, Build=8w, Base=rest
    - Half Marathon (21km):  Taper=2w, Peak=4w, Build=6w, Base=rest
    - 10K (10km):            Taper=1w, Peak=3w, Build=4w, Base=rest
    - 5K (5km):              Taper=1w, Peak=2w, Build=3w, Base=rest

    Returns taper_volume_factor: 1.0 = full load, 0.25 = race week.
    """
    if not race_date_str:
        return {
            "phase": "Base Phase",
            "weeks_left": 99,
            "microcycle": "Load",
            "taper_factor": 1.0,
        }

    try:
        tz = pytz.timezone(timezone_str)
        today = datetime.now(tz).date()
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()

        days_left = (race_date - today).days
        if days_left <= 0:
            return {
                "phase": "Race Week",
                "weeks_left": 0,
                "microcycle": "Race",
                "taper_factor": 0.0,
            }

        weeks_left = math.ceil(days_left / 7.0)

        # Phase boundaries based on race distance
        if race_distance_km >= 42:
            taper_w, peak_w, build_w = 3, 5, 8
        elif race_distance_km >= 21:
            taper_w, peak_w, build_w = 2, 4, 6
        elif race_distance_km >= 10:
            taper_w, peak_w, build_w = 1, 3, 4
        else:
            taper_w, peak_w, build_w = 1, 2, 3

        # Determine macro phase
        if weeks_left <= taper_w:
            phase_name = "Taper Phase"
        elif weeks_left <= taper_w + peak_w:
            phase_name = "Peak Phase"
        elif weeks_left <= taper_w + peak_w + build_w:
            phase_name = "Build Phase"
        else:
            phase_name = "Base Phase"

        # Structured taper volume factor
        if weeks_left == 1:
            taper_factor = 0.25  # Race week
        elif weeks_left == 2 and taper_w >= 2:
            taper_factor = 0.50  # Week -2
        elif weeks_left == 3 and taper_w >= 3:
            taper_factor = 0.75  # Week -3
        else:
            taper_factor = 1.0

        # Microcycle: 3 load : 1 cutback, always cutback in taper
        is_cutback = (weeks_left % 4 == 1) or (weeks_left <= taper_w)
        microcycle_type = (
            "Cutback / Recovery Week" if is_cutback else "Load / Progression Week"
        )

        return {
            "phase": f"{phase_name} (Còn {weeks_left} tuần)",
            "weeks_left": weeks_left,
            "microcycle": microcycle_type,
            "taper_factor": taper_factor,
        }
    except Exception as e:
        logger.error(f"[UTILS] Phase calculation error: {e}")
        return {
            "phase": "Error Phase",
            "weeks_left": 99,
            "microcycle": "Load",
            "taper_factor": 1.0,
        }


def debug_log_prompt(title: str, content: str):
    """
    Standardize the logging of AI Prompts.
    Only active when DEBUG_PROMPTS=true in the environment variables.
    Keeps the Agent and Scheduler modules clean from excessive logging logic.
    """
    if os.getenv("DEBUG_PROMPTS", "false").lower() == "true":
        logger.info(
            f"\n========== [{title}] ==========\n{content}\n=============================================="
        )


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
    safe_volume_limit = (
        round(historical_avg_volume * 1.15, 1) if historical_avg_volume > 0 else 30.0
    )

    chronic_load = loads.get("chronic_load_28d", 0)
    current_acute = loads.get("acute_load_7d", 0)
    max_safe_acute = chronic_load * 1.3
    safe_trimp_remaining = round(max_safe_acute - current_acute, 1)
    if safe_trimp_remaining < 0:
        safe_trimp_remaining = 0

    db_target = get_weekly_target(user_id, week_start_date)

    return {
        "1_historical_avg_volume": historical_avg_volume,
        "2_safe_volume_limit": safe_volume_limit,
        "3_safe_trimp_remaining": safe_trimp_remaining,
        "4_standard_plan_goal": db_target["standard_target_km"] if db_target else None,
        "5_actual_target_km": db_target["actual_target_km"] if db_target else None,
    }


def get_formatted_weekly_context(user_id: str) -> str:
    """
    Format the weekly volume context into a string block for AI Prompts.
    Shared across both Scheduler (Morning Standup) and Agent (Telegram Chat) flows.
    """
    tz = get_local_tz()
    now = datetime.now(tz)

    # Calculate the Monday of the current week
    monday = now - timedelta(days=now.weekday())
    week_start_str = monday.strftime("%Y-%m-%d")

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

_RETRYABLE_ERRORS = (
    "503",
    "504",
    "429",
    "Unavailable",  # Google server overload
    "DEADLINE_EXCEEDED",  # Google server deadline (504 variant)
    "timed out",
    "timeout",  # Network/SSL timeout
    "ssl",
    "SSL",
    "handshake",  # TLS handshake failure
)


def send_message_with_retry(chat_session, message, max_retries=3):
    """
    Canonical retry wrapper for all Gemini chat calls.
    Retries on: 503/429, SSL/TLS handshake timeouts, MALFORMED_RESPONSE.
    Non-retryable errors (invalid API key, etc.) fail immediately.
    """
    for attempt in range(max_retries):
        try:
            response = chat_session.send_message(message)

            # Detect MALFORMED_RESPONSE — model stopped mid-generation, retry
            finish = None
            try:
                finish = (
                    response.candidates[0].finish_reason
                    if response.candidates
                    else None
                )
            except Exception:
                pass
            if finish is not None and "MALFORMED" in str(finish):
                if attempt < max_retries - 1:
                    logger.warning(
                        f"[API RESILIENCE] MALFORMED_RESPONSE — response truncated. Retrying... (Attempt {attempt + 1}/{max_retries})"
                    )
                    continue
                logger.error(
                    "[API RESILIENCE] MALFORMED_RESPONSE persists after all retries."
                )

            # DEBUG: when enabled, log request+response previews for prompt review
            try:
                val = os.getenv("DEBUG_PROMPTS")
                debug_enabled = val is not None and (
                    val.strip() == ""
                    or val.strip().lower() in ("1", "true", "yes", "on")
                )
                if debug_enabled:
                    req_preview = (message or "")[:2000]
                    resp_text = getattr(response, "text", None)
                    resp_preview = (resp_text or "")[:2000]
                    try:
                        cand_preview = (
                            str(response.candidates[0])[:1000]
                            if getattr(response, "candidates", None)
                            else None
                        )
                    except Exception:
                        cand_preview = None
                    logger.debug(f"[API DEBUG] Request preview={req_preview!r}")
                    logger.debug(f"[API DEBUG] Response preview={resp_preview!r}")
                    if cand_preview:
                        logger.debug(f"[API DEBUG] Candidate preview={cand_preview!r}")
            except Exception:
                logger.debug("[API DEBUG] Failed to build request/response preview")

            return response

        except Exception as e:
            error_msg = str(e)
            exc_type = type(e).__name__
            if any(token in error_msg for token in _RETRYABLE_ERRORS):
                if attempt < max_retries - 1:
                    # Server-side overload (504/503) needs longer back-off than network blips
                    _server_error = any(
                        t in error_msg
                        for t in ("503", "504", "DEADLINE_EXCEEDED", "Unavailable")
                    )
                    wait_time = min(
                        5 * (2**attempt) if _server_error else 2**attempt, 60
                    )
                    logger.warning(
                        "[API RESILIENCE] Transient error %s: %s. Retrying in %ds... (Attempt %d/%d)",
                        exc_type,
                        error_msg[:120],
                        wait_time,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        "[API RESILIENCE] Max retries exhausted. %s: %s",
                        exc_type,
                        error_msg[:120],
                    )
                    raise e
            else:
                logger.debug(
                    "[API RESILIENCE] Non-retryable error %s: %s",
                    exc_type,
                    error_msg[:120],
                )
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
    hr_zones_text: str = ""
    pace_zones_text: str = ""
    taper_factor: float = 1.0


def build_agent_context(
    user_id: str, config: dict, now: datetime = None
) -> AgentContext:
    """Single Source of Truth for gathering and formatting agent context for all flows."""
    from app.core.database import get_training_loads, get_weekly_volume
    from app.agents.coach.prompts import (
        build_system_instruction,
        get_shared_context_block,
    )

    tz = get_local_tz()
    if now is None:
        now = datetime.now(tz)

    race_date_str = config.get("race_date", "")
    race_distance_km = float(config.get("race_distance_km", 21.1))
    phase_info = calculate_training_phase(race_date_str, race_distance_km)
    phase_text = f"{phase_info['phase']} | Cycle: {phase_info['microcycle']}"
    taper_factor = phase_info.get("taper_factor", 1.0)
    countdown_text = (
        f"Còn {phase_info['weeks_left']} tuần đến ngày đua."
        if race_date_str
        else "Duy trì thể lực."
    )

    loads = get_training_loads(user_id)
    acwr_data = calculate_acwr(
        loads.get("acute_load_7d", 0), loads.get("chronic_load_28d", 0)
    )
    acwr_text = f"{acwr_data['acwr']} ({acwr_data['status']})"
    actual_volume = get_weekly_volume(user_id, now)
    weekly_decision_context = get_formatted_weekly_context(user_id)

    # Compute HR zones — prefer Joe Friel LTHR model when lthr_bpm is configured
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    lthr_bpm = int(config.get("lthr_bpm", 0))
    if lthr_bpm > 0:
        hr_zones_text = format_lthr_zones_for_prompt(calculate_lthr_zones(lthr_bpm))
        hr_zones_label = f"JOE FRIEL — LTHR {lthr_bpm} bpm"
    else:
        hr_zones_text = format_hr_zones_for_prompt(calculate_hr_zones(max_hr, rest_hr))
        hr_zones_label = "KARVONEN — HRR"

    # Compute Power zones (Stryd) — only when rftp_watts is configured
    rftp_watts = int(config.get("rftp_watts", 0))
    if rftp_watts > 0:
        power_zones_text = format_power_zones_for_prompt(
            calculate_power_zones(rftp_watts)
        )
    else:
        power_zones_text = ""

    # Compute Pace zones (optional — only if threshold_pace_per_km is configured)
    threshold_pace = int(config.get("threshold_pace_per_km", 0))
    if threshold_pace > 0:
        pace_zones = calculate_pace_zones(threshold_pace)
        pace_zones_text = format_pace_zones_for_prompt(pace_zones)
    else:
        pace_zones_text = "Chưa cấu hình ngưỡng pace (threshold_pace_per_km)."

    gender = config.get("gender", "male")

    system_inst = build_system_instruction(
        config.get("system_instruction", ""),
        config.get("user_profile", ""),
        max_hr,
        rest_hr,
        gender,
        hr_zones_text,
        pace_zones_text,
        taper_factor,
        rftp_watts=rftp_watts,
        lthr_bpm=lthr_bpm,
        hr_zones_label=hr_zones_label,
        power_zones_text=power_zones_text,
    )
    shared_context = get_shared_context_block(
        now.strftime("%A, %d/%m/%Y"),
        user_id,
        phase_text,
        countdown_text,
        acwr_text,
        actual_volume,
        weekly_decision_context,
        hr_zones_text,
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
        hr_zones_text=hr_zones_text,
        pace_zones_text=pace_zones_text,
        taper_factor=taper_factor,
    )

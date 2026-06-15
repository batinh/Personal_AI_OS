from datetime import date, timedelta
from typing import Optional

from app.core.logging_conf import get_module_logger
from app.core.user_context import get_primary_user_id
from app.core.notification import send_telegram_msg
from app.core.database import (
    get_athlete_state,
    get_garmin_daily_metrics,
    upsert_weekly_plan,
    get_pending_weekly_plan,
    update_weekly_plan_status,
    has_active_plan_this_week,
    get_db,
)
from app.agents.coach.schemas import WeeklyPlanResult
from app.agents.coach.utils import build_agent_context
from app.agents._prompt_telemetry import log_prompt_metrics

logger = get_module_logger("weekly_plan_gen")

_QUALITY_TYPES = {"Tempo", "Interval"}
_MAX_QUALITY_PER_WEEK = 2


def _current_week_monday() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")


def _upcoming_monday() -> str:
    """Return the Monday of the week that is about to start.

    On Sunday the scheduler generates next week's plan, so we return next Monday.
    Any other day of the week we return this week's Monday (plan is for the current week).
    """
    today = date.today()
    if today.weekday() == 6:  # Sunday → next Monday is tomorrow
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")


def _calculate_acwr(user_id: str) -> float:
    """Compute ACWR (7-day / 28-day load ratio) from run_activities."""
    try:
        with get_db() as conn:
            acute = conn.execute(
                "SELECT COALESCE(SUM(trimp_score), 0) FROM run_activities WHERE user_id=? AND start_date >= date('now','-7 days')",
                (user_id,),
            ).fetchone()[0]
            chronic_28 = conn.execute(
                "SELECT COALESCE(SUM(trimp_score), 0) FROM run_activities WHERE user_id=? AND start_date >= date('now','-28 days')",
                (user_id,),
            ).fetchone()[0]
        chronic_weekly = chronic_28 / 4 if chronic_28 > 0 else 1.0
        return round(acute / chronic_weekly, 3) if chronic_weekly > 0 else 1.0
    except Exception as e:
        logger.warning(f"[WEEKLY_PLAN] ACWR calc error: {e}")
        return 1.0


def _compute_max_weekly_km(acwr: float, current_weekly_km: float, phase: str) -> float:
    """Apply volume ceiling rules from the plan spec."""
    if acwr > 1.4:
        return round(current_weekly_km * 0.85, 1)
    if acwr > 1.3:
        return round(current_weekly_km * 1.0, 1)
    if "taper" in phase.lower() or "race" in phase.lower():
        return round(current_weekly_km * 0.5, 1)
    return round(current_weekly_km * 1.10, 1)


def _build_weekly_plan_prompt(config: dict, context_data: dict) -> str:
    race_date = config.get("race_date", "unknown")
    race_distance = config.get("race_distance_km", 21.1)
    target_time = config.get("race_target_time_min", "unknown")
    max_hr = config.get("max_hr", 185)
    rest_hr = config.get("rest_hr", 55)
    lthr = config.get("lthr_bpm", 160)

    acwr = context_data.get("acwr", 1.0)
    readiness = context_data.get("readiness_score", "unknown")
    hrv_status = context_data.get("hrv_status", "BALANCED")
    sleep_h = context_data.get("sleep_last_night_hours", "unknown")
    phase = context_data.get("phase", "Build")
    athlete_state = context_data.get("athlete_state", "healthy")
    weekly_km = context_data.get("current_weekly_km", 40)
    max_weekly_km = context_data.get("max_weekly_km", 44)
    week_start = context_data.get("week_start_date", _current_week_monday())
    countdown_days = context_data.get("countdown_days", 60)
    recovery_week_due = context_data.get("recovery_week_due", False)
    rpe_trend = context_data.get("rpe_last_3_runs", [])
    training_status = context_data.get("garmin_training_status", "MAINTAINING")

    readiness_instruction = _readiness_gate_instruction(readiness)
    taper_instruction = _taper_instruction(countdown_days)

    return f"""
Bạn là HLV marathon AI chuyên nghiệp. Hãy tạo giáo án 7 ngày cho tuần bắt đầu {week_start}.

## THÔNG TIN VĐV
- Cự ly đua: {race_distance}km | Ngày đua: {race_date} | Mục tiêu: {target_time} phút
- HR max: {max_hr} | HR nghỉ: {rest_hr} | LTHR: {lthr}
- Phase hiện tại: {phase} | Còn {countdown_days} ngày đến đua

## TRẠNG THÁI HIỆN TẠI
- Trạng thái VĐV: {athlete_state}
- Training Readiness: {readiness}/100
- HRV: {hrv_status} | Ngủ tối qua: {sleep_h}h
- Garmin Training Status: {training_status}
- ACWR: {acwr} (7d/28d load ratio)
- Khối lượng tuần trước: {weekly_km}km
- RPE 3 bài gần nhất: {rpe_trend}

## RÀNG BUỘC BẮT BUỘC
- Khối lượng tối đa tuần này: {max_weekly_km}km
- Tối đa {_MAX_QUALITY_PER_WEEK} bài chất lượng (Tempo/Interval/RacePace) mỗi tuần
- Tối thiểu 1 ngày nghỉ hoàn toàn
- Long Run phải vào Thứ Bảy hoặc Chủ Nhật
- Không xếp 2 bài chất lượng liên tiếp (phải có Easy/Rest ở giữa)
{readiness_instruction}
{taper_instruction}
{"- TUẦN PHỤC HỒI: giảm 25-30% khối lượng so với tuần trước" if recovery_week_due else ""}

## YÊU CẦU ĐẦU RA
Trả về JSON theo schema WeeklyPlanResult với đúng 7 ngày (Mon-Sun).
Tất cả title và description phải bằng tiếng Việt.
Giải thích logic trong training_rationale (3-4 câu tiếng Việt).
Liệt kê những điều chỉnh đã thực hiện trong adaptations_made.
""".strip()


def _readiness_gate_instruction(readiness) -> str:
    if readiness == "unknown" or readiness is None:
        return "- Không có dữ liệu readiness — lập kế hoạch theo mức trung bình"
    score = int(readiness)
    if score >= 80:
        return "- Readiness EXCELLENT (≥80): Có thể tăng 10% khối lượng hoặc thêm bài chất lượng thứ 2"
    if score >= 60:
        return "- Readiness GOOD (60-79): Thực hiện theo kế hoạch bình thường"
    if score >= 50:
        return "- Readiness MODERATE (50-59): Tối đa 1 bài chất lượng, đổi Interval → Tempo nếu cần"
    if score >= 40:
        return (
            "- Readiness LOW (40-49): Tất cả bài Hard → Easy. Không có bài chất lượng"
        )
    return "- Readiness POOR (<40): CHỈ Rest hoặc Recovery Run. Cấm Interval/Tempo"


def _taper_instruction(countdown_days: int) -> str:
    if countdown_days <= 7:
        return "- RACE WEEK: 25% khối lượng đỉnh, chỉ Easy + Rest, 0 bài chất lượng"
    if countdown_days <= 14:
        return "- TAPER WEEK -2: 50% khối lượng đỉnh, tối đa 1 bài chất lượng (bài hard cuối cùng)"
    if countdown_days <= 21:
        return (
            "- TAPER WEEK -3: 75% khối lượng đỉnh, 2 bài chất lượng, cường độ vẫn cao"
        )
    return ""


def generate_weekly_plan(user_id: str, config: dict) -> Optional[WeeklyPlanResult]:
    """
    Generate a weekly plan using Gemini structured output.
    Stores the result in weekly_plans as 'pending'.
    Returns None if plan should be skipped (sick, duplicate, etc.).
    """
    week_start = _upcoming_monday()

    existing = get_pending_weekly_plan(user_id, week_start)
    if existing or has_active_plan_this_week(user_id, week_start):
        logger.info(
            f"[WEEKLY_PLAN] Plan already exists for {user_id}/{week_start}, skipping"
        )
        return None

    athlete_state = get_athlete_state(user_id)
    if athlete_state in ("sick", "injured"):
        chat_id = get_primary_user_id()
        send_telegram_msg(
            chat_id,
            f"⚠️ Không tạo giáo án tuần này — trạng thái VĐV: <b>{athlete_state}</b>.\n"
            "Dùng /recover khi anh đã khỏe lại.",
        )
        logger.info(
            f"[WEEKLY_PLAN] Skipping plan gen for {user_id}: state={athlete_state}"
        )
        return None

    acwr = _calculate_acwr(user_id)
    today_str = date.today().strftime("%Y-%m-%d")
    garmin = get_garmin_daily_metrics(user_id, today_str) or {}
    ctx = build_agent_context(user_id, config)

    countdown_days = 60
    if config.get("race_date"):
        try:
            from datetime import datetime as dt

            race_date = dt.strptime(config["race_date"], "%Y-%m-%d").date()
            countdown_days = (race_date - date.today()).days
        except ValueError:
            pass

    week_num = (date.today() - date(date.today().year, 1, 1)).days // 7
    recovery_week_due = week_num % 4 == 0

    weekly_km = config.get("setup", {}).get("current_weekly_km") or 40.0
    phase = getattr(ctx, "phase_text", "Build")
    max_km = _compute_max_weekly_km(acwr, weekly_km, phase)

    context_data = {
        "acwr": acwr,
        "readiness_score": garmin.get("training_readiness_score"),
        "hrv_status": garmin.get("hrv_status", "BALANCED"),
        "sleep_last_night_hours": round(
            (garmin.get("sleep_duration_sec") or 0) / 3600, 1
        )
        or "unknown",
        "phase": phase,
        "athlete_state": athlete_state,
        "current_weekly_km": weekly_km,
        "max_weekly_km": max_km,
        "week_start_date": week_start,
        "countdown_days": countdown_days,
        "recovery_week_due": recovery_week_due,
        "garmin_training_status": garmin.get("training_status", "MAINTAINING"),
    }

    prompt = _build_weekly_plan_prompt(config, context_data)
    logger.info(
        f"[WEEKLY_PLAN] Generating plan for {user_id}, week {week_start}, ACWR={acwr}"
    )

    result = _call_gemini_for_plan(config, prompt)
    if not result:
        return None

    ai_output = result.model_dump_json(indent=2)
    plan_id = upsert_weekly_plan(user_id, week_start, ai_output)
    _write_plan_to_training_plans(user_id, result, plan_id)

    chat_id = get_primary_user_id()
    preview = _format_plan_preview(result)
    send_telegram_msg(chat_id, preview)
    logger.info(f"[WEEKLY_PLAN] Plan generated, saved, and sent to {user_id}")
    return result


def _call_gemini_for_plan(config: dict, prompt: str) -> Optional[WeeklyPlanResult]:
    """Call Gemini with structured output schema. Retries once on schema error."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            http_options=types.HttpOptions(timeout=120000)
        )  # 120s in ms
        model = config.get("model_name", "models/gemini-flash-latest")

        log_prompt_metrics(
            flow="coach.flows.weekly_plan",
            system_inst="",
            user_prompt=prompt,
            model=model,
        )
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WeeklyPlanResult,
            ),
        )
        result = WeeklyPlanResult.model_validate_json(response.text)
        _validate_plan_constraints(result)
        return result

    except Exception as e:
        logger.error(f"[WEEKLY_PLAN] Gemini call failed: {e}")
        return None


def _validate_plan_constraints(plan: WeeklyPlanResult) -> None:
    """Log warnings if the AI violated hard constraints — doesn't raise."""
    quality_count = sum(1 for d in plan.days if d.workout_type in _QUALITY_TYPES)
    if quality_count > _MAX_QUALITY_PER_WEEK:
        logger.warning(
            f"[WEEKLY_PLAN] Constraint violation: {quality_count} quality sessions > max {_MAX_QUALITY_PER_WEEK}"
        )

    rest_count = sum(1 for d in plan.days if d.workout_type == "Rest")
    if rest_count < 1:
        logger.warning("[WEEKLY_PLAN] Constraint violation: no rest day in the week")

    for i in range(len(plan.days) - 1):
        if (
            plan.days[i].workout_type in _QUALITY_TYPES
            and plan.days[i + 1].workout_type in _QUALITY_TYPES
        ):
            logger.warning(
                f"[WEEKLY_PLAN] Constraint violation: back-to-back quality sessions on days {i+1} and {i+2}"
            )


def _format_plan_preview(plan: WeeklyPlanResult) -> str:
    """Format compact plan preview for Telegram (< 3500 chars)."""
    from datetime import datetime as dt

    week_start = dt.strptime(plan.week_start_date, "%Y-%m-%d")
    week_end = week_start + timedelta(days=6)
    header = (
        f"🗓 <b>Giáo án tuần {week_start.strftime('%d/%m')}–{week_end.strftime('%d/%m')}</b>\n"
        f"📊 Tổng: {plan.week_total_km}km | ACWR dự kiến: {plan.acwr_projection}\n\n"
    )

    day_names_vi = [
        "Thứ Hai",
        "Thứ Ba",
        "Thứ Tư",
        "Thứ Năm",
        "Thứ Sáu",
        "Thứ Bảy",
        "Chủ Nhật",
    ]
    lines = [header]
    for i, day in enumerate(plan.days):
        day_label = day_names_vi[i] if i < len(day_names_vi) else f"Ngày {i+1}"
        if day.workout_type == "Rest":
            lines.append(f"📅 <b>{day_label}</b>: Nghỉ\n")
        else:
            details = []
            if day.target_distance_km:
                details.append(f"{day.target_distance_km}km")
            if day.target_pace_range:
                details.append(day.target_pace_range)
            if day.rpe_target:
                details.append(f"RPE{day.rpe_target}")
            detail_str = " | ".join(details)
            line = f"📅 <b>{day_label}</b>: {day.workout_type} {detail_str}\n   {day.title}"
            if day.nutrition_alert:
                line += f"\n   ⚡ {day.nutrition_alert}"
            lines.append(line + "\n")

    if plan.adaptations_made:
        lines.append("⚡ <i>Điều chỉnh:</i> " + "; ".join(plan.adaptations_made[:2]))

    lines.append(
        "\n✅ <i>Giáo án đã được lưu. Chat với coach để điều chỉnh nếu cần.</i>"
    )
    preview = "\n".join(lines)

    if len(preview) > 3400:
        preview = preview[:3400] + "\n..."
    return preview


def accept_weekly_plan(user_id: str, week_start: Optional[str] = None) -> str:
    """
    Accept the pending plan for the current week.
    Writes 7 rows to training_plans. Returns status message.
    """
    if week_start is None:
        week_start = _current_week_monday()

    plan_row = get_pending_weekly_plan(user_id, week_start)
    if not plan_row:
        return "⚠️ Không tìm thấy giáo án đang chờ duyệt. Dùng /plan để tạo mới."

    try:
        result = WeeklyPlanResult.model_validate_json(plan_row["ai_output"])
    except Exception as e:
        logger.error(f"[WEEKLY_PLAN] Failed to parse plan for accept: {e}")
        return "❌ Dữ liệu giáo án bị lỗi. Dùng /plan để tạo lại."

    _write_plan_to_training_plans(user_id, result, plan_row["id"])
    update_weekly_plan_status(user_id, week_start, "accepted")
    logger.info(f"[WEEKLY_PLAN] Plan accepted for {user_id}/{week_start}")
    return (
        f"✅ <b>Giáo án tuần đã được xác nhận!</b>\n"
        f"📊 {result.week_total_km}km trong 7 ngày. Chúc anh tập tốt! 💪"
    )


def reject_weekly_plan(
    user_id: str, reason: str = "", week_start: Optional[str] = None
) -> str:
    """
    Reject the pending plan. Stores reason and triggers regeneration.
    """
    if week_start is None:
        week_start = _current_week_monday()

    plan_row = get_pending_weekly_plan(user_id, week_start)
    if not plan_row:
        return "⚠️ Không tìm thấy giáo án đang chờ duyệt."

    update_weekly_plan_status(user_id, week_start, "rejected", rejected_reason=reason)
    logger.info(f"[WEEKLY_PLAN] Plan rejected for {user_id}/{week_start}: {reason}")

    # Trigger regeneration on next Sunday scheduler run (delete the weekly_plan row so dedup guard allows it)
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM weekly_plans WHERE user_id=? AND week_start_date=? AND status='rejected'",
                (user_id, week_start),
            )
    except Exception:
        pass

    return (
        "🔄 Đã ghi nhận. Giáo án mới sẽ được tạo lại vào Chủ Nhật 20:30.\n"
        "Hoặc dùng /plan để tạo ngay."
    )


def _write_plan_to_training_plans(
    user_id: str, result: WeeklyPlanResult, weekly_plan_id: int
) -> None:
    """Write accepted WeeklyPlanResult to training_plans table (7 rows)."""
    try:
        with get_db() as conn:
            for day in result.days:
                conn.execute(
                    """
                    INSERT INTO training_plans
                        (user_id, date, workout_title, description, status,
                         workout_type, target_distance_km, target_duration_min,
                         target_pace_range, target_hr_zone, target_hr_range,
                         rpe_target, nutrition_alert, weekly_plan_id, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, date) DO UPDATE SET
                        workout_title=excluded.workout_title,
                        description=excluded.description,
                        workout_type=excluded.workout_type,
                        target_distance_km=excluded.target_distance_km,
                        target_duration_min=excluded.target_duration_min,
                        target_pace_range=excluded.target_pace_range,
                        target_hr_zone=excluded.target_hr_zone,
                        target_hr_range=excluded.target_hr_range,
                        rpe_target=excluded.rpe_target,
                        nutrition_alert=excluded.nutrition_alert,
                        weekly_plan_id=excluded.weekly_plan_id,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        user_id,
                        day.date,
                        day.title,
                        day.description,
                        "planned",
                        day.workout_type,
                        day.target_distance_km,
                        day.target_duration_min,
                        day.target_pace_range,
                        day.target_hr_zone,
                        day.target_hr_range,
                        day.rpe_target,
                        day.nutrition_alert,
                        weekly_plan_id,
                    ),
                )
    except Exception as e:
        logger.error(f"[WEEKLY_PLAN] Failed to write plan to training_plans: {e}")

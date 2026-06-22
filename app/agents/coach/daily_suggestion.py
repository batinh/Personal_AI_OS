from typing import Optional

READINESS_THRESHOLDS = {"excellent": 80, "good": 60, "moderate": 50, "low": 40}
ACWR_SAFE_MAX = 1.3
ACWR_CRITICAL = 1.4

_SUGGESTION_TYPES = {
    "rest": {
        "workout_type": "Rest",
        "title_vi": "Nghỉ ngơi hoàn toàn",
        "description_vi": "Hôm nay cơ thể cần nghỉ ngơi. Đừng tập — ăn uống đủ chất và ngủ sớm.",
        "target_km": None,
        "target_pace_zone": None,
        "rpe_target": None,
    },
    "recovery": {
        "workout_type": "Recovery",
        "title_vi": "Chạy phục hồi nhẹ",
        "description_vi": "Chạy Z1 rất nhẹ, 20–30 phút. Không quan tâm pace — chỉ cần vận động nhẹ để kích thích lưu thông máu.",
        "target_km": 5.0,
        "target_pace_zone": "Z1",
        "rpe_target": 3,
    },
    "easy": {
        "workout_type": "Easy",
        "title_vi": "Chạy nhẹ aerobic",
        "description_vi": "Chạy Z2 thoải mái. Giữ HR trong vùng aerobic, có thể trò chuyện bình thường khi chạy.",
        "target_km": 8.0,
        "target_pace_zone": "Z2",
        "rpe_target": 5,
    },
    "easy_short": {
        "workout_type": "Easy",
        "title_vi": "Chạy nhẹ duy trì",
        "description_vi": "Chạy ngắn Z2 để duy trì thói quen. Không cần tăng khối lượng hôm nay.",
        "target_km": 6.0,
        "target_pace_zone": "Z2",
        "rpe_target": 4,
    },
    "long_run": {
        "workout_type": "LongRun",
        "title_vi": "Long Run cuối tuần",
        "description_vi": "Bài dài aerobic. Giữ pace ổn định, HR trong Z2. Mang đủ nước và gel nếu > 15km.",
        "target_km": 16.0,
        "target_pace_zone": "Z2",
        "rpe_target": 6,
    },
    "tempo": {
        "workout_type": "Tempo",
        "title_vi": "Chạy Tempo",
        "description_vi": "Ngày tốt để chạy ngưỡng. Khởi động 10 phút, chạy Tempo 20–30 phút, hạ nhiệt 10 phút.",
        "target_km": 10.0,
        "target_pace_zone": "Z3-4",
        "rpe_target": 7,
    },
}


def compute_daily_suggestion(
    readiness_score: Optional[int],
    acwr: Optional[float],
    recent_runs: list,
    athlete_state: str,
    day_of_week: int = 0,
    days_since_last_run: int = 1,
    today_plan: Optional[dict] = None,
) -> dict:
    """
    Pure function — no I/O, no LLM, deterministic.
    Returns a suggestion dict with keys: workout_type, title_vi, description_vi,
    target_km, target_pace_zone, rpe_target, reason.

    Rule priority (first match wins):
    1. sick/injured → Rest
    2. acwr > 1.4 → Rest (overreach risk)
    3. readiness < 40 → Recovery run
    4. days_since_last_run >= 3 → Easy (habit maintenance)
    5. acwr > 1.3 → Easy short (no volume increase)
    6. readiness 40–59 → Easy short
    7. weekend + readiness >= 60 → Long run
    8. readiness >= 80 + no recent quality → Tempo
    9. readiness 60–79 → Easy
    10. fallback → Easy
    """
    planned_type = today_plan.get("workout_type") if today_plan else None

    if athlete_state in ("sick", "injured"):
        s = dict(_SUGGESTION_TYPES["rest"])
        if athlete_state == "sick":
            s["description_vi"] = (
                "Anh đang ốm — hãy nghỉ hoàn toàn, uống nhiều nước và ăn uống đủ chất."
            )
        else:
            s["description_vi"] = (
                "Đang chấn thương — không chạy. Tham khảo bác sĩ/PT trước khi quay lại tập."
            )
        s["reason"] = f"athlete_state={athlete_state}"
        return s

    if acwr is not None and acwr > ACWR_CRITICAL:
        s = dict(_SUGGESTION_TYPES["rest"])
        s["title_vi"] = "Nghỉ — nguy cơ quá tải"
        s["description_vi"] = (
            f"ACWR = {acwr:.2f} (ngưỡng nguy hiểm > 1.4). Nghỉ hôm nay để tránh chấn thương."
        )
        s["reason"] = f"acwr={acwr:.2f} > {ACWR_CRITICAL}"
        return s

    eff_readiness = readiness_score if readiness_score is not None else 65

    if eff_readiness < READINESS_THRESHOLDS["low"]:
        s = dict(_SUGGESTION_TYPES["recovery"])
        s["reason"] = f"readiness={eff_readiness} < {READINESS_THRESHOLDS['low']}"
        return s

    if days_since_last_run >= 3:
        s = dict(_SUGGESTION_TYPES["easy"])
        s["description_vi"] = (
            f"Đã {days_since_last_run} ngày không chạy — chạy nhẹ hôm nay để duy trì thói quen."
        )
        s["reason"] = f"days_since_last_run={days_since_last_run}"
        return s

    if acwr is not None and acwr > ACWR_SAFE_MAX:
        s = dict(_SUGGESTION_TYPES["easy_short"])
        s["description_vi"] = (
            f"ACWR = {acwr:.2f} — không tăng khối lượng hôm nay. Chạy nhẹ và ngắn."
        )
        s["reason"] = f"acwr={acwr:.2f} > {ACWR_SAFE_MAX}"
        return s

    if READINESS_THRESHOLDS["low"] <= eff_readiness < READINESS_THRESHOLDS["good"]:
        s = dict(_SUGGESTION_TYPES["easy_short"])
        s["reason"] = f"readiness={eff_readiness} (low/moderate)"
        return s

    is_weekend = day_of_week in (5, 6)
    if is_weekend and eff_readiness >= READINESS_THRESHOLDS["good"]:
        s = dict(_SUGGESTION_TYPES["long_run"])
        s["reason"] = f"weekend day={day_of_week}, readiness={eff_readiness}"
        return s

    recent_quality = sum(
        1
        for r in recent_runs
        if r.get("workout_type_detected") in ("tempo", "interval")
        or (r.get("gcs_score") or 0) >= 7
    )

    if eff_readiness >= READINESS_THRESHOLDS["excellent"] and recent_quality == 0:
        s = dict(_SUGGESTION_TYPES["tempo"])
        s["reason"] = (
            f"readiness={eff_readiness} (excellent), no recent quality session"
        )
        return s

    s = dict(_SUGGESTION_TYPES["easy"])
    s["reason"] = f"readiness={eff_readiness} (good/default)"

    if planned_type and planned_type not in ("Rest", "Recovery"):
        s["plan_conflict"] = (
            f"Giáo án: {today_plan.get('workout_title', planned_type)}. "
            "Dữ liệu cơ thể hôm nay cho thấy nên chạy nhẹ hơn — coach sẽ điều chỉnh trong buổi sáng."
        )

    return s


def _format_garmin_wellness(garmin_data: Optional[dict]) -> str:
    """Render a compact Vietnamese wellness line from Garmin metrics, or '' if none."""
    if not garmin_data:
        return ""

    parts = []
    readiness = garmin_data.get("training_readiness_score")
    if readiness is not None:
        parts.append(f"⚡ Sẵn sàng {readiness}")
    sleep_score = garmin_data.get("sleep_score")
    if sleep_score is not None:
        parts.append(f"😴 Ngủ {sleep_score}")
    hrv = garmin_data.get("hrv_last_night")
    if hrv is not None:
        parts.append(f"❤️ HRV {hrv}ms")
    battery = garmin_data.get("body_battery_morning")
    if battery is not None:
        parts.append(f"🔋 Pin {battery}")
    resting_hr = garmin_data.get("resting_hr")
    if resting_hr is not None:
        parts.append(f"💓 RHR {resting_hr}")

    if not parts:
        return ""
    return "⌚ <b>Garmin:</b> " + " · ".join(parts)


def format_daily_suggestion_for_briefing(
    suggestion: dict,
    garmin_data: Optional[dict] = None,
    has_pending_plan: bool = False,
) -> str:
    """Format a daily suggestion dict into a Vietnamese Telegram message block."""
    lines = ["💡 <b>Gợi ý hôm nay (chưa có giáo án):</b>"]

    title = suggestion.get("title_vi", "Hoạt động thể thao")
    desc = suggestion.get("description_vi", "")
    target_km = suggestion.get("target_km")
    pace_zone = suggestion.get("target_pace_zone")
    rpe = suggestion.get("rpe_target")
    plan_conflict = suggestion.get("plan_conflict")

    details = []
    if target_km:
        details.append(f"{target_km}km")
    if pace_zone:
        details.append(f"@ {pace_zone}")
    if rpe:
        details.append(f"RPE {rpe}")

    detail_str = " | ".join(details)
    if detail_str:
        lines.append(f"   <b>{title}</b> — {detail_str}")
    else:
        lines.append(f"   <b>{title}</b>")

    if desc:
        lines.append(f"   {desc}")

    wellness = _format_garmin_wellness(garmin_data)
    if wellness:
        lines.append("")
        lines.append(wellness)

    if plan_conflict:
        lines.append(f"\n⚠️ {plan_conflict}")

    lines.append("")
    if has_pending_plan:
        lines.append("⏳ Anh có giáo án đang chờ xác nhận. Dùng /accept để bắt đầu.")
    else:
        lines.append("ℹ️ Chưa có giáo án tuần này. Dùng /plan để tạo giáo án AI ngay.")
    return "\n".join(lines)

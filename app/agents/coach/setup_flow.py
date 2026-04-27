import json

from app.core.database import (
    get_setup_session, upsert_setup_session, complete_setup_session,
    abandon_stale_setup_sessions,
)
from app.core.config import load_config, save_config
from app.core.logging_conf import get_module_logger
from app.agents.coach.setup_validators import (
    validate_distance, validate_date, validate_time,
    validate_kmweek, validate_days, validate_rest_days,
)

logger = get_module_logger("setup_flow")

_STEP_PROMPTS = [
    (
        1,
        "race_distance_km",
        (
            "🏁 <b>Bước 1/6: Cự ly đua</b>\n\n"
            "Anh đang luyện tập cho cự ly nào?\n"
            "→ Nhập số km hoặc tên: <code>10</code>, <code>21.1</code>, <code>42.2</code>, "
            "<code>HM</code>, <code>FM</code>"
        ),
    ),
    (
        2,
        "race_date",
        (
            "📅 <b>Bước 2/6: Ngày đua</b>\n\n"
            "Ngày thi đấu của anh là khi nào?\n"
            "→ Nhập định dạng <code>DD/MM/YYYY</code>, ví dụ: <code>15/06/2026</code>"
        ),
    ),
    (
        3,
        "race_target_time_min",
        (
            "⏱ <b>Bước 3/6: Mục tiêu hoàn thành</b>\n\n"
            "Anh muốn hoàn thành trong bao lâu?\n"
            "→ Nhập <code>H:MM</code> (ví dụ: <code>1:45</code>) hoặc số phút (<code>105</code>)"
        ),
    ),
    (
        4,
        "current_weekly_km",
        (
            "📊 <b>Bước 4/6: Khối lượng hiện tại</b>\n\n"
            "Hiện tại anh chạy khoảng bao nhiêu km mỗi tuần?\n"
            "→ Nhập số km, ví dụ: <code>35</code>"
        ),
    ),
    (
        5,
        "training_days_per_week",
        (
            "🗓 <b>Bước 5/6: Số ngày tập</b>\n\n"
            "Anh muốn tập mấy ngày mỗi tuần?\n"
            "→ Nhập số từ <code>3</code> đến <code>6</code>"
        ),
    ),
    (
        6,
        "preferred_rest_days",
        (
            "😴 <b>Bước 6/6: Ngày nghỉ</b>\n\n"
            "Anh thường nghỉ vào ngày nào trong tuần?\n"
            "→ Nhập tên ngày, ví dụ: <code>Thứ Hai, Thứ Sáu</code> hoặc <code>T2, T6</code>"
        ),
    ),
]


def _build_completion_message(data: dict) -> str:
    from datetime import date as date_type, datetime
    distance = data.get("race_distance_km", "?")
    race_date_str = data.get("race_date", "")
    target_min = data.get("race_target_time_min", 0)
    weekly_km = data.get("current_weekly_km", "?")
    training_days = data.get("training_days_per_week", "?")
    rest_days_raw = data.get("preferred_rest_days", [])

    day_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    rest_day_names = ", ".join(day_names[d] for d in rest_days_raw) if rest_days_raw else "?"

    target_fmt = f"{target_min // 60}:{target_min % 60:02d}" if target_min else "?"

    weeks_left = "?"
    if race_date_str:
        try:
            race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
            delta = (race_date - date_type.today()).days
            weeks_left = max(0, delta // 7)
        except ValueError:
            pass

    distance_label = f"{distance}km"
    if distance == 21.1:
        distance_label = "Half Marathon (21.1km)"
    elif distance == 42.2:
        distance_label = "Marathon (42.2km)"
    elif distance == 10.0:
        distance_label = "10K"
    elif distance == 5.0:
        distance_label = "5K"

    return (
        "✅ <b>Thiết lập hoàn tất!</b>\n\n"
        "📋 <b>Tóm tắt:</b>\n"
        f"• Cự ly: {distance_label}\n"
        f"• Ngày đua: {race_date_str.replace('-', '/')} (còn {weeks_left} tuần)\n"
        f"• Mục tiêu: {target_fmt}\n"
        f"• Khối lượng hiện tại: {weekly_km}km/tuần\n"
        f"• Ngày tập: {training_days} ngày/tuần\n"
        f"• Ngày nghỉ: {rest_day_names}\n\n"
        "🤖 Giáo án tuần đầu tiên sẽ được tạo vào <b>Chủ Nhật 20:30</b>.\n"
        "Dùng /status để xem trạng thái, /plan để tạo ngay."
    )


def is_setup_in_progress(user_id: str) -> bool:
    """Return True if the user has an active setup session in the DB."""
    session = get_setup_session(user_id)
    return session is not None


def get_step_prompt(step_num: int) -> str:
    """Return the Telegram prompt for a given step number."""
    for num, _, prompt in _STEP_PROMPTS:
        if num == step_num:
            return prompt
    return ""


def start_setup(user_id: str) -> str:
    """
    Start (or restart) a setup session. Returns the first step prompt.
    If called while a plan is active, the caller must have already confirmed invalidation.
    """
    upsert_setup_session(user_id, step=1, data={}, status="active")
    logger.info(f"[SETUP] Started setup for user {user_id}")
    intro = (
        "🏃 <b>Thiết lập Coach Dyno</b>\n\n"
        "Tôi sẽ hỏi anh 6 câu để tạo giáo án cá nhân hóa.\n"
        "Trả lời từng bước — anh có thể gõ /setup lại để bắt đầu từ đầu.\n\n"
    )
    return intro + get_step_prompt(1)


def advance_setup(user_id: str, user_reply: str) -> str:
    """
    Validate the user's reply for the current step, store it, and advance.
    Returns the next step prompt on success, or an error message (same step) on failure.
    On completion, writes config and returns the completion message.
    """
    session = get_setup_session(user_id)
    if not session:
        return start_setup(user_id)

    step = session["step"]
    data = json.loads(session["data"])

    ok, value, error = False, None, "Lỗi không xác định."

    if step == 1:
        ok, value, error = validate_distance(user_reply)
        if ok:
            data["race_distance_km"] = value

    elif step == 2:
        ok, value, error = validate_date(user_reply)
        if ok:
            data["race_date"] = value

    elif step == 3:
        distance = data.get("race_distance_km", 21.1)
        ok, value, error = validate_time(user_reply, race_distance_km=distance)
        if ok:
            data["race_target_time_min"] = value

    elif step == 4:
        ok, value, error = validate_kmweek(user_reply)
        if ok:
            data["current_weekly_km"] = value

    elif step == 5:
        ok, value, error = validate_days(user_reply)
        if ok:
            data["training_days_per_week"] = value

    elif step == 6:
        training_days = data.get("training_days_per_week", 5)
        ok, value, error = validate_rest_days(user_reply, training_days=training_days)
        if ok:
            data["preferred_rest_days"] = value

    if not ok:
        return f"⚠️ {error}\n\n{get_step_prompt(step)}"

    next_step = step + 1

    if next_step > 6:
        upsert_setup_session(user_id, step=6, data=data, status="active")
        finalize_setup(user_id, data)
        return _build_completion_message(data)

    upsert_setup_session(user_id, step=next_step, data=data)
    return get_step_prompt(next_step)


def finalize_setup(user_id: str, collected_data: dict) -> None:
    """Write collected data to config.json and mark session completed."""
    config = load_config()
    config["race_date"] = collected_data.get("race_date", config.get("race_date", ""))
    config["race_distance_km"] = collected_data.get("race_distance_km", config.get("race_distance_km", 21.1))
    config["race_target_time_min"] = collected_data.get("race_target_time_min", config.get("race_target_time_min"))
    config.setdefault("setup", {})["current_weekly_km"] = collected_data.get("current_weekly_km")
    config["setup"]["training_days_per_week"] = collected_data.get("training_days_per_week")
    config["setup"]["preferred_rest_days"] = collected_data.get("preferred_rest_days", [])
    save_config(config)
    complete_setup_session(user_id)
    logger.info(f"[SETUP] Finalized setup for user {user_id}: {collected_data}")


def invalidate_plans_for_resetup(user_id: str) -> None:
    """
    Expire any pending/accepted plans before re-setup. Call before start_setup() on re-entry.
    """
    from datetime import date, timedelta
    next_monday = (date.today() - timedelta(days=date.today().weekday()) + timedelta(weeks=4)).strftime("%Y-%m-%d")
    expire_stale_weekly_plans_range(user_id, before_date=next_monday)
    logger.info(f"[SETUP] Invalidated plans for user {user_id} due to re-setup")


def expire_stale_weekly_plans_range(user_id: str, before_date: str) -> None:
    """Expire all pending plans before before_date."""
    from app.core.database import get_db
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE weekly_plans SET status='expired' WHERE user_id=? AND week_start_date<=? AND status IN ('pending','accepted')",
                (user_id, before_date),
            )
    except Exception as e:
        logger.error(f"[SETUP] Failed to expire plans for re-setup: {e}")


def cleanup_stale_setup_sessions(timeout_hours: int = 24) -> int:
    """Abandon setup sessions with no activity for timeout_hours. Returns count."""
    count = abandon_stale_setup_sessions(timeout_hours)
    if count:
        logger.info(f"[SETUP] Abandoned {count} stale setup session(s) (>{timeout_hours}h inactive)")
    return count

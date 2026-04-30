from datetime import date, datetime
from typing import Optional

_DISTANCE_ALIASES = {
    "5k": 5.0,
    "5km": 5.0,
    "10k": 10.0,
    "10km": 10.0,
    "hm": 21.1,
    "half": 21.1,
    "halfmarathon": 21.1,
    "half marathon": 21.1,
    "21k": 21.1,
    "21km": 21.1,
    "fm": 42.2,
    "full": 42.2,
    "marathon": 42.2,
    "42k": 42.2,
    "42km": 42.2,
}

_DAY_ALIASES = {
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
    "t2": 0,
    "thu 2": 0,
    "thứ 2": 0,
    "thứ hai": 0,
    "monday": 0,
    "mon": 0,
    "t3": 1,
    "thu 3": 1,
    "thứ 3": 1,
    "thứ ba": 1,
    "tuesday": 1,
    "tue": 1,
    "t4": 2,
    "thu 4": 2,
    "thứ 4": 2,
    "thứ tư": 2,
    "wednesday": 2,
    "wed": 2,
    "t5": 3,
    "thu 5": 3,
    "thứ 5": 3,
    "thứ năm": 3,
    "thursday": 3,
    "thu": 3,
    "t6": 4,
    "thu 6": 4,
    "thứ 6": 4,
    "thứ sáu": 4,
    "friday": 4,
    "fri": 4,
    "t7": 5,
    "thu 7": 5,
    "thứ 7": 5,
    "thứ bảy": 5,
    "saturday": 5,
    "sat": 5,
    "cn": 6,
    "chủ nhật": 6,
    "chu nhat": 6,
    "sunday": 6,
    "sun": 6,
}


def validate_distance(text: str) -> tuple[bool, Optional[float], str]:
    """
    Validate race distance input. Accepts km number or known aliases.
    Returns (ok, value_km, error_msg).
    """
    text = text.strip().lower()
    if text in _DISTANCE_ALIASES:
        return True, _DISTANCE_ALIASES[text], ""
    try:
        km = float(text.replace(",", "."))
        if 1.0 <= km <= 200.0:
            return True, km, ""
        return False, None, "Cự ly phải từ 1 đến 200km. Ví dụ: 10, 21.1, 42.2"
    except ValueError:
        return False, None, "Không nhận ra cự ly này. Thử: 10, 21.1, 42.2, HM, FM"


def validate_date(text: str) -> tuple[bool, Optional[str], str]:
    """
    Validate race date. Accepts DD/MM/YYYY or YYYY-MM-DD.
    Must be at least 4 weeks from today.
    Returns (ok, date_str_iso, error_msg).
    """
    text = text.strip()
    parsed: Optional[date] = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue

    if not parsed:
        return (
            False,
            None,
            "Định dạng ngày không đúng. Nhập DD/MM/YYYY, ví dụ: 15/06/2026",
        )

    min_date = date.today().replace(day=date.today().day)
    from datetime import timedelta

    if parsed < min_date + timedelta(weeks=4):
        return (
            False,
            None,
            f"Ngày đua phải cách hôm nay ít nhất 4 tuần. Hôm nay là {date.today().strftime('%d/%m/%Y')}",
        )

    return True, parsed.strftime("%Y-%m-%d"), ""


def validate_time(
    text: str, race_distance_km: float = 21.1
) -> tuple[bool, Optional[int], str]:
    """
    Validate target finish time. Accepts H:MM, HH:MM, or plain minutes.
    Returns (ok, total_minutes, error_msg).
    """
    text = text.strip()
    minutes: Optional[int] = None

    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 2:
                hours, mins = int(parts[0]), int(parts[1])
                minutes = hours * 60 + mins
            elif len(parts) == 3:
                hours, mins, _ = int(parts[0]), int(parts[1]), int(parts[2])
                minutes = hours * 60 + mins
        except ValueError:
            pass
    else:
        try:
            minutes = int(text)
        except ValueError:
            pass

    if minutes is None:
        return False, None, "Định dạng thời gian không đúng. Thử: 1:45 hoặc 105"

    # Sanity check: pace must be between 3:00/km and 12:00/km
    if race_distance_km > 0:
        pace = minutes / race_distance_km
        if pace < 3.0:
            return (
                False,
                None,
                f"Mục tiêu {minutes}' quá nhanh cho {race_distance_km}km. Pace < 3:00/km không hợp lệ.",
            )
        if pace > 12.0:
            return (
                False,
                None,
                f"Mục tiêu {minutes}' quá chậm cho {race_distance_km}km. Pace > 12:00/km không hợp lệ.",
            )

    return True, minutes, ""


def validate_kmweek(text: str) -> tuple[bool, Optional[float], str]:
    """
    Validate current weekly km. Range: 0–200.
    Returns (ok, km_float, error_msg).
    """
    try:
        km = float(text.strip().replace(",", "."))
        if 0 <= km <= 200:
            return True, km, ""
        return False, None, "Khối lượng phải từ 0 đến 200 km/tuần."
    except ValueError:
        return False, None, "Nhập số km, ví dụ: 30"


def validate_days(text: str) -> tuple[bool, Optional[int], str]:
    """
    Validate training days per week. Range: 3–6.
    Returns (ok, days_int, error_msg).
    """
    try:
        days = int(text.strip())
        if 3 <= days <= 6:
            return True, days, ""
        return False, None, "Số ngày tập phải từ 3 đến 6 ngày/tuần."
    except ValueError:
        return False, None, "Nhập số nguyên, ví dụ: 4 hoặc 5"


def validate_rest_days(
    text: str, training_days: int = 5
) -> tuple[bool, Optional[list], str]:
    """
    Validate preferred rest days. Accepts comma-separated day names or numbers.
    Returns (ok, list_of_weekday_ints [0=Mon..6=Sun], error_msg).
    """
    text = text.strip().lower()
    parts = [
        p.strip()
        for p in text.replace("và", ",").replace("and", ",").split(",")
        if p.strip()
    ]

    if not parts:
        return False, None, "Nhập ít nhất 1 ngày nghỉ, ví dụ: Thứ Hai, Thứ Sáu"

    days: list[int] = []
    for part in parts:
        if part in _DAY_ALIASES:
            day = _DAY_ALIASES[part]
            if day not in days:
                days.append(day)
        else:
            return (
                False,
                None,
                f"Không nhận ra ngày '{part}'. Thử: Thứ Hai, T2, Monday, 1",
            )

    max_rest = 7 - training_days
    if len(days) > max_rest:
        return (
            False,
            None,
            f"Với {training_days} ngày tập, tối đa {max_rest} ngày nghỉ.",
        )

    return True, days, ""


def validate_hr(text: str, kind: str = "max") -> tuple[bool, Optional[int], str]:
    """
    Validate heart rate (max or rest). Returns (ok, bpm_int, error_msg).
    Accepts '/skip' to skip optional step.
    """
    if text.strip().lower() in ["/skip", "skip", "bỏ qua"]:
        return True, None, ""
    try:
        bpm = int(text.strip())
        if kind == "max":
            if 120 <= bpm <= 230:
                return True, bpm, ""
            return False, None, "HR max phải từ 120 đến 230 bpm. Thử: 185 hoặc /skip"
        else:
            if 30 <= bpm <= 100:
                return True, bpm, ""
            return False, None, "HR nghỉ phải từ 30 đến 100 bpm. Thử: 55 hoặc /skip"
    except ValueError:
        return (
            False,
            None,
            f"Nhập số bpm, ví dụ: {'185' if kind == 'max' else '55'} hoặc /skip",
        )

"""Format the stored Runalyze daily load row for coach prompts.

Pure formatters plus one thin DB wrapper. Runalyze ACWR is deliberately NOT exposed:
the coach's safety rules stay on the app's own ACWR so two ACWR numbers never compete.
"""

import json
from datetime import date
from typing import Optional

from app.core.database import get_runalyze_daily_load
from app.core.logging_conf import get_module_logger

logger = get_module_logger("runalyze_context")

RUNALYZE_MAX_STALE_DAYS = 3

# TSB (CTL - ATL) bands.
TSB_FRESH_MIN = 5
TSB_BALANCED_MIN = -10
TSB_TIRED_MIN = -30

_PROGNOSIS_LABELS = {5: "5K", 10: "10K", 21.1: "HM", 42.2: "FM"}
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_MINUTE = 60


def _tsb_label(tsb: float) -> str:
    if tsb > TSB_FRESH_MIN:
        return "tươi"
    if tsb >= TSB_BALANCED_MIN:
        return "cân bằng"
    if tsb >= TSB_TIRED_MIN:
        return "mệt"
    return "quá tải"


def _hrv_part(row: dict) -> Optional[str]:
    hrv = row.get("hrv_last")
    if hrv is None:
        return None
    low, high = row.get("hrv_range_low"), row.get("hrv_range_high")
    baseline = row.get("hrv_baseline")
    if low is None or high is None:
        return f"HRV {hrv:.0f}ms"
    status = "thấp" if hrv < low else "cao" if hrv > high else "bình thường"
    base = f"nền {baseline:.1f}, " if baseline is not None else ""
    return f"HRV {hrv:.0f}ms ({base}vùng {low:.1f}–{high:.1f}: {status})"


def _load_parts(row: dict) -> list[str]:
    parts = []
    if row.get("ctl") is not None and row.get("atl") is not None:
        parts.append(f"CTL {row['ctl']:.0f} | ATL {row['atl']:.0f}")
    if row.get("tsb") is not None:
        parts.append(f"TSB {row['tsb']:+.0f} ({_tsb_label(row['tsb'])})")
    return parts


def format_runalyze_context(row: Optional[dict], today: date) -> str:
    """One-line load/recovery summary, or '' when there is nothing to show."""
    if not row:
        return ""
    parts = _load_parts(row)
    if (hrv := _hrv_part(row)) is not None:
        parts.append(hrv)
    if row.get("marathon_shape") is not None:
        parts.append(f"Marathon shape {row['marathon_shape']:.0f}%")
    if row.get("effective_vo2max") is not None:
        parts.append(f"VO2max hiệu dụng {row['effective_vo2max']:.1f}")
    if not parts:
        return ""
    text = " | ".join(parts)
    age_days = (today - date.fromisoformat(row["date"])).days if row.get("date") else 0
    return f"{text} (cũ {age_days} ngày)" if age_days > 0 else text


def _raw_payload(row: Optional[dict]) -> dict:
    if not row or not row.get("raw_json"):
        return {}
    try:
        return json.loads(row["raw_json"])
    except json.JSONDecodeError:
        logger.warning("[RUNALYZE] raw_json is not valid JSON; ignoring")
        return {}


def _format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_runalyze_prognosis(row: Optional[dict]) -> str:
    """Race-time predictions (Runalyze model) for 5K/10K/HM/FM, or ''."""
    prognosis = _raw_payload(row).get("prognosis") or {}
    if not prognosis.get("predictions_available"):
        return ""
    by_distance = {
        round(d["distance"], 1): d.get("prognosis_seconds")
        for d in prognosis.get("distances", [])
    }
    parts = [
        f"{label} {_format_duration(by_distance[dist])}"
        for dist, label in _PROGNOSIS_LABELS.items()
        if by_distance.get(dist)
    ]
    if not parts:
        return ""
    vo2 = (row or {}).get("effective_vo2max")
    model = f"VO2max {vo2:.1f}" if vo2 is not None else prognosis.get("model", "")
    return f"Dự báo Runalyze ({model}): " + " | ".join(parts)


def format_runalyze_races(row: Optional[dict]) -> str:
    """Planned goal races from Runalyze, or ''."""
    items = (_raw_payload(row).get("planned_races") or {}).get("items") or []
    parts = [
        f"{r['name']} {r['date']} (ưu tiên {r.get('priority', '?')}, "
        f"còn {r.get('days_until', '?')} ngày)"
        for r in items
    ]
    return "; ".join(parts)


def get_runalyze_text(user_id: str, today: Optional[date] = None) -> str:
    """Latest stored load line within the staleness window, or ''."""
    today = today or date.today()
    row = get_runalyze_daily_load(
        user_id, today.isoformat(), max_stale_days=RUNALYZE_MAX_STALE_DAYS
    )
    return format_runalyze_context(row, today)

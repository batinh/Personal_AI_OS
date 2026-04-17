"""
Pure-Python running science metrics engine.
Computes all metrics from Strava stream arrays + activity metadata.

No DB, no Gemini imports — this module is side-effect-free.
All helpers return None (not raise) when data is absent or insufficient.
"""
import json
import math
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.logging_conf import get_module_logger
logger = get_module_logger("coach")

# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compute_stream_metrics(
    streams: Dict[str, Any],
    meta: Dict[str, Any],
    config: Dict[str, Any],
    activity_name: str = "",
) -> Dict[str, Any]:
    """
    Compute all running science metrics from Strava stream data.

    Args:
        streams:       key_by_type dict — e.g. {"velocity_smooth": [0.1, ...], ...}
                       Use stream_storage.get_stream_arrays() to get this.
        meta:          extended_meta from strava_client
                       Expected keys: moving_time (sec), distance (m), avg_hr, splits, etc.
        config:        user physiology config
                       Expected keys: max_hr, rest_hr, rftp_watts, threshold_pace_min_km
        activity_name: Strava activity name — used as a hint for workout_type_detected.

    Returns:
        Flat dict of all metric columns. Field = None when data is missing (never raises).
    """
    try:
        arrays = _extract_arrays(streams)
        max_hr = int(config.get("max_hr") or 185)
        rest_hr = int(config.get("rest_hr") or 55)
        rftp_watts = config.get("rftp_watts") or config.get("ftp_watts")
        threshold_pace = config.get("threshold_pace_min_km")
        moving_time_min = (meta.get("moving_time") or 0) / 60.0

        vel = arrays.get("velocity_smooth") or []
        hr = arrays.get("heartrate") or []
        cad = arrays.get("cadence") or []
        alt = arrays.get("altitude") or []
        pwr = arrays.get("watts") or []
        grade = arrays.get("grade_smooth") or []
        time_arr = arrays.get("time") or []

        metrics: Dict[str, Any] = {}

        # ---- Group A: Aerobic Base ----
        hr_zones_result = _hr_zones(hr, max_hr, rest_hr, time_arr) if hr else None
        if hr_zones_result:
            metrics["hr_zone_distribution"] = json.dumps(hr_zones_result["distribution"])
            metrics["time_in_hr_zones_sec"] = json.dumps(hr_zones_result["seconds"])
        else:
            metrics["hr_zone_distribution"] = None
            metrics["time_in_hr_zones_sec"] = None

        if vel and hr and len(vel) == len(hr) and len(vel) > 10:
            aero = _aerobic_decoupling(hr, vel)
            metrics["aerobic_decoupling_pct"] = aero.get("aerobic_decoupling_pct")
            metrics["cardiac_drift_pct"] = aero.get("cardiac_drift_pct")
            metrics["avg_efficiency_factor"] = aero.get("avg_efficiency_factor")
        else:
            metrics["aerobic_decoupling_pct"] = None
            metrics["cardiac_drift_pct"] = None
            metrics["avg_efficiency_factor"] = None

        # ---- Group B: Cadence / Mechanics ----
        if cad and vel and len(cad) == len(vel) and len(cad) > 10:
            metrics.update(_cadence_stats(cad, vel))
        else:
            metrics["avg_cadence_spm"] = None
            metrics["avg_stride_length_m"] = None

        # ---- Group C: Pace / Effort ----
        if vel and len(vel) > 10:
            metrics.update(_pace_stats(vel, time_arr, threshold_pace))
        else:
            metrics["avg_pace_min_km"] = None
            metrics["pace_variability_cv"] = None
            metrics["positive_split_ratio"] = None
            metrics["time_in_pace_zones_pct"] = None

        # ---- Group D: Elevation / Grade ----
        if alt and len(alt) > 5:
            metrics.update(_elevation_stats(alt, vel, grade, time_arr))
        else:
            metrics["total_elevation_gain_m"] = None
            metrics["grade_adjusted_pace_min_km"] = None

        # ---- Group E: Power (Stryd only) ----
        if pwr and any(p and p > 0 for p in pwr):
            metrics.update(_power_stats(pwr, rftp_watts, moving_time_min))
        else:
            metrics["avg_power_watts"] = None
            metrics["normalized_power_watts"] = None
            metrics["intensity_factor"] = None
            metrics["training_stress_score"] = None

        # ---- Group F: Interval / Sprint detection ----
        metrics.update(_detect_intervals(vel, hr, time_arr, activity_name, metrics))

        return metrics

    except Exception as exc:
        logger.error(f"[METRICS_ENGINE] compute_stream_metrics failed: {exc}", exc_info=True)
        return {}


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #

def _extract_arrays(streams: Dict[str, Any]) -> Dict[str, List]:
    """
    Accept both raw key_by_type dicts (each value is {"data": [...], "series_type": ...})
    and already-flat dicts (value is a list directly).
    Returns flat {key: [values]} dict.
    """
    if not streams:
        return {}
    out: Dict[str, List] = {}
    for key, obj in streams.items():
        if isinstance(obj, dict) and "data" in obj:
            out[key] = obj["data"]
        elif isinstance(obj, list):
            out[key] = obj
    return out


def _hr_zones(
    hr: List[float],
    max_hr: int,
    rest_hr: int,
    time_arr: List[int],
) -> Optional[Dict]:
    """
    Compute HR zone distribution using Karvonen (HR reserve) method.
    Returns {"distribution": {"z1": pct, ...}, "seconds": {"z1": sec, ...}}
    Zone boundaries (% of HR reserve):  Z1<60  Z2 60-70  Z3 70-80  Z4 80-90  Z5>90
    """
    if not hr or len(hr) < 5:
        return None
    try:
        hrr = max_hr - rest_hr
        bounds = [
            rest_hr + hrr * 0.60,
            rest_hr + hrr * 0.70,
            rest_hr + hrr * 0.80,
            rest_hr + hrr * 0.90,
        ]
        # seconds per sample (1s default, or derive from time array)
        secs_per = 1.0
        if time_arr and len(time_arr) == len(hr) and len(time_arr) > 1:
            total_time = time_arr[-1] - time_arr[0]
            secs_per = total_time / max(len(hr) - 1, 1)

        counts = {"z1": 0, "z2": 0, "z3": 0, "z4": 0, "z5": 0}
        for h in hr:
            if h is None:
                continue
            if h < bounds[0]:
                counts["z1"] += 1
            elif h < bounds[1]:
                counts["z2"] += 1
            elif h < bounds[2]:
                counts["z3"] += 1
            elif h < bounds[3]:
                counts["z4"] += 1
            else:
                counts["z5"] += 1

        total = sum(counts.values())
        if total == 0:
            return None

        distribution = {z: round(cnt / total * 100, 1) for z, cnt in counts.items()}
        seconds = {z: round(cnt * secs_per) for z, cnt in counts.items()}
        return {"distribution": distribution, "seconds": seconds}
    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _hr_zones error: {exc}")
        return None


def _aerobic_decoupling(hr: List[float], vel: List[float]) -> Dict:
    """
    Compute aerobic decoupling, cardiac drift, and avg efficiency factor.
    Aerobic decoupling (Pa:Hr): (EF_first_half / EF_second_half - 1) * 100
    EF = avg(velocity) / avg(HR)
    Positive value = cardiac drift / poor aerobic base.
    Cardiac drift = (avg_hr_second - avg_hr_first) / avg_hr_first * 100
    """
    result = {"aerobic_decoupling_pct": None, "cardiac_drift_pct": None, "avg_efficiency_factor": None}
    try:
        valid = [(v, h) for v, h in zip(vel, hr) if v is not None and h and h > 0 and v > 0]
        if len(valid) < 10:
            return result

        n = len(valid)
        mid = n // 2
        first = valid[:mid]
        second = valid[mid:]

        avg_vel_all = sum(v for v, _ in valid) / n
        avg_hr_all = sum(h for _, h in valid) / n
        if avg_hr_all > 0:
            result["avg_efficiency_factor"] = round(avg_vel_all / avg_hr_all * 1000, 4)

        ef1 = (sum(v for v, _ in first) / len(first)) / (sum(h for _, h in first) / len(first))
        ef2 = (sum(v for v, _ in second) / len(second)) / (sum(h for _, h in second) / len(second))
        if ef1 > 0:
            result["aerobic_decoupling_pct"] = round((ef1 - ef2) / ef1 * 100, 2)

        avg_hr1 = sum(h for _, h in first) / len(first)
        avg_hr2 = sum(h for _, h in second) / len(second)
        if avg_hr1 > 0:
            result["cardiac_drift_pct"] = round((avg_hr2 - avg_hr1) / avg_hr1 * 100, 2)

    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _aerobic_decoupling error: {exc}")
    return result


def _cadence_stats(cad: List[float], vel: List[float]) -> Dict:
    """
    Compute avg cadence (spm) and avg stride length (m).
    Strava cadence is steps/min (one foot). Multiply by 2 for SPM (both feet).
    Stride length = velocity / (cadence_both_feet / 60)
    """
    result = {"avg_cadence_spm": None, "avg_stride_length_m": None}
    try:
        pairs = [(c, v) for c, v in zip(cad, vel) if c and c > 0 and v and v > 0]
        if len(pairs) < 5:
            return result
        # Strava cadence = steps/min (single foot) → multiply by 2 for SPM
        avg_cad = sum(c for c, _ in pairs) / len(pairs) * 2
        result["avg_cadence_spm"] = round(avg_cad, 1)
        # Stride length = velocity_m_s / (cadence_spm / 60)
        strides = [v / (c * 2 / 60) for c, v in pairs if c * 2 / 60 > 0]
        if strides:
            result["avg_stride_length_m"] = round(sum(strides) / len(strides), 3)
    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _cadence_stats error: {exc}")
    return result


def _pace_stats(
    vel: List[float],
    time_arr: List[int],
    threshold_pace_min_km: Optional[float],
) -> Dict:
    """
    Compute avg pace, pace variability (CV), positive split ratio, and pace zone distribution.
    velocity_smooth is in m/s.
    avg_pace_min_km = 1000 / (60 * mean_velocity)
    """
    result = {
        "avg_pace_min_km": None,
        "pace_variability_cv": None,
        "positive_split_ratio": None,
        "time_in_pace_zones_pct": None,
    }
    try:
        active = [v for v in vel if v and v > 0.5]  # exclude stopped/walking samples
        if not active:
            return result

        mean_vel = sum(active) / len(active)
        if mean_vel <= 0:
            return result

        result["avg_pace_min_km"] = round(1000 / (60 * mean_vel), 3)

        # CV = std / mean (use numpy for std)
        arr = np.array(active, dtype=float)
        if arr.mean() > 0:
            result["pace_variability_cv"] = round(float(np.std(arr) / arr.mean()), 4)

        # Positive split: first half mean velocity vs second half
        n = len(active)
        mid = n // 2
        if mid > 0:
            vel1 = sum(active[:mid]) / mid
            vel2 = sum(active[mid:]) / max(len(active[mid:]), 1)
            if vel2 > 0:
                result["positive_split_ratio"] = round(vel1 / vel2, 4)

        # Pace zones (% of time)
        if threshold_pace_min_km and threshold_pace_min_km > 0:
            thresh_vel = 1000 / (60 * threshold_pace_min_km)
            zones = {"easy": 0, "tempo": 0, "race": 0, "fast": 0}
            for v in active:
                if v < thresh_vel * 0.80:
                    zones["easy"] += 1
                elif v < thresh_vel * 0.95:
                    zones["tempo"] += 1
                elif v < thresh_vel * 1.05:
                    zones["race"] += 1
                else:
                    zones["fast"] += 1
            total = sum(zones.values())
            if total > 0:
                pct = {k: round(cnt / total * 100, 1) for k, cnt in zones.items()}
                result["time_in_pace_zones_pct"] = json.dumps(pct)

    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _pace_stats error: {exc}")
    return result


def _elevation_stats(
    alt: List[float],
    vel: List[float],
    grade: List[float],
    time_arr: List[int],
) -> Dict:
    """
    Compute total elevation gain (m) and grade-adjusted pace (min/km).
    GAP: flat-equivalent pace = actual_pace / (1 + 0.033 * grade_pct)
    """
    result = {"total_elevation_gain_m": None, "grade_adjusted_pace_min_km": None}
    try:
        # Total elevation gain: sum of positive altitude differences
        gain = sum(
            max(0.0, alt[i] - alt[i - 1])
            for i in range(1, len(alt))
            if alt[i] is not None and alt[i - 1] is not None
        )
        result["total_elevation_gain_m"] = round(gain, 1)

        # GAP: use grade_smooth if available, else skip
        if grade and len(grade) == len(vel):
            gap_velocities = []
            for g, v in zip(grade, vel):
                if v and v > 0.5 and g is not None:
                    correction = 1.0 + 0.033 * g  # g is already in % from Strava
                    if correction > 0:
                        gap_velocities.append(v / correction)
            if gap_velocities:
                avg_gap_vel = sum(gap_velocities) / len(gap_velocities)
                if avg_gap_vel > 0:
                    result["grade_adjusted_pace_min_km"] = round(1000 / (60 * avg_gap_vel), 3)

    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _elevation_stats error: {exc}")
    return result


def _power_stats(
    pwr: List[float],
    rftp_watts: Optional[float],
    moving_time_min: float,
) -> Dict:
    """
    Compute power metrics (Stryd / power meter only).
    Returns None for all if no valid power data.
    """
    result = {
        "avg_power_watts": None,
        "normalized_power_watts": None,
        "intensity_factor": None,
        "training_stress_score": None,
    }
    try:
        valid = [p for p in pwr if p is not None and p > 0]
        if not valid:
            return result

        avg_p = sum(valid) / len(valid)
        result["avg_power_watts"] = round(avg_p, 1)

        # Normalized Power: 30s rolling mean^4, take mean, then ^0.25
        arr = np.array([p if p is not None else 0.0 for p in pwr], dtype=float)
        window = 30  # 30 samples ≈ 30 seconds
        if len(arr) >= window:
            kernel = np.ones(window) / window
            rolling_mean = np.convolve(arr, kernel, mode="valid")
            rolling_pow4 = rolling_mean ** 4
            np_val = float(np.mean(rolling_pow4) ** 0.25)
            result["normalized_power_watts"] = round(np_val, 1)

            if rftp_watts and rftp_watts > 0:
                if_val = np_val / rftp_watts
                result["intensity_factor"] = round(if_val, 4)
                # TSS = (time_min × IF² / 36) × 100  [from design doc formula]
                if moving_time_min > 0:
                    tss = (moving_time_min * if_val ** 2 / 36) * 100
                    result["training_stress_score"] = round(tss, 1)

    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _power_stats error: {exc}")
    return result


def _detect_intervals(
    vel: List[float],
    hr: List[float],
    time_arr: List[int],
    activity_name: str,
    metrics: Dict,
) -> Dict:
    """
    Auto-detect workout type (easy/tempo/interval/long/sprint/recovery) and
    compute interval-specific metrics.

    Algorithm:
    1. Smooth velocity (5-point moving average)
    2. hard_threshold = global_avg_velocity × 1.15
    3. Hard effort = contiguous blocks where smoothed velocity > threshold for > 30s
    4. Guard: reps < 3 AND avg_rep_duration > 300s → "tempo" not "interval"
    5. Guard: z4_z5_time_pct < 10% → "easy" or "long_run"
    """
    result = {
        "workout_type_detected": None,
        "interval_reps_count": None,
        "interval_avg_pace_min_km": None,
        "interval_pace_consistency_pct": None,
        "interval_avg_hr_bpm": None,
        "recovery_hr_quality_bpm": None,
        "max_velocity_m_s": None,
        "anaerobic_time_sec": None,
        "z4_z5_time_pct": None,
    }
    try:
        active = [v for v in vel if v and v > 0.5]
        if len(active) < 30:
            result["workout_type_detected"] = "unknown"
            return result

        arr = np.array(vel, dtype=float)
        arr = np.clip(arr, 0, None)

        # 5-point moving average smoothing
        kernel = np.ones(5) / 5
        smoothed = np.convolve(arr, kernel, mode="same")

        avg_vel = float(np.mean([v for v in smoothed if v > 0.5]))
        hard_threshold = avg_vel * 1.15

        # Max velocity
        result["max_velocity_m_s"] = round(float(np.max(arr)), 3)

        # Anaerobic time: samples above 95th percentile of active velocity
        p95 = float(np.percentile([v for v in arr if v > 0.5], 95))
        secs_per = 1.0
        if time_arr and len(time_arr) == len(vel) and len(time_arr) > 1:
            secs_per = (time_arr[-1] - time_arr[0]) / max(len(vel) - 1, 1)
        anaerobic_samples = sum(1 for v in smoothed if v > p95)
        result["anaerobic_time_sec"] = round(anaerobic_samples * secs_per, 1)

        # z4_z5_time_pct from already-computed hr_zone_distribution
        hr_zones_json = metrics.get("hr_zone_distribution")
        z45_pct = 0.0
        if hr_zones_json:
            try:
                zones = json.loads(hr_zones_json)
                z45_pct = (zones.get("z4", 0.0) or 0.0) + (zones.get("z5", 0.0) or 0.0)
                result["z4_z5_time_pct"] = round(z45_pct, 1)
            except (json.JSONDecodeError, TypeError):
                pass

        # Detect hard effort blocks
        blocks = _find_hard_blocks(smoothed, hard_threshold, secs_per, min_duration_sec=30)

        name_lower = activity_name.lower()

        # Workout type classification
        if not blocks:
            # No hard effort blocks detected
            if z45_pct >= 10:
                workout_type = "tempo"
            elif avg_vel > 0 and 1000 / (60 * avg_vel) < 5.5:  # faster than 5:30/km = tempo-ish
                workout_type = "tempo"
            else:
                workout_type = _classify_easy_or_long(vel, active, time_arr, secs_per)
        else:
            reps = len(blocks)
            avg_rep_dur = sum(b["duration_sec"] for b in blocks) / reps

            # Sprint check: name hint OR very high velocity
            is_sprint = (
                "sprint" in name_lower
                or result["max_velocity_m_s"] > 5.5  # ~20 km/h — sprint territory
                or ("strides" in name_lower and avg_rep_dur < 30)
            )

            if is_sprint:
                workout_type = "sprint"
            elif reps < 3 or avg_rep_dur > 300:
                workout_type = "tempo"
            else:
                workout_type = "interval"

            # Interval-specific metrics
            result["interval_reps_count"] = reps
            rep_paces = []
            rep_hrs = []

            for block in blocks:
                s, e = block["start_idx"], block["end_idx"]
                block_vel = [v for v in vel[s:e] if v and v > 0]
                if block_vel:
                    rep_paces.append(1000 / (60 * (sum(block_vel) / len(block_vel))))
                if hr and len(hr) > e:
                    block_hr = [h for h in hr[s:e] if h and h > 0]
                    if block_hr:
                        rep_hrs.append(sum(block_hr) / len(block_hr))

            if rep_paces:
                avg_rep_pace = sum(rep_paces) / len(rep_paces)
                result["interval_avg_pace_min_km"] = round(avg_rep_pace, 3)
                if len(rep_paces) > 1:
                    cv = float(np.std(rep_paces)) / avg_rep_pace if avg_rep_pace > 0 else 0
                    result["interval_pace_consistency_pct"] = round((1 - cv) * 100, 1)

            if rep_hrs:
                result["interval_avg_hr_bpm"] = round(sum(rep_hrs) / len(rep_hrs), 1)

            # Recovery HR quality: avg HR drop per minute after each block
            if hr and blocks:
                recovery_drops = _recovery_hr_quality(hr, blocks, secs_per)
                if recovery_drops:
                    result["recovery_hr_quality_bpm"] = round(
                        sum(recovery_drops) / len(recovery_drops), 1
                    )

        result["workout_type_detected"] = workout_type

    except Exception as exc:
        logger.warning(f"[METRICS_ENGINE] _detect_intervals error: {exc}")
    return result


def _find_hard_blocks(
    smoothed: np.ndarray,
    threshold: float,
    secs_per: float,
    min_duration_sec: float = 30,
) -> List[Dict]:
    """Find contiguous blocks where velocity > threshold for >= min_duration_sec."""
    blocks = []
    in_block = False
    start = 0
    min_samples = max(1, int(min_duration_sec / secs_per))

    for i, v in enumerate(smoothed):
        if not in_block and v > threshold:
            in_block = True
            start = i
        elif in_block and v <= threshold:
            if i - start >= min_samples:
                blocks.append({
                    "start_idx": start,
                    "end_idx": i,
                    "duration_sec": (i - start) * secs_per,
                })
            in_block = False

    # Close final block if still open
    if in_block and len(smoothed) - start >= min_samples:
        blocks.append({
            "start_idx": start,
            "end_idx": len(smoothed),
            "duration_sec": (len(smoothed) - start) * secs_per,
        })
    return blocks


def _recovery_hr_quality(
    hr: List[float],
    blocks: List[Dict],
    secs_per: float,
) -> List[float]:
    """
    Compute HR drop (bpm/min) after each hard block, looking 60s into the recovery window.
    Returns list of bpm/min values (one per block).
    """
    recovery_drops = []
    look_ahead_samples = max(1, int(60 / secs_per))

    for block in blocks:
        end = block["end_idx"]
        # HR at end of block
        block_hr_end_slice = hr[max(0, end - 3): end]
        valid_end = [h for h in block_hr_end_slice if h and h > 0]
        if not valid_end:
            continue
        hr_peak = max(valid_end)

        # HR 60s after block
        recovery_slice = hr[end: end + look_ahead_samples]
        valid_rec = [h for h in recovery_slice if h and h > 0]
        if not valid_rec:
            continue
        hr_recovery = min(valid_rec)

        drop = hr_peak - hr_recovery
        if drop > 0:
            recovery_drops.append(drop)  # already bpm/min since we look 60s ahead

    return recovery_drops


def _classify_easy_or_long(
    vel: List[float],
    active: List[float],
    time_arr: List[int],
    secs_per: float,
) -> str:
    """Classify no-hard-effort run as easy, long, or recovery."""
    total_time_min = len(vel) * secs_per / 60
    avg_vel = sum(active) / len(active) if active else 0

    # Long run: > 75 minutes
    if total_time_min > 75:
        return "long_run"
    # Recovery: very slow pace (avg pace > 7 min/km → velocity < 2.38 m/s)
    if avg_vel < 2.38:
        return "recovery"
    return "easy"


# --------------------------------------------------------------------------- #
# Prompt-side helper (used by run_analysis.py)
# --------------------------------------------------------------------------- #

def build_run_metrics_block(metrics: Dict[str, Any], config: Dict[str, Any]) -> str:
    """
    Build a concise, human-readable metrics block for LLM injection.
    Capped at ~700 characters to avoid prompt bloat.
    Skips None values and power section entirely when all power fields are None.
    """
    if not metrics:
        return ""

    lines = []

    def _add(label: str, val: Any, unit: str = "", fmt: str = "") -> None:
        if val is None:
            return
        if fmt:
            lines.append(f"{label}: {val:{fmt}}{unit}")
        else:
            lines.append(f"{label}: {val}{unit}")

    wt = metrics.get("workout_type_detected")
    if wt:
        lines.append(f"Workout type: {wt}")

    _add("Avg pace", metrics.get("avg_pace_min_km"), " min/km", ".2f")
    _add("GAP", metrics.get("grade_adjusted_pace_min_km"), " min/km", ".2f")
    _add("Avg cadence", metrics.get("avg_cadence_spm"), " spm", ".0f")
    _add("Stride length", metrics.get("avg_stride_length_m"), " m", ".2f")
    _add("Aerobic decoupling", metrics.get("aerobic_decoupling_pct"), "%", ".1f")
    _add("Cardiac drift", metrics.get("cardiac_drift_pct"), "%", ".1f")
    _add("Efficiency factor", metrics.get("avg_efficiency_factor"), "", ".3f")
    _add("Pace variability CV", metrics.get("pace_variability_cv"), "", ".3f")
    _add("Positive split ratio", metrics.get("positive_split_ratio"), "", ".3f")
    _add("Elevation gain", metrics.get("total_elevation_gain_m"), " m", ".0f")
    _add("Max velocity", metrics.get("max_velocity_m_s"), " m/s", ".2f")
    _add("Anaerobic time", metrics.get("anaerobic_time_sec"), " s", ".0f")
    _add("Z4+Z5 time", metrics.get("z4_z5_time_pct"), "%", ".1f")

    # HR zones (compact)
    hr_zones_json = metrics.get("hr_zone_distribution")
    if hr_zones_json:
        try:
            z = json.loads(hr_zones_json)
            zones_str = " ".join(f"Z{i}:{z.get(f'z{i}', 0):.0f}%" for i in range(1, 6))
            lines.append(f"HR zones: {zones_str}")
        except (json.JSONDecodeError, TypeError):
            pass

    # Interval section
    reps = metrics.get("interval_reps_count")
    if reps is not None and wt in ("interval", "sprint", "tempo"):
        lines.append(f"Reps: {reps}")
        _add("Rep avg pace", metrics.get("interval_avg_pace_min_km"), " min/km", ".2f")
        _add("Rep consistency", metrics.get("interval_pace_consistency_pct"), "%", ".1f")
        _add("Rep avg HR", metrics.get("interval_avg_hr_bpm"), " bpm", ".0f")
        _add("Recovery HR quality", metrics.get("recovery_hr_quality_bpm"), " bpm drop/min", ".0f")

    # Power section (only if data present)
    avg_p = metrics.get("avg_power_watts")
    np_w = metrics.get("normalized_power_watts")
    if avg_p is not None or np_w is not None:
        _add("Avg power", avg_p, " W", ".0f")
        _add("NP", np_w, " W", ".0f")
        _add("IF", metrics.get("intensity_factor"), "", ".3f")
        _add("TSS", metrics.get("training_stress_score"), "", ".0f")

    block = "\n".join(lines)
    # Cap at 700 chars
    if len(block) > 700:
        block = block[:697] + "..."
    return block

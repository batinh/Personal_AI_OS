"""
Stream file storage: save/load raw Strava activity streams under data/streams.
Used for full-fidelity re-analysis and segment-level analysis without re-fetching from API.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("AI_COACH")

# Absolute path anchored to this file's location.
# stream_storage.py is at: <project_root>/app/services/stream_storage.py
# So parent.parent.parent = <project_root>
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = _BASE_DIR / "data"
STREAMS_SUBDIR = "streams"


def _streams_base() -> Path:
    """Streams root: data/streams."""
    return DATA_DIR / STREAMS_SUBDIR


def get_stream_file_path(user_id: str, activity_id: str) -> str:
    """Return relative path from data/ for this activity's stream file."""
    return f"{STREAMS_SUBDIR}/{user_id}/{activity_id}.json"


def save_activity_stream_to_file(
    user_id: str,
    activity_id: str,
    stream_dict: Dict[str, Any],
) -> Optional[str]:
    """
    Save raw Strava stream response (key_by_type) to data/streams/{user_id}/{activity_id}.json.
    Returns relative path from data/ (e.g. streams/123/456.json) or None on failure.
    """
    if not stream_dict:
        return None
    try:
        base = _streams_base() / str(user_id)
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{activity_id}.json"
        payload = {
            "activity_id": str(activity_id),
            "user_id": str(user_id),
            "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "streams": stream_dict,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        rel = get_stream_file_path(str(user_id), str(activity_id))
        logger.info(f"[STREAM] Saved raw stream to {path}")
        return rel
    except Exception as e:
        logger.error(f"[STREAM] Failed to save stream file for {activity_id}: {e}")
        return None


def load_activity_stream_from_file(stream_file_path: str) -> Optional[Dict[str, Any]]:
    """
    Load stream payload from data/{stream_file_path}.
    Returns dict with keys: activity_id, user_id, fetched_at, streams (key_by_type dict); or None.
    """
    if not stream_file_path or not stream_file_path.strip():
        return None
    try:
        full_path = DATA_DIR / stream_file_path.lstrip("/").replace("data/", "")
        if not full_path.is_file():
            logger.warning(f"[STREAM] File not found: {full_path}")
            return None
        with open(full_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[STREAM] Failed to load stream file {stream_file_path}: {e}")
        return None


def get_stream_arrays(payload: Dict[str, Any]) -> Optional[Dict[str, list]]:
    """
    From loaded payload, return flat dict of stream type -> data array for easy analysis.
    E.g. {"time": [0,1,2,...], "heartrate": [120,121,...], ...}.
    """
    if not payload or "streams" not in payload:
        return None
    out = {}
    for key, obj in payload["streams"].items():
        if isinstance(obj, dict) and "data" in obj:
            out[key] = obj["data"]
    return out if out else None

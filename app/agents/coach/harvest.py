import os
from app.core.user_context import get_primary_user_id
import json
import logging
import time
from datetime import datetime, timedelta

from app.agents.coach.strava_client import StravaClient
from app.agents.coach.utils import calculate_trimp
from app.core.config import load_config
from app.core.database import (
    init_db,
    upsert_user,
    save_run_activity,
    save_run_activity_raw,
    delete_run_activity,
    list_run_activity_ids_in_date_range,
    upsert_run_computed_metrics,
)
from app.agents.coach.metrics_engine import compute_stream_metrics
from app.services.stream_storage import save_activity_stream_to_file
from app.core.notification import send_telegram_msg
from app.services.rag_memory import rag_db

from app.core.logging_conf import get_module_logger

logger = get_module_logger("coach")

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


# ==========================================
# SHARED HELPER: Build activity record from Strava API response
# ==========================================
def build_activity_record(
    activity: dict, max_hr: int = 185, rest_hr: int = 55, gender: str = "male"
) -> dict:
    """
    Build a normalized activity_data dict from a raw Strava activity response.
    Single Source of Truth for distance/time/TRIMP calculation across all pipelines
    (Webhook ingest, Cron harvest, Manual sync).
    """
    dist_km = activity.get("distance", 0) / 1000
    moving_min = activity.get("moving_time", 0) / 60
    avg_hr = activity.get("average_heartrate", 0)
    trimp_data = calculate_trimp(moving_min, avg_hr, max_hr, rest_hr, gender)

    return {
        "activity_id": str(activity.get("id", activity.get("activity_id", ""))),
        "name": activity.get("name", "Unknown Run"),
        "start_date": activity.get("start_date_local", activity.get("start_date", "")),
        "distance_km": round(dist_km, 2),
        "moving_time_min": round(moving_min, 2),
        "avg_hr": int(avg_hr),
        "max_hr": int(activity.get("max_heartrate", 0)),
        "suffer_score": int(activity.get("suffer_score", 0) or 0),
        "trimp_score": trimp_data.get("trimp", 0.0),
        "_trimp_data": trimp_data,
    }


# ==========================================
# CANONICAL PIPELINE: Ingest one activity end-to-end
# ==========================================
def _ingest_one_activity(
    act_summary: dict,
    chat_id: str,
    config: dict,
    strava_client: StravaClient,
    source: str = "sync",
) -> dict:
    """
    Canonical single-activity pipeline used by all sync entry points.

    Steps:
    1. build_activity_record + save_run_activity (always, idempotent REPLACE)
    2. RAG gateway — skip detail fetch if memory already exists
    3. get_activity_data → save_run_activity_raw + stream file
    4. compute_stream_metrics → upsert_run_computed_metrics
    5. RAG memorize

    Returns dict: {"loaded": bool, "memorized": bool, "metrics": bool, "skipped_rag": bool}
    """
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    act_id = str(act_summary.get("id"))
    result = {
        "loaded": False,
        "memorized": False,
        "metrics": False,
        "skipped_rag": False,
    }

    # Step 1: Save/overwrite basic record (distance, TRIMP, HR)
    activity_data = build_activity_record(act_summary, max_hr, rest_hr, gender)
    trimp_data = activity_data.pop("_trimp_data")
    try:
        save_run_activity(user_id=chat_id, activity_data=activity_data)
        result["loaded"] = True
    except Exception as exc:
        logger.error(f"[INGEST] Failed to save {act_id}: {exc}")
        return result

    # Step 2: RAG gateway — skip expensive detail fetch if memory exists
    existing = rag_db.collection.get(ids=[act_id])
    if existing and existing["ids"]:
        logger.info(f"[INGEST] Skipped RAG fetch for {act_id} (memory exists)")
        result["skipped_rag"] = True
        return result

    # Step 3: Fetch detailed data (raw stream + full meta)
    logger.info(f"[INGEST] Fetching detail for {act_id} from Strava...")
    act_name, _csv_data, meta_data, stream_raw = strava_client.get_activity_data(act_id)
    if not act_name or not meta_data:
        logger.warning(
            f"[INGEST] No detail returned for {act_id}; skipping RAG/metrics."
        )
        return result

    stream_file_path = (
        save_activity_stream_to_file(chat_id, act_id, stream_raw)
        if stream_raw
        else None
    )
    save_run_activity_raw(
        chat_id,
        act_id,
        act_name,
        meta_data,
        stream_csv="",
        stream_file_path=stream_file_path,
    )

    # Step 4: Compute running science metrics and persist
    if stream_raw:
        try:
            metrics = compute_stream_metrics(stream_raw, meta_data, config, act_name)
            if metrics:
                upsert_run_computed_metrics(act_id, chat_id, metrics)
                result["metrics"] = True
        except Exception as exc:
            logger.error(f"[INGEST] Metrics failed for {act_id}: {exc}")

    # Step 5: Memorize in RAG
    dist_km = activity_data["distance_km"]
    moving_min = activity_data["moving_time_min"]
    avg_hr = activity_data["avg_hr"]
    pace_str = (
        f"{int(moving_min/dist_km)}:{int(((moving_min/dist_km)%1)*60):02d}"
        if dist_km > 0
        else "0:00"
    )

    memory_content = (
        f"[PHÂN TÍCH BÀI CHẠY LỊCH SỬ]\n"
        f"- Cơ bản: Ngày {activity_data['start_date'][:10]}, '{act_name}'. Quãng đường {dist_km:.2f}km, thời gian {moving_min:.1f} phút.\n"
        f"- Tải trọng (Load): Tim TB {int(avg_hr)} bpm (Max {int(activity_data['max_hr'])}). TRIMP: {activity_data['trimp_score']} ({trimp_data.get('intensity_level')}).\n"
        f"- Kỹ thuật (Form): Pace TB {pace_str} min/km."
    )
    try:
        rag_db.memorize(
            doc_id=act_id,
            content=memory_content,
            domain="coach",
            extra_meta={
                "user_id": str(chat_id),
                "type": "run_analysis",
                "source": source,
            },
        )
        result["memorized"] = True
    except Exception as exc:
        logger.error(f"[INGEST] RAG memorize failed for {act_id}: {exc}")

    return result


# ==========================================
# ENTRY POINT 1: Cron auto-harvest (scheduled, lightweight)
# ==========================================
def harvest_data():
    """Auto-harvest background process triggered by Cron."""
    logger.info("[HARVEST] Starting Strava data harvest process...")
    init_db()
    strava_client = StravaClient()
    config = load_config()

    chat_id = get_primary_user_id()
    athlete_id = os.getenv("STRAVA_ATHLETE_ID")
    if not chat_id or not athlete_id:
        return

    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    upsert_user(user_id=chat_id, name="Primary Runner", max_hr=max_hr, rest_hr=rest_hr)

    athlete_stats = strava_client.get_athlete_stats(athlete_id)
    if athlete_stats:
        os.makedirs("data", exist_ok=True)
        with open("data/athlete_stats.json", "w", encoding="utf-8") as f:
            json.dump(athlete_stats, f, indent=4)

    recent_activities = strava_client.get_recent_activities(limit=10)
    for activity in reversed(recent_activities):
        if activity.get("type") not in RUN_TYPES:
            continue
        _ingest_one_activity(activity, chat_id, config, strava_client, source="harvest")
        time.sleep(1)

    logger.info("[HARVEST] Cron Auto-Harvest complete.")


# ==========================================
# ENTRY POINT 2: Manual /sync N or /sync month
# ==========================================
def execute_manual_sync(chat_id: str, limit: int = 3, days_back: int = None):
    """
    Manual sync for recent activities (/sync N, /sync month).
    NOTE: regular def (not async) — runs in BackgroundTasks threadpool.
    """
    logger.info(f"[SYNC] Starting manual sync. Limit: {limit}, Days back: {days_back}")
    send_telegram_msg(
        chat_id,
        f"⏳ Đang thu hoạch dữ liệu Strava ({'30 ngày qua' if days_back else f'{limit} bài gần nhất'})...",
    )

    init_db()
    strava_client = StravaClient()
    config = load_config()

    recent_activities = strava_client.get_recent_activities(limit=limit)
    target_activities = _filter_by_days(recent_activities, days_back)

    if not target_activities:
        send_telegram_msg(chat_id, "⚠️ Không tìm thấy bài chạy nào phù hợp.")
        return

    loaded, memorized, metrics = 0, 0, 0
    for activity in reversed(target_activities):
        if activity.get("type") not in RUN_TYPES:
            continue
        r = _ingest_one_activity(
            activity, chat_id, config, strava_client, source="sync"
        )
        if r["loaded"]:
            loaded += 1
        if r["memorized"]:
            memorized += 1
        if r["metrics"]:
            metrics += 1
        time.sleep(1)

    send_telegram_msg(
        chat_id,
        f"🎉 <b>Hoàn tất Đồng bộ!</b>\n"
        f"💾 Đã lưu DB: {loaded} | 🧠 Ký ức: {memorized} | 📐 Metrics: {metrics}",
    )

    _reconcile(chat_id, strava_client, target_activities, days_back)


# ==========================================
# ENTRY POINT 3: Full-history /sync all
# ==========================================
def execute_sync_all(chat_id: str):
    """
    Full-history sync via Strava pagination (/sync all).
    NOTE: regular def (not async) — runs in BackgroundTasks threadpool.
    """
    logger.info(f"[SYNC-ALL] Starting full-history sync for {chat_id}")
    send_telegram_msg(
        chat_id,
        "⏳ <b>Sync All</b> đang bắt đầu — đang tải toàn bộ lịch sử Strava theo trang...",
    )

    init_db()
    strava_client = StravaClient()
    config = load_config()

    all_activities = strava_client.get_all_activities_paginated(per_page=100)
    runs = [a for a in all_activities if a.get("type") in RUN_TYPES]

    if not runs:
        send_telegram_msg(chat_id, "⚠️ Không tìm thấy bài chạy nào trên Strava.")
        return

    send_telegram_msg(
        chat_id, f"📋 Tìm thấy <b>{len(runs)}</b> bài chạy. Đang xử lý..."
    )

    loaded, memorized, metrics = 0, 0, 0
    for idx, activity in enumerate(reversed(runs), start=1):
        r = _ingest_one_activity(
            activity, chat_id, config, strava_client, source="sync_all"
        )
        if r["loaded"]:
            loaded += 1
        if r["memorized"]:
            memorized += 1
        if r["metrics"]:
            metrics += 1
        if idx % 25 == 0:
            send_telegram_msg(
                chat_id,
                f"⏳ Đã xử lý {idx}/{len(runs)} bài — đã cấy {memorized} ký ức...",
            )
        time.sleep(1)

    send_telegram_msg(
        chat_id,
        f"✅ <b>Sync All hoàn tất!</b>\n"
        f"📊 Tổng bài chạy: {len(runs)}\n"
        f"💾 Đã lưu DB: {loaded}\n"
        f"🧠 Đã cấy ký ức: {memorized}\n"
        f"📐 Đã tính metrics: {metrics}",
    )


# ==========================================
# PRIVATE HELPERS
# ==========================================
def _filter_by_days(activities: list, days_back: int | None) -> list:
    """Filter activity list to those within the last days_back days. Returns all if days_back is None."""
    if not days_back:
        return activities
    cutoff = datetime.now() - timedelta(days=days_back)
    result = []
    for act in activities:
        try:
            act_date = datetime.strptime(act["start_date_local"][:10], "%Y-%m-%d")
            if act_date >= cutoff:
                result.append(act)
        except Exception:
            result.append(act)
    return result


def _reconcile(
    chat_id: str,
    strava_client: StravaClient,
    target_activities: list,
    days_back: int | None,
):
    """Detect activities deleted on Strava and remove local copies (safe mode)."""
    try:
        strava_ids = {
            str(a.get("id")) for a in target_activities if a.get("type") in RUN_TYPES
        }

        if days_back:
            window_start = (datetime.now() - timedelta(days=days_back)).strftime(
                "%Y-%m-%d"
            )
            window_end = datetime.now().strftime("%Y-%m-%d")
        else:
            dates = []
            for a in target_activities:
                try:
                    dates.append(a.get("start_date_local", a.get("start_date"))[:10])
                except Exception:
                    continue
            if not dates:
                return
            window_start, window_end = min(dates), max(dates)

        db_rows = list_run_activity_ids_in_date_range(chat_id, window_start, window_end)
        candidates = [r for r in db_rows if r["activity_id"] not in strava_ids]
        max_verify = int(os.getenv("SYNC_RECONCILE_CAP", "10"))
        removed = 0

        logger.info(
            f"[SYNC-RECONCILE] Window {window_start} -> {window_end}. Candidates: {len(candidates)}"
        )
        for cand in candidates[:max_verify]:
            aid = str(cand["activity_id"])
            res = strava_client.fetch_activity_detail_status(aid)
            status = res.get("status")
            logger.info(f"[SYNC-RECONCILE] {aid}: {status}")
            if status == "not_found":
                try:
                    delete_run_activity(aid)
                    rag_db.forget(doc_id=aid)
                    removed += 1
                except Exception as exc:
                    logger.error(f"[SYNC-RECONCILE] Failed to remove {aid}: {exc}")
            elif status == "rate_limited":
                logger.warning("[SYNC-RECONCILE] Rate limited; stopping.")
                break
            time.sleep(0.5)

        if removed > 0:
            send_telegram_msg(
                chat_id,
                f"🗑️ <b>Reconcile:</b> Đã gỡ {removed} bài không còn trên Strava.",
            )
        else:
            logger.info("[SYNC-RECONCILE] No stale activities removed.")
    except Exception as exc:
        logger.error(f"[SYNC-RECONCILE] Exception: {exc}")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    harvest_data()

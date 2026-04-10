import os
from app.core.user_context import get_primary_user_id
import json
import logging
import time
import io
import pandas as pd
from datetime import datetime, timedelta

from app.agents.coach.strava_client import StravaClient
from app.agents.coach.utils import calculate_trimp, calculate_efficiency_factor, analyze_decoupling
from app.core.config import load_config
from app.core.database import init_db, upsert_user, save_run_activity, save_run_activity_raw, save_message, get_db_connection
from app.services.stream_storage import save_activity_stream_to_file
from app.core.notification import send_telegram_msg
from app.services.rag_memory import rag_db

logger = logging.getLogger("AI_COACH")


# ==========================================
# 🧱 SHARED HELPER: Build activity record from Strava API response
# ==========================================
def build_activity_record(activity: dict, max_hr: int = 185, rest_hr: int = 55, gender: str = "male") -> dict:
    """
    Build a normalized activity_data dict from a raw Strava activity response.
    Single Source of Truth for distance/time/TRIMP calculation across all pipelines
    (Webhook ingest, Cron harvest, Manual sync).
    """
    dist_km = activity.get('distance', 0) / 1000
    moving_min = activity.get('moving_time', 0) / 60
    avg_hr = activity.get('average_heartrate', 0)
    trimp_data = calculate_trimp(moving_min, avg_hr, max_hr, rest_hr, gender)

    return {
        'activity_id': str(activity.get('id', activity.get('activity_id', ''))),
        'name': activity.get('name', 'Unknown Run'),
        'start_date': activity.get('start_date_local', activity.get('start_date', '')),
        'distance_km': round(dist_km, 2),
        'moving_time_min': round(moving_min, 2),
        'avg_hr': int(avg_hr),
        'max_hr': int(activity.get('max_heartrate', 0)),
        'suffer_score': int(activity.get('suffer_score', 0) or 0),
        'trimp_score': trimp_data.get('trimp', 0.0),
        '_trimp_data': trimp_data,  # Internal: carry full trimp result for downstream use
    }


def harvest_data():
    """Auto-harvest background process triggered by Cron"""
    logger.info("[HARVEST] Starting Strava data harvest process...")
    init_db()
    strava_client = StravaClient()
    config = load_config()
    
    chat_id = get_primary_user_id()
    athlete_id = os.getenv("STRAVA_ATHLETE_ID")

    if not chat_id or not athlete_id: return

    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    upsert_user(user_id=chat_id, name="Primary Runner", max_hr=max_hr, rest_hr=rest_hr)

    athlete_stats = strava_client.get_athlete_stats(athlete_id)
    if athlete_stats:
        os.makedirs("data", exist_ok=True)
        with open("data/athlete_stats.json", "w", encoding="utf-8") as file:
            json.dump(athlete_stats, file, indent=4)

    recent_activities = strava_client.get_recent_activities(limit=10)
    for activity in reversed(recent_activities):
        if activity.get('type') in ['Run', 'TrailRun', 'VirtualRun']:
            activity_data = build_activity_record(activity, max_hr, rest_hr, gender)
            save_run_activity(user_id=chat_id, activity_data=activity_data)
    logger.info("[HARVEST] Cron Auto-Harvest complete.")

def execute_manual_sync(chat_id: str, limit: int = 3, days_back: int = None):
    """
    Manual sync flow: Protects Quota and directly injects Python Memory.
    NOTE: This is a regular def (NOT async) because FastAPI BackgroundTasks
    runs functions in a threadpool — async functions would not be awaited properly.
    Uses time.sleep() for Strava API rate limiting between requests.
    """
    logger.info(f"[SYNC] Starting manual sync. Limit: {limit}, Days back: {days_back}")
    send_telegram_msg(chat_id, f"⏳ Đang thu hoạch dữ liệu Strava ({'30 ngày qua' if days_back else f'{limit} bài gần nhất'})...")
    
    init_db()
    strava_client = StravaClient()
    config = load_config()
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    gender = config.get("gender", "male")
    
    recent_activities = strava_client.get_recent_activities(limit=limit)
    target_activities = []
    
    if days_back:
        cutoff_date = datetime.now() - timedelta(days=days_back)
        for act in recent_activities:
            try:
                act_date = datetime.strptime(act['start_date_local'][:10], "%Y-%m-%d")
                if act_date >= cutoff_date: target_activities.append(act)
            except Exception: target_activities.append(act)
    else: target_activities = recent_activities

    if not target_activities:
        send_telegram_msg(chat_id, "⚠️ Không tìm thấy bài chạy nào phù hợp.")
        return


    loaded_count = 0
    analyzed_count = 0
    for activity in reversed(target_activities):
        act_id = str(activity.get('id'))
        if activity.get('type') not in ['Run', 'TrailRun', 'VirtualRun']: continue

        # 1. Always calculate and update SQLite (REPLACE command ensures safe overwrite/healing)
        activity_data = build_activity_record(activity, max_hr, rest_hr, gender)
        trimp_data = activity_data.pop('_trimp_data')
        try:
            save_run_activity(user_id=chat_id, activity_data=activity_data)
        except Exception as exc:
            logger.error(f"[SYNC] Failed to save activity {act_id}: {exc}")
            continue
        loaded_count += 1

        dist_km = activity_data['distance_km']
        moving_min = activity_data['moving_time_min']
        avg_hr = activity_data['avg_hr']
        
        # 2. NEW GATEWAY: Check ChromaDB directly to see if memory already exists
        existing_memory = rag_db.collection.get(ids=[act_id])
        if existing_memory and existing_memory['ids']:
            logger.info(f"[SYNC] Skipped RAG for {act_id} because memory already exists.")
            continue # Skip Streams parsing to save CPU if memory exists
            
        # 3. Inject Python Memory for missing runs (e.g., previous 429 error runs)
        logger.info(f"[SYNC] Patching memory gaps for activity {act_id}...")
        act_name, csv_data, meta_data, stream_raw = strava_client.get_activity_data(act_id)
        if act_name and meta_data:
            stream_file_path = save_activity_stream_to_file(chat_id, act_id, stream_raw) if stream_raw else None
            save_run_activity_raw(chat_id, act_id, act_name, meta_data, stream_csv="", stream_file_path=stream_file_path)
        ef_val, decoupling_val, cadence_avg, stride_avg = 0.0, 0.0, 0, 0.0
        pace_str = f"{int(moving_min/dist_km)}:{int(((moving_min/dist_km)%1)*60):02d}" if dist_km > 0 else "0:00"

        if csv_data:
            try:
                df = pd.read_csv(io.StringIO(csv_data))
                if not df.empty:
                    decoupling_val = analyze_decoupling(df)
                    ef_val = calculate_efficiency_factor(df['Velocity_m_s'].mean() * 60, df['HR_bpm'].mean())
                    
                    # [FIX BUG] Safe handling for Cadence (Avoid NaN errors)
                    c_mean = df['Cadence_spm'].mean() if 'Cadence_spm' in df.columns else 0
                    cadence_avg = int(c_mean) if pd.notna(c_mean) else 0
                    
                    # [FIX BUG] Safe handling for Stride
                    s_mean = df['Stride_m'].mean() if 'Stride_m' in df.columns else 0.0
                    stride_avg = round(s_mean, 2) if pd.notna(s_mean) else 0.0
            except Exception as e:
                logger.error(f"[SYNC] Error analyzing Streams for {act_id}: {e}")

        # [ZONE 3] Standardized template header to match webhooks.py
        memory_content = (
            f"[PHÂN TÍCH BÀI CHẠY LỊCH SỬ]\n"
            f"- Cơ bản: Ngày {activity_data['start_date'][:10]}, '{act_name}'. Quãng đường {dist_km:.2f}km, thời gian {moving_min:.1f} phút.\n"
            f"- Tải trọng (Load): Tim TB {int(avg_hr)} bpm (Max {int(activity_data['max_hr'])}). TRIMP: {activity_data['trimp_score']} ({trimp_data.get('intensity_level')}).\n"
            f"- Hiệu suất (Performance): Pace TB {pace_str} min/km. Chỉ số hiệu quả (EF): {ef_val}. Độ trôi nhịp tim (Decoupling): {decoupling_val}%.\n"
            f"- Kỹ thuật (Form): Cadence {cadence_avg} spm, Sải chân {stride_avg} mét."
        )

        try:
            rag_db.memorize(
                doc_id=act_id,
                content=memory_content,
                domain="coach",
                extra_meta={
                    "user_id": str(chat_id), 
                    "type": "run_analysis",   # [FIX] Changed from 'historical_run' to unify RAG filters
                    "source": "sync_job"      # [NEW] Tag source for debugging
                }
            )
        except Exception as e:
            logger.error(f"[SYNC] Failed to memorize activity {act_id}: {e}")
            
        analyzed_count += 1
        # [FIX P0] Use time.sleep (NOT asyncio.sleep) — this runs in a threadpool via BackgroundTasks
        time.sleep(1)
    send_telegram_msg(chat_id, f"🎉 <b>Hoàn tất Đồng bộ Lịch sử!</b>\nĐã bổ sung {loaded_count} bài chạy vào Cơ sở dữ liệu và cấy {analyzed_count} Gói Ký ức (EF, Decoupling, TRIMP) vào não bộ AI. Số liệu ACWR đã được cân bằng.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    harvest_data()
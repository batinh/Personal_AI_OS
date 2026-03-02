from fastapi import APIRouter, Request, BackgroundTasks
import os
import logging

from app.core.config import load_config
from app.core.notification import send_telegram_msg, send_html_email
from app.agents.coach.agent import analyze_run_with_gemini, handle_telegram_chat
from app.agents.coach.strava_client import StravaClient

# Include execute_manual_sync in imports
from app.agents.coach.harvest import harvest_data, execute_manual_sync
from app.core.state import state
from app.services.scheduler import task_morning_briefing
from app.agents.coach.utils import calculate_trimp
from app.core.database import save_run_activity, delete_run_activity
from app.services.rag_memory import rag_db

router = APIRouter()
logger = logging.getLogger("AI_COACH")

# --- BUSINESS LOGIC (SERVICE LAYER) ---
def _ingest_realtime_run(activity_id: str, act_name: str, meta_data: dict, chat_id: str, config: dict):
    """
    Service function isolating the responsibility of calculating and saving to DB.
    Ensures Data Integrity before calling the LLM.
    """
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))
    dist_km = meta_data.get('distance', 0) / 1000
    moving_min = meta_data.get('moving_time', 0) / 60
    avg_hr = meta_data.get('average_heartrate', 0)
    
    trimp_data = calculate_trimp(moving_min, avg_hr, max_hr, rest_hr)
    
    activity_data = {
        'activity_id': activity_id,
        'name': act_name,
        'start_date': meta_data.get('start_date_local'),
        'distance_km': round(dist_km, 2),
        'moving_time_min': round(moving_min, 2),
        'avg_hr': int(avg_hr),
        'max_hr': int(meta_data.get('max_heartrate', 0)),
        'suffer_score': int(meta_data.get('suffer_score', 0) or 0),
        'trimp_score': trimp_data.get('trimp', 0.0)
    }
    save_run_activity(user_id=chat_id, activity_data=activity_data)
    logger.info(f"[*] Successfully saved Full Data (TRIMP: {trimp_data.get('trimp')}) to SQLite for Activity {activity_id}")

def handle_deleted_activity(activity_id: str):
    """Service to clean up the system when Strava reports an activity deletion."""
    logger.info(f"[*] Cleaning up records for deleted activity: {activity_id}")
    delete_run_activity(activity_id)
    rag_db.forget(doc_id=activity_id)
    
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(chat_id, f"🗑️ **Strava Sync:** Đã tự động xóa bài chạy trùng lặp (ID: {activity_id}) khỏi hệ thống!")

# --- STRAVA WORKFLOW ---
# --- ORCHESTRATION LAYER ---
def run_strava_workflow(activity_id: str):
    if not state.service_active: 
        logger.info(f"[WEBHOOK] Service is PAUSED. Ignoring Activity {activity_id}.")
        return
        
    config = load_config()
    client = StravaClient()
    
    logger.info(f"[*] Fetching data for Activity {activity_id}...")
    try:
        act_name, csv_data, meta_data = client.get_activity_data(activity_id)
    except ValueError:
        return
    
    if not csv_data: return

    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    # 1. Ensure Data Integrity before invoking LLM
    if chat_id:
        _ingest_realtime_run(activity_id, act_name, meta_data, chat_id, config)

    # 2. Handover to AI for semantic analysis and GCS scoring
    logger.info("[*] Sending Data to Gemini...")
    analysis_text = analyze_run_with_gemini(activity_id, act_name, csv_data, meta_data, config)
# =========================================================================
    # [NEW ARCHITECTURE] 2.5: Inject immediately into RAG Memory (Tier 3)
    # =========================================================================
    if analysis_text and chat_id:
        logger.info(f"[*] Memorizing analysis for activity {activity_id} into RAG...")
        
        # [ZONE 3] String template in Vietnamese, variables in English
        memory_content = (
            f"[PHÂN TÍCH BÀI CHẠY]\n"
            f"- Tên bài: {act_name}\n"
            f"- Chi tiết:\n{analysis_text}"
        )
        
        try:
            rag_db.memorize(
                doc_id=str(activity_id),
                content=memory_content,
                domain="coach",
                extra_meta={"user_id": str(chat_id), "type": "run_analysis"}
            )
            logger.info(f"[RAG] Successfully embedded run analysis {activity_id}")
        except Exception as e:
            logger.error(f"[RAG] Failed to memorize activity {activity_id}: {e}")
    # =========================================================================
    #     # 3. Trigger Notifications
    if analysis_text:
        client.update_activity_description(activity_id, analysis_text)
        
        email_body = f"""
        <h2>🏃‍♂️ Run Analysis: {act_name}</h2>
        <p><a href="https://www.strava.com/activities/{activity_id}">View on Strava</a></p>
        <hr>
        <pre style="white-space: pre-wrap; font-family: sans-serif;">{analysis_text}</pre>
        """
        send_html_email(f"Coach Dyno Report: {act_name}", email_body, config)

        if chat_id:
            # [ZONE 3] Telegram text structure remains in Vietnamese
            telegram_msg = (
                f"🏃‍♂️ **Phân tích bài chạy mới:** {act_name}\n\n"
                f"{analysis_text}\n\n"
                f"🔗 [Xem trên Strava](https://www.strava.com/activities/{activity_id})"
            )
            send_telegram_msg(chat_id, telegram_msg)
            logger.info(f"[*] Sent Telegram notification for Activity {activity_id}")     

@router.post("/webhook")
async def strava_event(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    
    if data.get("object_type") == "activity":
        activity_id = str(data.get("object_id"))
        
        # 1. Catch CREATE activity event
        if data.get("aspect_type") == "create":
            background_tasks.add_task(run_strava_workflow, activity_id)
            
        # 2. [ARCHITECTURE UPDATE] Catch DELETE activity event
        elif data.get("aspect_type") == "delete":
            background_tasks.add_task(handle_deleted_activity, activity_id)
            
    return {"status": "ok"}

@router.get("/webhook")
def verify_strava(request: Request):
    if request.query_params.get("hub.verify_token") == os.getenv("VERIFY_TOKEN"):
        return {"hub.challenge": request.query_params.get("hub.challenge")}
    return {"error": "Invalid token"}

# --- TELEGRAM WORKFLOW ---
@router.post("/telegram-webhook")
async def telegram_event(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")
        
        # 1. Catch manual /sync command
        if text.strip().startswith("/sync"):
            parts = text.strip().split()
            limit = 3         # Default: 3 activities
            days_back = None  # Default: no day limit
            
            if len(parts) > 1:
                param = parts[1].lower()
                if param == "month":
                    limit = 50
                    days_back = 30
                elif param.isdigit():
                    limit = int(param)
                    
            # [FIX BUG] Trigger Sync and Return response immediately
            background_tasks.add_task(execute_manual_sync, str(chat_id), limit, days_back)
            return {"status": "ok"}

        # 2. Catch /standup command to test morning briefing
        if text.strip().lower() == "/standup":
            # [ZONE 3]
            send_telegram_msg(chat_id, "⏳ Đang gọi Coach Dyno dậy để rà soát ACWR và lên giáo án hôm nay...")
            background_tasks.add_task(task_morning_briefing)
            return {"status": "ok"}

        # 3. If not a system command, route to AI Chat
        config = load_config()
        background_tasks.add_task(handle_telegram_chat, str(chat_id), text, config)
        
    return {"status": "ok"}
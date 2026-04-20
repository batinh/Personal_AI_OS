from fastapi import APIRouter, Request, BackgroundTasks
import json
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.user_context import get_primary_user_id


class StravaWebhookPayload(BaseModel):
    object_type: str
    object_id: int = Field(..., gt=0)
    aspect_type: str
    owner_id: Optional[int] = None
    subscription_id: Optional[int] = None
    event_time: Optional[int] = None
    updates: Optional[dict[str, Any]] = None

from app.core.config import load_config
from app.core.notification import send_telegram_msg, send_html_email
from app.agents.coach.agent import analyze_run_with_gemini, handle_telegram_chat
from app.agents.coach.strava_client import StravaClient

# Include execute_manual_sync in imports
from app.agents.coach.harvest import harvest_data, execute_manual_sync, execute_sync_all, build_activity_record
from app.core.state import state
from app.services.scheduler import task_morning_briefing
from app.core.database import save_run_activity, save_run_activity_raw, delete_run_activity, upsert_run_computed_metrics
from app.services.rag_memory import rag_db
from app.services.stream_storage import save_activity_stream_to_file
from app.agents.coach.metrics_engine import compute_stream_metrics

router = APIRouter()
from app.core.logging_conf import get_module_logger
logger = get_module_logger("webhook")

# --- BUSINESS LOGIC (SERVICE LAYER) ---
def _ingest_realtime_run(activity_id: str, act_name: str, meta_data: dict, chat_id: str, config: dict):
    """
    Service function isolating the responsibility of calculating and saving to DB.
    Ensures Data Integrity before calling the LLM.
    Uses shared build_activity_record() to avoid DRY violation.
    """
    max_hr = int(config.get("max_hr", 185))
    rest_hr = int(config.get("rest_hr", 55))

    # Reuse shared helper — Single Source of Truth for activity calculation
    activity_data = build_activity_record(meta_data, max_hr, rest_hr)
    # Override activity_id and name from webhook context (more reliable than meta_data keys)
    activity_data['activity_id'] = activity_id
    activity_data['name'] = act_name
    activity_data.pop('_trimp_data', None)

    save_run_activity(user_id=chat_id, activity_data=activity_data)
    logger.info(f"[*] Successfully saved Full Data (TRIMP: {activity_data['trimp_score']}) to SQLite for Activity {activity_id}")

def handle_deleted_activity(activity_id: str):
    """Service to clean up the system when Strava reports an activity deletion."""
    logger.info(f"[*] Cleaning up records for deleted activity: {activity_id}")
    delete_run_activity(activity_id)
    rag_db.forget(doc_id=activity_id)
    
    chat_id = get_primary_user_id()
    if chat_id:
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(chat_id, f"🗑️ <b>Strava Sync:</b> Đã tự động xóa bài chạy trùng lặp (ID: {activity_id}) khỏi hệ thống!")

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
        act_name, csv_data, meta_data, stream_raw = client.get_activity_data(activity_id)
    except (ValueError, TypeError):
        return

    if not stream_raw and not csv_data:
        return

    chat_id = get_primary_user_id()

    # 1. Ensure Data Integrity before invoking LLM
    if chat_id:
        _ingest_realtime_run(activity_id, act_name, meta_data, chat_id, config)
        # Persist metadata in DB; save full raw streams to file and store path
        stream_file_path = None
        if stream_raw:
            stream_file_path = save_activity_stream_to_file(chat_id, activity_id, stream_raw)
        save_run_activity_raw(chat_id, activity_id, act_name, meta_data, stream_csv="", stream_file_path=stream_file_path)

        # 1.5. Compute and persist running science metrics from stream data
        if stream_raw:
            try:
                metrics = compute_stream_metrics(stream_raw, meta_data, config, act_name)
                if metrics:
                    upsert_run_computed_metrics(activity_id, chat_id, metrics)
                    logger.info(f"[METRICS] Stored computed metrics for activity {activity_id}")
            except Exception as e:
                logger.error(f"[METRICS] Failed to compute metrics for activity {activity_id}: {e}")

    # 2. Handover to AI for semantic analysis and GCS scoring
    logger.info("[*] Sending Data to Gemini...")
    analysis_text = analyze_run_with_gemini(activity_id, act_name, meta_data, config)

    # [NEW ARCHITECTURE] 2.5: Inject immediately into RAG Memory (Tier 3)
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

    # 3. Trigger Notifications
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
                f"🏃‍♂️ <b>Phân tích bài chạy mới:</b> {act_name}\n\n"
                f"{analysis_text}\n\n"
                f"🔗 Xem trên Strava: https://www.strava.com/activities/{activity_id}"
            )
            send_telegram_msg(chat_id, telegram_msg)
            logger.info(f"[*] Sent Telegram notification for Activity {activity_id}")     

@router.post("/webhook")
async def strava_event(payload: StravaWebhookPayload, background_tasks: BackgroundTasks):
    if payload.object_type == "activity":
        activity_id = str(payload.object_id)

        if payload.aspect_type == "create":
            background_tasks.add_task(run_strava_workflow, activity_id)
        elif payload.aspect_type == "delete":
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
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        logger.warning("[WEBHOOK] Malformed JSON in telegram-webhook body; ignoring.")
        return {"status": "ok"}
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")
        
        # 1. Catch manual /sync command
        if text.strip().startswith("/sync"):
            parts = text.strip().split()
            param = parts[1].lower() if len(parts) > 1 else ""

            if param == "all":
                send_telegram_msg(str(chat_id), "🚀 <b>Sync All</b>: đang khởi động đồng bộ toàn bộ lịch sử Strava. Có thể mất vài phút...")
                background_tasks.add_task(execute_sync_all, str(chat_id))
            else:
                limit = 3         # Default: 3 activities
                days_back = None  # Default: no day limit
                if param == "month":
                    limit = 50
                    days_back = 30
                elif param.isdigit():
                    limit = int(param)
                background_tasks.add_task(execute_manual_sync, str(chat_id), limit, days_back)
            return {"status": "ok"}

        # 2. Catch /standup command to test morning briefing
        if text.strip().lower() == "/standup":
            # [ZONE 3]
            send_telegram_msg(chat_id, "⏳ Đang gọi Coach Dyno dậy để rà soát ACWR và lên giáo án hôm nay...")
            background_tasks.add_task(task_morning_briefing)
            return {"status": "ok"}

        # 3. Catch /news command for manual news trigger
        if text.strip().lower().startswith("/news"):
            from app.agents.news.telegram_handler import handle_news_command
            config = load_config()
            parts = text.strip().split()
            args = parts[1:] if len(parts) > 1 else []
            background_tasks.add_task(handle_news_command, str(chat_id), args, config)
            return {"status": "ok"}

        # 4. Route free-text to the correct agent (@news/@tin → news, default → coach)
        config = load_config()
        from app.services.telegram_router import route_message
        agent, cleaned_text = route_message(text)
        if agent == "news":
            from app.agents.news.telegram_handler import handle_news_chat
            background_tasks.add_task(handle_news_chat, str(chat_id), cleaned_text, config)
        else:
            background_tasks.add_task(handle_telegram_chat, str(chat_id), text, config)
        
    return {"status": "ok"}
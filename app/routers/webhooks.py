import json
import os
from typing import Any, Optional

from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel, Field

from app.core.user_context import get_primary_user_id
from app.core.config import load_config
from app.core.notification import (
    send_telegram_msg,
    send_html_email,
    send_inline_keyboard_menu,
    answer_callback_query,
)
from app.core.state import state
from app.core.database import (
    save_run_activity,
    save_run_activity_raw,
    delete_run_activity,
    upsert_run_computed_metrics,
    get_plan_for_date,
)
from app.core.logging_conf import get_module_logger
from app.agents.coach.agent import analyze_run_with_gemini, handle_telegram_chat
from app.agents.coach.strava_client import StravaClient
from app.agents.coach.harvest import (
    execute_manual_sync,
    execute_sync_all,
    build_activity_record,
)
from app.agents.coach.metrics_engine import compute_stream_metrics
from app.services.scheduler import task_morning_briefing
from app.services.rag_memory import rag_db
from app.services.stream_storage import save_activity_stream_to_file


class StravaWebhookPayload(BaseModel):
    object_type: str
    object_id: int = Field(..., gt=0)
    aspect_type: str
    owner_id: Optional[int] = None
    subscription_id: Optional[int] = None
    event_time: Optional[int] = None
    updates: Optional[dict[str, Any]] = None


router = APIRouter()
logger = get_module_logger("webhook")


# --- BUSINESS LOGIC (SERVICE LAYER) ---
def _ingest_realtime_run(
    activity_id: str, act_name: str, meta_data: dict, chat_id: str, config: dict
):
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
    activity_data["activity_id"] = activity_id
    activity_data["name"] = act_name
    activity_data.pop("_trimp_data", None)

    save_run_activity(user_id=chat_id, activity_data=activity_data)
    logger.info(
        f"[*] Successfully saved Full Data (TRIMP: {activity_data['trimp_score']}) to SQLite for Activity {activity_id}"
    )


def handle_deleted_activity(activity_id: str):
    """Service to clean up the system when Strava reports an activity deletion."""
    logger.info(f"[*] Cleaning up records for deleted activity: {activity_id}")
    delete_run_activity(activity_id)
    rag_db.forget(doc_id=activity_id)

    chat_id = get_primary_user_id()
    if chat_id:
        # [ZONE 3] User-facing notification remains in Vietnamese
        send_telegram_msg(
            chat_id,
            f"🗑️ <b>Strava Sync:</b> Đã tự động xóa bài chạy trùng lặp (ID: {activity_id}) khỏi hệ thống!",
        )


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
        act_name, csv_data, meta_data, stream_raw = client.get_activity_data(
            activity_id
        )
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
            stream_file_path = save_activity_stream_to_file(
                chat_id, activity_id, stream_raw
            )
        save_run_activity_raw(
            chat_id,
            activity_id,
            act_name,
            meta_data,
            stream_csv="",
            stream_file_path=stream_file_path,
        )

        # 1.5. Compute and persist running science metrics from stream data
        if stream_raw:
            try:
                metrics = compute_stream_metrics(
                    stream_raw, meta_data, config, act_name
                )
                if metrics:
                    upsert_run_computed_metrics(activity_id, chat_id, metrics)
                    logger.info(
                        f"[METRICS] Stored computed metrics for activity {activity_id}"
                    )
            except Exception as e:
                logger.error(
                    f"[METRICS] Failed to compute metrics for activity {activity_id}: {e}"
                )

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
                extra_meta={"user_id": str(chat_id), "type": "run_analysis"},
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

            # Send RPE (Rate of Perceived Exertion) keyboard after analysis
            try:
                rpe_text = "Cảm giác bài chạy vừa rồi thế nào? (1=rất dễ, 10=kiệt sức)"
                rpe_buttons = [
                    [
                        {"text": str(i), "callback_data": f"rpe:{activity_id}:{i}"}
                        for i in range(1, 6)
                    ],
                    [
                        {"text": str(i), "callback_data": f"rpe:{activity_id}:{i}"}
                        for i in range(6, 11)
                    ],
                ]
                send_inline_keyboard_menu(str(chat_id), rpe_text, rpe_buttons)
                logger.info(f"[*] Sent RPE keyboard for Activity {activity_id}")
            except Exception as e:
                logger.error(
                    f"[*] Failed to send RPE keyboard for Activity {activity_id}: {e}"
                )
    elif chat_id:
        # Gemini analysis failed (timeout/API error) — still notify user the run was saved
        dist_km = round(meta_data.get("distance", 0) / 1000, 2)
        moving_min = round(meta_data.get("moving_time", 0) / 60, 1)
        avg_hr = int(meta_data.get("average_heartrate", 0) or 0)
        fallback_msg = (
            f"✅ <b>Đã lưu bài chạy:</b> {act_name}\n"
            f"📏 {dist_km}km · ⏱ {moving_min} phút · ❤️ {avg_hr} bpm\n"
            f"⚠️ Phân tích AI tạm thời không khả dụng (Gemini timeout). Dữ liệu đã được lưu.\n"
            f"🔗 https://www.strava.com/activities/{activity_id}"
        )
        send_telegram_msg(chat_id, fallback_msg)
        logger.warning(
            f"[*] Sent fallback Telegram (no analysis) for Activity {activity_id}"
        )


@router.post("/webhook")
async def strava_event(
    payload: StravaWebhookPayload, background_tasks: BackgroundTasks
):
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


# --- TELEGRAM CALLBACK QUERY HANDLER ---
def handle_telegram_callback(callback_query: dict) -> None:
    """Handle callback_query from inline button presses.

    Routes callback data to appropriate handler:
    - rpe:<activity_id>:<score> → save RPE score to run_activities
    - plan:accept → accept weekly plan
    - plan:reject → reject plan and prompt for reason
    """
    callback_data = callback_query.get("data", "")
    user_id = str(callback_query.get("from", {}).get("id", ""))
    callback_id = callback_query.get("id", "")

    if not all([callback_data, user_id, callback_id]):
        logger.warning("[TELEGRAM] Malformed callback_query; missing required fields.")
        return

    logger.info(f"[TELEGRAM] Callback: {callback_data} from {user_id}")

    if callback_data.startswith("rpe:"):
        # RPE callback: rpe:activity_id:score
        parts = callback_data.split(":")
        if len(parts) >= 3:
            try:
                activity_id = parts[1]
                score = int(parts[2])

                # Save RPE to run_activities table
                from app.core.database import get_db

                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE run_activities SET rpe_score = ? WHERE activity_id = ?",
                        (score, activity_id),
                    )
                    conn.commit()

                logger.info(f"[TELEGRAM] Saved RPE {score} to activity {activity_id}")

                # Get the run plan to check workout type for overtraining alert
                run_date_str = None
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        "SELECT start_date FROM run_activities WHERE activity_id = ?",
                        (activity_id,),
                    )
                    row = c.fetchone()
                    if row:
                        start_date_str = row["start_date"]
                        if start_date_str:
                            run_date_str = start_date_str[:10]

                # Check for overtraining: RPE > 8 on Easy run
                if run_date_str:
                    plan = get_plan_for_date(user_id, run_date_str)
                    if plan and score > 8:
                        workout_type = plan.get("workout_title", "").lower()
                        if "easy" in workout_type or "recovery" in workout_type:
                            send_telegram_msg(
                                user_id,
                                "⚠️ RPE cao trên bài chạy dễ — có thể đang bị quá tải. Hãy nghỉ ngơi hoặc giảm cường độ ngày mai.",
                            )

                # Answer callback to clear loading spinner
                answer_callback_query(callback_id, text=f"RPE {score} đã lưu ✓")

            except (ValueError, IndexError) as e:
                logger.error(
                    f"[TELEGRAM] Invalid RPE callback data: {callback_data} - {e}"
                )
                answer_callback_query(callback_id, text="Lỗi lưu RPE")

    elif callback_data == "plan:accept":
        # Plan accept callback
        send_telegram_msg(
            user_id,
            "✅ Đã chấp nhận giáo án. Bắt đầu luyện tập theo kế hoạch ngay hôm nay!",
        )
        answer_callback_query(callback_id, text="Đã chấp nhận giáo án ✓")
        # TODO: implement accept_weekly_plan logic in future phase

    elif callback_data == "plan:reject":
        # Plan reject callback
        send_telegram_msg(
            user_id,
            "❌ Từ chối giáo án. Gõ /reject <lý do> để cung cấp phản hồi chi tiết.",
        )
        answer_callback_query(callback_id, text="Từ chối giáo án")
        # TODO: implement reject_weekly_plan logic in future phase

    else:
        logger.warning(f"[TELEGRAM] Unknown callback data: {callback_data}")
        answer_callback_query(callback_id, text="Lệnh không được nhận dạng")


# --- TELEGRAM WORKFLOW ---
@router.post("/telegram-webhook")
async def telegram_event(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        logger.warning("[WEBHOOK] Malformed JSON in telegram-webhook body; ignoring.")
        return {"status": "ok"}
    if not isinstance(data, dict):
        return {"status": "ok"}

    # Route callback_query (inline button presses)
    if "callback_query" in data:
        background_tasks.add_task(handle_telegram_callback, data["callback_query"])
        return {"status": "ok"}

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        # 1. Catch manual /sync command
        if text.strip().startswith("/sync"):
            parts = text.strip().split()
            param = parts[1].lower() if len(parts) > 1 else ""

            if param == "all":
                send_telegram_msg(
                    str(chat_id),
                    "🚀 <b>Sync All</b>: đang khởi động đồng bộ toàn bộ lịch sử Strava. Có thể mất vài phút...",
                )
                background_tasks.add_task(execute_sync_all, str(chat_id))
            else:
                limit = 3  # Default: 3 activities
                days_back = None  # Default: no day limit
                if param == "month":
                    limit = 50
                    days_back = 30
                elif param.isdigit():
                    limit = int(param)
                background_tasks.add_task(
                    execute_manual_sync, str(chat_id), limit, days_back
                )
            return {"status": "ok"}

        # 2. Catch /standup command to test morning briefing
        if text.strip().lower() == "/standup":
            # [ZONE 3]
            send_telegram_msg(
                chat_id,
                "⏳ Đang gọi Coach Dyno dậy để rà soát ACWR và lên giáo án hôm nay...",
            )
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

            background_tasks.add_task(
                handle_news_chat, str(chat_id), cleaned_text, config
            )
        else:
            background_tasks.add_task(handle_telegram_chat, str(chat_id), text, config)

    return {"status": "ok"}

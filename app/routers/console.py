# app/routers/console.py
# TinhN AI OS — Unified Control Console
# Merges: admin (settings, system control) + dashboard (metrics, charts, training log) + memory view

import json
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import load_config, save_config
from app.core.notification import send_html_email
from app.core.logging_conf import (
    log_capture_string,
    apply_log_levels,
    get_effective_log_levels,
    KNOWN_DOMAINS,
    get_module_logger,
)
from app.core.state import state
from app.core.user_context import get_primary_user_id
from app.core.admin_auth import verify_admin
from app.core.database import (
    get_db_connection,
    get_training_loads,
    get_historical_training_loads,
    get_all_active_memories,
)
from app.agents.coach.utils import calculate_acwr
from app.services.scheduler import reload_scheduler
from app.services.coverage_metrics import load_coverage_report, report_to_dict
from app.services.requirements_coverage import load_requirements_matrix

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = get_module_logger("admin")


# ==========================================
# 🖥️ UNIFIED CONSOLE — GET
# ==========================================
@router.get("/console", response_class=HTMLResponse)
async def console_page(
    request: Request, tab: str = "overview", username: str = Depends(verify_admin)
):
    """Render the TinhN AI OS unified control console."""
    config = load_config()
    chat_id = get_primary_user_id()

    # --- Overview: training metrics ---
    loads = get_training_loads(chat_id)
    acwr_results = calculate_acwr(loads["acute_load_7d"], loads["chronic_load_28d"])
    load_history = get_historical_training_loads(chat_id, days=30)

    # --- Training Log: last 20 runs ---
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """SELECT start_date, name, distance_km, trimp_score, gcs_score, avg_hr
           FROM run_activities WHERE user_id = ?
           ORDER BY start_date DESC LIMIT 20""",
        (str(chat_id),),
    )
    activities = [dict(row) for row in c.fetchall()]
    conn.close()

    # --- Memory tab ---
    memories = get_all_active_memories(chat_id)

    # --- Testing tab: coverage + test metrics ---
    try:
        coverage_data = report_to_dict(load_coverage_report())
    except FileNotFoundError:
        coverage_data = None
    except Exception:
        coverage_data = None

    try:
        req_matrix = load_requirements_matrix()
    except Exception:
        req_matrix = None

    # --- System tab ---
    logs_text = "\n".join(list(log_capture_string))

    news_config = config.get("news_agent", {})
    log_levels = get_effective_log_levels()

    return templates.TemplateResponse(
        request,
        "console.html",
        {
            "active_tab": tab,
            # Overview
            "acwr": acwr_results,
            "loads": loads,
            "load_history": load_history,
            "activities": activities[::-1],  # oldest first for charts
            "activities_desc": activities,  # newest first for table
            # Memory
            "memories": memories,
            # Settings / System
            "config": config,
            "logs": logs_text,
            "service_active": state.service_active,
            # News settings
            "news_config": news_config,
            # Logging settings
            "log_levels": log_levels,
            "log_domains": KNOWN_DOMAINS,
            # Testing tab
            "coverage_data": coverage_data,
            "req_matrix": req_matrix,
        },
    )


# ==========================================
# 💾 SAVE SETTINGS — POST
# ==========================================
@router.post("/console/save")
async def console_save(
    request: Request,
    system_instruction: str = Form(...),
    user_profile: str = Form(...),
    task_description: str = Form(""),
    analysis_requirements: str = Form(""),
    report_structure: str = Form(""),
    output_format: str = Form(""),
    max_hr: int = Form(185),
    rest_hr: int = Form(55),
    race_date: Optional[str] = Form(None),
    race_distance_km: float = Form(21.1),
    threshold_pace_per_km: int = Form(0),
    gender: str = Form("male"),
    current_goal: str = Form(""),
    briefing_time: str = Form("06:00"),
    backup_time: str = Form("02:00"),
    harvest_hours: str = Form("0,6,12,18"),
    harvest_minute: str = Form("15"),
    email_enabled: Optional[str] = Form(None),
    debug_mode: Optional[str] = Form(None),
    model_name: str = Form("models/gemini-flash-latest"),
    username: str = Depends(verify_admin),
):
    config = load_config()
    config["system_instruction"] = system_instruction
    config["user_profile"] = user_profile
    config["task_description"] = task_description
    config["analysis_requirements"] = analysis_requirements
    config["report_structure"] = report_structure
    config["output_format"] = output_format
    config["max_hr"] = max_hr
    config["rest_hr"] = rest_hr
    config["race_date"] = race_date
    config["race_distance_km"] = race_distance_km
    config["threshold_pace_per_km"] = threshold_pace_per_km
    config["gender"] = gender
    config["current_goal"] = current_goal
    config["scheduler"] = {
        "briefing_time": briefing_time,
        "backup_time": backup_time,
        "harvest_hours": harvest_hours,
        "harvest_minute": harvest_minute,
    }
    if "email_config" not in config:
        config["email_config"] = {}
    config["email_config"]["enabled"] = email_enabled == "on"
    config["debug_mode"] = debug_mode == "on"
    config["model_name"] = model_name
    save_config(config)
    reload_scheduler()
    logger.info(f"[CONSOLE] User '{username}' saved configuration.")
    return RedirectResponse(url="/console?tab=settings", status_code=303)


@router.get("/console/save", include_in_schema=False)
async def console_save_redirect(username: str = Depends(verify_admin)):
    return RedirectResponse(url="/console?tab=settings", status_code=303)


# ==========================================
# 📰 NEWS SETTINGS — POST
# ==========================================
@router.post("/console/save-news")
async def console_save_news(
    request: Request,
    username: str = Depends(verify_admin),
):
    """Save news agent configuration from the console News tab."""
    form = await request.form()

    config = load_config()
    news_cfg = config.get("news_agent", {})

    # --- Basic toggles & scalars ---
    news_cfg["enabled"] = form.get("news_enabled") == "on"
    news_cfg["news_model"] = (
        form.get("news_model", "models/gemini-flash-latest").strip()
        or "models/gemini-flash-latest"
    )
    news_cfg["morning_time"] = form.get("morning_time", "06:30").strip()
    news_cfg["afternoon_time"] = form.get("afternoon_time", "17:30").strip()
    news_cfg["evening_time"] = form.get("evening_time", "20:00").strip()
    news_cfg["telegram_chat_id"] = form.get("news_telegram_chat_id", "").strip()

    # --- Per-session toggles ---
    news_cfg["sessions"] = {
        "morning": form.get("session_morning") == "on",
        "afternoon": form.get("session_afternoon") == "on",
        "evening": form.get("session_evening") == "on",
    }

    # --- Topics (serialized as JSON by client-side JS) ---
    topics_json = form.get("topics_json", "[]")
    try:
        topics = json.loads(topics_json)
        if isinstance(topics, list):
            news_cfg["topics"] = [
                {"emoji": str(t.get("emoji", "📰")), "name": str(t.get("name", ""))}
                for t in topics
                if isinstance(t, dict) and t.get("name", "").strip()
            ]
    except (json.JSONDecodeError, ValueError):
        logger.warning("[CONSOLE] Invalid topics_json — keeping existing topics.")

    # Remove deprecated RSS-era keys if present
    for old_key in (
        "watch_interval_minutes",
        "alert_threshold",
        "digest_threshold",
        "topic_cooldown_hours",
        "max_articles_per_feed",
        "feeds",
        "shock_threshold",
    ):
        news_cfg.pop(old_key, None)

    # --- Interest profile (serialized as JSON by client-side JS) ---
    profile_json = form.get("interest_profile_json", "{}")
    try:
        profile = json.loads(profile_json)
        if isinstance(profile, dict):
            news_cfg["interest_profile"] = profile
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "[CONSOLE] Invalid interest_profile_json — keeping existing profile."
        )

    config["news_agent"] = news_cfg
    save_config(config)
    reload_scheduler()
    logger.info(f"[CONSOLE] User '{username}' saved news agent configuration.")
    return RedirectResponse(url="/console?tab=news", status_code=303)


def _parse_int(value: Optional[str], default: int) -> int:
    """Parse a form string value to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ==========================================
# 🛠️ SETUP: COACHING PROFILE + GARMIN
# ==========================================
def _mask_email(email: str) -> str:
    """Mask email for display (show only first 3 chars of local part)."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return local[:3] + "***@" + domain


def _is_circuit_open() -> bool:
    """Check if Garmin circuit breaker is currently open."""
    from app.agents.coach.garmin_client import _is_circuit_open as garmin_circuit_open
    return garmin_circuit_open()


@router.get("/console/setup")
async def get_setup_state(request: Request, _=Depends(verify_admin)):
    """Return current coaching profile + garmin status for pre-filling the web form."""
    from app.core.config import load_config
    from app.agents.coach.garmin_client import GarminClient
    from app.core.secrets import has_garmin_credentials, decrypt_garmin_credentials
    from app.core.database import get_garmin_daily_metrics, get_athlete_state
    from app.core.user_context import get_primary_user_id
    import datetime
    import os
    from fastapi.responses import JSONResponse

    cfg = load_config()
    user_id = get_primary_user_id()

    # Coaching profile from config
    _DAY_ABBRS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    rest_days_raw = cfg.get("setup", {}).get("preferred_rest_days", [])
    # Normalise: stored as int weekday indices (0=Mon) or already as abbr strings
    rest_days_abbr = [
        _DAY_ABBRS[d] if isinstance(d, int) and 0 <= d < 7 else str(d)
        for d in rest_days_raw
    ]

    coaching = {
        "race_distance_km": cfg.get("race_distance_km"),
        "race_date": cfg.get("race_date"),
        "race_target_time_min": cfg.get("race_target_time_min"),
        "current_weekly_km": cfg.get("setup", {}).get("current_weekly_km", cfg.get("current_weekly_km")),
        "training_days_per_week": cfg.get("setup", {}).get("training_days_per_week"),
        "preferred_rest_days": rest_days_abbr,
        "is_complete": bool(cfg.get("race_date") and cfg.get("race_distance_km")),
    }

    # Garmin status (no plaintext password)
    garmin_cfg = cfg.get("garmin", {})
    has_creds = has_garmin_credentials() or bool(os.getenv("GARMIN_EMAIL"))

    # Get last sync from DB
    today = datetime.date.today().isoformat()
    metrics = get_garmin_daily_metrics(user_id, today)
    last_sync = metrics.get("created_at") if metrics else None

    from app.agents.coach.garmin_client import has_oauth_token as _has_oauth_token

    garmin = {
        "is_enabled": garmin_cfg.get("enabled", False),
        "has_credentials": has_creds,
        "has_oauth_token": _has_oauth_token(),
        "email_masked": _mask_email(decrypt_garmin_credentials()[0]) if has_garmin_credentials() else None,
        "last_sync": last_sync,
        "sync_time": garmin_cfg.get("sync_time", "05:45"),
        "circuit_open": _is_circuit_open(),
        "is_connected": (_has_oauth_token() or has_creds) and not _is_circuit_open(),
    }

    return JSONResponse({"coaching": coaching, "garmin": garmin})


@router.post("/console/setup/coaching")
async def save_coaching_profile(request: Request, _=Depends(verify_admin)):
    """Validate and save coaching profile from web form."""
    from app.agents.coach.setup_validators import (
        validate_distance, validate_date, validate_time,
        validate_kmweek, validate_days, validate_rest_days,
    )
    from app.agents.coach.setup_flow import finalize_setup
    from app.core.user_context import get_primary_user_id
    from fastapi.responses import JSONResponse

    body = await request.json()
    errors = {}

    # Validators return (ok, value, error_msg) 3-tuples
    dist_ok, dist_val, dist_err = validate_distance(str(body.get("race_distance_km", "")))
    if not dist_ok:
        errors["race_distance_km"] = dist_err or "Cự ly không hợp lệ (ví dụ: 10, 21.1, 42.2)"

    date_ok, date_val, date_err = validate_date(str(body.get("race_date", "")))
    if not date_ok:
        errors["race_date"] = date_err or "Ngày không hợp lệ hoặc chưa đủ 4 tuần từ hôm nay"

    km_ok, km_val, km_err = validate_kmweek(str(body.get("current_weekly_km", "")))
    if not km_ok:
        errors["current_weekly_km"] = km_err or "Số km không hợp lệ (0–200)"

    days_ok, days_val, days_err = validate_days(str(body.get("training_days_per_week", "")))
    if not days_ok:
        errors["training_days_per_week"] = days_err or "Số ngày tập phải từ 3–6"

    # Validate time uses race_distance_km for pace sanity check
    time_ok, time_val, time_err = validate_time(
        str(body.get("race_target_time_min", "")),
        race_distance_km=dist_val or 21.1,
    )
    if not time_ok:
        errors["race_target_time_min"] = time_err or "Thời gian không hợp lệ (ví dụ: 1:45 hoặc 105)"

    # preferred_rest_days arrives as JSON array ["sat", "sun"] — join for validator
    rest_days_raw = body.get("preferred_rest_days", [])
    if isinstance(rest_days_raw, list):
        rest_days_str = ", ".join(str(d) for d in rest_days_raw)
    else:
        rest_days_str = str(rest_days_raw)
    rest_ok, rest_val, rest_err = validate_rest_days(rest_days_str, training_days=days_val or 5)
    if not rest_ok:
        errors["preferred_rest_days"] = rest_err or "Ngày nghỉ không hợp lệ"

    if errors:
        return JSONResponse({"success": False, "errors": errors}, status_code=422)

    # Write to config using same path as Telegram wizard
    collected_data = {
        "race_distance_km": dist_val,
        "race_date": date_val,
        "race_target_time_min": time_val,
        "current_weekly_km": km_val,
        "training_days_per_week": days_val,
        "preferred_rest_days": rest_val,
    }
    user_id = get_primary_user_id()
    finalize_setup(user_id, collected_data)

    return JSONResponse({"success": True, "message": "Đã lưu hồ sơ tập luyện thành công."})


@router.post("/console/setup/garmin")
async def save_garmin_credentials(request: Request, _=Depends(verify_admin)):
    """Store Garmin credentials encrypted and test connection."""
    from app.agents.coach.garmin_client import GarminClient
    from app.core.secrets import encrypt_garmin_credentials
    from fastapi.responses import JSONResponse

    body = await request.json()
    email = body.get("email", "").strip()
    password = body.get("password", "")
    enable = bool(body.get("enable", True))

    logger.info(f"[GARMIN-SETUP] POST /console/setup/garmin — email={email[:3] + '***' if email else '(empty)'} enable={enable}")

    if not email or not password:
        logger.warning("[GARMIN-SETUP] Missing email or password in request body")
        return JSONResponse({"success": False, "message": "Email và password là bắt buộc."}, status_code=400)

    # Store encrypted BEFORE testing (so GarminClient can read them)
    encrypt_garmin_credentials(email, password)
    logger.info("[GARMIN-SETUP] Credentials encrypted and stored — starting connection test (max 30s)")

    # Test connection
    client = GarminClient()
    import time
    t0 = time.monotonic()
    success, error = client.test_connection(timeout_sec=30)
    elapsed = round(time.monotonic() - t0, 1)
    logger.info(f"[GARMIN-SETUP] test_connection result: success={success} elapsed={elapsed}s error={error!r}")

    if not success:
        # Still keep credentials but report failure (user may retry)
        return JSONResponse({
            "success": False,
            "is_connected": False,
            "message": f"Kết nối thất bại sau {elapsed}s: {error}. Thông tin đã lưu, thử lại sau.",
        })

    # Update garmin.enabled in config
    cfg = load_config()
    cfg.setdefault("garmin", {})["enabled"] = enable
    save_config(cfg)

    return JSONResponse({
        "success": True,
        "is_connected": True,
        "message": "Kết nối Garmin thành công! Đồng bộ sẽ chạy lúc 05:45 sáng.",
    })


@router.post("/console/setup/garmin/upload-token")
async def upload_garmin_token(request: Request, _=Depends(verify_admin)):
    """Accept an OAuth token JSON string exported from garmin_auth_local.py."""
    from app.agents.coach.garmin_client import save_oauth_token, has_oauth_token, GarminClient, _garmin_client_instance
    from fastapi.responses import JSONResponse

    body = await request.json()
    token_json = (body.get("token_json") or "").strip()

    if not token_json:
        return JSONResponse({"success": False, "message": "token_json là bắt buộc."}, status_code=400)

    # Basic sanity check — must be JSON with at least one Garmin key
    try:
        parsed = json.loads(token_json)
        if not any(k in parsed for k in ("di_token", "di_refresh_token", "oauth_token", "access_token")):
            return JSONResponse(
                {"success": False, "message": "JSON không hợp lệ — không chứa Garmin OAuth token."},
                status_code=400,
            )
    except json.JSONDecodeError:
        return JSONResponse({"success": False, "message": "Không phải JSON hợp lệ."}, status_code=400)

    save_oauth_token(token_json)

    # Reset singleton so next use picks up the new token
    import app.agents.coach.garmin_client as _gc_mod
    _gc_mod._garmin_client_instance = None

    # Quick verification (with timeout) using the new token
    client = GarminClient()
    import concurrent.futures
    def _verify():
        c = client._get_client()
        return c.get_full_name() or "OK"

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_verify)
    executor.shutdown(wait=False)
    try:
        name = future.result(timeout=15)
        logger.info(f"[GARMIN-SETUP] OAuth token upload verified — user={name}")
        return JSONResponse({"success": True, "message": f"Token hợp lệ! Đã xác thực: {name}"})
    except concurrent.futures.TimeoutError:
        logger.warning("[GARMIN-SETUP] OAuth token upload: verification timed out (token saved anyway)")
        return JSONResponse({"success": True, "message": "Token đã lưu. Xác minh timeout — thử đồng bộ sau."})
    except Exception as e:
        logger.error(f"[GARMIN-SETUP] OAuth token upload: verification failed — {e}")
        return JSONResponse({"success": False, "message": f"Token không hoạt động: {e}"}, status_code=400)


@router.get("/console/setup/garmin/status")
async def get_garmin_status(request: Request, _=Depends(verify_admin)):
    """Return Garmin connection status without exposing credentials."""
    from app.agents.coach.garmin_client import _TOKEN_FILE, has_oauth_token
    from app.core.secrets import has_garmin_credentials, decrypt_garmin_credentials
    from fastapi.responses import JSONResponse
    from app.core.database import get_garmin_daily_metrics
    from app.core.user_context import get_primary_user_id
    import datetime
    import os

    cfg = load_config()
    user_id = get_primary_user_id()
    today = datetime.date.today().isoformat()
    metrics = get_garmin_daily_metrics(user_id, today)

    has_creds = has_garmin_credentials() or bool(os.getenv("GARMIN_EMAIL"))
    has_tokens = _TOKEN_FILE.exists()
    has_oauth = has_oauth_token()
    circuit_open = _is_circuit_open()
    is_connected = (has_oauth or (has_creds and has_tokens)) and not circuit_open

    return JSONResponse({
        "is_enabled": cfg.get("garmin", {}).get("enabled", False),
        "is_connected": is_connected,
        "has_credentials": has_creds,
        "has_oauth_token": has_oauth,
        "last_sync": metrics.get("created_at") if metrics else None,
        "sync_time": cfg.get("garmin", {}).get("sync_time", "05:45"),
        "circuit_open": circuit_open,
    })


@router.post("/console/setup/garmin/reauth")
async def reauth_garmin(request: Request, _=Depends(verify_admin)):
    """Clear tokens and credentials to force re-authentication."""
    from app.core.secrets import delete_garmin_credentials
    from app.agents.coach.garmin_client import GarminClient
    from fastapi.responses import JSONResponse

    delete_garmin_credentials()
    client = GarminClient()
    client.clear_tokens()

    return JSONResponse({"success": True, "message": "Đã xóa token. Nhập lại thông tin để kết nối."})


# ==========================================
# 📊 LOG LEVELS — POST
# ==========================================
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.post("/console/save-log-levels")
async def console_save_log_levels(
    request: Request,
    username: str = Depends(verify_admin),
):
    """Save per-domain log level configuration from the console Logging tab."""
    form = await request.form()
    config = load_config()

    new_levels: dict[str, str] = {}
    for domain in KNOWN_DOMAINS:
        level = str(form.get(f"log_{domain}", "INFO")).upper()
        if level not in _VALID_LOG_LEVELS:
            level = "INFO"
        new_levels[domain] = level

    config["log_levels"] = new_levels
    save_config(config)
    apply_log_levels(new_levels)
    logger.info(f"[CONSOLE] User '{username}' updated log levels: {new_levels}")
    return RedirectResponse(url="/console?tab=logging", status_code=303)


# ==========================================
# ⚙️ SYSTEM ACTIONS
# ==========================================
@router.post("/console/toggle")
async def console_toggle(username: str = Depends(verify_admin)):
    state.service_active = not state.service_active
    status_str = "RESUMED" if state.service_active else "PAUSED"
    logger.info(f"[CONSOLE] User '{username}' triggered Service {status_str}")
    return RedirectResponse(url="/console?tab=system", status_code=303)


@router.get("/console/test-email")
async def console_test_email(username: str = Depends(verify_admin)):
    try:
        cfg = load_config()
        send_html_email(
            "Test Email from TinhN AI OS",
            "<h1>✅ It Works!</h1><p>Hệ thống email của bạn đang hoạt động tốt.</p>",
            cfg,
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[CONSOLE] Test email failed: {e}")
        return {"status": "error", "message": str(e)}


# ==========================================
# 🔀 BACKWARD-COMPAT REDIRECTS
# Keep old /admin and /dashboard URLs working
# ==========================================
@router.get("/admin", include_in_schema=False)
async def legacy_admin_redirect(username: str = Depends(verify_admin)):
    return RedirectResponse(url="/console?tab=settings", status_code=301)


@router.get("/dashboard", include_in_schema=False)
async def legacy_dashboard_redirect():
    return RedirectResponse(url="/console?tab=overview", status_code=301)

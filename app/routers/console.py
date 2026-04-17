# app/routers/console.py
# TinhN AI OS — Unified Control Console
# Merges: admin (settings, system control) + dashboard (metrics, charts, training log) + memory view

import json
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import load_config, save_config
from app.core.notification import send_html_email
from app.core.logging_conf import (
    log_capture_string,
    apply_log_levels,
    get_effective_log_levels,
    KNOWN_DOMAINS,
)
from app.core.state import state
from app.core.user_context import get_primary_user_id
from app.core.database import (
    get_db_connection,
    get_training_loads,
    get_historical_training_loads,
    get_all_active_memories,
)
from app.agents.coach.utils import calculate_acwr
from app.services.scheduler import reload_scheduler

router = APIRouter()
templates = Jinja2Templates(directory="templates")
from app.core.logging_conf import get_module_logger
logger = get_module_logger("admin")

# ==========================================
# 🔐 AUTHENTICATION
# ==========================================
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    env_user = os.getenv("ADMIN_USERNAME", "admin")
    env_pass = os.getenv("ADMIN_PASSWORD", "123456")
    is_user_ok = secrets.compare_digest(credentials.username, env_user)
    is_pass_ok = secrets.compare_digest(credentials.password, env_pass)
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai tài khoản hoặc mật khẩu!",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ==========================================
# 🖥️ UNIFIED CONSOLE — GET
# ==========================================
@router.get("/console", response_class=HTMLResponse)
async def console_page(request: Request, tab: str = "overview", username: str = Depends(verify_credentials)):
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
            "activities_desc": activities,   # newest first for table
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
    username: str = Depends(verify_credentials),
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
async def console_save_redirect(username: str = Depends(verify_credentials)):
    return RedirectResponse(url="/console?tab=settings", status_code=303)


# ==========================================
# 📰 NEWS SETTINGS — POST
# ==========================================
@router.post("/console/save-news")
async def console_save_news(
    request: Request,
    username: str = Depends(verify_credentials),
):
    """Save news agent configuration from the console News tab."""
    form = await request.form()

    config = load_config()
    news_cfg = config.get("news_agent", {})

    # --- Basic toggles & scalars ---
    news_cfg["enabled"] = form.get("news_enabled") == "on"
    news_cfg["news_model"] = form.get("news_model", "models/gemini-flash-latest").strip() or "models/gemini-flash-latest"
    news_cfg["morning_time"] = form.get("morning_time", "06:30").strip()
    news_cfg["afternoon_time"] = form.get("afternoon_time", "17:30").strip()
    news_cfg["evening_time"] = form.get("evening_time", "20:00").strip()
    news_cfg["telegram_chat_id"] = form.get("news_telegram_chat_id", "").strip()

    # Remove deprecated RSS-era keys if present
    for old_key in ("watch_interval_minutes", "alert_threshold", "digest_threshold",
                    "topic_cooldown_hours", "max_articles_per_feed", "feeds",
                    "shock_threshold"):
        news_cfg.pop(old_key, None)

    # --- Interest profile (serialized as JSON by client-side JS) ---
    profile_json = form.get("interest_profile_json", "{}")
    try:
        profile = json.loads(profile_json)
        if isinstance(profile, dict):
            news_cfg["interest_profile"] = profile
    except (json.JSONDecodeError, ValueError):
        logger.warning("[CONSOLE] Invalid interest_profile_json — keeping existing profile.")

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
# 📊 LOG LEVELS — POST
# ==========================================
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.post("/console/save-log-levels")
async def console_save_log_levels(
    request: Request,
    username: str = Depends(verify_credentials),
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
async def console_toggle(username: str = Depends(verify_credentials)):
    state.service_active = not state.service_active
    status_str = "RESUMED" if state.service_active else "PAUSED"
    logger.info(f"[CONSOLE] User '{username}' triggered Service {status_str}")
    return RedirectResponse(url="/console?tab=system", status_code=303)


@router.get("/console/test-email")
async def console_test_email(username: str = Depends(verify_credentials)):
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
async def legacy_admin_redirect(username: str = Depends(verify_credentials)):
    return RedirectResponse(url="/console?tab=settings", status_code=301)


@router.get("/dashboard", include_in_schema=False)
async def legacy_dashboard_redirect():
    return RedirectResponse(url="/console?tab=overview", status_code=301)

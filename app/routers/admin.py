from typing import Optional

from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import load_config, save_config
from app.core.notification import send_html_email
from app.core.logging_conf import log_capture_string
from app.core.state import state
from app.core.admin_auth import verify_admin
from app.services.scheduler import reload_scheduler
from app.core.logging_conf import get_module_logger

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = get_module_logger("admin")

# ==========================================
# 🌐 ADMIN ROUTES
# ==========================================

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, username: str = Depends(verify_admin)):
    """Render the Admin Dashboard interface."""
    logs_text = "\n".join(list(log_capture_string))
    
    return templates.TemplateResponse(request, "admin.html", {
        "config": load_config(),
        "logs": logs_text,
        "service_active": state.service_active
    })

@router.post("/admin/save")
async def save_settings(
    request: Request,
    system_instruction: str = Form(...),
    user_profile: str = Form(...),
    task_description: str = Form(""),       # <-- Form("") allows empty values
    analysis_requirements: str = Form(""),  # <-- Form("") allows empty values
    report_structure: str = Form(""),       
    output_format: str = Form(""),          
    max_hr: int = Form(185),
    rest_hr: int = Form(55),
    lthr_bpm: int = Form(0),
    rftp_watts: int = Form(0),
    race_date: Optional[str] = Form(None),
    current_goal: str = Form(""),
    briefing_time: str = Form("06:00"),
    backup_time: str = Form("02:00"),
    harvest_hours: str = Form("0,6,12,18"),
    harvest_minute: str = Form("15"),
    email_enabled: Optional[str] = Form(None),
    debug_mode: Optional[str] = Form(None),
    model_name: str = Form("models/gemini-flash-latest"),
    username: str = Depends(verify_admin)
):
    """Process configuration saving form from Admin UI."""
    config = load_config()
    
    # 1. Update AI Persona information
    config["system_instruction"] = system_instruction
    config["user_profile"] = user_profile
    config["task_description"] = task_description
    config["analysis_requirements"] = analysis_requirements
    config["report_structure"] = report_structure   
    config["output_format"] = output_format
    
    # 2. Update Sports Science parameters & Goals
    config["max_hr"] = max_hr
    config["rest_hr"] = rest_hr
    config["lthr_bpm"] = lthr_bpm
    config["rftp_watts"] = rftp_watts
    config["race_date"] = race_date
    config["current_goal"] = current_goal
    
    # 3. Update Scheduler settings
    config["scheduler"] = {
        "briefing_time": briefing_time,
        "backup_time": backup_time,
        "harvest_hours": harvest_hours,
        "harvest_minute": harvest_minute
    }
    
    # 4. Update Email configuration
    if "email_config" not in config:
        config["email_config"] = {}
    config["email_config"]["enabled"] = True if email_enabled == "on" else False
    config["email_config"]["smtp_server"] = config.get("email_config", {}).get("smtp_server", "smtp.gmail.com")
    config["email_config"]["smtp_port"] = config.get("email_config", {}).get("smtp_port", 587)
    
    # 5. Update System settings
    config["debug_mode"] = True if debug_mode == "on" else False
    config["model_name"] = model_name
    
    save_config(config)
    reload_scheduler()
    
    logger.info(f"[ADMIN] Auth User '{username}' saved configuration.")
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/admin/save", include_in_schema=False)
async def catch_accidental_get_save(username: str = Depends(verify_admin)):
    """
    Error 405 Trap: If a user accidentally refreshes (F5) or types /admin/save directly (GET),
    gracefully redirect them back to the Admin home instead of throwing an error.
    """
    logger.info(f"[ADMIN] Caught accidental GET request to /admin/save from user '{username}'. Redirecting to home...")
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/admin/test-email")
async def test_email_route(username: str = Depends(verify_admin)):
    """Send a test email to verify SMTP connection."""
    try:
        cfg = load_config()
        send_html_email(
            "Test Email from AI Coach", 
            "<h1>It Works!</h1><p>Hệ thống gửi email của bạn đang hoạt động tốt.</p>", # [ZONE 3] UI Text
            cfg
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[ADMIN] Test email failed: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/admin/toggle")
async def toggle_service(username: str = Depends(verify_admin)):
    """Enable/Disable AI service (Pause/Resume)."""
    state.service_active = not state.service_active
    status = "RESUMED" if state.service_active else "PAUSED"
    logger.info(f"[ADMIN] User '{username}' triggered Service {status}")
    return RedirectResponse(url="/admin", status_code=303)
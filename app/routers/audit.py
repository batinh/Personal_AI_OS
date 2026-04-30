"""
Audit Router — log audit management endpoints.

GET  /audit          → HTML dashboard (requires Basic Auth)
GET  /audit/api/entries  → JSON list with filters
POST /audit/api/entries/{id}/acknowledge → mark as acknowledged
POST /audit/api/entries/{id}/resolve     → mark as resolved
POST /audit/api/run  → trigger manual audit scan
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.database import get_audit_entries, update_audit_status, get_audit_stats
from app.core.user_context import get_primary_user_id
from app.core.admin_auth import verify_admin
from app.services.log_auditor import run_audit

from app.core.logging_conf import get_module_logger

logger = get_module_logger("audit")
router = APIRouter(prefix="/audit", tags=["Audit"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def audit_page(request: Request, _=Depends(verify_admin)):
    """Render audit dashboard HTML page."""
    return templates.TemplateResponse(request, "audit.html", {})


@router.get("/api/entries")
async def list_entries(
    status: Optional[str] = Query(None, description="open|acknowledged|resolved"),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, description="error|warning|info"),
    limit: int = Query(200, ge=1, le=1000),
    _=Depends(verify_admin),
):
    """Return audit entries as JSON with optional filters."""
    user_id = str(get_primary_user_id())
    entries = get_audit_entries(
        user_id=user_id,
        status=status,
        category=category,
        severity=severity,
        limit=limit,
    )
    stats = get_audit_stats(user_id)
    return {"entries": entries, "stats": stats}


@router.post("/api/entries/{entry_id}/acknowledge")
async def acknowledge_entry(entry_id: int, _=Depends(verify_admin)):
    """Mark an audit entry as acknowledged."""
    ok = update_audit_status(entry_id, "acknowledged")
    return {"success": ok, "id": entry_id, "status": "acknowledged"}


@router.post("/api/entries/{entry_id}/resolve")
async def resolve_entry(entry_id: int, _=Depends(verify_admin)):
    """Mark an audit entry as resolved."""
    ok = update_audit_status(entry_id, "resolved")
    return {"success": ok, "id": entry_id, "status": "resolved"}


@router.post("/api/run")
async def run_audit_now(_=Depends(verify_admin)):
    """Trigger an immediate audit scan and return count of new entries."""
    user_id = str(get_primary_user_id())
    count = run_audit(user_id)
    logger.info(f"[AUDIT] Manual scan triggered: {count} new entries.")
    return {"success": True, "new_entries": count}

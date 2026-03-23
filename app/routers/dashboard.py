from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import logging
import os
from app.core.user_context import get_primary_user_id

from app.core.database import get_db_connection, get_training_loads, get_historical_training_loads
from app.agents.coach.utils import calculate_acwr
from app.core.config import load_config

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("AI_COACH")

@router.get("/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    # 1. Fetch configuration and Athlete info
    config = load_config()
    # Get Chat ID from environment (representing the Primary Tenant)
    chat_id = get_primary_user_id()
    
    # 2. Fetch training loads and calculate ACWR
    loads = get_training_loads(chat_id)
    acwr_results = calculate_acwr(loads['acute_load_7d'], loads['chronic_load_28d'])

    # [NEW] Fetch 30-day time series data for Garmin-style chart
    load_history = get_historical_training_loads(chat_id, days=30)
    
    # 3. Fetch the last 20 run activities for charting
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT start_date, name, distance_km, trimp_score, gcs_score, avg_hr 
        FROM run_activities 
        WHERE user_id = ?
        ORDER BY start_date DESC LIMIT 20
    ''', (str(chat_id),))
    activities = [dict(row) for row in c.fetchall()]
    conn.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "acwr": acwr_results,
        "loads": loads,
        "load_history": load_history, # [NEW] Inject into Jinja2
        "activities": activities[::-1], # Reverse to draw chart from left to right
        "config": config
    })
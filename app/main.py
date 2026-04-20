import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# --- IMPORTS (Modular Structure) ---
from app.core.database import init_db, DB_PATH
from app.core.config import load_config, CONFIG_PATH
from app.routers import webhooks, admin, dashboard, console, audit
from app.services.scheduler import start_scheduler, scheduler
from app.core.logging_conf import setup_logging, apply_log_levels

# 1. Setup Logging
logger = setup_logging()

# Route uvicorn.error into the AI_COACH file handler so startup/error logs
# are captured in logs/app.log and survive container restarts.
# uvicorn.access is suppressed (too verbose for persistent log files).
logging.getLogger("uvicorn.error").handlers = []
logging.getLogger("uvicorn.error").propagate = True
logging.getLogger("uvicorn.access").handlers = []
logging.getLogger("uvicorn.access").propagate = False

_REQUIRED_ENV_VARS = [
    "GEMINI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
_IMPORTANT_ENV_VARS = [
    "STRAVA_CLIENT_ID",
    "STRAVA_CLIENT_SECRET",
    "STRAVA_REFRESH_TOKEN",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "VERIFY_TOKEN",
]


def _validate_env() -> None:
    missing_required = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
    missing_important = [v for v in _IMPORTANT_ENV_VARS if not os.getenv(v)]
    if missing_required:
        logger.error("[STARTUP] Missing REQUIRED env vars — core features will fail: %s", missing_required)
    if missing_important:
        logger.warning("[STARTUP] Missing IMPORTANT env vars — some features degraded: %s", missing_important)


# 2. Lifespan context manager (replaces deprecated @app.on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle handler."""
    # --- STARTUP ---
    logger.info("🚀 Personal AI OS is starting up...")

    _validate_env()

    # Initialize database schema
    init_db()

    # Log resolved paths so Docker volume mount issues are visible immediately
    logger.info(f"[STARTUP] DB path     : {DB_PATH} (exists: {os.path.exists(DB_PATH)})")
    logger.info(f"[STARTUP] Config path : {CONFIG_PATH} (exists: {os.path.exists(CONFIG_PATH)})")

    # Trigger config load (auto-initializes from example if missing)
    cfg = load_config()
    if cfg:
        logger.info(f"[STARTUP] Config loaded. Model: {cfg.get('model_name', 'default')}")
        apply_log_levels(cfg.get("log_levels", {}))
    else:
        logger.warning("[STARTUP] Config is EMPTY — system will run with defaults. Set up via Admin UI.")

    start_scheduler()
    logger.info("✅ System Ready. Scheduler Active.")

    yield  # Application is running

    # --- SHUTDOWN ---
    logger.info("🛑 Personal AI OS is shutting down...")
    if scheduler.running:
        scheduler.shutdown()
    logger.info("✅ Scheduler Stopped. Goodbye!")


# 3. Initialize FastAPI App
app = FastAPI(
    title="Personal AI OS",
    description="Modular Monolith AI Agent System (Coach Dyno)",
    version="2.0.0",
    lifespan=lifespan,
)

# 4. Register Routers
app.include_router(webhooks.router)
app.include_router(console.router)   # Unified console (replaces admin + dashboard)
app.include_router(admin.router)     # Legacy — redirects to /console
app.include_router(dashboard.router) # Legacy — redirects to /console
app.include_router(audit.router)     # Log audit dashboard


# 5. Health check endpoint for Docker and uptime monitoring
@app.get("/health", tags=["System"])
async def health_check():
    """Returns system health status. Used by Docker HEALTHCHECK and uptime monitors."""
    db_ok = os.path.exists(DB_PATH)
    config_ok = os.path.exists(CONFIG_PATH)
    scheduler_ok = scheduler.running
    healthy = db_ok and config_ok and scheduler_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "db": "ok" if db_ok else "missing",
            "config": "ok" if config_ok else "missing",
            "scheduler": "running" if scheduler_ok else "stopped",
        },
    )
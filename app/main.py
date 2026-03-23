import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# --- IMPORTS (Modular Structure) ---
from app.core.database import init_db, DB_PATH
from app.core.config import load_config, CONFIG_PATH
from app.routers import webhooks, admin, dashboard
from app.services.scheduler import start_scheduler, scheduler
from app.core.logging_conf import setup_logging

# 1. Setup Logging
logger = setup_logging()

# 2. Lifespan context manager (replaces deprecated @app.on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle handler."""
    # --- STARTUP ---
    logger.info("🚀 Personal AI OS is starting up...")

    # Initialize database schema
    init_db()

    # Log resolved paths so Docker volume mount issues are visible immediately
    logger.info(f"[STARTUP] DB path     : {DB_PATH} (exists: {os.path.exists(DB_PATH)})")
    logger.info(f"[STARTUP] Config path : {CONFIG_PATH} (exists: {os.path.exists(CONFIG_PATH)})")

    # Trigger config load (auto-initializes from example if missing)
    cfg = load_config()
    if cfg:
        logger.info(f"[STARTUP] Config loaded. Model: {cfg.get('model_name', 'default')}")
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
app.include_router(admin.router)
app.include_router(dashboard.router)


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
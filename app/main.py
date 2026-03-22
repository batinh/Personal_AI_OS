import logging
from fastapi import FastAPI

# --- IMPORTS (Modular Structure) ---
# Folders/Files use snake_case: app.core.database
from app.core.database import init_db, DB_PATH
from app.core.config import load_config, CONFIG_PATH
from app.routers import webhooks, admin, dashboard
from app.services.scheduler import start_scheduler, scheduler
from app.core.logging_conf import setup_logging

# 1. Setup Logging
# Function name uses snake_case
logger = setup_logging()

# 2. Initialize Database
init_db()

# 3. Initialize FastAPI App
# Variable 'app' uses snake_case
app = FastAPI(
    title="Personal AI OS",
    description="Modular Monolith AI Agent System (Coach Dyno)",
    version="2.0.0"
)

# 4. Register Routers
# 'webhooks', 'admin', and 'dashboard' are module names (snake_case)
# 'router' is the APIRouter instance inside them
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(dashboard.router) # Register dashboard router

# 5. Lifecycle Events
@app.on_event("startup")
async def startup_event():
    """Executed once when the application container starts."""
    logger.info("🚀 Personal AI OS is starting up...")

    # Log resolved paths so Docker volume mount issues are visible immediately
    import os
    logger.info(f"[STARTUP] DB path     : {DB_PATH} (exists: {os.path.exists(DB_PATH)})")
    logger.info(f"[STARTUP] Config path : {CONFIG_PATH} (exists: {os.path.exists(CONFIG_PATH)})")

    # Trigger config load (auto-initializes from example if missing)
    cfg = load_config()
    if cfg:
        logger.info(f"[STARTUP] Config loaded. Model: {cfg.get('model_name', 'default')}")
    else:
        logger.warning("[STARTUP] Config is EMPTY — system will run with defaults. Set up via Admin UI.")

    # Start background tasks
    start_scheduler()
    
    logger.info("✅ System Ready. Scheduler Active.")

@app.on_event("shutdown")
async def shutdown_event():
    """Executed when the application container stops."""
    logger.info("🛑 Personal AI OS is shutting down...")
    
    # Gracefully stop the scheduler
    if scheduler.running:
        scheduler.shutdown()
        
    logger.info("✅ Scheduler Stopped. Goodbye!")
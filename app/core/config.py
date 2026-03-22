import json
import os
import time
import shutil
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from the root directory
load_dotenv()

logger = logging.getLogger("AI_COACH")

# --- Absolute paths anchored to this file's location ---
# config.py is at: <project_root>/app/core/config.py
# So parent.parent.parent = <project_root>
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = str(_BASE_DIR / "data" / "config.json")
_EXAMPLE_CONFIG_PATH = str(_BASE_DIR / "config.example.json")

# In-memory cache to avoid reading JSON from disk on every request
_config_cache: dict = {}
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: int = 60  # seconds

def _ensure_config_exists():
    """
    If data/config.json is missing, auto-copy from config.example.json.
    Logs a clear WARNING so the issue is visible in Docker logs.
    """
    config_file = Path(CONFIG_PATH)
    if config_file.exists():
        return

    logger.warning(f"[CONFIG] data/config.json not found at {CONFIG_PATH}")
    example_file = Path(_EXAMPLE_CONFIG_PATH)
    if example_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example_file, config_file)
        logger.warning(f"[CONFIG] Auto-initialized data/config.json from config.example.json. Please review and update via Admin UI.")
    else:
        logger.error(f"[CONFIG] config.example.json also not found at {_EXAMPLE_CONFIG_PATH}. System will run with empty config!")

def load_config() -> dict:
    """
    Load configuration from the central JSON file.
    Uses a 60-second in-memory cache to prevent repeated disk reads on every request.
    Cache is invalidated immediately after save_config() is called.
    Auto-initializes config from example file if missing.
    """
    global _config_cache, _config_cache_time
    now = time.monotonic()
    if _config_cache and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache

    _ensure_config_exists()

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _config_cache = json.load(f)
                _config_cache_time = now
                logger.debug(f"[CONFIG] Loaded config from {CONFIG_PATH}")
                return _config_cache
        except Exception as e:
            logger.error(f"[CONFIG] Failed to parse {CONFIG_PATH}: {e}")
            return {}
    return {}

def save_config(data: dict):
    """
    Save configuration object to the central JSON file.
    Immediately invalidates the in-memory cache so next load_config() reads fresh data.
    """
    global _config_cache, _config_cache_time
    Path(CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    # Invalidate cache so Admin UI changes take effect immediately
    _config_cache = {}
    _config_cache_time = 0.0
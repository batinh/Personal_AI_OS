import json
import logging
import os
import shutil
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
import time
from dotenv import load_dotenv

_logger = logging.getLogger("config")

# Load environment variables from the root directory
load_dotenv()

_ROOT = Path(__file__).resolve().parents[2]
_lock = RLock()
CONFIG_PATH = str(_ROOT / "data/config.json")
_EXAMPLE_CONFIG_PATH = str(_ROOT / "config.example.json")

# In-memory cache to avoid reading JSON from disk on every request
_config_cache: dict = {}
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: int = 60  # seconds


def load_config() -> dict:
    with _lock:
        return deepcopy(_load_config())


def _load_config() -> dict:
    """
    Load configuration from the central JSON file.
    Uses a 60-second in-memory cache to prevent repeated disk reads on every request.
    Cache is invalidated immediately after save_config() is called.
    Auto-initializes data/config.json from config.example.json on first boot.
    """
    global _config_cache, _config_cache_time
    now = time.monotonic()
    if _config_cache and (now - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache

    if not os.path.exists(CONFIG_PATH) and os.path.exists(_EXAMPLE_CONFIG_PATH):
        shutil.copy(_EXAMPLE_CONFIG_PATH, CONFIG_PATH)

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _config_cache = json.load(f)
                _config_cache_time = now
                return _config_cache
        except Exception as e:
            _logger.error(
                "[CONFIG] Failed to parse %s: %s — running with empty config",
                CONFIG_PATH,
                e,
            )
            return {}
    return {}


def save_config(data: dict):
    """
    Save configuration object to the central JSON file.
    Immediately invalidates the in-memory cache so next load_config() reads fresh data.
    """
    global _config_cache, _config_cache_time
    with _lock:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=Path(CONFIG_PATH).parent, delete=False
        ) as temp:
            try:
                json.dump(data, temp, indent=4, ensure_ascii=False)
                temp.flush()
                os.fsync(temp.fileno())
                os.replace(temp.name, CONFIG_PATH)
            finally:
                Path(temp.name).unlink(missing_ok=True)
        _config_cache = {}
        _config_cache_time = 0.0

import logging
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Buffer storing the last 50 log lines to display on the Web Admin UI
log_capture_string = deque(maxlen=50)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Absolute path to log file — anchored to project root (logging_conf.py → app/core → project root)
# Writes to ./logs/ which is bind-mounted out of the container: ./logs/:/app/logs
# Rotates daily at midnight; archive format: app.log.2026-04-18, keeps 30 days.
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_FILE_PATH = _BASE_DIR / "logs" / "app.log"

# All configurable log domains — each maps to logger "AI_COACH.<domain>"
KNOWN_DOMAINS: list[str] = [
    "news",
    "coach",
    "strava",
    "scheduler",
    "webhook",
    "database",
    "memory",
    "notification",
    "weather",
    "backup",
    "audit",
    "admin",
]

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ListHandler(logging.Handler):
    """Custom Handler to push log records into a deque buffer."""
    def emit(self, record):
        try:
            msg = self.format(record)
            log_capture_string.append(msg)
        except Exception:
            self.handleError(record)


def setup_logging() -> logging.Logger:
    """Initialize global application logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
    )

    logger = logging.getLogger("AI_COACH")

    # Attach ListHandler to capture live logs for the Admin UI
    list_handler = ListHandler()
    list_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(list_handler)

    # Attach TimedRotatingFileHandler — daily rotation, 30-day retention.
    # Rotate at midnight → archive names: app.log.2026-04-18, app.log.2026-04-17, …
    # Survives container restarts because ./logs/ is bind-mounted to the host.
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            str(LOG_FILE_PATH),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
            delay=False,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"[LOGGING] Could not attach file handler: {e}")

    return logger


def get_module_logger(domain: str) -> logging.Logger:
    """Return a domain-scoped child logger under the AI_COACH hierarchy.

    Usage in module files:
        from app.core.logging_conf import get_module_logger
        logger = get_module_logger("news")

    The returned logger propagates to AI_COACH (and its handlers) unless its
    level is explicitly overridden via apply_log_levels().
    """
    return logging.getLogger(f"AI_COACH.{domain}")


def apply_log_levels(log_levels: dict) -> None:
    """Apply per-domain log level overrides from config.

    Args:
        log_levels: dict mapping domain name to level string,
                    e.g. {"news": "DEBUG", "coach": "WARNING"}
    """
    root_logger = logging.getLogger("AI_COACH")
    for domain, level_str in log_levels.items():
        level_str = str(level_str).upper()
        if level_str not in _VALID_LEVELS:
            root_logger.warning(
                "[LOGGING] Unknown log level '%s' for domain '%s' — skipping.", level_str, domain
            )
            continue
        child = logging.getLogger(f"AI_COACH.{domain}")
        child.setLevel(getattr(logging, level_str))
        root_logger.debug("[LOGGING] Set AI_COACH.%s → %s", domain, level_str)


def get_effective_log_levels() -> dict[str, str]:
    """Return the current effective log level string for each known domain.

    Returns:
        dict mapping domain → level string (e.g. {"news": "DEBUG", "coach": "INFO"})
        A domain at NOTSET (not explicitly set) reports its effective inherited level.
    """
    result: dict[str, str] = {}
    for domain in KNOWN_DOMAINS:
        child = logging.getLogger(f"AI_COACH.{domain}")
        effective = child.getEffectiveLevel()
        result[domain] = logging.getLevelName(effective)
    return result
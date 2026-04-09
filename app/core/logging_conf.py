import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Buffer storing the last 50 log lines to display on the Web Admin UI
log_capture_string = deque(maxlen=50)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Absolute path to log file — anchored to project root (logging_conf.py → app/core → project root)
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_FILE_PATH = _BASE_DIR / "data" / "app.log"


class ListHandler(logging.Handler):
    """Custom Handler to push log records into a deque buffer."""
    def emit(self, record):
        try:
            msg = self.format(record)
            log_capture_string.append(msg)
        except Exception:
            self.handleError(record)

def setup_logging():
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

    # Attach RotatingFileHandler to persist logs for audit scanning
    try:
        LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE_PATH),
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"[LOGGING] Could not attach file handler: {e}")

    return logger
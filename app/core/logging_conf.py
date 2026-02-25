import logging
from collections import deque

# Buffer storing the last 50 log lines to display on the Web Admin UI
log_capture_string = deque(maxlen=50)

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
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    logger = logging.getLogger("AI_COACH")
    
    # Attach ListHandler to capture live logs for the Admin UI
    list_handler = ListHandler()
    list_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(list_handler)
    
    return logger
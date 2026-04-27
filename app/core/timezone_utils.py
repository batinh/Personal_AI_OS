import os
import pytz


def get_local_tz() -> pytz.BaseTzInfo:
    """Return the configured local timezone (defaults to Asia/Ho_Chi_Minh)."""
    return pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))

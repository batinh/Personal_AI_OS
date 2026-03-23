import os
from typing import Optional


def get_primary_user_id() -> Optional[str]:
    """
    Resolve the primary user identity from environment settings.
    Returns None when TELEGRAM_CHAT_ID is not configured.
    """
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    return str(chat_id) if chat_id else None

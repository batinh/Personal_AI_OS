"""
Telegram message router — routes free-text messages to the correct agent.

Rules:
- "@news <query>" or "@tin <query>" → news agent
- Everything else → coach (default)

Returns the agent name and the cleaned text (prefix stripped).
"""

_NEWS_PREFIXES = ("@news", "@tin")


def route_message(text: str) -> tuple[str, str]:
    """
    Determine which agent should handle an incoming Telegram message.

    Args:
        text: raw message text from Telegram

    Returns:
        (agent_name, cleaned_text) where agent_name is "news" or "coach"
        and cleaned_text has the routing prefix stripped.
    """
    stripped = text.strip()
    lower = stripped.lower()

    for prefix in _NEWS_PREFIXES:
        if lower.startswith(prefix):
            cleaned = stripped[len(prefix) :].strip()
            return "news", cleaned

    return "coach", stripped

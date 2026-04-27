"""
News Agent — Telegram command and chat handler.

Handles /news [morning|afternoon|evening|help] commands from Telegram.
Also handles free-text messages routed here via @news / @tin prefix.

Zone 3: function names/logic = English, user-facing messages = Vietnamese.
"""

import time
from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.agents.news.memory import run_extract_in_background
from app.core.logging_conf import get_module_logger

logger = get_module_logger("news")

# ==========================================
# ERROR MESSAGE CATALOG (Section 4 of PRD)
# Tests MUST import these constants — do NOT assert on literal strings.
# ==========================================

ERR_001 = "⚠️ Không lấy được tin tức thực tế lúc này. Thử lại sau."
ERR_002 = "⚠️ Không tìm thấy kết quả cho yêu cầu này. Thử lại sau."
ERR_003 = "⚠️ Bạn đã gửi quá nhiều yêu cầu. Thử lại sau 1 tiếng."
ERR_004 = "⚠️ News Agent đang tắt. Bật lại trong phần cài đặt."
ERR_005 = "❌ Lệnh không hợp lệ: /news {arg}."  # format with .format(arg=...)
ERR_006 = "❌ Lỗi khi lấy tin {session_label}. Xem log để biết thêm."  # format with .format(...)
ERR_007 = ERR_002  # on-demand < 100 chars — same message as ERR_002

# ==========================================
# HELP MESSAGE
# ==========================================

_HELP_MSG = (
    "📰 <b>News Agent</b>\n\n"
    "<b>Lịch tự động:</b>\n"
    "• 🌅 06:30 — Điểm tin buổi sáng\n"
    "• 🌆 17:30 — Điểm tin buổi chiều\n"
    "• 🌙 20:00 — Điểm tin buổi tối\n\n"
    "<b>Lệnh thủ công:</b>\n"
    "• <code>/news</code> — Điểm tin buổi sáng ngay bây giờ\n"
    "• <code>/news morning</code> — Điểm tin buổi sáng\n"
    "• <code>/news afternoon</code> — Điểm tin buổi chiều\n"
    "• <code>/news evening</code> — Điểm tin buổi tối\n\n"
    "<b>Chat với News Agent:</b>\n"
    "Nhắn <code>@news câu hỏi</code> hoặc <code>@tin câu hỏi</code>\n"
    "Ví dụ: <code>@news tóm tắt tình hình kinh tế hôm nay</code>\n\n"
    "<i>Agent học sở thích của bạn qua các cuộc trò chuyện.</i>"
)

_FLOW_LABELS: dict[str, str] = {
    "morning": "buổi sáng",
    "afternoon": "buổi chiều",
    "evening": "buổi tối",
}

_VALID_FLOWS = frozenset({"morning", "afternoon", "evening", "help"})

# ==========================================
# RATE LIMITING (FR-2.7, NFR-14)
# In-memory counter, resets on server restart — acceptable for v1.0.
# ==========================================

RATE_LIMIT = 10
RATE_WINDOW = 3600  # seconds in 1 hour

_rate_limit_store: dict[str, list[float]] = {}


def _check_rate_limit(chat_id: str, limit: int = RATE_LIMIT) -> bool:
    """
    Returns True if the request is allowed, False if rate limited.

    Slides a 1-hour window over recorded timestamps for the given chat_id.
    Counter resets automatically as old timestamps age out.
    """
    now = time.time()
    timestamps = _rate_limit_store.get(chat_id, [])
    recent = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(recent) >= limit:
        return False
    recent.append(now)
    _rate_limit_store[chat_id] = recent
    return True


# ==========================================
# COMMAND HANDLER
# ==========================================


def handle_news_command(chat_id: str, args: list[str], config: dict) -> None:
    """
    Parse /news [morning|afternoon|evening|help] and dispatch to the correct flow.

    Args:
        chat_id: Telegram chat ID to send feedback to.
        args: List of command arguments (everything after /news).
        config: Loaded config dict from load_config().
    """
    from app.agents.news.agent import generate_news_briefing

    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, ERR_004)
        logger.info("[NEWS-CMD] News agent disabled. Sent disabled message.")
        return

    sub = args[0].lower().strip() if args else "morning"

    if sub == "help":
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    if sub not in _VALID_FLOWS:
        send_telegram_msg(chat_id, ERR_005.format(arg=sub) + f"\n\n{_HELP_MSG}")
        return

    label = _FLOW_LABELS[sub]
    logger.info(f"[NEWS-CMD] Triggered '{sub}' flow for chat_id={chat_id}")

    try:
        generate_news_briefing(config, session=sub)
    except Exception as e:
        logger.error(f"[NEWS-CMD] Error in '{sub}' flow: {e}")
        send_telegram_msg(chat_id, ERR_006.format(session_label=label))


# ==========================================
# CHAT HANDLER
# ==========================================


def handle_news_chat(chat_id: str, text: str, config: dict) -> None:
    """
    Handle a free-text message routed to the news agent (via @news / @tin prefix).

    Delegates to generate_on_demand_briefing for search + synthesis, then extracts
    preference signals from the exchange and saves to memory in background.

    Args:
        chat_id: Telegram chat ID to reply to.
        text: user message with the routing prefix already stripped.
        config: loaded config dict from load_config().
    """
    from app.agents.news.agent import generate_on_demand_briefing, _get_model

    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, ERR_004)
        return

    if not text:
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    rate_limit = int(news_cfg.get("ondemand_rate_limit_per_hour", RATE_LIMIT))
    if not _check_rate_limit(chat_id, limit=rate_limit):
        logger.warning(f"[NEWS-CHAT] Rate limit exceeded for chat_id={chat_id}")
        send_telegram_msg(chat_id, ERR_003)
        return

    user_id = str(get_primary_user_id())
    model = _get_model(config)
    logger.info(f"[NEWS-CHAT] Handling message for chat_id={chat_id}: '{text[:60]}'")

    reply = generate_on_demand_briefing(text, chat_id, config)

    if reply:
        chat_text = f"Người dùng: {text}\nTrợ lý: {reply}"
        run_extract_in_background(user_id, chat_text, model)

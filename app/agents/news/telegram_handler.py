"""
News Agent — Telegram command and chat handler.

Handles /news [morning|afternoon|evening|help] commands from Telegram.
Also handles free-text messages routed here via @news / @tin prefix.

Zone 3: function names/logic = English, user-facing messages = Vietnamese.
"""
import logging

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.agents.news.memory import run_extract_in_background

logger = logging.getLogger("AI_COACH")

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

_DISABLED_MSG = (
    "⚠️ <b>News Agent đang tắt.</b>\n"
    "Bật lại trong phần cài đặt tại /console?tab=news"
)

_FLOW_LABELS: dict[str, str] = {
    "morning": "buổi sáng",
    "afternoon": "buổi chiều",
    "evening": "buổi tối",
}

_VALID_FLOWS = frozenset({"morning", "afternoon", "evening", "help"})


def handle_news_command(chat_id: str, args: list[str], config: dict) -> None:
    """
    Parse /news [morning|afternoon|evening|help] and dispatch to the correct flow.

    Called as a background task from the Telegram webhook handler.

    Args:
        chat_id: Telegram chat ID to send feedback to.
        args: List of command arguments (everything after /news).
        config: Loaded config dict from load_config().
    """
    from app.agents.news.agent import generate_news_briefing

    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, _DISABLED_MSG)
        logger.info("[NEWS-CMD] News agent disabled. Sent disabled message.")
        return

    sub = args[0].lower().strip() if args else "morning"

    if sub == "help":
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    if sub not in _VALID_FLOWS:
        send_telegram_msg(
            chat_id,
            f"❌ Lệnh không hợp lệ: <code>/news {sub}</code>\n\n{_HELP_MSG}"
        )
        return

    label = _FLOW_LABELS[sub]
    logger.info(f"[NEWS-CMD] Triggered '{sub}' flow for chat_id={chat_id}")

    try:
        generate_news_briefing(config, session=sub)
    except Exception as e:
        logger.error(f"[NEWS-CMD] Error in '{sub}' flow: {e}")
        send_telegram_msg(
            chat_id,
            f"❌ Lỗi khi lấy tin {label}. Xem log để biết thêm chi tiết."
        )


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
        send_telegram_msg(chat_id, _DISABLED_MSG)
        return

    if not text:
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    user_id = str(get_primary_user_id())
    model = _get_model(config)
    logger.info(f"[NEWS-CHAT] Handling message for chat_id={chat_id}: '{text[:60]}'")

    reply = generate_on_demand_briefing(text, chat_id, config)

    if reply:
        # Extract preference signals in background — non-blocking
        chat_text = f"Người dùng: {text}\nTrợ lý: {reply}"
        run_extract_in_background(user_id, chat_text, model)

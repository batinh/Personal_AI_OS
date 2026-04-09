"""
News Agent — Telegram command handler.

Handles /news [morning|afternoon|watch|help] commands from Telegram.
Routes to the appropriate news agent flow.

Zone 3: function names/logic = English, user-facing messages = Vietnamese.
"""
import logging

from app.core.notification import send_telegram_msg

logger = logging.getLogger("AI_COACH")

_HELP_MSG = (
    "📰 <b>Lệnh tin tức:</b>\n\n"
    "• <code>/news</code> — Điểm tin buổi sáng ngay bây giờ\n"
    "• <code>/news morning</code> — Điểm tin buổi sáng\n"
    "• <code>/news afternoon</code> — Điểm tin buổi chiều\n"
    "• <code>/news watch</code> — Quét và gửi tin nóng ngay lập tức\n"
    "• <code>/news help</code> — Hiển thị trợ giúp này"
)

_DISABLED_MSG = (
    "⚠️ <b>News Agent đang tắt.</b>\n"
    "Bật lại trong phần cài đặt tại /console?tab=news"
)

_FLOW_LABELS: dict[str, str] = {
    "morning": "buổi sáng",
    "afternoon": "buổi chiều",
    "watch": "quét tin nóng",
}

_VALID_FLOWS = frozenset({"morning", "afternoon", "watch", "help"})


def handle_news_command(chat_id: str, args: list[str], config: dict) -> None:
    """
    Parse /news [morning|afternoon|watch|help] and dispatch to the correct flow.

    Called as a background task from the Telegram webhook handler.

    Args:
        chat_id: Telegram chat ID to send feedback to.
        args: List of command arguments (everything after /news).
        config: Loaded config dict from load_config().
    """
    # Lazy imports to avoid circular dependencies and keep startup fast
    from app.agents.news.agent import generate_news_briefing
    from app.agents.news.alert_engine import run_news_watch

    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, _DISABLED_MSG)
        logger.info("[NEWS-CMD] News agent disabled. Sent disabled message.")
        return

    # Parse sub-command — default to morning if none provided
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
    send_telegram_msg(chat_id, f"⏳ Đang lấy tin <b>{label}</b>...")
    logger.info(f"[NEWS-CMD] Triggered '{sub}' flow for chat_id={chat_id}")

    try:
        if sub == "watch":
            run_news_watch(config)
        else:
            generate_news_briefing(config, session=sub)
    except Exception as e:
        logger.error(f"[NEWS-CMD] Error in '{sub}' flow: {e}")
        send_telegram_msg(
            chat_id,
            f"❌ Lỗi khi lấy tin {label}. Xem log để biết thêm chi tiết."
        )

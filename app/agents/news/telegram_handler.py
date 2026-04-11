"""
News Agent — Telegram command and chat handler.

Handles /news [morning|afternoon|watch|help] commands from Telegram.
Also handles free-text messages routed here via @news / @tin prefix.

Zone 3: function names/logic = English, user-facing messages = Vietnamese.
"""
import logging

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.agents.news.prompts import build_news_system_instruction

logger = logging.getLogger("AI_COACH")
client = genai.Client()

_HELP_MSG = (
    "📰 <b>News Agent</b>\n\n"
    "<b>Lịch tự động:</b>\n"
    "• 🌅 06:30 — Điểm tin buổi sáng\n"
    "• 🌆 17:30 — Điểm tin buổi chiều\n"
    "• 🌙 20:00 — Điểm tin buổi tối\n"
    "• ⚡ Tin nóng sốc: cảnh báo ngay, không đợi lịch\n\n"
    "<b>Lệnh thủ công:</b>\n"
    "• <code>/news</code> — Điểm tin buổi sáng ngay bây giờ\n"
    "• <code>/news morning</code> — Điểm tin buổi sáng\n"
    "• <code>/news afternoon</code> — Điểm tin buổi chiều\n"
    "• <code>/news evening</code> — Điểm tin buổi tối\n"
    "• <code>/news watch</code> — Quét tin nóng ngay lập tức\n\n"
    "<b>Chat với News Agent:</b>\n"
    "Nhắn <code>@news câu hỏi</code> hoặc <code>@tin câu hỏi</code>\n"
    "Ví dụ: <code>@news tóm tắt tình hình kinh tế hôm nay</code>"
)

_DISABLED_MSG = (
    "⚠️ <b>News Agent đang tắt.</b>\n"
    "Bật lại trong phần cài đặt tại /console?tab=news"
)

_FLOW_LABELS: dict[str, str] = {
    "morning": "buổi sáng",
    "afternoon": "buổi chiều",
    "evening": "buổi tối",
    "watch": "quét tin nóng",
}

_VALID_FLOWS = frozenset({"morning", "afternoon", "evening", "watch", "help"})


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


def handle_news_chat(chat_id: str, text: str, config: dict) -> None:
    """
    Handle a free-text message routed to the news agent (via @news / @tin prefix).

    Responds conversationally using the news agent persona.
    Does not have live article context — answers general news-related questions.

    Args:
        chat_id: Telegram chat ID to reply to.
        text: user message with the routing prefix already stripped.
        config: loaded config dict from load_config().
    """
    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, _DISABLED_MSG)
        return

    if not text:
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    model_name = config.get("model_name", "models/gemini-flash-latest")
    logger.info(f"[NEWS-CHAT] Handling chat message for chat_id={chat_id}: '{text[:60]}'")

    try:
        system_inst = build_news_system_instruction()
        response = client.models.generate_content(
            model=model_name,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                max_output_tokens=1000,
            ),
        )
        reply = response.text or "⚠️ Không thể trả lời lúc này."
        send_telegram_msg(chat_id, reply)
        logger.info(f"[NEWS-CHAT] Sent reply for chat_id={chat_id}")
    except Exception as e:
        logger.error(f"[NEWS-CHAT] Error: {e}")
        send_telegram_msg(chat_id, "❌ Lỗi khi xử lý câu hỏi. Vui lòng thử lại.")

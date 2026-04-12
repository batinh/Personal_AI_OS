"""
News Agent — Telegram command and chat handler.

Handles /news [morning|afternoon|evening|help] commands from Telegram.
Also handles free-text messages routed here via @news / @tin prefix.

Zone 3: function names/logic = English, user-facing messages = Vietnamese.
"""
import logging

from google import genai
from google.genai import types

from app.core.notification import send_telegram_msg
from app.core.user_context import get_primary_user_id
from app.agents.news.prompts import build_news_system_instruction
from app.agents.news.memory import load_news_memory, run_extract_in_background

logger = logging.getLogger("AI_COACH")
client = genai.Client()

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

    Uses google_search grounding so Gemini can find current news while responding.
    Extracts preference signals from the exchange and saves to memory in background.

    Args:
        chat_id: Telegram chat ID to reply to.
        text: user message with the routing prefix already stripped.
        config: loaded config dict from load_config().
    """
    from app.agents.news.agent import _get_model

    news_cfg = config.get("news_agent", {})

    if not news_cfg.get("enabled", False):
        send_telegram_msg(chat_id, _DISABLED_MSG)
        return

    if not text:
        send_telegram_msg(chat_id, _HELP_MSG)
        return

    model = _get_model(config)
    user_id = str(get_primary_user_id())
    logger.info(f"[NEWS-CHAT] Handling message for chat_id={chat_id}: '{text[:60]}'")

    memory = load_news_memory(user_id)
    memory_hint = ""
    if memory.get("liked_topics") or memory.get("extra_notes"):
        liked = ", ".join(memory.get("liked_topics", [])[:5])
        notes = memory.get("extra_notes", "")[:100]
        parts = [p for p in [liked, notes] if p]
        memory_hint = f"\n(Sở thích người dùng: {'; '.join(parts)})" if parts else ""

    prompt = text + memory_hint

    try:
        system_inst = build_news_system_instruction()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_inst,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=1200,
            ),
        )
        reply = response.text or "⚠️ Không thể trả lời lúc này."
        logger.debug(f"[NEWS-CHAT] Reply length={len(reply)}: {reply[:300]!r}")
        send_telegram_msg(chat_id, reply)
        logger.info(f"[NEWS-CHAT] Sent reply for chat_id={chat_id}")

        # Extract preference signals in background — non-blocking
        chat_text = f"Người dùng: {text}\nTrợ lý: {reply}"
        run_extract_in_background(user_id, chat_text, client, model)

    except Exception as e:
        logger.error(f"[NEWS-CHAT] Error: {e}")
        send_telegram_msg(chat_id, "❌ Lỗi khi xử lý câu hỏi. Vui lòng thử lại.")

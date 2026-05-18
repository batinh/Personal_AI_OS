import os
import re
import html as html_lib
import time
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

from app.core.logging_conf import get_module_logger

load_dotenv()
logger = get_module_logger("notification")

# Valid inline tags that Telegram HTML parser accepts.
_TG_TAG_RE = re.compile(
    r"(</?(?:b|i|u|s|code|pre|tg-spoiler)>"
    r'|<a\s+href=["\'][^"\'<>]*["\'][^>]*>'
    r"|</a>)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Remove all HTML tags and unescape entities."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))


def sanitize_md_to_tg_html(text: str) -> str:
    """Convert LLM output (Markdown or mixed HTML) to valid Telegram HTML.

    1. Convert Markdown bold/headers/bullets to HTML.
    2. Escape only the text segments; pass valid Telegram tags through unchanged.
    3. Close any unclosed inline tags to prevent Telegram 400 errors.
    """
    if not text:
        return text

    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^#{1,6}\s*(.*)", r"<b>\1</b>", text)
    text = re.sub(r"^(\s*)[*+-]\s+", r"\1• ", text, flags=re.MULTILINE)

    parts = _TG_TAG_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        result.append(part if i % 2 else html_lib.escape(part))
    text = "".join(result)

    for tag in ("b", "i", "u", "s", "code", "pre"):
        unmatched = text.count(f"<{tag}>") - text.count(f"</{tag}>")
        if unmatched > 0:
            text += f"</{tag}>" * unmatched

    return text


def _split_plain(text: str, limit: int) -> list[str]:
    """Split plain text into chunks ≤ limit chars at word boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def _split_html_naive(text: str, limit: int) -> list[str]:
    """Split HTML at paragraph boundaries (\\n\\n).

    Each chunk is sent as-is with parse_mode=HTML.
    Oversized individual paragraphs are stripped to plain text and word-split —
    losing inline formatting for that paragraph, but preserving all content.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(para) > limit:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_plain(_strip_html(para), limit))
        elif current and len(current) + len(para) + 2 > limit:
            chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para).lstrip("\n") if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text]


# ---------------------------------------------------------------------------
# Core send logic
# ---------------------------------------------------------------------------

def _send_chunks(chat_id: str, chunks: list[str], parse_mode: str | None, token: str) -> bool:
    """Send a list of text chunks with 429-retry.

    Returns True if any chunk failed with 400 (HTML parse error) — caller
    should retry the whole message as plain text.
    """
    send_url = f"https://api.telegram.org/bot{token}/sendMessage"
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        label = f"chunk {i + 1}/{total}" if total > 1 else "message"
        payload: dict = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(send_url, json=payload, timeout=15)

            if resp.status_code == 429:
                try:
                    retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    retry_after = 5
                logger.warning(f"[TELEGRAM] Rate-limited on {label}; retry after {retry_after}s")
                time.sleep(retry_after)
                resp = requests.post(send_url, json=payload, timeout=15)

            if resp.status_code == 400 and parse_mode == "HTML":
                logger.warning(
                    f"[TELEGRAM] HTML parse failed on {label}: {resp.text}"
                )
                return True  # signal top-level to retry as plain

            if resp.status_code != 200:
                logger.error(f"[TELEGRAM] Failed to send {label}: {resp.text}")

        except Exception as e:
            logger.error(f"[TELEGRAM] Connection error on {label}: {e}")

    return False


def _send_telegram_impl(chat_id, text: str, parse_mode: str | None) -> None:
    """Dispatch: split into chunks and send. Long messages become multiple messages."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No TELEGRAM_BOT_TOKEN in environment.")
        return

    limit = int(os.getenv("TELEGRAM_LIMIT", "4000"))
    logger.info(f"[TELEGRAM] len={len(text)} parse_mode={parse_mode}; head={text[:80]!r}")

    chunks = _split_html_naive(text, limit) if parse_mode == "HTML" else _split_plain(text, limit)
    had_400 = _send_chunks(chat_id, chunks, parse_mode, token)

    if had_400 and parse_mode == "HTML":
        logger.warning("[TELEGRAM] HTML parse failed; retrying entire message as plain text")
        plain_chunks = _split_plain(_strip_html(text), limit)
        _send_chunks(chat_id, plain_chunks, None, token)


# ---------------------------------------------------------------------------
# Public API — same call signature as before
# ---------------------------------------------------------------------------

def send_telegram_msg(chat_id, text) -> None:
    """Send a plain-text Telegram message.

    HTML tags in `text` are stripped before sending. Use this for coach
    briefings, error messages, and all notifications that don't contain links.
    """
    safe = sanitize_md_to_tg_html(text) if text else ""
    _send_telegram_impl(chat_id, _strip_html(safe), parse_mode=None)


def send_telegram_html(chat_id, html_text) -> None:
    """Send an HTML-formatted Telegram message.

    Use this when the message contains clickable <a href> links (e.g. news
    briefings). The text is sanitized and balanced before sending.
    """
    safe = sanitize_md_to_tg_html(html_text) if html_text else ""
    _send_telegram_impl(chat_id, safe, parse_mode="HTML")


def send_typing_action(chat_id) -> None:
    """Send 'typing…' indicator. Non-critical; silently ignored on failure."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=3,
        )
    except Exception:
        pass


def send_html_email(subject, html_content, config) -> None:
    """Send an HTML email via SMTP."""
    email_cfg = config.get("email_config", {})
    if not email_cfg.get("enabled"):
        return

    env_sender = os.getenv("EMAIL_SENDER")
    env_password = os.getenv("EMAIL_PASSWORD")
    env_receiver = os.getenv("EMAIL_RECEIVER")

    if not all([env_sender, env_password, env_receiver]):
        logger.error("[EMAIL] Missing EMAIL_SENDER/PASSWORD/RECEIVER in .env")
        return

    try:
        smtp_server = email_cfg.get("smtp_server", "smtp.gmail.com")
        smtp_port = int(email_cfg.get("smtp_port", 587))
        msg = MIMEMultipart()
        msg["From"] = env_sender
        msg["To"] = env_receiver
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(env_sender, env_password)
        server.send_message(msg)
        server.quit()
        logger.info(f"[EMAIL] Sent report to {env_receiver}")
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send email: {e}")


def send_inline_keyboard_menu(
    chat_id: str,
    text: str,
    buttons: list[list[dict]],
) -> None:
    """Send Telegram message with inline keyboard for button responses.

    Args:
        chat_id: Telegram chat ID
        text: Message text to display
        buttons: 2D list of button dicts, each with "text" and "callback_data"
                 Example: [[{"text": "1", "callback_data": "rpe:act123:1"}, ...]]
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No TELEGRAM_BOT_TOKEN in environment.")
        return

    send_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons},
    }

    try:
        resp = requests.post(send_url, json=payload, timeout=15)

        if resp.status_code == 429:
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                retry_after = 5
            logger.warning(f"[TELEGRAM] Rate-limited on keyboard menu; retry after {retry_after}s")
            time.sleep(retry_after)
            resp = requests.post(send_url, json=payload, timeout=15)

        if resp.status_code != 200:
            logger.error(f"[TELEGRAM] Failed to send inline keyboard menu: {resp.text}")

    except Exception as e:
        logger.error(f"[TELEGRAM] Connection error sending inline keyboard: {e}")


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Answer a Telegram callback query to clear the loading spinner.

    Args:
        callback_query_id: The callback_query.id from Telegram
        text: Optional toast notification text (shown to user)
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No TELEGRAM_BOT_TOKEN in environment.")
        return

    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
    }
    if text:
        payload["text"] = text
        payload["show_alert"] = False  # Show as toast, not popup

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"[TELEGRAM] Failed to answer callback query: {resp.text}")
    except Exception as e:
        logger.error(f"[TELEGRAM] Connection error answering callback: {e}")

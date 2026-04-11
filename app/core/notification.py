import os
import logging
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import re
import html

load_dotenv()
logger = logging.getLogger(__name__)

# Matches valid Telegram HTML tags that should pass through unchanged.
# Telegram supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a href>, <tg-spoiler>
_TG_TAG_RE = re.compile(
    r'(</?(?:b|i|u|s|code|pre|tg-spoiler)>'
    r'|<a\s+href=["\'][^"\'<>]*["\'][^>]*>'
    r'|</a>)',
    re.IGNORECASE
)


def _strip_html(text: str) -> str:
    """Remove all HTML tags and unescape entities, yielding clean plain text."""
    no_tags = re.sub(r'<[^>]+>', '', text)
    return html.unescape(no_tags)


def sanitize_md_to_tg_html(text: str) -> str:
    """Convert LLM output (Markdown or HTML) to valid Telegram HTML.

    1. Convert Markdown bold/headers/bullets to HTML.
    2. Split on valid Telegram tags; escape only text segments.
       This preserves <a href="..."> links that the old approach destroyed.
    3. Balance unclosed <b> tags to prevent Telegram 400 errors.
    """
    if not text:
        return text

    # Step 1: Convert Markdown syntax to HTML
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?m)^#{1,6}\s*(.*)', r'<b>\1</b>', text)
    text = re.sub(r'^(\s*)[*+-]\s+', r'\1• ', text, flags=re.MULTILINE)

    # Step 2: Split on valid Telegram tags; escape only the text segments.
    # re.split() with a capturing group returns [text, tag, text, tag, ...text]
    parts = _TG_TAG_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(html.escape(part))  # text segment — escape special chars
        else:
            result.append(part)               # valid tag — pass through unchanged
    text = ''.join(result)

    # Step 3: Balance unclosed <b> tags (most common Gemini formatting error)
    open_b = text.count('<b>') - text.count('</b>')
    if open_b > 0:
        text += '</b>' * open_b

    return text


def send_telegram_msg(chat_id, text):
    """Send formatted message via Telegram Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No token found in environment variables.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    safe_text = sanitize_md_to_tg_html(text)

    payload = {
        "chat_id": chat_id,
        "text": safe_text,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=payload)

        # Fallback: strip HTML tags so user receives clean plain text instead of raw markup
        if response.status_code == 400 and "parse entities" in response.text:
            logger.warning(f"[TELEGRAM] HTML parse failed. Fallback to raw text. Error: {response.text}")
            payload["text"] = _strip_html(safe_text)
            payload.pop("parse_mode")
            response = requests.post(url, json=payload)

        if response.status_code != 200:
            logger.error(f"[TELEGRAM] Failed to send message: {response.text}")
            
    except Exception as e:
        logger.error(f"[TELEGRAM] Connection error: {e}")

def send_typing_action(chat_id):
    """
    Send 'typing...' indicator to Telegram immediately.
    Call this at the start of any slow operation so user gets instant feedback.
    The indicator auto-expires after 5 seconds on Telegram's side.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=3
        )
    except Exception:
        pass  # Non-critical: silently ignore if this fails

def send_html_email(subject, html_content, config):
    """Sends an HTML email report using SMTP configuration."""
    email_cfg = config.get("email_config", {})
    if not email_cfg.get("enabled"): return

    env_sender = os.getenv("EMAIL_SENDER")
    env_password = os.getenv("EMAIL_PASSWORD")
    env_receiver = os.getenv("EMAIL_RECEIVER")
    
    if not all([env_sender, env_password, env_receiver]):
        logger.error("[EMAIL] Missing EMAIL_SENDER/PASSWORD/RECEIVER in .env")
        return

    try:
        smtp_server = email_cfg.get('smtp_server', 'smtp.gmail.com')
        smtp_port = int(email_cfg.get('smtp_port', 587))

        msg = MIMEMultipart()
        msg['From'] = env_sender       
        msg['To'] = env_receiver       
        msg['Subject'] = subject
        msg.attach(MIMEText(html_content, 'html'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(env_sender, env_password)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"[EMAIL] Sent report to {env_receiver}")
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send email: {e}")
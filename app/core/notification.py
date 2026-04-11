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
    """Send formatted message via Telegram Bot API.

    Features:
    - Preserve HTML formatting for short messages
    - For long messages, attempt HTML-balanced chunking so formatting survives chunk boundaries
    - If message is enormous, send as a .txt attachment
    - Increment a telemetry counter in app.core.state when chunking is used
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No token found in environment variables.")
        return

    send_url = f"https://api.telegram.org/bot{token}/sendMessage"
    doc_url = f"https://api.telegram.org/bot{token}/sendDocument"

    safe_text = sanitize_md_to_tg_html(text)

    # Log message sizes and previews for debugging truncation issues
    try:
        logger.info(f"[TELEGRAM] Prepared message length={len(safe_text)}; head={safe_text[:80]!r}; tail={safe_text[-80:]!r}")
    except Exception:
        pass

    TELEGRAM_LIMIT = int(os.getenv('TELEGRAM_LIMIT', '4000'))
    ATTACHMENT_THRESHOLD = int(os.getenv('TELEGRAM_ATTACHMENT_THRESHOLD', '100000'))  # send as .txt if larger than this

    def post_json(url, payload):
        return requests.post(url, json=payload)

    # If message is extremely large, send as a text file attachment instead
    if len(safe_text) > ATTACHMENT_THRESHOLD:
        logger.info(f"[TELEGRAM] Message > {ATTACHMENT_THRESHOLD} chars; sending as document.")
        plain = _strip_html(safe_text)
        files = {"document": ("report.txt", plain.encode("utf-8"))}
        data = {"chat_id": chat_id, "caption": "Full report attached as text file."}
        try:
            resp = requests.post(doc_url, files=files, data=data)
            if resp.status_code != 200:
                logger.error(f"[TELEGRAM] Failed to send document: {resp.text}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Connection error while sending document: {e}")
        return

    # Short message: send as HTML with existing fallback
    if len(safe_text) <= TELEGRAM_LIMIT:
        payload = {"chat_id": chat_id, "text": safe_text, "parse_mode": "HTML"}
        try:
            response = post_json(send_url, payload)
            if response.status_code == 400 and "parse entities" in response.text:
                logger.warning(f"[TELEGRAM] HTML parse failed. Fallback to raw text. Error: {response.text}")
                payload["text"] = _strip_html(safe_text)
                payload.pop("parse_mode", None)
                response = post_json(send_url, payload)
            if response.status_code != 200:
                logger.error(f"[TELEGRAM] Failed to send message: {response.text}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Connection error: {e}")
        return

    # Medium-length message: attempt HTML-balanced chunking so formatting is preserved
    # Helper: split HTML into tokens (tags vs text)
    tag_re = re.compile(r'(<[^>]+>)')

    def _get_tag_name(tag: str):
        m = re.match(r'</\s*([a-zA-Z0-9\-]+)\s*>', tag)
        if m:
            return m.group(1).lower()
        m = re.match(r'<\s*([a-zA-Z0-9\-]+)', tag)
        return m.group(1).lower() if m else None

    def split_html_preserving_tags(html_text: str, limit: int):
        tokens = tag_re.split(html_text)
        chunks = []
        current = ''
        open_tags = []  # list of (tagname, opening_tag_str)

        for tok in tokens:
            if not tok:
                continue
            if tok.startswith('<') and tok.endswith('>'):
                # a tag
                tagname = _get_tag_name(tok)
                if tok.startswith('</'):
                    # closing tag
                    # remove last matching open tag if present
                    for i in range(len(open_tags)-1, -1, -1):
                        if open_tags[i][0] == tagname:
                            open_tags.pop(i)
                            break
                    candidate = current + tok
                else:
                    # opening tag (could have attrs)
                    open_tags.append((tagname, tok))
                    candidate = current + tok
            else:
                # text
                candidate = current + tok
            # If adding this token would exceed limit, close current chunk
            if len(candidate) > limit:
                # If current is empty, token itself longer than limit -> hard split text inside
                if not current:
                    # split tok (must be text because tags are small). Split tok into substrings
                    piece = tok
                    while piece:
                        take = piece[:limit]
                        # close tags in take
                        suffix = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
                        chunks.append(take + suffix)
                        piece = piece[limit:]
                    # current remains empty
                    current = ''
                else:
                    # close current by appending closing tags
                    suffix = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
                    chunks.append(current + suffix)
                    # reopen tags for next chunk
                    opener = ''.join(t[1] for t in open_tags)
                    current = opener
                    # Re-evaluate tok: append tok to current (it should fit now, unless tok itself > limit)
                    if tok.startswith('<') and tok.endswith('>'):
                        # tag
                        current += tok
                    else:
                        # text
                        # if still too big, split
                        remaining = tok
                        while remaining:
                            space = limit - len(current)
                            if space <= 0:
                                # start new chunk
                                suffix = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
                                chunks.append(current + suffix)
                                current = ''.join(t[1] for t in open_tags)
                                space = limit - len(current)
                            take = remaining[:space]
                            current += take
                            remaining = remaining[space:]
                            if remaining:
                                suffix = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
                                chunks.append(current + suffix)
                                current = ''.join(t[1] for t in open_tags)
                continue
            else:
                # safe to accept candidate
                current = candidate
        # finalize
        if current:
            suffix = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
            chunks.append(current + suffix)
        return chunks

    try:
        chunks = split_html_preserving_tags(safe_text, TELEGRAM_LIMIT)
    except Exception as e:
        logger.warning(f"[TELEGRAM] HTML chunking failed: {e}; falling back to plain text chunking")
        # fallback: plain-text chunking
        plain = _strip_html(safe_text).replace('\r\n', '\n')
        paragraphs = plain.split('\n\n')
        chunks = []
        current = ''
        for p in paragraphs:
            if not p:
                p = '\n'
            if len(p) > TELEGRAM_LIMIT:
                lines = p.split('\n')
                for line in lines:
                    if len(current) + len(line) + 1 > TELEGRAM_LIMIT:
                        if current:
                            chunks.append(current)
                        current = line
                    else:
                        current = (current + '\n' + line) if current else line
                if len(current) + 2 > TELEGRAM_LIMIT:
                    chunks.append(current)
                    current = ''
                else:
                    current = current + '\n\n' if current else '\n\n'
            else:
                if len(current) + len(p) + 2 > TELEGRAM_LIMIT:
                    if current:
                        chunks.append(current)
                    current = p
                else:
                    current = (current + '\n\n' + p) if current else p
        if current:
            chunks.append(current)

    # record telemetry
    try:
        from app.core.state import state
        state.increment_chunked_send_count(1)
    except Exception:
        pass

    # Send chunks (prefer HTML where possible)
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        try:
            resp = post_json(send_url, payload)
            # If parse error occurs on a chunk, fallback to plain text for that chunk
            if resp.status_code == 400 and "parse entities" in resp.text:
                logger.warning(f"[TELEGRAM] Chunk HTML parse failed; sending plain text chunk. Error: {resp.text}")
                plain_chunk = _strip_html(chunk)
                resp = post_json(send_url, {"chat_id": chat_id, "text": plain_chunk})
            if resp.status_code != 200:
                logger.error(f"[TELEGRAM] Failed to send chunk {i+1}/{len(chunks)}: {resp.text}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Connection error while sending chunk {i+1}: {e}")

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
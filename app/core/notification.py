import os
import logging
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import re
import html # Thêm thư viện này ở đầu file

load_dotenv()
logger = logging.getLogger(__name__)

def sanitize_md_to_tg_html(text: str) -> str:
    if not text:
        return text
    
    # BƯỚC 1: Thoát tất cả ký tự HTML nguy hiểm (Biến < thành &lt;, > thành &gt;)
    # Điều này giúp xử lý các trường hợp nhịp tim <138 bpm cực kỳ an toàn
    text = html.escape(text)

    # BƯỚC 2: Sau khi đã an toàn, chúng ta mới dịch ngược lại 
    # các ký tự Markdown thành thẻ HTML chuẩn của Telegram
    
    # In đậm: **text** -> <b>text</b>
    # Lưu ý: Dùng &ast; vì html.escape đã biến * thành ký tự an toàn tùy phiên bản
    # Nhưng đơn giản nhất là xử lý in đậm trước hoặc sau escape cẩn thận:
    
    # Cách tốt nhất: Dịch MD sang HTML placeholder, sau đó escape, rồi trả lại HTML
    # Nhưng để đơn giản và hiệu quả cho trường hợp của anh:
    text = text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>") # Nếu AI tự sinh <b>
    
    # Xử lý các dấu sao Markdown (Bây giờ nó là dấu * bình thường)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    # Header
    text = re.sub(r'(?m)^#{1,6}\s*(.*)', r'<b>\1</b>', text)
    
    # Bullet points
    text = re.sub(r'^(\s*)[*+-]\s+', r'\1• ', text, flags=re.MULTILINE)

    return text

def send_telegram_msg(chat_id, text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("[TELEGRAM] No token found in environment variables.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Bơm văn bản qua tầng Sanitizer trước khi đóng gói
    safe_text = sanitize_md_to_tg_html(text)
    
    payload = {
        "chat_id": chat_id,
        "text": safe_text,
        "parse_mode": "HTML" 
    }
    
    try:
        response = requests.post(url, json=payload)
        
        # [FALLBACK] Cứu nét nếu lỡ có thẻ HTML nào đó bị hở/lỗi
        if response.status_code == 400 and "parse entities" in response.text:
            logger.warning(f"[TELEGRAM] HTML parse failed. Cứu nét văn bản thô. Lỗi: {response.text}")
            payload.pop("parse_mode") 
            response = requests.post(url, json=payload)
            
        if response.status_code != 200:
            logger.error(f"[TELEGRAM] Failed to send message: {response.text}")
            
    except Exception as e:
        logger.error(f"[TELEGRAM] Connection error: {e}")

def send_html_email(subject, html_content, config):
    """
    Sends an HTML email report using SMTP configuration.
    """
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
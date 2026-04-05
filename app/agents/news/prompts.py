"""
Prompt builders for the News Agent.

Zone 3 boundary rule (per CLAUDE.md):
- Python logic and variable names: English
- Injected f-string content / user-facing templates: Vietnamese

Security note: use .replace() instead of f-strings to inject article content.
RSS article titles/summaries may contain literal {} characters (e.g. GDP growth {2.5%}),
which would cause KeyError with str.format() or f-strings.
"""

_MORNING_TEMPLATE = """Hôm nay là {date_str}.

Dưới đây là các tin tức mới nhất:

{articles_text}

Hãy tóm tắt 3-5 tin quan trọng nhất theo format sau:
📰 TIN TỨC BUỔI SÁNG

Với mỗi tin: dùng emoji phù hợp + tiêu đề ngắn gọn + 1-2 câu tóm tắt.

Tone: Ngắn gọn, rõ ràng, tích cực để bắt đầu ngày mới.
Không dùng Markdown headers (##). Dùng bold Telegram (<b>tiêu đề</b>) nếu cần."""

_AFTERNOON_TEMPLATE = """Hôm nay là {date_str}.

Cập nhật tin tức buổi chiều:

{articles_text}

Hãy tóm tắt 3-5 tin nổi bật nhất theo format sau:
🌆 CẬP NHẬT CHIỀU

Với mỗi tin: dùng emoji phù hợp + tiêu đề + 1-2 câu phân tích ngắn.

Tone: Phân tích, trung lập, nhìn nhận đa chiều.
Không dùng Markdown headers (##). Dùng bold Telegram (<b>tiêu đề</b>) nếu cần."""


def build_morning_news_prompt(articles_text: str, date_str: str) -> str:
    """Build the morning news summary prompt. Uses .replace() to safely inject content."""
    return _MORNING_TEMPLATE.replace("{date_str}", date_str).replace("{articles_text}", articles_text)


def build_afternoon_news_prompt(articles_text: str, date_str: str) -> str:
    """Build the afternoon news update prompt. Uses .replace() to safely inject content."""
    return _AFTERNOON_TEMPLATE.replace("{date_str}", date_str).replace("{articles_text}", articles_text)

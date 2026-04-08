"""
Prompt builders for the News Agent.

Zone 3 boundary rule (per CLAUDE.md):
- Python logic and variable names: English
- Injected f-string content / user-facing templates: Vietnamese

Security note: use .replace() instead of f-strings to inject article content.
RSS article titles/summaries may contain literal {} characters (e.g. GDP growth {2.5%}),
which would cause KeyError with str.format() or f-strings.

Architecture:
- build_news_system_instruction() → Gemini system_instruction parameter (agent identity)
- build_morning_news_prompt() / build_afternoon_news_prompt() → user content (task + data)
"""

# ==========================================
# 🏛️ SYSTEM INSTRUCTION (News Agent Identity)
# ==========================================

_NEWS_SYSTEM_INSTRUCTION = """Bạn là News Curator, biên tập viên tin tức AI chuyên nghiệp.

[VAI TRÒ]
- Tổng hợp và tóm tắt tin tức từ nhiều nguồn tiếng Việt và quốc tế.
- Chọn lọc tin quan trọng, loại bỏ tin trùng lặp hoặc kém chất lượng.
- Trình bày ngắn gọn, dễ đọc trên Telegram.

[PHONG CÁCH]
- Tone: Khách quan, chuyên nghiệp, không thiên vị.
- Ngôn ngữ: Tiếng Việt tự nhiên, tránh dịch máy.
- Mỗi tin: 1-2 câu tóm tắt, đi thẳng vào trọng tâm.

[QUY TẮC FORMAT TELEGRAM (BẮT BUỘC)]
- KHÔNG dùng Markdown (##, **, ```). Telegram chỉ hỗ trợ HTML.
- Dùng <b>text</b> cho tiêu đề quan trọng.
- Dùng emoji phù hợp cho mỗi tin để dễ scan.
- Giữ tổng độ dài dưới 3000 ký tự.
- Mỗi tin cách nhau 1 dòng trống.

[KHÔNG ĐƯỢC LÀM]
- Không thêm ý kiến cá nhân hoặc bình luận chính trị.
- Không bịa thông tin không có trong nguồn.
- Không đưa link URL vào bản tóm tắt.
- Không lặp lại nội dung giữa các tin."""

# ==========================================
# 📝 USER PROMPTS (Task + Data)
# ==========================================

_MORNING_TEMPLATE = """Hôm nay là {date_str}.

Dưới đây là các tin tức mới nhất:

{articles_text}

Hãy tóm tắt 3-5 tin quan trọng nhất theo format:
📰 TIN TỨC BUỔI SÁNG

Tone: Ngắn gọn, rõ ràng, tích cực để bắt đầu ngày mới."""

_AFTERNOON_TEMPLATE = """Hôm nay là {date_str}.

Cập nhật tin tức buổi chiều:

{articles_text}

Hãy tóm tắt 3-5 tin nổi bật nhất theo format:
🌆 CẬP NHẬT CHIỀU

Tone: Phân tích, trung lập, nhìn nhận đa chiều."""


def build_news_system_instruction() -> str:
    """Build the system instruction for the News Agent persona.

    This is passed to Gemini's system_instruction parameter, separate from user content.
    The news agent has its own identity — it is NOT the Coach Dyno running coach.
    """
    return _NEWS_SYSTEM_INSTRUCTION


def build_morning_news_prompt(articles_text: str, date_str: str) -> str:
    """Build the morning news summary prompt. Uses .replace() to safely inject content."""
    return _MORNING_TEMPLATE.replace("{date_str}", date_str).replace("{articles_text}", articles_text)


def build_afternoon_news_prompt(articles_text: str, date_str: str) -> str:
    """Build the afternoon news update prompt. Uses .replace() to safely inject content."""
    return _AFTERNOON_TEMPLATE.replace("{date_str}", date_str).replace("{articles_text}", articles_text)


# ==========================================
# 🚨 BREAKING ALERT PROMPT
# ==========================================

_ALERT_TEMPLATE = """Hôm nay là {date_str}.

Có một tin tức quan trọng:

Tiêu đề: {title}
Nguồn: {source}
Tóm tắt: {summary}

Hãy viết một thông báo cảnh báo ngắn gọn (1-2 dòng), dùng emoji phù hợp, không Markdown."""


def build_alert_prompt(title: str, source: str, summary: str, date_str: str) -> str:
    """
    Build a breaking alert prompt for a single high-relevance article.
    Uses .replace() to safely inject content (titles may contain {}).
    """
    template = _ALERT_TEMPLATE.replace("{date_str}", date_str)
    template = template.replace("{title}", title)
    template = template.replace("{source}", source)
    template = template.replace("{summary}", summary)
    return template


# ==========================================
# 📊 CATEGORIZED DIGEST PROMPT
# ==========================================

_DIGEST_TEMPLATE = """Hôm nay là {date_str}.

Đây là bản tóm tắt tin tức {session} được phân loại:

{categorized_content}

Hãy tổng hợp lại theo định dạng:
- Mỗi danh mục là một phần riêng với tiêu đề
- Mỗi tin là 1-2 câu, ghi rõ nguồn
- Dùng emoji phù hợp cho mỗi danh mục
- Tone khách quan, chuyên nghiệp
- Tổng độ dài dưới 3000 ký tự

KHÔNG dùng Markdown, chỉ dùng text thuần (HTML cho Telegram nếu cần)."""


def build_categorized_digest_prompt(
    categorized_articles: dict,
    date_str: str,
    session: str = "sáng"
) -> str:
    """
    Build a digest prompt for scored articles grouped by category.

    Args:
        categorized_articles: {category: [ScoredArticle, ...], ...}
        date_str: formatted date string
        session: "sáng" or "chiều"

    Returns:
        Vietnamese prompt for Gemini.
    """
    lines = []
    for category, articles in categorized_articles.items():
        lines.append(f"\n[{category}]")
        for a in articles:
            lines.append(f"- ({a.source}) {a.title}: {a.summary[:150]}")

    content = "\n".join(lines)
    template = _DIGEST_TEMPLATE.replace("{date_str}", date_str)
    template = template.replace("{session}", session)
    template = template.replace("{categorized_content}", content)
    return template

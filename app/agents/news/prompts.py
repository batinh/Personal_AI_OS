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
- Dùng <a href="URL">Đọc thêm</a> để liên kết nguồn cho mỗi tin (URL lấy từ dữ liệu đầu vào).
- Dùng emoji phù hợp cho mỗi tin để dễ scan.
- Giữ tổng độ dài dưới 3000 ký tự.
- Mỗi tin cách nhau 1 dòng trống.

[KHÔNG ĐƯỢC LÀM]
- Không thêm ý kiến cá nhân hoặc bình luận chính trị.
- Không bịa thông tin không có trong nguồn.
- Không tự bịa URL — chỉ dùng URL được cung cấp trong dữ liệu.
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
# 🚨 BREAKING ALERT PROMPT (BATCH)
# ==========================================

_BATCH_ALERT_TEMPLATE = """Hôm nay là {date_str}.

Các tin tức nóng cần cảnh báo ngay:

{articles_text}

Hãy viết MỘT thông báo cảnh báo duy nhất, ngắn gọn:
- Mỗi tin 1-2 câu, dùng <b>tiêu đề</b> và emoji phù hợp
- Kèm <a href="URL">Đọc thêm</a> cho mỗi tin (URL lấy từ dữ liệu)
- KHÔNG dùng Markdown, chỉ HTML
- Tổng dưới 1500 ký tự"""


def build_batch_alert_prompt(articles: list, date_str: str) -> str:
    """
    Build a single breaking-alert prompt for one or more high-relevance articles.

    Uses .replace() to inject content safely — article titles may contain {}.

    Args:
        articles: list of ScoredArticle objects
        date_str: formatted date string

    Returns:
        Vietnamese prompt for Gemini.
    """
    lines = []
    for i, a in enumerate(articles, 1):
        link = getattr(a, "link", "")
        summary_short = getattr(a, "summary", "")[:200]
        lines.append(
            f"{i}. {a.title}\n"
            f"   Nguồn: {a.source} | URL: {link}\n"
            f"   Tóm tắt: {summary_short}"
        )
    articles_text = "\n\n".join(lines)
    template = _BATCH_ALERT_TEMPLATE.replace("{date_str}", date_str)
    template = template.replace("{articles_text}", articles_text)
    return template


# ==========================================
# 📊 CATEGORIZED DIGEST PROMPT
# ==========================================

_DIGEST_TEMPLATE = """Hôm nay là {date_str}.

Đây là bản tóm tắt tin tức {session} được phân loại:

{categorized_content}

Hãy tổng hợp lại theo định dạng:
- Mỗi danh mục là một phần riêng với tiêu đề <b>emoji DANH MỤC</b>
- Mỗi tin là 1-2 câu, kèm <a href="URL">Đọc thêm</a> (URL lấy từ dữ liệu, không tự bịa)
- Dùng emoji phù hợp cho mỗi danh mục
- Tone khách quan, chuyên nghiệp
- Tổng độ dài dưới 3000 ký tự

KHÔNG dùng Markdown, chỉ dùng HTML cho Telegram."""


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
        session: "sáng", "chiều", or "tối"

    Returns:
        Vietnamese prompt for Gemini.
    """
    lines = []
    for category, articles in categorized_articles.items():
        lines.append(f"\n[{category}]")
        for a in articles:
            link = getattr(a, "link", "")
            lines.append(
                f"- ({a.source}) {a.title}: {a.summary[:150]}\n  URL: {link}"
            )

    content = "\n".join(lines)
    template = _DIGEST_TEMPLATE.replace("{date_str}", date_str)
    template = template.replace("{session}", session)
    template = template.replace("{categorized_content}", content)
    return template

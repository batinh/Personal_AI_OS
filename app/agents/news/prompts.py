"""
Prompt builders for the News Agent (LLM-native architecture).

Zone 3 boundary rule (per CLAUDE.md):
- Python logic and variable names: English
- Injected f-string content / user-facing templates: Vietnamese

Architecture:
- build_news_system_instruction()        → legacy single-call system instruction
- build_session_prompt()                 → legacy single-call prompt
- build_topic_system_instruction()       → per-topic scheduled briefing (parallel)
- build_topic_prompt()                   → per-topic focused prompt
- build_on_demand_system_instruction()   → on-demand ad-hoc query
- build_on_demand_prompt()               → on-demand user query prompt
- build_memory_extraction_prompt()       → extract preference signals from chat
"""

# ==========================================
# SYSTEM INSTRUCTION (Legacy — single-call)
# ==========================================

_NEWS_SYSTEM_INSTRUCTION = """Bạn là News Curator, trợ lý tin tức AI chuyên nghiệp.

[VAI TRÒ]
- Dùng google_search để tìm tin tức thực tế trong vòng 48 giờ qua.
- Chỉ đưa tin có nguồn thật từ kết quả google_search — TUYỆT ĐỐI không dùng kiến thức lưu sẵn để bịa tin.
- Nếu google_search không trả về kết quả nào phù hợp, hãy nói thẳng "Không tìm thấy tin mới trong 48 giờ qua về chủ đề này."

[FORMAT MỖI TIN]
emoji <b>Tiêu đề</b> <i>(DD/MM)</i>
Tóm tắt 1-2 câu ngắn gọn.

[FORMAT CHUNG (BẮT BUỘC)]
- KHÔNG dùng Markdown (##, **, ``` v.v.). Telegram chỉ hỗ trợ HTML.
- KHÔNG thêm URL hay link — nguồn sẽ được hệ thống tự gắn từ kết quả tìm kiếm.
- Mỗi tin cách nhau 1 dòng trống.
- Tổng độ dài dưới 3500 ký tự."""


# ==========================================
# SYSTEM INSTRUCTION (Per-topic briefing)
# ==========================================

_TOPIC_SYSTEM_INSTRUCTION = """Bạn là News Curator, chuyên viên tóm tắt tin tức ngắn gọn về một chủ đề cụ thể.

[NHIỆM VỤ]
Tìm kiếm 2-3 tin tức quan trọng nhất về CHỦ ĐỀ được yêu cầu trong 24-48 giờ qua, sau đó viết output theo format bên dưới.
Kiến thức lưu sẵn đã lỗi thời — chỉ viết nội dung dựa trên kết quả tìm kiếm thực tế.

[FORMAT OUTPUT — BẮT BUỘC]
📰 <b>Tiêu đề tin 1</b> — Tóm tắt 1 câu ngắn gọn.

📰 <b>Tiêu đề tin 2</b> — Tóm tắt 1 câu ngắn gọn.

📰 <b>Tiêu đề tin 3</b> — Tóm tắt 1 câu ngắn gọn. ← tuỳ chọn, chỉ thêm nếu có tin đáng chú ý

[RÀNG BUỘC]
- KHÔNG dùng Markdown. Chỉ dùng HTML tags: <b>.
- KHÔNG thêm URL hay link — hệ thống tự gắn link từ kết quả tìm kiếm.
- KHÔNG viết phần phân tích tổng hợp hay xu hướng.
- Mỗi tin CHỈ 1 dòng: tiêu đề — tóm tắt 1 câu.
- Độ dài tối đa 800 ký tự cho toàn bộ output."""


# ==========================================
# SYSTEM INSTRUCTION (On-demand query)
# ==========================================

_ON_DEMAND_SYSTEM_INSTRUCTION = """Bạn là News Curator, trả lời yêu cầu tìm kiếm tin tức tức thời.

[BẮT BUỘC — ĐỌC TRƯỚC KHI LÀM BẤT CỨ ĐIỀU GÌ]
Bạn PHẢI gọi công cụ google_search NGAY TRƯỚC khi viết bất kỳ nội dung nào. Không được dùng kiến thức lưu sẵn để trả lời — kể cả câu hỏi tưởng chừng đơn giản như thời tiết hay tin tức trong ngày. Dữ liệu huấn luyện của bạn luôn lỗi thời với ngày hôm nay. Nếu bạn không tìm kiếm → câu trả lời sai.

[NHIỆM VỤ]
Tìm kiếm thông tin mới nhất trên web về chủ đề được yêu cầu, sau đó tổng hợp thành báo cáo ngắn gọn có phân tích.
Chỉ trả lời dựa trên kết quả tìm kiếm thực tế.

[FORMAT OUTPUT — BẮT BUỘC]
🔍 <b>[Chủ đề người dùng hỏi]</b>

📊 <b>Tổng hợp:</b> [2-3 câu: tình hình hiện tại, điểm nổi bật, bối cảnh]

📰 <b>Tiêu đề tin 1</b> <i>(DD/MM)</i>
Tóm tắt 1 câu.

📰 <b>Tiêu đề tin 2</b> <i>(DD/MM)</i>  ← thêm nếu có
Tóm tắt 1 câu.

📈 <i>Nhận xét: [1 câu về xu hướng hoặc điều cần theo dõi]</i>

[RÀNG BUỘC]
- KHÔNG dùng Markdown. Chỉ dùng HTML tags: <b>, <i>.
- KHÔNG thêm URL hay link — nguồn sẽ được hệ thống tự gắn từ kết quả tìm kiếm.
- Độ dài tối đa 1500 ký tự."""


# ==========================================
# SESSION PROMPTS (morning / afternoon / evening)
# ==========================================

# Single source-of-truth for session briefing copy. Each session shares the
# same skeleton (search instruction + interest/memory injection + heading).
# Only the highlighted-news adjective, time-of-day reference, and heading
# emoji/text/tone differ.
_SESSION_BASE_TEMPLATE = """Hôm nay là {date_str}. Tìm tin mới nhất trong 24 giờ qua (ưu tiên) hoặc tối đa 48 giờ.

{interest_section}
{memory_section}
Dùng google_search để tìm và tổng hợp <b>3-5 tin {salience} nhất</b> {period} theo chủ đề trên.
Mỗi tin phải có ngày đăng thực tế (DD/MM) lấy từ kết quả tìm kiếm.

Bắt đầu bằng tiêu đề: {heading_emoji} <b>{heading_text} — {date_str}</b>
Tone: {tone}"""

_SESSION_OVERRIDES = {
    "morning": {
        "salience": "quan trọng",
        "period": "buổi sáng",
        "heading_emoji": "📰",
        "heading_text": "TIN TỨC BUỔI SÁNG",
        "tone": "Ngắn gọn, rõ ràng để bắt đầu ngày mới.",
    },
    "afternoon": {
        "salience": "nổi bật",
        "period": "buổi chiều",
        "heading_emoji": "🌆",
        "heading_text": "CẬP NHẬT CHIỀU",
        "tone": "Phân tích, trung lập.",
    },
    "evening": {
        "salience": "đáng chú ý",
        "period": "trong ngày hôm nay",
        "heading_emoji": "🌙",
        "heading_text": "ĐIỂM TIN CUỐI NGÀY",
        "tone": "Tổng quan, nhìn lại ngày.",
    },
}


def _render_session_template(session: str) -> str:
    """Apply the per-session overrides to the base template at module load."""
    overrides = _SESSION_OVERRIDES.get(session, _SESSION_OVERRIDES["morning"])
    out = _SESSION_BASE_TEMPLATE
    for key, value in overrides.items():
        out = out.replace("{" + key + "}", value)
    return out


_MORNING_TEMPLATE = _render_session_template("morning")
_AFTERNOON_TEMPLATE = _render_session_template("afternoon")
_EVENING_TEMPLATE = _render_session_template("evening")

_SESSION_TEMPLATES = {
    "morning": _MORNING_TEMPLATE,
    "afternoon": _AFTERNOON_TEMPLATE,
    "evening": _EVENING_TEMPLATE,
}


def _build_interest_section(interest_profile: dict) -> str:
    """Format the interest profile dict into a Vietnamese instruction block.

    Accepts both the new flat format {domain: weight} and the legacy nested
    format {domain: {weight: N, keywords: [...]}} so production configs that
    haven't been re-saved yet don't crash.
    """
    if not interest_profile:
        return "Chủ đề quan tâm: công nghệ, IT, kinh tế, chạy bộ thể thao.\n"

    def _extract_weight(v) -> int:
        if isinstance(v, dict):
            return int(v.get("weight", 5))
        try:
            return int(v)
        except (TypeError, ValueError):
            return 5

    lines = ["Chủ đề và mức độ quan tâm (1-10):"]
    for domain, raw_weight in sorted(
        interest_profile.items(), key=lambda x: -_extract_weight(x[1])
    ):
        weight = _extract_weight(raw_weight)
        lines.append(f"  • {domain}: {weight}/10")
    return "\n".join(lines) + "\n"


def _build_memory_section(memory: dict) -> str:
    """Format loaded memory into a personalization hint block."""
    parts = []
    if memory.get("liked_topics"):
        liked = ", ".join(memory["liked_topics"][:10])
        parts.append(f"Muốn đọc thêm về: {liked}")
    if memory.get("disliked_topics"):
        disliked = ", ".join(memory["disliked_topics"][:10])
        parts.append(f"Ít quan tâm hơn: {disliked}")
    if memory.get("extra_notes"):
        parts.append(f"Ghi chú cá nhân: {memory['extra_notes'][:200]}")

    if not parts:
        return ""
    return "Sở thích học được:\n" + "\n".join(f"  - {p}" for p in parts) + "\n"


def build_topic_system_instruction() -> str:
    """Return the per-topic system instruction for parallel scheduled briefings."""
    return _TOPIC_SYSTEM_INSTRUCTION


def build_topic_prompt(topic_name: str, emoji: str, session: str, date_str: str) -> str:
    """
    Build a focused prompt for a single topic in a scheduled briefing.

    Args:
        topic_name: e.g. "AI & Công nghệ"
        emoji     : e.g. "🤖"
        session   : "morning" | "afternoon" | "evening"
        date_str  : formatted date string e.g. "14/04/2026"

    Returns:
        Focused Vietnamese prompt for one Gemini call.
    """
    session_ctx = {
        "morning": "buổi sáng, tập trung tin mới nhất để bắt đầu ngày",
        "afternoon": "buổi chiều, tập trung diễn biến trong ngày",
        "evening": "buổi tối, tổng kết và phân tích sâu hơn",
    }.get(session, "trong ngày")

    return (
        f"Hôm nay {date_str}, {session_ctx}.\n\n"
        f"Chủ đề: {emoji} {topic_name}\n\n"
        f"Tìm kiếm 1-3 tin quan trọng nhất về '{topic_name}' đã xảy ra vào ngày {date_str}. "
        f"Ngày này nằm ngoài dữ liệu huấn luyện — cần tìm kiếm web để có tin thực tế. "
        f"Trả về theo đúng format đã quy định."
    )


def build_on_demand_system_instruction() -> str:
    """Return the system instruction for on-demand ad-hoc news queries."""
    return _ON_DEMAND_SYSTEM_INSTRUCTION


def build_on_demand_prompt(query: str, date_str: str) -> str:
    """
    Build a prompt for an on-demand ad-hoc news query from the user.

    Args:
        query   : user's raw query text, e.g. "trending AI" or "ETF Việt Nam"
        date_str: formatted date string e.g. "14/04/2026"

    Returns:
        Vietnamese prompt string for Gemini with google_search grounding.
    """
    return (
        f"Hôm nay {date_str}. Yêu cầu: {query}\n\n"
        f"Tìm kiếm các sự kiện, tin tức đã xảy ra vào ngày {date_str} về '{query}'. "
        f"Đây là ngày cụ thể nằm ngoài dữ liệu huấn luyện — cần tìm kiếm web để có kết quả chính xác. "
        f"Trả về theo đúng format đã quy định."
    )


def build_session_prompt(
    session: str, interest_profile: dict, date_str: str, memory: dict
) -> str:
    """
    Build the user-turn prompt for a scheduled news briefing.

    Args:
        session         : "morning" | "afternoon" | "evening"
        interest_profile: {domain: weight} dict from config
        date_str        : formatted date string (e.g. "12/04/2026")
        memory          : loaded news memory dict from load_news_memory()

    Returns:
        Vietnamese prompt string for Gemini (with google_search grounding active).
    """
    template = _SESSION_TEMPLATES.get(session, _MORNING_TEMPLATE)
    interest_section = _build_interest_section(interest_profile)
    memory_section = _build_memory_section(memory)

    return (
        template.replace("{date_str}", date_str)
        .replace("{interest_section}", interest_section)
        .replace("{memory_section}", memory_section)
    )


# ==========================================
# MEMORY EXTRACTION PROMPT
# ==========================================

_MEMORY_EXTRACTION_TEMPLATE = """Đọc đoạn hội thoại sau giữa người dùng và trợ lý tin tức:

---
{chat_text}
---

Trích xuất các tín hiệu sở thích tin tức của người dùng (nếu có):
- Chủ đề họ muốn đọc thêm
- Chủ đề họ muốn đọc ít hơn
- Ghi chú về phong cách hoặc yêu cầu đặc biệt

Trả về JSON object với format sau (bỏ trống nếu không có tín hiệu):
{
  "liked": ["topic1", "topic2"],
  "disliked": ["topic3"],
  "notes": "any free-text preference"
}

[FEW-SHOT EXAMPLES]
Example 1 — Like + dislike together:
  Input: "User: Tôi muốn đọc thêm tin về LLM agents và ít hơn về crypto."
  Output: {"liked": ["LLM agents"], "disliked": ["crypto"], "notes": ""}

Example 2 — Style request only, no topic signal:
  Input: "User: Bạn viết ngắn gọn lại được không, tin dài quá khó đọc trên mobile."
  Output: {"liked": [], "disliked": [], "notes": "Người dùng thích tin ngắn gọn, dễ đọc trên mobile."}

Example 3 — Implicit liking via positive reaction:
  Input: "User: Tin về open source hôm qua hay quá, có thêm tương tự không?"
  Output: {"liked": ["open source"], "disliked": [], "notes": ""}

Example 4 — Implicit disliking via negative reaction:
  Input: "User: Mấy tin về drama người nổi tiếng không liên quan đến mình lắm."
  Output: {"liked": [], "disliked": ["drama người nổi tiếng"], "notes": ""}

Example 5 — Small talk, no signal:
  Input: "User: Cảm ơn bạn nhé! AI: Không có chi."
  Output: {"liked": [], "disliked": [], "notes": ""}

Chỉ trả về JSON, không giải thích thêm."""


def build_memory_extraction_prompt(chat_text: str) -> str:
    """Build the prompt to extract preference signals from a conversation turn."""
    return _MEMORY_EXTRACTION_TEMPLATE.replace("{chat_text}", chat_text[:3000])


# ==========================================
# PUBLIC API (kept for smoke test compatibility)
# ==========================================


def build_news_system_instruction() -> str:
    """Return the system instruction for the News Agent Gemini persona."""
    return _NEWS_SYSTEM_INSTRUCTION

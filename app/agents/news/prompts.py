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
- Chỉ đưa tin có nguồn thật từ kết quả google_search — TUYỆT ĐỐI không dùng kiến thức lưu sẵn để bịa tin hoặc bịa URL.
- Nếu google_search không trả về kết quả nào phù hợp, hãy nói thẳng "Không tìm thấy tin mới trong 48 giờ qua về chủ đề này."

[NGUYÊN TẮC URL — BẮT BUỘC]
- URL trong "Đọc thêm" PHẢI là URL nguồn chính xác của bài báo đó.
- KHÔNG lấy URL từ bài/chủ đề khác gán vào bài này — dù URL đó có trong search results.
- Nếu bài đó không có URL rõ ràng trong search → BỎ HẲN dòng "Đọc thêm", không thay bằng URL khác.
- KHÔNG tự tạo URL.

[FORMAT MỖI TIN]
emoji <b>Tiêu đề</b> <i>(DD/MM)</i>
Tóm tắt 1-2 câu ngắn gọn.
<a href="URL_THỰC">Đọc thêm</a>   ← chỉ có nếu có URL thực

[FORMAT CHUNG (BẮT BUỘC)]
- KHÔNG dùng Markdown (##, **, ``` v.v.). Telegram chỉ hỗ trợ HTML.
- Mỗi tin cách nhau 1 dòng trống.
- Tổng độ dài dưới 3500 ký tự."""


# ==========================================
# SYSTEM INSTRUCTION (Per-topic briefing)
# ==========================================

_TOPIC_SYSTEM_INSTRUCTION = """Bạn là News Curator, chuyên viên phân tích một chủ đề cụ thể.

[BƯỚC BẮT BUỘC — THỰC HIỆN NGAY]
BƯỚC 1: Gọi google_search NGAY LẬP TỨC trước khi viết bất cứ nội dung nào.
Kiến thức lưu sẵn của bạn ĐÃ LỖI THỜI — tuyệt đối không viết phân tích hoặc tin tức nếu chưa tìm kiếm.
BƯỚC 2: Sau khi có kết quả tìm kiếm, viết output theo format bên dưới.

[NHIỆM VỤ]
Dùng google_search để tìm tin tức và phân tích về CHỦ ĐỀ được yêu cầu trong 24-48 giờ qua.

[NGUYÊN TẮC URL — BẮT BUỘC]
- URL "Đọc thêm" PHẢI là URL nguồn của chính bài báo đó. Không được dùng URL của bài khác.
- Nếu không có URL chính xác cho bài đó → BỎ HẲN "Đọc thêm", không thay bằng URL khác.
- KHÔNG tự tạo URL hay đoán URL.

[FORMAT OUTPUT — BẮT BUỘC]
📊 <b>Phân tích:</b> [2-3 câu tổng hợp: điều gì đang xảy ra, tại sao quan trọng, bối cảnh]

📰 <b>Tiêu đề tin 1</b> <i>(DD/MM)</i>
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📰 <b>Tiêu đề tin 2</b> <i>(DD/MM)</i>
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📰 <b>Tiêu đề tin 3</b> <i>(DD/MM)</i>  ← tuỳ chọn, chỉ thêm nếu có tin đáng chú ý
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📈 <i>Xu hướng: [1 câu nhận xét signal/pattern đang nổi trong tuần]</i>

[RÀNG BUỘC]
- KHÔNG dùng Markdown. Chỉ dùng HTML tags: <b>, <i>, <a href>.
- Độ dài tối đa 1200 ký tự cho toàn bộ output."""


# ==========================================
# SYSTEM INSTRUCTION (On-demand query)
# ==========================================

_ON_DEMAND_SYSTEM_INSTRUCTION = """Bạn là News Curator, trả lời yêu cầu tìm kiếm tin tức tức thời.

[NHIỆM VỤ — BẮT BUỘC]
BƯỚC 1 (BẮT BUỘC): Gọi google_search NGAY LẬP TỨC trước khi làm bất cứ điều gì khác.
Kiến thức lưu sẵn của bạn đã lỗi thời — KHÔNG được dùng để trả lời câu hỏi về tin tức hiện tại.
Không có lý do nào để bỏ qua bước tìm kiếm này.

BƯỚC 2: Tổng hợp kết quả google_search thành báo cáo ngắn gọn, có phân tích và nguồn.

[NGUYÊN TẮC URL — BẮT BUỘC]
- URL "Đọc thêm" PHẢI là URL nguồn của chính bài báo đó.
- Nếu không có URL chính xác → BỎ HẲN "Đọc thêm".
- KHÔNG tự tạo URL.

[FORMAT OUTPUT — BẮT BUỘC]
🔍 <b>[Chủ đề người dùng hỏi]</b>

📊 <b>Tổng hợp:</b> [2-3 câu: tình hình hiện tại, điểm nổi bật, bối cảnh]

📰 <b>Tiêu đề tin 1</b> <i>(DD/MM)</i>
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📰 <b>Tiêu đề tin 2</b> <i>(DD/MM)</i>  ← thêm nếu có
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📈 <i>Nhận xét: [1 câu về xu hướng hoặc điều cần theo dõi]</i>

[RÀNG BUỘC]
- KHÔNG dùng Markdown. Chỉ dùng HTML tags.
- Độ dài tối đa 1000 ký tự."""


# ==========================================
# SESSION PROMPTS (morning / afternoon / evening)
# ==========================================

_MORNING_TEMPLATE = """Hôm nay là {date_str}. Tìm tin mới nhất trong 24 giờ qua (ưu tiên) hoặc tối đa 48 giờ.

{interest_section}
{memory_section}
Dùng google_search để tìm và tổng hợp <b>3-5 tin quan trọng nhất</b> buổi sáng theo chủ đề trên.
Mỗi tin phải có ngày đăng thực tế (DD/MM) lấy từ kết quả tìm kiếm.

Bắt đầu bằng tiêu đề: 📰 <b>TIN TỨC BUỔI SÁNG — {date_str}</b>
Tone: Ngắn gọn, rõ ràng để bắt đầu ngày mới."""

_AFTERNOON_TEMPLATE = """Hôm nay là {date_str}. Tìm tin mới nhất trong 24 giờ qua (ưu tiên) hoặc tối đa 48 giờ.

{interest_section}
{memory_section}
Dùng google_search để tìm và tổng hợp <b>3-5 tin nổi bật nhất</b> buổi chiều theo chủ đề trên.
Mỗi tin phải có ngày đăng thực tế (DD/MM) lấy từ kết quả tìm kiếm.

Bắt đầu bằng tiêu đề: 🌆 <b>CẬP NHẬT CHIỀU — {date_str}</b>
Tone: Phân tích, trung lập."""

_EVENING_TEMPLATE = """Hôm nay là {date_str}. Tìm tin mới nhất trong 24 giờ qua (ưu tiên) hoặc tối đa 48 giờ.

{interest_section}
{memory_section}
Dùng google_search để tìm và tổng hợp <b>3-5 tin đáng chú ý nhất</b> trong ngày hôm nay theo chủ đề trên.
Mỗi tin phải có ngày đăng thực tế (DD/MM) lấy từ kết quả tìm kiếm.

Bắt đầu bằng tiêu đề: 🌙 <b>ĐIỂM TIN CUỐI NGÀY — {date_str}</b>
Tone: Tổng quan, nhìn lại ngày."""

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
    for domain, raw_weight in sorted(interest_profile.items(), key=lambda x: -_extract_weight(x[1])):
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
        f"⚠️ BẮT BUỘC: Gọi google_search ngay bây giờ để tìm 1-3 tin quan trọng nhất về '{topic_name}' "
        f"trong 24-48 giờ qua. Không được viết phân tích nếu chưa tìm kiếm.\n\n"
        f"Sau khi có kết quả tìm kiếm, trả về theo đúng format đã quy định."
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
        f"Hôm nay {date_str}. Yêu cầu tìm kiếm: {query}\n\n"
        f"⚠️ BẮT BUỘC: Gọi google_search ngay bây giờ để tìm thông tin mới nhất về '{query}' "
        f"(ưu tiên 24-48 giờ qua). Kiến thức lưu sẵn đã lỗi thời — KHÔNG được trả lời nếu chưa tìm kiếm.\n\n"
        f"Sau khi có kết quả tìm kiếm, trả về theo đúng format đã quy định."
    )


def build_session_prompt(session: str, interest_profile: dict, date_str: str, memory: dict) -> str:
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
        template
        .replace("{date_str}", date_str)
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

"""
Prompt builders for the News Agent (LLM-native architecture).

Zone 3 boundary rule (per CLAUDE.md):
- Python logic and variable names: English
- Injected f-string content / user-facing templates: Vietnamese

Architecture:
- build_news_system_instruction() → Gemini system_instruction (agent identity)
- build_session_prompt()          → single call with google_search grounding
- build_memory_extraction_prompt()→ extract preference signals from chat
"""

# ==========================================
# SYSTEM INSTRUCTION (News Agent Identity)
# ==========================================

_NEWS_SYSTEM_INSTRUCTION = """Bạn là News Curator, trợ lý tin tức AI chuyên nghiệp.

[VAI TRÒ]
- Dùng google_search để tìm tin tức thực tế trong vòng 48 giờ qua.
- Chỉ đưa tin có nguồn thật từ kết quả google_search — TUYỆT ĐỐI không dùng kiến thức lưu sẵn để bịa tin hoặc bịa URL.
- Nếu google_search không trả về kết quả nào phù hợp, hãy nói thẳng "Không tìm thấy tin mới trong 48 giờ qua về chủ đề này."

[NGUYÊN TẮC URL]
- Chỉ kèm link nếu URL đó có trong kết quả google_search. KHÔNG tự tạo URL.
- Nếu không có URL thực → bỏ hoàn toàn phần "Đọc thêm", không để link rỗng.

[FORMAT MỖI TIN]
emoji <b>Tiêu đề</b> <i>(DD/MM)</i>
Tóm tắt 1-2 câu ngắn gọn.
<a href="URL_THỰC">Đọc thêm</a>   ← chỉ có nếu có URL thực

[FORMAT CHUNG (BẮT BUỘC)]
- KHÔNG dùng Markdown (##, **, ``` v.v.). Telegram chỉ hỗ trợ HTML.
- Mỗi tin cách nhau 1 dòng trống.
- Tổng độ dài dưới 3500 ký tự."""


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

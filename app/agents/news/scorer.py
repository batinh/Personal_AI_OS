"""
News relevance scorer.

Uses Gemini to score articles against user interest profile.
Batch-scores to minimize API calls — one call per batch of articles.

Frozen dataclass ScoredArticle extends Article with score, category, and reason.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types

from app.agents.news.feeds import Article

logger = logging.getLogger("AI_COACH")
client = genai.Client()

_BATCH_SIZE = 20  # Max articles per Gemini call


@dataclass(frozen=True)
class ScoredArticle(Article):
    """Article extended with relevance score, category, and explanation."""
    score: int  # 0-10
    category: str  # technology, sports_running, it_workforce, economics_politics, general
    reason: str  # Vietnamese explanation


def _build_batch_scoring_prompt(articles_text: str, interest_profile: dict) -> str:
    """
    Build prompt for batch-scoring articles.

    Returns:
        Vietnamese prompt instructing Gemini to return JSON array of scores.
    """
    # Build interest profile summary for Gemini
    profile_summary = "Hồ sơ quan tâm của người dùng:\n"
    for category, details in interest_profile.items():
        keywords = ", ".join(details.get("keywords", []))
        weight = details.get("weight", 5)
        profile_summary += f"- {category} (trọng lượng: {weight}): {keywords}\n"

    prompt = f"""Bạn là chuyên gia phân tích tin tức. Hãy đánh giá mức độ liên quan của các tin tức sau dựa trên hồ sơ quan tâm của người dùng.

{profile_summary}

Các tin tức cần đánh giá:
{articles_text}

Hãy trả về JSON array với cấu trúc sau (CHỈ JSON, không có text khác):
[
  {{"index": 0, "score": 8, "category": "technology", "reason": "Tin về AI ảnh hưởng trực tiếp ngành IT"}},
  {{"index": 1, "score": 5, "category": "general", "reason": "Tin chung chung, ít liên quan"}}
]

Ghi chú:
- score: 0-10, 0 là không liên quan, 10 là rất quan trọng
- category: tên danh mục từ hồ sơ, hoặc "general" nếu không phù hợp
- reason: giải thích bằng tiếng Việt tại sao tin này quan trọng (1-2 câu)
- Sắp xếp theo index giống thứ tự tin tức đầu vào"""

    return prompt


def score_articles(
    articles: list[Article],
    interest_profile: dict,
    model_name: str = "models/gemini-2.0-flash"
) -> list[ScoredArticle]:
    """
    Batch-score articles using Gemini.

    Splits into batches to avoid prompt overflow. Returns list of ScoredArticle
    with score (0-10), category, and reason.

    On JSON parse error or Gemini error: falls back to score=5 (neutral) for failed items.
    """
    if not articles:
        return []

    scored: list[ScoredArticle] = []

    # Process in batches to avoid token overflow
    for batch_idx in range(0, len(articles), _BATCH_SIZE):
        batch = articles[batch_idx : batch_idx + _BATCH_SIZE]
        batch_prompt = _build_batch_scoring_prompt(
            _format_articles_for_scoring(batch),
            interest_profile
        )

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=batch_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=2000,
                    response_mime_type="application/json",
                ),
            )

            response_text = response.text or ""

            # Extract JSON from response (Gemini might add extra text)
            json_str = _extract_json(response_text)
            if not json_str:
                logger.warning("[NEWS-SCORER] Could not extract JSON from Gemini response, using defaults")
                scored.extend(_default_scores(batch))
                continue

            batch_scores = json.loads(json_str)
            if not isinstance(batch_scores, list):
                batch_scores = [batch_scores]

            # Map scores back to articles
            for item in batch_scores:
                try:
                    idx = int(item.get("index", -1))
                    if 0 <= idx < len(batch):
                        article = batch[idx]
                        score_val = int(item.get("score", 5))
                        score_val = max(0, min(10, score_val))  # Clamp to 0-10
                        category = str(item.get("category", "general")).lower()
                        reason = str(item.get("reason", "Đánh giá trung bình")).strip()

                        scored.append(ScoredArticle(
                            title=article.title,
                            summary=article.summary,
                            link=article.link,
                            source=article.source,
                            published=article.published,
                            score=score_val,
                            category=category,
                            reason=reason
                        ))
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning(f"[NEWS-SCORER] Error parsing score item {item}: {e}")
                    # Add article with default neutral score
                    idx = int(item.get("index", -1))
                    if 0 <= idx < len(batch):
                        scored.append(_neutral_scored(batch[idx]))

        except Exception as e:
            logger.error(f"[NEWS-SCORER] Error scoring batch: {e}")
            # Fall back to neutral scores for entire batch
            scored.extend(_default_scores(batch))

    return scored


def _format_articles_for_scoring(articles: list[Article]) -> str:
    """Format articles for scoring prompt (indexed by position)."""
    lines = []
    for i, a in enumerate(articles):
        lines.append(f"[{i}] {a.source}: {a.title}")
        if a.summary:
            lines.append(f"    {a.summary[:200]}")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[str]:
    """Extract JSON array from response text. Handles markdown code blocks and stray prose."""
    text = text.strip()

    # 1. Try direct parse first (response_mime_type=application/json should give clean output)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return text
        if isinstance(parsed, dict):
            return json.dumps([parsed])
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Strip markdown code fences
    for fence in ("```json", "```"):
        if fence in text:
            start = text.find(fence) + len(fence)
            end = text.find("```", start)
            if end > start:
                candidate = text[start:end].strip()
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        return candidate
                    if isinstance(parsed, dict):
                        return json.dumps([parsed])
                except (json.JSONDecodeError, ValueError):
                    pass

    # 3. Scan for outermost [...] bracket pair
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
        try:
            json.loads(candidate)  # Validate before returning
            return candidate
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _neutral_scored(article: Article) -> ScoredArticle:
    """Create a neutral-scored version of an article (fallback)."""
    return ScoredArticle(
        title=article.title,
        summary=article.summary,
        link=article.link,
        source=article.source,
        published=article.published,
        score=5,
        category="general",
        reason="Đánh giá trung bình do lỗi xử lý"
    )


def _default_scores(articles: list[Article]) -> list[ScoredArticle]:
    """Create neutral-scored versions of a batch (fallback)."""
    return [_neutral_scored(a) for a in articles]

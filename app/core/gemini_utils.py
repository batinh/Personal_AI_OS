"""
Shared Gemini response utilities.
"""

import re as _re

from google.genai import types

from app.core.schemas import RunAnalysisResult

DEFAULT_MODEL = "models/gemini-flash-latest"
GEMINI_API_VERSION = "v1beta"

# Matches "thought", "thoughtful", "thoughtfully", "thoughts", etc. — any word
# starting with "thought" followed by whitespace.  Incident 2026-04-21: Gemini
# emitted "thoughtful\n..." and the narrower pattern ^thought[\n\r ]\w missed it.
_THOUGHT_PREFIX_RE = _re.compile(r"^thought\w*[\n\r ]", _re.IGNORECASE)


def extract_text(response) -> str | None:
    """
    Extract non-thinking text from a Gemini response.

    Filters out parts where thought=True (chain-of-thought from thinking models
    like gemini-pro-latest, gemini-2.5-flash). Joins remaining text parts.
    Falls back to response.text when candidates structure is unavailable.
    """
    try:
        candidates = response.candidates or []
        if not candidates:
            return response.text or None
        parts = getattr(candidates[0].content, "parts", None) or []
        texts = [
            p.text
            for p in parts
            if getattr(p, "text", None) and not getattr(p, "thought", False)
        ]
        return "".join(texts).strip() or None
    except Exception:
        return response.text or None


def strip_thought_preamble(text: str) -> str | None:
    """
    Remove a raw 'thought\\n...' preamble that slipped through thought=False filtering.

    Returns None when the entire text appears to be thinking (no HTML or emoji anchor).
    """
    if not _THOUGHT_PREFIX_RE.match(text):
        return text

    anchor = _re.search(r"(<[bBiIaA][\s>]|📊|📰|🔍|📈|✅)", text)
    if not anchor:
        return None
    return text[anchor.start() :].strip() or None


class GeminiFailure(Exception):
    """Coach-facing explanation of a provider failure."""

    def __init__(self, message: str, *, blocks_batch: bool = False):
        super().__init__(message)
        self.blocks_batch = blocks_batch

    @classmethod
    def from_error(cls, error: Exception):
        code = getattr(error, "code", None)
        message = str(error).lower()
        if code == 402 or "prepayment credits are depleted" in message:
            return cls(
                "Gemini đã hết số dư trả trước. Vui lòng kiểm tra thanh toán.",
                blocks_batch=True,
            )
        if code == 404 and "model" in message:
            return cls(
                "Model Gemini đã chọn không khả dụng. Vui lòng kiểm tra cấu hình model.",
                blocks_batch=True,
            )
        if (
            isinstance(error, TimeoutError)
            or code == 504
            or any(
                token in message
                for token in ("timeout", "timed out", "deadline_exceeded")
            )
        ):
            return cls("Gemini phản hồi quá hạn. Vui lòng thử lại sau.")
        return cls("Chưa thể tạo phản hồi AI. Vui lòng thử lại sau.")


def validate_coach_model(client, model: str):
    """Probe structured generation with an inert tool declaration."""
    response = client.models.generate_content(
        model=model,
        contents="Trả về analysis_text là OK và gcs_score là 0. Không gọi công cụ.",
        config=types.GenerateContentConfig(
            response_schema=RunAnalysisResult,
            response_mime_type="application/json",
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="check_status", description="Check status."
                        )
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            ),
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    RunAnalysisResult.model_validate_json(response.text)

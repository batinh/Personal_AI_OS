import os
import re
import json

from google import genai
from google.genai import types

from app.core.database import (
    load_history_for_gemini,
    get_all_active_memories,
    insert_memory,
)
from app.agents.coach.utils import send_message_with_retry
from app.agents.coach.prompts import build_memory_extraction_prompt
from app.core.schemas import MemoryExtractionResult

from app.core.logging_conf import get_module_logger

logger = get_module_logger("coach")
client = genai.Client()


def extract_implicit_memory(user_id_str: str):
    """
    [BRAIN] Analyzes recent chats to extract or mutate implicit memory states.
    Uses Structured Outputs via Pydantic to strictly enforce Categories.
    """
    # [FEATURE FLAG] Fetch debug flag from environment variables (default is False)
    debug_mode = os.getenv("ENABLE_MEMORY_DEBUG", "false").lower() == "true"

    logger.info(f"[MEMORY] Starting extraction for user: {user_id_str}")

    raw_history = load_history_for_gemini(user_id_str, limit=30)
    if not raw_history:
        if debug_mode:
            logger.info("[MEMORY DEBUG] History is empty.")
        return

    chat_history_text = "\n".join(
        [
            f"{'User' if m['role']=='user' else 'AI'}: {m['parts'][0]}"
            for m in reversed(raw_history)
        ]
    )

    # [NEW] Fetch existing active memories globally (Cross-Domain Deduplication)
    memories = get_all_active_memories(user_id_str)

    if memories:
        existing_text = "\n".join(
            [f"- [{m['category'].upper()}]: {m['fact']}" for m in memories]
        )
    else:
        existing_text = "No existing states recorded."

    # Build the state-aware prompt
    prompt = build_memory_extraction_prompt(chat_history_text, existing_text)

    try:
        from app.core.config import load_config

        cfg = load_config()

        chat_session = client.chats.create(
            model=cfg.get("model_name", "models/gemini-2.0-flash"),
            config=types.GenerateContentConfig(
                temperature=0.2,  # Lowered temperature to minimize hallucinations
                response_mime_type="application/json",
                response_schema=MemoryExtractionResult,  # [ARCHITECTURE UPDATE] Strict Pydantic Enforcement
            ),
        )
        response = send_message_with_retry(chat_session, prompt)

        raw_text = response.text if response and response.text else "EMPTY_RESPONSE"

        if debug_mode:
            logger.info(f"[MEMORY DEBUG] Raw AI Response: {raw_text}")

        cleaned_text = re.sub(r"```json\n|\n```|```", "", raw_text).strip()

        if debug_mode:
            logger.info(f"[MEMORY DEBUG] Cleaned Text for JSON: {cleaned_text}")

        extracted_data = json.loads(cleaned_text)

        # Extract the 'items' list mapped from the Pydantic wrapper
        extracted_facts = extracted_data.get("items", [])

        valid_count = 0
        for i, item in enumerate(extracted_facts):
            if debug_mode:
                logger.info(f"[MEMORY DEBUG] Inspecting item {i}: {item}")

            if isinstance(item, dict):
                # Enforced by Schema, guaranteed to match Enum
                domain = item.get("domain", "general")
                category = item.get("category", "other")
                fact = item.get("fact")
                status = item.get("status", "active")  # Parse dynamic status

                if fact:
                    try:
                        if debug_mode:
                            logger.info(
                                f"[MEMORY DEBUG] Attempting DB insert for {user_id_str} | Category: {category} | Status: {status}"
                            )
                        insert_memory(user_id_str, domain, category, fact, status)
                        valid_count += 1
                    except Exception as db_err:
                        logger.error(f"[MEMORY] DB Insert failed: {db_err}")

        # Always output the final summary
        logger.info(f"[MEMORY] Success. Mutated {valid_count} states in core_memory.")

    except json.JSONDecodeError as e:
        logger.error(f"[MEMORY] JSON Parse Error: {e}")
    except Exception as e:
        import traceback

        logger.error(f"[MEMORY] CRITICAL ERROR: {str(e)}")
        if debug_mode:
            logger.error(traceback.format_exc())

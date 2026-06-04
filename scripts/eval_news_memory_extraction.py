#!/usr/bin/env python3
"""
Eval helper for the news memory-extraction prompt.

Why this exists:
- After Phase 5 added 5 few-shot examples, we want to spot-check JSON output
  quality on real chat samples without writing to production state.
- This script renders the prompt for a list of chat fixtures, calls Gemini,
  and prints (sample, parsed-or-error). It does NOT mutate any DB.

Usage:
    python scripts/eval_news_memory_extraction.py [--samples N]

Inputs: tests/fixtures/news_memory_samples.json (create if missing — see
        template at bottom of this file).

Output: one block per sample with the parsed JSON and any schema errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_FILE = REPO_ROOT / "tests" / "fixtures" / "news_memory_samples.json"


SAMPLE_TEMPLATE = [
    {
        "label": "explicit_like_dislike",
        "chat": "User: Tôi muốn đọc thêm tin về LLM agents, ít hơn về crypto.",
    },
    {
        "label": "style_request",
        "chat": "User: Tin dài quá. Viết ngắn lại cho dễ đọc trên mobile được không?",
    },
    {
        "label": "implicit_positive",
        "chat": "User: Tin về open source hôm qua hay. Có thêm tương tự không?",
    },
    {
        "label": "implicit_negative",
        "chat": "User: Mấy tin showbiz hôm nay không liên quan gì đến mình.",
    },
    {"label": "small_talk", "chat": "User: Cảm ơn nhé. AI: Không có chi."},
]


def _ensure_samples() -> list[dict]:
    if SAMPLES_FILE.exists():
        with SAMPLES_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    SAMPLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES_FILE.write_text(
        json.dumps(SAMPLE_TEMPLATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[eval] Seeded sample file at {SAMPLES_FILE}")
    return SAMPLE_TEMPLATE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Limit to first N samples (0 = all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print rendered prompts, do not call Gemini.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    from app.agents.news.prompts import build_memory_extraction_prompt

    samples = _ensure_samples()
    if args.samples:
        samples = samples[: args.samples]

    if args.dry_run or not os.environ.get("GEMINI_API_KEY"):
        if not args.dry_run:
            print("[eval] No GEMINI_API_KEY in env — falling back to --dry-run mode.")
        for s in samples:
            print(f"\n=== {s['label']} ===")
            print(build_memory_extraction_prompt(s["chat"]))
        return 0

    # Live Gemini path: import client lazily so dry-run never needs it.
    from google import genai
    from google.genai import types

    client = genai.Client()
    model_name = os.environ.get("GEMINI_MODEL", "models/gemini-2.0-flash")

    passes = failures = 0
    for s in samples:
        prompt = build_memory_extraction_prompt(s["chat"])
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = (response.text or "").strip()
            parsed = json.loads(text)
            keys = sorted(parsed.keys())
            ok = keys == ["disliked", "liked", "notes"]
            print(f"\n=== {s['label']} === {'PASS' if ok else 'FAIL'}")
            print(f"  keys: {keys}")
            print(f"  output: {parsed}")
            if ok:
                passes += 1
            else:
                failures += 1
        except Exception as e:
            failures += 1
            print(f"\n=== {s['label']} === ERROR")
            print(f"  {e}")

    print(f"\n[eval] {passes} pass / {failures} fail / {len(samples)} total")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

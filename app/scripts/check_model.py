"""List Gemini models or verify the selected coach model with a small request."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from app.core.gemini_utils import (  # noqa: E402
    DEFAULT_MODEL,
    GEMINI_API_VERSION,
    validate_coach_model,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", nargs="?", const=DEFAULT_MODEL, metavar="MODEL")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    with genai.Client(
        http_options=types.HttpOptions(api_version=GEMINI_API_VERSION, timeout=30000)
    ) as client:  # 30s in ms
        models = [
            m.name
            for m in client.models.list()
            if "generateContent" in (m.supported_actions or [])
        ]
        if args.check:
            validate_coach_model(client, args.check)
            print(f"{args.check}: sinh nội dung thành công ({GEMINI_API_VERSION})")
            return
        now = datetime.now(timezone.utc)
        output = ROOT / f"available_models_{now:%Y-%m-%d}.json"
        output.write_text(
            json.dumps(
                {
                    "fetched_at": now.isoformat().replace("+00:00", "Z"),
                    "models": models,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(*models, sep="\n")
        print(f"Đã lưu {len(models)} model vào {output}")


if __name__ == "__main__":
    main()

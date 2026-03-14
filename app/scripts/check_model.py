import json
import os
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Default output: ../../ from script (project root), filename includes date
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = (SCRIPT_DIR / ".." / "..").resolve()
OUTPUT_FILE = OUTPUT_DIR / f"available_models_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"

print("=== DANH SÁCH MODEL KHẢ DỤNG ===")
try:
    model_list = []
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            model_list.append(m.name)
            print(f"- {m.name}")

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "models": model_list,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nĐã lưu {len(model_list)} model vào: {OUTPUT_FILE}")
except Exception as e:
    print(f"Lỗi API Key: {e}")

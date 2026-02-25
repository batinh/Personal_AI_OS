import json
import os
from dotenv import load_dotenv

# Load environment variables from the root directory
load_dotenv()

CONFIG_PATH = "data/config.json"

def load_config():
    """Load configuration from the central JSON file."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: 
            return {}
    return {}

def save_config(data):
    """Save configuration object to the central JSON file."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

NACE_API_KEY = os.getenv("NACE_API_KEY", "").strip()
NDI_API_KEY = os.getenv("NDI_API_KEY", "").strip() or NACE_API_KEY
NDI_BASE_URL = os.getenv("NDI_BASE_URL", "https://ndi-api.nace.ai").rstrip("/")
NACE_CONSOLE_URL = os.getenv("NACE_CONSOLE_URL", "https://console.nace.ai/v1").rstrip("/")
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "").strip()
DEEPINFRA_BASE_URL = os.getenv("DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai").rstrip("/")

DEFAULT_SYSTEM1_MODEL = "drex-latest"
DEFAULT_SYSTEM2_MODEL = "Qwen/Qwen3.8-27B"

def get_nace_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if NDI_API_KEY:
        headers["X-API-Key"] = NDI_API_KEY
    return headers


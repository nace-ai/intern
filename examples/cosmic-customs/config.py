"""Runtime configuration. Secrets stay in the environment and are never logged."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RULEBOOK_VERSION = "cosmic-customs-rules-1"
PROMPT_VERSION = "cosmic-customs-prompt-1"

NACE_BASE_URL = os.getenv("NACE_BASE_URL", "https://console.nace.ai/v1").rstrip("/")
NACE_DREX_MODEL = os.getenv("NACE_DREX_MODEL", "drex-max-v1.1")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-5.4")
APP_MODE = os.getenv("APP_MODE", "mock").strip().lower() or "mock"

DREX_BUDGET_USD = Decimal(os.getenv("DREX_BUDGET_USD", "1.00"))
NDI_BUDGET_USD = Decimal(os.getenv("NDI_BUDGET_USD", "2.00"))
OPENROUTER_BUDGET_USD = Decimal(os.getenv("OPENROUTER_BUDGET_USD", "3.00"))

# Official inference price captured from
# https://console.nace.ai/docs/drex/reference/pricing on 2026-11-14.
# drex-max-v1.1: $0.00000003 per input token ($0.03 per 1M). Output tokens are not billed.
# GET /v1/models confirms availability but does not publish the rate. The live
# pilot re-checks the pricing page and refuses to price a call if the rate changed.
DREX_INPUT_USD_PER_TOKEN = Decimal("0.00000003")
DREX_PRICING_PROVENANCE = (
    "https://console.nace.ai/docs/drex/reference/pricing "
    "table row drex-max-v1.1, $0.00000003 (30 nUSD) per input token, "
    "$0.03 per 1,000,000 input tokens; output tokens not billed. "
    "Captured 2026-11-14. Browser-run Max pricing is a different product and is not used."
)

DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
STATIC_DIR = ROOT / "static"


def secret_present(name: str) -> bool:
    value = os.getenv(name, "")
    return bool(value and value.strip())


def nace_api_key() -> str | None:
    value = os.getenv("NACE_API_KEY", "").strip()
    return value or None


def openrouter_api_key() -> str | None:
    value = os.getenv("OPENROUTER_API_KEY", "").strip()
    return value or None

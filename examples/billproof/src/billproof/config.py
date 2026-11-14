"""Settings from .env (keys only ever come from .env / the environment; never hard-code them).

MOCK_MODE: auto = real adapter when its key is set, else mock · 1 = force mocks · 0 = require real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PKG_ROOT = Path(__file__).resolve().parents[2]  # billproof/
REPO_ROOT = PKG_ROOT.parent
DATA_DIR = PKG_ROOT / "data"
CMS_DIR = DATA_DIR / "cms"
SYN_DIR = DATA_DIR / "synthetic"
REAL_DIR = DATA_DIR / "real"
RUNS_DIR = DATA_DIR / "runs"
CACHE_DIR = DATA_DIR / "cache"  # gitignored
STATIC_DIR = Path(__file__).resolve().parent / "server" / "static"

load_dotenv(REPO_ROOT / ".env")
load_dotenv(PKG_ROOT / ".env")


def _f(name: str) -> float | None:
    v = os.getenv(name, "").strip()
    return float(v) if v else None


@dataclass(frozen=True)
class Settings:
    nace_api_key: str
    nace_base_url: str
    drex_model: str
    ndi_api_key: str
    ndi_base_url: str
    frontier_base_url: str
    frontier_api_key: str
    frontier_model: str
    drex_price_per_mtok: float | None
    frontier_price_in: float | None
    frontier_price_out: float | None
    mock_mode: str  # "auto" | "1" | "0"

    def use_real(self, service: str) -> bool:
        """service in {"drex", "ndi", "frontier"}. False means: use the mock/simulator."""
        if self.mock_mode == "1":
            return False
        has_key = {
            "drex": bool(self.nace_api_key),
            "ndi": bool(self.ndi_api_key or self.nace_api_key),
            "frontier": bool(self.frontier_api_key and self.frontier_base_url and self.frontier_model),
        }[service]
        if self.mock_mode == "0" and not has_key:
            raise RuntimeError(f"MOCK_MODE=0 but no credentials for {service} in .env")
        return has_key

    def mode_label(self, service: str) -> str:
        return "LIVE" if self.use_real(service) else "SIMULATED"


@lru_cache(maxsize=1)
def settings() -> Settings:
    g = lambda k, d="": os.getenv(k, d).strip()  # noqa: E731
    return Settings(
        nace_api_key=g("NACE_API_KEY"),
        nace_base_url=g("NACE_BASE_URL", "https://console.nace.ai").rstrip("/"),
        drex_model=g("DREX_MODEL", "drex-max-finance"),
        ndi_api_key=g("NDI_API_KEY"),
        ndi_base_url=g("NDI_BASE_URL", "https://ndi-api.nace.ai/api/v1").rstrip("/"),
        frontier_base_url=g("FRONTIER_BASE_URL").rstrip("/"),
        frontier_api_key=g("FRONTIER_API_KEY"),
        frontier_model=g("FRONTIER_MODEL"),
        drex_price_per_mtok=_f("DREX_PRICE_PER_MTOK"),
        frontier_price_in=_f("FRONTIER_PRICE_PER_MTOK_INPUT"),
        frontier_price_out=_f("FRONTIER_PRICE_PER_MTOK_OUTPUT"),
        mock_mode=g("MOCK_MODE", "auto") or "auto",
    )

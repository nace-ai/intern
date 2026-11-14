"""Runtime configuration for Builder B's service.

Secrets come only from the process environment or a local, git-ignored `.env`
file at the repository root. Nothing here prints or returns the key itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# .env is listed in .gitignore; load it if present without overriding real env vars.
load_dotenv(REPO_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    decision_model: str
    drex_timeout_seconds: float
    evidence_cache_dir: Path
    evidence_manifest: Path

    @property
    def nace_configured(self) -> bool:
        # The nace SDK also accepts the legacy DREX_API_KEY name.
        key = os.environ.get("NACE_API_KEY") or os.environ.get("DREX_API_KEY") or ""
        return key.strip().startswith("nace_sk_")


def get_settings() -> Settings:
    return Settings(
        decision_model=os.environ.get("NEARMISS_DECISION_MODEL", "drex-latest"),
        drex_timeout_seconds=float(os.environ.get("NEARMISS_DREX_TIMEOUT", "60")),
        evidence_cache_dir=Path(
            os.environ.get("NEARMISS_EVIDENCE_CACHE", REPO_ROOT / "evidence" / "cache")
        ),
        evidence_manifest=REPO_ROOT / "evidence" / "sources.json",
    )

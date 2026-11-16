"""Paths and runtime settings. Keys are read from the environment only."""

from __future__ import annotations

import os
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
DATA: Path = ROOT / "backend" / "data"
DATASET: Path = ROOT / "dataset" / "data"
NDI_CACHE: Path = DATA / "ndi_cache"
USAGE_FILE: Path = DATA / "usage.json"
TRUSTED_FILE: Path = DATA / "trusted.json"

DREX_MODEL: str = os.environ.get("DREX_MODEL", "drex-max-finance")
CONCURRENCY: int = 8
STUB: bool = os.environ.get("QUORUM_STUB", "").strip().lower() in {"1", "true", "yes"}

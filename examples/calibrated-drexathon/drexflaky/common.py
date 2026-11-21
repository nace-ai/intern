"""Shared paths, env loading, cost table and helpers."""
from __future__ import annotations

import csv
import json
import os
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = DATA / "logs"
RESULTS = ROOT / "results"
CHARTS = RESULTS / "charts"

ACTIONS = ("QUARANTINE", "RETRY", "BLOCK")
DEFAULT_LO, DEFAULT_HI = 0.40, 0.80

QUESTION = "Is this failure caused by test flakiness rather than a real code regression?"


def load_env() -> None:
    """Load /workspace/drex-flaky/.env if present (never printed)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass


def has_key(name: str) -> bool:
    load_env()
    v = os.environ.get(name, "").strip()
    if not v or "..." in v:
        return False
    if name == "NACE_API_KEY" and not v.startswith("nace_sk_"):
        return False  # malformed key -> stay in mock mode
    return True


def resolve_mode(args_mock: bool, key: str) -> str:
    """'mock' if forced or the key is missing, else 'real'."""
    if args_mock:
        return "mock"
    return "real" if has_key(key) else "mock"


def load_labels() -> list[dict]:
    with open(DATA / "labels.csv") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["ambiguous"] = r["ambiguous"] == "1"
        r["feedback_set"] = r["feedback_set"] == "1"
    return rows


def load_costs() -> dict[tuple[str, str], float]:
    """(truth, action) -> cost in engineer-minutes."""
    out = {}
    with open(DATA / "cost_table.csv") as f:
        for r in csv.DictReader(f):
            out[(r["truth"], r["action"])] = float(r["cost_minutes"])
    return out


def read_json(path: Path):
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def result_path(stage: str, mode: str) -> Path:
    return RESULTS / f"{stage}_{mode}.json"


def p50(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def banner(mode: str) -> str:
    return "REAL API RESULTS" if mode == "real" else "MOCK DATA - NOT REAL API OUTPUT"

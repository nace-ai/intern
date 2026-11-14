"""List-price router accounting for the later Hard subset run.

The first run stored cost_usd at $2.50 / $15 per 1M. This module recomputes
from tokens at the list rate, $2 in and $10 out per 1M. It does not change
the frozen caps in policy.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from billproof.config import RUNS_DIR

LIST_IN_PER_MTOK = 2.0
LIST_OUT_PER_MTOK = 10.0
NIGHT_CAP_USD = 30.0
SUBSET_GPT_CAP_USD = 28.0

# Every E router file. Spend sums every row, including retries.
ROUTER_FILES = (
    "hard_dev_gpt.jsonl",
    "hard_dev_hybrid.jsonl",
    "hard_test_gpt.jsonl",
    "hard_test_gpt-one.jsonl",
    "hard_test_gpt_subset.jsonl",
    "hard_test_hybrid_subset.jsonl",
    "hard_dev_family_hybrid.jsonl",
    "hard_test_family_hybrid.jsonl",
)
SUBSET_GPT_FILE = "hard_test_gpt_subset.jsonl"


def list_cost(input_tokens: int, output_tokens: int) -> float:
    return (int(input_tokens) * LIST_IN_PER_MTOK + int(output_tokens) * LIST_OUT_PER_MTOK) / 1_000_000


def row_list_cost(row: dict) -> float | None:
    if row.get("input_tokens") is None or row.get("output_tokens") is None:
        return None
    return list_cost(int(row["input_tokens"]), int(row["output_tokens"]))


def _rows(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def summed(names: tuple[str, ...]) -> tuple[float, list[str]]:
    """List-price sum. Missing token fields are reported, not treated as zero."""
    total = 0.0
    missing = []
    for name in names:
        for row in _rows(RUNS_DIR / name):
            cost = row_list_cost(row)
            if cost is None:
                missing.append(f"{name}:{row.get('case_id')}")
                continue
            total += cost
    return total, missing

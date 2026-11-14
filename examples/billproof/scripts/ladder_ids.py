#!/usr/bin/env python3
"""Freeze ladder subsets before any model call. Refuses to overwrite an id file."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"
HARD_CASES = ROOT / "data" / "synthetic" / "hard" / "test.jsonl"
PA_PANEL = RUNS / "pa_panel_dev370.json"
PERSIUS_IDS = RUNS / "c_persius_test_ids.json"

HARD_OUT = RUNS / "ladder_hard_ids.json"
PA_OUT = RUNS / "ladder_pa_ids.json"
PERSIUS_OUT = RUNS / "ladder_persius_ids.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _write_new(path: Path, payload: dict) -> None:
    if path.exists():
        raise SystemExit(f"refuse to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path} n={payload['n']}")


def freeze_hard() -> None:
    ids = []
    for line in HARD_CASES.read_text().splitlines():
        if line.strip():
            ids.append(json.loads(line)["case_id"])
    if len(ids) != 300:
        raise SystemExit(f"hard test has {len(ids)} cases, want 300")
    if ids[0] != "HARD-TEST-0001" or ids[-1] != "HARD-TEST-0300":
        raise SystemExit("hard test ids are not HARD-TEST-0001..0300 in file order")
    ranked = sorted(ids, key=lambda case_id: _md5(f"hard-gpt100|20261114|{case_id}"))
    chosen = ranked[:100]
    _write_new(HARD_OUT, {
        "bench": "hard",
        "split": "test",
        "n": 100,
        "population": 300,
        "seed": "hard-gpt100|20261114|{case_id}",
        "rank": "md5 hex ascending, first 100",
        "chosen_before_any_ladder_call": True,
        "run_order": chosen,
        "frozen_at": _now(),
        "note": "Not chosen by any model outcome. GPT-6.1 Sol rows that exist are scored only where they intersect this list.",
    })


def freeze_pa() -> None:
    panel = json.loads(PA_PANEL.read_text())
    if not isinstance(panel, list) or len(panel) != 370:
        raise SystemExit(f"pa panel has {len(panel) if isinstance(panel, list) else 'non-list'} ids, want 370")
    ranked = sorted(panel, key=lambda case_id: _md5(f"pa-ladder|0|{case_id}"))
    chosen = ranked[:100]
    _write_new(PA_OUT, {
        "bench": "criteria-gym",
        "split": "development",
        "panel": "data/runs/pa_panel_dev370.json",
        "n": 100,
        "population": 370,
        "seed": "pa-ladder|0|{case_id}",
        "rank": "md5 hex ascending, first 100 of the frozen 370",
        "chosen_before_any_ladder_call": True,
        "run_order": chosen,
        "frozen_at": _now(),
        "note": "Subset of frozen panel. Not chosen by any model outcome.",
    })


def freeze_persius() -> None:
    document = json.loads(PERSIUS_IDS.read_text())
    items = document["items"]
    if len(items) != 400:
        raise SystemExit(f"persius test ids have {len(items)} items, want 400")
    by_gold: dict[str, list[dict]] = {"yes": [], "no": []}
    for item in items:
        gold = item["gold"]
        if gold not in by_gold:
            raise SystemExit(f"unexpected gold {gold}")
        by_gold[gold].append(item)
    if len(by_gold["yes"]) != 200 or len(by_gold["no"]) != 200:
        raise SystemExit("persius classes are not 200/200")
    picked = []
    for gold in ("yes", "no"):
        ordered = sorted(by_gold[gold], key=lambda item: item["rank_md5"])
        picked.extend(ordered[:100])
    picked.sort(key=lambda item: item["rank_md5"])
    slim = [
        {
            "case_id": item["case_id"],
            "row_index": item["row_index"],
            "gold": item["gold"],
            "text_sha256": item["text_sha256"],
            "rank_md5": item["rank_md5"],
        }
        for item in picked
    ]
    _write_new(PERSIUS_OUT, {
        "bench": "persius",
        "split": "test",
        "source": "data/runs/c_persius_test_ids.json",
        "n": 200,
        "population": 400,
        "per_class": 100,
        "rank": "within each frozen gold class, rank_md5 ascending, first 100; run_order is rank_md5 over those 200",
        "chosen_before_any_ladder_call": True,
        "run_order": slim,
        "frozen_at": _now(),
        "note": "Prefix of frozen stratified test sample. Gold is the label used to keep the classes balanced, not a model outcome.",
    })


def main() -> None:
    freeze_hard()
    freeze_pa()
    freeze_persius()


if __name__ == "__main__":
    main()

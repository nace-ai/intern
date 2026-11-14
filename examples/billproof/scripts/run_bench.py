#!/usr/bin/env python3
"""Run a BillProof simulated or live benchmark and print compact lane results."""

from __future__ import annotations

import argparse

from billproof.bench.runner import run_bench
from billproof.config import RUNS_DIR


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _num(value, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", required=True, choices=("synthetic", "imr"))
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("--lanes", default="frontier,drex,hybrid")
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--assume-human-correct", action="store_true",
                        help="score human-routed hybrid questions as correct; report strict and assumed scores")
    args = parser.parse_args()
    run_id = run_bench(args.bench, args.split, args.lanes, run_id=args.run_id,
                       limit=args.limit, seed=args.seed,
                       assume_human_correct=args.assume_human_correct)
    report = __import__("json").loads((RUNS_DIR / f"{run_id}.report.json").read_text())
    meta = report.get("meta", {})
    print(f"{args.bench} / {args.split} · {meta.get('mode', 'SIMULATED')} · run {run_id}")
    print(f"{'lane':<12} {'accuracy':>10} {'case pass':>10} {'escalation':>10} {'p50 ms':>10} {'cost/case':>12} {'n':>6}")
    lanes = report.get("lanes", {})
    for lane in args.lanes.split(","):
        row = lanes.get(lane.strip(), {})
        cost = row.get("cost_per_case_usd")
        cost_text = "n/a" if cost is None else f"${cost:.4f}"
        print(f"{lane.strip():<12} {_pct(row.get('accuracy')):>10} "
              f"{_pct(row.get('case_pass')):>10} {_pct(row.get('escalation_rate')):>10} "
              f"{_num(row.get('p50_ms')):>10} {cost_text:>12} {row.get('n', 0):>6}")


if __name__ == "__main__":
    main()

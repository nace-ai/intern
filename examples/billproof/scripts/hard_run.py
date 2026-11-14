#!/usr/bin/env python3
"""Detached, resumable BillProof-Hard lane. Stops at the pre-registered spend cap."""
import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.adapters.drex import ModelMismatch
from billproof.bench.hard.policy import (
    DREX_MODELS, GPT_DEV_CAP_USD, GPT_HYBRID_CAP_USD, GPT_ONE_CAP_USD, GPT_TEST_CAP_USD,
    NACE_CAP_USD,
)
from billproof.bench.hard.runner import (
    allow, append_row, call_gpt_one, call_hybrid, call_lane, done_ids, drex_adapter,
    gpt_adapter, jsonl_path, load_cases, now, spend,
)
from billproof.config import DATA_DIR


def _cap(split: str, lane: str) -> float:
    if lane in DREX_MODELS or lane == "drex-both":
        return NACE_CAP_USD
    if lane == "gpt":
        return GPT_DEV_CAP_USD if split == "dev" else GPT_TEST_CAP_USD
    if lane == "hybrid":
        return GPT_HYBRID_CAP_USD if split == "dev" else GPT_TEST_CAP_USD
    if lane == "gpt-one":
        return GPT_ONE_CAP_USD
    raise SystemExit(f"unknown lane {lane}")


def _cases(split: str, limit: int | None) -> list[dict]:
    path = DATA_DIR / "synthetic" / "hard" / f"{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"missing {path}; run scripts/hard_generate.py --split {split}")
    rows = load_cases(path)
    if split == "test":
        manifest = json.loads((DATA_DIR / "synthetic" / "hard" / "manifest.json").read_text())
        if not manifest.get("frozen"):
            raise SystemExit("test split is not frozen")
    if lane_limit := limit:
        rows = rows[:lane_limit]
    return rows


def run_one(record, lane, adapters) -> dict:
    if lane == "gpt":
        return call_lane(record, "gpt", adapters["gpt"])
    if lane == "gpt-one":
        return call_gpt_one(record, adapters["gpt"])
    if lane == "hybrid":
        tau = adapters["tau"]
        row = call_hybrid(record, adapters["gpt"], tau)
        if row is None:
            raise RuntimeError("drex-v1 row missing; run that lane first")
        return row
    return call_lane(record, lane, adapters[lane])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--lane", required=True,
                        choices=("drex-v1", "drex-finance", "drex-both", "gpt", "hybrid", "gpt-one"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--tau", type=float, default=None, help="Hybrid cutoff. Dev defaults to the ceiling.")
    args = parser.parse_args()
    if args.lane == "gpt-one" and args.split != "test":
        raise SystemExit("gpt-one is the test-split packing reference")
    cases = _cases(args.split, args.limit or None)
    lanes = ("drex-v1", "drex-finance") if args.lane == "drex-both" else (args.lane,)
    from billproof.bench.hard.policy import HYBRID_TAU_CEILING
    tau = args.tau if args.tau is not None else HYBRID_TAU_CEILING
    if args.split == "test" and "hybrid" in lanes:
        manifest = json.loads((DATA_DIR / "synthetic" / "hard" / "manifest.json").read_text())
        if "hybrid_tau" not in manifest:
            raise SystemExit("manifest has no hybrid_tau; freeze dev before the test hybrid")
        tau = float(manifest["hybrid_tau"])
    adapters = {"tau": tau}
    if any(lane in ("gpt", "hybrid", "gpt-one") for lane in lanes):
        adapters["gpt"] = gpt_adapter()
    for lane in lanes:
        if lane in DREX_MODELS:
            adapters[lane] = drex_adapter(DREX_MODELS[lane])
    print(f"start {args.split} {args.lane} n={len(cases)} tau={tau} {now()}", flush=True)
    # drex-both alternates models per case so a Nace cap cuts both lanes on the same prefix.
    work: list[tuple[str, dict]] = []
    if args.lane == "drex-both":
        for record in cases:
            for lane in lanes:
                work.append((lane, record))
    else:
        for lane in lanes:
            for record in cases:
                work.append((lane, record))
    finished = {lane: done_ids(jsonl_path(args.split, lane)) for lane in lanes}
    for lane, record in work:
        if record["case_id"] in finished[lane]:
            continue
        if lane == "gpt-one" and record["index"] >= 20:
            continue
        cap = _cap(args.split, lane)
        ok, reason = allow(lane, cap)
        if not ok:
            print(f"STOP budget {lane}: {reason}", flush=True)
            return
        path = jsonl_path(args.split, lane)
        try:
            row = run_one(record, lane, adapters)
        except ModelMismatch:
            raise
        except Exception as exc:  # noqa: BLE001 — record and keep the resume file moving
            row = {
                "run_id": f"hard_{args.split}_{lane}", "bench": "hard", "case_id": record["case_id"],
                "split": args.split, "lane": lane, "model": DREX_MODELS.get(lane, lane),
                "simulated": False, "error": f"{type(exc).__name__}: {exc}",
                "cost_usd": 0, "latency_ms": 0, "input_tokens": 0, "output_tokens": 0,
                "gold": record["gold"], "answers": {},
                "correct": {name: False for name in record["gold"]},
                "invalid": {name: True for name in record["gold"]},
                "case_correct": False, "n_questions": len(record["gold"]), "n_correct": 0,
                "n_invalid": len(record["gold"]), "ts": now(),
                "trace": traceback.format_exc(limit=4),
            }
        append_row(path, row)
        finished[lane].add(record["case_id"])
        router, nace = spend()
        acc = (row.get("n_correct") or 0) / row["n_questions"] if row.get("n_questions") else 0
        print(
            f"{lane} {row['case_id']} acc={acc:.3f} invalid={row.get('n_invalid')} "
            f"cost={row.get('cost_usd')} latency_ms={row.get('latency_ms')} "
            f"router={router:.4f} nace={nace:.4f}",
            flush=True,
        )
        if row.get("error") or row.get("gpt_error"):
            print(f"ERROR {row['case_id']} {row.get('error') or row.get('gpt_error')}", flush=True)
    print(f"DONE {args.split} {args.lane} {now()}", flush=True)


if __name__ == "__main__":
    main()

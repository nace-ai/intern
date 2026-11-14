#!/usr/bin/env python3
"""Resumable runner for the prior-authorization benchmark .

One JSONL file per (column, split): data/runs/pa_<column>_<split>.jsonl, one row
per case: answers/qa, confidence, tokens, latency, cost, derived prediction and
per-part score. Re-running skips (case_id, column) pairs already present.

Columns: gpt (1) · drex (2) · hybrid (3: Drex->GPT on low confidence) ·
checked (4: GPT checked by Drex) · drexfin (column 2 with drex-max-finance).

Usage (from billproof/):
  PYTHONPATH=src nohup .venv/bin/python scripts/pa_run.py --split validation \
      --columns gpt,drex --sample 100 >> out/logs/pa_run_val.log 2>&1 &
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa import columns as pc
from billproof.bench.pa.cases import load_cases, stratified_sample
from billproof.bench.pa.score import score_prediction
from billproof.config import RUNS_DIR

COLUMNS = ("gpt", "drex", "hybrid", "checked", "drexfin", "gptqa")


OUT_SUFFIX = ""


def _out_path(column: str, split: str) -> Path:
    return RUNS_DIR / f"pa_{column}{OUT_SUFFIX}_{split}.jsonl"


def _done_keys(path: Path) -> set[str]:
    done: set[str] = set()
    if path.exists():
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    if not r.get("error"):
                        done.add(r["case_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _row(case, column: str, result: dict, *, check_evidence: bool) -> dict:
    pred = result.get("prediction")
    score = score_prediction(case, pred, check_evidence=check_evidence) if pred else {
        "whole_case": False, "decision": False, "per_fact": {},
        "n_facts": case.n_facts, "n_facts_right": 0, "error": True,
    }
    return {
        "case_id": case.case_id, "parent_id": case.parent_id, "split": case.split,
        "scenario": case.scenario, "list_id": case.list_id,
        "gold_decision": case.target.get("decision"),
        "column": column, "model": result.get("model"),
        "prediction": pred, "facts": result.get("facts"),
        "qa": result.get("qa"), "escalated": result.get("escalated"),
        "flagged": result.get("flagged"), "overrides": result.get("overrides"),
        "score": score,
        "latency_ms": result.get("latency_ms"), "calls": result.get("calls"),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "cost_usd": result.get("cost_usd"),
        "error": result.get("error"),
        "simulated": False,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split", required=True, choices=("validation", "development"))
    p.add_argument("--columns", default="gpt,drex,hybrid,checked")
    p.add_argument("--limit", type=int, default=None, help="first N selected cases")
    p.add_argument("--sample", type=int, default=None, help="stratified sample size")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--cases-file", default=None, help="JSON list of case_ids to run")
    p.add_argument("--drex-model", default=None, help="Drex model for the drex column")
    p.add_argument("--cutoff3", type=float, default=0.75, help="col3 escalation cutoff")
    p.add_argument("--cutoff4", type=float, default=0.75, help="col4 Drex-override cutoff")
    p.add_argument("--stages", default="initial_packet",
                   help="comma list, e.g. initial_packet or supplement_received")
    p.add_argument("--out-suffix", default="", help="append to output filenames")
    args = p.parse_args()

    wanted = [c.strip() for c in args.columns.split(",") if c.strip()]
    for c in wanted:
        if c not in COLUMNS:
            p.error(f"unknown column {c!r}; choose from {COLUMNS}")
    global OUT_SUFFIX
    OUT_SUFFIX = args.out_suffix

    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    cases = load_cases(args.split, stages=stages)
    if args.cases_file:
        keep = set(json.loads(Path(args.cases_file).read_text()))
        cases = [c for c in cases if c.case_id in keep]
    elif args.sample:
        cases = stratified_sample(cases, args.sample, seed=args.seed)
    if args.limit:
        cases = cases[: args.limit]

    print(f"[pa_run] split={args.split} n={len(cases)} columns={wanted} "
          f"cutoff3={args.cutoff3} cutoff4={args.cutoff4} drex_model={args.drex_model}",
          flush=True)

    paths = {c: _out_path(c, args.split) for c in wanted}
    done = {c: _done_keys(paths[c]) for c in wanted}
    print(f"[pa_run] already done: { {c: len(d) for c, d in done.items()} }", flush=True)

    n_ok = n_err = 0
    for i, case in enumerate(cases):
        todo = [c for c in wanted if case.case_id not in done[c]]
        if not todo:
            continue
        try:
            gpt_out = drex_out = None
            if set(todo) & {"gpt", "checked"}:
                if "gpt" in todo:
                    gpt_out = pc.gpt_structured(case)
                else:
                    # checked without a fresh gpt run: reuse the stored row;
                    # only pay for a completion if none exists.
                    gpt_out = _load_prediction(RUNS_DIR / f"pa_gpt_{args.split}.jsonl",
                                               case.case_id)
                    if gpt_out.get("prediction") is None:
                        gpt_out = pc.gpt_structured(case)
            if set(todo) & {"drex", "hybrid", "checked"}:
                if "drex" in todo:
                    drex_out = pc.drex_column(case, model=args.drex_model)
                else:
                    drex_out = _load_drex(RUNS_DIR / f"pa_drex_{args.split}.jsonl",
                                          case.case_id)
                    if not drex_out:
                        drex_out = pc.drex_column(case, model=args.drex_model)
            for column in todo:
                if column == "gpt":
                    row = _row(case, "gpt", gpt_out, check_evidence=True)
                elif column == "drex":
                    row = _row(case, "drex", drex_out, check_evidence=False)
                elif column == "drexfin":
                    row = _row(case, "drexfin",
                               pc.drex_column(case, model="drex-max-finance"),
                               check_evidence=False)
                elif column == "gptqa":
                    from billproof.bench.pa.questions import build_questions
                    res = pc.gpt_questions(case, build_questions(case))
                    row = {"case_id": case.case_id, "split": case.split,
                           "column": "gptqa", "qa": res.get("answers"),
                           "latency_ms": res.get("latency_ms"), "calls": res.get("calls"),
                           "input_tokens": res.get("input_tokens"),
                           "output_tokens": res.get("output_tokens"),
                           "cost_usd": res.get("cost_usd"), "error": res.get("error"),
                           "gold_decision": case.target.get("decision"),
                           "scenario": case.scenario,
                           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                elif column == "hybrid":
                    row = _row(case, "hybrid",
                               pc.drex_then_gpt_column(case, drex_out, cutoff=args.cutoff3),
                               check_evidence=False)
                elif column == "checked":
                    row = _row(case, "checked",
                               pc.gpt_checked_column(case, gpt_out, drex_out,
                                                     cutoff=args.cutoff4),
                               check_evidence=True)
                _write(paths[column], row)
                done[column].add(case.case_id)
                n_ok += 1
        except Exception as exc:  # keep the run alive; row skipped, retried next pass
            n_err += 1
            print(f"[pa_run] case {case.case_id} ERROR: {exc}", flush=True)
        if (i + 1) % 10 == 0:
            print(f"[pa_run] {i+1}/{len(cases)} cases · wrote {n_ok} rows · {n_err} errors",
                  flush=True)
    print(f"[pa_run] DONE split={args.split} wrote={n_ok} errors={n_err}", flush=True)


def _load_prediction(path: Path, case_id: str) -> dict:
    # last non-error row wins — error rows may precede a successful retry.
    best: dict | None = None
    if path.exists():
        with open(path) as fh:
            for line in fh:
                row = json.loads(line)
                if row["case_id"] == case_id and not row.get("error"):
                    best = row
    if best is None:
        return {"prediction": None}
    return {"prediction": best.get("prediction"), "latency_ms": best.get("latency_ms"),
            "calls": best.get("calls"), "cost_usd": best.get("cost_usd"),
            "input_tokens": best.get("input_tokens"),
            "output_tokens": best.get("output_tokens"), "error": best.get("error")}


def _load_drex(path: Path, case_id: str) -> dict:
    best: dict | None = None
    if path.exists():
        with open(path) as fh:
            for line in fh:
                row = json.loads(line)
                if row["case_id"] == case_id and not row.get("error"):
                    best = row
    if best is None:
        return {}
    return {"qa": best.get("qa") or {}, "facts": best.get("facts") or {},
            "latency_ms": best.get("latency_ms"), "calls": best.get("calls"),
            "cost_usd": best.get("cost_usd"), "input_tokens": best.get("input_tokens"),
            "output_tokens": best.get("output_tokens")}


if __name__ == "__main__":
    main()

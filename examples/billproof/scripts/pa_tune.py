#!/usr/bin/env python3
"""Tune PA cutoffs on validation rows (offline, no API calls).

Sweeps the col3 escalation cutoff (automation rate vs accuracy, using stored
gptqa answers as the escalation oracle) and the col4 Drex-override cutoff
(flag rate vs resolved accuracy). Also reports calibration of Drex native
probabilities vs GPT self-reported confidences on the same questions.

Prints a table; --json writes data/metrics/pa_tune_<split>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.metrics import brier, ece, wilson_ci
from billproof.bench.pa.cases import load_cases
from billproof.bench.pa.questions import build_questions
from billproof.config import DATA_DIR, RUNS_DIR
from pa_derive import checked_pred, hybrid_pred  # noqa: E402  (script dir import)


def _load(path: Path) -> dict[str, dict]:
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                rows[r["case_id"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return rows


def _fact_gold(case):
    return {f: (v if v is None else bool(v)) for f, v in case.gold_facts().items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="validation")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    split = args.split

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    cases = {c.case_id: c for c in load_cases(split)}
    drex = _load(RUNS_DIR / f"pa_drex_{split}.jsonl")
    gpt = _load(RUNS_DIR / f"pa_gpt_{split}.jsonl")
    gptqa = _load(RUNS_DIR / f"pa_gptqa_{split}.jsonl")
    have = [cid for cid in cases if cid in drex and drex[cid].get("qa")]
    print(f"[pa_tune] cases with drex rows: {len(have)}; gpt: {len(gpt)}; gptqa: {len(gptqa)}")

    # ---- calibration: per-fact confidence vs correctness
    def calib_rows(src, key):
        confs, corr = [], []
        for cid in have:
            row = src.get(cid)
            if not row:
                continue
            case = cases[cid]
            gold = _fact_gold(case)
            for qn, qa in (row.get(key) or {}).items():
                if not qn.startswith("fact::") or not isinstance(qa, dict):
                    continue
                fact = qn.split("::", 1)[1]
                if fact not in gold:
                    continue
                from billproof.bench.pa.score import _norm_fact
                pred = _norm_fact(qa.get("choice"))
                confs.append(float(qa.get("confidence") or 0.0))
                corr.append(pred == gold[fact])
        return confs, corr

    dconf, dcorr = calib_rows(drex, "qa")
    gconf, gcorr = calib_rows(gptqa, "qa")
    for label, confs, corr in (("drex", dconf, dcorr), ("gpt-selfrep", gconf, gcorr)):
        if confs:
            e, _ = ece(confs, corr, bins=8)
            br = brier([{True: c, False: 1 - c} for c in confs], corr)
            print(f"[calib] {label:12s} n={len(confs)} acc={sum(corr)/len(corr):.3f} "
                  f"meanconf={sum(confs)/len(confs):.3f} brier={br:.4f} ece={e:.4f}")

    # ---- col3 sweep: cutoff -> {escalation rate, whole-case, decision}
    print("\n[col3 sweep: Drex->GPT escalation]")
    print(" cutoff  auto_rate  whole_case        decision   (n=%d)" % len(have))
    grid = [0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.975, 0.99, 0.995, 0.999, 1.0]
    sweep3 = []
    for tau in grid:
        n_esc = right_w = right_d = nn = 0
        for cid in have:
            case = cases[cid]
            gq = (gptqa.get(cid) or {}).get("qa")
            pred, esc = hybrid_pred(case, drex[cid]["qa"], gq, tau)
            n_esc += len(esc)
            nn += 1
            s = __import__("billproof.bench.pa.score", fromlist=["score_prediction"])\
                .score_prediction(case, pred, check_evidence=False)
            right_w += s["whole_case"]
            right_d += s["decision"]
        nq = sum(len(build_questions(cases[cid])) for cid in have)
        sweep3.append({"cutoff": tau, "escalation_rate": n_esc / max(nq, 1),
                       "cases_with_escalation": n_esc, "whole_case": right_w / nn,
                       "decision": right_d / nn})
        lo, hi = wilson_ci(right_w, nn)
        print(f"  {tau:.3f}   {n_esc/nq:7.3f}   {right_w/nn:.3f} [{lo:.3f},{hi:.3f}]"
              f"   {right_d/nn:.3f}")

    # ---- col4 sweep: cutoff -> {override rate, flag rate, whole-case}
    print("\n[col4 sweep: GPT checked by Drex]")
    print(" cutoff  overrides  flagged_cases  whole_case")
    sweep4 = []
    for tau in grid:
        n_ov = n_fl = right_w = nn = 0
        for cid in have:
            row = gpt.get(cid)
            case = cases[cid]
            if not row or not row.get("prediction"):
                continue
            pred, flagged, overrides = checked_pred(case, row["prediction"],
                                                    drex[cid]["qa"], tau)
            nn += 1
            n_ov += len(overrides)
            n_fl += bool(flagged)
            from billproof.bench.pa.score import score_prediction
            right_w += score_prediction(case, pred, check_evidence=True)["whole_case"]
        sweep4.append({"cutoff": tau, "overrides": n_ov, "flagged_cases": n_fl,
                       "whole_case": right_w / nn if nn else None, "n": nn})
        print(f"  {tau:.3f}   {n_ov:5d}      {n_fl:4d}          {right_w/nn:.3f}" if nn else f"  {tau:.3f}   n/a")

    if args.json:
        out = {
            "n_cases": len(have),
            "calibration": {
                "drex": {"n": len(dconf), "acc": sum(dcorr) / max(len(dcorr), 1),
                         "mean_conf": sum(dconf) / max(len(dconf), 1),
                         "ece": ece(dconf, dcorr, bins=8)[0] if dconf else None,
                         "brier": brier([{True: c, False: 1 - c} for c in dconf], dcorr) if dconf else None},
                "gpt_selfrep": {"n": len(gconf), "acc": sum(gcorr) / max(len(gcorr), 1),
                                "mean_conf": sum(gconf) / max(len(gconf), 1),
                                "ece": ece(gconf, gcorr, bins=8)[0] if gconf else None,
                                "brier": brier([{True: c, False: 1 - c} for c in gconf], gcorr) if gconf else None},
            },
            "col3_sweep": sweep3,
            "col4_sweep": sweep4,
        }
        path = DATA_DIR / "metrics" / f"pa_tune_{split}.json"
        path.write_text(json.dumps(out, indent=1))
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()

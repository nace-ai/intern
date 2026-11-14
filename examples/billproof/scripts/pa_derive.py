#!/usr/bin/env python3
"""Re-derive hybrid (col3) and checked (col4) rows at arbitrary cutoffs, offline.

Inputs: pa_gpt_<split>.jsonl (structured answers), pa_drex_<split>.jsonl (typed
answers + native confidences), and optionally pa_gptqa_<split>.jsonl (GPT's fresh
answer to EVERY typed question — the tuning stand-in for escalation calls).

Outputs rows to pa_<column>[_<suffix>]_<split>.jsonl — never overwrite live rows.

  col3 emulation: facts = drex answer where P>=cutoff else gptqa answer; then the
  deterministic decision code. (At test time real subset-escalation calls run
  instead — see pa_run.py.)

  col4 emulation: gpt facts overridden by drex where disagree & P>=cutoff, else
  flagged; decision recomputed by code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import load_cases
from billproof.bench.pa.logic import decide
from billproof.bench.pa.questions import DATE_Q, PAYER_Q, build_questions
from billproof.bench.pa.score import INVALID, _norm_fact, facts_from_answers, score_prediction
from billproof.config import RUNS_DIR


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


def _scope_from_qa(qa: dict) -> str:
    if (qa.get(PAYER_Q) or {}).get("choice") == "no":
        return "wrong_payer"
    if (qa.get(DATE_Q) or {}).get("choice") == "no":
        return "wrong_date"
    return "applicable"


def hybrid_pred(case, drex_qa: dict, gpt_qa: dict | None, cutoff: float):
    """Merged facts under a cutoff. gpt_qa maps qname -> {choice, confidence}."""
    escalated = []
    answers = {}
    questions = build_questions(case)
    for q in questions:
        dq = drex_qa.get(q.name) or {}
        conf = float(dq.get("confidence") or 0.0)
        if conf >= cutoff or gpt_qa is None:
            answers[q.name] = dq.get("choice")
        else:
            gq = gpt_qa.get(q.name) or {}
            answers[q.name] = gq.get("choice") or dq.get("choice")
            escalated.append(q.name)
    facts = facts_from_answers(answers, case.requested_facts)
    scope = _scope_from_qa({k: {"choice": v} for k, v in answers.items()})
    derived = decide(facts, case.criterion_deps, scope)
    pred = {"policy_id": case.policy.get("id"), "scope": scope,
            "branch": case.policy.get("branch", "unspecified"), "facts": facts,
            "criteria": {n: {"state": s, "evidence": []} for n, s in derived["criteria"].items()},
            "missing_info": derived["missing_info"], "decision": derived["decision"],
            "action": derived["action"]}
    return pred, escalated


def checked_pred(case, gpt_pred: dict, drex_qa: dict, cutoff: float):
    gpt_facts = {f: _norm_fact(v) for f, v in (gpt_pred.get("facts") or {}).items()}
    resolved, flagged, overrides = {}, [], []
    for fact in case.requested_facts:
        g = gpt_facts.get(fact)
        dq = drex_qa.get(f"fact::{fact}") or {}
        d = _norm_fact(dq.get("choice"))
        if d is INVALID:
            d = None
        conf = float(dq.get("confidence") or 0.0)
        if d == g:
            resolved[fact] = g
        elif conf >= cutoff:
            resolved[fact] = d
            overrides.append({"fact": fact, "gpt": g, "drex": d, "conf": conf})
        else:
            resolved[fact] = g
            flagged.append({"fact": fact, "gpt": g, "drex": d, "conf": conf})
    scope = gpt_pred.get("scope")
    dscope = _scope_from_qa(drex_qa)
    if dscope != scope:
        implied = {"wrong_payer": {PAYER_Q: "no"}, "wrong_date": {DATE_Q: "no"},
                   "applicable": {PAYER_Q: "yes", DATE_Q: "yes"}}.get(scope) or {}
        disagree_confs = [
            float((drex_qa.get(q) or {}).get("confidence") or 0.0)
            for q, want in implied.items()
            if (drex_qa.get(q) or {}).get("choice") != want
        ]
        if disagree_confs and min(disagree_confs) >= cutoff:
            scope = dscope
            overrides.append({"fact": "scope", "gpt": gpt_pred.get("scope"), "drex": dscope})
        else:
            flagged.append({"fact": "scope", "gpt": gpt_pred.get("scope"), "drex": dscope})
    derived = decide(resolved, case.criterion_deps, scope)
    criteria = {}
    for name, s in derived["criteria"].items():
        gpt_item = (gpt_pred.get("criteria") or {}).get(name) or {}
        criteria[name] = {"state": s, "evidence": gpt_item.get("evidence") or []}
    pred = {"policy_id": case.policy.get("id") or gpt_pred.get("policy_id"), "scope": scope,
            "branch": gpt_pred.get("branch", "unspecified"), "facts": resolved,
            "criteria": criteria, "missing_info": derived["missing_info"],
            "decision": derived["decision"], "action": derived["action"]}
    return pred, flagged, overrides


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", required=True)
    ap.add_argument("--column", required=True, choices=("hybrid", "checked"))
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--suffix", default="_derived",
                    help="output file suffix, e.g. _t075 (default _derived; NEVER use '' — that would append into live column files)")
    ap.add_argument("--drex-file", default=None)
    ap.add_argument("--gpt-file", default=None)
    ap.add_argument("--gptqa-file", default=None)
    args = ap.parse_args()

    split = args.split
    cases = {c.case_id: c for c in load_cases(split)}
    drex = _load(Path(args.drex_file) if args.drex_file else RUNS_DIR / f"pa_drex_{split}.jsonl")
    gpt = _load(Path(args.gpt_file) if args.gpt_file else RUNS_DIR / f"pa_gpt_{split}.jsonl")
    gptqa = _load(Path(args.gptqa_file) if args.gptqa_file else RUNS_DIR / f"pa_gptqa_{split}.jsonl")

    out_path = RUNS_DIR / f"pa_{args.column}{args.suffix}_{split}.jsonl"
    n = 0
    for case_id, case in cases.items():
        drow = drex.get(case_id)
        if not drow or not drow.get("qa"):
            continue
        if args.column == "hybrid":
            gq = gptqa.get(case_id)
            gq_map = (gq or {}).get("qa")
            pred, esc = hybrid_pred(case, drow["qa"], gq_map, args.cutoff)
            score = score_prediction(case, pred, check_evidence=False)
            extra = {"escalated": esc, "via": "offline_tuning" if gq_map else "drex_only"}
        else:
            grow = gpt.get(case_id)
            if not grow or not grow.get("prediction"):
                continue
            pred, flagged, overrides = checked_pred(case, grow["prediction"], drow["qa"], args.cutoff)
            score = score_prediction(case, pred, check_evidence=True)
            extra = {"flagged": flagged, "overrides": overrides, "via": "offline"}
        row = {"case_id": case_id, "split": split, "column": args.column,
               "cutoff": args.cutoff, "prediction": pred, "score": score,
               "gold_decision": case.target.get("decision"), "scenario": case.scenario,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra}
        with open(out_path, "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        n += 1
    print(f"[pa_derive] {args.column} cutoff={args.cutoff} wrote {n} rows -> {out_path}")


if __name__ == "__main__":
    main()

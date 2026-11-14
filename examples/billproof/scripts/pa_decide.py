#!/usr/bin/env python3
"""Smart use: "what decides this case" via Drex record ablation.

For each case: baseline Drex fact answers; then for every record R, re-ask the
same fact questions with R removed from the state (scope questions skipped —
records can't change payer/date). A record whose removal flips the derived
decision is THE DECIDING RECORD. If none flips, predicted = "none".

Gold deciding set, from construction.ids (fact -> designed record) + target:
  criteria_met   -> records of true facts (any single removal flips to incomplete)
  criteria_unmet -> records of false facts whose individual removal lifts unmet
                    (one false fact: its record; two+: "none" gold — joint)
  incomplete     -> "none" is correct (the deciding item is an ABSENT record;
                    a removal that creates a new missing fact also counts as a
                    hit on that fact's record)
  not_applicable -> "none" is correct (scope, not records, decides)

Output: data/runs/pa_decide_<split>.jsonl, resumable. Agreement = predicted
record's construction fact belongs to the gold deciding fact set (or
predicted=="none" when the gold set is empty/absent).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import load_cases, stratified_sample
from billproof.bench.pa.columns import _drex_decide, _prediction_from_facts
from billproof.bench.pa.questions import build_questions
from billproof.bench.pa.score import facts_from_answers
from billproof.config import RUNS_DIR


def _facts_from_result(case, result) -> dict:
    answers = {name: a.choice for name, a in result.answers.items()}
    return facts_from_answers(answers, case.requested_facts)


def gold_deciding(case) -> dict:
    """Return {'kind':..., 'facts': set, 'records': set, 'none_ok': bool}."""
    t = case.target
    ids = case.construction.get("ids") or {}
    cons_facts = case.construction.get("facts") or {}
    decision = t.get("decision")
    if decision == "not_applicable":
        return {"kind": "scope", "facts": set(), "records": set(), "none_ok": True}
    connective = (case.policy.get("connective") or "AND").upper()
    if decision == "criteria_met":
        facts = {f for f, v in cons_facts.items() if v is True}
        # AND: every true fact flips when removed — all are deciding.
        # OR with >1 true: no single removal flips — "none" is correct.
        none_ok = connective == "OR" and len(facts) > 1
        kind = decision if not none_ok else "joint_met"
    elif decision == "criteria_unmet":
        facts = {f for f, v in cons_facts.items() if v is False}
        # AND with >1 false: one removal leaves another unmet — "none" correct.
        # OR: every false fact flips unmet->incomplete — "none" is wrong.
        none_ok = connective == "AND" and len(facts) > 1
        kind = "joint_unmet" if none_ok else decision
    else:  # incomplete: deciding items are absent records
        gold_facts = case.gold_facts()
        facts = {f for f, v in gold_facts.items() if v is None}
        return {"kind": "absent", "facts": facts, "records": set(), "none_ok": True}
    records = {ids[f] for f in facts if f in ids}
    return {"kind": kind, "facts": facts, "records": records, "none_ok": none_ok}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="development")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--drex-model", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cases-file", default=None, help="JSON list of case_ids")
    args = ap.parse_args()

    cases = load_cases(args.split)
    if args.cases_file:
        keep = set(json.loads(Path(args.cases_file).read_text()))
        cases = [c for c in cases if c.case_id in keep]
    else:
        cases = stratified_sample(cases, args.sample, seed=args.seed)
    out_path = Path(args.out) if args.out else RUNS_DIR / f"pa_decide_{args.split}.jsonl"
    done = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            try:
                r = json.loads(line)
                if not r.get("error"):
                    done.add(r["case_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    print(f"[pa_decide] n={len(cases)} done={len(done)} out={out_path}", flush=True)

    for case in cases:
        if case.case_id in done:
            continue
        t0 = time.time()
        questions = [q for q in build_questions(case, include_scope=False)]
        row = {"case_id": case.case_id, "split": args.split, "scenario": case.scenario,
               "gold_decision": case.target.get("decision"), "calls": 0,
               "input_tokens": 0, "cost_usd": 0.0, "removals": []}
        try:
            base = _drex_decide(case, questions, model=args.drex_model)
            row["calls"] += base.calls
            row["input_tokens"] += base.input_tokens
            row["cost_usd"] += base.cost_usd
            base_facts = _facts_from_result(case, base)
            # Scope questions are excluded from the probe (include_scope=False):
            # removing a record cannot change payer/date answers, so the gold
            # scope is the correct scope for every ablation — verified by
            # compute_scope == target.scope on all 3,237 rows.
            gold_scope = case.target.get("scope")
            base_pred_gs = _prediction_from_facts(case, base_facts, gold_scope)
            base_dec = base_pred_gs["decision"]
            row["baseline_decision"] = base_dec
            flips = []
            changes = []
            for rec in case.records:
                rid = rec.get("id")
                st = case.state(drop_records={rid})
                r = _drex_decide(case, questions, model=args.drex_model, state=st)
                row["calls"] += r.calls
                row["input_tokens"] += r.input_tokens
                row["cost_usd"] += r.cost_usd
                facts = _facts_from_result(case, r)
                pred = _prediction_from_facts(case, facts, gold_scope)
                diff_facts = [f for f in case.requested_facts if facts.get(f) != base_facts.get(f)]
                flipped = pred["decision"] != base_dec
                row["removals"].append({"record": rid, "record_fact": rec.get("fact"),
                                       "decision": pred["decision"], "flipped": flipped,
                                       "changed_facts": diff_facts})
                if flipped:
                    flips.append(rid)
                if diff_facts:
                    changes.append((rid, diff_facts))
            # predicted deciding record = first record whose removal flips the decision
            if flips:
                predicted = flips[0]
            else:
                predicted = "none"
            g = gold_deciding(case)
            ids_rev = {v: k for k, v in (case.construction.get("ids") or {}).items()}
            pred_fact = ids_rev.get(predicted)
            if predicted == "none":
                agree = bool(g["none_ok"])
            else:
                agree = predicted in g["records"] or pred_fact in g["facts"]
            row.update(predicted=predicted, predicted_fact=pred_fact,
                       gold_facts=sorted(g["facts"]), gold_records=sorted(g["records"]),
                       gold_kind=g["kind"], agree=agree,
                       elapsed_s=round(time.time() - t0, 1))
        except Exception as exc:
            row["error"] = str(exc)
        with open(out_path, "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        print(f"[pa_decide] {case.case_id} pred={row.get('predicted')} "
              f"agree={row.get('agree')} calls={row['calls']} ${row['cost_usd']:.4f}",
              flush=True)
    print("[pa_decide] DONE", flush=True)


if __name__ == "__main__":
    main()

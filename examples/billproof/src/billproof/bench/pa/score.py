"""Scorer: compare a predicted structured answer (or derived decision) to the gold target.

A prediction is a dict shaped like the dataset target:
  {policy_id, scope, branch, facts {f: true|false|null},
   criteria {c: {state, evidence:[R..]}}, missing_info, decision, action}
Columns 2-4 produce facts/criteria from typed-question answers; evidence is not
asked of them, so evidence correctness is only enforced for column 1 (GPT's
structured answer) via scoring.support_sets / allowed_evidence.
"""

from __future__ import annotations

from typing import Any


INVALID = object()  # unrecognized fact value — never equals any gold value


def _norm_fact(v: Any) -> Any:
    if v is True or v is False or v is None:
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "met", "documented_satisfied", "satisfied"):
            return True
        if s in ("false", "no", "unmet", "documented_not_satisfied", "not_satisfied"):
            return False
        if s in ("null", "none", "unknown", "not_documented", "not documented", ""):
            return None
    return INVALID  # garbage input scores wrong everywhere, incl. vs null gold


def facts_from_answers(answers: dict[str, str], facts: list[str]) -> dict[str, Any]:
    """Map typed per-fact answers to dataset fact values."""
    return {f: (v if (v := _norm_fact(answers.get(f"fact::{f}"))) is not INVALID else None)
            for f in facts}


def evidence_ok(case, criterion: str, cited: list[str]) -> bool:
    """Cited set is valid iff subset of allowed_evidence and superset of some support_set."""
    ev = (case.scoring.get("evidence") or {}).get(criterion) or {}
    allowed = set(ev.get("allowed_evidence") or [])
    sets = [set(s) for s in (ev.get("support_sets") or [])]
    got = set(cited or [])
    if not got:
        return False
    if allowed and not got <= allowed:
        return False
    if sets and not any(s <= got for s in sets):
        return False
    return True


def score_prediction(case, pred: dict[str, Any], *, check_evidence: bool = True) -> dict[str, Any]:
    """Return per-part correctness for one case.

    whole_case = facts AND criteria states AND missing_info AND decision AND
    action AND scope (AND evidence when check_evidence and the prediction cites).
    """
    t = case.target
    gold_facts = {k: _norm_fact(v) for k, v in (t.get("facts") or {}).items()}
    pred_facts_raw = pred.get("facts") or {}
    pred_facts = {k: _norm_fact(v) for k, v in pred_facts_raw.items()}
    fact_correct = {f: (pf := pred_facts.get(f)) is not INVALID and pf == gold_facts.get(f)
                    for f in gold_facts}
    n_fact_right = sum(fact_correct.values())

    gold_crit = {k: v.get("state") for k, v in (t.get("criteria") or {}).items()}
    pred_crit_raw = pred.get("criteria") or {}
    pred_crit = {k: (v or {}).get("state") if isinstance(v, dict) else v for k, v in pred_crit_raw.items()}
    crit_correct = {c: pred_crit.get(c) == gold_crit.get(c) for c in gold_crit}
    n_crit_right = sum(crit_correct.values())

    ev_ok = True
    if check_evidence:
        for c, item in (t.get("criteria") or {}).items():
            cited = []
            pv = pred_crit_raw.get(c)
            if isinstance(pv, dict):
                cited = pv.get("evidence") or []
            if not evidence_ok(case, c, cited):
                ev_ok = False
                break

    gold_missing = sorted(t.get("missing_info") or [])
    pred_missing = sorted(pred.get("missing_info") or [])
    parts = {
        "facts_all": n_fact_right == len(gold_facts),
        "criteria_all": n_crit_right == len(gold_crit),
        "evidence": ev_ok,
        "missing_info": gold_missing == pred_missing,
        "scope": pred.get("scope") == t.get("scope"),
        "decision": pred.get("decision") == t.get("decision"),
        "action": pred.get("action") == t.get("action"),
        "policy_id": pred.get("policy_id") == t.get("policy_id"),
        "branch": pred.get("branch") == t.get("branch"),
    }
    whole = all(parts.values())
    return {
        "whole_case": whole,
        "decision": parts["decision"],
        "action": parts["action"],
        "scope": parts["scope"],
        "policy_id": parts["policy_id"],
        "missing_info": parts["missing_info"],
        "facts_all": parts["facts_all"],
        "criteria_all": parts["criteria_all"],
        "evidence": parts["evidence"],
        "per_fact": fact_correct,
        "n_facts": len(gold_facts),
        "n_facts_right": n_fact_right,
        "per_criterion": crit_correct,
        "n_criteria_right": n_crit_right,
        "parts": parts,
    }


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(scores)
    if not n:
        return {}
    tot_facts = sum(s["n_facts"] for s in scores)
    return {
        "n": n,
        "whole_case": sum(s["whole_case"] for s in scores) / n,
        "decision": sum(s["decision"] for s in scores) / n,
        "action": sum(s["action"] for s in scores) / n,
        "scope": sum(s["scope"] for s in scores) / n,
        "missing_info": sum(s["missing_info"] for s in scores) / n,
        "facts_all": sum(s["facts_all"] for s in scores) / n,
        "per_fact": sum(s["n_facts_right"] for s in scores) / tot_facts if tot_facts else None,
        "criteria_all": sum(s["criteria_all"] for s in scores) / n,
        "evidence": sum(s["evidence"] for s in scores) / n,
    }

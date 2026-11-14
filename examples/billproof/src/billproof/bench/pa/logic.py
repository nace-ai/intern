"""Deterministic decision code for criteria-gym policy_checklist.

Reproduces the dataset's documented semantics (system-prompt contract):
  facts: true (a current record explicitly documents satisfied),
         false (a record explicitly documents NOT satisfied),
         null (no record documents it).
  criterion state over its fact dependencies, three-valued OR:
         any true -> met; all false -> unmet; otherwise unknown.
  decision over criteria (list connective AND/OR collapses to the same rule
         because AND lists have 1 fact per criterion and OR lists have 1
         criterion over all alternatives):
         any unmet -> criteria_unmet/review_unmet_criteria
         else any unknown -> incomplete/request_evidence
         else criteria_met/continue_full_review
  scope: request.payer != policy.payer -> wrong_payer;
         service_date outside exercise_applicability -> wrong_date;
         wrong scope -> not_applicable/route_to_applicable_policy.
  missing_info = exactly the unknown criterion names.
"""

from __future__ import annotations

from typing import Any

ACTION_FOR_DECISION = {
    "criteria_met": "continue_full_review",
    "criteria_unmet": "review_unmet_criteria",
    "incomplete": "request_evidence",
    "not_applicable": "route_to_applicable_policy",
}


def compute_scope(policy: dict[str, Any], request: dict[str, Any]) -> str:
    if request.get("payer") != policy.get("payer"):
        return "wrong_payer"
    window = policy.get("exercise_applicability") or {}
    date = request.get("service_date") or ""
    if not (window.get("from", "") <= date <= window.get("through", "")):
        return "wrong_date"
    return "applicable"


def criterion_states(
    facts: dict[str, Any], criterion_deps: dict[str, list[str]]
) -> dict[str, str]:
    states: dict[str, str] = {}
    for name, deps in criterion_deps.items():
        values = [facts.get(d) for d in deps]
        if any(v is True for v in values):
            states[name] = "met"
        elif values and all(v is False for v in values):
            states[name] = "unmet"
        else:
            states[name] = "unknown"
    return states


def decide(
    facts: dict[str, Any],
    criterion_deps: dict[str, list[str]],
    scope: str,
) -> dict[str, Any]:
    """facts {fact: true|false|None} -> {criteria, missing_info, decision, action, scope}."""
    states = criterion_states(facts, criterion_deps)
    missing = [name for name, s in states.items() if s == "unknown"]
    if scope != "applicable":
        decision = "not_applicable"
    elif any(s == "unmet" for s in states.values()):
        decision = "criteria_unmet"
    elif missing:
        decision = "incomplete"
    else:
        decision = "criteria_met"
    return {
        "scope": scope,
        "criteria": states,
        "missing_info": missing,
        "decision": decision,
        "action": ACTION_FOR_DECISION[decision],
    }


def decide_from_states(
    states: dict[str, str], scope: str
) -> dict[str, Any]:
    """Same as decide() but the caller already has criterion states (e.g. direct
    criterion-level model answers)."""
    missing = [name for name, s in states.items() if s == "unknown"]
    if scope != "applicable":
        decision = "not_applicable"
    elif any(s == "unmet" for s in states.values()):
        decision = "criteria_unmet"
    elif missing:
        decision = "incomplete"
    else:
        decision = "criteria_met"
    return {
        "scope": scope,
        "criteria": dict(states),
        "missing_info": missing,
        "decision": decision,
        "action": ACTION_FOR_DECISION[decision],
    }


def verify_reproduction(cases: list) -> dict[str, Any]:
    """Check decide() reproduces target.decision/action/scope/missing from gold facts."""
    n = 0
    bad: list[dict[str, Any]] = []
    for c in cases:
        scope = compute_scope(c.policy, c.request)
        got = decide(c.gold_facts(), c.criterion_deps, scope)
        t = c.target
        n += 1
        diffs = {}
        for key in ("scope", "decision", "action", "missing_info", "criteria"):
            want = t.get(key)
            have = got.get(key)
            if key in ("missing_info",):
                ok = sorted(want or []) == sorted(have or [])
            elif key == "criteria":
                want_states = {k: v["state"] for k, v in (want or {}).items()}
                ok = want_states == have
            else:
                ok = want == have
            if not ok:
                diffs[key] = {"want": want, "have": have}
        if diffs:
            bad.append({"row_id": c.row_id, "diffs": diffs})
    return {"n": n, "mismatched": len(bad), "examples": bad[:10]}

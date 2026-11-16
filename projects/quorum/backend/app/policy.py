"""Policy: cost-weighted decision and guard that raises actions.

Pure functions. The guard reads only Facts and may only raise actions.
"""

from __future__ import annotations

from typing import Any

from backend.app.contracts import Facts

ORDER: dict[str, int] = {"approve": 0, "verify": 1, "block": 2}


def _check_action(action: object) -> str:
    """Return action if it is approve, verify or block; else raise ValueError."""
    if not isinstance(action, str) or action not in ORDER:
        raise ValueError(f"unknown action {action!r}; expected one of {list(ORDER)}")
    return action


def risk_score(p: dict[str, float]) -> float:
    """Working risk score: p[block] + 0.5 * p[verify].

    Not a fraud probability. Not calibrated. Called "risk score" on screen.
    """
    return p.get("block", 0.0) + 0.5 * p.get("verify", 0.0)


def choose(p: dict[str, float], L: float = 100, V: float = 5, D: float = 30) -> str:
    """Pick the action with lowest expected cost.

    Expected costs:
        approve:  r * L
        verify:   V
        block:    (1 - r) * D

    On a tie, return the stricter action (by ORDER).
    """
    for key in p:
        _check_action(key)
    r = risk_score(p)

    cost_approve = r * L
    cost_verify = V
    cost_block = (1 - r) * D

    candidates: list[tuple[float, str]] = [
        (cost_approve, "approve"),
        (cost_verify, "verify"),
        (cost_block, "block"),
    ]

    # Sort by cost ascending, then by strictness (ORDER) descending to break ties
    candidates.sort(key=lambda x: (x[0], -ORDER[x[1]]))

    return candidates[0][1]


def guard(action: str, facts: Facts | dict[str, Any]) -> tuple[str, list[str]]:
    """Raise an action based on Facts. Returns (new_action, findings).

    The guard may only raise (approve < verify < block). It never lowers.
    Findings are short codes explaining each raise.

    Conditions:

    | Condition                                    | Minimum | Finding               |
    |----------------------------------------------|---------|-----------------------|
    | iban_status is not "found"                   | verify  | iban_unreadable       |
    | Supplier unknown                             | verify  | supplier_unknown      |
    | IBAN mismatch + no trusted verification      | verify  | unverified_bank_change|
    | duplicate_invoice_number                     | block   | duplicate_paid        |
    """
    # Accept both Facts objects and plain dicts
    if isinstance(facts, Facts):
        f = facts
    else:
        f = Facts(**{k: facts[k] for k in Facts.model_fields if k in facts})

    current_order = ORDER[_check_action(action)]
    findings: list[str] = []

    # Condition 1: iban_status not "found"
    if f.iban_status != "found":
        current_order = max(current_order, ORDER["verify"])
        findings.append("iban_unreadable")

    # Condition 2: supplier unknown
    if not f.supplier_known:
        current_order = max(current_order, ORDER["verify"])
        findings.append("supplier_unknown")

    # Condition 3: IBAN doesn't match trusted and no trusted verification
    if not f.iban_matches_trusted and not f.trusted_verification_for_this_iban:
        current_order = max(current_order, ORDER["verify"])
        findings.append("unverified_bank_change")

    # Condition 4: duplicate invoice number
    if f.duplicate_invoice_number:
        current_order = max(current_order, ORDER["block"])
        findings.append("duplicate_paid")

    # Map order back to action string
    new_action = {v: k for k, v in ORDER.items()}.get(current_order, action)

    return new_action, findings


def raw_action_of(p: dict[str, float]) -> str:
    """Most probable action; ties go to the higher (stricter) action."""
    for key in p:
        _check_action(key)
    return max(ORDER, key=lambda a: (p.get(a, 0.0), ORDER[a]))


def resolve(
    p: dict[str, float],
    facts: Facts | dict[str, Any],
    L: float = 100,
    V: float = 5,
    D: float = 30,
) -> dict[str, Any]:
    """Combine model, policy and guard into one action that never goes below any of them.

    Returns {"raw_action", "policy_action", "final_action", "findings", "raised_by"}.
    raised_by lists "policy" (policy above raw) and/or "guard" (guard raised it further).
    Raises ValueError on an unknown action key.
    """
    raw = raw_action_of(p)
    policy_action = choose(p, L, V, D)
    start = raw if ORDER[raw] >= ORDER[policy_action] else policy_action
    final, findings = guard(start, facts)

    raised_by: list[str] = []
    if ORDER[policy_action] > ORDER[raw]:
        raised_by.append("policy")
    if ORDER[final] > ORDER[start]:
        raised_by.append("guard")

    if not (ORDER[final] >= ORDER[raw] and ORDER[final] >= ORDER[policy_action]
            and ORDER[final] >= ORDER[start]):
        raise AssertionError(
            f"final {final!r} below raw {raw!r} / policy {policy_action!r} / guard input {start!r}"
        )
    return {
        "raw_action": raw,
        "policy_action": policy_action,
        "final_action": final,
        "findings": findings,
        "raised_by": raised_by,
    }

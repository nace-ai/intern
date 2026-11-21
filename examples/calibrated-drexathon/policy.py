"""Step 3: turn a Drex probability into an action with two thresholds.

    p >= hi        -> QUARANTINE  (confidently flaky: skip + file ticket)
    lo <= p < hi   -> RETRY       (unsure: rerun once, cheap)
    p <  lo        -> BLOCK       (likely real regression: block merge, alert a human)
"""
from __future__ import annotations

from drexflaky.common import DEFAULT_HI, DEFAULT_LO


def decide(p: float, lo: float = DEFAULT_LO, hi: float = DEFAULT_HI) -> str:
    if p is None:
        return "RETRY"  # no answer -> safest cheap action
    if p >= hi:
        return "QUARANTINE"
    if p >= lo:
        return "RETRY"
    return "BLOCK"


def reason(p: float, lo: float, hi: float) -> str:
    a = decide(p, lo, hi)
    return {"QUARANTINE": f"P(flaky)={p:.3f} >= hi={hi:.2f}",
            "RETRY": f"{lo:.2f} <= P(flaky)={p:.3f} < hi={hi:.2f}",
            "BLOCK": f"P(flaky)={p:.3f} < lo={lo:.2f}"}[a]


def cost_of(decisions, truths, costs) -> float:
    return sum(costs[(t, d)] for d, t in zip(decisions, truths))

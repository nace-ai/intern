"""The decision gates: drex (the System 1 decision model) judges findings through two calibrated
yes/no questions, and answers branch questions the agent would otherwise guess at.

Gate 1 — real vs false positive: ``p(real) ≥ 0.85`` auto-confirms, ``≤ 0.30`` drops as a false
positive, the middle band is left for manual judgement. Gate 2 — impact vs claimed severity: a
HIGH/CRITICAL additionally needs ``p(impact demonstrated at the claimed severity) ≥ 0.60``, else
it is held at ``suspected``; an already-confirmed HIGH/CRITICAL whose evidence does not carry the
severity is downgraded. Every ask goes through ``ctx.decisions`` (the session's DecisionClient),
so it gets the cache, the retries and the ``decision`` event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from intern.decisions import DecisionClient, NoulAnswer, NoulQuestion
from intern.engagement.models import Vuln
from intern.engagement.store import EngagementStore

CONFIRM_AT = 0.85
DROP_AT = 0.30
IMPACT_AT = 0.60

FALSE_POSITIVE_QUESTION = NoulQuestion(
    instructions=(
        "Is this a REAL, exploitable security vulnerability (true) or a false positive / informational "
        "noise (false)? Judge strictly on the evidence — no proof, banner-only, or expected behavior "
        "means false."
    )
)

IMPACT_QUESTION = NoulQuestion(
    instructions=(
        "Does the ATTACHED EVIDENCE actually DEMONSTRATE security impact at the claimed severity "
        "(true), or is the severity overstated relative to what was proven (false)? Judge strictly: "
        "a theoretical or architectural risk, an assertion that something 'is possible'/'is "
        "bypassable' with no working demonstration, a configuration weakness with no shown "
        "consequence, or bypassing a control that is trivially defeated anyway (e.g. a User-Agent "
        "or bot filter sidestepped just by changing the request) does NOT demonstrate impact at a "
        "high severity. True only if the evidence shows the attacker concretely obtaining what the "
        "claimed severity implies (the data, the file, the shell, the unauthorized action)."
    )
)


def _target(v: Vuln) -> str:
    return v.host + (f":{v.service_port}" if v.service_port else "")


async def score_false_positive(client: DecisionClient, v: Vuln) -> float:
    state = (
        f"finding: {v.title}\nseverity: {v.severity or 'unknown'}\n"
        f"evidence: {v.evidence or '(none)'}\ntarget: {_target(v)}"
    )
    answers = await client.ask("drex triage", state, {"q": FALSE_POSITIVE_QUESTION}, subject=v.id)
    answer = answers["q"]
    assert isinstance(answer, NoulAnswer)
    return answer.noul


async def score_impact_supported(client: DecisionClient, v: Vuln) -> float:
    state = (
        f"finding: {v.title}\nclaimed_severity: {v.severity or 'unknown'}\n"
        f"evidence: {v.evidence or '(none)'}\ntarget: {_target(v)}"
    )
    answers = await client.ask("drex impact gate", state, {"q": IMPACT_QUESTION}, subject=v.id)
    answer = answers["q"]
    assert isinstance(answer, NoulAnswer)
    return answer.noul


def _impact_note(v: Vuln, p_impact: float) -> str:
    base = re.sub(r"\s*·?\s*\[drex impact@[^\]]*\]", "", v.evidence or "").strip()
    marker = f"[drex impact@{v.severity}={p_impact:.2f}: not demonstrated — downgrade or prove it]"
    return f"{base} · {marker}" if base else marker


@dataclass
class TriageCounts:
    confirmed: int = 0
    dropped: int = 0
    manual: int = 0
    held: int = 0
    downgraded: int = 0


def _is_high_or_critical(v: Vuln) -> bool:
    return v.severity in ("high", "critical")


async def triage(
    store: EngagementStore,
    client: DecisionClient,
    *,
    confirm_at: float = CONFIRM_AT,
    drop_at: float = DROP_AT,
    impact_at: float = IMPACT_AT,
) -> tuple[TriageCounts, list[str]]:
    """Run both gates over every finding that still owes a verdict; re-check confirmed HIGH/CRIT
    findings and downgrade overclaimed ones. Returns the counts and one line per finding."""
    counts = TriageCounts()
    lines: list[str] = []
    handled: set[str] = set()

    async def impact_or_fail_open(v: Vuln) -> float:
        try:
            return await score_impact_supported(client, v)
        except Exception:
            return 1.0

    for v in store.unvalidated():
        handled.add(v.id)
        try:
            p_real = await score_false_positive(client, v)
        except Exception as e:
            lines.append(f"? {v.title}: drex error ({e})")
            continue
        if p_real <= drop_at:
            v.status = "false_positive"
            v.confidence, v.decided_by, v.drex_call, v.reviews = p_real, "drex", "dropped", []
            lines.append(f"✗ DROP {p_real:.2f} — {v.title}")
            counts.dropped += 1
        elif p_real >= confirm_at:
            p_impact = await impact_or_fail_open(v) if _is_high_or_critical(v) else 1.0
            if p_impact < impact_at:
                v.status = "suspected"
                v.confidence, v.decided_by, v.drex_call, v.reviews = p_real, "drex", "held", []
                v.evidence = _impact_note(v, p_impact)
                lines.append(f"⚠ HOLD real {p_real:.2f} but impact@{v.severity}={p_impact:.2f} unproven — {v.title}")
                counts.held += 1
            else:
                v.status = "confirmed"
                v.confidence, v.decided_by, v.drex_call, v.reviews = p_real, "drex", "confirmed", []
                lines.append(f"✓ CONFIRM {p_real:.2f} — {v.title}")
                counts.confirmed += 1
        else:
            v.confidence, v.decided_by, v.drex_call, v.reviews = p_real, "drex", None, []
            lines.append(f"→ keep {p_real:.2f} — {v.title} (your call)")
            counts.manual += 1

    # Overclaim re-check: an already-confirmed HIGH/CRIT must still carry its severity.
    for v in store.state.vulns:
        if v.id in handled or v.status != "confirmed" or not _is_high_or_critical(v):
            continue
        try:
            p_impact = await score_impact_supported(client, v)
        except Exception:
            continue
        if p_impact < impact_at:
            v.status = "suspected"
            v.decided_by, v.drex_call, v.reviews = "drex", "downgraded", []
            v.evidence = _impact_note(v, p_impact)
            lines.append(f"↓ DOWNGRADE impact@{v.severity}={p_impact:.2f} unproven — {v.title}")
            counts.downgraded += 1

    store.log(
        "drex",
        f"triaged: {counts.confirmed} confirmed, {counts.dropped} dropped, {counts.manual} manual, "
        f"{counts.held} held (impact), {counts.downgraded} downgraded (impact)",
    )
    return counts, lines


async def decide(
    store: EngagementStore, client: DecisionClient, question: str, context: str | None
) -> tuple[float, str]:
    """A single calibrated yes/no on a branch question. Bands: yes ≥ 0.6, no ≤ 0.4, else uncertain."""
    state = context or repr(store.summary())
    answers = await client.ask("drex decide", state, {"q": NoulQuestion(instructions=question)})
    answer = answers["q"]
    assert isinstance(answer, NoulAnswer)
    verdict = "yes" if answer.noul >= 0.6 else ("no" if answer.noul <= 0.4 else "uncertain")
    store.log("drex", f"decide {question[:70]!r} → {answer.noul:.2f} ({verdict})")
    return answer.noul, verdict


def describe_review_queue(store: EngagementStore) -> str:
    """The queue of drex calls awaiting verification and final calls, for state_query."""
    queue = store.review_queue()
    decisions = queue["decisions"]
    if not isinstance(decisions, int) or decisions == 0:
        return "drex has made no calls on findings — nothing to review."
    to_verify = queue["to_verify"]
    to_finalize = queue["to_finalize"]
    assert isinstance(to_verify, list) and isinstance(to_finalize, list)
    lines = [
        f"drex calls: {decisions} · verified {queue['verified']} · final {queue['finalized']} · "
        f"overturned {queue['overturned']}"
    ]
    if to_verify:
        lines.append(
            "To verify (dispatch role=verifier — it re-tests each live, from scratch, and records "
            "state_update action=review):"
        )
        for v in to_verify:
            assert isinstance(v, Vuln)
            lines.append(_queue_line(v))
    if to_finalize:
        lines.append(
            "To finalize (main agent: weigh the verifier's verdict, then state_update action=review "
            "id=… verdict=upheld|overturned review_status=… notes=…):"
        )
        for v in to_finalize:
            assert isinstance(v, Vuln)
            lines.append(_queue_line(v))
    return "\n".join(lines)


def _queue_line(v: Vuln) -> str:
    verifier = next((r for r in v.reviews if r.stage == "verifier"), None)
    parts = [
        f"- {v.id} [{v.severity}] {v.title} ({v.host}) — drex {v.drex_call} (p={v.confidence:.2f}), now {v.status}"
    ]
    if verifier is not None:
        extra = f" → {verifier.status}" if verifier.status else ""
        parts.append(f"; verifier: {verifier.verdict}{extra} — {verifier.notes}")
    if v.evidence:
        parts.append(f"; evidence: {v.evidence[:200]}")
    return "".join(parts)


__all__ = [
    "CONFIRM_AT",
    "DROP_AT",
    "IMPACT_AT",
    "TriageCounts",
    "decide",
    "describe_review_queue",
    "score_false_positive",
    "score_impact_supported",
    "triage",
]

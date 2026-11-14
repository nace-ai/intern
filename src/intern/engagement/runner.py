"""The unattended engagement supervisor: keeps an agent working until the completion gates pass,
the budget is spent, or it stops making progress — then leaves a summary and an exit code CI can
act on. Explore rounds name exactly what is still open; once exploration is done (or the budget
reaches its wrap-up point) wrap-up rounds close proof, re-check and report gaps.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from intern.engagement.depth import shallow_findings
from intern.engagement.models import Vuln
from intern.engagement.owasp import coverage_gaps, coverage_matrix
from intern.engagement.pivot import open_opportunities
from intern.engagement.retest import retest_kickoff
from intern.engagement.store import SEVERITY_ORDER, EngagementStore

#: At this fraction of the budget the run switches from exploring to wrapping up.
WRAP_AT = 0.85
_STALE_ROUNDS = 2
_ERROR_ROUNDS = 2
_ERROR_REASONS = frozenset({"provider_error", "system1_unavailable", "internal_error"})
MAX_ITEMS = 12

#: One log record: ``{"ts": <ISO>, "type": str, "detail": object}``. A ``summary`` record's detail is
#: the whole summary dict, for the caller to write to ``logs/summary.json``.
EmitLog = Callable[[dict[str, Any]], None]

Exit = Literal[0, 1, 2, 3]
Stop = Literal["complete", "budget", "no-progress", "max-rounds", "error"]


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


@dataclass(frozen=True)
class Gate:
    """One piece of work the harness knows is unfinished."""

    id: str
    summary: str
    items: tuple[str, ...]


def _cap(gate: Gate) -> Gate:
    if len(gate.items) <= MAX_ITEMS:
        return gate
    return Gate(gate.id, gate.summary, (*gate.items[:MAX_ITEMS], f"… and {len(gate.items) - MAX_ITEMS} more"))


def _report_gaps(store: EngagementStore) -> list[str]:
    report = store.state.report
    gaps: list[str] = []
    for name, value in (
        ("synopsis", report.synopsis),
        ("headline", report.headline),
        ("summary", report.summary),
        ("worked_well", report.worked_well),
        ("coverage_notes", report.coverage_notes),
    ):
        if not value or not str(value).strip():
            gaps.append(f"report_content {name} is empty")
    if not report.observations:
        gaps.append("report_content observations: none recorded")
    return gaps


def gates(store: EngagementStore) -> list[Gate]:
    """The exploration gates: what the store says is still unfinished."""
    state = store.state
    out: list[Gate] = []
    gaps = coverage_gaps(coverage_matrix(state.vulns, state.endpoints, state.coverage))
    if gaps:
        out.append(
            Gate(
                "coverage",
                _plural(len(gaps), "category", "categories") + " untested",
                tuple(f"{row.id} {row.title}" for row in gaps),
            )
        )
    shallow = shallow_findings(state.vulns)
    if shallow:
        out.append(
            Gate(
                "depth",
                _plural(len(shallow), "finding") + " still owes depth",
                tuple(f"{v.id} {v.title}: {why}" for v, why in shallow),
            )
        )
    unvalidated = store.unvalidated()
    if unvalidated:
        out.append(
            Gate(
                "validation",
                _plural(len(unvalidated), "finding") + " not validated",
                tuple(
                    f"{v.id} [{v.severity}] {v.title}: "
                    f"{'suspected' if v.status == 'suspected' else f'confidence {v.confidence}'} "
                    f"— validate it or record why it is dropped"
                    for v in unvalidated
                ),
            )
        )
    pivots = open_opportunities(state)
    if pivots:
        out.append(
            Gate("pivots", _plural(len(pivots), "pivot") + " open", tuple(f"{o.id}: {o.action}" for o in pivots))
        )
    queue = store.review_queue()
    to_verify, to_finalize = queue["to_verify"], queue["to_finalize"]
    assert isinstance(to_verify, list) and isinstance(to_finalize, list)
    if to_verify or to_finalize:
        out.append(
            Gate(
                "review",
                _plural(len(to_verify) + len(to_finalize), "drex decision") + " not reviewed",
                (
                    *(
                        f"{v.id} {v.title} — drex {v.drex_call}: dispatch the verifier to re-test it live "
                        "(state_query what=review)"
                        for v in to_verify
                    ),
                    *(
                        f"{v.id} {v.title} — the verifier has re-tested it: make the final call "
                        "(state_update action=review)"
                        for v in to_finalize
                    ),
                ),
            )
        )
    return [_cap(g) for g in out]


def wrap_gates(store: EngagementStore) -> list[Gate]:
    """The wrap-up gates: proof, live re-checks and the report."""
    state = store.state
    out: list[Gate] = []
    confirmed = [v for v in state.vulns if v.status == "confirmed"]
    proven = {vid for e in state.evidence for vid in e.vuln_ids}
    unproven = [v for v in confirmed if v.id not in proven]
    if unproven:
        out.append(
            Gate(
                "proof",
                _plural(len(unproven), "confirmed finding") + " without captured proof",
                tuple(f"{v.id} {v.title}" for v in unproven),
            )
        )
    unchecked = [v for v in confirmed if v.rechecked_at is None]
    if unchecked:
        out.append(
            Gate(
                "recheck",
                _plural(len(unchecked), "confirmed finding") + " not re-checked live",
                tuple(f"{v.id} {v.title}" for v in unchecked),
            )
        )
    report = _report_gaps(store)
    if report:
        out.append(Gate("report", f"report: {_plural(len(report), 'gap')}", tuple(report)))
    return [_cap(g) for g in out]


def progress_mark(store: EngagementStore) -> str:
    """A fingerprint of the engagement's progress: if a round changes none of it, the agent is stuck."""
    s = store.state
    confirmed = sum(1 for v in s.vulns if v.status == "confirmed")
    return "|".join(
        str(part)
        for part in (
            len(s.hosts),
            len(s.vulns),
            confirmed,
            "".join((v.depth or "") + (v.status or "") for v in s.vulns),
            len(s.endpoints),
            len(s.creds),
            len(s.access),
            len(s.coverage),
            len(s.evidence),
            len(s.pivots),
            len(s.report.observations),
            ",".join(str(v.rechecked_at or 0) for v in s.vulns),
            ",".join((v.retest.result if v.retest else "-") for v in s.vulns),
        )
    )


@dataclass
class Budget:
    """Time and (optional) cost budget. At ``WRAP_AT`` the run wraps up; at 100% it stops."""

    started: float = field(default_factory=time.monotonic)
    max_minutes: float = 60.0
    max_cost: float | None = None
    cost: float = 0.0
    clock: Callable[[], float] = time.monotonic

    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started)

    def used(self) -> float:
        time_used = self.elapsed() / (self.max_minutes * 60.0)
        cost_used = self.cost / self.max_cost if self.max_cost else 0.0
        return max(time_used, cost_used)

    def phase(self) -> Literal["explore", "wrap", "stop"]:
        used = self.used()
        return "stop" if used >= 1.0 else "wrap" if used >= WRAP_AT else "explore"

    def reason(self) -> str:
        minutes = round(self.max_minutes, 1)
        if self.max_cost and self.cost / self.max_cost > self.elapsed() / (self.max_minutes * 60.0):
            return f"{round(self.cost / self.max_cost * 100)}% of the ${self.max_cost} cost budget used"
        return f"{round(min(self.used(), 1.0) * 100)}% of the {minutes}-minute budget used"


def _approved_line(approved: frozenset[str]) -> str:
    pre = ", ".join(sorted(approved)) if approved else "nothing"
    return (
        f"Pre-approved for this run: {pre}. Anything you would normally ask the operator about and "
        "that is not pre-approved: record it as a note with the concrete action and what it would "
        "prove, then continue with everything else. Nobody is watching; nobody will answer."
    )


def kickoff_prompt(target: str, scope: Sequence[str], objective: str, approved: frozenset[str]) -> str:
    return (
        "# Engagement\n\n"
        f"Target: {target}\nScope: {', '.join(scope)}\n\n{objective}\n\n"
        "Unattended run: nobody is watching and nobody will answer questions. Never stop to ask, "
        "offer options or wait for confirmation: decide, act within the rules of engagement, and "
        "keep going until the work is done.\n"
        f"{_approved_line(approved)}\n"
        "Coverage: n/a means a category can't apply to this target, and the note says why. A "
        "category you didn't get to is untested, not n/a.\n\n"
        "Start with set_engagement for the target, then recon. Work through the methodology "
        "without stopping for confirmation."
    )


def _bullets(gate: Gate) -> str:
    return "\n".join(f"  - {item}" for item in gate.items)


def follow_up_prompt(open_gates: Sequence[Gate], round_: int, approved: frozenset[str]) -> str:
    parts = [
        f"Round {round_}: the engagement isn't finished. Continue without asking; work through "
        "these, then keep going on anything else you find:"
    ]
    for gate in open_gates:
        parts.append(f"\n{gate.summary}:\n{_bullets(gate)}")
    parts.append(
        "\nFor a category that genuinely doesn't apply to this target, record it with state_update "
        "action=coverage coverage_status=na and the reason. For a finding that can't go deeper, "
        "record stop_reason with its stop_kind. For a pivot that doesn't work, record its outcome."
    )
    if approved:
        parts.append(f"\n{_approved_line(approved)}")
    return "\n".join(parts)


def wrap_up_prompt(open_gates: Sequence[Gate], reason: str, approved: frozenset[str]) -> str:
    parts = [
        f"Wrap up now ({reason}). Stop exploring new attack surface. In this order:",
        "1. Capture proof for every confirmed finding that lacks it (evidence linked to the finding id).",
        "2. Re-check every confirmed finding live (state_update action=recheck id=<id> remediated=true|false).",
        "3. Write the report content (report_content: synopsis, headline, summary, observations, "
        "priorities, worked_well, coverage_notes, attack_path where findings chain, roadmap), then "
        "run the report tool to render it.",
    ]
    if open_gates:
        parts.append("\nStill open:")
        for gate in open_gates:
            parts.append(f"\n{gate.summary}:\n{_bullets(gate)}")
    if approved:
        parts.append(f"\n{_approved_line(approved)}")
    return "\n".join(parts)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _session_cost(session: Any) -> float | None:
    """The session's spend so far, from its token accounting — None when it keeps none."""
    usage = getattr(session, "usage", None)
    cost = getattr(usage, "cost_usd", None)
    return float(cost) if isinstance(cost, (int, float)) else None


def _fail_on_hit(store: EngagementStore, fail_on: str | None) -> bool:
    if not fail_on or fail_on not in SEVERITY_ORDER:
        return False
    floor = SEVERITY_ORDER.index(fail_on)
    return any(v.status == "confirmed" and SEVERITY_ORDER.index(v.severity) >= floor for v in store.state.vulns)


def _confirmed_severities(vulns: Iterable[Vuln]) -> dict[str, int]:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for v in vulns:
        if v.status == "confirmed":
            counts[v.severity] = counts.get(v.severity, 0) + 1
    return counts


async def run_engagement(
    options: Any,
    store: EngagementStore,
    session_factory: Callable[[], Any],
    *,
    target: str,
    scope: Sequence[str],
    objective: str,
    max_minutes: float = 60.0,
    max_cost: float | None = None,
    max_rounds: int = 12,
    fail_on: str | None = None,
    approve: Iterable[str] = (),
    retest: bool = False,
    emit_log: EmitLog,
) -> int:
    """Drive an unattended engagement to completion. Returns the exit code: 0 all gates passed,
    1 error, 2 stopped with work open, 3 a confirmed finding at or above ``fail_on``."""
    started_ts = time.time()
    approved = frozenset(approve)

    def event(type_: str, detail: object) -> None:
        with contextlib.suppress(Exception):
            emit_log({"ts": _iso(time.time()), "type": type_, "detail": detail})

    event(
        "run_start",
        {
            "target": target,
            "scope": list(scope),
            "max_minutes": max_minutes,
            "max_cost": max_cost,
            "max_rounds": max_rounds,
            "approve": sorted(approved),
            "retest": retest,
        },
    )

    budget = Budget(started=time.monotonic(), max_minutes=max_minutes, max_cost=max_cost)
    stop: Stop = "complete"
    error: str | None = None
    rounds = 0
    errors = 0

    session = session_factory()
    if inspect.isawaitable(session):
        session = await session
    try:
        prompt = retest_kickoff(store) if retest else kickoff_prompt(target, scope, objective, approved)
        phase: Literal["explore", "wrap"] = "explore"
        wrap_reason = ""
        mark = progress_mark(store)
        stale = 0
        while True:
            outcome = await session.run(prompt)
            rounds += 1
            cost = _session_cost(session)
            if cost is not None:
                budget.cost = cost
            event("round", {"n": rounds, "phase": phase, "outcome": outcome.reason})
            if outcome.reason in _ERROR_REASONS:
                errors += 1
                if errors >= _ERROR_ROUNDS:
                    stop, error = "error", outcome.detail or "the model returned errors twice in a row"
                    break
            else:
                errors = 0

            if budget.phase() == "stop":
                stop = "budget"
                event("phase", {"phase": "stop", "reason": budget.reason()})
                break
            if phase == "explore" and budget.phase() == "wrap":
                phase, wrap_reason = "wrap", budget.reason()
                event("phase", {"phase": "wrap", "reason": wrap_reason})

            now_mark = progress_mark(store)
            stale = stale + 1 if now_mark == mark else 0
            mark = now_mark
            if stale >= _STALE_ROUNDS:
                if phase == "explore":
                    phase, wrap_reason = "wrap", "no progress in the last rounds"
                    event("phase", {"phase": "wrap", "reason": wrap_reason})
                    stale = 0
                else:
                    stop = "no-progress"
                    break

            open_gates = gates(store) if phase == "explore" else wrap_gates(store)
            if not open_gates and phase == "explore":
                phase, wrap_reason = "wrap", "exploration complete"
                event("phase", {"phase": "wrap", "reason": wrap_reason})
                open_gates = wrap_gates(store)
            if not open_gates:
                stop = "complete"
                break
            if rounds >= max_rounds:
                stop = "max-rounds"
                break
            prompt = (
                follow_up_prompt(open_gates, rounds + 1, approved)
                if phase == "explore"
                else wrap_up_prompt(open_gates, wrap_reason, approved)
            )
            event("gates", {"phase": phase, "open": [{"gate": g.id, "summary": g.summary} for g in open_gates]})
    except (Exception, asyncio.CancelledError) as exc:
        stop, error = "error", str(exc) or exc.__class__.__name__
        event("error", {"error": error})

    open_gates = [*gates(store), *wrap_gates(store)]
    exit_code: Exit = 0
    if stop == "error":
        exit_code = 1
    elif _fail_on_hit(store, fail_on):
        exit_code = 3
    elif stop != "complete" and open_gates:
        exit_code = 2

    state = store.state
    rows = coverage_matrix(state.vulns, state.endpoints, state.coverage)
    summary = {
        "target": target,
        "started": _iso(started_ts),
        "ended": _iso(time.time()),
        "exit": exit_code,
        "stop": stop,
        "error": error,
        "rounds": rounds,
        "gates_passed": not open_gates,
        "findings": {
            "confirmed": sum(1 for v in state.vulns if v.status == "confirmed"),
            "suspected": sum(1 for v in state.vulns if v.status == "suspected"),
            "false_positive": sum(1 for v in state.vulns if v.status == "false_positive"),
            "by_severity": _confirmed_severities(state.vulns),
        },
        "coverage": {
            "accounted": sum(1 for r in rows if r.status != "untested"),
            "total": len(rows),
        },
        "open_pivots": len(open_opportunities(state)),
        "open_gates": [{"gate": g.id, "summary": g.summary} for g in open_gates],
    }
    event("summary", summary)
    event("run_end", {"stop": stop, "exit": exit_code, "rounds": rounds})
    return exit_code

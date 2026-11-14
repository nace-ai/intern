"""The drill-down ladder: every finding climbs detected → confirmed → impact → chained, and a
finding may only stop early with a stated stop_reason and its kind. Shallow findings are listed
before the report and shown to its reader."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from intern.engagement.models import Depth, StopKind, Vuln, VulnStatus

DEPTHS: tuple[Depth, ...] = ("detected", "confirmed", "impact", "chained")
STOP_KINDS: tuple[StopKind, ...] = ("terminal", "rules", "disproved", "stuck", "time")


def may_hand_on(kind: StopKind | None) -> bool:
    """Only these may be handed on to another model; a ``rules`` stop stays stopped."""
    return kind in ("stuck", "time")


def depth_rank(depth: str | None) -> int:
    return DEPTHS.index(depth) if depth in DEPTHS else 0


def merge_depth(current: str | None, next_: str | None, status: VulnStatus) -> Depth:
    """Depth never drops on a partial update; a confirmed finding is at least ``confirmed``."""
    floor = "confirmed" if status == "confirmed" else "detected"
    return DEPTHS[max(depth_rank(current), depth_rank(next_), depth_rank(floor))]


_NEXT_STEP = {
    "detected": (
        "CONFIRM it with a differential against a control (TRUE vs FALSE condition, payload vs "
        "benign input, with vs without auth, dead-IP control for network results); a single "
        "reacting payload proves nothing"
    ),
    "confirmed": (
        "PROVE IMPACT read-only: obtain what the bug class gives (the record, the file, command "
        "output, the other user's object, the admin action) and attach it with `evidence`"
    ),
    "impact": (
        "CHAIN it: what does this unlock that you didn't have? reuse creds/tokens/keys elsewhere, "
        "pivot to what this reaches, escalate the role — or set stop_reason if it is genuinely terminal"
    ),
    "chained": "re-plan from the new vantage (drex_decide: 'is there more worth extracting here?')",
}


def next_step(depth: str | None) -> str:
    return _NEXT_STEP[depth or "detected"]


def shallow_findings(vulns: Sequence[Vuln]) -> list[tuple[Vuln, str]]:
    """Every finding that still owes depth, with why: below ``impact``, or a HIGH/CRITICAL proven
    at impact but not chained."""
    out: list[tuple[Vuln, str]] = []
    for v in vulns:
        if v.status == "false_positive" or v.severity == "info" or v.stop_reason:
            continue
        rank = depth_rank(v.depth)
        if rank < depth_rank("impact"):
            out.append((v, f"at {v.depth or 'detected'} — {next_step(v.depth)}"))
        elif v.severity in ("high", "critical"):
            out.append((v, f"impact proven but not chained — {next_step('impact')}"))
    return out


def stalled_findings(vulns: Sequence[Vuln]) -> list[Vuln]:
    """Findings stopped with a hand-on-able reason before reaching ``chained``."""
    return [
        v
        for v in vulns
        if v.status != "false_positive"
        and v.severity != "info"
        and v.stop_reason
        and may_hand_on(v.stop_kind)
        and depth_rank(v.depth) < depth_rank("chained")
    ]


def render_shallow_lines(vulns: Sequence[Vuln]) -> list[str]:
    shallow = shallow_findings(vulns)
    if not shallow:
        return ["every finding is at impact (HIGH/CRIT chained) or carries a stop_reason."]
    lines = [f"{len(shallow)} finding(s) still owe depth:"]
    lines += [f"{v.id} [{v.severity}] {v.title} ({v.host}): {why}" for v, why in shallow]
    return lines


def drill_nudge(v: Vuln, playbooks_for: Callable[[str], Sequence[str]]) -> str:
    """The instruction appended to a finding's record: what the next rung is, how to record it."""
    if v.status == "false_positive" or v.stop_reason:
        return ""
    parts = [f"DRILL DOWN ({v.id} at {v.depth or 'detected'}): {next_step(v.depth)}."]
    if v.depth != "chained":
        parts.append(
            f"Record progress with state_update action=vuln id={v.id} depth=<confirmed|impact|chained>, "
            f"or stop_reason=<why> with stop_kind=<{'|'.join(STOP_KINDS)}> if it can't go further."
        )
    if not v.owasp:
        parts.append("Tag it with owasp=[A01..A10 / API1..API10] so coverage is counted.")
    else:
        playbooks: list[str] = []
        for tag in v.owasp:
            for name in playbooks_for(tag):
                if name not in playbooks:
                    playbooks.append(name)
        if playbooks and depth_rank(v.depth) < depth_rank("impact"):
            parts.append(f"Method: load_playbook surface={' or '.join(playbooks)}.")
    return " ".join(parts)

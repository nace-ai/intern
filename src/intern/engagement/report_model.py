"""Report model: the engagement record → everything the report pages need, computed once and
pure. The renderers (``report_render``) only format what is here; no business rules live in
them. ``content_gaps`` is the warn-only list the ``report`` tool returns: what the report still
lacks, said as an instruction, never as an error.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Literal

from pydantic import BaseModel, Field

from intern.engagement import pivot
from intern.engagement.depth import shallow_findings
from intern.engagement.diagram import Figure, build_figures, load_diagrams
from intern.engagement.models import (
    EvidenceRecord,
    ReportContent,
    Severity,
    State,
    Vuln,
    now_ms,
)
from intern.engagement.owasp import coverage_matrix
from intern.engagement.store import EngagementStore, detect_vuln_ids

SEVERITIES: tuple[Severity, ...] = ("critical", "high", "medium", "low", "info")
SEV_RANK: dict[str, int] = {sev: i for i, sev in enumerate(SEVERITIES)}

ReportStatus = Literal["open", "remediated", "risk_accepted", "false_positive"]
STATUS_LABEL: dict[ReportStatus, str] = {
    "open": "Open",
    "remediated": "Remediated",
    "risk_accepted": "Risk accepted",
    "false_positive": "False positive",
}

#: Client-readability limits, shared by ``report_content`` and the report pages: a field over its
#: limit is rejected with the reason, so the agent summarises instead of pasting a wall of text.
NARRATIVE_LIMITS: dict[str, int] = {
    "synopsis": 500,
    "headline": 160,
    "summary": 600,
    "worked_well": 400,
    "coverage_notes": 600,
    "limitations": 600,
}
ITEM_LIMITS: dict[str, int] = {
    "observation_title": 90,
    "observation_detail": 320,
    "priority": 140,
    "attack_narrative": 450,
    "roadmap_action": 160,
}

ATTACK_STAGES: tuple[str, ...] = (
    "initial_access",
    "execution",
    "credential_access",
    "privilege_escalation",
    "lateral_movement",
    "impact",
)
STAGE_LABEL: dict[str, tuple[str, str]] = {
    "initial_access": ("Initial access", "Entry point and attacker starting position"),
    "execution": ("Execution", "Code or command execution achieved"),
    "credential_access": ("Credential access", "Secrets, tokens or keys obtained"),
    "privilege_escalation": ("Privilege escalation", "Elevated or administrative context reached"),
    "lateral_movement": ("Lateral movement", "Pivot to adjacent systems or data stores"),
    "impact": ("Impact", "Data, integrity or availability consequence"),
}
ROADMAP_PHASES: tuple[str, ...] = ("contain", "fix", "harden")
PHASE_TITLE: dict[str, str] = {
    "contain": "Phase 1 — Contain (0–72 hours)",
    "fix": "Phase 2 — Fix (≤ 30 days)",
    "harden": "Phase 3 — Harden (≤ 90 days)",
}

RiskRating = Literal["Critical", "High", "Medium", "Low", "Minimal"]
RATING: dict[str, RiskRating] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Minimal",
}
RATING_SEGMENTS: dict[str, int] = {"Critical": 8, "High": 6, "Medium": 4, "Low": 2, "Minimal": 1}


def fmt_day(ts_ms: int) -> str:
    return _dt.datetime.fromtimestamp(ts_ms / 1000, tz=_dt.UTC).strftime("%d %b %Y").lstrip("0")


def test_window(start_ms: int, end_ms: int) -> str:
    a = _dt.datetime.fromtimestamp(start_ms / 1000, tz=_dt.UTC)
    b = _dt.datetime.fromtimestamp(end_ms / 1000, tz=_dt.UTC)
    if a.date() == b.date():
        return fmt_day(start_ms)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day:02d}–{fmt_day(end_ms)}"
    return f"{fmt_day(start_ms)} – {fmt_day(end_ms)}"


def report_status(v: Vuln) -> ReportStatus:
    if v.status == "false_positive":
        return "false_positive"
    if v.risk_accepted:
        return "risk_accepted"
    if v.remediated:
        return "remediated"
    return "open"


def register_findings(state: State) -> list[Vuln]:
    """Confirmed findings in the register: worst severity first, then stable (insertion) order."""
    return sorted(
        (v for v in state.vulns if v.status == "confirmed"),
        key=lambda v: SEV_RANK[v.severity],
    )


def _linked_ids(record: EvidenceRecord, known_ids: list[str]) -> list[str]:
    """Finding ids an artifact proves: the explicit link, or its id named in the text or filename."""
    return list(dict.fromkeys([*record.vuln_ids, *detect_vuln_ids(record.description, record.name, known_ids)]))


class Finding(BaseModel):
    """One register row plus everything the finding's own pages show."""

    fid: str
    vuln: Vuln
    status: ReportStatus
    reverified: bool
    #: critical/high findings get a full page block; the rest a compact one.
    detailed: bool
    #: artifacts linked to this finding (explicit ``vuln_ids`` or its id detected in the artifact).
    artifacts: list[EvidenceRecord] = Field(default_factory=list)

    @property
    def screenshots(self) -> list[EvidenceRecord]:
        return [a for a in self.artifacts if a.kind == "screenshot"]


StageTone = Literal["critical", "high", "precondition"]


class StageView(BaseModel):
    stage: str
    title: str
    subtitle: str
    narrative: str
    fids: list[str]
    tone: StageTone


class RoadmapRow(BaseModel):
    action: str
    fids: list[str]
    owner: str
    effort: int | None = None


class RoadmapPhaseView(BaseModel):
    phase: str
    rows: list[RoadmapRow]


class CoverageView(BaseModel):
    id: str
    title: str
    list: str
    status: str
    note: str | None = None


class RetestRow(BaseModel):
    fid: str
    title: str
    result: str | None = None
    date: str | None = None
    note: str | None = None


class RetestView(BaseModel):
    started: str
    rows: list[RetestRow]
    fixed: int
    not_fixed: int
    partial: int
    pending: int


RETEST_LABEL: dict[str, str] = {"fixed": "Fixed", "not_fixed": "Not fixed", "partial": "Partially fixed"}


class ReportModel(BaseModel):
    """Everything the report pages need, computed once from the engagement record."""

    client: str
    target: str
    engagement_id: str
    version: str
    test_window: str
    scope: list[str]
    generated: str
    year: int
    content: ReportContent
    rating: RiskRating
    rating_segments: int
    counts: dict[str, int]
    confirmed_count: int
    open_count: int
    hosts_in_scope: int
    known_hosts: list[str]
    endpoints_tested: int
    findings: list[Finding]
    attack_path: list[StageView]
    roadmap: list[RoadmapPhaseView]
    roadmap_derived: bool
    coverage: list[CoverageView]
    leads: list[Vuln]
    shallow: list[str]
    #: proof artifacts not tied to any finding — they can only appear in the appendix.
    unlinked: list[EvidenceRecord]
    #: pivot paths implied by the findings but never attempted (client wording).
    untried_pivots: list[str]
    #: stored evidence file name → F-ids it proves (for the integrity index).
    evidence_fids: dict[str, list[str]]
    figures: list[Figure]
    figure_warnings: list[str]
    #: findings with a decision-model call still awaiting verification or the final word.
    review_to_verify: int
    review_to_finalize: int
    retest: RetestView | None = None

    def fid_of(self, vuln_id: str) -> str | None:
        return next((f.fid for f in self.findings if f.vuln.id == vuln_id), None)


def _known_hosts(state: State) -> list[str]:
    raw = [state.target, *state.scope]
    raw += [h.hostname or "" for h in state.hosts.values()]
    raw += [v.host for v in state.vulns]
    names = []
    for item in raw:
        name = item.strip().lower()
        name = _strip_scheme(name)
        if _HOST_RE.fullmatch(name):
            names.append(name)
    return list(dict.fromkeys(names))


_HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def _strip_scheme(name: str) -> str:
    name = _SCHEME_RE.sub("", name)
    name = re.sub(r"^\*\.", "", name)
    return re.sub(r"[/:?#].*$", "", name)


def _stage_tone(fids: list[str], by_fid: dict[str, Finding]) -> StageTone:
    sevs = [by_fid[f].vuln.severity for f in fids if f in by_fid]
    if "critical" in sevs:
        return "critical"
    if "high" in sevs:
        return "high"
    return "precondition"


def _derive_roadmap(findings: list[Finding]) -> list[RoadmapPhaseView]:
    def phase_of(sev: str) -> str:
        return "contain" if sev in ("critical", "high") else "fix" if sev == "medium" else "harden"

    open_rows = [f for f in findings if f.status == "open" and f.vuln.remediation]
    return [
        RoadmapPhaseView(
            phase=phase,
            rows=[
                RoadmapRow(action=f.vuln.remediation[0], fids=[f.fid], owner="—")
                for f in open_rows
                if phase_of(f.vuln.severity) == phase
            ],
        )
        for phase in ROADMAP_PHASES
    ]


def _retest_view(state: State, findings: list[Finding]) -> RetestView:
    started = state.retest.started if state.retest else now_ms()
    rows = [
        RetestRow(
            fid=f.fid,
            title=f.vuln.title,
            result=f.vuln.retest.result if f.vuln.retest else None,
            date=(
                _dt.datetime.fromtimestamp(f.vuln.retest.ts / 1000, tz=_dt.UTC).strftime("%Y-%m-%d")
                if f.vuln.retest
                else None
            ),
            note=f.vuln.retest.note if f.vuln.retest else None,
        )
        for f in findings
    ]

    def count(result: str | None) -> int:
        return sum(1 for r in rows if r.result == result)

    return RetestView(
        started=_dt.datetime.fromtimestamp(started / 1000, tz=_dt.UTC).strftime("%Y-%m-%d"),
        rows=rows,
        fixed=count("fixed"),
        not_fixed=count("not_fixed"),
        partial=count("partial"),
        pending=count(None),
    )


def build_model(store: EngagementStore, *, now: int | None = None) -> ReportModel:
    """Pure computation over the engagement record; nothing here writes or reads the report files."""
    state = store.state
    c = state.report
    now = now if now is not None else now_ms()
    ordered = register_findings(state)
    width = max(2, len(str(len(ordered))))
    vuln_ids = [v.id for v in state.vulns]
    linked = {r.file: _linked_ids(r, vuln_ids) for r in state.evidence}
    findings = [
        Finding(
            fid=f"F-{i + 1:0{width}d}",
            vuln=v,
            status=report_status(v),
            reverified=bool(v.rechecked_at and not v.remediated),
            detailed=v.severity in ("critical", "high"),
            artifacts=[r for r in state.evidence if v.id in linked[r.file]],
        )
        for i, v in enumerate(ordered)
    ]
    fid_by_vuln = {f.vuln.id: f.fid for f in findings}
    by_fid = {f.fid: f for f in findings}

    def to_fids(ids: list[str]) -> list[str]:
        return [fid_by_vuln[i] for i in ids if i in fid_by_vuln]

    counts = {sev: sum(1 for f in findings if f.vuln.severity == sev) for sev in SEVERITIES}
    open_findings = [f for f in findings if f.status == "open"]
    worst = next((sev for sev in SEVERITIES if any(f.vuln.severity == sev for f in open_findings)), "info")

    attack_path = [
        StageView(
            stage=stage,
            title=STAGE_LABEL[stage][0],
            subtitle=STAGE_LABEL[stage][1],
            narrative=step.narrative,
            fids=to_fids(step.findings),
            tone=_stage_tone(to_fids(step.findings), by_fid),
        )
        for stage in ATTACK_STAGES
        if (step := next((a for a in c.attack_path if a.stage == stage), None))
    ]

    roadmap_derived = not c.roadmap
    roadmap = (
        _derive_roadmap(findings)
        if roadmap_derived
        else [
            RoadmapPhaseView(
                phase=phase,
                rows=[
                    RoadmapRow(action=a.action, fids=to_fids(a.closes), owner=a.owner or "—", effort=a.effort)
                    for a in c.roadmap
                    if a.phase == phase
                ],
            )
            for phase in ROADMAP_PHASES
        ]
    )

    figures, figure_warnings = build_figures(load_diagrams(store), fid_by_vuln, {v.id: v.severity for v in ordered})

    confirmed = [v for v in state.vulns if v.status == "confirmed"]
    coverage = [
        CoverageView(id=r.id, title=r.title, list=r.list, status=r.status, note=r.note)
        for r in coverage_matrix(confirmed, state.endpoints, state.coverage)
    ]
    with_call = [v for v in state.vulns if v.drex_call]

    return ReportModel(
        client=c.client or state.target,
        target=state.target,
        engagement_id=c.engagement_id
        or f"{state.slug.upper()[:24]}-"
        f"{_dt.datetime.fromtimestamp(state.started / 1000, tz=_dt.UTC).strftime('%Y%m%d')}",
        version=c.version or "1.0 · Final",
        test_window=test_window(state.started, now),
        scope=list(state.scope) or [state.target],
        generated=fmt_day(now),
        year=_dt.datetime.fromtimestamp(now / 1000, tz=_dt.UTC).year,
        content=c,
        rating=RATING[worst],
        rating_segments=RATING_SEGMENTS[RATING[worst]],
        counts=counts,
        confirmed_count=len(findings),
        open_count=len(open_findings),
        hosts_in_scope=max(len(state.hosts), len(state.scope)),
        known_hosts=_known_hosts(state),
        endpoints_tested=len(state.endpoints),
        findings=findings,
        attack_path=attack_path,
        roadmap=roadmap,
        roadmap_derived=roadmap_derived,
        coverage=coverage,
        leads=sorted((v for v in state.vulns if v.status == "suspected"), key=lambda v: SEV_RANK[v.severity]),
        shallow=[
            f"{fid_by_vuln.get(v.id, v.id)} {v.title}: "
            + (
                "impact demonstrated; onward exploitation from it was not attempted"
                if v.depth == "impact"
                else f'validated to "{v.depth or "detected"}"; business impact not demonstrated'
            )
            for v, _why in shallow_findings(confirmed)
        ],
        untried_pivots=[f"{o.kind}: {o.target} — not attempted" for o in pivot.open_opportunities(state)],
        unlinked=[r for r in state.evidence if r.kind != "note" and not linked[r.file]],
        evidence_fids={r.name: [fid_by_vuln[i] for i in linked[r.file] if i in fid_by_vuln] for r in state.evidence},
        figures=figures,
        figure_warnings=figure_warnings,
        review_to_verify=sum(1 for v in with_call if not any(r.stage == "verifier" for r in v.reviews)),
        review_to_finalize=sum(
            1
            for v in with_call
            if any(r.stage == "verifier" for r in v.reviews) and not any(r.stage == "final" for r in v.reviews)
        ),
        retest=_retest_view(state, findings) if (state.retest or any(v.retest for v in state.vulns)) else None,
    )


def content_gaps(m: ReportModel) -> list[str]:
    """What the report still lacks — returned to the agent by the ``report`` tool (warn-only)."""
    g: list[str] = []
    c = m.content
    if not c.client:
        g.append("client name (report_content client=…) — the cover shows the target instead")
    if not c.synopsis:
        g.append("cover synopsis (report_content synopsis=…)")
    if not c.headline or not c.summary:
        g.append("executive headline + summary (report_content headline=… summary=…)")
    if not c.observations and m.findings:
        g.append("key observations (report_content observations=[{title, detail, findings:[id]}])")
    if not (c.priorities.immediate or c.priorities.short_term or c.priorities.structural):
        g.append("strategic priorities (report_content priorities={immediate, short_term, structural})")
    if not c.worked_well:
        g.append("what worked well (report_content worked_well=…)")
    if not c.coverage_notes:
        g.append("coverage notes (report_content coverage_notes=…)")
    if not c.limitations:
        g.append("limitations and assumptions (report_content limitations=…)")
    if not c.testers:
        g.append("testers — ask the operator (report_content testers=[{name, role}])")
    if not c.source_ips:
        g.append("source IP addresses the testing came from — ask the operator (report_content source_ips=[…])")
    if not c.attack_path and any(f.vuln.depth == "chained" for f in m.findings):
        g.append("attack path — chained findings exist but no stages were written (report_content attack_path=[…])")
    if m.roadmap_derived:
        g.append(
            "remediation roadmap — derived from each finding's first remediation step; "
            "write it with report_content roadmap=[…] for owners and effort"
        )
    for f in m.findings:
        if f.status == "false_positive":
            continue
        v = f.vuln
        missing = [
            *(["description"] if not v.description else []),
            *(["remediation"] if not v.remediation else []),
            *(["impact"] if not v.impact else []),
            *(["cwe"] if not v.cwe else []),
            *(["CVSS v4.0 vector (cvss=CVSS:4.0/…)"] if v.status == "confirmed" and not v.cvss else []),
            *(
                ["evidence (http_request/http_response, or an artifact linked with evidence vuln_id=…)"]
                if not v.http and not f.artifacts
                else []
            ),
        ]
        if missing:
            g.append(f"{f.fid} {v.title} ({v.id}): missing {', '.join(missing)}")
        if not missing and v.http and not f.artifacts:
            g.append(
                f"{f.fid} {v.title} ({v.id}): proof is only the request/response you typed — capture it "
                "with an evidence artifact (screenshot or response file linked with evidence vuln_id=…) "
                "so the report shows captured evidence"
            )
    g.extend(m.figure_warnings)
    if m.unlinked:
        g.append(
            f"{len(m.unlinked)} evidence artifact(s) not linked to any finding, so they appear only in "
            f"the appendix — link each with evidence vuln_id=<id> file=<name>: "
            f"{', '.join(e.name for e in m.unlinked)}"
        )
    for row in m.coverage:
        if row.status == "untested":
            g.append(f"coverage gap: {row.id} {row.title} — not exercised; absence of a finding proves nothing")
    for line in m.shallow:
        g.append(f"shallow finding: {line}")
    for line in m.untried_pivots:
        g.append(f"open pivot — {line}")
    if m.review_to_verify:
        g.append(
            f"{m.review_to_verify} decision-model call(s) still awaiting a verifier review (state_query what=review)"
        )
    if m.review_to_finalize:
        g.append(
            f"{m.review_to_finalize} decision-model call(s) still awaiting the final word (state_query what=review)"
        )
    return g


__all__ = [
    "ATTACK_STAGES",
    "ITEM_LIMITS",
    "NARRATIVE_LIMITS",
    "PHASE_TITLE",
    "RETEST_LABEL",
    "ROADMAP_PHASES",
    "SEVERITIES",
    "SEV_RANK",
    "STAGE_LABEL",
    "STATUS_LABEL",
    "ReportModel",
    "build_model",
    "content_gaps",
    "fmt_day",
    "register_findings",
    "report_status",
    "test_window",
]

"""The engagement record: one shared, file-backed model of a penetration test.

Everything the harness and its agents learn about a target — hosts, services, findings,
credentials, tested endpoints, footholds, coverage, evidence, pivots — lives in one
``State`` persisted to ``out/<slug>/state.json`` after every mutation. The store
(``store.py``) owns fresh-vs-resume semantics and history archiving; the tools
(``tools.py``) are thin wrappers over its mutators.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

Severity = Literal["info", "low", "medium", "high", "critical"]
VulnStatus = Literal["suspected", "confirmed", "false_positive"]
Depth = Literal["detected", "confirmed", "impact", "chained"]
StopKind = Literal["terminal", "rules", "disproved", "stuck", "time"]
#: The decision model's latest call on a finding (``decided_by`` says who decided).
DrexCall = Literal["confirmed", "dropped", "held", "downgraded"]
Reachability = Literal["internal", "public", "unknown"]
EvidenceKind = Literal["screenshot", "response", "loot", "note"]
PrimitiveKind = Literal["ssrf", "rce", "shell", "tunnel", "file_read", "db_read", "token", "xss"]


def now_ms() -> int:
    import time

    return int(time.time() * 1000)


class Service(BaseModel):
    port: int
    proto: str | None = None
    name: str | None = None
    version: str | None = None
    banner: str | None = None


class Host(BaseModel):
    ip: str
    hostname: str | None = None
    os: str | None = None
    services: list[Service] = Field(default_factory=list)
    #: ``internal`` = only reachable from inside the network (what pivots unlock).
    reachability: Reachability | None = None
    reach_why: str | None = None
    source: str | None = None


class Review(BaseModel):
    """One re-test of the decision model's call: a verifier's recommendation, or the final word."""

    stage: Literal["verifier", "final"]
    verdict: Literal["upheld", "overturned"]
    status: VulnStatus | None = None
    notes: str
    ts: int


class Cvss(BaseModel):
    version: Literal["4.0"] = "4.0"
    vector: str
    score: float
    band: Severity


class Retest(BaseModel):
    result: Literal["fixed", "not_fixed", "partial"]
    ts: int
    note: str | None = None


class Vuln(BaseModel):
    id: str
    host: str
    title: str
    severity: Severity = "info"
    status: VulnStatus = "suspected"
    #: p(real) from the decision model, [0, 1].
    confidence: float | None = None
    evidence: str | None = None
    service_port: int | None = None
    decided_by: Literal["agent", "drex"] | None = None
    drex_call: DrexCall | None = None
    reviews: list[Review] = Field(default_factory=list)
    depth: Depth | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    found_by: str | None = None
    owasp: list[str] = Field(default_factory=list)
    cvss: Cvss | None = None

    @field_validator("cvss", mode="before")
    @classmethod
    def _score_cvss_vector(cls, value: object) -> object:
        """A board written by an early build stored the vector as a string; score it on load, and
        drop it when it does not score (the finding keeps its severity either way)."""
        if isinstance(value, str):
            from intern.engagement.cvss import score_cvss

            scored = score_cvss(value)
            return scored.score if not isinstance(scored, Exception) else None
        return value

    retest: Retest | None = None
    rechecked_at: int | None = None
    remediated: bool | None = None
    recheck_note: str | None = None
    # Report detail, filled as the finding is developed.
    cwe: str | None = None
    description: str | None = None
    impact: list[str] = Field(default_factory=list)
    likelihood: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    http: HttpProof | None = None
    risk_accepted: bool = False


class HttpProof(BaseModel):
    """A raw request/response pair, secrets masked — the fallback when nothing can be captured."""

    request: str | None = None
    response: str | None = None
    caption: str | None = None


class Cred(BaseModel):
    id: str
    username: str | None = None
    secret: str | None = None
    source: str | None = None
    valid_for: str | None = None
    works: bool | None = None


class ProbeCell(BaseModel):
    """One cell of an api_probe matrix: an identity × method result on an endpoint."""

    identity: str
    method: str
    status: int
    allow: str | None = None


class Endpoint(BaseModel):
    id: str
    method: str
    url: str
    auth: str | None = None
    status: int | None = None
    tested: str
    result: str | None = None
    interesting: bool | None = None
    owasp: list[str] = Field(default_factory=list)
    sample: str | None = None
    params: list[str] = Field(default_factory=list)
    source: str | None = None
    matrix: list[ProbeCell] = Field(default_factory=list)
    ts: int


class Access(BaseModel):
    """A foothold: any access obtained (shell, creds that work, an authenticated session)."""

    id: str
    type: str
    target: str
    via: str | None = None
    cred_ref: str | None = None
    level: str | None = None
    reusable: bool | None = True
    notes: str | None = None
    ts: int


class CoverageMark(BaseModel):
    """An explicit account of a category: tested and found solid, or not applicable (with a reason)."""

    id: str
    status: Literal["tested", "na"]
    note: str | None = None
    ts: int
    by: str | None = None
    testers: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    file: str
    name: str
    kind: EvidenceKind = "note"
    description: str
    vuln_ids: list[str] = Field(default_factory=list)
    url: str | None = None
    caption: str | None = None
    ts: int


class Primitive(BaseModel):
    """What a finding or foothold lets the agent DO (the "key" half of a pivot)."""

    from_: str | None = Field(default=None, validation_alias=AliasChoices("from_", "from"), serialization_alias="from")

    id: str
    kind: PrimitiveKind
    host: str
    via: str
    notes: str | None = None
    ts: int


class PivotMark(BaseModel):
    """The recorded outcome of one pivot opportunity (its id is the opportunity's id)."""

    id: str
    status: Literal["success", "failed", "skipped"]
    note: str | None = None
    ts: int


class EventLine(BaseModel):
    ts: int
    kind: str
    text: str


class Observation(BaseModel):
    title: str
    detail: str
    findings: list[str] = Field(default_factory=list)


class Priorities(BaseModel):
    immediate: list[str] = Field(default_factory=list)
    short_term: list[str] = Field(default_factory=list)
    structural: list[str] = Field(default_factory=list)


class AttackStep(BaseModel):
    stage: Literal[
        "initial_access", "execution", "credential_access", "privilege_escalation", "lateral_movement", "impact"
    ]
    narrative: str
    findings: list[str] = Field(default_factory=list)


class RoadmapItem(BaseModel):
    phase: Literal["contain", "fix", "harden"]
    action: str
    closes: list[str] = Field(default_factory=list)
    owner: str | None = None
    effort: int | None = Field(default=None, ge=1, le=3)


class Tester(BaseModel):
    name: str
    role: str | None = None


class ReportContent(BaseModel):
    """The report's narrative, filled by the agent with ``report_content``."""

    client: str | None = None
    engagement_id: str | None = None
    version: str | None = None
    synopsis: str | None = None
    headline: str | None = None
    summary: str | None = None
    observations: list[Observation] = Field(default_factory=list)
    priorities: Priorities = Field(default_factory=Priorities)
    worked_well: str | None = None
    coverage_notes: str | None = None
    attack_path: list[AttackStep] = Field(default_factory=list)
    roadmap: list[RoadmapItem] = Field(default_factory=list)
    testers: list[Tester] = Field(default_factory=list)
    source_ips: list[str] = Field(default_factory=list)
    limitations: str | None = None


class RetestInfo(BaseModel):
    started: int


class State(BaseModel):
    """The whole engagement record; persisted to ``out/<slug>/state.json`` after every mutation."""

    slug: str
    target: str
    scope: list[str] = Field(default_factory=list)
    objective: str = ""
    started: int
    hosts: dict[str, Host] = Field(default_factory=dict)
    vulns: list[Vuln] = Field(default_factory=list)
    creds: list[Cred] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    access: list[Access] = Field(default_factory=list)
    coverage: list[CoverageMark] = Field(default_factory=list)
    report: ReportContent = Field(default_factory=ReportContent)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    primitives: list[Primitive] = Field(default_factory=list)
    pivots: list[PivotMark] = Field(default_factory=list)
    events: list[EventLine] = Field(default_factory=list)
    retest: RetestInfo | None = None
    made_with: str | None = None

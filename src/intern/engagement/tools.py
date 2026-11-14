"""The state tools: the engagement record as five model-facing tools.

``state_update`` writes everything the agent learns into the store (its response carries the
next-step nudges — the drill-down ladder, the OWASP/CVSS check, the pivot diff); ``state_query``
reads the board back in the shape each decision needs; ``set_engagement`` re-keys the record to
the operator's target; ``drex_triage`` and ``drex_decide`` route judgement through the decision
model. The tools hold no state of their own beyond the store (and the ``decisions`` client the
extension assigns at session start), so every session works on the same file-backed truth.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import AliasChoices, BaseModel, Field

from intern.decisions import DecisionClient
from intern.engagement import drex, pivot
from intern.engagement.depth import Depth, StopKind, drill_nudge, render_shallow_lines
from intern.engagement.models import PrimitiveKind, Service, Severity, State, VulnStatus
from intern.engagement.owasp import (
    coverage_matrix,
    normalize_owasp,
    owasp_title,
    render_coverage_markdown,
    render_gap_lines,
)
from intern.engagement.store import EngagementStore, RecordError, json_dumps, severity_drift_note, slugify
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

#: What playbooks exist for an OWASP id. The extension passes the real mapping; the stub ships none.
PlaybooksFor = Callable[[str], Sequence[str]]


def _no_playbooks(category_id: str) -> tuple[str, ...]:
    return ()


UpdateAction = Literal[
    "host", "vuln", "cred", "endpoint", "access", "recheck", "coverage", "primitive", "pivot", "retest", "review"
]
QueryWhat = Literal[
    "summary",
    "hosts",
    "vulns",
    "unvalidated",
    "creds",
    "endpoints",
    "access",
    "coverage",
    "shallow",
    "pivots",
    "prior",
    "review",
]

#: An access whose vantage is the host itself: develop it before anything else.
_HOSTISH_ACCESS = re.compile(r"shell|ssh|rce|rdp|smb|db|sql|container|host|root|admin-shell", re.IGNORECASE)


def _iso_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


class StateUpdateArgs(BaseModel):
    """One flat argument set; ``action`` selects which slice the tool reads."""

    action: UpdateAction
    # host
    ip: str | None = None
    hostname: str | None = None
    os: str | None = None
    services: list[Service] | None = None
    reachability: Literal["internal", "public"] | None = None
    # vuln (the report detail included: the client reads it back)
    id: str | None = None
    host: str | None = None
    title: str | None = None
    severity: Severity | None = None
    status: VulnStatus | None = None
    evidence: str | None = None
    service_port: int | None = None
    owasp: list[str] | None = None
    cvss: str | None = Field(
        default=None,
        description="CVSS v4.0 base vector, e.g. CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/… — scored for you",
    )
    depth: Depth | None = None
    stop_reason: str | None = None
    stop_kind: StopKind | None = None
    cwe: str | None = None
    description: str | None = None
    impact: list[str] | None = None
    likelihood: list[str] | None = None
    remediation: list[str] | None = None
    references: list[str] | None = None
    http_request: str | None = None
    http_response: str | None = None
    http_caption: str | None = None
    risk_accepted: bool | None = None
    # cred
    username: str | None = None
    secret: str | None = None
    source: str | None = None
    valid_for: str | None = None
    works: bool | None = None
    # endpoint
    method: str | None = None
    url: str | None = None
    auth: str | None = None
    http_status: int | None = None
    result: str | None = None
    interesting: bool | None = None
    sample: str | None = None
    params: list[str] | None = None
    # access
    access_type: str | None = None
    target: str | None = None
    via: str | None = None
    cred_ref: str | None = None
    level: str | None = None
    reusable: bool | None = None
    # recheck
    remediated: bool | None = None
    # coverage
    coverage_status: Literal["tested", "na"] | None = None
    # primitive
    kind: PrimitiveKind | None = None
    from_: str | None = Field(default=None, validation_alias=AliasChoices("from", "from_"), serialization_alias="from")
    # pivot / retest / review
    pivot_result: Literal["success", "failed", "skipped"] | None = None
    retest_result: Literal["fixed", "not_fixed", "partial"] | None = None
    verdict: Literal["upheld", "overturned"] | None = None
    review_status: VulnStatus | None = None
    notes: str | None = None


class StateUpdateTool(Tool):
    """Records into the store; the response text carries the nudges (drill down, tag, pivot)."""

    name = "state_update"
    description = (
        "Record what you learn into engagement state. action=host (ip, hostname?, os?, services?[{port,"
        "proto?,name?,version?,banner?}], reachability=internal|public); action=vuln (host, title, "
        "severity, evidence, service_port?, status?, owasp?[], cvss? = CVSS v4.0 vector (scored for "
        "you), depth?, stop_reason? with stop_kind, plus the report detail: cwe, description, impact[], "
        "likelihood[], remediation[], references[], http_request, http_response, http_caption, "
        "risk_accepted — or id + only the fields to change, to advance a recorded finding up the "
        "drill-down ladder detected→confirmed→impact→chained); action=cred (username?, secret?, "
        "source?, valid_for?, works?); action=endpoint (map an endpoint you tested: method, url, "
        "auth?, http_status?, result?, interesting?, owasp?[] = the categories tested on it, title?, "
        "sample?, params?, source?) — do this for EVERY endpoint you probe so coverage is visible; "
        "action=coverage (owasp[], coverage_status=tested|na, notes = what you tested / why it doesn't "
        "apply); action=primitive (kind=ssrf|rce|shell|tunnel|file_read|db_read|token|xss, host, via = "
        "how to use it e.g. 'GET /api/fetch?url=', from = the finding or foothold that grants it); "
        "action=pivot (id = opportunity id, pivot_result=success|failed|skipped, notes); action=access "
        "(record a foothold you obtained so it can be reused: access_type e.g. ssh|web-admin|db|shell, "
        "target, via?, cred_ref?, level?, reusable?, notes?); action=retest (after the client's fixes: "
        "id, retest_result=fixed|not_fixed|partial, notes = what you observed); action=recheck (final "
        "LIVE re-verification of a finding: id (or host+title), remediated=true|false, notes?); "
        "action=review (review a call drex made — confirmed, dropped as a false positive, held or "
        "downgraded: id, verdict=upheld|overturned, review_status = the status the evidence supports "
        "when overturning, notes = what you re-tested live; the verifier's recommendation is recorded "
        "first, then the main agent's final call applies it). Call it IMMEDIATELY after discovering "
        "anything."
    )
    Args = StateUpdateArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, store: EngagementStore, playbooks_for: PlaybooksFor = _no_playbooks) -> None:
        self.store = store
        self._playbooks_for = playbooks_for

    async def run(self, args: StateUpdateArgs, ctx: ToolContext) -> ToolOutput:
        before = {o.id for o in pivot.open_opportunities(self.store.state)}
        out = self._record(args)
        if out.status != "ok":
            return out
        opened = [o for o in pivot.open_opportunities(self.store.state) if o.id not in before]
        if not opened:
            return out
        count = len(opened)
        note = (
            f"\n→ PIVOT: {count} new opportunit{'y' if count == 1 else 'ies'} — connect the dots before "
            f"moving on (state_query what=pivots; record outcomes with state_update action=pivot):\n"
            f"{pivot.render_opportunities(opened, 8)}"
        )
        return out.model_copy(update={"content": out.content + note})

    def _record(self, args: StateUpdateArgs) -> ToolOutput:
        actions: dict[str, Callable[[StateUpdateArgs], ToolOutput]] = {
            "host": self._action_host,
            "vuln": self._action_vuln,
            "cred": self._action_cred,
            "endpoint": self._action_endpoint,
            "access": self._action_access,
            "recheck": self._action_recheck,
            "coverage": self._action_coverage,
            "primitive": self._action_primitive,
            "pivot": self._action_pivot,
            "retest": self._action_retest,
            "review": self._action_review,
        }
        try:
            return actions[args.action](args)
        except RecordError as error:
            return fail(str(error), "Fix the arguments from the message above and call state_update again.")

    def _action_host(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.ip:
            return fail("ip required", "Call state_update action=host with the host's ip.", argument="ip")
        self.store.add_host(
            args.ip,
            hostname=args.hostname,
            os_name=args.os,
            services=args.services,
            reachability=args.reachability,
        )
        return ToolOutput(content=f"recorded host {args.ip}")

    def _action_vuln(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.id and not (args.host and args.title and args.severity):
            missing = [n for n, v in (("host", args.host), ("title", args.title), ("severity", args.severity)) if not v]
            return fail(
                f"{', '.join(missing)} required for a new finding (or pass id to update a recorded one)",
                'title is a short name for the finding, e.g. "SSRF in the link preview service" '
                "(you gave "
                + ", ".join(f"{n}={getattr(args, n)}" for n in ("host", "severity") if getattr(args, n))
                + "). Resend the same call with it added.",
                argument=missing[0] if missing else "host",
            )
        v = self.store.add_vuln(
            {name: value for name, value in args.model_dump(by_alias=True).items() if value is not None}
        )
        lines = [f"recorded vuln {v.id}: [{v.severity}] {v.title} ({v.status}, depth={v.depth})"]
        if v.owasp:
            listed = " · ".join(f"{tag} {owasp_title(tag)}" for tag in v.owasp)
            lines.append(f"OWASP: {listed} — check these names fit the finding (2025 numbering).")
        if v.cvss:
            line = f"CVSS 4.0: {v.cvss.score} ({v.cvss.band})"
            if drift := severity_drift_note(v):
                line += f" — {drift}"
            lines.append(line)
        elif v.status == "confirmed":
            lines.append("CVSS 4.0: no vector — add cvss=CVSS:4.0/… (base metrics).")
        if nudge := drill_nudge(v, self._playbooks_for):
            lines.append(f"→ {nudge}")
        return ToolOutput(content="\n".join(lines))

    def _action_cred(self, args: StateUpdateArgs) -> ToolOutput:
        cred = self.store.add_cred(
            username=args.username, secret=args.secret, source=args.source, valid_for=args.valid_for, works=args.works
        )
        return ToolOutput(content=f"recorded credential {cred.id}")

    def _action_endpoint(self, args: StateUpdateArgs) -> ToolOutput:
        if not (args.method and args.url):
            return fail("method and url required", "Call state_update action=endpoint with method and url.")
        endpoint = self.store.add_endpoint(
            method=args.method,
            url=args.url,
            auth=args.auth,
            status=args.http_status,
            result=args.result,
            interesting=args.interesting,
            owasp=args.owasp,
            title=args.title,
            sample=args.sample,
            params=args.params,
            source=args.source,
        )
        return ToolOutput(content=f"recorded endpoint {endpoint.id}: {endpoint.method} {endpoint.url}")

    def _action_access(self, args: StateUpdateArgs) -> ToolOutput:
        if not (args.access_type and args.target):
            return fail("access_type and target required", "Record what access, on what target.")
        access = self.store.add_access(
            access_type=args.access_type,
            target=args.target,
            via=args.via,
            cred_ref=args.cred_ref,
            level=args.level,
            reusable=args.reusable if args.reusable is not None else True,
            notes=args.notes,
        )
        # A foothold is a START, not a finish — fire the develop loop, matched to the kind of access.
        if _HOSTISH_ACCESS.search(access.type):
            develop = (
                "DEVELOP the foothold: id, whoami/priv, processes, localhost-only ports, "
                "configs/.env/keys/history, neighbors (ARP, subnets), then escalate/pivot"
            )
        else:
            develop = (
                "DEVELOP from the new vantage: sideways (other users/tenants objects, IDOR), "
                "up (admin/role endpoints, password reset), behind (backends, secrets, integrations)"
            )
        return ToolOutput(content=f"recorded access {access.id}: {access.type} on {access.target}\n→ {develop}")

    def _action_recheck(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.id and not (args.host and args.title):
            return fail(
                "pass id (preferred) or host+title to identify the finding",
                "state_query what=vulns lists the ids.",
                argument="id",
            )
        if not isinstance(args.remediated, bool):
            return fail(
                "remediated (true=fixed during the test, false=still open) required",
                "Say whether the finding was fixed.",
                argument="remediated",
            )
        ref = args.id or self._vuln_ref(args.host, args.title)
        if ref is None:
            return fail("no matching finding", "Check the id, or the exact host+title.", argument="id")
        v = self.store.recheck_vuln(ref, remediated=args.remediated, notes=args.notes)
        state = "remediated" if v.remediated else "open"
        return ToolOutput(content=f"re-checked {v.id}: {state} as of now — {v.title}")

    def _action_coverage(self, args: StateUpdateArgs) -> ToolOutput:
        ids, unknown = normalize_owasp(args.owasp)
        if unknown or not ids:
            detail = f" (unknown: {', '.join(unknown)})" if unknown else ""
            return fail(
                f"owasp=[A01..A10 / API1..API10] required{detail}",
                "Name the OWASP category this coverage accounts for.",
                argument="owasp",
            )
        if not args.coverage_status:
            return fail("coverage_status=tested|na required", "Say whether it was tested or cannot apply.")
        note = (args.notes or "").strip()
        if not note:
            return fail(
                "notes required — say what you tested, or why the category doesn't apply to this target",
                "The report shows this note in the coverage table.",
                argument="notes",
            )
        self.store.mark_coverage(ids, status=args.coverage_status, note=note)
        listed = ", ".join(f"{id_} {owasp_title(id_)}" for id_ in ids)
        return ToolOutput(
            content=f"coverage recorded: {listed} = {args.coverage_status} — "
            "check the category name matches your note (2025 numbering)"
        )

    def _action_primitive(self, args: StateUpdateArgs) -> ToolOutput:
        if not (args.kind and args.host and args.via):
            return fail(
                "primitive needs kind, host and via (how to use it, e.g. 'GET /api/fetch?url=')",
                "Say what it lets you do, where, and how.",
            )
        primitive = self.store.add_primitive(
            kind=args.kind, host=args.host, via=args.via, from_=args.from_, notes=args.notes
        )
        return ToolOutput(
            content=f"recorded primitive {primitive.id}: {primitive.kind} on {primitive.host} ({primitive.via})"
        )

    def _action_pivot(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.id:
            return fail("id required: the pivot opportunity being recorded", "state_query what=pivots lists them.")
        if not args.pivot_result:
            return fail("pivot_result=success|failed|skipped required", "Record how the pivot went.")
        self.store.mark_pivot(args.id, status=args.pivot_result, note=args.notes)
        tip = (
            " Record what it reached (hosts, endpoints, creds, access) — each one opens the next round of pivots."
            if args.pivot_result == "success"
            else ""
        )
        return ToolOutput(content=f"pivot {args.id} → {args.pivot_result}.{tip}")

    def _action_retest(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.id:
            return fail("retest needs id", "state_query what=vulns lists them.", argument="id")
        if not args.retest_result:
            return fail("retest_result=fixed|not_fixed|partial required", "Record the verdict on the client's fix.")
        self.store.retest_vuln(args.id, result=args.retest_result, notes=args.notes or "")
        return ToolOutput(
            content=f"retest recorded for {args.id}: {args.retest_result.replace('_', ' ')} — "
            f"now capture proof of this verdict (evidence for {args.id})"
        )

    def _action_review(self, args: StateUpdateArgs) -> ToolOutput:
        if not args.id:
            return fail("review needs id", "state_query what=review lists drex's calls.", argument="id")
        if args.verdict not in ("upheld", "overturned"):
            return fail("review needs verdict=upheld|overturned", "Say whether the call holds up.", argument="verdict")
        v = self.store.vuln_by_ref(args.id)
        if v is None:
            return fail(f"no finding {args.id}", "state_query what=vulns lists the ids.", argument="id")
        # The verifier recommends first; once it has, the same call is the main agent's final word.
        stage: Literal["verifier", "final"] = (
            "final" if any(review.stage == "verifier" for review in v.reviews) else "verifier"
        )
        _, message = self.store.review_drex_call(
            args.id, stage, verdict=args.verdict, status=args.review_status, notes=args.notes
        )
        return ToolOutput(content=message)

    def _vuln_ref(self, host: str | None, title: str | None) -> str | None:
        if not (host and title):
            return None
        v = next((x for x in self.store.state.vulns if x.host == host and x.title == title), None)
        return v.id if v else None


class StateQueryArgs(BaseModel):
    what: QueryWhat = "summary"


class StateQueryTool(Tool):
    """Reads the board back in the shape each decision needs."""

    name = "state_query"
    description = (
        "Read engagement state. what=summary|hosts|vulns|unvalidated|creds|endpoints|access|coverage|"
        "shallow|pivots|prior|review (default summary). 'pivots' lists what your findings/footholds "
        "let you do (primitives), the internal assets you know of, and every OPEN pivot opportunity "
        "pairing them (SSRF → internal host, shell → neighbour, credential → other service, token → "
        "other API) — work through them recursively. 'coverage' is the OWASP Top 10:2025 + API Top "
        "10:2023 matrix (which categories have findings, were tested, are n/a, or are still UNTESTED, "
        "with the playbook for each gap). 'shallow' lists findings not yet driven to impact (or "
        "HIGH/CRIT impact not chained) with the next step for each. 'prior' shows the archived prior "
        "run on this object (findings/endpoints you can resume with set_engagement resume=true). "
        "'review' lists the calls drex made (confirmed, dropped, held, downgraded) still to verify or "
        "finalize before the report."
    )
    Args = StateQueryArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})

    def __init__(self, store: EngagementStore, playbooks_for: PlaybooksFor = _no_playbooks) -> None:
        self.store = store
        self._playbooks_for = playbooks_for

    async def run(self, args: StateQueryArgs, ctx: ToolContext) -> ToolOutput:
        s = self.store.state
        if args.what == "summary":
            return ToolOutput(content=json_dumps(self.store.summary()))
        if args.what == "hosts":
            return ToolOutput(content=json_dumps([host.model_dump() for host in s.hosts.values()]))
        if args.what == "creds":
            return ToolOutput(content=json_dumps([cred.model_dump() for cred in s.creds]))
        if args.what == "endpoints":
            return ToolOutput(content=json_dumps([endpoint.model_dump() for endpoint in s.endpoints]))
        if args.what == "access":
            return ToolOutput(content=json_dumps([foothold.model_dump() for foothold in s.access]))
        if args.what == "coverage":
            rows = coverage_matrix(s.vulns, s.endpoints, s.coverage)
            text = render_coverage_markdown(rows)
            gaps = render_gap_lines(rows, self._playbooks_for)
            if gaps:
                text += "\n\nTo close:\n" + "\n".join(f"- {gap}" for gap in gaps)
            return ToolOutput(content=text)
        if args.what == "pivots":
            return ToolOutput(content=self._pivots())
        if args.what == "shallow":
            return ToolOutput(content="\n".join(render_shallow_lines(s.vulns)))
        if args.what == "review":
            return ToolOutput(content=drex.describe_review_queue(self.store))
        if args.what == "prior":
            return ToolOutput(content=self._prior())
        if args.what == "unvalidated":
            return ToolOutput(
                content=json_dumps(
                    [
                        {
                            "id": v.id,
                            "host": v.host,
                            "title": v.title,
                            "severity": v.severity,
                            "evidence": v.evidence,
                        }
                        for v in self.store.unvalidated()
                    ]
                )
            )
        return ToolOutput(content=json_dumps([v.model_dump(by_alias=True) for v in s.vulns]))

    def _pivots(self) -> str:
        s = self.store.state
        open_ = pivot.open_opportunities(s)
        internal = [asset for asset in pivot.known_assets(s.hosts, s.endpoints) if asset.reach == "internal"]
        primitives = [
            f"- {x.id} {x.kind} on {x.host}: {x.via}" + (f" (from {x.from_})" if x.from_ else "") for x in s.primitives
        ]
        sections = [
            "Primitives (what you can do):\n" + ("\n".join(primitives) or "- none yet"),
            f"Internal assets ({len(internal)}): "
            + (", ".join(f"{asset.name} [{asset.why}]" for asset in internal) or "none yet"),
            f"Open opportunities ({len(open_)}):\n{pivot.render_opportunities(open_, 40)}",
        ]
        if s.pivots:
            outcomes = [f"- {mark.id} {mark.status}" + (f": {mark.note}" if mark.note else "") for mark in s.pivots]
            sections.append("Recorded outcomes:\n" + "\n".join(outcomes))
        return "\n\n".join(sections)

    def _prior(self) -> str:
        prior = self.store.latest_prior()
        if prior is None:
            return "no prior run archived for this object (nothing to resume)."
        try:
            state = State.model_validate_json(Path(str(prior["file"])).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return "no prior run archived for this object (nothing to resume)."
        return json_dumps(
            {
                "when": _iso_date(state.started),
                "vulns": [
                    {"host": v.host, "title": v.title, "severity": v.severity, "status": v.status} for v in state.vulns
                ],
                "endpoints": len(state.endpoints),
                "creds": len(state.creds),
                "access": len(state.access),
            }
        )


class SetEngagementArgs(BaseModel):
    target: str
    targets: list[str] | None = None
    objective: str | None = None
    resume: bool = Field(
        default=False, description="load the archived prior run on this object back into the live board"
    )


class SetEngagementTool(Tool):
    """Re-keys the record to the operator's target (fresh reuse, or resume)."""

    name = "set_engagement"
    description = (
        "Record the engagement target and objective (use when the operator names a target to test). "
        "Re-keys to the object's folder (out/<slug>): by default a FRESH session that REUSES that "
        "object's prior recon/creds/footholds (so you don't redo work) with a clean findings board; "
        "the prior run is archived. Set resume=true (or when the operator says 'resume'/'continue') "
        "to load the prior findings back into the live board. The optional targets list documents the "
        "hosts in play — it is not an enforced boundary."
    )
    Args = SetEngagementArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, store: EngagementStore) -> None:
        self.store = store

    async def run(self, args: SetEngagementArgs, ctx: ToolContext) -> ToolOutput:
        same_slug = slugify(args.target) == self.store.state.slug
        resumed, prior = "", ""
        try:
            self.store.switch_target(args.target, args.objective, args.resume)
            # the store's switch keeps the loaded state's target/scope on a slug change; re-key both
            # to what the operator named.
            self.store.configure(target=args.target, scope=args.targets or [args.target], objective=args.objective)
            # switch_target fully loads when it SWITCHES objects with resume; on the same object the
            # fold happens here.
            if args.resume and same_slug:
                findings, endpoints = self.store.load_prior()
                resumed = f" (resumed prior: +{findings} finding(s), +{endpoints} endpoint(s))"
            elif args.resume:
                resumed = " (resumed prior session)"
            if not args.resume and self.store.prior_meta:
                meta = self.store.prior_meta
                when, findings = meta["when"], meta["findings"]
                assert isinstance(when, int) and isinstance(findings, int)
                prior = (
                    f" — prior run from {_iso_date(when)} with {findings} finding(s) archived "
                    "(reusing recon/creds/footholds; say resume to load it)"
                )
        except RecordError as error:
            return fail(str(error), "set_engagement was not applied; fix the state the message names.")
        return ToolOutput(
            content=f"engagement set: target={self.store.state.target} dir={self.store.dir} "
            f"targets=[{', '.join(self.store.state.scope)}]{resumed}{prior}"
        )


class DrexTriageArgs(BaseModel):
    confirm_at: float = drex.CONFIRM_AT
    drop_at: float = drex.DROP_AT
    impact_at: float = drex.IMPACT_AT


class DrexTriageTool(Tool):
    """Both decision gates over every finding that still owes a verdict."""

    name = "drex_triage"
    description = (
        "Validate findings with the decision model (drex) BEFORE trusting them. Two gates: (1) "
        "real-vs-FP — p(real) ≥ 0.85 auto-confirms, ≤ 0.30 drops as a false positive, in between is "
        "left for your manual judgement; (2) for HIGH/CRITICAL it ALSO asks p(impact demonstrated at "
        "that severity) — a finding that is real but whose evidence doesn't prove the claimed severity "
        "(theory, 'bypass possible' with no demo, a trivially-defeated control) is HELD at suspected, "
        "not confirmed. It also re-checks already-confirmed HIGH/CRITICAL findings and DOWNGRADES "
        "overclaimed ones — so run it again after you add or change evidence on a high finding. Run "
        "after a round of enumeration and before writing the report."
    )
    Args = DrexTriageArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write", "network"})

    def __init__(self, store: EngagementStore) -> None:
        self.store = store
        self.decisions: DecisionClient | None = None
        """The session's decision client; the extension assigns it when the session starts."""

    async def run(self, args: DrexTriageArgs, ctx: ToolContext) -> ToolOutput:
        if self.decisions is None:
            return fail(
                "drex is not available in this session",
                "Validate the findings yourself: confirm only what your evidence proves, mark the rest suspected.",
            )
        total = len(self.store.unvalidated())
        counts, lines = await drex.triage(
            self.store, self.decisions, confirm_at=args.confirm_at, drop_at=args.drop_at, impact_at=args.impact_at
        )
        content = (
            f"drex triaged {total} finding(s): {counts.confirmed} confirmed, {counts.dropped} dropped as FP, "
            f"{counts.manual} left for you, {counts.held} held (real but impact unproven), "
            f"{counts.downgraded} downgraded (overclaimed HIGH/CRIT).\n" + "\n".join(lines)
        )
        return ToolOutput(content=content)


class DrexDecideArgs(BaseModel):
    question: str = Field(description="a single yes/no question about whether to proceed/deepen/trust")
    context: str | None = Field(default=None, description="what you know — state, the finding, the vector")


class DrexDecideTool(Tool):
    """One calibrated yes/no the agent would otherwise guess at."""

    name = "drex_decide"
    description = (
        "Ask the decision model (drex) ONE yes/no question about the current situation and get a "
        "calibrated probability p(yes) in [0,1] in a single fast pass. Use it at decision points "
        "instead of guessing — especially: 'should I go deeper on this host/vector — is there more "
        "worth extracting here?' (depth decision), 'which of these vectors is most likely "
        "exploitable?' (ask per vector, pursue highest), 'is this evidence enough to call it "
        "CONFIRMED?', 'could this result be a network/middlebox artifact rather than the target's "
        "real service?'. Returns p and a verdict: yes ≥ 0.6, no ≤ 0.4, else uncertain. When it says "
        "go deeper — or is uncertain — go deeper; when in doubt on depth, go deeper anyway. Pass a "
        "concise context (what you know) and the question."
    )
    Args = DrexDecideArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "network"})

    def __init__(self, store: EngagementStore) -> None:
        self.store = store
        self.decisions: DecisionClient | None = None
        """The session's decision client; the extension assigns it when the session starts."""

    async def run(self, args: DrexDecideArgs, ctx: ToolContext) -> ToolOutput:
        if self.decisions is None:
            return fail("drex is not available in this session", "Decide yourself — and bias toward going deeper.")
        probability, verdict = await drex.decide(self.store, self.decisions, args.question, args.context)
        return ToolOutput(content=f"drex: p(yes)={probability:.2f} → {verdict}")


__all__ = [
    "DrexDecideArgs",
    "DrexDecideTool",
    "DrexTriageArgs",
    "DrexTriageTool",
    "PlaybooksFor",
    "SetEngagementArgs",
    "SetEngagementTool",
    "StateQueryArgs",
    "StateQueryTool",
    "StateUpdateArgs",
    "StateUpdateTool",
]

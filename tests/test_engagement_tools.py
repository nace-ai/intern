"""The engagement state tools against a real store in a temporary directory."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from intern.decisions import Answers, DecisionClient, NoulAnswer, Question
from intern.engagement import drex
from intern.engagement.prompt import GUIDE
from intern.engagement.store import EngagementStore, slugify
from intern.engagement.tools import (
    DrexDecideTool,
    DrexTriageTool,
    SetEngagementTool,
    StateQueryTool,
    StateUpdateTool,
)
from intern.executor import LocalExecutor
from intern.tools.base import ToolContext, ToolOutput

CVSS_VECTOR = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"


@pytest.fixture
def store(tmp_path: Path) -> EngagementStore:
    return EngagementStore(tmp_path, slugify("example.com"), "example.com")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, executor=LocalExecutor(), abort=asyncio.Event(), max_output_chars=100_000)


async def call(tool, ctx: ToolContext, **arguments) -> ToolOutput:
    parsed = tool.parse(arguments)
    if isinstance(parsed, ToolOutput):
        return parsed
    return await tool.run(parsed, ctx)


class FakeDrex:
    """Answers the two gates from a script keyed by finding title; any other ask gets ``otherwise``."""

    name = "fake-drex"

    def __init__(
        self,
        real: dict[str, float] | None = None,
        impact: dict[str, float] | None = None,
        otherwise: float = 0.5,
    ) -> None:
        self.real = real or {}
        self.impact = impact or {}
        self.otherwise = otherwise

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        question = questions["q"]
        title = state.split("\n", 1)[0].removeprefix("finding: ") if state.startswith("finding: ") else ""
        if question == drex.FALSE_POSITIVE_QUESTION:
            return Answers(answers={"q": NoulAnswer(noul=self.real.get(title, self.otherwise))})
        if question == drex.IMPACT_QUESTION:
            return Answers(answers={"q": NoulAnswer(noul=self.impact.get(title, 1.0))})
        return Answers(answers={"q": NoulAnswer(noul=self.otherwise)})


def client(fake: FakeDrex) -> DecisionClient:
    return DecisionClient(fake, lambda event: None)


def vuln(store: EngagementStore, part: str):
    return next(v for v in store.state.vulns if part in v.title)


async def test_vuln_create_then_update_merges_without_losing_ground(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store, playbooks_for=lambda tag: ("access-control",) if tag == "A01" else ())
    created = await call(
        tool,
        ctx,
        action="vuln",
        host="10.0.0.5",
        title="IDOR in GET /api/users/{id}",
        severity="medium",
        evidence="fetched another tenant's user record",
        owasp=["A01"],
        depth="confirmed",
    )
    assert created.status == "ok"
    v = store.state.vulns[0]
    assert created.content.splitlines()[0] == (
        f"recorded vuln {v.id}: [medium] IDOR in GET /api/users/{{id}} (suspected, depth=confirmed)"
    )
    assert "OWASP: A01 Broken Access Control · " not in created.content  # one tag, one title
    assert "OWASP: A01 Broken Access Control — check these names fit" in created.content
    assert "load_playbook surface=access-control" in created.content  # the playbook the gap suggests

    updated = await call(tool, ctx, action="vuln", id=v.id, owasp=["api1"], depth="detected", status="confirmed")
    assert updated.status == "ok"
    v = store.state.vulns[0]
    assert v.status == "confirmed"
    assert v.depth == "confirmed"  # depth never drops; a confirmed finding floors at confirmed
    assert v.owasp == ["A01", "API1"]  # tags union, ids normalized
    assert "A01 Broken Access Control · API1 Broken Object Level Authorization" in updated.content
    assert "no vector — add cvss=CVSS:4.0/… (base metrics)" in updated.content  # confirmed without a vector
    assert f"DRILL DOWN ({v.id} at confirmed)" in updated.content

    unknown = await call(tool, ctx, action="vuln", id="v9-nope", severity="low")
    assert unknown.status == "error" and "no finding v9-nope" in unknown.content


async def test_vuln_cvss_line_scores_and_flags_drift(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    result = await call(
        tool, ctx, action="vuln", host="10.0.0.5", title="Unauthenticated admin panel", severity="low", cvss=CVSS_VECTOR
    )
    assert result.status == "ok"
    v = store.state.vulns[0]
    assert v.cvss is not None and v.cvss.score >= 9 and v.cvss.band == "critical"
    assert "CVSS 4.0: 9." in result.content and "more than one step from the CVSS band (critical)" in result.content

    broken = await call(
        tool, ctx, action="vuln", host="10.0.0.5", title="Bad vector", severity="low", cvss="CVSS:4.0/AV:N"
    )
    assert broken.status == "error" and "missing base metric" in broken.content


async def test_stop_reason_without_stop_kind_is_rejected(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    rejected = await call(
        tool,
        ctx,
        action="vuln",
        host="10.0.0.5",
        title="Open redirect on login",
        severity="low",
        stop_reason="redirect target is whitelisted, nothing further to gain",
    )
    assert rejected.status == "error" and "stop_reason needs stop_kind" in rejected.content
    accepted = await call(
        tool,
        ctx,
        action="vuln",
        host="10.0.0.5",
        title="Open redirect on login",
        severity="low",
        stop_reason="redirect target is whitelisted, nothing further to gain",
        stop_kind="terminal",
    )
    assert accepted.status == "ok"
    assert "DRILL DOWN" not in accepted.content  # a stopped finding owes no more depth


async def test_coverage_rejects_excuses_and_unknown_ids(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    excuse = await call(
        tool, ctx, action="coverage", owasp=["A04"], coverage_status="na", notes="no time left to test crypto"
    )
    assert excuse.status == "error" and "n/a means the category can't apply" in excuse.content
    unnamed = await call(tool, ctx, action="coverage", coverage_status="tested", notes="reviewed the auth flows")
    assert unnamed.status == "error" and "owasp" in unnamed.content
    unknown = await call(tool, ctx, action="coverage", owasp=["B99"], coverage_status="tested", notes="x")
    assert unknown.status == "error" and "B99" in unknown.content
    bare = await call(tool, ctx, action="coverage", owasp=["A04"], coverage_status="tested", notes="  ")
    assert bare.status == "error" and "notes required" in bare.content
    marked = await call(
        tool,
        ctx,
        action="coverage",
        owasp=["a4"],
        coverage_status="na",
        notes="TLS everywhere enforced; no external crypto surface to test",
    )
    assert marked.status == "ok"
    assert marked.content.startswith("coverage recorded: A04 Cryptographic Failures = na")


async def test_endpoint_updates_dedupe_on_method_and_url(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    first = await call(
        tool,
        ctx,
        action="endpoint",
        method="get",
        url="https://example.com/api/users",
        http_status=200,
        result="json array of users",
        owasp=["api1"],
        title="list users",
    )
    assert first.status == "ok"
    second = await call(
        tool,
        ctx,
        action="endpoint",
        method="GET",
        url="https://example.com/api/users",
        http_status=500,
        interesting=True,
        result="stack trace leaked",
    )
    assert second.status == "ok"
    assert len(store.state.endpoints) == 1
    endpoint = store.state.endpoints[0]
    assert endpoint.method == "GET" and endpoint.status == 500 and endpoint.interesting is True
    assert endpoint.owasp == ["API1"] and endpoint.tested == "list users"
    assert second.content.startswith(f"recorded endpoint {endpoint.id}: GET https://example.com/api/users")


async def test_access_develop_nudge_matches_the_vantage(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    shell = await call(tool, ctx, action="access", access_type="ssh shell", target="10.0.0.5", level="www-data")
    assert shell.status == "ok"
    assert "DEVELOP the foothold" in shell.content
    assert "whoami/priv" in shell.content and "neighbors (ARP, subnets)" in shell.content
    web = await call(tool, ctx, action="access", access_type="web-admin session", target="example.com", notes="jwt")
    assert "DEVELOP from the new vantage" in web.content
    assert "IDOR" in web.content and "password reset" in web.content
    shell_access = store.state.access[0]
    assert shell.content.startswith(f"recorded access {shell_access.id}: ssh shell on 10.0.0.5")
    # a shell foothold is a primitive: the pivot engine can pair it with internal assets
    assert store.state.primitives and store.state.primitives[0].kind == "shell"


async def test_cred_next_to_a_login_service_opens_a_pivot_note(store: EngagementStore, ctx: ToolContext):
    tool = StateUpdateTool(store)
    host = await call(tool, ctx, action="host", ip="10.0.0.5", services=[{"port": 22, "name": "ssh"}])
    assert host.status == "ok" and "→ PIVOT:" not in host.content
    cred = await call(tool, ctx, action="cred", username="deploy", source="config dump", works=True)
    assert cred.status == "ok"
    assert "→ PIVOT: 1 new opportunity — connect the dots" in cred.content
    assert "[cred→reuse]" in cred.content and "try credential" in cred.content

    query = StateQueryTool(store)
    pivots = (await call(query, ctx, what="pivots")).content
    assert "Internal assets (1): 10.0.0.5" in pivots
    assert "Open opportunities (1):" in pivots

    import intern.engagement.pivot as pivot_engine

    oid = pivot_engine.open_opportunities(store.state)[0].id
    skipped = await call(tool, ctx, action="pivot", id=oid, pivot_result="skipped", notes="out of scope")
    assert skipped.status == "ok"
    assert skipped.content == f"pivot {oid} → skipped."  # recording an outcome opens nothing

    await call(tool, ctx, action="cred", username="svc-backup", works=True)
    oid2 = pivot_engine.open_opportunities(store.state)[0].id
    success = await call(tool, ctx, action="pivot", id=oid2, pivot_result="success")
    assert success.status == "ok"
    assert success.content == (
        f"pivot {oid2} → success. Record what it reached (hosts, endpoints, creds, access) "
        "— each one opens the next round of pivots."
    )
    unknown = await call(tool, ctx, action="pivot", id="pdeadbeef", pivot_result="failed")
    assert unknown.status == "error" and "no pivot opportunity pdeadbeef" in unknown.content
    outcomes = (await call(query, ctx, what="pivots")).content
    assert f"Recorded outcomes:\n- {oid} skipped: out of scope\n- {oid2} success" in outcomes


async def test_state_query_modes(store: EngagementStore, ctx: ToolContext):
    update = StateUpdateTool(store)
    query = StateQueryTool(store, playbooks_for=lambda tag: ("auth-session",))
    await call(
        update,
        ctx,
        action="host",
        ip="10.0.0.5",
        hostname="app.internal",
        services=[{"port": 8080, "name": "http"}],
    )
    await call(update, ctx, action="vuln", host="10.0.0.5", title="Reflected XSS in q", severity="medium")

    summary = json.loads((await call(query, ctx)).content)
    assert summary["hosts"] == 1 and summary["services"] == 1 and summary["vulns"] == 1
    assert summary["suspected"] == 1 and summary["confirmed"] == 0

    unvalidated = json.loads((await call(query, ctx, what="unvalidated")).content)
    assert [u["title"] for u in unvalidated] == ["Reflected XSS in q"]
    assert unvalidated[0]["severity"] == "medium" and unvalidated[0]["id"] == store.state.vulns[0].id

    hosts = json.loads((await call(query, ctx, what="hosts")).content)
    assert hosts[0]["ip"] == "10.0.0.5" and hosts[0]["services"][0]["port"] == 8080

    vulns = json.loads((await call(query, ctx, what="vulns")).content)
    assert vulns[0]["host"] == "10.0.0.5" and vulns[0]["severity"] == "medium"

    coverage = (await call(query, ctx, what="coverage")).content
    assert "| A01 Broken Access Control" in coverage and "UNTESTED" in coverage
    assert "To close:" in coverage and "load_playbook surface=auth-session" in coverage

    shallow = (await call(query, ctx, what="shallow")).content
    assert "1 finding(s) still owe depth:" in shallow and "Reflected XSS in q" in shallow

    prior = (await call(query, ctx, what="prior")).content
    assert prior == "no prior run archived for this object (nothing to resume)."

    review = (await call(query, ctx, what="review")).content
    assert review == "drex has made no calls on findings — nothing to review."


async def test_triage_confirms_drops_and_holds_overclaimed_highs(store: EngagementStore, ctx: ToolContext):
    update = StateUpdateTool(store)
    triage = DrexTriageTool(store)
    unavailable = await call(triage, ctx)
    assert unavailable.status == "error" and "drex is not available in this session" in unavailable.content

    await call(update, ctx, action="vuln", host="example.com", title="Verbose stack trace on 500s", severity="low")
    await call(
        update,
        ctx,
        action="vuln",
        host="example.com",
        title="SQLi in the login form",
        severity="high",
        evidence="union select returned the users table",
    )
    await call(
        update,
        ctx,
        action="vuln",
        host="example.com",
        title="RCE via deserialization",
        severity="critical",
        evidence="payload ran id as www-data",
    )
    triage.decisions = client(
        FakeDrex(
            real={"SQLi in the login form": 0.95, "Verbose stack trace on 500s": 0.1, "RCE via deserialization": 0.9},
            impact={"SQLi in the login form": 0.9, "RCE via deserialization": 0.3},
        )
    )
    result = await call(triage, ctx)
    assert result.status == "ok"
    assert "drex triaged 3 finding(s): 1 confirmed, 1 dropped as FP, 0 left for you, 1 held" in result.content
    assert "✓ CONFIRM 0.95 — SQLi in the login form" in result.content
    assert "✗ DROP 0.10 — Verbose stack trace on 500s" in result.content
    assert "⚠ HOLD real 0.90 but impact@critical=0.30 unproven — RCE via deserialization" in result.content

    sqli = vuln(store, "SQLi")
    assert sqli.status == "confirmed" and sqli.drex_call == "confirmed" and sqli.confidence == 0.95
    verbose = vuln(store, "Verbose")
    assert verbose.status == "false_positive" and verbose.drex_call == "dropped"
    rce = vuln(store, "RCE")
    assert rce.status == "suspected" and rce.drex_call == "held"
    assert "[drex impact@critical=0.30: not demonstrated — downgrade or prove it]" in (rce.evidence or "")


async def test_triage_downgrades_a_confirmed_high_whose_evidence_changed(store: EngagementStore, ctx: ToolContext):
    update = StateUpdateTool(store)
    triage = DrexTriageTool(store)
    await call(
        update,
        ctx,
        action="vuln",
        host="example.com",
        title="Admin bypass via header spoof",
        severity="high",
        evidence="X-Admin: true returned the admin page",
    )
    triage.decisions = client(FakeDrex(real={"Admin bypass via header spoof": 0.95}))
    assert (await call(triage, ctx)).status == "ok"
    finding = vuln(store, "Admin bypass")
    assert finding.status == "confirmed" and finding.confidence == 0.95

    # the client patched it; re-running with the new evidence re-checks the confirmed finding
    triage.decisions = client(FakeDrex(impact={"Admin bypass via header spoof": 0.1}))
    again = await call(triage, ctx)
    assert again.status == "ok" and "1 downgraded (overclaimed HIGH/CRIT)" in again.content
    assert "↓ DOWNGRADE impact@high=0.10 unproven" in again.content
    assert finding.status == "suspected" and finding.drex_call == "downgraded"


async def test_review_queue_flows_from_drex_call_to_final_word(store: EngagementStore, ctx: ToolContext):
    update = StateUpdateTool(store)
    query = StateQueryTool(store)
    triage = DrexTriageTool(store)
    await call(
        update,
        ctx,
        action="vuln",
        host="example.com",
        title="SQLi in the login form",
        severity="high",
        evidence="union select returned the users table",
    )
    triage.decisions = client(FakeDrex(real={"SQLi in the login form": 0.95}, impact={"SQLi in the login form": 0.9}))
    await call(triage, ctx)
    sqli = vuln(store, "SQLi")
    assert sqli.drex_call == "confirmed"

    queue = (await call(query, ctx, what="review")).content
    assert "drex calls: 1 · verified 0 · final 0 · overturned 0" in queue
    assert f"- {sqli.id} [high] SQLi in the login form (example.com) — drex confirmed (p=0.95), now confirmed" in queue

    verifier = await call(
        update,
        ctx,
        action="review",
        id=sqli.id,
        verdict="overturned",
        review_status="suspected",
        notes="re-tested live: prepared statements block every variation",
    )
    assert verifier.status == "ok"
    assert "The status stays confirmed until the main agent's final call." in verifier.content
    assert sqli.status == "confirmed"  # a verifier only recommends

    final = await call(
        update,
        ctx,
        action="review",
        id=sqli.id,
        verdict="overturned",
        review_status="suspected",
        notes="agreed: no bypass works, the union is escaped",
    )
    assert final.status == "ok"
    assert f"final call on {sqli.id}: drex's call (confirmed) overturned → suspected" in final.content
    assert sqli.status == "suspected" and sqli.decided_by == "agent"

    after = (await call(query, ctx, what="review")).content
    assert "drex calls: 1 · verified 1 · final 1 · overturned 1" in after
    assert "To verify" not in after and "To finalize" not in after


async def test_drex_decide_bands_the_verdict(store: EngagementStore, ctx: ToolContext):
    tool = DrexDecideTool(store)
    unavailable = await call(tool, ctx, question="is there more worth extracting?")
    assert unavailable.status == "error" and "drex is not available in this session" in unavailable.content

    for probability, verdict in ((0.7, "yes"), (0.2, "no"), (0.5, "uncertain")):
        tool.decisions = client(FakeDrex(otherwise=probability))
        result = await call(
            tool, ctx, question="is there more worth extracting on example.com?", context="3 endpoints untested"
        )
        assert result.content == f"drex: p(yes)={probability:.2f} → {verdict}"


async def test_set_engagement_switches_to_a_new_slug_dir(store: EngagementStore, ctx: ToolContext, tmp_path):
    tool = SetEngagementTool(store)
    result = await call(
        tool, ctx, target="corp.example", targets=["corp.example", "vpn.example"], objective="external perimeter"
    )
    assert result.status == "ok"
    assert result.content.startswith("engagement set: target=corp.example dir=")
    assert str(tmp_path / "corp.example") in result.content
    assert (tmp_path / "corp.example" / "state.json").is_file()
    assert "targets=[corp.example, vpn.example]" in result.content
    assert store.state.objective == "external perimeter"


async def test_set_engagement_prior_notice_and_resume(tmp_path: Path, ctx: ToolContext):
    first = EngagementStore(tmp_path, slugify("example.com"), "example.com")
    await call(
        StateUpdateTool(first),
        ctx,
        action="vuln",
        host="example.com",
        title="Reflected XSS in search",
        severity="medium",
    )
    second = EngagementStore(tmp_path, slugify("example.com"), "example.com")  # a fresh session archives the prior run
    assert second.prior_meta is not None and second.prior_meta["findings"] == 1

    tool = SetEngagementTool(second)
    fresh = await call(tool, ctx, target="example.com")
    assert fresh.status == "ok"
    assert "prior run from 20" in fresh.content and "say resume to load it" in fresh.content

    prior = json.loads((await call(StateQueryTool(second), ctx, what="prior")).content)
    assert prior["vulns"] == [
        {"host": "example.com", "title": "Reflected XSS in search", "severity": "medium", "status": "suspected"}
    ]
    assert prior["endpoints"] == 0 and prior["creds"] == 0 and prior["access"] == 0

    resumed = await call(tool, ctx, target="example.com", resume=True)
    assert resumed.status == "ok"
    assert "(resumed prior: +1 finding(s), +0 endpoint(s))" in resumed.content
    assert any(v.title == "Reflected XSS in search" for v in second.state.vulns)


def test_the_guide_stays_a_compact_addendum():
    assert len(GUIDE) < 2000
    for tool_name in ("set_engagement", "state_update", "state_query", "drex_triage", "drex_decide"):
        assert tool_name in GUIDE

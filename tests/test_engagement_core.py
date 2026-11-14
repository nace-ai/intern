"""The engagement core: store lifecycle, finding merge rules, coverage accounting, the pivot
engine, the depth ladder, CVSS scoring, and the drex gates over a fake decision backend."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from intern.decisions import Answers, DecisionClient, NoulAnswer, Question
from intern.engagement import cvss, depth, drex, owasp, pivot
from intern.engagement.models import Service, Vuln, now_ms
from intern.engagement.store import EngagementStore, RecordError, detect_vuln_ids, severity_drift_note, slugify

CVSS_ALL_HIGH = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"


class FakeBackend:
    """Answers every noul question with a preset probability, recording what was asked."""

    name = "fake"

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.asked: list[str] = []

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        self.asked.append(state)
        return Answers(answers={"q": NoulAnswer(noul=self.probability)})


def client(backend: FakeBackend) -> DecisionClient:
    return DecisionClient(backend, lambda event: None)


@pytest.fixture
def store(tmp_path: Path) -> EngagementStore:
    return EngagementStore(tmp_path, "shop-example-com", "shop.example.com", objective="test the shop")


def a_finding(store: EngagementStore, **fields: object) -> Vuln:
    base: dict[str, object] = {"host": "10.0.0.5", "title": "SQL injection in /search", "severity": "high"}
    base.update(fields)
    return store.add_vuln(base)


# -- slugify -----------------------------------------------------------------------------------------------


def test_slugify_strips_scheme_and_collapses() -> None:
    assert slugify("https://Shop.Example.com/app") == "shop.example.com-app"
    assert slugify("  target  ") == "target"
    assert slugify("!!!") == "target"


# -- store lifecycle ---------------------------------------------------------------------------------------


def test_fresh_reopen_archives_prior_and_keeps_durable_knowledge(tmp_path: Path) -> None:
    first = EngagementStore(tmp_path, "shop-example-com", "shop.example.com")
    first.add_host("10.0.0.5", hostname="shop.internal")
    a_finding(first, evidence="dumped users")
    first.add_cred(username="admin", secret="pw", works=True)

    second = EngagementStore(tmp_path, "shop-example-com", "shop.example.com")
    # durable knowledge survives a fresh board; findings do not
    assert "10.0.0.5" in second.state.hosts
    assert [c.username for c in second.state.creds] == ["admin"]
    assert second.state.vulns == []
    history = tmp_path / "shop-example-com" / "history"
    assert [e.name for e in history.iterdir()], "the prior run must be archived"

    findings, endpoints = second.load_prior()
    assert findings == 1 and endpoints == 0
    assert second.state.vulns[0].title == "SQL injection in /search"


def test_resume_keeps_the_prior_board(tmp_path: Path) -> None:
    first = EngagementStore(tmp_path, "shop-example-com", "shop.example.com")
    vuln = a_finding(first)
    resumed = EngagementStore(tmp_path, "shop-example-com", "shop.example.com", resume=True)
    assert [v.id for v in resumed.state.vulns] == [vuln.id]


def test_discard_if_unused_removes_only_untouched_folders(tmp_path: Path) -> None:
    empty = EngagementStore(tmp_path, "empty-target", "empty.example.com")
    empty.discard_if_unused()
    assert not (tmp_path / "empty-target").exists()

    used = EngagementStore(tmp_path, "used-target", "used.example.com")
    a_finding(used)
    used.discard_if_unused()
    assert (tmp_path / "used-target" / "state.json").exists()


# -- finding records ---------------------------------------------------------------------------------------


def test_vuln_update_merges_without_dropping_depth_or_losing_tags(store: EngagementStore) -> None:
    v = a_finding(store, owasp=["A05"], cvss=CVSS_ALL_HIGH)
    v.status = "confirmed"
    store.save()
    updated = store.add_vuln({"id": v.id, "depth": "impact", "owasp": ["API3"]})
    assert updated.id == v.id
    assert updated.depth == "impact"
    assert updated.owasp == ["A05", "API3"]
    # a later partial update cannot walk the depth back
    again = store.add_vuln({"id": v.id, "evidence": "also works unauthenticated"})
    assert again.depth == "impact"


def test_vuln_validation_rejects_bad_input(store: EngagementStore) -> None:
    with pytest.raises(RecordError, match="host and title"):
        store.add_vuln({"title": "orphan"})
    with pytest.raises(RecordError, match="severity"):
        store.add_vuln({"host": "h", "title": "t", "severity": "extreme"})
    v = a_finding(store)
    with pytest.raises(RecordError, match=r"no finding"):
        store.add_vuln({"id": "v99-zzzz"})
    with pytest.raises(RecordError, match="stop_reason needs stop_kind"):
        store.add_vuln({"id": v.id, "stop_reason": "gave up"})


def test_cvss_scoring_and_drift(store: EngagementStore) -> None:
    v = a_finding(store, severity="low", cvss=CVSS_ALL_HIGH)
    assert v.cvss is not None and v.cvss.score == 10.0 and v.cvss.band == "critical"
    assert severity_drift_note(v) is not None, "low severity vs critical band is more than one step"
    with pytest.raises(RecordError, match="missing base metric"):
        store.add_vuln({"host": "h", "title": "t2", "cvss": "CVSS:4.0/AV:N/AC:L"})
    with pytest.raises(RecordError, match=r"not a CVSS v4\.0 vector"):
        store.add_vuln({"host": "h", "title": "t3", "cvss": "CVSS:3.1/AV:N"})


def test_coverage_rejects_excuse_na_and_oversized_notes(store: EngagementStore) -> None:
    store.mark_coverage(["A01"], status="tested", note="matrix of 2 users × 14 routes", by="m/a")
    with pytest.raises(RecordError, match="can't apply"):
        store.mark_coverage(["A02"], status="na", note="not tested, ran out of time")
    with pytest.raises(RecordError, match="200 chars"):
        store.mark_coverage(["A03"], status="na", note="x" * 201)
    store.mark_coverage(["A01"], status="tested", note="again", by="m/b")
    mark = next(m for m in store.state.coverage if m.id == "A01")
    assert sorted(mark.testers) == ["m/a", "m/b"], "a later mark keeps the memory of who tested"


def test_coverage_matrix_counts_findings_endpoints_and_marks(store: EngagementStore) -> None:
    a_finding(store, owasp=["A05"])
    llm_tagged = a_finding(store, title="prompt injection in the support bot", severity="high", owasp=["LLM01"])
    store.add_endpoint(method="GET", url="/api/users", owasp=["API1"], status=200)
    store.mark_coverage(["A02"], status="na", note="single-tenant app, no tenant boundary to break")
    rows = {r.id: r for r in owasp.coverage_matrix(store.state.vulns, store.state.endpoints, store.state.coverage)}
    assert rows["A05"].status == "finding"
    assert rows["LLM01"].status == "finding", "the llm list joins once something is llm-tagged"
    assert rows["API1"].status == "tested"
    assert rows["A02"].status == "na"
    assert rows["A01"].status == "untested"
    assert llm_tagged.owasp == ["LLM01"]


def test_classify_host_truth_table() -> None:
    assert pivot.classify_host("10.1.2.3").reach == "internal"
    assert pivot.classify_host("169.254.169.254").why == "cloud metadata"
    assert pivot.classify_host("::1").reach == "internal"
    assert pivot.classify_host("203.0.113.9").reach == "public"
    assert pivot.classify_host("db.svc").why == "Kubernetes service name"
    assert pivot.classify_host("db.svc.cluster.local").reach == "internal"
    assert pivot.classify_host("https://Intranet.Corp:8443/x").reach == "internal"
    assert pivot.classify_host("app.example.com").reach == "unknown"


def test_opportunities_pair_primitives_assets_and_creds(store: EngagementStore) -> None:
    store.add_host(
        "10.0.0.5", hostname="app.corp", services=[Service(port=22, name="ssh"), Service(port=443, name="https")]
    )
    store.add_host("10.0.0.9", hostname="db.corp", services=[Service(port=5432, name="postgresql")])
    v = a_finding(store, title="SSRF in the import endpoint", severity="high", status="confirmed")
    store.add_vuln({"id": v.id, "evidence": "fetched internal url"})
    store.add_cred(username="svc", secret="pw", works=True, valid_for="app.corp")

    opps = pivot.open_opportunities(store.state)
    kinds = {o.kind for o in opps}
    assert "ssrf→metadata" in kinds
    assert "ssrf→internal" in kinds
    assert "cred→reuse" in kinds
    cred_reuse = [o for o in opps if o.kind == "cred→reuse"]
    assert {o.target for o in cred_reuse} == {"db.corp:5432"}, "valid_for excludes the known host"

    # marking a result closes the opportunity
    target = next(o for o in opps if o.kind == "ssrf→metadata")
    store.mark_pivot(target.id, status="failed", note="IMDSv2 requires a token")
    assert target.id not in {o.id for o in pivot.open_opportunities(store.state)}
    with pytest.raises(RecordError, match="no pivot opportunity"):
        store.mark_pivot("pdeadbeef", status="skipped")


# -- depth ladder -------------------------------------------------------------------------------------------


def test_shallow_findings_owe_their_next_step(store: EngagementStore) -> None:
    low = a_finding(store, title="verbose error page", severity="low")
    high = a_finding(store, title="admin bypass", severity="high")
    store.add_vuln({"id": high.id, "depth": "impact"})
    stalled = a_finding(store, title="dead endpoint", severity="medium", stop_reason="rules", stop_kind="rules")
    shallow = {v.id: why for v, why in depth.shallow_findings(store.state.vulns)}
    assert low.id in shallow and "CONFIRM it with a differential" in shallow[low.id]
    assert high.id in shallow and "CHAIN it" in shallow[high.id]
    assert stalled.id not in shallow


def test_drill_nudge_names_the_rung_and_the_record_call(store: EngagementStore) -> None:
    v = a_finding(store, owasp=["A05"])
    nudge = depth.drill_nudge(v, lambda _tag: ["web-sqli"])
    assert f"DRILL DOWN ({v.id} at detected)" in nudge
    assert f"state_update action=vuln id={v.id} depth=" in nudge
    assert "load_playbook surface=web-sqli" in nudge


# -- evidence -----------------------------------------------------------------------------------------------


def test_evidence_storage_linking_and_detection(store: EngagementStore, tmp_path: Path) -> None:
    v = a_finding(store)
    proof = tmp_path / "dump.txt"
    proof.write_text("users table, 3 rows")
    record = store.add_evidence(description="SQLi dump", file=proof, kind="loot", vuln_ids=[v.id])
    assert record is not None and record.name == "dump.txt"
    stored = Path(record.file)
    assert stored.parent == store.evidence_dir and stored.exists()

    other = tmp_path / "proof.png"
    other.write_bytes(b"\x89PNG")
    unlinked = store.add_evidence(description="screenshot proving " + v.id, file=other, kind="screenshot")
    assert unlinked is not None and detect_vuln_ids(unlinked.description, unlinked.name, [v.id]) == [v.id]

    linked = store.add_evidence(description="again", file=proof, kind="loot")
    assert linked is not None
    assert store.link_evidence(linked.name, [v.id]) == linked.name
    with pytest.raises(RecordError, match="no stored evidence"):
        store.link_evidence("missing.txt", [v.id])


# -- drex gates ---------------------------------------------------------------------------------------------


def test_drex_triage_drops_noise(store: EngagementStore) -> None:
    drop_me = a_finding(store, title="banner version disclosure", severity="info", evidence="Server: nginx header")
    counts, lines = asyncio.run(drex.triage(store, client(FakeBackend(0.05))))
    assert drop_me.status == "false_positive"
    assert drop_me.drex_call == "dropped" and drop_me.decided_by == "drex"
    assert counts.dropped == 1 and "DROP" in lines[0]


def test_drex_impact_gate_holds_an_overclaimed_critical(store: EngagementStore) -> None:
    v = a_finding(store, severity="critical", evidence="WAF bypass possible in theory")

    class Gate(FakeBackend):
        async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
            if drex.IMPACT_QUESTION in questions.values():
                return Answers(answers={"q": NoulAnswer(noul=0.1)})
            return Answers(answers={"q": NoulAnswer(noul=0.95)})

    counts, _ = asyncio.run(drex.triage(store, client(Gate(0))))
    assert v.status == "suspected" and v.drex_call == "held"
    assert counts.held == 1
    assert "[drex impact@critical=0.10: not demonstrated — downgrade or prove it]" in (v.evidence or "")


def test_drex_downgrades_an_overclaimed_confirmed_high(store: EngagementStore) -> None:
    v = a_finding(store, severity="high", evidence="found header", status="confirmed")
    v.confidence = 0.95  # already validated: outside loop 1, caught by the re-check
    store.save()
    counts, _ = asyncio.run(drex.triage(store, client(FakeBackend(0.2))))
    assert v.status == "suspected" and v.drex_call == "downgraded"
    assert counts.downgraded == 1


def test_drex_middle_band_leaves_the_call_to_the_operator(store: EngagementStore) -> None:
    v = a_finding(store, evidence="one reacting payload")
    counts, lines = asyncio.run(drex.triage(store, client(FakeBackend(0.5))))
    assert v.status == "suspected" and v.drex_call is None
    assert counts.manual == 1 and "your call" in lines[0]


def test_drex_decide_bands(store: EngagementStore) -> None:
    p, verdict = asyncio.run(drex.decide(store, client(FakeBackend(0.9)), "is the host exhausted?", None))
    assert (p, verdict) == (0.9, "yes")
    _, no = asyncio.run(drex.decide(store, client(FakeBackend(0.1)), "stop here?", None))
    assert no == "no"
    _, maybe = asyncio.run(drex.decide(store, client(FakeBackend(0.5)), "deeper?", None))
    assert maybe == "uncertain"


# -- the drex review queue -----------------------------------------------------------------------------------


def test_review_queue_verifier_recommends_final_applies(store: EngagementStore) -> None:
    v = a_finding(store, status="suspected")
    v.drex_call, v.confidence, v.decided_by = "held", 0.9, "drex"
    store.save()
    queue = store.review_queue()
    assert isinstance(queue["to_verify"], list) and len(queue["to_verify"]) == 1

    _, note = store.review_drex_call(v.id, "verifier", verdict="overturned", status="confirmed", notes="repro OK, real")
    assert "final call" in note
    assert v.status == "suspected", "the verifier only recommends"

    reviewed, message = store.review_drex_call(
        v.id, "final", verdict="overturned", status="confirmed", notes="it reproduces reliably"
    )
    assert reviewed.status == "confirmed" and "overturned" in message
    assert "overturned 1" in drex.describe_review_queue(store)


# -- dash -----------------------------------------------------------------------------------------------------


def test_board_line_and_dash_sections(store: EngagementStore) -> None:
    from intern.engagement import dash

    a_finding(store, severity="high", status="confirmed")
    store.mark_coverage(["A05"], status="tested", note="all parameters exercised")
    line = dash.board_line(store.state)
    assert "shop.example.com" in line and "H1" in line and "cov 1/20" in line
    sections = dash.dash_sections(store.state)
    assert any("SQL injection" in row for row in sections["findings"])
    assert sections["credentials"] == ["no credentials recorded"]
    assert any("open pivots" in row for row in sections["footholds"])


# -- cvss module ----------------------------------------------------------------------------------------------


def test_cvss_module_bands() -> None:
    assert cvss.cvss_band(9.5) == "critical"
    assert cvss.cvss_band(7.0) == "high"
    assert cvss.cvss_band(4.0) == "medium"
    assert cvss.cvss_band(0.1) == "low"
    assert cvss.cvss_band(0.0) == "info"
    assert cvss.severity_drift("critical", "low")
    assert not cvss.severity_drift("medium", "low")


# -- state model ---------------------------------------------------------------------------------------------


def test_state_persists_and_round_trips(store: EngagementStore) -> None:
    v = a_finding(store, status="confirmed", title="reflected XSS in the query parameter")
    store.add_vuln({"id": v.id, "evidence": "alert fired"})
    data = json.loads(store.state_file.read_text())
    assert data["vulns"], "findings persist"
    assert isinstance(now_ms(), int)

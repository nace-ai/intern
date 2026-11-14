"""Report pipeline tests: the model (F-ids, ordering, gaps), both render editions, the
report_content/report/report_diagram tools, and the PDF skip path."""

from __future__ import annotations

import asyncio
import re

import pytest

from intern.engagement.diagram import (
    Diagram,
    DiagramArgs,
    DiagramEdge,
    DiagramEdgeArg,
    DiagramNode,
    DiagramNodeArg,
    ReportDiagramTool,
    diagram_errors,
    load_diagrams,
)
from intern.engagement.models import Observation, Tester
from intern.engagement.report_model import build_model, content_gaps
from intern.engagement.report_render import render_html, render_markdown, render_pdf
from intern.engagement.report_tools import (
    AttackStepArg,
    ObservationArg,
    PrioritiesArg,
    ReportArgs,
    ReportContentArgs,
    ReportContentTool,
    ReportTool,
    RoadmapItemArg,
)
from intern.engagement.store import EngagementStore
from intern.tools.base import ToolContext

PNG = bytes.fromhex("89504e470d0a1a0a") + b"fake-image-bytes"


def make_store(tmp_path) -> EngagementStore:
    return EngagementStore(out_root=tmp_path / "out", slug="shop-example-com", target="shop.example.com")


def make_ctx(tmp_path) -> ToolContext:
    return ToolContext(cwd=tmp_path, executor=None, abort=asyncio.Event(), max_output_chars=1000)  # type: ignore[arg-type]


async def run_tool(tool, args, tmp_path):
    return await tool.run(args, make_ctx(tmp_path))


@pytest.fixture
def no_browser(monkeypatch):
    """Playwright reports itself missing, whatever the environment has installed."""

    def missing(name: str):
        raise ImportError(name)

    monkeypatch.setattr("intern.engagement.report_render.importlib.import_module", missing)


def seeded_store(tmp_path) -> EngagementStore:
    """Two confirmed findings (one with proof captured, one typed), plus noise the register skips."""
    store = make_store(tmp_path)
    store.add_vuln(
        {
            "host": "shop.example.com",
            "title": "Order API returns any order by ID",
            "severity": "high",
            "status": "confirmed",
            "description": "The endpoint authorizes the session, not the object.",
            "impact": ["Any order can be read."],
            "remediation": ["Enforce object ownership checks."],
            "cwe": "CWE-639",
            "owasp": ["A01"],
            "cvss": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:L/SI:L/SA:L",
            "http_request": "GET /v2/orders/1138 HTTP/1.1\nHost: shop.example.com",
            "http_response": 'HTTP/1.1 200 OK\n\n{"id": 1138, "total": 98}',
        }
    )
    store.add_vuln(
        {
            "host": "shop.example.com",
            "title": "Session cookie missing SameSite attribute",
            "severity": "low",
            "status": "confirmed",
            "description": "The session cookie is set without SameSite.",
            "impact": ["Cross-site requests carry the session."],
            "remediation": ["Set SameSite=Lax on the session cookie."],
            "cwe": "CWE-1275",
            "cvss": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:P/VC:L/VI:L/VA:L/SC:L/SI:L/SA:L",
            "http_request": "GET / HTTP/1.1",
            "http_response": "HTTP/1.1 200 OK\nSet-Cookie: sid=abc; Path=/",
        }
    )
    store.add_vuln({"host": "shop.example.com", "title": "maybe mass assignment", "status": "suspected"})
    store.add_vuln({"host": "shop.example.com", "title": "banner version noise", "status": "false_positive"})
    return store


def add_proof(store: EngagementStore, tmp_path, vuln_id: str) -> None:
    shot = tmp_path / "proof.png"
    shot.write_bytes(PNG)
    store.add_evidence(
        description="order response as another customer",
        file=shot,
        kind="screenshot",
        vuln_ids=[vuln_id],
        url="https://shop.example.com/orders/1138",
        caption="the response carries another customer's order",
    )


# -- model ----------------------------------------------------------------------------------------------


def test_fids_ordered_by_severity_then_insertion(tmp_path):
    store = make_store(tmp_path)
    store.add_vuln({"host": "h", "title": "low one", "severity": "low", "status": "confirmed"})
    store.add_vuln({"host": "h", "title": "critical one", "severity": "critical", "status": "confirmed"})
    store.add_vuln({"host": "h", "title": "high one", "severity": "high", "status": "confirmed"})
    store.add_vuln({"host": "h", "title": "critical two", "severity": "critical", "status": "confirmed"})
    store.add_vuln({"host": "h", "title": "medium one", "severity": "medium", "status": "confirmed"})
    store.add_vuln({"host": "h", "title": "a suspected lead", "severity": "high", "status": "suspected"})
    store.add_vuln({"host": "h", "title": "dismissed", "severity": "high", "status": "false_positive"})
    model = build_model(store)
    assert [(f.fid, f.vuln.title) for f in model.findings] == [
        ("F-01", "critical one"),
        ("F-02", "critical two"),
        ("F-03", "high one"),
        ("F-04", "medium one"),
        ("F-05", "low one"),
    ]
    assert [v.title for v in model.leads] == ["a suspected lead"]
    assert model.confirmed_count == 5
    assert model.counts == {"critical": 2, "high": 1, "medium": 1, "low": 1, "info": 0}
    assert model.rating == "Critical"
    assert model.fid_of(model.findings[0].vuln.id) == "F-01"


def test_register_status_open_and_remediated(tmp_path):
    store = make_store(tmp_path)
    open_id = store.add_vuln({"host": "h", "title": "open", "severity": "high", "status": "confirmed"}).id
    fixed_id = store.add_vuln({"host": "h", "title": "fixed", "severity": "medium", "status": "confirmed"}).id
    store.recheck_vuln(fixed_id, remediated=True, notes="patched")
    model = build_model(store)
    by_title = {f.vuln.title: f for f in model.findings}
    assert by_title["open"].status == "open"
    assert by_title["fixed"].status == "remediated" and by_title["fixed"].reverified is False
    assert model.open_count == 1
    assert open_id and fixed_id  # both recorded


def test_content_gaps_missing_narrative_unlinked_and_shallow(tmp_path):
    store = seeded_store(tmp_path)
    add_proof(store, tmp_path, store.state.vulns[0].id)  # linked proof for the first finding
    model = build_model(store)
    gaps = content_gaps(model)
    text = "\n".join(gaps)
    assert "cover synopsis (report_content synopsis=…)" in text
    assert "executive headline + summary" in text
    assert "testers — ask the operator" in text
    # the second finding's only proof is the typed request/response
    f02 = next(f for f in model.findings if f.vuln.title.startswith("Session cookie"))
    assert f02.artifacts == []
    assert any(g.startswith(f"{f02.fid} Session cookie") and "proof is only the request/response" in g for g in gaps)
    # both confirmed findings sit below impact depth → shallow warnings with their F-ids
    assert any(g.startswith(f"shallow finding: {model.findings[0].fid} Order API") for g in gaps)

    # an artifact nothing links to is a gap, and disappears once linked
    loose = tmp_path / "network_map.png"
    loose.write_bytes(PNG)
    store.add_evidence(description="internal network map", file=loose, kind="screenshot")
    unlinked = "\n".join(content_gaps(build_model(store)))
    assert "not linked to any finding" in unlinked and "network_map.png" in unlinked
    store.link_evidence("network_map.png", [store.state.vulns[0].id])
    assert "not linked to any finding" not in "\n".join(content_gaps(build_model(store)))


def test_gap_when_no_evidence_and_missing_fields(tmp_path):
    store = make_store(tmp_path)
    store.add_vuln({"host": "h", "title": "bare finding", "severity": "critical", "status": "confirmed"})
    gaps = content_gaps(build_model(store))
    assert any(g.startswith("F-01 bare finding") and "missing description, remediation, impact, cwe" in g for g in gaps)


# -- renderers ------------------------------------------------------------------------------------------


def test_markdown_edition(tmp_path):
    store = seeded_store(tmp_path)
    add_proof(store, tmp_path, store.state.vulns[0].id)
    model = build_model(store)
    md = render_markdown(model, store)
    order = [
        "## 01 — Executive summary",
        "## 02 — Findings overview",
        "## 03 — Attack path",
        "## 04 — Detailed findings",
        "## 05 — Remediation roadmap",
        "## 06 — Control coverage",
        "## Appendices",
    ]
    positions = [md.index(h) for h in order]
    assert positions == sorted(positions) and all(p >= 0 for p in positions)
    assert "| ID | Finding | Asset | Severity | Category | Status |" in md
    assert "### F-01 · [HIGH] Order API returns any order by ID" in md
    stored = store.state.evidence[0].file.split("/")[-1]
    assert f"- `evidence/{stored}` — sha256 `" in md
    assert "### A — Evidence integrity" in md and "### F — Severity rating" in md
    # the typed request/response is shown for the finding with no captured artifact
    assert "```http" in md and "GET / HTTP/1.1" in md


def test_html_edition_is_self_contained(tmp_path):
    store = seeded_store(tmp_path)
    add_proof(store, tmp_path, store.state.vulns[0].id)
    model = build_model(store)
    html = render_html(model, store)
    assert "<script" not in html
    assert "data:image/png;base64," in html  # the linked screenshot is embedded
    srcs = re.findall(r'src="([^"]+)"', html)
    assert srcs and all(src.startswith("data:") for src in srcs)
    allowed = ("cwe.mitre.org", "owasp.org", "first.org")
    for href in re.findall(r'href="(https?://[^"]+)"', html):
        assert href.startswith("https://") and any(host in href for host in allowed), href
    assert 'class="term"' in html  # the typed proof renders as a terminal block
    assert "F-01" in html and "Order API returns any order by ID" in html
    # user content is escaped
    store.update_report(headline="<script>alert(1)</script>")
    html = render_html(build_model(store), store)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_html_renders_har_summary_and_figures_inline(tmp_path):
    store = seeded_store(tmp_path)
    har = tmp_path / "traffic.har"
    har.write_text(
        '{"log": {"entries": ['
        '{"request": {"method": "GET", "url": "https://shop.example.com/v2/orders/1138"}, '
        '"response": {"status": 200, "content": {"mimeType": "application/json", "size": 512}}}, '
        '{"request": {"method": "GET", "url": "https://shop.example.com/logo.png"}, '
        '"response": {"status": 200, "content": {"mimeType": "image/png", "size": 99}}}'
        "]}}"
    )
    store.add_evidence(
        description="browser traffic behind the order fetch",
        file=har,
        kind="response",
        vuln_ids=[store.state.vulns[0].id],
    )
    save_diagram(store)
    model = build_model(store)
    html = render_html(model, store)
    assert "https://shop.example.com/v2/orders/1138  →  200 application/json 512 B" in html
    assert "logo.png" not in html  # static assets are skipped by the summary
    assert "<svg" in html and "Figure 1" in html


# -- report_content -------------------------------------------------------------------------------------


async def test_report_content_merges_and_rejects_unknown_findings(tmp_path):
    store = seeded_store(tmp_path)
    tool = ReportContentTool(store)
    bad = await run_tool(
        tool,
        ReportContentArgs(observations=[ObservationArg(title="t", detail="d", findings=["v999"])]),
        tmp_path,
    )
    assert bad.status == "error" and "v999" in bad.content
    ok = await run_tool(
        tool, ReportContentArgs(headline="Exposure of customer orders", summary="Two issues."), tmp_path
    )
    assert ok.status == "ok"
    assert store.state.report.headline == "Exposure of customer orders"
    ok = await run_tool(
        tool,
        ReportContentArgs(priorities=PrioritiesArg(immediate=["Rotate the signing key"]), worked_well="WAF held."),
        tmp_path,
    )
    assert ok.status == "ok"
    assert store.state.report.priorities.immediate == ["Rotate the signing key"]
    assert store.state.report.summary == "Two issues."  # untouched by the later call


async def test_report_content_length_limits(tmp_path):
    store = seeded_store(tmp_path)
    tool = ReportContentTool(store)
    result = await run_tool(tool, ReportContentArgs(synopsis="x" * 501), tmp_path)
    assert result.status == "error" and "synopsis is 501 chars (max 500)" in result.content
    result = await run_tool(
        tool, ReportContentArgs(observations=[ObservationArg(title="t", detail="y" * 321)]), tmp_path
    )
    assert result.status == "error" and "observations[0].detail is 321 chars (max 320)" in result.content
    result = await run_tool(tool, ReportContentArgs(roadmap=[RoadmapItemArg(phase="fix", action="z" * 161)]), tmp_path)
    assert result.status == "error" and "roadmap[0].action is 161 chars (max 160)" in result.content
    result = await run_tool(tool, ReportContentArgs(priorities=PrioritiesArg(structural=["w" * 141])), tmp_path)
    assert result.status == "error" and "priorities.structural[0] is 141 chars (max 140)" in result.content
    result = await run_tool(
        tool,
        ReportContentArgs(attack_path=[AttackStepArg(stage="impact", narrative="n" * 451)]),
        tmp_path,
    )
    assert result.status == "error" and "attack_path[0].narrative is 451 chars (max 450)" in result.content


async def test_report_content_requires_a_field(tmp_path):
    result = await run_tool(ReportContentTool(seeded_store(tmp_path)), ReportContentArgs(), tmp_path)
    assert result.status == "error" and "at least one field" in result.content


# -- diagrams --------------------------------------------------------------------------------------------


def save_diagram(store: EngagementStore, *, title: str = "Target architecture", nodes: int = 2) -> None:
    from intern.engagement.diagram import save_diagrams, upsert_diagram

    finding = store.state.vulns[0].id if store.state.vulns else None
    node_list = [
        DiagramNode(id="gateway", label="API gateway", finding=finding),
        DiagramNode(id="orders", label="Order service"),
        DiagramNode(id="db", label="Order database"),
    ][: max(nodes, 1)]
    edges = [
        DiagramEdge.model_validate({"from": "gateway", "to": "orders"}),
        DiagramEdge.model_validate({"from": "orders", "to": "db"}),
    ][: max(nodes - 1, 0)]
    diagram = Diagram(title=title, caption="What testing reached.", nodes=node_list, edges=edges)
    save_diagrams(store, upsert_diagram(load_diagrams(store), diagram))


def test_diagram_validation_errors():
    too_many = Diagram(
        title="too many",
        nodes=[DiagramNode(id=f"n{i}", label=f"n{i}") for i in range(33)],
    )
    assert any("33 nodes (max 32)" in e for e in diagram_errors(too_many))
    unknown = Diagram(
        title="unknown endpoint",
        nodes=[DiagramNode(id="a", label="a")],
        edges=[DiagramEdge.model_validate({"from": "a", "to": "ghost"})],
    )
    assert any('edge a→ghost: unknown node "ghost"' in e for e in diagram_errors(unknown))
    dup = Diagram(
        title="dup",
        nodes=[DiagramNode(id="a", label="x"), DiagramNode(id="a", label="y")],
    )
    assert any('duplicate node id "a"' in e for e in diagram_errors(dup))
    long_label = Diagram(title="long", nodes=[DiagramNode(id="a", label="x" * 49)])
    assert any("label is 49 chars (max 48)" in e for e in diagram_errors(long_label))
    assert Diagram(title="ok", nodes=[DiagramNode(id="a", label="a")]).edges == []


def test_diagram_svg_contains_labels_and_finding_eyebrow(tmp_path):
    store = seeded_store(tmp_path)
    save_diagram(store)
    figures = build_model(store).figures
    assert len(figures) == 1
    svg = figures[0].svg
    assert svg.startswith("<svg") and "</svg>" in svg
    assert "API gateway" in svg and "Order service" in svg  # wrapped node labels
    assert "F-01 · HIGH" in svg  # the finding-linked node carries its F-id and severity
    assert "http" not in svg.lower() or "www.w3.org" in svg  # self-contained: only the svg namespace


async def test_diagram_tool_upserts_by_title(tmp_path):
    store = seeded_store(tmp_path)
    tool = ReportDiagramTool(store)
    first = DiagramArgs(
        title="Target architecture",
        caption="first",
        nodes=[DiagramNodeArg(id="a", label="gateway"), DiagramNodeArg(id="b", label="orders")],
        edges=[DiagramEdgeArg.model_validate({"from": "a", "to": "b"})],
    )
    result = await run_tool(tool, first, tmp_path)
    assert result.status == "ok" and "2 nodes" in result.content
    assert len(load_diagrams(store)) == 1

    replaced = DiagramArgs(
        title="Target architecture",
        caption="second",
        nodes=[
            DiagramNodeArg(id="a", label="gateway"),
            DiagramNodeArg(id="b", label="orders"),
            DiagramNodeArg(id="c", label="db"),
        ],
        edges=[DiagramEdgeArg.model_validate({"from": "a", "to": "b"})],
    )
    result = await run_tool(tool, replaced, tmp_path)
    assert result.status == "ok" and "replaced the previous version" in result.content
    diagrams = load_diagrams(store)
    assert len(diagrams) == 1 and len(diagrams[0].nodes) == 3 and diagrams[0].caption == "second"

    gone = await run_tool(tool, DiagramArgs(title="Target architecture", remove=True), tmp_path)
    assert gone.status == "ok" and load_diagrams(store) == []
    missing = await run_tool(tool, DiagramArgs(title="Target architecture", remove=True), tmp_path)
    assert missing.status == "error" and "no diagram" in missing.content


async def test_diagram_tool_rejects_invalid_input(tmp_path):
    store = seeded_store(tmp_path)
    tool = ReportDiagramTool(store)
    unknown_edge = DiagramArgs(
        title="bad",
        nodes=[DiagramNodeArg(id="a", label="gateway")],
        edges=[DiagramEdgeArg.model_validate({"from": "a", "to": "ghost"})],
    )
    result = await run_tool(tool, unknown_edge, tmp_path)
    assert result.status == "error" and 'unknown node "ghost"' in result.content
    unconfirmed = store.add_vuln({"host": "h", "title": "lead", "status": "suspected"})
    bad_finding = DiagramArgs(
        title="bad finding",
        nodes=[DiagramNodeArg(id="a", label="gateway", finding=unconfirmed.id)],
    )
    result = await run_tool(tool, bad_finding, tmp_path)
    assert result.status == "error" and "not a confirmed finding" in result.content
    assert load_diagrams(store) == []


# -- report tool -----------------------------------------------------------------------------------------


async def test_report_tool_writes_files_and_lists_gaps(tmp_path, no_browser):
    store = seeded_store(tmp_path)
    add_proof(store, tmp_path, store.state.vulns[0].id)
    result = await run_tool(ReportTool(store), ReportArgs(), tmp_path)
    assert result.status == "ok"
    report_dir = store.report_dir
    assert (report_dir / "report.md").is_file()
    assert (report_dir / "report.html").is_file()
    assert not (report_dir / "report.pdf").exists()
    assert "report.pdf not produced" in result.content  # playwright missing → warn-only
    assert "⚠" in result.content and "cover synopsis" in result.content
    md = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "Order API returns any order by ID" in md
    html = (report_dir / "report.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in html

    # a complete narrative turns the gap list into "no gaps"
    store.update_report(
        synopsis="s",
        headline="h",
        summary="m",
        worked_well="w",
        coverage_notes="c",
        limitations="l",
        testers=[Tester(name="A. Tester")],
        source_ips=["203.0.113.9"],
        priorities={"immediate": ["fix the order check"]},
        observations=[Observation(title="o", detail="d")],
    )
    result = await run_tool(ReportTool(store), ReportArgs(), tmp_path)
    assert result.status == "ok"
    assert "no gaps" not in result.content  # findings still owe depth/evidence


async def test_report_tool_writes_figures(tmp_path, no_browser):
    store = seeded_store(tmp_path)
    save_diagram(store)
    result = await run_tool(ReportTool(store), ReportArgs(), tmp_path)
    assert result.status == "ok"
    figure_files = list((store.report_dir / "figures").glob("*.svg"))
    assert len(figure_files) == 1
    assert "target-architecture.svg" in [f.name for f in figure_files]


async def test_render_pdf_skips_without_playwright(tmp_path, no_browser):
    html = tmp_path / "report.html"
    html.write_text("<!doctype html><html><body>report</body></html>", encoding="utf-8")
    reason = await render_pdf(html, tmp_path / "report.pdf")
    assert reason is not None and "Chromium is unavailable" in reason
    assert not (tmp_path / "report.pdf").exists()

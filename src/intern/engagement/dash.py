"""The engagement dashboard, as pure text: the live board line above the prompt and the sections
the full screen shows. Rendering only — the state comes in, strings go out, no Textual here."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from intern.engagement.models import State, Vuln
from intern.engagement.owasp import coverage_categories, coverage_gaps, coverage_matrix

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SECTIONS = ("findings", "hosts", "endpoints", "agents", "activity", "coverage", "credentials", "footholds")


def findings_by_severity(vulns: Sequence[Vuln]) -> Mapping[str, int]:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for v in vulns:
        if v.status == "false_positive":
            continue
        counts[v.severity] = counts.get(v.severity, 0) + 1
    return counts


def board_line(state: State | None, agents: Iterable[Mapping[str, str]] = ()) -> str:
    """One line: what is running, the attack surface, findings by severity, coverage."""
    if state is None:
        return ""
    running = [f"{a.get('role', '?')}: {a.get('tool', '…')}" for a in agents if a.get("status") == "running"]
    counts = findings_by_severity(state.vulns)
    sev = " ".join(f"{sev[0].upper()}{counts[sev]}" for sev in SEVERITY_ORDER if counts.get(sev))
    rows = coverage_matrix(state.vulns, state.endpoints, state.coverage)
    accounted = sum(1 for r in rows if r.status != "untested")
    services = sum(len(h.services) for h in state.hosts.values())
    parts = [
        f"engage {state.target}",
        f"{len(state.hosts)} hosts · {services} svc",
        sev or "no findings",
        f"cov {accounted}/{len(rows)}",
    ]
    if running:
        parts.append("⧗ " + ", ".join(running[:3]) + (f" +{len(running) - 3}" if len(running) > 3 else ""))
    return " │ ".join(parts)


def _finding_line(v: Vuln, index: int) -> str:
    flags = []
    if v.status == "confirmed":
        flags.append("confirmed")
    elif v.status == "suspected":
        flags.append("suspected")
    if v.remediated:
        flags.append("remediated")
    if v.retest:
        flags.append(f"retest:{v.retest.result}")
    depth = v.depth or "detected"
    port = f":{v.service_port}" if v.service_port else ""
    meta = f" ({', '.join(flags)})" if flags else ""
    return f"F-{index:02d} [{v.severity}] {v.title} — {v.host}{port} · {depth}{meta}"


def dash_sections(state: State) -> dict[str, list[str]]:
    """The full dashboard: one list of lines per section, in SECTIONS order."""
    findings: list[str] = []
    live = [v for v in state.vulns if v.status != "false_positive"]
    for sev in SEVERITY_ORDER:
        sev_vulns = [v for v in live if v.severity == sev]
        for v in sev_vulns:
            findings.append(_finding_line(v, live.index(v) + 1))
    fps = [v for v in state.vulns if v.status == "false_positive"]
    if fps:
        findings.append(f"{len(fps)} false positive(s) dropped")
    if not findings:
        findings.append("no findings recorded yet")

    hosts: list[str] = []
    for host in state.hosts.values():
        names = " ".join(
            f"{s.port}/{'tcp' if (s.proto or 'tcp') == 'tcp' else s.proto}{'/' + s.name if s.name else ''}"
            f"{f' {s.version}' if s.version else ''}"
            for s in sorted(host.services, key=lambda x: x.port)
        )
        reach = f" · {host.reachability}" if host.reachability else ""
        hosts.append(f"{host.ip}{f' ({host.hostname})' if host.hostname else ''}{reach}: {names or 'no services'}")
    if not hosts:
        hosts.append("no hosts recorded yet")

    endpoints = [f"{e.method} {e.url} → {e.status or '?'}{' ★' if e.interesting else ''}" for e in state.endpoints]
    endpoints = endpoints or ["no endpoints tested yet"]

    activity = [f"{line.kind}: {line.text}" for line in state.events[-40:][::-1]] or ["nothing recorded yet"]

    rows = coverage_matrix(state.vulns, state.endpoints, state.coverage)
    coverage = [f"{r.id} {r.title}: {r.status}" + (f" — {r.note}" if r.note else "") for r in rows]
    gaps = coverage_gaps(rows)
    coverage.append(f"{len(rows) - len(gaps)}/{len(rows)} accounted for")

    creds = []
    for c in state.creds:
        secret = "••••" if c.secret else "—"
        works = " works" if c.works else (" fails" if c.works is False else "")
        creds.append(f"{c.id} {c.username or '?'}:{secret}{works}{f' for {c.valid_for}' if c.valid_for else ''}")
    creds = creds or ["no credentials recorded"]

    footholds = [f"{a.id} {a.type} on {a.target}{f' via {a.via}' if a.via else ''}" for a in state.access]
    from intern.engagement.pivot import open_opportunities, render_opportunities

    opps = open_opportunities(state)
    footholds.append(f"open pivots: {len(opps)}")
    footholds.extend(render_opportunities(opps, 20).splitlines())
    if not state.access and not opps:
        footholds.append("no footholds, no open pivots")

    return {
        "findings": findings,
        "hosts": hosts,
        "endpoints": endpoints,
        "agents": [],  # filled by the caller from the dispatch tracker
        "activity": activity,
        "coverage": coverage,
        "credentials": creds,
        "footholds": footholds,
    }


def coverage_total(state: State) -> tuple[int, int]:
    rows = coverage_matrix(state.vulns, state.endpoints, state.coverage)
    return sum(1 for r in rows if r.status != "untested"), len(rows)


def category_titles(llm_touched: bool) -> list[str]:
    return [f"{c.id} {c.title}" for c in coverage_categories(llm_touched)]

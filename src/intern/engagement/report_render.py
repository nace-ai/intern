"""Report renderers: the markdown edition (diffable, pastes into tickets; evidence referenced
with SHA-256) and the HTML edition (one self-contained file — inline CSS, images embedded as
data URIs, figures as inline SVG, no scripts, no external resources). The PDF is the HTML
printed by Playwright's Chromium; without the optional browser extra the PDF is skipped and the
markdown and HTML are still complete.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import hashlib
import html as _html
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from intern.engagement.models import EvidenceRecord
from intern.engagement.report_model import (
    PHASE_TITLE,
    RETEST_LABEL,
    SEVERITIES,
    STAGE_LABEL,
    STATUS_LABEL,
    Finding,
    ReportModel,
    ReportStatus,
    build_model,
)
from intern.engagement.store import EngagementStore

#: Where figure SVGs are written, relative to report.md / report.html.
FIGURES_DIR = "figures"

IMG_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TEXT_EXT = {
    ".txt",
    ".json",
    ".log",
    ".md",
    ".csv",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".conf",
    ".ini",
    ".har",
    ".js",
    ".sql",
    ".out",
}
IMG_MAX = 4 * 1024 * 1024
TEXT_MAX = 1024 * 1024
MAX_TEXT_ARTIFACTS = 6
CAPTION_MAX = 320
#: A HAR is shown as a request list, not raw JSON; at most this many rows.
HAR_ROWS = 14

CVSS_SEV_WORD: dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "None",
}


# -- evidence files on disk --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceFile:
    """One file in the evidence dir, with its SHA-256 and capture time."""

    file: str
    name: str
    when: str
    size: int
    sha: str
    ext: str


def read_evidence(evidence_dir: Path) -> list[EvidenceFile]:
    """Every non-hidden file in the evidence dir, sorted; a 10+-digit name prefix is the epoch-ms
    capture time, the rest is the original name."""
    try:
        names = sorted(f for f in evidence_dir.iterdir() if not f.name.startswith("."))
    except OSError:
        return []
    out: list[EvidenceFile] = []
    for path in names:
        try:
            if not path.is_file():
                continue
            data = path.read_bytes()
        except OSError:
            continue
        match = re.match(r"^(\d{10,})-(.*)$", path.name)
        when = _dt_from_ms(int(match.group(1))) if match else "unknown time"
        name = match.group(2) if match else path.name
        out.append(
            EvidenceFile(
                file=path.name,
                name=name,
                when=when,
                size=len(data),
                sha=hashlib.sha256(data).hexdigest(),
                ext=path.suffix.lower(),
            )
        )
    return out


def _dt_from_ms(ts_ms: int) -> str:
    return _dt.datetime.fromtimestamp(ts_ms / 1000, tz=_dt.UTC).isoformat()


# -- shared helpers -----------------------------------------------------------------------------------


def esc(value: object) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def md_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n+", " ")


def clamp_lines(text: str, max_lines: int) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n… ({len(lines) - max_lines} more lines in the evidence file)"


def caption_of(text: str | None, fallback: str) -> str:
    c = re.sub(r"\s+", " ", text or fallback).strip()
    cut = f"{c[: CAPTION_MAX - 1]}…" if len(c) > CAPTION_MAX else c
    return cut if re.search(r"[.!?…]$", cut) else f"{cut}."


def summarize_har(raw: str, max_rows: int = HAR_ROWS) -> str | None:
    """A HAR file as one line per meaningful request (shared with the evidence pipeline)."""
    from intern.engagement.har import summarize_har as summarize

    return summarize(raw, max_rows)


@dataclass(frozen=True)
class _TextArtifact:
    name: str
    body: str
    caption: str


@dataclass(frozen=True)
class FindingEvidence:
    """A finding's linked artifacts, split into embedded screenshots and text excerpts."""

    shots: list[EvidenceRecord] = field(default_factory=list)
    texts: list[_TextArtifact] = field(default_factory=list)
    extra_texts: int = 0


def collect_evidence(f: Finding, evidence_dir: Path) -> FindingEvidence:
    shots: list[EvidenceRecord] = []
    texts: list[_TextArtifact] = []
    for record in f.artifacts:
        if IMG_MIME.get(Path(record.file).suffix.lower()):
            if record.kind == "screenshot" and all(Path(s.file).name != Path(record.file).name for s in shots):
                shots.append(record)
            continue
        path = _resolve(record.file, evidence_dir)
        if path.suffix.lower() not in TEXT_EXT:
            continue
        try:
            if path.stat().st_size > TEXT_MAX:
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = summarize_har(raw) if path.suffix.lower() == ".har" else raw
        if body is not None:
            note = caption_of(record.caption or record.description, record.name)
            texts.append(_TextArtifact(record.name, body, note))
    return FindingEvidence(shots, texts[:MAX_TEXT_ARTIFACTS], max(0, len(texts) - MAX_TEXT_ARTIFACTS))


def _resolve(file: str, evidence_dir: Path) -> Path:
    path = Path(file)
    return path if path.is_absolute() else evidence_dir / path


def _image_data_uri(file: str, evidence_dir: Path) -> str | None:
    path = _resolve(file, evidence_dir)
    mime = IMG_MIME.get(path.suffix.lower())
    if not mime:
        return None
    try:
        if path.stat().st_size > IMG_MAX:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# -- markdown edition ----------------------------------------------------------------------------------


def render_markdown(model: ReportModel, store: EngagementStore) -> str:
    evidence = read_evidence(store.evidence_dir)
    c = model.content
    sha_by_file = {e.file: e.sha for e in evidence}
    L: list[str] = []
    L += [
        f"# Penetration test report — {model.client} — {model.target}",
        "",
        f"**Client:** {model.client} · **Engagement:** {model.engagement_id} · "
        f"**Test window:** {model.test_window} · **Version:** {model.version}",
        "",
        f"**Overall risk rating:** {model.rating} · critical {model.counts['critical']} · "
        f"high {model.counts['high']} · medium {model.counts['medium']} · low {model.counts['low']}",
        "",
    ]
    if c.synopsis:
        L += [c.synopsis, ""]
    L += [
        "_Confidential · restricted. Credentials and secret values observed during testing are "
        "withheld from this report and handed over separately via a secure channel._",
        "",
    ]

    L += ["## 01 — Executive summary", ""]
    if c.headline:
        L += [f"**{c.headline}**", ""]
    if c.summary:
        L += [c.summary, ""]
    L += [
        f"Confirmed findings: {model.confirmed_count} · Open at re-test: {model.open_count} · "
        f"Hosts in scope: {model.hosts_in_scope} · Endpoints tested: {model.endpoints_tested}",
        "",
    ]
    if c.observations:
        L += ["### Key observations", ""]
        for i, o in enumerate(c.observations):
            fids = ", ".join(model.fid_of(i) or i for i in o.findings) if o.findings else ""
            L += [f"{i + 1}. **{o.title}** — {o.detail}{f' ({fids})' if fids else ''}"]
        L += [""]
    L += ["### Severity distribution", "", *[f"- {sev}: {model.counts[sev]}" for sev in SEVERITIES], ""]
    priorities = c.priorities
    if priorities.immediate or priorities.short_term or priorities.structural:
        L += ["### Strategic priorities", ""]
        for key, title in (
            ("immediate", "Immediate (0–72 hours)"),
            ("short_term", "Short term (≤ 30 days)"),
            ("structural", "Structural (≤ 90 days)"),
        ):
            items = getattr(priorities, key)
            if items:
                L += [f"**{title}**", *[f"- {x}" for x in items], ""]
    if c.worked_well:
        L += [f"**What worked well —** {c.worked_well}", ""]

    L += ["## 02 — Findings overview", ""]
    if not model.findings:
        L += ["_No vulnerabilities were confirmed._", ""]
    else:
        L += ["| ID | Finding | Asset | Severity | Category | Status |", "|---|---|---|---|---|---|"]
        for f in model.findings:
            L += [
                f"| {f.fid} | {md_cell(f.vuln.title)} | {md_cell(f.vuln.host)} | {f.vuln.severity.upper()} | "
                f"{md_cell(' · '.join(f.vuln.owasp) or '—')} | {STATUS_LABEL[f.status]} |"
            ]
        L += [""]

    L += ["## 03 — Attack path", ""]
    if not model.attack_path:
        L += ["_No multi-stage attack path was established._", ""]
    else:
        for i, s in enumerate(model.attack_path):
            fids = ", ".join(s.fids) if s.fids else "—"
            L += [f"{i + 1}. **{STAGE_LABEL[s.stage][0]}** ({fids}) — {s.narrative}"]
        L += [""]

    L += ["## 04 — Detailed findings", ""]
    if not model.findings:
        L += ["_No vulnerabilities were confirmed._", ""]
    for f in (x for x in model.findings if x.status != "false_positive"):
        v = f.vuln
        asset = f"{v.host}:{v.service_port}" if v.service_port else v.host
        L += [f"### {f.fid} · [{v.severity.upper()}] {v.title}", ""]
        reverified = " · re-verified" if f.reverified else ""
        rechecked = f" ({_dt_from_ms(v.rechecked_at)[:10]})" if v.rechecked_at else ""
        L += [f"- **Status:** {STATUS_LABEL[f.status]}{reverified}{rechecked}"]
        L += [f"- **Asset:** {asset} · **Category:** {' · '.join(v.owasp) or '—'} · **CWE:** {v.cwe or '—'}"]
        if v.cvss:
            L += [f"- **CVSS v4.0:** {v.cvss.score:.1f} ({v.cvss.band}) `{v.cvss.vector}`"]
        L += [""]
        L += [v.description or v.evidence or "—", ""]
        if v.impact:
            L += ["**Business impact**", *[f"- {x}" for x in v.impact], ""]
        if v.likelihood:
            L += ["**Likelihood factors**", *[f"- {x}" for x in v.likelihood], ""]
        if not f.artifacts and v.http and (v.http.request or v.http.response):
            L += ["**Evidence**", "", "```http"]
            if v.http.request:
                L += [v.http.request.strip()]
            if v.http.response:
                L += ["", v.http.response.strip()]
            L += ["```", f"_{v.http.caption}_" if v.http.caption else "", ""]
        if f.artifacts:
            L += ["**Evidence artifacts**"]
            for a in f.artifacts:
                sha = sha_by_file.get(Path(a.file).name, "—")
                note = a.caption or a.description
                L += [f"- `evidence/{Path(a.file).name}` — sha256 `{sha}`{f' — {note}' if note else ''}"]
            L += [""]
        if v.remediation:
            L += ["**Remediation**", *[f"{i + 1}. {x}" for i, x in enumerate(v.remediation)], ""]
        if v.references:
            L += [f"**References:** {' · '.join(v.references)}", ""]

    L += ["## 05 — Remediation roadmap", ""]
    for phase in model.roadmap:
        if not phase.rows:
            continue
        L += [f"### {PHASE_TITLE[phase.phase]}", "", "| Action | Closes | Owner | Effort |", "|---|---|---|---|"]
        for row in phase.rows:
            effort = "●" * row.effort if row.effort else "—"
            L += [f"| {md_cell(row.action)} | {', '.join(row.fids) or '—'} | {md_cell(row.owner)} | {effort} |"]
        L += [""]
    if all(not phase.rows for phase in model.roadmap):
        L += ["_No remediation actions are open._", ""]

    L += ["## 06 — Control coverage", "", _coverage_markdown(model), ""]
    if model.retest:
        r = model.retest
        L += [
            "## 07 — Remediation verification",
            "",
            f"Retest from {r.started}: {r.fixed} fixed · {r.partial} partially fixed · "
            f"{r.not_fixed} not fixed · {r.pending} not retested.",
            "",
            "| ID | Finding | Result | Retested | Observation |",
            "|---|---|---|---|---|",
        ]
        for row in r.rows:
            L += [
                f"| {row.fid} | {md_cell(row.title)} | "
                f"{RETEST_LABEL.get(row.result or '', 'Not retested')} | {row.date or '—'} "
                f"| {md_cell(row.note or '')} |"
            ]
        L += [""]
    if c.coverage_notes:
        L += [f"**Coverage notes —** {c.coverage_notes}", ""]

    L += ["## Appendices", ""]
    if evidence:
        L += [
            "### A — Evidence integrity",
            "",
            "| Artifact | Finding | Captured (UTC) | SHA-256 |",
            "|---|---|---|---|",
        ]
        for e in evidence:
            fids = model.evidence_fids.get(e.name) or []
            L += [f"| {md_cell(e.name)} | {', '.join(fids) or '—'} | {e.when[:16].replace('T', ' ')} | `{e.sha}` |"]
        L += [""]
    if store.state.endpoints:
        L += ["### B — Tested surface", "", "| Method | URL | Status | Result |", "|---|---|---|---|"]
        for e in store.state.endpoints:
            L += [f"| {e.method.upper()} | {md_cell(e.url)} | {e.status or '—'} | {md_cell(e.result or e.tested)} |"]
        L += [""]
    gaps = [
        *(f"- Unexplored attack path — {g}" for g in model.untried_pivots),
        *(f"- Unconfirmed lead [{v.severity}] {v.title} ({v.host})" for v in model.leads),
        *(f"- Depth: {line}" for line in model.shallow),
        *(f"- {row.id} {row.title} — not exercised" for row in model.coverage if row.status == "untested"),
    ]
    if gaps:
        L += ["### C — Open testing gaps", "", *gaps, ""]
    if model.figures:
        L += ["### D — Figures", ""]
        for fig in model.figures:
            L += [
                f"![Figure {fig.number} — {fig.title}]({FIGURES_DIR}/{fig.slug}.svg)",
                "",
                f"_Figure {fig.number} — {fig.title}.{f' {fig.caption}' if fig.caption else ''}_",
                "",
            ]
    testers = ", ".join(f"{t.name} ({t.role})" if t.role else t.name for t in c.testers) or "—"
    L += [
        "### E — Engagement details",
        "",
        f"- **Testing period:** {model.test_window}",
        f"- **Scope:** {', '.join(f'`{x}`' for x in model.scope)}",
        f"- **Source IP addresses:** {', '.join(f'`{x}`' for x in c.source_ips) if c.source_ips else '—'}",
        f"- **Testers:** {testers}",
        "- **Approach:** Testing was performed with intern, an AI-assisted penetration testing "
        "harness operated by the testers above, following the OWASP Web Security Testing Guide "
        "and API Security Top 10. Findings were confirmed live and re-checked before reporting.",
        f"- **Limitations and assumptions:** {c.limitations or '—'}",
        "",
    ]
    L += [
        "### F — Severity rating",
        "",
        "Each finding carries a CVSS v4.0 base vector and score. Severity follows the CVSS band "
        "unless the client's environment changes the risk; any finding rated more than one level "
        "away from its band explains why in its likelihood factors.",
        "",
        "| Severity | CVSS v4.0 | Meaning |",
        "|---|---|---|",
        "| Critical | 9.0 – 10.0 | Exploitable now with few or no preconditions; leads directly to "
        "compromise of critical systems or data at scale. Fix immediately. |",
        "| High | 7.0 – 8.9 | Serious compromise of systems or data with modest preconditions, such as "
        "a low-privilege account. Fix within days. |",
        "| Medium | 4.0 – 6.9 | Meaningful impact that needs specific conditions or chaining, or exposes "
        "limited data. Fix in the normal release cycle. |",
        "| Low | 0.1 – 3.9 | Limited impact or hard to exploit; a defence-in-depth improvement. |",
        "| Info | 0.0 | No direct risk: an observation or hardening advice. |",
        "",
        "---",
        f"_© {model.year} · Confidential — prepared for {model.client} · {model.engagement_id}_",
        "",
    ]
    return "\n".join(L)


def _coverage_markdown(model: ReportModel) -> str:
    lines = ["| Category | Status | Note |", "|---|---|---|"]
    label = {"finding": "⚠ finding", "tested": "✓ tested", "na": "n/a", "untested": "✗ UNTESTED"}
    for row in model.coverage:
        lines.append(f"| {row.id} {row.title} | {label.get(row.status, row.status)} | {md_cell(row.note or '')} |")
    accounted = sum(1 for r in model.coverage if r.status != "untested")
    lines += ["", f"{accounted}/{len(model.coverage)} accounted for"]
    return "\n".join(lines)


# -- HTML edition --------------------------------------------------------------------------------------


def _http_terminal(request: str | None, response: str | None, caption: str | None) -> str:
    body = ""
    if request:
        body += f'<span class="step">1 · REQUEST</span>{esc(clamp_lines(request, 14))}'
    if response:
        n = 2 if request else 1
        sep = "\n\n" if request else ""
        body += (
            f'{sep}<span class="step">{n} · RESPONSE</span><span class="resp">{esc(clamp_lines(response, 14))}</span>'
        )
    return (
        f'<div class="term"><div class="bar"><span class="lights"><i></i><i></i><i></i></span>'
        f'<span class="file">request / response as recorded</span></div><pre>{body}</pre></div>'
        f'<p class="cap">{esc(caption_of(caption, "Request and response demonstrating the issue."))} '
        "Secret values are masked.</p>"
    )


def _text_terminal(name: str, body: str) -> str:
    return (
        f'<div class="term"><div class="bar"><span class="lights"><i></i><i></i><i></i></span>'
        f'<span class="file">{esc(name)}</span></div>'
        f'<pre><span class="resp">{esc(clamp_lines(body.rstrip(), 14))}</span></pre></div>'
    )


def _shot(record: EvidenceRecord, evidence_dir: Path) -> str:
    uri = _image_data_uri(record.file, evidence_dir)
    body = (
        f'<img src="{uri}" alt="">'
        if uri
        else f'<div class="shot-missing">Image not embeddable ({esc(Path(record.file).name)}) — '
        "listed in the evidence index.</div>"
    )
    url = esc(record.url or record.name)
    return (
        f'<div class="browser"><div class="chrome"><span class="lights"><i></i><i></i><i></i></span>'
        f'<span class="url">{url}</span></div>{body}</div>'
        f'<p class="cap">{esc(caption_of(record.caption or record.description, "Screenshot during testing."))} '
        "Sensitive values redacted.</p>"
    )


def _paras(text: str | None) -> str:
    """Longer text as HTML: blank-line separated paragraphs, single newlines kept as breaks."""
    if not text:
        return ""
    parts = []
    for para in re.split(r"\n\s*\n", text.strip()):
        rendered = "<br>".join(esc(line) for line in para.splitlines())
        parts.append(f"<p>{rendered}</p>")
    return "".join(parts)


def _finding_evidence_html(f: Finding, evidence_dir: Path) -> str:
    ev = collect_evidence(f, evidence_dir)
    blocks: list[str] = []
    if not ev.shots and not ev.texts and f.vuln.http and (f.vuln.http.request or f.vuln.http.response):
        blocks.append(
            '<div class="blk"><div class="label">Evidence</div>'
            + _http_terminal(f.vuln.http.request, f.vuln.http.response, f.vuln.http.caption)
            + "</div>"
        )
    for shot in ev.shots:
        blocks.append(f'<div class="blk">{_shot(shot, evidence_dir)}</div>')
    for i, text in enumerate(ev.texts):
        label = '<div class="label">Evidence</div>' if i == 0 and not blocks else ""
        blocks.append(
            f'<div class="blk">{label}{_text_terminal(text.name, text.body)}'
            f'<p class="cap">{esc(text.caption)}</p></div>'
        )
    if ev.extra_texts:
        blocks.append(
            f'<div class="blk"><p class="cap">{ev.extra_texts} further artifact(s) for this finding '
            "are listed in the evidence index.</p></div>"
        )
    return "".join(blocks)


def _sev_pill(sev: str) -> str:
    return f'<span class="pill sev-{sev}">{esc(sev)}</span>'


def _status_pill(status: ReportStatus, reverified: bool) -> str:
    label = STATUS_LABEL[status] + (" · re-verified" if status == "open" and reverified else "")
    return f'<span class="pill status st-{status}">{esc(label)}</span>'


def _cvss_link(vector: str) -> str:
    return f"https://www.first.org/cvss/calculator/4-0#{vector}"


def _cwe_link(cwe: str | None) -> str | None:
    match = re.search(r"(\d+)", cwe or "")
    return f"https://cwe.mitre.org/data/definitions/{match.group(1)}.html" if match else None


def _fids(fids: list[str]) -> str:
    return " ".join(f'<span class="fref">{esc(x)}</span>' for x in fids) or "—"


def _finding_html(f: Finding, model: ReportModel, evidence_dir: Path) -> str:
    v = f.vuln
    asset = f"{v.host}:{v.service_port}" if v.service_port else v.host
    retest = _dt_from_ms(v.rechecked_at)[:10] if v.rechecked_at else "—"
    meta = "".join(
        f'<div><div class="k">{esc(k)}</div><div class="v">{esc(val)}</div></div>'
        for k, val in (
            ("Affected asset", asset),
            ("Category", " · ".join(v.owasp) or "—"),
            ("CWE", v.cwe or "—"),
            ("Last re-test", retest),
        )
    )
    parts = [
        f'<div class="blk fhead"><div class="tags"><span class="fid">{f.fid}</span>'
        f"{_sev_pill(v.severity)}{_status_pill(f.status, f.reverified)}</div>"
        f'<div class="ftitle">{esc(v.title)}</div><div class="fmeta">{meta}</div></div>',
        f'<div class="blk"><div class="label">Description</div><div class="prose">'
        f"{_paras(v.description or v.evidence or '—')}</div></div>",
    ]
    if v.impact or v.likelihood:
        impact = "".join(f"<li>{esc(x)}</li>" for x in v.impact) or "<li>—</li>"
        likelihood = "".join(f"<li>{esc(x)}</li>" for x in v.likelihood) or "<li>—</li>"
        parts.append(
            f'<div class="blk impact2"><div class="bi"><div class="t">⚠ Business impact</div>'
            f'<ul class="plain">{impact}</ul></div>'
            f'<div class="lf"><div class="t">◎ Likelihood factors</div><ul class="plain">{likelihood}</ul></div></div>'
        )
    parts.append(_finding_evidence_html(f, evidence_dir))
    if v.remediation:
        steps = "".join(
            f'<li><span class="n">{i + 1}</span><span>{esc(x)}</span></li>' for i, x in enumerate(v.remediation)
        )
        parts.append(f'<div class="blk"><div class="label">Remediation</div><ol class="steps">{steps}</ol></div>')
    refs = list(v.references)
    cwe = _cwe_link(v.cwe)
    if cwe and cwe not in refs:
        refs.append(cwe)
    if v.cvss:
        parts.append(
            f'<div class="blk refsline"><span class="k">CVSS v4.0</span><span>{v.cvss.score:.1f} '
            f"{esc(CVSS_SEV_WORD.get(v.cvss.band, v.cvss.band))} · "
            f'<a href="{esc(_cvss_link(v.cvss.vector))}" rel="noopener">{esc(v.cvss.vector)}</a></span></div>'
        )
    if refs:
        links = " · ".join(f'<a href="{esc(r)}" rel="noopener">{esc(r)}</a>' for r in refs)
        parts.append(f'<div class="blk refsline"><span class="k">References</span><span>{links}</span></div>')
    return "".join(parts)


def _page(model: ReportModel, sec: str, body: str, *, footer_extra: str = "") -> str:
    return (
        f'<section class="page"><div class="hdr"><span class="brand">intern · security testing</span>'
        f'<span class="sec">{esc(sec)}</span></div><div class="body">{body}</div>'
        f'<div class="ftr"><span>Confidential — prepared for {esc(model.client)} · '
        f"{esc(model.engagement_id)}</span><span>{esc(footer_extra)}</span></div></section>"
    )


def _cover(model: ReportModel) -> str:
    c = model.content
    tally = "".join(
        f'<div><b class="on-{sev}">{model.counts[sev]}</b><span class="ck on-{sev}">{sev}</span></div>'
        for sev in ("critical", "high", "medium", "low")
    )
    segs = "".join('<i class="on"></i>' if i < model.rating_segments else "<i></i>" for i in range(8))
    synopsis = esc(c.synopsis or f"Penetration test of {model.target}.")
    return (
        '<section class="page cover"><div class="c-top"><span class="brand">intern · security testing</span>'
        f'<span class="conf">Confidential</span></div>'
        f'<h1>Penetration test report</h1><div class="c-client">{esc(model.client)}</div>'
        f'<div class="c-target">{esc(model.target)}</div>'
        f'<div class="c-meta"><div><span class="k">Engagement</span><span>{esc(model.engagement_id)}</span></div>'
        f'<div><span class="k">Test window</span><span>{esc(model.test_window)}</span></div>'
        f'<div><span class="k">Version</span><span>{esc(model.version)}</span></div>'
        f'<div><span class="k">Generated</span><span>{esc(model.generated)}</span></div></div>'
        f'<div class="c-rating"><div class="rating">{esc(model.rating)}</div>'
        f'<div class="segs">{segs}</div><div class="tally">{tally}</div></div>'
        f'<p class="c-synopsis">{synopsis}</p>'
        '<p class="c-note">Credentials and secret values observed during testing are withheld from this '
        "report and handed over separately via a secure channel.</p></section>"
    )


def _contents(model: ReportModel) -> str:
    rows = [
        ("01 · Executive summary", "Overall risk, key observations and strategic priorities"),
        ("02 · Findings overview", "Severity distribution and consolidated findings register"),
        ("03 · Attack path", "How individual findings chain together"),
        ("04 · Detailed findings", "Description, impact, evidence and remediation per finding"),
        ("05 · Remediation roadmap", "Prioritised actions by time horizon and owner"),
        ("06 · Control coverage", "OWASP Top 10 and API Security Top 10 coverage"),
        *([("07 · Remediation verification", "Retest verdict for every reported finding")] if model.retest else []),
        (
            "Appendices",
            "Evidence integrity, tested surface, open gaps, figures, engagement details, severity rating",
        ),
    ]
    items = "".join(
        f'<div class="toc-row"><span class="n">{i + 1:02d}</span><div><div class="t">{esc(t)}</div>'
        f'<div class="d">{esc(d)}</div></div></div>'
        for i, (t, d) in enumerate(rows)
    )
    return _page(model, "Contents", f'<h2>Contents</h2><div class="toc">{items}</div>')


def _executive(model: ReportModel) -> str:
    c = model.content
    headline = c.headline or (
        f"{model.confirmed_count} confirmed finding{'s' if model.confirmed_count != 1 else ''}; "
        f"overall risk {model.rating.lower()}."
        if model.confirmed_count
        else "No confirmed vulnerabilities."
    )
    stats = "".join(
        f'<div class="stat"><b>{count}</b><span>{esc(label)}</span></div>'
        for count, label in (
            (model.confirmed_count, "Confirmed findings"),
            (model.open_count, "Open at re-test"),
            (model.hosts_in_scope, "Hosts in scope"),
            (model.endpoints_tested, "Endpoints tested"),
        )
    )
    top = max(1, max(model.counts.values()))
    dist = "".join(
        f'<div class="dist"><div class="row">{esc(sev)}<b class="c-{sev}">{model.counts[sev]}</b></div>'
        f'<div class="track"><div class="fill b-{sev}" '
        f'style="width:{model.counts[sev] / top * 100:.0f}%"></div></div></div>'
        for sev in SEVERITIES
    )
    obs = ""
    if c.observations:
        items = "".join(
            f'<div class="obs"><div class="t">{esc(o.title)}</div><p>{esc(o.detail)}</p>'
            + (f'<div class="frefs">{_fids([model.fid_of(x) or x for x in o.findings])}</div>' if o.findings else "")
            + "</div>"
            for o in c.observations
        )
        obs = f'<div class="label">Key observations</div>{items}'
    priorities_html = ""
    p = c.priorities
    if p.immediate or p.short_term or p.structural:
        rows = "".join(
            f'<div class="prio"><div class="t">{esc(title)}</div><ul class="plain">'
            + "".join(f"<li>{esc(x)}</li>" for x in items)
            + "</ul></div>"
            for title, items in (
                ("Immediate (0–72 hours)", p.immediate),
                ("Short term (≤ 30 days)", p.short_term),
                ("Structural (≤ 90 days)", p.structural),
            )
            if items
        )
        priorities_html = f'<div class="label">Strategic priorities</div><div class="prios">{rows}</div>'
    worked = (
        f'<div class="blk positive"><span>✓</span><span>What worked well — {esc(c.worked_well)}</span></div>'
        if c.worked_well
        else ""
    )
    body = (
        f'<h2>Executive summary</h2><p class="headline">{esc(headline)}</p>'
        + (f'<p class="lead">{esc(c.summary)}</p>' if c.summary else "")
        + f'<div class="stats">{stats}</div><div class="dist2">{dist}</div>{obs}{priorities_html}{worked}'
    )
    return _page(model, "01 · Executive summary", body)


def _overview(model: ReportModel) -> str:
    if not model.findings:
        body = (
            '<h2>Findings overview</h2><p class="notes">No vulnerabilities were confirmed. See the '
            "coverage matrix and appendices for the tested surface and unconfirmed leads.</p>"
        )
        return _page(model, "02 · Findings overview", body)
    rows = "".join(
        f'<tr><td class="fid-cell">{f.fid}</td><td>{esc(f.vuln.title)}</td><td>{esc(f.vuln.host)}</td>'
        f"<td>{_sev_pill(f.vuln.severity)}</td><td>{esc(' · '.join(f.vuln.owasp) or '—')}</td>"
        f"<td>{_status_pill(f.status, f.reverified)}</td></tr>"
        for f in model.findings
    )
    body = (
        "<h2>Findings overview</h2>"
        '<table class="reg"><thead><tr><th>ID</th><th>Finding</th><th>Asset</th><th>Severity</th>'
        f"<th>Category</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    return _page(model, "02 · Findings overview", body)


def _attack_path(model: ReportModel) -> str:
    if not model.attack_path:
        body = '<h2>Attack path</h2><p class="notes">No multi-stage attack path was established.</p>'
        return _page(model, "03 · Attack path", body)
    steps = "".join(
        f'<div class="stage tone-{s.tone}"><div class="s-head"><span class="s-n">{i + 1}</span>'
        f'<div><div class="t">{esc(s.title)}</div><div class="d">{esc(s.subtitle)}</div></div>'
        f'<div class="frefs">{_fids(s.fids)}</div></div><p>{esc(s.narrative)}</p></div>'
        for i, s in enumerate(model.attack_path)
    )
    return _page(model, "03 · Attack path", f'<h2>Attack path</h2><div class="stages">{steps}</div>')


def _findings(model: ReportModel, evidence_dir: Path) -> str:
    if not model.findings:
        body = (
            '<h2>Detailed findings</h2><p class="notes">Nothing was confirmed; see the coverage matrix '
            "and appendices for the tested surface and unconfirmed leads.</p>"
        )
        return _page(model, "04 · Detailed findings", body)
    body = "<h2>Detailed findings</h2>" + "".join(
        _finding_html(f, model, evidence_dir) for f in model.findings if f.status != "false_positive"
    )
    return _page(model, "04 · Detailed findings", body)


def _roadmap(model: ReportModel) -> str:
    blocks = []
    for phase in model.roadmap:
        if not phase.rows:
            continue
        title = PHASE_TITLE[phase.phase]
        rows = "".join(
            f'<tr><td>{esc(r.action)}</td><td class="fid-cell">{_fids(r.fids)}</td><td>{esc(r.owner)}</td>'
            f'<td class="effort">{"●" * r.effort if r.effort else "—"}</td></tr>'
            for r in phase.rows
        )
        blocks.append(
            f'<div class="phase"><div class="ph-t">{esc(title)}</div>'
            '<table class="rm"><thead><tr><th>Action</th><th>Closes</th><th>Owner</th><th>Effort</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    if not blocks:
        blocks = ['<p class="notes">No remediation actions are open.</p>']
    return _page(model, "05 · Remediation roadmap", f"<h2>Remediation roadmap</h2>{''.join(blocks)}")


def _coverage(model: ReportModel) -> str:
    def table(list_key: str, title: str) -> str:
        rows = "".join(
            f'<tr><td class="cid">{esc(r.id)}</td><td>{esc(r.title)}</td>'
            f'<td><span class="cv cv-{esc(r.status)}">'
            f"{esc(r.status.upper() if r.status != 'na' else 'N/A')}</span></td></tr>"
            for r in model.coverage
            if r.list == list_key
        )
        if not rows:
            return ""
        return f'<div><div class="label">{esc(title)}</div><table class="cov"><tbody>{rows}</tbody></table></div>'

    body = (
        "<h2>Control coverage</h2>"
        '<p class="lead">Every category in the OWASP Top 10:2025 and API Security Top 10:2023 is '
        "accounted for — either with a finding, tested with no issue identified, or marked not "
        "applicable with a reason.</p>"
        '<div class="cov2">'
        + table("web", "OWASP Top 10 : 2025")
        + table("api", "OWASP API Top 10 : 2023")
        + table("llm", "OWASP Top 10 for LLM Applications : 2025")
        + "</div>"
    )
    if model.content.coverage_notes:
        body += (
            f'<div class="label">Coverage notes</div><div class="prose">{_paras(model.content.coverage_notes)}</div>'
        )
    return _page(model, "06 · Control coverage", body)


def _retest(model: ReportModel) -> str:
    r = model.retest
    if r is None:
        return ""
    rows = "".join(
        f'<tr><td class="fid-cell">{esc(x.fid)}</td><td>{esc(x.title)}</td>'
        f'<td><span class="cv cv-{esc(x.result or "pending")}">'
        f"{esc(RETEST_LABEL.get(x.result or '', 'Not retested'))}</span></td>"
        f"<td>{esc(x.date or '—')}</td><td>{esc(x.note or '')}</td></tr>"
        for x in r.rows
    )
    body = (
        f"<h2>Remediation verification</h2>"
        f'<p class="lead">Retest from {esc(r.started)}: {r.fixed} fixed · {r.partial} partially fixed · '
        f"{r.not_fixed} not fixed · {r.pending} not retested.</p>"
        '<table class="reg"><thead><tr><th>ID</th><th>Finding</th><th>Result</th><th>Retested</th>'
        f"<th>Observation</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    return _page(model, "07 · Remediation verification", body)


def _appendix(model: ReportModel, store: EngagementStore) -> str:
    evidence = read_evidence(store.evidence_dir)
    blocks: list[str] = []
    if evidence:
        rows = "".join(
            f"<tr><td>{esc(e.name)}</td>"
            f'<td class="fid-cell">{esc(", ".join(model.evidence_fids.get(e.name, [])) or "—")}</td>'
            f'<td>{esc(e.when[:16].replace("T", " "))}</td><td class="sha">{e.sha}</td></tr>'
            for e in evidence
        )
        blocks.append(
            '<div class="label">A — Evidence integrity</div>'
            '<p class="lead">Every artifact captured during testing, the finding it proves (shown inline '
            "in that finding), its capture time and SHA-256, so the evidence can be verified against "
            "the originals.</p>"
            f'<table class="ev"><thead><tr><th>Artifact</th><th>Finding</th><th>Captured (UTC)</th>'
            f"<th>SHA-256</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    if store.state.endpoints:
        rows = "".join(
            f'<tr><td class="mono">{esc(e.method)}</td><td class="u">{esc(e.url)}</td>'
            f'<td class="mono">{e.status if e.status is not None else "—"}</td>'
            f"<td>{esc(e.result or e.tested)}</td></tr>"
            for e in store.state.endpoints
        )
        blocks.append(
            '<div class="label">B — Tested surface</div>'
            '<p class="lead">Every endpoint exercised, including those found solid — absence of a '
            "finding here is a tested result.</p>"
            f'<table class="ep"><thead><tr><th>Method</th><th>URL</th><th>Status</th><th>Result</th>'
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    gaps = [
        *(f"Unexplored attack path — {g}." for g in model.untried_pivots),
        *(
            f"Unconfirmed lead [{v.severity}] {v.title} ({v.host}) — not driven to confirmation; "
            "treat as a hypothesis, not a finding."
            for v in model.leads
        ),
        *(f"Depth: {line}" for line in model.shallow),
        *(
            f"{r.id} {r.title} — not exercised; absence of a finding here proves nothing."
            for r in model.coverage
            if r.status == "untested"
        ),
    ]
    if gaps:
        blocks.append(
            '<div class="label">C — Open testing gaps</div>' + "".join(f'<p class="gap">• {esc(g)}</p>' for g in gaps)
        )
    if model.figures:
        blocks.append(
            '<div class="label">D — Figures</div>'
            '<p class="lead">Diagrams drawn from what was observed during testing: architecture, data '
            "flows and connections between components.</p>"
        )
        for fig in model.figures:
            blocks.append(
                f'<div class="figure"><div class="figframe">{fig.svg}</div><div class="figcap">'
                f'<div class="fh"><span class="fn">Figure {fig.number}</span>'
                f'<span class="ft">{esc(fig.title)}</span></div>'
                + (f"<p>{esc(fig.caption)}</p>" if fig.caption else "")
                + "</div></div>"
            )
    testers = "<br>".join(esc(f"{t.name} ({t.role})" if t.role else t.name) for t in model.content.testers) or "—"
    source_ips = "<br>".join(f'<span class="mono">{esc(x)}</span>' for x in model.content.source_ips) or "—"
    blocks += [
        '<div class="label">E — Engagement details</div>'
        '<table class="deflist">'
        f"<tr><th>Testing period</th><td>{esc(model.test_window)}</td></tr>"
        "<tr><th>Scope</th><td>"
        + "<br>".join(f'<span class="mono">{esc(x)}</span>' for x in model.scope)
        + "</td></tr>"
        f"<tr><th>Source IP addresses</th><td>{source_ips}</td></tr>"
        f"<tr><th>Testers</th><td>{testers}</td></tr>"
        "<tr><th>Approach</th><td>Testing was performed with intern, an AI-assisted penetration "
        "testing harness operated by the testers above, following the OWASP Web Security Testing "
        "Guide and API Security Top 10. Findings were confirmed live and re-checked before "
        "reporting.</td></tr>"
        "<tr><th>Limitations and assumptions</th><td>"
        + (_paras(model.content.limitations) or "—")
        + "</td></tr></table>",
        '<div class="label">F — Severity rating</div>'
        '<p class="lead">Each finding carries a CVSS v4.0 base vector and score. Severity follows the '
        "CVSS band unless the client's environment changes the risk; any finding rated more than one "
        "level away from its band explains why in its likelihood factors.</p>"
        + "".join(
            f'<div class="rating-row"><span>{_sev_pill(sev)}</span><span class="mono">CVSS v4.0 {rng}</span>'
            f"— {esc(text)}</div>"
            for sev, rng, text in (
                (
                    "critical",
                    "9.0 – 10.0",
                    "Exploitable now with few or no preconditions; leads directly to compromise of "
                    "critical systems or data at scale. Fix immediately.",
                ),
                (
                    "high",
                    "7.0 – 8.9",
                    "Serious compromise of systems or data with modest preconditions, such as a "
                    "low-privilege account. Fix within days.",
                ),
                (
                    "medium",
                    "4.0 – 6.9",
                    "Meaningful impact that needs specific conditions or chaining, or exposes limited "
                    "data. Fix in the normal release cycle.",
                ),
                ("low", "0.1 – 3.9", "Limited impact or hard to exploit; a defence-in-depth improvement."),
                ("info", "0.0", "No direct risk: an observation or hardening advice."),
            )
        ),
    ]
    return _page(model, "Appendices", "<h2>Appendices</h2>" + "".join(blocks))


def render_html(model: ReportModel, store: EngagementStore) -> str:
    """One self-contained HTML file: inline CSS, embedded images, inline SVG figures, no scripts."""
    pages = [
        _cover(model),
        _contents(model),
        _executive(model),
        _overview(model),
        _attack_path(model),
        _findings(model, store.evidence_dir),
        _roadmap(model),
        _coverage(model),
        _retest(model),
        _appendix(model, store),
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>"
        + esc(f"Penetration test report — {model.client} — {model.target}")
        + f"</title><style>{REPORT_CSS}</style></head><body>"
        f'<div class="doc">{"".join(pages)}</div></body></html>'
    )


# -- PDF (optional browser extra) ---------------------------------------------------------------------


async def render_pdf(html_path: Path, out: Path) -> str | None:
    """Print the HTML report with Playwright's Chromium. Returns None when written, else the reason
    it was skipped — the markdown and HTML editions are still complete."""
    try:
        api = importlib.import_module("playwright.async_api")
    except ImportError:
        from intern.browser import BROWSER_NEEDS

        return f"Chromium is unavailable: {BROWSER_NEEDS.instruction}"
    uri = await asyncio.to_thread(lambda: html_path.resolve().as_uri())
    try:
        async with api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                chromium_sandbox=True,
                args=["--disable-quic", "--disable-background-networking", "--dns-prefetch-disable"],
            )
            try:
                page = await browser.new_page(viewport={"width": 1100, "height": 1500})
                await page.goto(uri, wait_until="load", timeout=60_000)
                await page.pdf(path=str(out), print_background=True, prefer_css_page_size=True)
            finally:
                await browser.close()
    except Exception as error:  # a missing browser build, a crashed print — never a report blocker
        return f"Chromium could not print the report ({error})"
    return None


@dataclass(frozen=True)
class ReportFiles:
    model: ReportModel
    md: Path
    html: Path
    pdf: Path | None
    #: set when the PDF was skipped: the reason (the html and md are still complete).
    pdf_skip: str | None
    figures: Path
    figure_count: int


async def write_report(store: EngagementStore, *, now: int | None = None) -> ReportFiles:
    """Build the model once and write everything into ``store.report_dir``."""
    model = build_model(store, now=now)
    report_dir = store.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = report_dir / FIGURES_DIR
    _write_figures(figures_dir, model)
    md_path = report_dir / "report.md"
    md_path.write_text(render_markdown(model, store), encoding="utf-8")
    html_path = report_dir / "report.html"
    html_path.write_text(render_html(model, store), encoding="utf-8")
    pdf_path = report_dir / "report.pdf"
    skip = await render_pdf(html_path, pdf_path)
    return ReportFiles(
        model=model,
        md=md_path,
        html=html_path,
        pdf=None if skip else pdf_path,
        pdf_skip=skip,
        figures=figures_dir,
        figure_count=len(model.figures),
    )


def _write_figures(figures_dir: Path, model: ReportModel) -> None:
    """Standalone SVGs for report.md (stale figures from earlier runs are removed)."""
    keep = {f"{f.slug}.svg" for f in model.figures}
    if figures_dir.exists():
        for old in figures_dir.iterdir():
            if old.suffix == ".svg" and old.name not in keep:
                old.unlink(missing_ok=True)
    if not model.figures:
        return
    figures_dir.mkdir(parents=True, exist_ok=True)
    for fig in model.figures:
        (figures_dir / f"{fig.slug}.svg").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n{fig.svg}\n', encoding="utf-8"
        )


REPORT_CSS = """
:root{--ink:#14181f;--ink2:#4a5160;--muted:#5f687a;--line:#d8dde6;--paper:#fff;--bg:#eef0f4;
--crit:#d4183d;--high:#c4320a;--med:#b54708;--low:#5f687a;--info:#4a5160;--ok:#0b8a5a}
*{box-sizing:border-box}html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em}
.doc{display:flex;flex-direction:column;align-items:center;gap:28px;padding:28px 0}
.page{width:1000px;min-height:1414px;background:var(--paper);padding:56px 72px 40px;
box-shadow:0 2px 14px rgba(20,24,31,.12);display:flex;flex-direction:column}
.page .body{flex:1}
.hdr{display:flex;justify-content:space-between;align-items:baseline;
border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:26px;font-size:11px;
color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.ftr{display:flex;justify-content:space-between;border-top:1px solid var(--line);
padding-top:10px;margin-top:26px;font-size:10.5px;color:var(--muted)}
h2{font-size:24px;margin:0 0 18px;letter-spacing:-.01em}
h3{font-size:16px;margin:26px 0 10px}
p{margin:0 0 12px}
.lead,.headline{font-size:16px;color:var(--ink2)}
.headline{font-weight:600;color:var(--ink)}
.notes{color:var(--muted)}
.label{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
color:var(--muted);margin:22px 0 8px}
/* cover */
.cover{justify-content:flex-start}
.c-top{display:flex;justify-content:space-between;font-size:12px;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);margin-bottom:150px}
.cover h1{font-size:44px;margin:0 0 8px;letter-spacing:-.02em}
.c-client{font-size:24px;font-weight:600;margin-bottom:2px}
.c-target{font-size:15px;color:var(--muted);margin-bottom:44px}
.c-meta{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:44px}
.c-meta .k{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;
color:var(--muted)}
.c-meta span{font-size:13.5px}
.c-rating{border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin-bottom:28px}
.c-rating .rating{font-size:28px;font-weight:700}
.segs{display:flex;gap:4px;margin:12px 0 16px}
.segs i{width:34px;height:8px;border-radius:4px;background:var(--line)}
.segs i.on{background:var(--crit)}
.tally{display:flex;gap:22px}
.tally b{font-size:22px;display:block}
.tally .ck{font-size:11px;color:var(--muted)}
.tally .on-critical{color:var(--crit)}.tally .on-high{color:var(--high)}
.tally .on-medium{color:var(--med)}.tally .on-low{color:var(--low)}
.c-synopsis{max-width:70%}
.c-note{margin-top:auto;color:var(--muted);font-size:12px}
/* contents */
.toc-row{display:flex;gap:18px;align-items:baseline;padding:13px 0;
border-bottom:1px solid var(--line)}
.toc-row .n{color:var(--muted);font-size:12px}
.toc-row .t{font-weight:600}
.toc-row .d{color:var(--muted);font-size:13px}
/* executive */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}
.stat{border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{font-size:24px;display:block}
.stat span{color:var(--muted);font-size:12px}
.dist2{display:flex;gap:18px;margin-bottom:8px}
.dist{flex:1}
.dist .row{display:flex;justify-content:space-between;font-size:12px;color:var(--ink2)}
.c-critical,.b-critical{color:var(--crit);background:var(--crit)}
.c-high,.b-high{color:var(--high);background:var(--high)}
.c-medium,.b-medium{color:var(--med);background:var(--med)}
.c-low,.b-low{color:var(--low);background:var(--low)}
.c-info,.b-info{color:var(--info);background:var(--info)}
.track{height:6px;border-radius:3px;background:var(--line);margin:4px 0 6px}
.fill{height:6px;border-radius:3px}
.obs{border-left:3px solid var(--line);padding:4px 0 4px 14px;margin:14px 0}
.obs .t{font-weight:600;margin-bottom:2px}
.frefs{margin-top:4px}
.fref{display:inline-block;background:#f0f3f8;border:1px solid var(--line);border-radius:5px;
padding:0 6px;font-size:11px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
margin-right:4px}
.prios{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.prio .t{font-weight:600;margin-bottom:6px}
.prio li{margin:0 0 6px 18px}
.blk{margin:0 0 22px}
.blk.positive{background:#eefaf3;border:1px solid #bfe6d4;border-radius:10px;
padding:12px 14px;color:#0b6b46}
/* tables */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;
color:var(--muted);border-bottom:2px solid var(--line);padding:7px 8px}
td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}
.fid-cell{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
.sha{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;
word-break:break-all;color:var(--muted)}
.pill{display:inline-block;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:600}
.sev-critical{background:#fbe0e6;color:#a80f31}.sev-high{background:#fce7dd;color:#9e2a08}
.sev-medium{background:#fdf0dc;color:#8f3c07}
.sev-low{background:#edf0f5;color:#3d4556}.sev-info{background:#eef1f6;color:#4a5160}
.status{border:1px solid var(--line);color:var(--ink2);font-weight:500}
.cv{display:inline-block;border-radius:4px;padding:1px 7px;font-size:10.5px;font-weight:700;
letter-spacing:.04em}
.cv-finding{background:#fbe0e6;color:#a80f31}.cv-tested{background:#eefaf3;color:#0b6b46}
.cv-na{background:#eef1f6;color:var(--muted)}
.cv-untested{border:1px dashed var(--crit);color:var(--crit)}
.cv-fixed{background:#eefaf3;color:#0b6b46}.cv-not_fixed{background:#fbe0e6;color:#a80f31}
.cv-partial{background:#fdf0dc;color:#8f3c07}
/* findings */
.fhead{border-top:3px solid var(--ink);padding-top:12px}
.fid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700;font-size:13px}
.fmeta{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px}
.fmeta .k{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.fmeta .v{font-size:12.5px}
.ftitle{font-size:20px;font-weight:600;margin-top:6px}
ul.plain,ol.steps{list-style:none;margin:0;padding:0}
ul.plain li{margin:0 0 6px;padding-left:14px;position:relative}
ul.plain li::before{content:"•";position:absolute;left:0;color:var(--muted)}
ol.steps{counter-reset:step}
ol.steps li{display:flex;gap:10px;margin:0 0 8px}
ol.steps li::before{content:counter(step);flex:none;width:20px;height:20px;border-radius:10px;
background:#f0f3f8;color:var(--ink2);font-size:11px;font-weight:700;display:flex;
align-items:center;justify-content:center;margin-top:1px}
.impact2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.impact2 .t{font-weight:600;font-size:12.5px;margin-bottom:6px}
.term{border:1px solid #272c36;border-radius:10px;overflow:hidden;margin:10px 0 6px;
background:#14181f}
.term .bar{display:flex;align-items:center;gap:8px;background:#272c36;color:#9aa3b5;
font-size:11px;padding:6px 10px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.term .lights i{display:inline-block;width:9px;height:9px;border-radius:5px;background:#3d4453;
margin-right:4px}
.term pre{margin:0;padding:12px 14px;color:#dbe2ee;
font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
white-space:pre-wrap;word-break:break-word}
.term .step{display:block;color:#7f8ba3;margin-bottom:4px}
.term .resp{color:#cfe0d4}
.cap{font-size:11.5px;color:var(--muted);margin:2px 0 0}
.browser{border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:10px 0 6px}
.browser .chrome{display:flex;align-items:center;gap:10px;background:#f0f3f8;padding:7px 12px}
.browser .lights i{display:inline-block;width:10px;height:10px;border-radius:5px;margin-right:5px}
.browser .lights i:nth-child(1){background:#f26d6d}
.browser .lights i:nth-child(2){background:#f2c94c}
.browser .lights i:nth-child(3){background:#6dcf8e}
.browser .url{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.browser img{display:block;width:100%;background:#fff}
.shot-missing{padding:36px;text-align:center;font-size:12px;color:var(--muted)}
.refsline{display:flex;gap:12px;font-size:13px;border-top:1px solid var(--line);
padding-top:10px}
.refsline .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;
color:var(--muted);flex:none;padding-top:2px}
a{color:#1258ff;text-decoration:none;border-bottom:1px solid #b9ccff}
/* roadmap, coverage, appendix */
.phase{margin-bottom:18px}
.ph-t{font-weight:700;margin:8px 0 6px}
.effort{white-space:nowrap;color:var(--high)}
.cov2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:10px}
.cid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap;
color:var(--muted)}
.gap{font-size:13px;color:var(--ink2);margin:0 0 7px}
.deflist th{width:220px;vertical-align:top;border-bottom:1px solid var(--line)}
.rating-row{display:flex;gap:12px;align-items:baseline;font-size:13px;margin:0 0 9px}
.figure{margin:20px 0}
.figframe{border:1px solid var(--line);border-radius:10px;padding:8px;overflow:hidden}
.figframe svg{display:block;width:100%;height:auto}
.figcap .fh{margin-top:8px;font-size:13px}
.figcap .fn{font-weight:700;margin-right:8px}
.figure + .figure{border-top:1px solid var(--line);padding-top:16px}
/* print: one A4-ish sheet per section, no scripts needed */
@media print{@page{size:1000px 1414px;margin:0}
body{background:#fff}.doc{gap:0;padding:0}
.page{box-shadow:none;break-after:page;page-break-after:always}
.page:last-child{break-after:auto;page-break-after:auto}}
"""

__all__ = [
    "CAPTION_MAX",
    "FIGURES_DIR",
    "HAR_ROWS",
    "EvidenceFile",
    "ReportFiles",
    "read_evidence",
    "render_html",
    "render_markdown",
    "render_pdf",
    "summarize_har",
    "write_report",
]

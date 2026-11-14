"""OWASP coverage: the categories a web/API engagement must account for, and the matrix that
shows which ones this run actually touched.

A category is accounted for when a finding or a tested endpoint is tagged with it, or when the
agent explicitly records it as tested-clean or n/a (with a reason). Anything else is a gap the
report surfaces. Lists: OWASP Top 10:2025 (A01–A10), OWASP API Security Top 10:2023 (API1–API10),
and the OWASP Top 10 for LLM Applications 2025 (LLM01–LLM10), which joins once an AI feature is
tagged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from intern.engagement.models import CoverageMark, Endpoint, Vuln

OwaspList = Literal["web", "api", "llm"]


@dataclass(frozen=True)
class OwaspCategory:
    id: str
    title: str
    list: OwaspList


OWASP_CATEGORIES: tuple[OwaspCategory, ...] = (
    OwaspCategory("A01", "Broken Access Control", "web"),
    OwaspCategory("A02", "Security Misconfiguration", "web"),
    OwaspCategory("A03", "Software Supply Chain Failures", "web"),
    OwaspCategory("A04", "Cryptographic Failures", "web"),
    OwaspCategory("A05", "Injection", "web"),
    OwaspCategory("A06", "Insecure Design", "web"),
    OwaspCategory("A07", "Authentication Failures", "web"),
    OwaspCategory("A08", "Software or Data Integrity Failures", "web"),
    OwaspCategory("A09", "Security Logging and Alerting Failures", "web"),
    OwaspCategory("A10", "Mishandling of Exceptional Conditions", "web"),
    OwaspCategory("API1", "Broken Object Level Authorization", "api"),
    OwaspCategory("API2", "Broken Authentication", "api"),
    OwaspCategory("API3", "Broken Object Property Level Authorization", "api"),
    OwaspCategory("API4", "Unrestricted Resource Consumption", "api"),
    OwaspCategory("API5", "Broken Function Level Authorization", "api"),
    OwaspCategory("API6", "Unrestricted Access to Sensitive Business Flows", "api"),
    OwaspCategory("API7", "Server Side Request Forgery", "api"),
    OwaspCategory("API8", "Security Misconfiguration", "api"),
    OwaspCategory("API9", "Improper Inventory Management", "api"),
    OwaspCategory("API10", "Unsafe Consumption of APIs", "api"),
)

#: Accounted for once an engagement touches an AI feature.
LLM_CATEGORIES: tuple[OwaspCategory, ...] = (
    OwaspCategory("LLM01", "Prompt Injection", "llm"),
    OwaspCategory("LLM02", "Sensitive Information Disclosure", "llm"),
    OwaspCategory("LLM03", "Supply Chain", "llm"),
    OwaspCategory("LLM04", "Data and Model Poisoning", "llm"),
    OwaspCategory("LLM05", "Improper Output Handling", "llm"),
    OwaspCategory("LLM06", "Excessive Agency", "llm"),
    OwaspCategory("LLM07", "System Prompt Leakage", "llm"),
    OwaspCategory("LLM08", "Vector and Embedding Weaknesses", "llm"),
    OwaspCategory("LLM09", "Misinformation", "llm"),
    OwaspCategory("LLM10", "Unbounded Consumption", "llm"),
)

_IDS = {c.id: c for c in (*OWASP_CATEGORIES, *LLM_CATEGORIES)}
_ALIASES: dict[str, str] = {}
for _c in (*OWASP_CATEGORIES, *LLM_CATEGORIES):
    _n = int(re.sub(r"\D", "", _c.id))
    if _c.list == "web":
        _ALIASES[f"a{_n:02d}"] = _c.id
        _ALIASES[f"a{_n}"] = _c.id
    elif _c.list == "api":
        _ALIASES[f"api-{_n}"] = _c.id
        _ALIASES[f"api{_n:02d}"] = _c.id
        _ALIASES[f"api{_n}"] = _c.id
    else:
        _ALIASES[f"llm{_n:02d}"] = _c.id
        _ALIASES[f"llm{_n}"] = _c.id

#: "A01 Broken Access Control · A02 Security Misconfiguration · …" — the 2025 numbering spelled
#: out, so agents don't tag with the 2021 ids (where A02 was Cryptographic Failures, A03 Injection).
OWASP_ID_GUIDE = " · ".join(f"{c.id} {c.title}" for c in OWASP_CATEGORIES)

COVERAGE_NOTE_MAX = 200


def owasp_title(category_id: str) -> str:
    return _IDS[category_id].title if category_id in _IDS else category_id


def normalize_owasp(tags: Sequence[str] | None) -> tuple[list[str], list[str]]:
    """Normalize free-form tags ("a1", "A01:2025", "API-1", "api01") to catalog ids; unknown tags
    are returned separately so the caller can reject them instead of silently dropping coverage."""
    ids: list[str] = []
    unknown: list[str] = []
    for raw in tags or []:
        base = re.split(r"[:\s]", raw.strip().upper(), maxsplit=1)[0]  # "A10 Server-Side …" → A10
        found = _ALIASES.get(base.lower(), base)
        if found in _IDS:
            if found not in ids:
                ids.append(found)
        elif raw not in unknown:
            unknown.append(raw)
    return ids, unknown


#: An n/a note that really says "not tested" (no time, budget, skipped). n/a means the category
#: can't apply to the target; a category nobody got to stays untested.
_NA_EXCUSE = re.compile(
    r"\b(?:not|never|wasn'?t|weren'?t)\s+(?:been\s+)?"
    r"(?:exercised|tested|covered|assessed|checked|examined|evaluated|reached|attempted)\b"
    r"|\bno time\b|\bout of time\b|\btime[- ](?:budget|limit|box|constraints?)\b"
    r"|\bsmoke test\b|\bskipped\b|\bdeferred\b",
    re.IGNORECASE,
)


def na_excuse(note: str | None) -> bool:
    return bool(note) and bool(_NA_EXCUSE.search(note))


CoverageStatus = Literal["finding", "tested", "na", "untested"]


@dataclass(frozen=True)
class CoverageRow:
    id: str
    title: str
    list: OwaspList
    status: CoverageStatus
    note: str | None = None


def coverage_categories(llm_touched: bool) -> tuple[OwaspCategory, ...]:
    """Every category coverage can account for; the LLM list joins only once something is
    LLM-tagged (a target without an AI feature is never in LLM scope)."""
    return OWASP_CATEGORIES + (LLM_CATEGORIES if llm_touched else ())


def _llm_touched(vulns: Sequence[Vuln], endpoints: Sequence[Endpoint]) -> bool:
    return any(any(t.startswith("LLM") for t in v.owasp) for v in vulns) or any(
        any(t.startswith("LLM") for t in e.owasp) for e in endpoints
    )


def coverage_matrix(
    vulns: Sequence[Vuln],
    endpoints: Sequence[Endpoint],
    coverage: Sequence[CoverageMark],
) -> list[CoverageRow]:
    """Build the coverage matrix. Per category: a finding (non-false-positive) tagged with it
    wins; else an explicit mark (an n/a that reads as an excuse counts as untested); else any
    endpoint tagged with it counts as tested; else the category is a gap."""
    marks: Mapping[str, CoverageMark] = {m.id: m for m in coverage}
    rows: list[CoverageRow] = []
    for cat in coverage_categories(_llm_touched(vulns, endpoints)):
        status: CoverageStatus = "untested"
        note: str | None = None
        if any(v.status != "false_positive" and cat.id in v.owasp for v in vulns):
            status = "finding"
        elif (mark := marks.get(cat.id)) is not None:
            if mark.status == "na" and na_excuse(mark.note):
                status, note = "untested", mark.note
            else:
                status, note = mark.status, mark.note
        elif any(cat.id in (e.owasp or []) for e in endpoints):
            status = "tested"
        rows.append(CoverageRow(cat.id, cat.title, cat.list, status, note))
    return rows


def coverage_gaps(rows: Sequence[CoverageRow]) -> list[CoverageRow]:
    return [row for row in rows if row.status == "untested"]


_STATUS_LABEL: dict[CoverageStatus, str] = {
    "finding": "⚠ finding",
    "tested": "✓ tested",
    "na": "n/a",
    "untested": "✗ UNTESTED",
}


def render_coverage_markdown(rows: Sequence[CoverageRow]) -> str:
    lines = ["| Category | Status | Note |", "|---|---|---|"]
    for row in rows:
        note = (row.note or "").replace("|", "\\|")
        lines.append(f"| {row.id} {row.title} | {_STATUS_LABEL[row.status]} | {note} |")
    accounted = sum(1 for r in rows if r.status != "untested")
    lines.append("")
    lines.append(f"{accounted}/{len(rows)} accounted for")
    return "\n".join(lines)


def render_gap_lines(rows: Sequence[CoverageRow], playbooks_for: Callable[[str], Sequence[str]]) -> list[str]:
    """One line per gap, with the playbook to load — for tool results and the report's gap list."""
    lines: list[str] = []
    for row in coverage_gaps(rows):
        playbooks = playbooks_for(row.id)
        method = f"load_playbook surface={playbooks[0]}" if playbooks else "state_update action=coverage"
        lines.append(f"{row.id} {row.title}: {method} …, or coverage_status=na notes=<why it can't apply>")
    return lines

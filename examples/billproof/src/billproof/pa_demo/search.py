"""Per-case Drex Search: policy slice + packet in a workspace; per criterion,
find the record that addresses it (quote + page) or confirm none does.

Quotes are verified word-for-word against the packet PDF text by code.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from billproof.bench.pa.cases import PaCase
from billproof.search.client import WorkspaceClient, answer_of, cost_of

MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "matches": {"type": "array", "items": {"type": "object", "properties": {
            "criterion": {"type": "string", "description": "criterion name as given"},
            "record_id": {"type": ["string", "null"], "description": "record id like R2, or null"},
            "document": {"type": "string"},
            "page": {"type": ["integer", "null"]},
            "quote": {"type": ["string", "null"], "description": "verbatim text from the record"},
        }, "required": ["criterion", "record_id", "quote"]}},
    },
    "required": ["matches"],
}


def workspace_name(slug: str) -> str:
    return f"billproof-{slug.lower()}"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def quote_in_pdf(quote: str, pdf: Path) -> dict:
    from pypdf import PdfReader

    q = _norm(quote)
    if not q:
        return {"verbatim": False, "page": None}
    for n, p in enumerate(PdfReader(str(pdf)).pages, 1):
        if q in _norm(p.extract_text() or ""):
            return {"verbatim": True, "page": n}
    return {"verbatim": False, "page": None}


def match_query(case: PaCase) -> str:
    crits = "; ".join(f"{name}: \"{txt}\"" for name, txt in
                      ((c.get("fact"), c.get("text")) for c in case.policy.get("criteria_list") or []))
    return (
        "This workspace holds a payer prior-authorization criteria page and one synthetic "
        "clinical packet (documents R1..Rn). For each approval criterion below, find which "
        "enclosed record (if any) addresses it — the record that documents it satisfied, "
        "not satisfied, or missing. Quote the record's text word for word, with its page. "
        "If no record addresses a criterion, say record_id null.\nCriteria: " + crits
    )


def run_match(slug: str, case: PaCase, files: dict[str, Path],
              client: WorkspaceClient | None = None) -> dict[str, Any]:
    """files: {'policy': policy_slice.pdf, 'packet': packet.pdf}. Records timings/cost."""
    c = client or WorkspaceClient()
    ws = c.workspace(workspace_name(slug))
    t0 = time.monotonic()
    have = {f.get("path") for f in c.files(ws)}
    for kind, pdf in files.items():
        path = f"{slug}/{Path(pdf).name}"
        if path not in have:
            c.upload(ws, path, Path(pdf).read_bytes())
    job = c.ingest(ws, f"{slug}/")
    ingest_s = round(time.monotonic() - t0, 1)
    query = match_query(case)
    t0 = time.monotonic()
    sjob = c.search(ws, query, mode="agent", path_prefix=f"{slug}/", output_schema=MATCH_SCHEMA)
    search_s = round(time.monotonic() - t0, 1)
    ans = answer_of(sjob)
    sa = ans.get("structured_answer") or {}
    packet = files["packet"]
    matches = []
    for m in sa.get("matches") or []:
        ver = quote_in_pdf(m.get("quote") or "", packet) if m.get("quote") else {"verbatim": False, "page": None}
        matches.append({**m, "verbatim": ver["verbatim"], "page_checked": ver["page"],
                        "page_ok": ver["page"] == m.get("page")})
    return {
        "workspace_id": ws, "query": query, "job_id": sjob.get("job_id"),
        "status": sjob.get("status"), "matches": matches,
        "evidences": [{k: e.get(k) for k in ("source_path", "page", "quote")}
                      for e in ans.get("evidences") or []],
        "ingest_seconds": ingest_s, "search_seconds": search_s,
        "cost_usd": cost_of(sjob) or _usage_cost(sjob),
    }


def _usage_cost(job: dict) -> float | None:
    out = ((job.get("result") or {}).get("output") or {})
    usage = (out.get("answer") or {}).get("usage") if isinstance(out.get("answer"), dict) else out.get("usage")
    if not usage:
        return None
    return round(usage.get("input_tokens", 0) * 0.10 / 1e6
                 + usage.get("output_tokens", 0) * 0.50 / 1e6, 5)

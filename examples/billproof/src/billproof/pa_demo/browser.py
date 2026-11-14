"""One recorded Nace browser run: find the PA criteria documents on the public
Prime Therapeutics Arkansas Medicaid provider-documents page and quote the
listing text verbatim. Quotes are verified by code — row/heading quotes against
the HTML page, and the document link against the PDF it serves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from billproof.browser.runs import start_and_wait, _norm, _now
from billproof.pa_demo.showcase import PA_DEMO_DIR

RECORD_PATH = PA_DEMO_DIR / "browser_run.json"

TASK: dict[str, Any] = {
    "kind": "policy_lookup",
    "title": "Find Arkansas Medicaid PA criteria on Prime Therapeutics provider page",
    "task": (
        "You are on the public Prime Therapeutics Arkansas Medicaid provider documents page "
        "(hackathon demo use). Stay on this website; do not log in and do not submit forms. "
        "Find the row whose description is \"Prior Authorization Criteria\" and copy the full "
        "URL of its document link as pacriteria_url. Find the row \"Physician Administered Drug "
        "PA Criteria\" and copy its document link URL as pad_url. Copy word for word, as "
        "row_quote, the description and file name shown for the Prior Authorization Criteria "
        "row (for example 'Prior Authorization Criteria PACriteria.pdf'). Copy the visible page "
        "title or main heading as heading_quote. If a document link opens a readable document, "
        "find the approval criterion for SILDENAFIL (Revatio) and copy one criterion line word "
        "for word as criterion_quote; otherwise leave criterion_quote empty. Do not invent text."),
    "start_url": "https://ar.primetherapeutics.com/provider-documents",
    "allowed_origins": ["https://ar.primetherapeutics.com"],
    "answer_schema": {"type": "object", "properties": {
        "pacriteria_url": {"type": "string"}, "pad_url": {"type": "string"},
        "row_quote": {"type": "string"}, "heading_quote": {"type": "string"},
        "criterion_quote": {"type": "string"}, "page_url": {"type": "string"}},
        "required": ["pacriteria_url", "pad_url", "row_quote", "heading_quote", "page_url"]},
    "max_cost_usd": 0.08, "max_seconds": 300,
}


def _pdf_text_sample(url: str) -> str | None:
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return None
        from io import BytesIO
        from pypdf import PdfReader
        rd = PdfReader(BytesIO(r.content))
        return " ".join((p.extract_text() or "") for p in rd.pages)
    except Exception:
        return None


def verify(answer: dict | None, start_url: str) -> dict:
    """Verify quotes verbatim against the page; verify the doc link serves a PDF."""
    out: dict[str, Any] = {"checked": False, "quotes": [], "doc_links": []}
    if not answer:
        return out
    from billproof.browser.runs import page_text
    try:
        text = page_text(start_url)
        out["checked"] = True
    except Exception as exc:
        out["error"] = str(exc)
        text = ""
    for field in ("row_quote", "heading_quote"):
        q = _norm(answer.get(field) or "")
        out["quotes"].append({"field": field, "quote": answer.get(field),
                              "verbatim": bool(q) and q in text})
    for field in ("pacriteria_url", "pad_url"):
        url = answer.get(field) or ""
        ok = False
        if url.startswith("http"):
            try:
                r = httpx.get(url, timeout=60, follow_redirects=True)
                ok = r.status_code == 200 and r.content[:4] == b"%PDF"
            except Exception:
                ok = False
        out["doc_links"].append({"field": field, "url": url, "serves_pdf": ok})
    cq = answer.get("criterion_quote") or ""
    if cq and out["doc_links"]:
        pdf_text = _pdf_text_sample(out["doc_links"][0]["url"])
        out["quotes"].append({"field": "criterion_quote", "quote": cq,
                              "verbatim": bool(pdf_text) and _norm(cq) in _norm(pdf_text or ""),
                              "against": "pdf"})
    return out


def record() -> dict:
    run, arts, secs = start_and_wait(TASK)
    final_url = run.get("final_url") or TASK["start_url"]
    rec = {
        "task": "pa_criteria_lookup", "kind": TASK["kind"], "title": TASK["title"],
        "run_id": run.get("id"), "status": run.get("status"),
        "outcome": (run.get("outcome") or {}).get("kind"),
        "reason": (run.get("outcome") or {}).get("reason"),
        "seconds": secs, "cost_usd": (run.get("usage") or {}).get("cost_usd"),
        "usage": run.get("usage"), "start_url": TASK["start_url"], "final_url": final_url,
        "answer": run.get("answer"), "steps": run.get("steps") or [],
        "evidence": (run.get("outcome") or {}).get("evidence") or [],
        "screenshots": [{"kind": x.get("kind"), "step": x.get("step")} for x in arts],
        "verification": verify(run.get("answer"), TASK["start_url"]),
        "recorded_at": _now(),
        "provenance": "recorded live Nace browser run; replayed without network",
    }
    RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECORD_PATH.write_text(json.dumps(rec, indent=1))
    return rec


def recorded() -> dict | None:
    return json.loads(RECORD_PATH.read_text()) if RECORD_PATH.is_file() else None

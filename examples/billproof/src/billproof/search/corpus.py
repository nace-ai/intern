"""Which files make up a case workspace, and checks that a quote is really in them."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from billproof.config import DATA_DIR

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
_PDF_DIRS = (DATA_DIR / "showcase" / "pdf", DATA_DIR / "synthetic" / "pdf", CORPUS_DIR)
_PACKET = ("bill", "eob", "denial", "plan")
_EXTRA = {"coverage": "Plan Evidence of Coverage", "ed_record": "ED record", "policy": "Published imaging guideline"}
LABELS = {"bill": "Itemized bill", "eob": "Explanation of benefits", "denial": "Denial notice",
          "plan": "Summary of benefits", **_EXTRA}


def case_docs(case_id: str) -> list[dict]:
    """[{kind, label, local, path}] for every document on disk for this case; path = workspace path."""
    docs = []
    for kind in (*_PACKET, *_EXTRA):
        for d in _PDF_DIRS:
            p = d / f"{case_id}_{kind}.pdf"
            if p.exists():
                docs.append({"kind": kind, "label": LABELS[kind], "local": p, "path": f"{case_id}/{p.name}"})
                break
    return docs


def kind_of(source_path: str) -> str:
    stem = Path(source_path or "").stem
    return stem.split("_", 1)[1] if "_" in stem else stem


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@lru_cache(maxsize=64)
def _pages(local: str) -> tuple[str, ...]:
    from pypdf import PdfReader

    return tuple(_norm(p.extract_text() or "") for p in PdfReader(local).pages)


def locate_quote(case_id: str, source_path: str, quote: str) -> dict:
    """Is the quote verbatim (ignoring case/punctuation/wrapping) in the cited file, and on which page?"""
    q = _norm(quote)
    doc = next((d for d in case_docs(case_id) if Path(d["path"]).name == Path(source_path or "").name), None)
    if not q or doc is None:
        return {"verbatim": False, "page": None}
    for n, text in enumerate(_pages(str(doc["local"])), 1):
        if q in text:
            return {"verbatim": True, "page": n}
    return {"verbatim": False, "page": None}


def write_policy_pdf(case_id: str, policy: dict) -> Path:
    """The browser-fetched guideline as a workspace file: source line + verbatim page excerpts."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out = CORPUS_DIR / f"{case_id}_policy.pdf"
    c = canvas.Canvas(str(out), pagesize=letter)
    y = 740
    rows = [("PUBLISHED IMAGING GUIDELINE (FETCHED BY BROWSER RUN)", True),
            (f"{policy.get('title')} · Doc ID {policy.get('policy_number')} · effective {policy.get('effective_date')}", False),
            (f"Source: {policy.get('url')} · retrieved {policy.get('retrieved_at')}", False),
            (f"Section: {policy.get('section')}", True)]
    rows += [(f"... {ctx} ...", False) for ctx in policy.get("contexts") or []]
    for text, bold in rows:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
        words, line = text.split(), ""
        for w in words:
            if len(line) + len(w) > 105:
                c.drawString(50, y, line)
                y -= 13
                line = w
            else:
                line = f"{line} {w}".strip()
        c.drawString(50, y, line)
        y -= 20
    c.save()
    return out

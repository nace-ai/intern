"""Forge test invoices: swap the printed IBAN and add notes (PyMuPDF + pyarrow).

Reads source invoices from the dataset parquet only; never reads expected labels.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / "dataset" / "data" / "invoice" / "test-00001.parquet"
PDF_DIR = ROOT / "backend" / "data" / "pdfs"
COMPANY = "cobalt-wholesale-ltd"
META_COLS = ["doc_id", "company", "pages", "words", "extracted_data",
             "number", "total", "currency", "counterparty_name"]
STYLES: dict[str, tuple[float, tuple[float, float, float]]] = {
    "body": (9.0, (0.0, 0.0, 0.0)),
    "faint": (6.0, (0.6, 0.6, 0.6)),
}
MIN_FONT = 4.0
PAD = 3.0


def _has_iban_label(words_json: str) -> bool:
    return any(w["text"] == "IBAN:" and w["page"] == 0 for w in json.loads(words_json))


def list_sources(limit: int | None = None) -> list[dict[str, Any]]:
    """One-page Cobalt invoices that print `IBAN:`, sorted by doc_id."""
    table = pq.read_table(PARQUET, columns=META_COLS)
    out: list[dict[str, Any]] = []
    for row in table.to_pylist():
        if row["company"] != COMPANY or row["pages"] != 1:
            continue
        if not _has_iban_label(row["words"]):
            continue
        bank = json.loads(row["extracted_data"])["parties"]["issuer"].get("bank") or {}
        out.append({
            "doc_id": row["doc_id"],
            "number": row["number"],
            "total": row["total"],
            "currency": row["currency"],
            "counterparty_name": row["counterparty_name"],
            "iban": str(bank.get("iban") or ""),
        })
    out.sort(key=lambda r: r["doc_id"])
    return out[:limit] if limit is not None else out


def _read_row(doc_id: str, columns: list[str]) -> dict[str, Any]:
    table = pq.read_table(PARQUET, columns=["doc_id"])
    mask = pc.equal(table.column("doc_id"), doc_id)
    idx = pc.indices_nonzero(mask).to_pylist()
    if not idx:
        raise KeyError(f"doc_id not found: {doc_id}")
    pf = pq.ParquetFile(PARQUET)
    i = idx[0]
    start = 0
    for rg in range(pf.num_row_groups):
        n = pf.metadata.row_group(rg).num_rows
        if i < start + n:
            rows = pf.read_row_group(rg, columns=columns).slice(i - start, 1)
            return rows.to_pylist()[0]
        start += n
    raise KeyError(doc_id)


def load_pdf(doc_id: str) -> bytes:
    return _read_row(doc_id, ["pdf"])["pdf"]["bytes"]


def load_words(doc_id: str) -> str:
    return _read_row(doc_id, ["words"])["words"]


def random_iban(seed: str, country: str = "GB", length: int = 24) -> str:
    """Deterministic fake IBAN (no valid checksum), printed in groups of four.

    Default length 24 matches the dataset, e.g. "GB18 1790 8734 9062 1374 9599".
    """
    rng = random.Random(seed)
    raw = country + "".join(str(rng.randrange(10)) for _ in range(length - len(country)))
    return " ".join(raw[i:i + 4] for i in range(0, len(raw), 4))


# ---------------------------------------------------------------------------
# IBAN swap
# ---------------------------------------------------------------------------

def iban_rect(words_json: str) -> pymupdf.Rect:
    """Union of the word boxes right of `IBAN:` on the same line, page 0."""
    words = [w for w in json.loads(words_json) if w["page"] == 0]
    label = next((w for w in words if w["text"] == "IBAN:"), None)
    if label is None:
        raise ValueError("no IBAN: label on page 0")
    y0, lx1 = label["bbox"][1], label["bbox"][2]
    tokens = [w for w in words
              if abs(w["bbox"][1] - y0) <= 1.0 and w["bbox"][0] >= lx1 and w is not label]
    if not tokens:
        raise ValueError("no IBAN tokens right of IBAN:")
    rect = pymupdf.Rect(tokens[0]["bbox"])
    for w in tokens[1:]:
        rect |= pymupdf.Rect(w["bbox"])
    return rect


def _span_at(page: pymupdf.Page, rect: pymupdf.Rect) -> dict[str, Any] | None:
    """Text span whose box overlaps the middle of `rect` (gives size and baseline)."""
    probe = pymupdf.Point(rect.x0 + 1, (rect.y0 + rect.y1) / 2)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if pymupdf.Rect(span["bbox"]).contains(probe):
                    return span
    return None


def forge_iban(pdf_bytes: bytes, words_json: str, new_iban: str) -> bytes:
    """Really redact the printed IBAN and write `new_iban` on the same baseline."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    rect = iban_rect(words_json)
    span = _span_at(page, rect)
    if span is not None:
        size = float(span["size"])
        baseline = float(span["origin"][1])
    else:
        size = rect.height * 0.85
        baseline = rect.y1 - 2
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    page.insert_text((rect.x0, baseline), new_iban, fontsize=size,
                     fontname="helv", color=(0, 0, 0))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _obstacles(page: pymupdf.Page) -> list[pymupdf.Rect]:
    page_area = page.rect.get_area()
    rects = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    for d in page.get_drawings():
        r = d["rect"]
        if r.get_area() < 0.5 * page_area:
            rects.append(pymupdf.Rect(r))
    return rects


def free_gap(page: pymupdf.Page) -> pymupdf.Rect:
    """Largest free vertical band in the lower half of the page, between content."""
    half = page.rect.height / 2
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    left = min((r.x0 for r in words), default=36.0)
    right = max((r.x1 for r in words), default=page.rect.width - 36.0)
    spans = sorted((r.y0, r.y1) for r in _obstacles(page) if r.y1 > half)
    merged: list[list[float]] = []
    for y0, y1 in spans:
        if merged and y0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    best = (0.0, 0.0)
    prev = half
    for y0, y1 in merged:
        if y0 - prev > best[1] - best[0]:
            best = (prev, y0)
        prev = max(prev, y1)
    if best[1] - best[0] <= 2 * PAD:
        raise ValueError("no free gap in lower half of page")
    return pymupdf.Rect(left, best[0] + PAD, right, best[1] - PAD)


def _fits(pdf_bytes: bytes, box: pymupdf.Rect, text: str, size: float) -> bool:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    rc = doc[0].insert_textbox(box, text, fontsize=size, fontname="helv")
    doc.close()
    return rc >= 0


def add_note(pdf_bytes: bytes, text: str, style: str = "body") -> bytes:
    """Write `text` into the largest free gap in the lower half of page 0."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style}")
    size, color = STYLES[style]
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    box = free_gap(page)
    while size >= MIN_FONT and not _fits(pdf_bytes, box, text, size):
        size -= 0.5
    if size < MIN_FONT:
        doc.close()
        raise ValueError("note does not fit in the free gap")
    page.insert_textbox(box, text, fontsize=size, fontname="helv", color=color)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


# ---------------------------------------------------------------------------
# One call
# ---------------------------------------------------------------------------

def _printed_iban(doc_id: str) -> str:
    row = _read_row(doc_id, ["extracted_data"])
    bank = json.loads(row["extracted_data"])["parties"]["issuer"].get("bank") or {}
    return str(bank.get("iban") or "")


def forge_invoice(doc_id: str, note: str | None, style: str = "body",
                  new_iban: str | None = None,
                  out_path: str | Path | None = None) -> dict[str, Any]:
    """Apply the IBAN swap and/or note, save under backend/data/pdfs/."""
    pdf = load_pdf(doc_id)
    printed = _printed_iban(doc_id)
    if new_iban is not None:
        pdf = forge_iban(pdf, load_words(doc_id), new_iban)
        printed = new_iban
    if note is not None:
        pdf = add_note(pdf, note, style)
    path = Path(out_path) if out_path is not None else PDF_DIR / f"{doc_id}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return {"path": str(path), "doc_id": doc_id, "printed_iban": printed, "note": note}

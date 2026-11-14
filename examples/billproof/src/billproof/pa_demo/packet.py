"""Render a case's records as a synthetic one/two-page doctor's packet PDF.

Each record prints VERBATIM (the record text is what NDI grounds a box
around). Clearly labelled synthetic/demo on every page — no real PHI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter as PAGE
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from billproof.bench.pa.cases import PaCase

INK = HexColor("#1B2430")
MUTED = HexColor("#6E7A89")
CARD = HexColor("#FFFCF8")
HAIRLINE = HexColor("#C9BFAE")
BAND = HexColor("#EFE9DD")

_RECORD_KINDS = ("Chart documentation", "Prescriber attestation", "Clinic note",
                 "Prescriber response", "Lab / monitoring summary", "Consultation note",
                 "Pharmacy record", "Prior authorization history", "Imaging report", "Discharge summary")


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "banner": ParagraphStyle("banner", fontName="Helvetica-Bold", fontSize=8, leading=10,
                                 textColor=HexColor("#8A4B08"), alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=8.5, leading=11, textColor=MUTED),
        "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK),
        "tag": ParagraphStyle("tag", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=INK),
        "rec": ParagraphStyle("rec", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK),
        "foot": ParagraphStyle("foot", fontName="Helvetica", fontSize=7, leading=9, textColor=MUTED),
    }


def _kv(rows: list[tuple[str, str]], styles, width) -> Table:
    cells = []
    for k, v in rows:
        cells.append([Paragraph(k.upper(), styles["label"]), Paragraph(_xml(v), styles["body"])])
    t = Table(cells, colWidths=[110, width - 110])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _xml(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _paint(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.72 * inch, 0.32 * inch,
                      "SYNTHETIC PATIENT PACKET - hackathon demo, fictional documents")
    canvas.drawRightString(PAGE[0] - 0.58 * inch, 0.32 * inch, str(doc.page))
    canvas.restoreState()


def render_packet(case: PaCase, spec: dict[str, Any], out_path: Path,
                  *, patient_id: str = "PT-0042") -> Path:
    """Write the packet PDF; return the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=PAGE, leftMargin=0.72 * inch,
                            rightMargin=0.58 * inch, topMargin=0.72 * inch, bottomMargin=0.62 * inch,
                            title=f"Synthetic PA packet {case.row_id}", author="BillProof demo")
    s = _styles()
    w = doc.width
    policy, request = case.policy, case.request
    story: list = [
        Paragraph("SYNTHETIC PATIENT PACKET - DEMONSTRATION DATA, NOT A REAL MEDICAL RECORD", s["banner"]),
        Spacer(1, 8),
        Paragraph("Prior Authorization - Supporting Documentation", s["h1"]),
        Spacer(1, 3),
        Paragraph("Riverbend Neurology Clinic (fictional) - sent with prior-authorization request "
                  f"{policy.get('id', '')}", s["sub"]),
        Spacer(1, 10),
        _kv([
            ("Member", f"{patient_id} (synthetic) - DOB 1982-04-17 - member ARK-SYN-0042"),
            ("Requesting provider", "Dr. A. Whitfield, MD - Riverbend Neurology Clinic (fictional), NPI 1093817465"),
            ("Payer", str(request.get("payer", ""))),
            ("Requested service", str(spec.get("drug") or policy.get("document_title", ""))),
            ("Service date", str(request.get("service_date", ""))),
            ("Criteria applied", f"{policy.get('payer', '')} policy {policy.get('id', '')} "
                                 f"(published {policy.get('capture_vintage', '')}, applies to service dates "
                                 f"{(policy.get('exercise_applicability') or {}).get('from')} through "
                                 f"{(policy.get('exercise_applicability') or {}).get('through')})"),
        ], s, w),
        Spacer(1, 12),
        Paragraph("CLINICAL RECORD ENCLOSED", s["tag"]),
        Spacer(1, 4),
        Paragraph("Each numbered document below is a separate record received with this request.",
                  s["sub"]),
        Spacer(1, 8),
    ]
    for i, rec in enumerate(case.records):
        rid = rec.get("id", f"R{i+1}")
        kind = _RECORD_KINDS[i % len(_RECORD_KINDS)]
        head = Paragraph(f"{rid} - {kind} - received {request.get('service_date', '')}", s["tag"])
        body = Paragraph(_xml(str(rec.get("text", ""))), s["rec"])
        block = Table([[head], [body]], colWidths=[w])
        block.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("BACKGROUND", (0, 1), (-1, 1), CARD),
            ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend([block, Spacer(1, 8)])
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Packet assembled for demonstration. Every line above is fictional or quoted verbatim "
        "from the supplied dataset records; it does not describe a real person.", s["foot"]))
    doc.build(story, onFirstPage=_paint, onLaterPages=_paint)
    return out_path

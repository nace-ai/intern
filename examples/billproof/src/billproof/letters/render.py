"""Patient packet PDF. Page 1 is the summary, page 2 is the letter, page 3 is the evidence.

Amounts and dates are the ones already on the case and the verdict. The letter
text is the clause library with those slots filled. A person signs before it is sent.
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter as PAGE
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from billproof.letters.assembler import fmt_cents
from billproof.schema import Action, Case, Citation, Letter, LetterClause, Verdict

INK = HexColor("#1B2430")
INK_SOFT = HexColor("#3E4C5E")
MUTED = HexColor("#6E7A89")
TEAL = HexColor("#0F6E6C")
TEAL_SOFT = HexColor("#E5F3F2")
PAPER = HexColor("#F6F3EE")
CARD = HexColor("#FFFCF8")
HAIRLINE = HexColor("#E3DCD2")
AMBER = HexColor("#8A4B08")
VIOLET = HexColor("#5B3F8C")
GREEN = HexColor("#1C6B45")

_ACTION_PLAIN = {
    Action.OK: "No change",
    Action.DISPUTE: "Dispute",
    Action.APPEAL: "Appeal",
    Action.ASK_PATIENT: "Ask the patient",
    Action.INSUFFICIENT_EVIDENCE: "Need records",
}
_ACTION_COLOR = {
    Action.OK: GREEN,
    Action.DISPUTE: AMBER,
    Action.APPEAL: VIOLET,
    Action.ASK_PATIENT: INK_SOFT,
    Action.INSUFFICIENT_EVIDENCE: INK_SOFT,
}
_TYPE_TITLE = {
    "dispute": "Billing dispute",
    "appeal": "Appeal",
    "itemized_request": "Request for an itemized bill",
    "none": "No letter",
}


def render_packet_pdf(case: Case, verdict: Verdict, letter: Letter, out_path, crops: dict[str, bytes] | None = None) -> Path:
    """Write the three-part packet. `crops` maps a citation key, or `doc:page`, to PNG bytes."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=PAGE,
        leftMargin=0.72 * inch,
        rightMargin=0.58 * inch,
        topMargin=0.62 * inch,
        bottomMargin=0.58 * inch,
        title=f"BillProof packet {case.case_id}",
        author="BillProof",
    )
    styles = _styles()
    width = doc.width
    story: list = []
    story.extend(_summary(case, verdict, letter, styles, width))
    story.append(PageBreak())
    story.extend(_letter_page(case, letter, styles, width))
    story.append(PageBreak())
    story.extend(_evidence_page(case, letter, styles, width, crops or {}))
    doc.build(story, onFirstPage=_paint, onLaterPages=_paint)
    return path


def _paint(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, PAGE[0], PAGE[1], fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.rect(0, 0, 8, PAGE[1], fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(0.72 * inch, 0.34 * inch, "A human signs every dispute. Not medical or legal advice.")
    canvas.drawRightString(PAGE[0] - 0.58 * inch, 0.34 * inch, str(doc.page))
    canvas.restoreState()


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "kicker": ParagraphStyle("kicker", fontName="Helvetica", fontSize=8, leading=11, textColor=TEAL),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=INK),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9, leading=12, textColor=INK_SOFT),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED),
        "kpi_label": ParagraphStyle("kpi_label", fontName="Helvetica", fontSize=7.5, leading=10, textColor=TEAL),
        "kpi_value": ParagraphStyle("kpi_value", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=INK),
        "kpi_sub": ParagraphStyle("kpi_sub", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=INK_SOFT),
        "td": ParagraphStyle("td", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
        "clause_title": ParagraphStyle("clause_title", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=TEAL),
        "note": ParagraphStyle("note", fontName="Helvetica", fontSize=7, leading=9, textColor=HexColor("#8B93A0")),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=9, leading=12, textColor=INK_SOFT),
        "right": ParagraphStyle("right", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK, alignment=TA_RIGHT),
        "sign": ParagraphStyle("sign", fontName="Helvetica", fontSize=10, leading=14, textColor=INK),
    }


def _summary(case: Case, verdict: Verdict, letter: Letter, styles, width) -> list:
    bits = []
    if case.eob is not None and (case.eob.payer or "").strip():
        bits.append(f"Payer {case.eob.payer.strip()}")
    if case.eob is not None and (case.eob.claim_id or "").strip():
        bits.append(f"Claim {case.eob.claim_id.strip()}")
    bits.append(f"Account {case.bill.account_no}")
    deadline = (letter.deadline or verdict.deadline or "").strip()
    if deadline.lower() == "not on file":
        deadline = ""
    callout = "A human signs every dispute before it is sent."
    if deadline:
        callout = f"Deadline on file: {deadline}. {callout}"
    story = [
        Paragraph("BILLPROOF  ·  PATIENT PACKET", styles["kicker"]),
        Spacer(1, 6),
        Paragraph(case.title, styles["h1"]),
        Spacer(1, 3),
        Paragraph(
            f"{case.case_id}  ·  Patient {case.patient.patient_id}  ·  {case.provider.name}",
            styles["sub"],
        ),
        Paragraph("  ·  ".join(bits), styles["sub"]),
        Spacer(1, 12),
        _kpi_row(verdict, styles, width),
        Spacer(1, 14),
        Paragraph("Actions on each line", styles["h2"]),
        Spacer(1, 4),
        _line_table(case, verdict, styles, width),
        Spacer(1, 12),
        _callout(callout, styles, width),
        Spacer(1, 8),
        Paragraph(
            "Drex selected the clauses. Code filled the amounts, dates, and rule cites. "
            f"Words written by a model: {letter.generated_words}. "
            "This packet is not a full claims adjudication, and it is not medical or legal advice.",
            styles["small"],
        ),
    ]
    return story


def _kpi_row(verdict: Verdict, styles, width) -> Table:
    cells = [
        _kpi("AMOUNT BILLED", fmt_cents(verdict.billed_balance_cents), "What the bill asks for", styles, width),
        _kpi("ACTUALLY OWED", fmt_cents(verdict.owed_cents), "What this review calculates", styles, width),
        _kpi("IN DISPUTE", fmt_cents(verdict.disputed_cents), "What this packet asks to correct", styles, width),
    ]
    col = width / 3.0
    table = Table([cells], colWidths=[col, col, col])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("BACKGROUND", (0, 0), (-1, -1), PAPER),
    ]))
    return table


def _kpi(label: str, value: str, sub: str, styles, width) -> Table:
    inner_w = width / 3.0 - 10
    inner = Table(
        [[Paragraph(label, styles["kpi_label"])],
         [Paragraph(value, styles["kpi_value"])],
         [Paragraph(sub, styles["kpi_sub"])]],
        colWidths=[inner_w],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (0, 0), 7),
        ("BOTTOMPADDING", (0, -1), (0, -1), 7),
        ("TOPPADDING", (0, 1), (0, 1), 1),
        ("BOTTOMPADDING", (0, 1), (0, 1), 1),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return inner


def _line_table(case: Case, verdict: Verdict, styles, width) -> Table:
    header = [Paragraph(text, styles["th"]) for text in ("Line", "Code", "Service", "Action", "Disputed")]
    rows = [header]
    by_no = {line.line_no: line for line in verdict.lines}
    line_nos = [line.line_no for line in verdict.lines] or [line.line_no for line in case.bill.lines]
    for line_no in line_nos:
        verdict_line = by_no.get(line_no)
        bill_line = next((item for item in case.bill.lines if item.line_no == line_no), None)
        action_value = verdict_line.action if verdict_line is not None else None
        label = _clip(bill_line.label, 42) if bill_line else "-"
        code = bill_line.code if bill_line else "-"
        disputed = fmt_cents(verdict_line.disputed_cents) if verdict_line is not None else "-"
        action_text = _ACTION_PLAIN.get(action_value, "-")
        color = _ACTION_COLOR.get(action_value, INK)
        action_style = ParagraphStyle("act", parent=styles["td"], textColor=color, fontName="Helvetica-Bold")
        rows.append([
            Paragraph(str(line_no), styles["td"]),
            Paragraph(code, styles["td"]),
            Paragraph(_xml(label), styles["td"]),
            Paragraph(_xml(action_text), action_style),
            Paragraph(disputed, styles["td"]),
        ])
    if len(rows) == 1:
        rows.append([Paragraph("-", styles["td"]) for _ in range(5)])
    col = [36, 58, width - 36 - 58 - 78 - 72, 78, 72]
    table = Table(rows, colWidths=col, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL_SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_SOFT),
        ("BACKGROUND", (0, 1), (-1, -1), CARD),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, HAIRLINE),
        ("BOX", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _callout(text: str, styles, width) -> Table:
    inner = Table([[Paragraph(_xml(text), styles["body"])]], colWidths=[width - 8])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL_SOFT),
        ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return inner


def _letter_page(case: Case, letter: Letter, styles, width) -> list:
    who, where = _addressee(case, letter)
    story = [
        Paragraph("BILLPROOF  ·  LETTER", styles["kicker"]),
        Spacer(1, 6),
        Paragraph(_TYPE_TITLE.get(letter.letter_type, "Letter"), styles["h1"]),
        Spacer(1, 8),
        Paragraph(_xml(who), styles["body"]),
        Paragraph(_xml(where), styles["meta"]),
        Spacer(1, 6),
        Paragraph(f"Subject: {_xml(letter.subject)}", styles["body"]),
        Paragraph(f"Statement date on file: {_xml(case.bill.statement_date)}", styles["meta"]),
        Spacer(1, 8),
    ]
    if not letter.clauses:
        story.append(Paragraph(
            "No letter was selected. The review did not choose a dispute, an appeal, or a records request.",
            styles["body"],
        ))
        return story
    note_w = 112
    body_w = width - note_w - 8
    for clause in letter.clauses:
        story.append(Spacer(1, 6))
        story.append(_clause_block(clause, styles, body_w, note_w))
    return story


def _addressee(case: Case, letter: Letter) -> tuple[str, str]:
    if letter.recipient == "insurer":
        name = case.eob.payer.strip() if case.eob and case.eob.payer and case.eob.payer.strip() else "Insurer"
        return name, "Appeals"
    line = case.provider.address or "Billing office"
    return case.provider.name, line


def _clause_block(clause: LetterClause, styles, body_w: float, note_w: float):
    note = _margin_note(clause)
    paragraphs = []
    paragraphs.append(Paragraph(_xml(clause.title), styles["clause_title"]))
    for block in _blocks(clause.text):
        paragraphs.append(Spacer(1, 2))
        paragraphs.append(Paragraph(block, styles["body"]))
    body = paragraphs
    row = Table(
        [[body, Paragraph(_xml(note), styles["note"])]],
        colWidths=[body_w, note_w],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 8),
        ("RIGHTPADDING", (0, 0), (0, 0), 8),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBEFORE", (0, 0), (0, 0), 2, TEAL),
        ("BACKGROUND", (0, 0), (0, 0), CARD),
        ("BACKGROUND", (1, 0), (1, 0), PAPER),
    ]))
    return KeepTogether([row])


def _margin_note(clause: LetterClause) -> str:
    if clause.probability is None:
        return "selected from the verdict"
    return f"selected by Drex · P={clause.probability:.2f}"


def _evidence_page(case: Case, letter: Letter, styles, width, crops: dict[str, bytes]) -> list:
    story = [
        Paragraph("BILLPROOF  ·  EVIDENCE", styles["kicker"]),
        Spacer(1, 6),
        Paragraph("Citations included in this packet", styles["h1"]),
        Spacer(1, 4),
        Paragraph(
            "Each item is a passage already printed on the bill, the explanation of benefits, "
            "the denial, or the plan. A yes from Drex keeps it. A missing answer keeps it. A no leaves it out.",
            styles["sub"],
        ),
        Spacer(1, 10),
    ]
    shown: list[tuple[str | None, Citation]] = []
    seen: set[tuple] = set()
    for clause in letter.clauses:
        for citation in clause.evidence:
            key = _key_of(case, citation)
            ident = (citation.doc, citation.page, citation.text)
            if ident in seen:
                continue
            seen.add(ident)
            shown.append((key, citation))
    if not shown:
        story.append(Paragraph("No evidence citation was included.", styles["body"]))
        return story
    for index, (key, citation) in enumerate(shown, start=1):
        story.append(_evidence_item(index, key, citation, styles, width, crops))
        story.append(Spacer(1, 8))
    return story


def _evidence_item(index: int, key: str | None, citation: Citation, styles, width, crops: dict[str, bytes]):
    quote = citation.text or "The region has no extracted text."
    header = Paragraph(
        f"{index}.  {citation.doc}  ·  page {citation.page}" + (f"  ·  {key}" if key else ""),
        styles["clause_title"],
    )
    body = Paragraph(f'"{_xml(quote)}"', styles["body"])
    parts: list = [header, Spacer(1, 2), body]
    png = _crop_bytes(crops, key, citation)
    if png:
        image = _png_flowable(png, width * 0.72, 2.3 * inch)
        if image is not None:
            parts.extend([Spacer(1, 4), image])
        else:
            parts.append(Paragraph("The crop image could not be read.", styles["small"]))
    block = Table([[parts]], colWidths=[width])
    block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return KeepTogether([block])


def _crop_bytes(crops: dict[str, bytes], key: str | None, citation: Citation) -> bytes | None:
    if key and key in crops:
        return crops[key]
    alt = f"{citation.doc}:{citation.page}"
    if alt in crops:
        return crops[alt]
    if citation.text and citation.text in crops:
        return crops[citation.text]
    return None


def _png_flowable(png: bytes, max_w: float, max_h: float):
    try:
        from reportlab.lib.utils import ImageReader

        reader = ImageReader(io.BytesIO(png))
        width, height = reader.getSize()
        if width <= 0 or height <= 0:
            return None
        scale = min(max_w / float(width), max_h / float(height), 1.0)
        return Image(io.BytesIO(png), width=width * scale, height=height * scale)
    except Exception:
        return None


def _key_of(case: Case, citation: Citation) -> str | None:
    for key, other in case.citations.items():
        if other.doc == citation.doc and other.page == citation.page and other.text == citation.text:
            return key
    return None


def _blocks(text: str) -> list[str]:
    blocks = [part.strip() for part in text.split("\n\n") if part.strip()]
    return [_xml(part).replace("\n", "<br/>") for part in blocks] or [""]


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _self_check() -> None:
    """Hand-built case. Run: cd billproof && PYTHONPATH=src .venv/bin/python -m billproof.letters.render"""
    import re

    from PIL import Image as PILImage
    from PIL import ImageDraw
    from pypdf import PdfReader

    from billproof.letters.assembler import _fill, _lines_phrase, assemble_letter, letter_questions, load_clauses
    from billproof.schema import (
        Answer, Bill, BillLine, Denial, DenialCategory, Eob, EobLine, LineVerdict, Patient, PlanSummary, Provider,
    )

    library = load_clauses()
    assert len(library) >= 16, len(library)
    fillable = {
        "line_no", "code", "label", "dos", "amount", "rule_cite", "deadline", "account_no",
        "claim_id", "payer", "provider", "date_of_death", "member_id", "patient_id", "billed",
        "owed", "disputed", "carc", "units", "lines",
    }
    for clause in library.values():
        bare = set(re.findall(r"\{([A-Za-z0-9_]+)\}", clause.body))
        gates = set(re.findall(r"\{([A-Za-z0-9_]+)\?", clause.body))
        used = bare | gates
        assert used <= set(clause.slots), (clause.id, used - set(clause.slots))
        assert used <= fillable, (clause.id, used - fillable)
        assert "not on file" not in clause.body.lower(), clause.id
        assert "within 30 days" not in clause.body.lower(), clause.id
    assert "Prepared with BillProof; reviewed and signed by: ____" in library["closing"].body
    assert _fill("Please correct.{deadline? Please reply by {deadline}.}", {"deadline": ""}) == "Please correct."
    assert _fill("Please correct.{deadline? Please reply by {deadline}.}", {"deadline": "2026-12-01"}) == (
        "Please correct. Please reply by 2026-12-01."
    )
    nested = "Send{claim_id? the EOB for claim {claim_id}{payer? ({payer})}.} Done."
    assert _fill(nested, {"claim_id": "CLM-1", "payer": "Example"}) == "Send the EOB for claim CLM-1 (Example). Done."
    assert _fill(nested, {"claim_id": "CLM-1", "payer": ""}) == "Send the EOB for claim CLM-1. Done."
    assert _fill(nested, {"claim_id": "", "payer": "Example"}) == "Send Done."
    assert "not on file" not in _fill("Reply by {deadline}.", {"deadline": "not on file"})
    assert _lines_phrase([5, 2, 4]) == "lines 2, 4 and 5"
    assert _lines_phrase([4, 2]) == "lines 2 and 4"
    assert _lines_phrase([2]) == "line 2"
    assert _lines_phrase([0, -1]) == ""

    case = Case(
        case_id="SYN-LETTER",
        split="demo",
        title="Office visit with a duplicate imaging charge",
        patient=Patient(patient_id="PT-0042", display_name="Patient", member_id="MBR-1001", dob="1980-04-02"),
        provider=Provider(name="Northwind Imaging", npi="1234567890", address="100 Example Ave"),
        bill=Bill(
            account_no="ACCT-7781",
            statement_date="2026-10-01",
            lines=[
                BillLine(line_no=1, dos="2026-09-12", code="70553", label="MRI of the brain, with and without contrast",
                         units=1, charge_cents=180000),
                BillLine(line_no=2, dos="2026-09-12", code="70553", label="MRI of the brain, with and without contrast",
                         units=1, charge_cents=180000),
            ],
            total_charges_cents=360000,
            patient_balance_cents=360000,
        ),
        eob=Eob(
            payer="Example Health Plan",
            claim_id="CLM-55019",
            processed_date="2026-09-20",
            lines=[
                EobLine(line_no=1, code="70553", billed_cents=180000, allowed_cents=90000,
                        plan_paid_cents=72000, patient_resp_cents=18000, carc=["CO-45"]),
                EobLine(line_no=2, code="70553", billed_cents=180000, allowed_cents=0,
                        plan_paid_cents=0, patient_resp_cents=0, carc=["OA-18"], remark="Exact duplicate"),
            ],
            patient_resp_total_cents=18000,
        ),
        plan=PlanSummary(plan_name="Example PPO", deductible_cents=150000, deductible_met_cents=150000,
                         coinsurance_pct=20, oop_max_cents=500000),
        service_setting="imaging",
        citations={
            "bill.lines.2.code": Citation(doc="bill", page=1, text="70553"),
            "bill.lines.2.charge_cents": Citation(doc="bill", page=1, text="$1,800.00"),
            "eob.lines.2.carc": Citation(doc="eob", page=1, text="OA-18"),
            "eob.patient_resp_total_cents": Citation(doc="eob", page=1, text="$180.00"),
        },
    )
    verdict = Verdict(
        case_action=Action.DISPUTE,
        lines=[
            LineVerdict(line_no=1, action=Action.OK),
            LineVerdict(line_no=2, action=Action.DISPUTE, reasons=["DUPLICATE"], disputed_cents=180000,
                        rule_cites=["CARC 18 exact duplicate claim/service"]),
        ],
        billed_balance_cents=360000,
        owed_cents=18000,
        disputed_cents=180000,
        needs_human=True,
        human_reasons=["A person signs the dispute before it is sent."],
        deadline="2026-12-01",
    )
    def ans(name: str, choice: str, confidence: float, probs: dict[str, float] | None = None) -> Answer:
        return Answer(name=name, choice=choice, confidence=confidence, probabilities=probs or {choice: confidence},
                      confidence_kind="native")

    questions = letter_questions(case, verdict)
    names = [item.name for item in questions]
    assert names[0] == "letter_type" and "letter_recipient" in names
    assert "ground_L2" in names and "ground_L1" not in names
    ground = next(item for item in questions if item.name == "ground_L2")
    assert ground.options() == ["duplicate_charge"]
    assert "evidence_eob.lines.2.carc" in names
    assert any(item.type == "noul" and item.name.startswith("evidence_") for item in questions)

    fallback = assemble_letter(case, verdict, {})
    assert fallback.letter_type == "dispute"
    assert fallback.recipient == "provider_billing"
    assert fallback.generated_words == 0
    assert [item.clause_id for item in fallback.clauses] == [
        "opening_dispute", "duplicate_charge", "hold_collections", "closing",
    ]
    dup = fallback.clauses[1]
    assert dup.picked_by == "verdict_L2" and dup.probability is None
    assert "$1,800.00" in dup.text and "CARC 18 exact duplicate claim/service" in dup.text
    fallback_text = "\n".join(item.text for item in fallback.clauses)
    assert "{" not in fallback_text
    assert "not on file" not in fallback_text.lower()
    assert "Please reply by 2026-12-01." in fallback.clauses[0].text
    assert "Please confirm the hold in writing before 2026-12-01." in fallback.clauses[2].text
    assert "Please reply in writing by 2026-12-01" in fallback.clauses[-1].text
    assert "Prepared with BillProof; reviewed and signed by: ____" in fallback.clauses[-1].text
    undated_verdict = verdict.model_copy(update={"deadline": None})
    undated = assemble_letter(case, undated_verdict, {})
    undated_text = "\n".join(item.text for item in undated.clauses)
    assert "{" not in undated_text
    assert "not on file" not in undated_text.lower()
    assert "reply by" not in undated_text.lower()
    assert "deadline on file" not in undated_text.lower()
    assert "in writing before" not in undated_text.lower()
    assert "within 30 days" not in undated_text.lower()
    assert "Member id on file: MBR-1001." in undated.clauses[-1].text
    assert "Prepared with BillProof; reviewed and signed by: ____" in undated.clauses[-1].text

    answered = assemble_letter(case, verdict, {
        "letter_type": ans("letter_type", "dispute", 0.97, {"dispute": 0.97, "appeal": 0.01, "itemized_request": 0.01, "none": 0.01}),
        "letter_recipient": ans("letter_recipient", "provider_billing", 0.91),
        "ground_L2": ans("ground_L2", "duplicate_charge", 0.97),
        "evidence_bill.lines.2.code": ans("evidence_bill.lines.2.code", "yes", 0.88),
        "evidence_bill.lines.2.charge_cents": ans("evidence_bill.lines.2.charge_cents", "no", 0.80),
        "evidence_eob.lines.2.carc": ans("evidence_eob.lines.2.carc", "yes", 0.93),
        "evidence_eob.patient_resp_total_cents": ans("evidence_eob.patient_resp_total_cents", "yes", 0.66),
    })
    picked = next(item for item in answered.clauses if item.clause_id == "duplicate_charge")
    assert picked.picked_by == "ground_L2" and picked.probability == 0.97
    evidence_text = [item.text for clause in answered.clauses for item in clause.evidence]
    assert "OA-18" in evidence_text and "70553" in evidence_text and "$1,800.00" not in evidence_text
    assert answered.amount_cents == 180000 and answered.generated_words == 0

    rejected = assemble_letter(case, verdict, {"ground_L2": ans("ground_L2", "medical_necessity", 0.4)})
    assert any(item.clause_id == "duplicate_charge" for item in rejected.clauses)
    assert all(item.clause_id != "medical_necessity" for item in rejected.clauses)

    appeal_case = case.model_copy(deep=True)
    appeal_case.denial = Denial(
        letter_date="2026-09-28", carc="CO-50", category=DenialCategory.MEDICAL_NECESSITY,
        reason_text="The plan states the service is not medically necessary.",
        service_code="70553", appeal_deadline="2026-12-15",
    )
    appeal_verdict = Verdict(
        case_action=Action.APPEAL,
        lines=[
            LineVerdict(line_no=1, action=Action.OK),
            LineVerdict(line_no=2, action=Action.APPEAL, reasons=["MEDICAL_NECESSITY"], disputed_cents=180000,
                        rule_cites=["CARC 50 medical necessity"]),
        ],
        billed_balance_cents=360000, owed_cents=18000, disputed_cents=180000,
        needs_human=True, deadline="2026-12-15",
    )
    appeal = assemble_letter(appeal_case, appeal_verdict, {})
    assert appeal.letter_type == "appeal" and appeal.recipient == "insurer"
    assert appeal.clauses[0].clause_id == "opening_appeal"
    assert any(item.clause_id == "medical_necessity" for item in appeal.clauses)
    assert all(item.clause_id != "hold_collections" for item in appeal.clauses)
    assert "45 CFR 147.136" in appeal.clauses[0].text
    assert "of claim CLM-55019" in appeal.clauses[0].text
    assert "The deadline on file is 2026-12-15." in appeal.clauses[0].text
    assert appeal.subject == "Appeal of claim CLM-55019"
    assert "does not offer a medical opinion" in next(
        item.text for item in appeal.clauses if item.clause_id == "medical_necessity"
    )
    no_claim = appeal_case.model_copy(deep=True)
    no_claim.eob = None
    no_claim_letter = assemble_letter(no_claim, appeal_verdict.model_copy(update={"deadline": None}), {})
    assert no_claim_letter.subject == "Appeal"
    assert no_claim_letter.clauses[0].text.startswith("Please treat this letter as an internal appeal.")
    assert "of claim" not in no_claim_letter.clauses[0].text
    assert "not on file" not in "\n".join(item.text for item in no_claim_letter.clauses).lower()

    clean = assemble_letter(case, Verdict(
        case_action=Action.OK,
        lines=[LineVerdict(line_no=1, action=Action.OK), LineVerdict(line_no=2, action=Action.OK)],
        billed_balance_cents=18000, owed_cents=18000, disputed_cents=0, needs_human=False,
    ), {})
    assert clean.letter_type == "none" and clean.clauses == [] and clean.generated_words == 0

    ask_case = case.model_copy(deep=True)
    ask_case.bill.lines.append(BillLine(
        line_no=4, dos="2026-09-12", code="99213", label="Office visit", units=1, charge_cents=15000,
    ))
    ask_case.bill.lines.append(BillLine(
        line_no=5, dos="2026-09-12", code="99214", label="Office visit, longer", units=1, charge_cents=22000,
    ))
    asked = assemble_letter(ask_case, Verdict(
        case_action=Action.ASK_PATIENT,
        lines=[
            LineVerdict(line_no=1, action=Action.OK),
            LineVerdict(line_no=2, action=Action.ASK_PATIENT),
            LineVerdict(line_no=4, action=Action.ASK_PATIENT),
            LineVerdict(line_no=5, action=Action.ASK_PATIENT),
        ],
        billed_balance_cents=360000, owed_cents=18000, disputed_cents=0, needs_human=True,
    ), {})
    assert [item.clause_id for item in asked.clauses] == [
        "opening_itemized", "records_request", "hold_collections", "closing",
    ]
    assert asked.clauses[1].text.count("This request covers lines 2, 4 and 5.") == 1
    assert "not on file" not in asked.clauses[1].text.lower()
    one_line = assemble_letter(case, Verdict(
        case_action=Action.ASK_PATIENT,
        lines=[
            LineVerdict(line_no=1, action=Action.OK),
            LineVerdict(line_no=2, action=Action.ASK_PATIENT),
        ],
        billed_balance_cents=360000, owed_cents=18000, disputed_cents=0, needs_human=True,
    ), {})
    assert [item.clause_id for item in one_line.clauses].count("records_request") == 1
    assert "This request covers line 2." in one_line.clauses[1].text

    wrong_recipient = ans("letter_recipient", "insurer", 0.8)
    forced = assemble_letter(case, verdict, {
        "letter_type": ans("letter_type", "dispute", 0.9),
        "letter_recipient": wrong_recipient,
    })
    assert forced.recipient == "provider_billing"
    assert wrong_recipient.raw["recipient_override"] == {
        "choice": "insurer", "used": "provider_billing", "letter_type": "dispute",
    }
    appeal_to_provider = ans("letter_recipient", "provider_billing", 0.7)
    forced_appeal = assemble_letter(appeal_case, appeal_verdict, {"letter_recipient": appeal_to_provider})
    assert forced_appeal.recipient == "insurer"
    assert appeal_to_provider.raw["recipient_override"]["used"] == "insurer"

    case_level = Verdict(
        case_action=Action.DISPUTE,
        lines=[
            LineVerdict(line_no=1, action=Action.APPEAL, reasons=["denial_appeal_strength_moderate"]),
            LineVerdict(line_no=0, action=Action.DISPUTE, reasons=["EOB_BILL_MISMATCH", "NO_SURPRISES_ACT"],
                        rule_cites=["Bill/EOB patient-responsibility reconciliation"]),
        ],
        billed_balance_cents=17887, owed_cents=14387, disputed_cents=3500,
        needs_human=True, human_reasons=["EOB_BILL_MISMATCH", "NO_SURPRISES_ACT"], deadline="2026-12-01",
    )
    case_questions = letter_questions(case, case_level)
    ground0 = next(item for item in case_questions if item.name == "ground_L0")
    assert ground0.options() == ["eob_mismatch", "nsa_balance_bill"]
    level_letter = assemble_letter(case, case_level, {
        "letter_recipient": ans("letter_recipient", "insurer", 0.66),
        "ground_L0": ans("ground_L0", "eob_mismatch", 0.91),
    })
    assert level_letter.letter_type == "dispute" and level_letter.recipient == "provider_billing"
    assert level_letter.amount_cents == 3500
    eob = next(item for item in level_letter.clauses if item.clause_id == "eob_mismatch")
    nsa = next(item for item in level_letter.clauses if item.clause_id == "nsa_balance_bill")
    assert eob.picked_by == "ground_L0" and eob.probability == 0.91
    assert nsa.picked_by == "verdict"
    assert "$35.00" in eob.text and "$35.00" in nsa.text
    assert "Line 0" not in eob.text and "Line not on file" not in eob.text
    assert "This request covers" not in eob.text and "This request covers" not in nsa.text
    assert [item.clause_id for item in level_letter.clauses].count("eob_mismatch") == 1
    assert [item.clause_id for item in level_letter.clauses].count("nsa_balance_bill") == 1
    assert "$1,800.00" not in eob.text

    image = PILImage.new("RGB", (360, 72), "#FFF8E8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 357, 69), outline="#0F6E6C", width=2)
    draw.text((12, 28), "EOB crop  OA-18", fill="#1B2430")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    out = Path("/tmp/billproof-g4-packet.pdf")
    written = render_packet_pdf(case, verdict, answered, out, crops={"eob.lines.2.carc": buffer.getvalue()})
    none_path = render_packet_pdf(case, verdict, clean, "/tmp/billproof-g4-none.pdf")
    reader = PdfReader(str(written))
    assert len(reader.pages) >= 3
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Actually owed" in text or "ACTUALLY OWED" in text
    assert "A human signs every dispute" in text
    assert "Deadline on file: 2026-12-01" in text
    assert "not on file" not in text.lower()
    assert "selected by Drex" in text and "P=0.97" in text
    assert "$1,800.00" in text and "$180.00" in text
    assert "Prepared with BillProof" in text
    undated_pdf = render_packet_pdf(case, undated_verdict, undated, "/tmp/billproof-g4-undated.pdf")
    undated_pages = "\n".join(page.extract_text() or "" for page in PdfReader(str(undated_pdf)).pages)
    assert "not on file" not in undated_pages.lower()
    assert "reply by" not in undated_pages.lower()
    assert "Deadline on file" not in undated_pages
    assert "A human signs every dispute" in undated_pages
    found_image = False
    for page in reader.pages[2:]:
        resources = page.get("/Resources")
        if resources and resources.get("/XObject"):
            found_image = True
    assert found_image, "evidence crop was not embedded"
    assert len(PdfReader(str(none_path)).pages) >= 3
    print(f"g4 letters self-check ok  {written}  clauses={len(library)}")


if __name__ == "__main__":
    from billproof.letters.assembler import assemble_letter, letter_questions

    _self_check()

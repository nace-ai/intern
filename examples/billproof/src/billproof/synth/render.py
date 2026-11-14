"""Render a synthetic Case to realistic PDF paperwork + NDI-shaped fixtures.

Every printed value that maps to a Case field is drawn through a tracker that
records its true reportlab bounding box, so Case.citations and
data/synthetic/ndi_fixtures/ carry geometrically real polygons (normalized
[0,1], top-left origin) rather than OCR guesses.

Docs per case: bill (itemized statement), eob ("THIS IS NOT A BILL"), denial
letter (only when case.denial), plan (1-page summary). Each <= 3 pages.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen import canvas as _canvas

from billproof.rules.money import fmt as money
from billproof.schema import Case, Citation, DocKind, DocRef

PAGE_W, PAGE_H = letter  # 612 x 792
MAX_PAGES = 3
MARGIN = 46
RIGHT = PAGE_W - MARGIN

INK = HexColor("#202733")
GREY = HexColor("#5d6673")
FAINT = HexColor("#98a0ac")
RULE = HexColor("#c8cdd5")
WHITE = HexColor("#ffffff")

DOC_KINDS: tuple[DocKind, ...] = ("bill", "eob", "denial", "plan")


# --------------------------------------------------------------------- theme


@dataclass(frozen=True)
class _Skin:
    accent: Color
    pale: Color
    head: str  # org-name / title font
    body: str  # body font
    num: str  # amounts font
    logo: str  # mark glyph: cross | ring | peak | bars | shield
    band: str  # page band: top | side | none


SKINS: tuple[_Skin, ...] = (
    _Skin(HexColor("#1e3a5f"), HexColor("#e9edf4"), "Helvetica-Bold", "Helvetica", "Helvetica", "cross", "top"),
    _Skin(HexColor("#175d57"), HexColor("#e7f0ee"), "Times-Bold", "Times-Roman", "Times-Roman", "ring", "side"),
    _Skin(HexColor("#6d3b47"), HexColor("#f4eaec"), "Helvetica-Bold", "Helvetica", "Courier", "peak", "none"),
    _Skin(HexColor("#4a5230"), HexColor("#eff0e5"), "Helvetica-Bold", "Helvetica", "Helvetica", "bars", "top"),
    _Skin(HexColor("#3f3d68"), HexColor("#ebeaf2"), "Courier-Bold", "Courier", "Courier", "shield", "side"),
)


def _skin(seed: str) -> _Skin:
    return SKINS[zlib.crc32(seed.encode("utf-8")) % len(SKINS)]


# --------------------------------------------------------------------- geom


def _n(x: float) -> float:
    return round(x, 5)


def _poly(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """reportlab rect (bottom-left origin) -> normalized top-left polygon."""
    t, b = (PAGE_H - y1) / PAGE_H, (PAGE_H - y0) / PAGE_H
    return [
        (_n(x0 / PAGE_W), _n(t)),
        (_n(x1 / PAGE_W), _n(t)),
        (_n(x1 / PAGE_W), _n(b)),
        (_n(x0 / PAGE_W), _n(b)),
    ]


def _denorm(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """normalized polygon -> reportlab rect (l, b, r, t)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs) * PAGE_W, (1 - max(ys)) * PAGE_H, max(xs) * PAGE_W, (1 - min(ys)) * PAGE_H)


# --------------------------------------------------------------------- _Doc


class _Doc:
    """One PDF under construction: tracked drawing + ordered field table."""

    def __init__(self, out: Path, doc: DocKind, skin: _Skin, title: str):
        self.out, self.doc, self.skin = out, doc, skin
        self.c = _canvas.Canvas(str(out), pagesize=letter)
        self.c.setTitle(title)
        self.c.setAuthor("BillProof synthetic corpus")
        self.c.setSubject("Synthetic demonstration document; no real persons or claims")
        self.c.setCreator("billproof synth renderer")
        self.page = 0
        self.fields: list[dict] = []  # {"path","value","cit"} in declaration order
        self._fi: dict[str, dict] = {}
        self.checks: list[tuple[int, list[tuple[float, float]], str]] = []

    # -- page lifecycle
    def begin_page(self) -> None:
        if self.page:
            self.c.showPage()
        self.page += 1

    def finish(self) -> int:
        self.c.save()
        return self.page

    # -- fields / citations
    def expect(self, path: str, value: Any) -> dict:
        f = self._fi.get(path)
        if f is None:
            f = {"path": path, "value": value, "cit": None}
            self._fi[path] = f
            self.fields.append(f)
        return f

    def _attach(self, path: str, value: Any) -> Citation:
        f = self.expect(path, value)
        if f["cit"] is None:
            f["cit"] = Citation(doc=self.doc, page=self.page, polygons=[], text="")
        return f["cit"]

    def _record(self, cit: Citation, x: float, y: float, w: float, font: str, size: float, check_text: str, pad: float = 0.9) -> None:
        asc, desc = getAscentDescent(font, size)
        poly = _poly(x - pad, y + desc - pad, x + w + pad, y + asc + pad)
        cit.polygons.append(poly)
        self.checks.append((self.page, poly, check_text))

    # -- primitives
    def text(
        self,
        x: float,
        y: float,
        s: Any,
        *,
        font: str | None = None,
        size: float = 9,
        color: Color = INK,
        align: str = "l",
        cs: float = 0.0,
        path: str | None = None,
        value: Any = None,
    ) -> float:
        """Draw s with baseline at (x, y); returns drawn width (incl. charSpace)."""
        font = font or self.skin.body
        s = str(s)
        w = stringWidth(s, font, size) + cs * max(0, len(s) - 1)
        if align == "r":
            x -= w
        elif align == "c":
            x -= w / 2
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        self.c.drawString(x, y, s, charSpace=cs)
        if path is not None:
            cit = self._attach(path, s if value is None else value)
            cit.text = s
            self._record(cit, x, y, w, font, size, s)
        return w

    def para(
        self,
        x: float,
        y: float,
        w: float,
        s: Any,
        *,
        font: str | None = None,
        size: float = 9,
        leading: float | None = None,
        color: Color = INK,
        path: str | None = None,
        value: Any = None,
        max_lines: int = 8,
    ) -> float:
        """Draw wrapped text top-down from baseline y; returns next baseline."""
        font = font or self.skin.body
        leading = leading or size * 1.32
        s = str(s)
        lines = _wrap(s, font, size, w)
        cit = self._attach(path, s if value is None else value) if path else None
        if cit is not None:
            cit.text = s
        yy = y
        for ln in lines[:max_lines]:
            lw = self.text(x, yy, ln, font=font, size=size, color=color)
            if cit is not None:
                self._record(cit, x, yy, lw, font, size, ln)
            yy -= leading
        return yy

    def hline(self, x0: float, y: float, x1: float, color: Color = RULE, w: float = 0.7, dash: tuple | None = None) -> None:
        self.c.saveState()
        self.c.setStrokeColor(color)
        self.c.setLineWidth(w)
        if dash:
            self.c.setDash(*dash)
        self.c.line(x0, y, x1, y)
        self.c.restoreState()

    def box(self, x: float, y: float, w: float, h: float, *, fill: Color | None = None, stroke: Color | None = None, lw: float = 0.7, r: float = 0) -> None:
        self.c.saveState()
        if fill is not None:
            self.c.setFillColor(fill)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(lw)
        if r:
            self.c.roundRect(x, y, w, h, r, fill=int(fill is not None), stroke=int(stroke is not None))
        else:
            self.c.rect(x, y, w, h, fill=int(fill is not None), stroke=int(stroke is not None))
        self.c.restoreState()

    def footer(self, line: str) -> None:
        self.hline(MARGIN, 50, RIGHT)
        self.text(MARGIN, 38, line, size=6.5, color=FAINT)
        self.text(RIGHT, 38, f"Page {self.page}", size=6.5, color=FAINT, align="r")

    # -- fixture
    def fixture_fields(self) -> list[dict]:
        out = []
        for f in self.fields:
            cit = f["cit"]
            if cit is not None:
                cits = [
                    {
                        "location": {"kind": "page_region", "page": cit.page, "polygons": cit.polygons},
                        "source_text": cit.text,
                    }
                ]
                out.append({"path": f["path"], "value": _jsonable(f["value"]), "status": "found", "citations": cits})
            else:
                out.append({"path": f["path"], "value": None, "status": "not_found", "citations": []})
        return out


def _jsonable(v: Any) -> Any:
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def _wrap(s: str, font: str, size: float, w: float) -> list[str]:
    lines, cur = [], ""
    for wd in str(s).split():
        t = f"{cur} {wd}".strip()
        if stringWidth(t, font, size) <= w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines or [""]


# ------------------------------------------------------------------ artwork


def _band(d: _Doc) -> None:
    c, s = d.c, d.skin
    c.saveState()
    c.setFillColor(s.accent)
    if s.band == "top":
        c.rect(0, PAGE_H - 12, PAGE_W, 12, fill=1, stroke=0)
    elif s.band == "side":
        c.rect(0, 0, 12, PAGE_H, fill=1, stroke=0)
    c.restoreState()


def _logo(d: _Doc, x: float, y: float, sz: float) -> None:
    c, s = d.c, d.skin
    c.saveState()
    c.setFillColor(s.accent)
    c.roundRect(x, y, sz, sz, sz * 0.22, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setStrokeColor(WHITE)
    cx, cy, r = x + sz / 2, y + sz / 2, sz * 0.27
    if s.logo == "cross":
        c.rect(cx - r * 0.4, cy - r, r * 0.8, 2 * r, fill=1, stroke=0)
        c.rect(cx - r, cy - r * 0.4, 2 * r, r * 0.8, fill=1, stroke=0)
    elif s.logo == "ring":
        c.setLineWidth(sz * 0.13)
        c.circle(cx, cy, r, fill=0, stroke=1)
        c.circle(cx, cy, r * 0.42, fill=1, stroke=0)
    elif s.logo == "peak":
        p = c.beginPath()
        p.moveTo(cx - r * 1.15, cy - r * 0.8)
        p.lineTo(cx - r * 0.15, cy + r)
        p.lineTo(cx + r * 0.35, cy - r * 0.2)
        p.lineTo(cx + r * 0.62, cy + r * 0.25)
        p.lineTo(cx + r * 1.15, cy - r * 0.8)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
    elif s.logo == "bars":
        for i, h in enumerate((0.45, 0.75, 1.0)):
            c.rect(x + sz * (0.27 + i * 0.18), y + sz * 0.24, sz * 0.12, sz * 0.52 * h, fill=1, stroke=0)
    else:  # shield
        p = c.beginPath()
        p.moveTo(cx - r, cy + r * 0.8)
        p.lineTo(cx + r, cy + r * 0.8)
        p.lineTo(cx + r, cy - r * 0.15)
        p.lineTo(cx, cy - r)
        p.lineTo(cx - r, cy - r * 0.15)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
    c.restoreState()


def _letterhead(d: _Doc, org: str, dept: str, meta: Iterable[tuple[str, Any, str | None]], title: str, *, org_path: str | None = None, org_value: Any = None) -> float:
    """Draw letterhead; returns the y where body content can start."""
    s = d.skin
    top = PAGE_H - MARGIN - 8
    _logo(d, MARGIN, top - 28, 28)
    d.text(MARGIN + 38, top - 14, org.upper(), font=s.head, size=12.5, cs=0.4, path=org_path, value=org_value)
    d.text(MARGIN + 38, top - 26, dept, size=7.5, color=GREY)
    d.text(RIGHT, top - 14, title, font=s.head, size=13, color=s.accent, align="r")
    my = top - 26
    for label, val, path in meta:
        d.text(RIGHT - 120, my, label, size=7.5, color=GREY)
        if val is not None:
            d.text(RIGHT, my, val, size=8.5, align="r", path=path)
        my -= 10
    hy = min(top - 40, my - 2)
    d.hline(MARGIN, hy, RIGHT, color=d.skin.accent, w=1.4)
    return hy - 12


def _cap(d: _Doc, x: float, y: float, s: str) -> None:
    d.text(x, y, s.upper(), size=6.5, color=FAINT, cs=0.6)


def _continuation_head(d: _Doc, org: str, note: str) -> float:
    _logo(d, MARGIN, PAGE_H - MARGIN - 20, 18)
    d.text(MARGIN + 26, PAGE_H - MARGIN - 15, org.upper(), font=d.skin.head, size=9, color=INK)
    d.text(RIGHT, PAGE_H - MARGIN - 15, note, size=8, color=GREY, align="r")
    d.hline(MARGIN, PAGE_H - MARGIN - 26, RIGHT, color=d.skin.accent, w=1.0)
    return PAGE_H - MARGIN - 40


# ---------------------------------------------------------------------- bill

BILL_COLS = ("DATE OF SERVICE", "CODE", "MOD", "DESCRIPTION", "UNITS", "POS", "AMOUNT")
_BX = (MARGIN, 124, 164, 198, 446, 482)  # column lefts; amount right-aligned at RIGHT
_DESC_W = 446 - 198 - 8


def _bill_table_head(d: _Doc, y: float, size: float) -> float:
    d.box(MARGIN, y - 4, RIGHT - MARGIN, 14, fill=d.skin.pale)
    for label, x in zip(BILL_COLS[:-1], _BX):
        a = "c" if label in ("UNITS", "POS") else "l"
        d.text(x if a == "l" else x + 10, y, label, size=size - 0.5, color=GREY, cs=0.3, align=a)
    d.text(RIGHT, y, "AMOUNT", size=size - 0.5, color=GREY, cs=0.3, align="r")
    return y - 6


def _render_bill(d: _Doc, case: Case, compact: bool = False) -> None:
    s, b = d.skin, case.bill
    size = 7.0 if compact else 8.0
    row_h = 10.5 if compact else 13.0
    table_floor = 150  # keep room for totals + remit stub on last page

    for n, ln in enumerate(b.lines, 1):
        base = f"bill.lines.{n}"
        for fpath, val in (
            ("dos", ln.dos), ("code", ln.code), ("modifiers", ln.modifiers or None),
            ("label", ln.label), ("rendering_provider", ln.rendering_provider),
            ("pos", ln.pos), ("units", ln.units), ("charge_cents", ln.charge_cents),
        ):
            d.expect(f"{base}.{fpath}", val)
    if case.eob is None:
        d.expect("patient.member_id", case.patient.member_id)

    d.begin_page()
    _band(d)
    y = _letterhead(
        d,
        case.provider.name,
        f"Billing Service Office · {case.provider.address or 'PO Box 5550100'} · (800) 555-0142",
        (
            ("STATEMENT DATE", b.statement_date, "bill.statement_date"),
            ("ACCOUNT NO", b.account_no, "bill.account_no"),
            ("PATIENT ID", case.patient.patient_id, "patient.patient_id"),
        ),
        "PATIENT STATEMENT",
        org_path="provider.name",
        org_value=case.provider.name,
    )

    # billed-to / provider strip
    strip_h = 56 + (9 if case.patient.date_of_death else 0) + (9 if case.patient.dob else 0)
    d.box(MARGIN, y - strip_h, RIGHT - MARGIN, strip_h, fill=s.pale)
    _cap(d, MARGIN + 10, y - 12, "Billed to")
    py = y - 24
    d.text(MARGIN + 10, py, "Patient", size=size, font=s.head)
    py -= 11
    if case.patient.dob:
        d.text(MARGIN + 10, py, "DOB", size=size - 1, color=GREY)
        d.text(MARGIN + 40, py, case.patient.dob, size=size, path="patient.dob", value=case.patient.dob)
        py -= 9
    if case.patient.date_of_death:
        d.text(MARGIN + 10, py, "DECEASED", size=size - 1, color=GREY)
        d.text(MARGIN + 68, py, case.patient.date_of_death, size=size, path="patient.date_of_death", value=case.patient.date_of_death)
        py -= 9
    if case.eob is None and case.patient.member_id:
        d.text(MARGIN + 10, py, "MEMBER", size=size - 1, color=GREY)
        d.text(MARGIN + 60, py, case.patient.member_id, size=size, path="patient.member_id", value=case.patient.member_id)

    _cap(d, 330, y - 12, "Provider")
    qy = y - 24
    d.text(330, qy, case.provider.name, size=size)  # cited in letterhead
    qy -= 10
    if case.provider.npi:
        d.text(330, qy, "NPI", size=size - 1, color=GREY)
        d.text(356, qy, case.provider.npi, size=size, path="provider.npi", value=case.provider.npi)
        qy -= 10
    if case.provider.address:
        d.expect("provider.address", case.provider.address)
        d.text(330, qy, case.provider.address, size=size - 1, color=GREY, path="provider.address", value=case.provider.address)
        qy -= 10
    net = "In-network participating provider" if case.provider.in_network else "Out-of-network provider"
    d.text(330, qy, net, size=size - 1, path="provider.in_network", value=case.provider.in_network)

    y = y - strip_h - 18
    y = _bill_table_head(d, y, size)
    y -= row_h

    for ln in b.lines:
        n = ln.line_no
        base = f"bill.lines.{n}"
        label_lines = _wrap(ln.label, s.body, size, _DESC_W)
        extra = (len(label_lines) - 1) * (size + 1.5) + (8 if ln.rendering_provider else 0)
        need = row_h + extra
        if y - need < table_floor:
            d.footer(f"{case.provider.name} · Patient statement · account {b.account_no}")
            d.begin_page()
            cy = _continuation_head(d, case.provider.name, f"Account {b.account_no} · page {d.page}")
            y = _bill_table_head(d, cy, size) - row_h
        ry = y
        d.text(_BX[0], ry, ln.dos or "", size=size, path=f"{base}.dos" if ln.dos else None)
        d.text(_BX[1], ry, ln.code, size=size, font=s.num, path=f"{base}.code", value=ln.code)
        if ln.modifiers:
            d.text(_BX[2], ry, ",".join(ln.modifiers), size=size, font=s.num, path=f"{base}.modifiers", value=ln.modifiers)
        ly = ry
        for i, ll in enumerate(label_lines):
            w = d.text(_BX[3], ly, ll, size=size)
            cit = d._attach(f"{base}.label", ln.label)
            cit.text = ln.label
            d._record(cit, _BX[3], ly, w, s.body, size, ll)
            ly -= size + 1.5
        if ln.rendering_provider:
            w = d.text(_BX[3], ly, f"Ref: {ln.rendering_provider}", size=size - 1.5, color=GREY)
            cit = d._attach(f"{base}.rendering_provider", ln.rendering_provider)
            cit.text = f"Ref: {ln.rendering_provider}"
            d._record(cit, _BX[3], ly, w, s.body, size - 1.5, f"Ref: {ln.rendering_provider}")
        d.text(_BX[4] + 10, ry, ln.units, size=size, font=s.num, align="c", path=f"{base}.units", value=ln.units)
        if ln.pos:
            d.text(_BX[5] + 10, ry, ln.pos, size=size, font=s.num, align="c", path=f"{base}.pos", value=ln.pos)
        d.text(RIGHT, ry, money(ln.charge_cents), size=size, font=s.num, align="r", path=f"{base}.charge_cents", value=ln.charge_cents)
        y = ry - need
        d.hline(MARGIN, y + 4, RIGHT, color=RULE, w=0.4)

    y -= 8
    tx = RIGHT - 190
    d.text(tx, y, "Total charges", size=size, color=GREY)
    d.text(RIGHT, y, money(b.total_charges_cents), size=size, font=s.num, align="r", path="bill.total_charges_cents", value=b.total_charges_cents)
    y -= 11
    d.text(tx, y, "Insurance payments", size=size, color=GREY)
    d.text(RIGHT, y, money(-b.insurance_paid_cents), size=size, font=s.num, align="r", path="bill.insurance_paid_cents", value=b.insurance_paid_cents)
    y -= 11
    d.text(tx, y, "Contractual adjustments", size=size, color=GREY)
    d.text(RIGHT, y, money(-b.adjustments_cents), size=size, font=s.num, align="r", path="bill.adjustments_cents", value=b.adjustments_cents)
    y -= 19
    d.box(tx - 8, y - 8, RIGHT - tx + 8, 22, stroke=s.accent, lw=1.1)
    d.text(tx, y, "BALANCE DUE", size=size + 0.5, font=s.head, color=s.accent)
    d.text(RIGHT - 6, y, money(b.patient_balance_cents), size=size + 1.5, font=s.num, align="r", path="bill.patient_balance_cents", value=b.patient_balance_cents)

    # remit stub
    stub_y = 108
    d.hline(MARGIN, stub_y + 34, RIGHT, color=FAINT, w=0.7, dash=(3, 3))
    _cap(d, MARGIN, stub_y + 22, "Detach and return with payment")
    d.text(MARGIN, stub_y + 10, "Remit to:", size=7, color=GREY)
    d.text(MARGIN + 44, stub_y + 10, f"{case.provider.name} Billing · PO Box 5550100 · Fairview, CA 94102", size=7.5)
    d.text(MARGIN, stub_y, f"Account {b.account_no}", size=7.5)
    d.text(RIGHT - 120, stub_y + 10, "Amount enclosed  $", size=7.5, color=GREY)
    d.box(RIGHT - 64, stub_y + 7, 64, 12, stroke=RULE)
    d.text(RIGHT, stub_y + 22, f"Amount due: {money(b.patient_balance_cents)}", size=7.5, align="r", color=GREY)
    d.footer(f"{case.provider.name} · Patient statement · account {b.account_no}")


# ----------------------------------------------------------------------- eob

EOB_COLS = ("LINE", "CODE", "UNITS", "BILLED", "ALLOWED", "PLAN PAID", "YOUR COST", "NOTES / ADJUSTMENT CODES")
_EX_C = {"LINE": 58, "CODE": 104, "UNITS": 144, "BILLED": 196, "ALLOWED": 262, "PLAN PAID": 326, "YOUR COST": 388}
_NOTES_X = 430


def _eob_table_head(d: _Doc, y: float, size: float) -> float:
    d.box(MARGIN, y - 4, RIGHT - MARGIN, 14, fill=d.skin.pale)
    for label in EOB_COLS[:-1]:
        d.text(_EX_C[label], y, label, size=size - 0.5, color=GREY, cs=0.2, align="c")
    d.text(_NOTES_X, y, "NOTES / ADJUSTMENT CODES", size=size - 0.5, color=GREY, cs=0.2)
    return y - 6


def _render_eob(d: _Doc, case: Case, compact: bool = False) -> None:
    s, eob = d.skin, case.eob
    assert eob is not None
    size = 7.0 if compact else 8.0
    row_h = 10.5 if compact else 13.0
    table_floor = 170

    for n, ln in enumerate(eob.lines, 1):
        base = f"eob.lines.{ln.line_no}"
        for fpath, val in (
            ("code", ln.code), ("units", ln.units), ("billed_cents", ln.billed_cents),
            ("allowed_cents", ln.allowed_cents), ("plan_paid_cents", ln.plan_paid_cents),
            ("patient_resp_cents", ln.patient_resp_cents), ("carc", ln.carc or None),
            ("remark", ln.remark),
        ):
            d.expect(f"{base}.{fpath}", val)
    d.expect("patient.member_id", case.patient.member_id)
    d.expect("patient.patient_id", case.patient.patient_id)

    d.begin_page()
    _band(d)
    y = _letterhead(
        d,
        eob.payer,
        "Member Services · (800) 555-0177 · PO Box 5550177 · Fairview, CA 94102",
        (
            ("CLAIM ID", eob.claim_id, "eob.claim_id"),
            ("PROCESSED", eob.processed_date, "eob.processed_date"),
            ("PATIENT", case.patient.patient_id, None),
        ),
        "EXPLANATION OF BENEFITS",
        org_path="eob.payer",
        org_value=eob.payer,
    )

    d.box(MARGIN, y - 18, RIGHT - MARGIN, 18, fill=s.pale)
    d.text(PAGE_W / 2, y - 13, "THIS IS NOT A BILL", font=s.head, size=10, color=s.accent, align="c")
    y -= 30

    # member / claim grid
    _cap(d, MARGIN, y, "Member")
    d.text(MARGIN, y - 11, "Member ID", size=size - 1, color=GREY)
    if case.patient.member_id:
        d.text(MARGIN + 60, y - 11, case.patient.member_id, size=size, path="patient.member_id", value=case.patient.member_id)
    d.text(MARGIN, y - 21, "Patient", size=size - 1, color=GREY)
    d.text(MARGIN + 60, y - 21, case.patient.patient_id, size=size, path="patient.patient_id", value=case.patient.patient_id)
    _cap(d, 300, y, "Provider")
    d.text(300, y - 11, case.provider.name, size=size)
    _cap(d, 300, y - 21, "Network")
    d.text(300, y - 32, "In-network" if case.provider.in_network else "Out-of-network", size=size)
    _cap(d, 460, y, "Claim")
    d.text(460, y - 11, "Claim ID", size=size - 1, color=GREY)
    d.text(460, y - 21, eob.claim_id, size=size, font=s.num)  # cited in letterhead
    y -= 54

    y = _eob_table_head(d, y, size)
    y -= row_h
    for ln in eob.lines:
        n = ln.line_no
        base = f"eob.lines.{n}"
        remark_lines = _wrap(ln.remark, s.body, size - 1, RIGHT - _NOTES_X - 4) if ln.remark else []
        need = row_h + (len(remark_lines) * (size + 1) if remark_lines else 0) + (8 if ln.carc else 0)
        if y - need < table_floor:
            d.footer(f"{eob.payer} · Explanation of Benefits · claim {eob.claim_id}")
            d.begin_page()
            cy = _continuation_head(d, eob.payer, f"Claim {eob.claim_id} · page {d.page}")
            y = _eob_table_head(d, cy, size) - row_h
        ry = y
        d.text(_EX_C["LINE"], ry, str(n), size=size, align="c")
        d.text(_EX_C["CODE"], ry, ln.code, size=size, font=s.num, align="c", path=f"{base}.code", value=ln.code)
        d.text(_EX_C["UNITS"], ry, ln.units, size=size, font=s.num, align="c", path=f"{base}.units", value=ln.units)
        d.text(_EX_C["BILLED"], ry, money(ln.billed_cents), size=size, font=s.num, align="c", path=f"{base}.billed_cents", value=ln.billed_cents)
        d.text(_EX_C["ALLOWED"], ry, money(ln.allowed_cents), size=size, font=s.num, align="c", path=f"{base}.allowed_cents", value=ln.allowed_cents)
        d.text(_EX_C["PLAN PAID"], ry, money(ln.plan_paid_cents), size=size, font=s.num, align="c", path=f"{base}.plan_paid_cents", value=ln.plan_paid_cents)
        d.text(_EX_C["YOUR COST"], ry, money(ln.patient_resp_cents), size=size, font=s.num, align="c", path=f"{base}.patient_resp_cents", value=ln.patient_resp_cents)
        ny = ry
        if ln.carc:
            carc_s = ", ".join(ln.carc)
            d.text(_NOTES_X, ny, carc_s, size=size - 0.5, font=s.num, path=f"{base}.carc", value=ln.carc)
            ny -= size + 1
        for rl in remark_lines:
            w = d.text(_NOTES_X, ny, rl, size=size - 1, color=GREY)
            cit = d._attach(f"{base}.remark", ln.remark)
            cit.text = ln.remark
            d._record(cit, _NOTES_X, ny, w, s.body, size - 1, rl)
            ny -= size + 1
        y = ry - need
        d.hline(MARGIN, y + 4, RIGHT, color=RULE, w=0.4)

    y -= 8
    tot_b = sum(ln.billed_cents for ln in eob.lines)
    tot_a = sum(ln.allowed_cents for ln in eob.lines)
    tot_p = sum(ln.plan_paid_cents for ln in eob.lines)
    d.text(_EX_C["CODE"], y, "TOTALS", size=size - 0.5, color=GREY, align="c")
    for col, v in (("BILLED", tot_b), ("ALLOWED", tot_a), ("PLAN PAID", tot_p)):
        d.text(_EX_C[col], y, money(v), size=size, font=s.num, align="c")
    d.box(366, y - 7, RIGHT - 366, 20, stroke=s.accent, lw=1.1)
    d.text(374, y, "TOTAL YOUR COST", size=size - 0.5, font=s.head, color=s.accent)
    d.text(RIGHT - 8, y, money(eob.patient_resp_total_cents), size=size + 1, font=s.num, align="r", path="eob.patient_resp_total_cents", value=eob.patient_resp_total_cents)

    # code glossary + disclaimer
    gy = y - 30
    carcs = sorted({c for ln in eob.lines for c in ln.carc})
    if carcs:
        _cap(d, MARGIN, gy, "Adjustment and remark codes")
        gy -= 11
        gloss = _carc_gloss()
        for code in carcs:
            if gy < 80:
                break
            d.text(MARGIN, gy, code, size=6.8, font=s.num)
            d.para(MARGIN + 44, gy, 300, gloss.get(code, "Adjustment applied per plan contract terms."), size=6.8, color=GREY, max_lines=2)
            gy -= 18
    d.footer(f"{eob.payer} · Explanation of Benefits · claim {eob.claim_id} · This is not a bill.")


_CARC_GLOSS: dict[str, str] | None = None


def _carc_gloss() -> dict[str, str]:
    global _CARC_GLOSS
    if _CARC_GLOSS is None:
        _CARC_GLOSS = {}
        try:
            import json
            from billproof.config import CMS_DIR

            f = CMS_DIR / "carc_rarc.json"
            if f.exists():
                data = json.loads(f.read_text())
                for k, v in data.get("carc", {}).items():
                    if isinstance(v, dict) and v.get("text"):
                        _CARC_GLOSS[k] = str(v["text"])
        except Exception:
            pass
    return _CARC_GLOSS


# -------------------------------------------------------------------- denial


def _render_denial(d: _Doc, case: Case) -> None:
    s, den = d.skin, case.denial
    assert den is not None
    size = 9.0
    payer = case.eob.payer if case.eob else "Plan Administrator"

    d.expect("denial.letter_date", den.letter_date)
    d.expect("denial.carc", den.carc)
    d.expect("denial.category", den.category)
    d.expect("denial.reason_text", den.reason_text)
    d.expect("denial.service_code", den.service_code)
    d.expect("denial.appeal_deadline", den.appeal_deadline)
    d.expect("denial.plan_section", den.plan_section)

    d.begin_page()
    _band(d)
    y = _letterhead(
        d,
        payer,
        "Grievances & Appeals Department · (800) 555-0139 · PO Box 5550139 · Fairview, CA 94102",
        (("DATE OF NOTICE", den.letter_date, "denial.letter_date"),),
        "IMPORTANT NOTICE",
    )

    _cap(d, MARGIN, y, "Re")
    ry = y - 12
    d.text(MARGIN, ry, f"Member: {case.patient.patient_id}" + (f" · Member ID {case.patient.member_id}" if case.patient.member_id else ""), size=size)
    ry -= 11
    if case.eob:
        d.text(MARGIN, ry, f"Claim: {case.eob.claim_id} · processed {case.eob.processed_date}", size=size)
        ry -= 11
    if den.service_code:
        d.text(MARGIN, ry, "Service in question:", size=size, color=GREY)
        d.text(MARGIN + 108, ry, den.service_code, size=size, font=s.num, path="denial.service_code", value=den.service_code)
        ry -= 11
    y = ry - 14

    d.text(MARGIN, y, "NOTICE OF ADVERSE BENEFIT DETERMINATION", font=s.head, size=12.5, color=s.accent, cs=0.5)
    d.hline(MARGIN, y - 8, RIGHT, color=s.accent, w=1.0)
    y -= 28

    d.text(MARGIN, y, "Determination:", size=size, color=GREY)
    wx = MARGIN + 86
    w = d.text(wx, y, den.carc, size=size + 0.5, font=s.head, path="denial.carc", value=den.carc)
    cat_words = den.category.value.replace("_", " ").title()
    d.text(wx + w + 14, y, "·", size=size, color=FAINT)
    d.text(wx + w + 28, y, cat_words, size=size + 0.5, font=s.head, path="denial.category", value=den.category)
    y -= 16

    y = d.para(MARGIN, y, RIGHT - MARGIN, den.reason_text, size=size, leading=size * 1.45, path="denial.reason_text", value=den.reason_text)
    y -= 12

    if den.plan_section:
        d.text(MARGIN, y, "Plan reference:", size=size, color=GREY)
        d.text(MARGIN + 86, y, den.plan_section, size=size, path="denial.plan_section", value=den.plan_section)
        y -= 22

    # appeal box
    box_h = 118
    d.box(MARGIN, y - box_h, RIGHT - MARGIN, box_h, stroke=s.accent, lw=1.2)
    d.text(MARGIN + 12, y - 18, "YOUR RIGHT TO APPEAL THIS DECISION", font=s.head, size=9.5, color=s.accent)
    ay = y - 34
    ay = d.para(
        MARGIN + 12,
        ay,
        RIGHT - MARGIN - 24,
        "You or your authorized representative may request an internal appeal of this determination. "
        "If the internal appeal is unsuccessful, you may have the right to an independent external "
        "review at no cost to you. Submit appeals in writing to the Grievances & Appeals Department "
        "at the address above, or by fax to (800) 555-0140.",
        size=8,
        leading=11,
        color=GREY,
    )
    ay -= 8
    if den.appeal_deadline:
        d.text(MARGIN + 12, ay, "APPEAL DEADLINE:", font=s.head, size=9, color=INK)
        d.text(MARGIN + 122, ay, den.appeal_deadline, font=s.head, size=9.5, path="denial.appeal_deadline", value=den.appeal_deadline)

    sy = y - box_h - 40
    d.text(MARGIN, sy, "Sincerely,", size=size, color=GREY)
    d.text(MARGIN, sy - 24, "Grievances & Appeals Department", size=size, font=s.head)
    d.text(MARGIN, sy - 36, payer, size=size - 1, color=GREY)
    d.footer(f"{payer} · Adverse benefit determination · member {case.patient.patient_id}")


# ---------------------------------------------------------------------- plan


def _render_plan(d: _Doc, case: Case) -> None:
    s, plan = d.skin, case.plan
    payer = case.eob.payer if case.eob else plan.plan_name
    size = 9.0

    for p, v in (
        ("plan.plan_name", plan.plan_name), ("plan.plan_type", plan.plan_type),
        ("plan.deductible_cents", plan.deductible_cents), ("plan.deductible_met_cents", plan.deductible_met_cents),
        ("plan.copay_cents", plan.copay_cents), ("plan.coinsurance_pct", plan.coinsurance_pct),
        ("plan.oop_max_cents", plan.oop_max_cents), ("plan.emergency_nsa_protected", plan.emergency_nsa_protected),
    ):
        d.expect(p, v)

    d.begin_page()
    _band(d)
    y = _letterhead(
        d,
        payer,
        "Member Services · (800) 555-0177",
        (("MEMBER", case.patient.member_id or case.patient.patient_id, None),),
        "SUMMARY OF BENEFITS",
    )

    d.text(MARGIN, y, "Plan:", size=size, color=GREY)
    w = d.text(MARGIN + 34, y, plan.plan_name, font=s.head, size=11, path="plan.plan_name", value=plan.plan_name)
    d.text(MARGIN + 34 + w + 12, y, plan.plan_type, font=s.head, size=11, color=s.accent, path="plan.plan_type", value=plan.plan_type)
    d.text(RIGHT, y, "Coverage year 2026", size=size - 1, color=GREY, align="r")
    y -= 22
    d.hline(MARGIN, y, RIGHT, color=RULE)
    y -= 18

    rows = [
        ("Annual deductible (individual)", money(plan.deductible_cents), "plan.deductible_cents", plan.deductible_cents),
        ("Deductible met to date", money(plan.deductible_met_cents), "plan.deductible_met_cents", plan.deductible_met_cents),
        ("Office visit copay", money(plan.copay_cents), "plan.copay_cents", plan.copay_cents),
        ("Coinsurance (you pay)", f"{plan.coinsurance_pct}%", "plan.coinsurance_pct", plan.coinsurance_pct),
        ("Out-of-pocket maximum", money(plan.oop_max_cents), "plan.oop_max_cents", plan.oop_max_cents),
        ("No Surprises Act protection (emergency)", "Applies" if plan.emergency_nsa_protected else "Does not apply", "plan.emergency_nsa_protected", plan.emergency_nsa_protected),
    ]
    for label, val, path, v in rows:
        d.text(MARGIN + 8, y, label, size=size)
        d.text(RIGHT - 8, y, val, size=size, font=s.num, align="r", path=path, value=v)
        y -= 8
        d.hline(MARGIN, y, RIGHT, color=RULE, w=0.4)
        y -= 12

    y -= 14
    _cap(d, MARGIN, y, "Coverage notes")
    y -= 12
    for note in (
        "Amounts shown are illustrative plan terms for this synthetic record.",
        "Emergency services are covered at the in-network cost-sharing level when No Surprises Act protections apply.",
        "Non-emergency services from out-of-network providers may result in additional member responsibility.",
        "This summary is not a guarantee of payment; the plan document controls.",
    ):
        d.text(MARGIN + 8, y, "·", size=size, color=s.accent)
        y = d.para(MARGIN + 18, y, RIGHT - MARGIN - 18, note, size=size - 1.5, leading=size * 1.25, color=GREY)
        y -= 4
    d.footer(f"{payer} · Summary of benefits · {plan.plan_name}")


# -------------------------------------------------------------------- driver


@dataclass
class _DocOut:
    ref: DocRef
    fields: list[dict]
    checks: list[tuple[int, list[tuple[float, float]], str]]
    cites: dict[str, Citation]


@dataclass
class CaseRender:
    docs: list[DocRef]
    citations: dict[str, Citation]
    fixture: dict
    checks: list[tuple[str, int, list[tuple[float, float]], str]]  # (doc, page, poly, text)


def _render_doc(case: Case, kind: DocKind, out: Path, payer_seed: str) -> _DocOut:
    skin = _skin(case.provider.name) if kind == "bill" else _skin(payer_seed)
    title = {
        "bill": f"Patient Statement — {case.provider.name}",
        "eob": f"Explanation of Benefits — {payer_seed}",
        "denial": f"Notice of Adverse Benefit Determination — {payer_seed}",
        "plan": f"Summary of Benefits — {payer_seed}",
    }[kind]
    d = _Doc(out, kind, skin, title)
    compact = len(case.bill.lines) > 40 or (case.eob and len(case.eob.lines) > 40)
    if kind == "bill":
        _render_bill(d, case, compact=compact)
    elif kind == "eob":
        _render_eob(d, case, compact=compact)
    elif kind == "denial":
        _render_denial(d, case)
    else:
        _render_plan(d, case)
    pages = d.finish()
    if pages > MAX_PAGES:
        raise RuntimeError(f"{out.name}: {pages} pages exceeds {MAX_PAGES}")
    cites = {f["path"]: f["cit"] for f in d.fields if f["cit"] is not None}
    rel = _rel(out)
    return _DocOut(DocRef(kind=kind, pdf=rel, pages=pages), d.fixture_fields(), d.checks, cites)


def _rel(out: Path) -> str:
    try:
        from billproof.config import PKG_ROOT

        return str(out.resolve().relative_to(PKG_ROOT))
    except Exception:
        return str(out)


def render_case(case: Case, pdf_dir: Path) -> CaseRender:
    """Render all docs for a case into pdf_dir; return refs, citations, fixture."""
    pdf_dir = Path(pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    payer_seed = case.eob.payer if case.eob else case.plan.plan_name
    kinds = [k for k in DOC_KINDS if k != "eob" or case.eob is not None]
    kinds = [k for k in kinds if k != "denial" or case.denial is not None]

    citations: dict[str, Citation] = {}
    docs: list[DocRef] = []
    fixture_docs: dict[str, dict] = {}
    checks: list[tuple[str, int, list, str]] = []
    for kind in kinds:
        out = pdf_dir / f"{case.case_id}_{kind}.pdf"
        r = _render_doc(case, kind, out, payer_seed)
        docs.append(r.ref)
        for k, v in r.cites.items():
            citations.setdefault(k, v)
        fixture_docs[kind] = {"pages": r.ref.pages, "fields": r.fields}
        checks.extend((kind, p, poly, t) for p, poly, t in r.checks)
    fixture = {"case_id": case.case_id, "docs": fixture_docs}
    return CaseRender(docs=docs, citations=citations, fixture=fixture, checks=checks)


# ------------------------------------------------------------------- verify


def verify_render(render: CaseRender, pdf_dir: Path, case_id: str) -> list[str]:
    """Check every tracked string is really inside its polygon (pypdfium2)."""
    import pypdfium2 as pdfium

    problems: list[str] = []
    docs: dict[str, Any] = {}
    for doc, page_no, poly, want in render.checks:
        path = pdf_dir / f"{case_id}_{doc}.pdf"
        if doc not in docs:
            docs[doc] = pdfium.PdfDocument(str(path))
        page = docs[doc][page_no - 1]
        tp = page.get_textpage()
        l, b, r, t = _denorm(poly)
        got = " ".join((tp.get_text_bounded(l - 0.5, b - 0.5, r + 0.5, t + 0.5) or "").split())
        w = " ".join(str(want).split())
        if w and w not in got:
            problems.append(f"{doc}.pdf p{page_no}: want {w!r} in box, got {got!r}")
    return problems


def emit_crops(render: CaseRender, pdf_dir: Path, case_id: str, out_dir: Path, limit: int = 24) -> list[Path]:
    """Render citation crops to PNGs for eyeball QA. Returns written paths."""
    import pypdfium2 as pdfium

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    docs: dict[str, Any] = {}
    per: dict[str, int] = {}
    for doc, page_no, poly, text in render.checks:
        if per.get(doc, 0) >= max(1, limit // 4):
            continue
        path = pdf_dir / f"{case_id}_{doc}.pdf"
        if doc not in docs:
            docs[doc] = pdfium.PdfDocument(str(path))
        page = docs[doc][page_no - 1]
        l, b, r, t = _denorm(poly)
        l = max(0.0, l - 2)
        b = max(0.0, b - 3)
        r = min(PAGE_W, r + 2)
        t = min(PAGE_H, t + 3)
        # pypdfium2 crop = per-side margins removed (left, bottom, right, top)
        bmp = page.render(scale=3, crop=(l, b, PAGE_W - r, PAGE_H - t))
        img = bmp.to_pil()
        safe = "".join(ch if ch.isalnum() else "_" for ch in text)[:30]
        p = out_dir / f"{doc}_p{page_no}_{len(written):03d}_{safe}.png"
        img.save(p)
        written.append(p)
        per[doc] = per.get(doc, 0) + 1
    return written

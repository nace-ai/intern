"""PDF manipulation helpers for building benchmark cases.

Public API (used by scripts/build_cases.py and by forge.py):
    forge_iban(pdf_bytes, new_iban) -> bytes
    add_note(pdf_bytes, text, style) -> bytes   # style: "body" | "faint"
"""
from __future__ import annotations

import pymupdf

__all__ = ["forge_iban", "add_note"]

_BODY_COLOR = (0x11 / 255, 0x18 / 255, 0x27 / 255)  # matches the invoice body text
_FAINT_COLOR = (0.6, 0.6, 0.6)
_FONT = "helv"
_MARGIN_X0 = 39.685
_MARGIN_X1 = 555.59


def _format_iban(iban: str) -> str:
    s = "".join(iban.split()).upper()
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


def _open(pdf_bytes: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=pdf_bytes, filetype="pdf")


def _save(doc: pymupdf.Document) -> bytes:
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def _find_iban_line(page: pymupdf.Page):
    """Return (label_word, [iban_token_words]) for the 'IBAN:' line on the page."""
    words = page.get_text("words")
    labels = [w for w in words if w[4] == "IBAN:"]
    if not labels:
        raise ValueError("no 'IBAN:' word on page 0")
    label = labels[0]
    tokens = [
        w for w in words
        if abs(w[1] - label[1]) <= 1.0 and w[0] > label[2] - 0.5 and w is not label
    ]
    tokens.sort(key=lambda w: w[0])
    # keep only the contiguous run right after the label (stop at a big gap)
    run, last_x1 = [], label[2]
    for w in tokens:
        if w[0] - last_x1 > 20:
            break
        run.append(w)
        last_x1 = w[2]
    if not run:
        raise ValueError("no IBAN tokens after 'IBAN:'")
    return label, run


def _span_style(page: pymupdf.Page, rect: pymupdf.Rect):
    """Font size, baseline y and colour of the first span overlapping rect."""
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if pymupdf.Rect(s["bbox"]).intersects(rect) and s["text"].strip():
                    c = s["color"]
                    color = ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
                    return s["size"], s["origin"][1], color
    return rect.height * 0.85, rect.y1 - 2, (0, 0, 0)


def forge_iban(pdf_bytes: bytes, new_iban: str) -> bytes:
    """Replace the printed IBAN on page 0 with new_iban (groups of four)."""
    doc = _open(pdf_bytes)
    page = doc[0]
    label, run = _find_iban_line(page)
    rect = pymupdf.Rect(run[0][:4])
    for w in run[1:]:
        rect |= pymupdf.Rect(w[:4])
    size, baseline, color = _span_style(page, rect)

    # Redact only the IBAN tokens; keep drawings and images untouched.
    redact = pymupdf.Rect(rect.x0 + 0.3, rect.y0 + 0.5, rect.x1 + 0.5, rect.y1 - 0.5)
    page.add_redact_annot(redact, fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0, text=0)

    page.insert_text(
        (rect.x0, baseline), _format_iban(new_iban),
        fontsize=size, fontname=_FONT, color=color,
    )
    return _save(doc)


def _wrap(text: str, fontsize: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if pymupdf.get_text_length(cand, fontname=_FONT, fontsize=fontsize) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _obstacles(page: pymupdf.Page) -> list[pymupdf.Rect]:
    obs = [pymupdf.Rect(w[:4]) + (-2, -2, 2, 2) for w in page.get_text("words")]
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if r.width >= page.rect.width - 1 and r.y0 <= 1:  # full-width header band
            obs.append(r)
            continue
        obs.append(r + (-1, -1, 1, 1))
    return obs


def _free(rect: pymupdf.Rect, obs: list[pymupdf.Rect]) -> bool:
    return not any(rect.intersects(o) for o in obs)


def add_note(pdf_bytes: bytes, text: str, style: str) -> bytes:
    """Print text in free space on page 0.

    style="body":  normal body text placed under the bank block.
    style="faint": 6pt grey (0.6) text placed just above the page footer.
    """
    if style not in ("body", "faint"):
        raise ValueError(f"unknown style {style!r}")
    doc = _open(pdf_bytes)
    page = doc[0]
    words = page.get_text("words")
    obs = _obstacles(page)

    # The page footer ("Invoice ... Page 1 / 1") is the lowest line; never go below it.
    footer_top = min(
        (w[1] for w in words if w[1] > page.rect.height - 60),
        default=page.rect.height - 40,
    ) - 3

    try:
        label, _ = _find_iban_line(page)
        bank_bottom = label[3]
        bic = [w for w in words if w[4].startswith("BIC") and 0 < w[1] - label[1] < 20]
        if bic:
            bank_bottom = bic[0][3]
    except ValueError:
        bank_bottom = page.rect.height / 2

    if style == "body":
        sizes, color = (9.0, 8.5, 8.0, 7.5, 7.0), _BODY_COLOR
    else:
        sizes, color = (6.0,), _FAINT_COLOR
    widths = [_MARGIN_X1 - _MARGIN_X0, 290.0]  # full width, or left of the totals box

    placed = None
    for fs in sizes:
        lh = fs * 1.25
        for width in widths:
            lines = _wrap(text, fs, width)
            h = lh * len(lines)
            if style == "body":
                ys = [bank_bottom + 4 + i * 0.5 for i in range(int((footer_top - h - bank_bottom - 4) * 2) + 1)]
            else:
                top = footer_top - h
                ys = [top - i * 0.5 for i in range(int((top - bank_bottom) * 2) + 1)]
            for y in ys:
                r = pymupdf.Rect(_MARGIN_X0, y, _MARGIN_X0 + width, y + h)
                if r.y1 <= footer_top and _free(r, obs):
                    placed = (y, fs, lh, lines)
                    break
            if placed:
                break
        if placed:
            break
    if placed is None:
        doc.close()
        raise ValueError("no free space for note")

    y, fs, lh, lines = placed
    for i, line in enumerate(lines):
        page.insert_text(
            (_MARGIN_X0, y + i * lh + fs * 0.8), line,
            fontsize=fs, fontname=_FONT, color=color,
        )
    return _save(doc)

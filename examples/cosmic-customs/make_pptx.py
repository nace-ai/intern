"""Write the Cosmic Customs briefing deck. Requires python-pptx locally."""

from __future__ import annotations

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

OUT = "submission/Cosmic-Customs.pptx"
GITHUB = "https://github.com/bashaboinamanojkumar/cosmic-customs"
SITE = "https://cosmic-customs.vercel.app"

NAVY = RGBColor(0x07, 0x11, 0x1F)
NAVY2 = RGBColor(0x10, 0x20, 0x33)
INK = RGBColor(0xD7, 0xE2, 0xEF)
MUTED = RGBColor(0x8E, 0xA0, 0xB5)
AMBER = RGBColor(0xE2, 0xA2, 0x3B)
AMBER2 = RGBColor(0xFF, 0xD2, 0x7A)
CREAM = RGBColor(0xF6, 0xF1, 0xE6)
CLEAR = RGBColor(0x7D, 0xCE, 0xA0)
HOLD = RGBColor(0xD4, 0x64, 0x4A)

W = Inches(13.333)
H = Inches(7.5)


def _set_run(run, text: str, size: int, color: RGBColor, bold: bool = False, font: str = "Calibri") -> None:
    run.text = text
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.name = font


def add_text(slide, text, left, top, width, height, size, color, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="Calibri"):
    shape = slide.shapes.add_textbox(left, top, width, height)
    frame = shape.text_frame
    frame.word_wrap = True
    frame.auto_size = None
    frame.margin_left = Emu(0)
    frame.margin_right = Emu(0)
    frame.margin_top = Emu(0)
    frame.margin_bottom = Emu(0)
    try:
        frame._txBody.bodyPr.set("anchor", {MSO_ANCHOR.TOP: "t", MSO_ANCHOR.MIDDLE: "ctr", MSO_ANCHOR.BOTTOM: "b"}[anchor])
    except Exception:
        pass
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    _set_run(paragraph.add_run(), text, size, color, bold, font)
    return shape


def add_bullets(slide, lines, left, top, width, height, size=20, color=INK, spacing=8):
    shape = slide.shapes.add_textbox(left, top, width, height)
    frame = shape.text_frame
    frame.word_wrap = True
    frame.margin_left = Emu(0)
    frame.margin_right = Emu(0)
    frame.margin_top = Emu(0)
    frame.margin_bottom = Emu(0)
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.space_after = Pt(spacing)
        paragraph.level = 0
        _set_run(paragraph.add_run(), line, size, color, False)
    return shape


def paint(slide) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = NAVY
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, Inches(0.08))
    bar.fill.solid()
    bar.fill.fore_color.rgb = AMBER
    bar.line.fill.background()
    footer = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Inches(7.18), W, Inches(0.32))
    footer.fill.solid()
    footer.fill.fore_color.rgb = NAVY2
    footer.line.fill.background()


def footer(slide, number: int, total: int) -> None:
    add_text(slide, "Manoj Kumar Bashaboina  ·  Cosmic Customs  ·  Nace Drexathon", Inches(0.45), Inches(7.2), Inches(9.2), Inches(0.26), 12, MUTED)
    add_text(slide, f"{number}  /  {total}", Inches(11.2), Inches(7.2), Inches(1.7), Inches(0.26), 12, AMBER, align=PP_ALIGN.RIGHT)


def notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def card(slide, left, top, width, height, fill):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    # Tighten the corner a little so the cards look like the dashboard panels.
    try:
        shape.adjustments[0] = 0.08
    except Exception:
        pass
    return shape


def new_slide(prs, total):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    paint(slide)
    footer(slide, len(prs.slides), total)
    return slide


def build() -> Presentation:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    prs.core_properties.author = "Manoj Kumar Bashaboina"
    prs.core_properties.last_modified_by = "Manoj Kumar Bashaboina"
    prs.core_properties.title = "Cosmic Customs"
    prs.core_properties.subject = "Nace Drexathon briefing"
    prs.core_properties.category = "Briefing"
    total = 7

    slide = new_slide(prs, total)
    add_text(slide, "ORBITAL CUSTOMS TERMINAL", Inches(0.7), Inches(1.35), Inches(11), Inches(0.34), 16, AMBER, True)
    add_text(slide, "Cosmic Customs", Inches(0.65), Inches(1.75), Inches(12), Inches(1.05), 60, CREAM, True)
    add_text(
        slide,
        "A fictional cargo desk that applies a written rulebook, then asks Nace Drex\nand a frontier model to make the same call on the same evidence.",
        Inches(0.7),
        Inches(3.05),
        Inches(11.2),
        Inches(1.15),
        24,
        INK,
    )
    add_text(slide, "Manoj Kumar Bashaboina", Inches(0.7), Inches(4.7), Inches(8), Inches(0.4), 20, AMBER2, True)
    add_text(slide, "Nace Drexathon  ·  November 2026", Inches(0.7), Inches(5.15), Inches(8), Inches(0.36), 18, MUTED)
    notes(
        slide,
        "Cosmic Customs is an orbital customs desk for fictional cargo. A deterministic rulebook labels every shipment. "
        "Drex and a frontier model see the same evidence and do not grade themselves.",
    )

    slide = new_slide(prs, total)
    add_text(slide, "The question", Inches(0.55), Inches(0.32), Inches(8), Inches(0.55), 32, CREAM, True)
    add_text(
        slide,
        "Can a fast decision model follow the rulebook without clearing a shipment that should be held or reviewed?",
        Inches(0.55),
        Inches(1.05),
        Inches(12.2),
        Inches(0.85),
        22,
        AMBER2,
    )
    points = [
        "Each shipment has a manifest, a containment certificate, and a watch note.",
        "The desk returns one verdict: CLEAR, HOLD, or REVIEW.",
        "A deterministic oracle, rules.decide, writes the label. The models never do.",
        "Both models receive the rulebook and the evidence only. The expected label stays off the page.",
        "Mock mode is the default. Opening the desk makes no network call and spends nothing.",
    ]
    add_bullets(slide, points, Inches(0.6), Inches(2.15), Inches(12.1), Inches(4.5), 22, INK, 14)
    notes(slide, "Lead with the safety question: speed is useful only if a dangerous or incomplete shipment is not cleared.")

    slide = new_slide(prs, total)
    add_text(slide, "The rulebook, in order", Inches(0.55), Inches(0.32), Inches(10), Inches(0.55), 32, CREAM, True)
    rules = [
        ("01  HOLD", "A damaged seal, or known pressure below 90 kPa, is HOLD. Exactly 90 kPa is not. This still wins when other facts conflict."),
        ("02  REVIEW", "An unresolved contradiction or a missing required fact is REVIEW. Above a 3% weight gap is REVIEW. Exactly 3% passes."),
        ("03  Waiver", "Singing cargo needs a valid certificate, or a valid waiver that is not class-4 acoustic. A waiver cannot override a physical hazard."),
        ("04  CLEAR", "Everything else is CLEAR. Class-4 cargo alone is not a hold. A corrected suspicion is not a damaged seal."),
    ]
    top = 1.15
    for title, body in rules:
        card(slide, Inches(0.5), Inches(top), Inches(12.3), Inches(1.3), NAVY2)
        add_text(slide, title, Inches(0.75), Inches(top + 0.16), Inches(2.3), Inches(0.95), 18, AMBER, True, anchor=MSO_ANCHOR.MIDDLE)
        add_text(slide, body, Inches(3.15), Inches(top + 0.18), Inches(9.3), Inches(0.95), 18, INK, anchor=MSO_ANCHOR.MIDDLE)
        top += 1.42
    notes(slide, "Walk the precedence from the top. The boundary cases, exactly 90 kPa and exactly 3 percent, are part of the test.")

    slide = new_slide(prs, total)
    add_text(slide, "A fair comparison", Inches(0.55), Inches(0.32), Inches(10), Inches(0.55), 32, CREAM, True)
    columns = [
        ("Same page", "One shared prompt, cosmic-customs-prompt-1. Drex answers a single CLEAR / HOLD / REVIEW question. The frontier model returns one JSON verdict."),
        ("Held out", "20 development cases expose every rule. 100 held-out cases are 33 CLEAR, 33 HOLD, and 34 REVIEW. Settings froze after the five-case pilot."),
        ("Separate sets", "A 34-case hard set is for diagnosis only. It is not part of the published score, and it has not been run live."),
    ]
    left = 0.45
    for title, body in columns:
        card(slide, Inches(left), Inches(1.25), Inches(4.0), Inches(4.55), NAVY2)
        add_text(slide, title, Inches(left + 0.25), Inches(1.5), Inches(3.5), Inches(0.7), 22, AMBER, True)
        add_text(slide, body, Inches(left + 0.25), Inches(2.35), Inches(3.5), Inches(3.0), 18, INK)
        left += 4.2
    notes(slide, "Stress that both providers saw the same text, and that the hard set must not be quoted as the measured result.")

    slide = new_slide(prs, total)
    add_text(slide, "Held-out result, 100 cases", Inches(0.55), Inches(0.28), Inches(9), Inches(0.5), 32, CREAM, True)
    add_text(slide, "Run 2026-11-14T215926+0000-36032342  ·  0 failures", Inches(0.55), Inches(0.85), Inches(10), Inches(0.32), 14, MUTED)
    headers = ["", "Drex  drex-max-v1.1", "Frontier  gpt-5.4"]
    rows = [
        ["Correct", "89 / 100", "95 / 100"],
        ["False clearance", "8 / 67", "3 / 67"],
        ["Speed, P50 / P95", "285 ms  /  447 ms", "1,083 ms  /  1,361 ms"],
        ["Known cost", "$0.00249756", "$0.1967275"],
    ]
    table_shape = slide.shapes.add_table(5, 3, Inches(0.55), Inches(1.4), Inches(12.2), Inches(3.55))
    table = table_shape.table
    table.columns[0].width = Inches(3.1)
    table.columns[1].width = Inches(4.55)
    table.columns[2].width = Inches(4.55)
    data = [headers] + rows
    for r, row in enumerate(data):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = ""
            cell.margin_left = Inches(0.16)
            cell.margin_right = Inches(0.1)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.LEFT
            color = AMBER2 if r == 0 else (CREAM if c else MUTED)
            weight = r == 0 or c > 0
            _set_run(paragraph.add_run(), value, 20 if r else 16, color, weight)
            fill = cell.fill
            fill.solid()
            fill.fore_color.rgb = RGBColor(0x1A, 0x2C, 0x42) if r == 0 else (NAVY2 if r % 2 else RGBColor(0x0C, 0x1A, 0x2C))
            tc = cell._tc
            tc_pr = tc.get_or_add_tcPr()
            for edge in ("lnL", "lnR", "lnT", "lnB"):
                line = tc_pr.find(qn(f"a:{edge}"))
                if line is not None:
                    tc_pr.remove(line)
    add_text(
        slide,
        "Median frontier / Drex latency on 100 matched pairs:  3.78.    Above 1 means Drex returned first.    Neither model cleared a HOLD.",
        Inches(0.55),
        Inches(5.15),
        Inches(12.2),
        Inches(0.7),
        18,
        CLEAR,
    )
    add_text(
        slide,
        "Development pilot, n=5: Drex 5/5. Frontier 4/5, one false clearance on DEV-12. That pilot is not this score.",
        Inches(0.55),
        Inches(5.85),
        Inches(12.2),
        Inches(0.55),
        16,
        MUTED,
    )
    notes(
        slide,
        "Read the table left to right. Drex was faster and cheaper. The frontier model was more accurate and cleared fewer shipments that should have been reviewed. "
        "No HOLD was cleared by either model. This is one 100-case sample of one fictional rulebook.",
    )

    slide = new_slide(prs, total)
    add_text(slide, "How to read the misses", Inches(0.55), Inches(0.32), Inches(10), Inches(0.55), 32, CREAM, True)
    misses = [
        ("Drex", "89 correct", "Cleared 8 shipments whose label was REVIEW, and called 3 CLEAR shipments REVIEW. It did not clear a HOLD."),
        ("Frontier", "95 correct", "Cleared 3 REVIEW shipments, and called 2 CLEAR shipments REVIEW. It did not clear a HOLD."),
        ("Document", "One packet", "TST-056 cost $0.1200 to parse, extract, and ground. 7 of 8 fields matched. Drex’s verdict on that extraction matched the oracle."),
    ]
    left = 0.45
    accents = [AMBER, CLEAR, HOLD]
    for (title, kicker, body), accent in zip(misses, accents):
        card(slide, Inches(left), Inches(1.25), Inches(4.0), Inches(4.55), NAVY2)
        add_text(slide, title, Inches(left + 0.25), Inches(1.5), Inches(3.5), Inches(0.45), 16, accent, True)
        add_text(slide, kicker, Inches(left + 0.25), Inches(2.05), Inches(3.5), Inches(0.7), 28, CREAM, True)
        add_text(slide, body, Inches(left + 0.25), Inches(2.95), Inches(3.5), Inches(2.4), 18, INK)
        left += 4.2
    notes(
        slide,
        "False clearance means the model said CLEAR when the oracle said HOLD or REVIEW. "
        "The single document packet is a separate check from the 100-case decision score. "
        "Do not describe the 34-case hard set as a measured model score.",
    )

    slide = new_slide(prs, total)
    add_text(slide, "See it", Inches(0.55), Inches(0.32), Inches(8), Inches(0.55), 32, CREAM, True)
    steps = [
        "Locally, install requirements.txt with Python 3.12 and open http://127.0.0.1:8000/.",
        "The page replays the saved 100-case run. Loading it does not call either provider.",
        "The Inspect button says MOCK unless the server has APP_MODE=live and both keys.",
        "A live click calls each provider once. A missing key is refused, and a mock is not substituted.",
        "Keys stay in the server environment. They are not in the repository and not in the browser.",
    ]
    add_bullets(slide, steps, Inches(0.6), Inches(1.15), Inches(12.1), Inches(3.3), 20, INK, 10)
    card(slide, Inches(0.55), Inches(4.85), Inches(12.2), Inches(1.85), NAVY2)
    add_text(slide, "SOURCE  ·  PUBLIC DASHBOARD", Inches(0.85), Inches(5.02), Inches(8), Inches(0.28), 13, AMBER, True)
    add_text(slide, GITHUB, Inches(0.85), Inches(5.32), Inches(11.5), Inches(0.34), 18, CREAM, True)
    add_text(slide, SITE + "   ·   mock mode, no API spend on load", Inches(0.85), Inches(5.72), Inches(11.5), Inches(0.4), 18, INK)
    notes(slide, "Close by opening the dashboard. Show a saved shipment, then point at the mock label before anyone clicks Inspect.")
    return prs


def main() -> None:
    deck = build()
    deck.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()

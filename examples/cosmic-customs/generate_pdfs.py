"""Three-page cargo packets. Artwork falls back to generated geometry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw
from reportlab.lib.colors import Color, white
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Image as FlowImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table

from config import ROOT

NAVY = Color(0.05, 0.09, 0.16)
AMBER = Color(0.89, 0.64, 0.23)
CREAM = Color(0.965, 0.945, 0.890)
INK = Color(0.12, 0.1, 0.08)

GENERATED = ROOT / "assets" / "generated"
FIREFLY = ROOT / "assets" / "adobe_firefly"
PDF_DIR = ROOT / "data" / "pdfs"
PREVIEW_DIR = PDF_DIR / "previews"

PROMPTS = {
    "specimen_01.png": "Scientific illustration of a small luminous moth sealed in a customs specimen tray, cream paper background, no text.",
    "specimen_02.png": "Scientific illustration of a glass-green newt in a round specimen dish, cream paper background, no text.",
    "specimen_03.png": "Scientific illustration of a paper-colored squid in a brass specimen ring, cream paper background, no text.",
    "containment_seal.png": "Circular mechanical containment seal, navy and amber ink, engraved rings, no letters.",
    "customs_stamp.png": "Worn circular orbital customs stamp, amber ink on transparent background, no readable verdict.",
}


def draw_specimen(path: Path, kind: int) -> None:
    image = Image.new("RGB", (640, 480), (246, 240, 226))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((24, 24, 616, 456), radius=28, outline=(20, 32, 48), width=6)
    pen.ellipse((180, 70, 460, 350), outline=(196, 122, 42), width=8)
    if kind == 0:
        pen.polygon([(320, 110), (250, 220), (390, 220)], outline=(20, 32, 48))
        pen.ellipse((280, 190, 360, 270), fill=(232, 176, 74))
    elif kind == 1:
        pen.ellipse((230, 180, 420, 300), outline=(36, 92, 78), width=6)
        pen.line((230, 230, 160, 200), fill=(36, 92, 78), width=6)
    else:
        pen.arc((220, 140, 430, 340), 20, 220, fill=(20, 32, 48), width=6)
        pen.line((250, 300, 210, 390), fill=(20, 32, 48), width=5)
        pen.line((380, 300, 430, 390), fill=(20, 32, 48), width=5)
    image.save(path)


def draw_seal(path: Path) -> None:
    image = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.ellipse((20, 20, 460, 460), outline=(20, 32, 48, 255), width=14)
    pen.ellipse((70, 70, 410, 410), outline=(196, 122, 42, 255), width=8)
    pen.ellipse((150, 150, 330, 330), outline=(20, 32, 48, 255), width=6)
    image.save(path)


def draw_stamp(path: Path) -> None:
    image = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.ellipse((16, 16, 464, 464), outline=(176, 92, 28, 255), width=18)
    pen.ellipse((58, 58, 422, 422), outline=(176, 92, 28, 255), width=4)
    pen.regular_polygon((240, 240, 90), 6, rotation=20, outline=(176, 92, 28, 255), width=6)
    image.save(path)


def ensure_artwork() -> dict[str, str]:
    GENERATED.mkdir(parents=True, exist_ok=True)
    FIREFLY.mkdir(parents=True, exist_ok=True)
    specimens = [GENERATED / f"specimen_0{index}.png" for index in (1, 2, 3)]
    for index, path in enumerate(specimens):
        if not path.exists():
            draw_specimen(path, index)
    seal = GENERATED / "containment_seal.png"
    stamp = GENERATED / "customs_stamp.png"
    if not seal.exists():
        draw_seal(seal)
    if not stamp.exists():
        draw_stamp(stamp)
    used = {}
    for name in ("specimen_01.png", "specimen_02.png", "specimen_03.png", "containment_seal.png", "customs_stamp.png"):
        firefly = FIREFLY / name
        used[name] = "adobe_firefly" if firefly.exists() else "pillow_geometric_fallback"
    manifest = {
        "adobe_assets_created": any(value == "adobe_firefly" for value in used.values()),
        "files": [
            {"file": name, "prompt": PROMPTS[name], "used_source": used[name]}
            for name in used
        ],
        "note": "Geometric files are generated in this repository. Claim Adobe Firefly only for files placed in assets/adobe_firefly.",
    }
    (FIREFLY / "asset_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (FIREFLY / "README.md").write_text(
        "# Specimen artwork\n\n"
        "Optional PNG files in this folder replace the generated geometric fallbacks:\n\n"
        + "\n".join(f"- `{name}` — {prompt}" for name, prompt in PROMPTS.items())
        + "\n\nNo file in this folder was created with Adobe Firefly unless `asset_manifest.json` says `adobe_firefly` for that file.\n",
        encoding="utf-8",
    )
    return used


def artwork_for(token: str, used: dict[str, str]) -> tuple[Path, Path, Path]:
    index = int(token) % 3
    specimen_name = f"specimen_0{index + 1}.png"
    specimen = FIREFLY / specimen_name if used[specimen_name] == "adobe_firefly" else GENERATED / specimen_name
    seal_name = "containment_seal.png"
    stamp_name = "customs_stamp.png"
    seal = FIREFLY / seal_name if used[seal_name] == "adobe_firefly" else GENERATED / seal_name
    stamp = FIREFLY / stamp_name if used[stamp_name] == "adobe_firefly" else GENERATED / stamp_name
    return specimen, seal, stamp


def paint(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(CREAM)
    canvas.rect(0, 0, letter[0], letter[1], fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.rect(0, letter[1] - 42, letter[0], 42, fill=1, stroke=0)
    canvas.setFillColor(AMBER)
    canvas.rect(0, letter[1] - 46, letter[0], 4, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont("Times-Bold", 11)
    canvas.drawString(36, letter[1] - 26, "ORBITAL CUSTOMS  ·  CARGO PACKET")
    canvas.setFillColor(INK)
    canvas.setFont("Times-Roman", 8)
    canvas.drawString(36, 22, f"Page {doc.page} of 3  ·  synthetic training packet  ·  no outcome stamp")
    canvas.restoreState()


def xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def build_pdf(case: dict, used: dict[str, str]) -> Path:
    token = "0"
    for document in case["documents"]:
        if "Reference K" in document["text"]:
            token = document["text"].split("Reference K", 1)[1].split(".", 1)[0]
            break
    specimen, seal, stamp = artwork_for(token, used)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = PDF_DIR / f"{case['case_id']}.pdf"
    styles = ParagraphStyle("body", fontName="Times-Roman", fontSize=9, leading=12, textColor=INK, alignment=TA_LEFT)
    title = ParagraphStyle("title", fontName="Times-Bold", fontSize=13, leading=16, textColor=NAVY)
    story = []
    images = (specimen, seal, stamp)
    for index, document in enumerate(case["documents"]):
        story.append(Paragraph(document["kind"].replace("_", " ").title(), title))
        story.append(Spacer(1, 6))
        flow = FlowImage(str(images[index]), width=1.35 * inch, height=1.05 * inch)
        table = Table(
            [[flow, Paragraph(xml(document["text"]), styles)]],
            colWidths=[1.5 * inch, 5.3 * inch],
        )
        table.setStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("BOX", (0, 0), (-1, -1), 0.6, AMBER),
                ("BACKGROUND", (0, 0), (-1, -1), white),
            ]
        )
        story.append(table)
        if index < 2:
            story.append(PageBreak())
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.5 * inch,
        title="Cosmic Customs cargo packet",
        author="Cosmic Customs",
    )
    doc.build(story, onFirstPage=paint, onLaterPages=paint)
    inspect_pdf(path, case)
    return path


def inspect_pdf(path: Path, case: dict) -> None:
    document = pymupdf.open(path)
    try:
        if document.page_count != 3:
            raise RuntimeError(f"{path.name} has {document.page_count} pages")
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        combined = []
        for index, page in enumerate(document):
            text = page.get_text()
            combined.append(text)
            if len(text.strip()) < 80:
                raise RuntimeError(f"{path.name} page {index + 1} has too little text")
            pixmap = page.get_pixmap(dpi=110)
            pixmap.save(PREVIEW_DIR / f"{case['case_id']}-p{index + 1}.png")
        blob = "\n".join(combined).upper()
        for word in ("CLEAR", "HOLD", "REVIEW"):
            if word in blob:
                raise RuntimeError(f"{path.name} contains verdict word {word}")
        for source in case["documents"]:
            distinctive = [line for line in source["text"].splitlines() if "kPa" in line or "kg" in line or "Reference K" in line]
            for line in distinctive[:2]:
                if line not in "\n".join(combined):
                    raise RuntimeError(f"{path.name} dropped evidence line: {line}")
    finally:
        document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    used = ensure_artwork()
    showcase = json.loads((ROOT / "data" / "showcase.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in json.loads((ROOT / "data" / "test_100.json").read_text(encoding="utf-8"))}
    chosen = showcase["case_ids"][: args.count]
    for case_id in chosen:
        path = build_pdf(cases[case_id], used)
        print("wrote", path.name)
    print("artwork", json.dumps(used))


if __name__ == "__main__":
    main()

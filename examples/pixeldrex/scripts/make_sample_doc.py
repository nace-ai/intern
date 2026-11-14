"""Make docs/samples/coral_reef_field_notes.pdf: a 2-page, scanned-style (image) PDF for the NDI demo.

Each page states different facts, so the world's rules cite different pages. NDI has to OCR it.
"""

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"

PAGES = [
    (
        "Reef Station 7 — Field Notes, page 1",
        [
            "Coral cover. Living coral colonies grow slowly outward and settle onto bare rock next to them. "
            "A patch of bare rock surrounded by healthy coral is usually recolonised within a few seasons.",
            "Heat stress. During a marine heatwave the water temperature rises above the bleaching threshold. "
            "Warm water spreads quickly across the reef flat from one patch to the next.",
            "Bleaching. Healthy coral sitting in warm water often expels its symbiotic algae and turns white "
            "(bleached). Bleached coral is alive but starving.",
        ],
    ),
    (
        "Reef Station 7 — Field Notes, page 2",
        [
            "Recovery or death. If the warm water cools within a few weeks, bleached coral slowly recovers its colour. "
            "If the heat persists, bleached coral dies and becomes bare skeleton.",
            "Algae. Seaweed (macroalgae) grows fast over dead coral skeleton and bare rock, and can smother "
            "nearby bleached coral.",
            "Grazers. Parrotfish graze seaweed; where parrotfish are common, seaweed is cleared back to bare rock, "
            "which gives new coral room to settle. Heatwaves eventually pass and the water cools.",
        ],
    ),
]


def page(title: str, paras: list[str]) -> Image.Image:
    W, H = 1240, 1754  # A4 at 150 dpi
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    d.text((110, 120), title, font=ImageFont.truetype(BOLD, 34), fill=20)
    body = ImageFont.truetype(FONT, 27)
    y = 220
    for p in paras:
        for line in textwrap.wrap(p, 70):
            d.text((110, y), line, font=body, fill=30)
            y += 42
        y += 30
    img = img.rotate(0.4, fillcolor=250).filter(ImageFilter.GaussianBlur(0.6))  # a little "scanned"
    return img.convert("RGB")


def main() -> None:
    out = ROOT / "docs" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    pages = [page(t, ps) for t, ps in PAGES]
    path = out / "coral_reef_field_notes.pdf"
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)
    print(path)


if __name__ == "__main__":
    main()

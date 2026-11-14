"""The same sprites at 16, 32, 64 and 128 pixels: one 16x16 part layout, then one Drex smoothing pass per doubling."""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

from parts_test import SUBJECTS  # noqa: E402
from pixeldrex.drex import Drex  # noqa: E402
from pixeldrex.parts import paint_parts, smooth_upscale, tidy  # noqa: E402

RES = [16, 32, 64, 128]


async def ladder(drex: Drex, subject: str, parts) -> tuple[list, list]:
    s = await paint_parts(drex, subject, parts, n=16)
    out, times = [s], [0.0]
    while s.n < RES[-1]:
        t0 = time.perf_counter()
        s = await smooth_upscale(drex, s)
        out.append(s)
        times.append(time.perf_counter() - t0)
    return out, times


async def main() -> None:
    names = list(SUBJECTS)[:4]  # rocket, heart, ufo, cat face
    async with Drex() as drex:
        t0 = time.perf_counter()
        results = await asyncio.gather(*(ladder(drex, s, SUBJECTS[s]) for s in names))
        total = time.perf_counter() - t0
        calls = drex.usage.calls
    cell = 256
    sheet = Image.new("RGB", (len(RES) * (cell + 10) + 10, len(names) * (cell + 10) + 40), (24, 26, 40))
    d = ImageDraw.Draw(sheet)
    d.text(
        (10, 10),
        "columns: 16, 32, 64, 128 px  (one 16x16 part layout, then a Drex edge-smoothing pass per doubling)",
        fill=(255, 204, 51),
    )
    for r, (levels, _) in enumerate(results):
        for c, lv in enumerate(levels):
            img = tidy(lv, passes=0).image(shade=True, outline=True)
            base = Image.new("RGBA", img.size, (205, 210, 220, 255))
            base.alpha_composite(img)
            sheet.paste(
                base.convert("RGB").resize((cell, cell), Image.NEAREST), (10 + c * (cell + 10), 35 + r * (cell + 10))
            )
    out = ROOT / "out" / "parts"
    out.mkdir(parents=True, exist_ok=True)
    sheet.save(out / "res_ladder.png")
    per = [round(sum(t[i] for _, t in results) / len(results), 1) for i in range(len(RES))]
    print(f"{len(names)} sprites x 4 resolutions in {total:.1f}s, {calls} Drex calls; avg seconds per pass {per[1:]}")


if __name__ == "__main__":
    asyncio.run(main())

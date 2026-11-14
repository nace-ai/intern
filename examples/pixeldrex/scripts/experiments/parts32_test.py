"""16x16 vs 32x32 sprites from part questions, same subjects and parts."""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from pixeldrex.drex import Drex  # noqa: E402
from pixeldrex.parts import paint_parts, paint_parts_32, tidy  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parts_test import SUBJECTS  # noqa: E402


async def main() -> None:
    out = ROOT / "out" / "parts"
    out.mkdir(parents=True, exist_ok=True)
    async with Drex() as drex:
        t0 = time.perf_counter()
        s16 = await asyncio.gather(*(paint_parts(drex, s, p, n=16) for s, p in SUBJECTS.items()))
        t16 = time.perf_counter() - t0
        c16 = drex.usage.calls
        t0 = time.perf_counter()
        s32 = await asyncio.gather(*(paint_parts_32(drex, s, p) for s, p in SUBJECTS.items()))
        t32 = time.perf_counter() - t0
        c32 = drex.usage.calls - c16
    cell = 200
    sheet = Image.new("RGB", (len(SUBJECTS) * (cell + 10) + 10, 2 * (cell + 10) + 40), (24, 26, 40))
    d = ImageDraw.Draw(sheet)
    d.text(
        (10, 8),
        "top: 16x16 parts   bottom: 32x32 — Drex smooths every edge pixel from its neighbourhood",
        fill=(255, 204, 51),
    )
    for i, (a, b) in enumerate(zip(s16, s32)):
        x = 10 + i * (cell + 10)
        sheet.paste(tidy(a).flat(1).resize((cell, cell), Image.NEAREST), (x, 30))
        sheet.paste(tidy(b).flat(1).resize((cell, cell), Image.NEAREST), (x, 40 + cell))
    sheet.save(out / "compare_16_32_smooth.png")
    print(f"16x16: {c16} calls {t16:.1f}s | 32x32: {c32} calls {t32:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())

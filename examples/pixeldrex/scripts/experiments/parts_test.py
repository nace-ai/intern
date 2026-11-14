"""Compare sprites: current method (color per pixel, best of 6) vs parts per pixel + symmetry + shading."""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from pixeldrex.drex import Drex  # noqa: E402
from pixeldrex.parts import Part, paint_parts  # noqa: E402
from pixeldrex.pixels import best_painting, cleanup, trim  # noqa: E402

SUBJECTS = {
    "a rocket": [
        Part("nose cone", "the pointed tip at the very top"),
        Part("body", "the tall narrow tube in the middle"),
        Part("window", "a small round porthole on the upper body"),
        Part("fins", "small wings at the bottom left and right of the body"),
        Part("flame", "fire coming out below the bottom of the body"),
    ],
    "a heart": [
        Part("heart", "the heart shape: two round lobes at the top meeting in a point at the bottom"),
        Part("shine", "a small highlight spot on the upper part of the left lobe"),
    ],
    "a ufo": [
        Part("dome", "a glass dome on top in the center"),
        Part("saucer", "a wide flat disc across the middle"),
        Part("lights", "small dots along the rim of the saucer"),
        Part("beam", "a cone of light going down from under the saucer"),
    ],
    "a cat face": [
        Part("head", "the big round head"),
        Part("ears", "two pointed triangles on top of the head"),
        Part("eyes", "two eyes in the upper middle of the head"),
        Part("nose", "a small nose in the center"),
        Part("mouth", "the mouth just below the nose"),
    ],
    "a tree": [
        Part("leaves", "a big round canopy of leaves at the top"),
        Part("trunk", "the vertical trunk in the bottom middle"),
    ],
    "a house": [
        Part("roof", "a triangular roof on top"),
        Part("walls", "the square walls under the roof"),
        Part("door", "a door at the bottom center of the walls"),
        Part("windows", "two small windows on the walls left and right of the door"),
    ],
}


async def main() -> None:
    out = ROOT / "out" / "parts"
    out.mkdir(parents=True, exist_ok=True)
    async with Drex() as drex:
        t0 = time.perf_counter()
        new = await asyncio.gather(*(paint_parts(drex, s, p) for s, p in SUBJECTS.items()))
        t_new = time.perf_counter() - t0
        calls_new = drex.usage.calls
        t0 = time.perf_counter()
        old = await asyncio.gather(*(best_painting(drex, s, n=16, k=6, sprite=True) for s in SUBJECTS))
        t_old = time.perf_counter() - t0
    cell = 180
    sheet = Image.new("RGB", (len(SUBJECTS) * (cell + 10) + 10, 2 * (cell + 10) + 40), (24, 26, 40))
    d = ImageDraw.Draw(sheet)
    d.text(
        (10, 8),
        "top: current (color per pixel, best of 6)   bottom: parts + symmetry + shading (1 call)",
        fill=(255, 204, 51),
    )
    for i, (o, nw) in enumerate(zip(old, new)):
        x = 10 + i * (cell + 10)
        sheet.paste(trim(cleanup(o)).flat(1, bg=(205, 210, 220)).resize((cell, cell), Image.NEAREST), (x, 30))
        sheet.paste(nw.flat(1).resize((cell, cell), Image.NEAREST), (x, 40 + cell))
    sheet.save(out / "compare.png")
    print(f"parts: {len(SUBJECTS)} sprites, {calls_new} calls, {t_new:.1f}s | current: {t_old:.1f}s")
    for s in new:
        print(f"  {s.subject}: colors {s.colors}")


if __name__ == "__main__":
    asyncio.run(main())

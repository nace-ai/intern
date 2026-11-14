"""Large pixel image with Drex: 16x16 -> 32x32 -> 64x64 (-> 128x128), tile by tile.

Run: .venv/bin/python scripts/experiments/big.py "an orange fox face" --sizes 16 32 64 --name fox
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from pixeldrex.drex import Drex  # noqa: E402
from pixeldrex.pixels import pyramid  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("description")
    ap.add_argument("--sizes", type=int, nargs="+", default=[16, 32, 64])
    ap.add_argument("--name", default=None)
    ap.add_argument("--px", type=int, default=512, help="output width in screen pixels")
    args = ap.parse_args()

    out = ROOT / "out" / "big"
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or args.description.replace(" ", "_")[:40]
    async with Drex() as drex:
        t0 = time.perf_counter()
        levels = await pyramid(drex, args.description, sizes=tuple(args.sizes))
        dt = time.perf_counter() - t0

    final = levels[-1]
    final.flat(max(1, args.px // final.n)).save(out / f"{name}.png")
    final.flat(1).save(out / f"{name}_{final.n}px.png")  # true-size pixel art
    # progression strip: every level at the same display size
    s = 256
    strip = Image.new("RGB", (s * len(levels) + 8 * (len(levels) - 1), s), (200, 200, 200))
    for i, lv in enumerate(levels):
        strip.paste(lv.flat(s // lv.n), (i * (s + 8), 0))
    strip.save(out / f"{name}_levels.png")
    u = drex.usage.summary()
    print(
        f"{name}: sizes {args.sizes} in {dt:.1f}s, {u['calls']} Drex calls, {u['questions']} pixel decisions, "
        f"{u['input_tokens']} tokens, ~${u['cost_usd']:.4f}"
    )


if __name__ == "__main__":
    asyncio.run(main())

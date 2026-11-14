"""Reference-guided big pixel art: FLUX reference -> Drex paints 64x64 tile by tile. Compare with plain quantization."""

import asyncio, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from pixeldrex.drex import Drex
from pixeldrex.llm import DeepInfra
from pixeldrex.pixels import PALETTE, paint_tiled, cleanup


def quantize(img, n):
    small = img.convert("RGB").resize((n, n), Image.LANCZOS)
    pal = Image.new("P", (1, 1))
    flat = [v for rgb in PALETTE.values() for v in rgb]
    pal.putpalette(flat + flat[:3] * (256 - len(PALETTE)))
    return small.quantize(palette=pal, dither=Image.NONE).convert("RGB")


async def main(name, prompt, n=64):
    out = ROOT / "out" / "big"
    out.mkdir(parents=True, exist_ok=True)
    async with DeepInfra() as di, Drex() as drex:
        ref_path = out / f"{name}_ref.jpg"
        if not ref_path.exists():
            ref_path.write_bytes(
                await di.image(prompt + ", flat colors, simple bold shapes, centered, plain background")
            )
        ref = Image.open(ref_path).convert("RGB")
        t0 = time.perf_counter()
        p = cleanup(await paint_tiled(drex, prompt, ref, n, reference=True))
        dt = time.perf_counter() - t0
    s = 384
    strip = Image.new("RGB", (s * 3 + 16, s), (200, 200, 200))
    strip.paste(ref.resize((s, s)), (0, 0))
    strip.paste(quantize(ref, n).resize((s, s), Image.NEAREST), (s + 8, 0))
    strip.paste(p.flat(s // n), (2 * s + 16, 0))
    strip.save(out / f"{name}_refcmp.png")
    p.flat(8).save(out / f"{name}_drex{n}.png")
    u = drex.usage.summary()
    print(f"{name}: {n}x{n} in {dt:.1f}s, {u['calls']} calls, {u['questions']} decisions, ${u['cost_usd']:.4f}")


name, prompt = sys.argv[1], sys.argv[2]
asyncio.run(main(name, prompt))

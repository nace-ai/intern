"""Drex Pixels engine: Drex paints pixel art one `choice` decision per pixel, judges, and animates.

Pieces:
  paint()     -> one grid, every pixel = one Drex choice question (<=512 per request, chunks above)
  sample()    -> extra variations for free by sampling from the returned probabilities
  judge()     -> Drex Max looks at up to 8 candidates and picks the best one
  refine()    -> paint again at higher resolution with the coarse version as a guide image
  motion()    -> one Drex call decides every frame's x/y/size via `score` questions (expected values = smooth)
  scene_gif() -> background layer + sprite layers + Drex-decided motion -> animated GIF
"""

from __future__ import annotations

import asyncio
import io
import random
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from pixeldrex.drex import Drex, choice, image_to_b64, score

PALETTE: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (20, 20, 24),
    "gray": (140, 140, 150),
    "lightgray": (205, 208, 215),
    "red": (225, 40, 50),
    "pink": (250, 150, 180),
    "orange": (245, 140, 30),
    "yellow": (250, 210, 40),
    "brown": (130, 80, 40),
    "green": (40, 170, 80),
    "darkgreen": (20, 100, 50),
    "skyblue": (150, 205, 250),
    "blue": (40, 110, 230),
    "navy": (25, 35, 90),
    "purple": (140, 70, 200),
}
TRANSPARENT = "none"  # sprite background

Grid = list[list[str]]


@dataclass
class Painting:
    grid: Grid
    probs: list[list[dict[str, float]]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.grid)

    def image(self, scale: int = 1, bg: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
        n = self.n
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        for r in range(n):
            for c in range(n):
                name = self.grid[r][c]
                if name != TRANSPARENT:
                    img.putpixel((c, r), PALETTE[name] + (255,))
        if scale > 1:
            img = img.resize((n * scale, n * scale), Image.NEAREST)
        return img

    def flat(self, scale: int = 1, bg=(255, 255, 255)) -> Image.Image:
        im = self.image(scale)
        base = Image.new("RGBA", im.size, bg + (255,))
        base.alpha_composite(im)
        return base.convert("RGB")


def png_b64(img: Image.Image, upscale_to: int = 256) -> str:
    if img.width < upscale_to:
        k = max(1, upscale_to // img.width)
        img = img.resize((img.width * k, img.height * k), Image.NEAREST)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return image_to_b64(buf.getvalue(), max_side=upscale_to)


def _pixel_questions(n: int, colors: list[str]) -> dict[str, dict]:
    crit = {c: None for c in colors}
    return {
        f"p_{r:02d}_{c:02d}": choice(
            f"What color is the pixel at row {r}, column {c}? (row 0 is the top, column 0 is the left, "
            f"the grid is {n}x{n})",
            crit,
        )
        for r in range(n)
        for c in range(n)
    }


async def paint(
    drex: Drex,
    description: str,
    *,
    n: int = 16,
    colors: list[str] | None = None,
    sprite: bool = False,
    guide: Image.Image | None = None,
    model: str = "drex-max-v1.1",
) -> Painting:
    """Paint one n x n image. sprite=True adds a transparent 'none' color for the background."""
    colors = list(colors or PALETTE)
    if sprite and TRANSPARENT not in colors:
        colors = [TRANSPARENT] + colors
    state = (
        f"You are painting a {n}x{n} pixel-art image, like classic 8-bit sprite art. "
        f"Subject: {description}. Use bold, simple, recognizable shapes. "
    )
    if sprite:
        state += (
            f"This is a sprite: draw only the subject, centered and filling most of the grid, and answer "
            f"'{TRANSPARENT}' for every background pixel. "
        )
    images = None
    if guide is not None:
        state += (
            "Image 1 is a rough low-resolution sketch of the same picture: keep its layout and colors, "
            "and add detail and cleaner edges."
        )
        images = [png_b64(guide)]
    answers = await drex.ask_many(state, _pixel_questions(n, colors), images=images, model=model)
    grid = [[answers[f"p_{r:02d}_{c:02d}"]["choice"] for c in range(n)] for r in range(n)]
    probs = [[answers[f"p_{r:02d}_{c:02d}"]["probabilities"] for c in range(n)] for r in range(n)]
    return Painting(grid, probs)


async def paint_background(
    drex: Drex,
    description: str,
    *,
    n: int = 16,
    colors: list[str] | None = None,
    row_weight: float = 1.0,
    model: str = "drex-max-v1.1",
) -> Painting:
    """Paint a background in ONE request: n*n pixel questions + n 'main color of row r' questions.

    Each pixel's color = argmax of P_pixel(color) * P_row(color)^row_weight. Because Drex's probabilities are
    calibrated, the row answers fill in large areas that the pixel answers are unsure about (e.g. a floor)."""
    colors = list(colors or PALETTE)
    crit = {c: None for c in colors}
    qs = _pixel_questions(n, colors)
    for r in range(n):
        qs[f"row_{r:02d}"] = choice(
            f"Which color covers most of row {r} of the picture? (row 0 is the top edge, row {n - 1} the bottom edge)",
            crit,
        )
    state = (
        f"You are painting a {n}x{n} pixel-art background, like classic 8-bit game scenery. "
        f"Scene: {description}. Use large flat areas of color."
    )
    r = await drex.ask(state, qs, model=model)
    grid, probs = [], []
    for row in range(n):
        prow = r.answers[f"row_{row:02d}"]["probabilities"]
        g_row, p_row = [], []
        for col in range(n):
            pp = r.answers[f"p_{row:02d}_{col:02d}"]["probabilities"]
            fused = {c: pp.get(c, 0.0) * (prow.get(c, 0.0) + 1e-6) ** row_weight for c in colors}
            z = sum(fused.values()) or 1.0
            fused = {c: v / z for c, v in fused.items()}
            g_row.append(max(fused, key=fused.get))
            p_row.append(fused)
        grid.append(g_row)
        probs.append(p_row)
    return Painting(grid, probs)


def cleanup(p: Painting, *, passes: int = 1, min_same: int = 2) -> Painting:
    """Remove stray pixels: a pixel with fewer than `min_same` same-colored 8-neighbours
    takes the most common neighbouring color."""
    grid = [row[:] for row in p.grid]
    n = len(grid)
    for _ in range(passes):
        new = [row[:] for row in grid]
        for r in range(n):
            for c in range(n):
                nb = [
                    grid[rr][cc]
                    for rr in range(r - 1, r + 2)
                    for cc in range(c - 1, c + 2)
                    if (rr, cc) != (r, c) and 0 <= rr < n and 0 <= cc < n
                ]
                if sum(x == grid[r][c] for x in nb) < min_same:
                    new[r][c] = max(set(nb), key=nb.count)
        grid = new
    return Painting(grid, p.probs)


def _where(r0: int, c0: int, t: int, n: int) -> str:
    v = ["top", "upper-middle", "lower-middle", "bottom"][min(3, int((r0 + t / 2) / n * 4))]
    h = ["left", "center-left", "center-right", "right"][min(3, int((c0 + t / 2) / n * 4))]
    return f"{v} {h}"


async def paint_tiled(
    drex: Drex,
    description: str,
    guide: "Painting | Image.Image",
    n: int,
    *,
    tile: int = 16,
    colors: list[str] | None = None,
    model: str = "drex-max-v1.1",
    reference: bool = False,
) -> Painting:
    """Paint an n x n image tile by tile. Each tile sees a zoomed crop of `guide` (Image 1)
    and the whole guide with the tile outlined (Image 2). One Drex call per tile, run in parallel."""
    from PIL import ImageDraw

    colors = list(colors or PALETTE)
    crit = {c: None for c in colors}
    g = guide.flat() if isinstance(guide, Painting) else guide.convert("RGB")
    gscale = g.width / n  # guide pixels per target pixel

    async def one(r0: int, c0: int) -> tuple[int, int, dict]:
        box = (round(c0 * gscale), round(r0 * gscale), round((c0 + tile) * gscale), round((r0 + tile) * gscale))
        resample = Image.LANCZOS if reference else Image.NEAREST
        crop = g.crop(box).resize((256, 256), resample)
        whole = g.resize((256, 256), resample)
        k = 256 / g.width
        ImageDraw.Draw(whole).rectangle(
            [box[0] * k, box[1] * k, box[2] * k - 1, box[3] * k - 1], outline=(255, 0, 0), width=4
        )
        state = (
            f"You are painting a detailed {n}x{n} pixel-art image of: {description}. "
            f"This request paints ONE {tile}x{tile} tile of it: rows {r0}-{r0 + tile - 1} and columns "
            f"{c0}-{c0 + tile - 1} of the full image (the {_where(r0, c0, tile, n)} part). "
            + (
                "Image 1 is a zoom of exactly this tile from the reference picture. Pixel-art it: each pixel "
                "takes the palette color that best matches that spot of Image 1, with clean flat color areas. "
                "Image 2 is the whole reference picture with this tile outlined in red."
                if reference
                else "Image 1 is a blurry low-resolution zoom of exactly this tile: keep its layout and colors, "
                "but make the edges crisp and add fine detail that fits the subject. "
                "Image 2 is the whole low-resolution picture with this tile outlined in red."
            )
        )
        qs = {
            f"p_{r:02d}_{c:02d}": choice(
                f"In this tile, what color is the pixel at tile row {r}, tile column {c}? "
                f"(tile row 0 is the tile's top edge, tile column 0 its left edge, the tile is {tile}x{tile})",
                crit,
            )
            for r in range(tile)
            for c in range(tile)
        }
        res = await drex.ask(state, qs, images=[png_b64(crop), png_b64(whole)], model=model)
        return r0, c0, res.answers

    results = await asyncio.gather(*(one(r0, c0) for r0 in range(0, n, tile) for c0 in range(0, n, tile)))
    grid = [["white"] * n for _ in range(n)]
    probs = [[{} for _ in range(n)] for _ in range(n)]
    for r0, c0, ans in results:
        for r in range(tile):
            for c in range(tile):
                a = ans[f"p_{r:02d}_{c:02d}"]
                grid[r0 + r][c0 + c] = a["choice"]
                probs[r0 + r][c0 + c] = a["probabilities"]
    return Painting(grid, probs)


async def pyramid(
    drex: Drex, description: str, *, sizes: tuple[int, ...] = (16, 32, 64), clean: bool = True, **kw
) -> list[Painting]:
    """Coarse-to-fine: paint the first size whole, then each larger size tile by tile from the previous."""
    levels = [await paint(drex, description, n=sizes[0], **kw)]
    if clean:
        levels[0] = cleanup(levels[0])
    for n in sizes[1:]:
        nxt = await paint_tiled(drex, description, levels[-1], n, **kw)
        levels.append(cleanup(nxt) if clean else nxt)
    return levels


def sample(p: Painting, k: int, *, temperature: float = 1.0, seed: int = 0) -> list[Painting]:
    """k variations of a painting, drawn from Drex's per-pixel probabilities (no extra API calls)."""
    rnd = random.Random(seed)
    out = []
    for _ in range(k):
        grid = []
        for row in p.probs:
            new_row = []
            for dist in row:
                names = list(dist)
                w = [max(dist[x], 1e-9) ** (1 / temperature) for x in names]
                new_row.append(rnd.choices(names, weights=w)[0])
            grid.append(new_row)
        out.append(Painting(grid, p.probs))
    return out


async def judge(drex: Drex, description: str, candidates: list[Painting]) -> tuple[int, dict[str, float]]:
    """Drex Max looks at up to 8 candidates and picks the one that best shows the description."""
    cands = candidates[:8]
    labels = {f"image_{i + 1}": f"Image {i + 1}" for i in range(len(cands))}
    r = await drex.ask(
        f"These are {len(cands)} candidate pixel-art drawings of: {description}.",
        {"best": choice("Which image is the clearest, most recognizable drawing of the subject?", labels)},
        images=[png_b64(c.flat(bg=(205, 210, 220))) for c in cands],  # gray, so white subjects show
    )
    best = r.pick("best")
    return int(best.split("_")[1]) - 1, r.probs("best")


async def refine(drex: Drex, description: str, coarse: Painting, n: int, **kw) -> Painting:
    """Paint at n x n using the (upscaled) coarse painting as the guide image."""
    return await paint(drex, description, n=n, guide=coarse.flat(), **kw)


SPRITE_STYLES = [
    "{d}",
    "{d}, drawn as a simple bold icon with a thick silhouette",
    "{d}, as a cute retro video-game character sprite, viewed from the side",
]


async def best_painting(
    drex: Drex, description: str, *, n: int = 16, k: int = 6, styles: list[str] | None = None, **kw
) -> Painting:
    """Paint the subject in several phrasings, add low-temperature samples from Drex's own probabilities,
    clean stray pixels from every candidate, and let Drex Max judge the best one."""
    styles = styles or SPRITE_STYLES
    bases = await asyncio.gather(*(paint(drex, s.format(d=description), n=n, **kw) for s in styles))
    cands = [cleanup(b) for b in bases]
    per = max(0, (k - len(cands)) // len(bases))
    for i, b in enumerate(bases):
        cands += [cleanup(s) for s in sample(b, per, temperature=0.35, seed=i)]
    cands = [c for c in cands if any(x != TRANSPARENT for row in c.grid for x in row)] or cands
    idx, _ = await judge(drex, description, cands[:8])
    return cands[idx]


def trim(p: Painting) -> Painting:
    """Crop transparent margins and re-center on a square grid, so size decisions mean what they say."""
    rows = [r for r in range(p.n) if any(x != TRANSPARENT for x in p.grid[r])]
    cols = [c for c in range(p.n) if any(p.grid[r][c] != TRANSPARENT for r in range(p.n))]
    if not rows:
        return p
    r0, r1, c0, c1 = rows[0], rows[-1], cols[0], cols[-1]
    side = max(r1 - r0, c1 - c0) + 1
    grid = [[TRANSPARENT] * side for _ in range(side)]
    dr, dc = (side - (r1 - r0 + 1)) // 2, (side - (c1 - c0 + 1)) // 2
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            grid[r - r0 + dr][c - c0 + dc] = p.grid[r][c]
    return Painting(grid)


def smooth(values: list[float], passes: int = 1) -> list[float]:
    """Light 3-tap smoothing of a motion path (keeps the endpoints)."""
    v = list(values)
    for _ in range(passes):
        v = [v[0]] + [(v[i - 1] + 2 * v[i] + v[i + 1]) / 4 for i in range(1, len(v) - 1)] + [v[-1]]
    return v


# ---------------------------------------------------------------- motion
@dataclass
class Track:
    xs: list[float]  # sprite center, 0..W-1
    ys: list[float]
    sizes: list[float]  # sprite size in canvas pixels
    tilts: list[float] = field(default_factory=list)  # degrees, + = leaning right (clockwise)
    poses: list[int] = field(default_factory=list)  # which pose is shown in each frame (PoseSprite)
    squash: list[float] = field(default_factory=list)  # -1 stretched tall .. 0 normal .. +1 squashed flat


async def motion(
    drex: Drex,
    scene: str,
    sprite_name: str,
    motion_desc: str,
    *,
    frames: int,
    canvas: int,
    steps: int = 11,
    keep_inside: bool = True,
    poses: list[dict] | None = None,
) -> Track:
    """One Drex call decides the sprite's position and size in every frame (score = expected value)."""
    pos_levels = [f"{round(i * (canvas - 1) / (steps - 1))}" for i in range(steps)]
    x_levels = [
        f"column {v} ({'far left' if i == 0 else 'far right' if i == steps - 1 else 'between'})"
        for i, v in enumerate(pos_levels)
    ]
    y_levels = [
        f"row {v} ({'top' if i == 0 else 'bottom' if i == steps - 1 else 'between'})" for i, v in enumerate(pos_levels)
    ]
    size_levels = [
        "tiny (about 1/5 of the canvas width)",
        "small (about 1/3 of the canvas width)",
        "medium (about half the canvas width)",
        "large (about 2/3 of the canvas width)",
        "huge (almost the whole canvas width)",
    ]
    tilt_levels = [
        "leaning far to the left",
        "leaning a little to the left",
        "upright",
        "leaning a little to the right",
        "leaning far to the right",
    ]
    squash_levels = [
        "stretched tall and thin (moving fast)",
        "slightly stretched",
        "normal shape",
        "slightly squashed",
        "squashed flat and wide (landing or impact)",
    ]
    qs: dict[str, dict] = {}
    for f in range(frames):
        when = f"in frame {f + 1} of {frames} ({f / max(frames - 1, 1):.0%} through the animation)"
        qs[f"x_{f:02d}"] = score(f"Where is the center of the {sprite_name} horizontally {when}?", x_levels)
        qs[f"y_{f:02d}"] = score(f"Where is the center of the {sprite_name} vertically {when}?", y_levels)
        qs[f"s_{f:02d}"] = score(f"How big is the {sprite_name} {when}?", size_levels)
        qs[f"t_{f:02d}"] = score(f"How is the {sprite_name} tilted {when}?", tilt_levels)
        qs[f"q_{f:02d}"] = score(f"What is the {sprite_name}'s shape {when}?", squash_levels)
        if poses:
            qs[f"p_{f:02d}"] = choice(
                f"Which pose is the {sprite_name} in {when}?",
                {f"pose_{i}": str(p.get("name") or f"pose {i + 1}") for i, p in enumerate(poses)},
            )
    state = (
        f"An animated {canvas}x{canvas} pixel-art scene: {scene}. The {sprite_name} moves like this: "
        f"{motion_desc}. Row 0 is the top of the canvas and column 0 is the left edge. Animate it like a "
        f"cartoon animator: it leans into the direction it moves, stretches when moving fast, squashes when "
        f"it lands, hits something or pushes off, and stays upright and normal when it is still."
    )
    r = await drex.ask(state, qs)
    to_px = (canvas - 1) / (steps - 1)
    size_px = [0.2, 0.35, 0.5, 0.65, 0.85]  # fraction of canvas
    xs = [r.score(f"x_{f:02d}") * to_px for f in range(frames)]
    ys = [r.score(f"y_{f:02d}") * to_px for f in range(frames)]

    def size_at(f: int) -> float:
        e = r.score(f"s_{f:02d}")
        lo = int(e)
        hi = min(lo + 1, len(size_px) - 1)
        return canvas * (size_px[lo] + (size_px[hi] - size_px[lo]) * (e - lo))

    sizes = smooth([size_at(f) for f in range(frames)])
    xs, ys = smooth(xs), smooth(ys)
    if keep_inside:  # keep at least ~90% of the sprite on screen
        xs = [min(max(x, s * 0.4), canvas - 1 - s * 0.4) for x, s in zip(xs, sizes)]
        ys = [min(max(y, s * 0.4), canvas - 1 - s * 0.4) for y, s in zip(ys, sizes)]
    # expected values: tilt 0..4 -> -24..+24 degrees; squash 0..4 -> -1..+1 (light smoothing keeps it fluid)
    tilts = smooth([(r.score(f"t_{f:02d}") - 2) * 12 for f in range(frames)])
    squash = smooth([(r.score(f"q_{f:02d}") - 2) / 2 for f in range(frames)])
    pose_seq = [int(r.pick(f"p_{f:02d}").split("_")[1]) for f in range(frames)] if poses else []
    return Track(xs, ys, sizes, tilts=tilts, squash=squash, poses=pose_seq)


def _outlined(img: Image.Image, color=(20, 20, 24)) -> Image.Image:
    """Add a 1-pixel dark outline around the opaque part of an RGBA sprite."""
    from PIL import ImageFilter

    alpha = img.getchannel("A").point(lambda a: 255 if a > 0 else 0)
    ring = alpha.filter(ImageFilter.MaxFilter(3))
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", img.size, color + (255,)), (0, 0), ring)
    out.alpha_composite(img)
    return out


def compose(
    background: Painting,
    sprites: list[tuple[Painting, Track]],
    frames: int,
    scale: int,
    *,
    canvas: int | None = None,
    outline: bool = True,
) -> list[Image.Image]:
    canvas = canvas or background.n
    bg = shade_background(background).resize((canvas, canvas), Image.NEAREST)
    out = []
    for f in range(frames):
        frame = bg.convert("RGBA")
        for sp, tr in sprites:
            size = max(2, round(tr.sizes[f]))
            if hasattr(sp, "poses"):
                src = sp.native(tr.poses[f] if tr.poses else 0)
            else:
                src = sp.native() if hasattr(sp, "native") else sp.image()
            q = tr.squash[f] if tr.squash else 0.0  # squash & stretch, volume roughly kept
            w = max(2, round(size * (1 + 0.22 * q)))
            h = max(2, round(size * (1 - 0.22 * q)))
            img = src.resize((w, h), Image.NEAREST)
            tilt = tr.tilts[f] if tr.tilts else 0.0
            if abs(tilt) >= 2:
                img = img.rotate(-tilt, resample=Image.NEAREST, expand=True)
            pad = 1 if outline else 0
            if outline:
                img = _outlined(img.crop((-pad, -pad, img.width + pad, img.height + pad)))
            x = round(tr.xs[f] - img.width / 2)
            y = round(tr.ys[f] + size / 2 - img.height + (pad if not outline else 0))  # keep the base planted
            layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            layer.paste(img, (x, y), img)
            frame.alpha_composite(layer)
        out.append(frame.convert("RGB").resize((canvas * scale, canvas * scale), Image.NEAREST))
    return out


def shade_background(p: Painting) -> Image.Image:
    """Pixel-art bevel for scenery: the top edge of each color area is lit, the bottom edge shadowed."""
    n = p.n
    img = Image.new("RGB", (n, n))
    for r in range(n):
        for c in range(n):
            name = p.grid[r][c]
            rgb = PALETTE.get(name, (255, 255, 255))
            if r > 0 and p.grid[r - 1][c] != name:
                rgb = tuple(int(v + (255 - v) * 0.3) for v in rgb)
            elif r < n - 1 and p.grid[r + 1][c] != name:
                rgb = tuple(int(v * 0.8) for v in rgb)
            img.putpixel((c, r), rgb)
    return img


def save_gif(frames: list[Image.Image], path, ms: int = 90) -> None:
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=False)


@dataclass
class SpriteSpec:
    name: str
    look: str
    motion: str
    parts: list[dict] = field(default_factory=list)  # [{"name": ..., "where": ...}] -> painted part by part
    poses: list[dict] = field(default_factory=list)  # [{"name": "wings up", "parts": [...]}, ...] -> changes shape
    symmetric: bool = True


async def scene_gif(
    drex: Drex,
    *,
    scene: str,
    background: str,
    sprites: list[SpriteSpec],
    frames: int = 16,
    canvas: int = 64,
    sprite_px: int = 32,
    k: int = 6,
    bg_mode: str = "flat",  # "flat": 16x16 cleaned, scaled up  |  "refine": repainted at canvas size
    outline: bool = True,
    row_weight: float = 0.5,
    on_event: "Callable[[str, dict], None] | None" = None,
) -> tuple[list[Image.Image], dict]:
    """Background painted once, sprites painted once (best of k, cleaned, trimmed), Drex decides motion.

    on_event(kind, payload) is called as each part finishes: "background", "sprite", "motion", "done".
    """
    emit = on_event or (lambda kind, payload: None)

    async def tagged(kind: str, payload: dict, coro):
        result = await coro
        emit(kind, {**payload, "result": result})
        return result

    bg_desc = (
        f"background scenery only, with no characters or objects in front: {background}. Use large flat areas of color."
    )
    bg_task = tagged("background", {}, paint_background(drex, bg_desc, n=16, row_weight=row_weight))
    from pixeldrex.parts import (
        Part,
        PartsSprite,
        PoseSprite,
        paint_parts,
        paint_parts_res,
        smooth_upscale,
        tidy,
        tidy_group,
    )

    def to_parts(items: list[dict]) -> list[Part]:
        return [
            Part(str(p.get("name", "part"))[:30], str(p.get("where") or p.get("description") or ""))
            for p in items[:6]
            if isinstance(p, dict)
        ]

    async def pose_painting(s: SpriteSpec):
        """Sprites that change shape: paint every pose with the first pose's colors, then share one frame."""
        subject = f"{s.name} ({s.look})"
        poses = [p for p in s.poses[:4] if to_parts(p.get("parts") or [])]
        res = max(sprite_px, 16)
        first = await paint_parts_res(drex, subject, to_parts(poses[0]["parts"]), res=res, symmetric=s.symmetric)
        rest = await asyncio.gather(
            *(
                paint_parts_res(
                    drex,
                    f"{subject}, pose: {p.get('name', '')}",
                    to_parts(p["parts"]),
                    res=res,
                    symmetric=s.symmetric,
                    colors=first.colors,
                )
                for p in poses[1:]
            )
        )
        return PoseSprite(tidy_group([first, *rest]), [str(p.get("name", "")) for p in poses])

    async def sprite_painting(s: SpriteSpec):
        if len(s.poses) >= 2:
            return await pose_painting(s)
        if s.parts:
            parts = [
                Part(str(p.get("name", "part"))[:30], str(p.get("where") or p.get("description") or ""))
                for p in s.parts[:6]
            ]
            subject = f"{s.name} ({s.look})"

            async def once(symmetric: bool):
                if sprite_px >= 32:  # 16x16 shape, then one Drex edge-smoothing pass per doubling
                    return await paint_parts_res(drex, subject, parts, res=sprite_px, symmetric=symmetric)
                return await paint_parts(drex, subject, parts, n=sprite_px, symmetric=symmetric)

            def filled(sp) -> float:
                return sum(x != "empty" for row in sp.grid for x in row) / (sp.n * sp.n)

            sp = await once(s.symmetric)
            if filled(sp) < 0.12:  # nearly empty sprite: repaint once, as a symmetric front view
                sp = await once(True)
            return tidy(sp)
        return trim(await best_painting(drex, s.look, n=min(sprite_px, 16), k=k, sprite=True))

    sprite_tasks = [tagged("sprite", {"name": s.name}, sprite_painting(s)) for s in sprites]
    others = ", ".join(f"the {s.name} ({s.motion})" for s in sprites)
    motion_tasks = [
        tagged(
            "motion",
            {"name": s.name},
            motion(
                drex,
                f"{scene}; in it: {others}",
                s.name,
                s.motion,
                frames=frames,
                canvas=canvas,
                poses=s.poses if len(s.poses) >= 2 else None,
            ),
        )
        for s in sprites
    ]
    bg16, *rest = await asyncio.gather(bg_task, *sprite_tasks, *motion_tasks)
    bg16 = cleanup(bg16, passes=2)
    sprite_ps = list(rest[: len(sprites)])
    tracks = rest[len(sprites) :]
    if canvas >= 64:  # Drex smooths the background's edges too, up to half the canvas (rounded hills, no blocks)
        smooth = PartsSprite(background, bg16.grid, {})
        while smooth.n < canvas // 2:
            smooth = await smooth_upscale(drex, smooth, symmetric=False, clamp_edges=True)
        bg = Painting(smooth.grid)
    elif bg_mode == "flat":
        bg = bg16
    else:
        bg = cleanup(await refine(drex, bg_desc, bg16, n=canvas), passes=2)
    frames_out = compose(
        bg, list(zip(sprite_ps, tracks)), frames, scale=max(1, 512 // canvas), canvas=canvas, outline=outline
    )
    meta = {"background16": bg16, "background": bg, "sprites": sprite_ps, "tracks": tracks}
    emit("done", {"frames": len(frames_out)})
    return frames_out, meta

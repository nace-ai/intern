"""Sprites from better questions: ask Drex which PART covers each pixel, not which color.

One Drex request per sprite:
  - for every pixel of the left half (mirrored for symmetric subjects):
      "Which part of the rocket covers the pixel at row 3, column 6? nose cone / body / window / fin / flame / empty"
  - for every part: "What color is the rocket's nose cone?" (palette choice)
Parts are semantic, so the model reasons about where a nose cone or a flame goes; each part gets one solid color,
then the renderer adds classic pixel-art shading (lit top-left edges, shadowed bottom-right edges) and an outline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from pixeldrex.drex import Drex, choice
from pixeldrex.pixels import PALETTE

EMPTY = "empty"


@dataclass
class Part:
    name: str
    description: str


@dataclass
class PartsSprite:
    subject: str
    grid: list[list[str]]  # part name or EMPTY per pixel
    colors: dict[str, str]  # part name -> palette color name
    probs: list[list[dict[str, float]]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.grid)

    def image(self, *, shade: bool = True, outline: bool = True) -> Image.Image:
        """Native-size RGBA sprite (n x n, or (n+2) x (n+2) with outline)."""
        n = self.n
        pad = 1 if outline else 0
        img = Image.new("RGBA", (n + 2 * pad, n + 2 * pad), (0, 0, 0, 0))

        def at(r: int, c: int) -> str:
            return self.grid[r][c] if 0 <= r < n and 0 <= c < n else EMPTY

        for r in range(n):
            for c in range(n):
                p = self.grid[r][c]
                if p == EMPTY:
                    continue
                rgb = PALETTE.get(self.colors.get(p, "gray"), (140, 140, 150))
                if shade:
                    lit = at(r - 1, c) != p or at(r, c - 1) != p
                    dark = at(r + 1, c) != p or at(r, c + 1) != p
                    if dark:
                        rgb = tuple(int(v * 0.72) for v in rgb)
                    elif lit:
                        rgb = tuple(int(v + (255 - v) * 0.35) for v in rgb)
                img.putpixel((c + pad, r + pad), rgb + (255,))
        if outline:
            ink = (20, 20, 28, 255)
            for r in range(-1, n + 1):
                for c in range(-1, n + 1):
                    if at(r, c) != EMPTY:
                        continue
                    if any(at(r + dr, c + dc) != EMPTY for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                        img.putpixel((c + pad, r + pad), ink)
        return img

    def native(self) -> Image.Image:
        """Shaded RGBA sprite without outline (the scene compositor adds its own outline)."""
        return self.image(shade=True, outline=False)

    def flat(self, scale: int = 10, bg=(205, 210, 220), **kw) -> Image.Image:
        im = self.image(**kw)
        base = Image.new("RGBA", im.size, bg + (255,))
        base.alpha_composite(im)
        return base.convert("RGB").resize((im.width * scale, im.height * scale), Image.NEAREST)


def tidy(s: PartsSprite, passes: int = 1) -> PartsSprite:
    """Remove stray pixels (fewer than 2 same-part 8-neighbours take the most common neighbour), then trim the
    empty margin and re-center on a square grid."""
    grid = [row[:] for row in s.grid]
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
                if sum(x == grid[r][c] for x in nb) < 2:
                    new[r][c] = max(sorted(set(nb)), key=nb.count)
        grid = new
    rows = [r for r in range(n) if any(x != EMPTY for x in grid[r])]
    cols = [c for c in range(n) if any(grid[r][c] != EMPTY for r in range(n))]
    if not rows:
        return PartsSprite(s.subject, grid, s.colors)
    r0, r1, c0, c1 = rows[0], rows[-1], cols[0], cols[-1]
    side = max(r1 - r0, c1 - c0) + 1
    out = [[EMPTY] * side for _ in range(side)]
    dr, dc = (side - (r1 - r0 + 1)) // 2, (side - (c1 - c0 + 1)) // 2
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            out[r - r0 + dr][c - c0 + dc] = grid[r][c]
    return PartsSprite(s.subject, out, s.colors)


async def paint_parts(
    drex: Drex,
    subject: str,
    parts: list[Part],
    *,
    n: int = 16,
    symmetric: bool = True,
    model: str = "drex-max-v1.1",
    guide: "PartsSprite | None" = None,
    colors: dict[str, str] | None = None,
) -> PartsSprite:
    """guide: a coarser painting of the same sprite (same framing). Drex sees it as Image 1 and keeps its shapes
    while adding smoother edges and finer detail; its part colors are reused, so no color questions are asked."""
    half = n // 2 if symmetric else n
    crit = {EMPTY: "background: no part of the subject is here"}
    crit |= {p.name: p.description for p in parts}
    where = f"Row 0 is the top edge and row {n - 1} the bottom edge. Column 0 is the far left edge" + (
        f" and column {half - 1} is just left of the vertical center line; the sprite is left-right "
        f"symmetric, so describe the left half."
        if symmetric
        else f" and column {n - 1} the far right edge."
    )
    qs: dict[str, dict] = {}
    for r in range(n):
        for c in range(half):
            qs[f"p_{r:02d}_{c:02d}"] = choice(
                f"Which part of the {subject} covers the pixel at row {r}, column {c}? {where}", crit
            )
    palette = {k: None for k in PALETTE}
    if guide is None and colors is None:
        for i, p in enumerate(parts):
            qs[f"color_{i}"] = choice(f"What color is the {subject}'s {p.name} ({p.description})?", palette)
    state = (
        f"A {n}x{n} pixel-art sprite of {subject}, like a classic 8-bit video game icon: centered, filling most "
        f"of the grid, bold and simple. It is made of these parts: "
        + "; ".join(f"{p.name}: {p.description}" for p in parts)
        + "."
    )
    images = None
    if guide is not None:
        from pixeldrex.pixels import png_b64

        legend = ", ".join(f"{p.name} = {guide.colors.get(p.name, '?')}" for p in parts)
        state += (
            f" Image 1 is a rough {guide.n}x{guide.n} version of exactly this sprite, in the same position "
            f"(colors: {legend}; the gray background is empty). Keep its layout, shapes and proportions, "
            f"and at {n}x{n} make the edges smoother and add small details."
        )
        images = [png_b64(guide.flat(1, bg=(205, 210, 220), shade=False, outline=False), upscale_to=512)]
    ans = await drex.ask_many(state, qs, model=model, images=images)
    grid, probs = [], []
    for r in range(n):
        row = [ans[f"p_{r:02d}_{c:02d}"]["choice"] for c in range(half)]
        prow = [ans[f"p_{r:02d}_{c:02d}"]["probabilities"] for c in range(half)]
        if symmetric:
            row = row + row[::-1]
            prow = prow + prow[::-1]
        grid.append(row)
        probs.append(prow)
    if colors is not None:  # reuse a sibling pose's colors so every pose matches
        colors = {p.name: colors.get(p.name, "gray") for p in parts}
    elif guide is not None:
        colors = {p.name: guide.colors.get(p.name, "gray") for p in parts}
    else:
        colors = {p.name: ans[f"color_{i}"]["choice"] for i, p in enumerate(parts)}
    return PartsSprite(subject, grid, colors, probs)


_DIRS8 = [
    ("north-west", -1, -1),
    ("north", -1, 0),
    ("north-east", -1, 1),
    ("west", 0, -1),
    ("east", 0, 1),
    ("south-west", 1, -1),
    ("south", 1, 0),
    ("south-east", 1, 1),
]


async def smooth_upscale(
    drex: Drex,
    s: PartsSprite,
    *,
    symmetric: bool = True,
    passes: int = 1,
    model: str = "drex-max-v1.1",
    clamp_edges: bool = False,
) -> PartsSprite:
    """Double the resolution, then let Drex decide every edge pixel from its local neighbourhood only:
    "this pixel is 'body'; north: nose cone, east: empty, ... which part should it be so the outline is smooth?"
    No coordinates are asked (Drex is good at local judgments, weak at exact positions). Identical neighbourhoods
    share one question, so a sprite needs about one request per pass."""
    n = s.n * 2
    grid = [[s.grid[r // 2][c // 2] for c in range(n)] for r in range(n)]
    cols = range(n // 2) if symmetric else range(n)

    def at(g, r, c):
        if 0 <= r < n and 0 <= c < n:
            return g[r][c]
        if clamp_edges:  # backgrounds: the world continues past the edge
            return g[min(max(r, 0), n - 1)][min(max(c, 0), n - 1)]
        return EMPTY

    for _ in range(passes):
        keys: dict[tuple, list[tuple[int, int]]] = {}
        for r in range(n):
            for c in cols:
                me = grid[r][c]
                nb = tuple(at(grid, r + dr, c + dc) for _, dr, dc in _DIRS8)
                if all(x == me for x in nb):
                    continue  # interior pixel: nothing to decide
                keys.setdefault((me, nb), []).append((r, c))
        if not keys:
            break
        qs = {}
        key_list = list(keys)
        for i, (me, nb) in enumerate(key_list):
            options = {me} | set(nb)
            crit = {o: ("background, nothing here" if o == EMPTY else f"part of the {o}") for o in sorted(options)}
            around = ", ".join(f"{d} {x}" for (d, _, _), x in zip(_DIRS8, nb))
            qs[f"k{i:04d}"] = choice(
                f"This pixel is currently '{me}'. Around it: {around}. To make every shape's outline smooth and "
                f"natural (rounded curves and clean diagonals, no jagged stair steps or stray pixels), which should "
                f"this pixel be?",
                crit,
            )
        state = (
            f"Smoothing a {n}x{n} pixel-art sprite of {s.subject} that was just enlarged from {s.n}x{s.n}, so "
            f"its edges are blocky 2x2 stair steps. Each question is about one edge pixel and its 8 neighbours."
        )
        ans = await drex.ask_many(state, qs, model=model)
        new = [row[:] for row in grid]
        for i, k in enumerate(key_list):
            pick = ans[f"k{i:04d}"]["choice"]
            for r, c in keys[k]:
                new[r][c] = pick
        grid = new
        if symmetric:
            for r in range(n):
                grid[r][n // 2 :] = grid[r][: n // 2][::-1]
    return PartsSprite(s.subject, grid, dict(s.colors))


async def paint_parts_32(drex: Drex, subject: str, parts: list[Part], *, symmetric: bool = True) -> PartsSprite:
    """32x32 sprite: a clean 16x16 layout (part questions), then Drex smooths every edge pixel at 32x32."""
    coarse = await paint_parts(drex, subject, parts, n=16, symmetric=symmetric)
    return await smooth_upscale(drex, coarse, symmetric=symmetric)


async def paint_parts_res(
    drex: Drex,
    subject: str,
    parts: list[Part],
    *,
    res: int = 32,
    symmetric: bool = True,
    colors: dict[str, str] | None = None,
) -> PartsSprite:
    """Any power-of-two resolution >= 16: the 16x16 part layout, then one Drex edge-smoothing pass per doubling
    (16 -> 32 -> 64 -> 128). Each pass only asks local questions, so edges get smoother and curves rounder;
    the detail itself comes from the 16x16 layout."""
    sprite = await paint_parts(drex, subject, parts, n=16, symmetric=symmetric, colors=colors)
    while sprite.n < res:
        sprite = await smooth_upscale(drex, sprite, symmetric=symmetric)
    return sprite


class PoseSprite:
    """A sprite that changes shape over time: several poses painted part by part with shared colors, all cropped
    to one shared frame so switching poses doesn't make the sprite jump. The scene compositor asks native(i)."""

    def __init__(self, poses: list[PartsSprite], names: list[str]):
        self.poses, self.names = poses, names

    @property
    def n(self) -> int:
        return self.poses[0].n

    def native(self, i: int = 0) -> Image.Image:
        return self.poses[min(max(i, 0), len(self.poses) - 1)].native()

    def flat(self, scale: int = 10, bg=(205, 210, 220), **kw) -> Image.Image:
        """All poses side by side (thumbnails and saved sprite sheets)."""
        imgs = [p.flat(scale, bg, **kw) for p in self.poses]
        out = Image.new("RGB", (sum(i.width for i in imgs), max(i.height for i in imgs)), bg)
        x = 0
        for im in imgs:
            out.paste(im, (x, 0))
            x += im.width
        return out


def tidy_group(sprites: list[PartsSprite]) -> list[PartsSprite]:
    """tidy() each pose, but crop all of them to the union of their extents so they share one frame."""

    def destray(s: PartsSprite) -> PartsSprite:
        g, n = s.grid, s.n
        new = [row[:] for row in g]
        for r in range(n):
            for c in range(n):
                nb = [
                    g[rr][cc]
                    for rr in range(r - 1, r + 2)
                    for cc in range(c - 1, c + 2)
                    if (rr, cc) != (r, c) and 0 <= rr < n and 0 <= cc < n
                ]
                if sum(x == g[r][c] for x in nb) < 2:
                    new[r][c] = max(sorted(set(nb)), key=nb.count)
        return PartsSprite(s.subject, new, s.colors)

    sprites = [destray(s) for s in sprites]
    n = sprites[0].n
    rows = [r for s in sprites for r in range(n) if any(x != EMPTY for x in s.grid[r])]
    cols = [c for s in sprites for c in range(n) if any(s.grid[r][c] != EMPTY for r in range(n))]
    if not rows:
        return sprites
    r0, r1, c0, c1 = min(rows), max(rows), min(cols), max(cols)
    side = max(r1 - r0, c1 - c0) + 1
    dr, dc = (side - (r1 - r0 + 1)) // 2, (side - (c1 - c0 + 1)) // 2
    out = []
    for s in sprites:
        grid = [[EMPTY] * side for _ in range(side)]
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r - r0 + dr][c - c0 + dc] = s.grid[r][c]
        out.append(PartsSprite(s.subject, grid, s.colors))
    return out

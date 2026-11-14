"""Drex Life: every pixel is an agent, the laws of physics are plain English, Drex decides every cell, every frame.

A world = a few cell states (each with a palette color) + rules in English + a starting layout.
  1. The director (DeepInfra LLM) turns an idea into a world spec. It never decides any cell.
  2. Drex paints the starting grid: one `choice` question per cell.
  3. Each step, every cell asks Drex "I am X and my 8 neighbours are ...; what do I become?"
     - Questions are asked once per UNIQUE neighbourhood (cells in the same situation share one answer),
       and answers are cached across steps, so the world gets faster as Drex learns its physics.
     - The next state is SAMPLED from Drex's calibrated probabilities, so "slowly regrows" really is slow.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from PIL import Image

from pixeldrex.drex import Drex, choice
from pixeldrex.llm import DeepInfra
from pixeldrex.pixels import PALETTE

EDGE = "edge"
DIRS = [
    ("north-west", -1, -1),
    ("north", -1, 0),
    ("north-east", -1, 1),
    ("west", 0, -1),
    ("east", 0, 1),
    ("south-west", 1, -1),
    ("south", 1, 0),
    ("south-east", 1, 1),
]


@dataclass
class CellState:
    name: str
    color: str
    meaning: str


@dataclass
class World:
    title: str
    states: list[CellState]
    rules: str
    start: str

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.states]

    def color(self, name: str) -> tuple[int, int, int]:
        for s in self.states:
            if s.name == name:
                return PALETTE.get(s.color, (255, 0, 255))
        return (255, 0, 255)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "states": [asdict(s) for s in self.states],
            "rules": self.rules,
            "start": self.start,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "World":
        states, seen = [], set()
        for s in d.get("states") or []:
            name = re.sub(r"[^a-z0-9 _-]", "", str(s.get("name", "")).lower()).strip()[:20]
            color = str(s.get("color", "")).lower().replace(" ", "")
            if not name or name in seen:
                continue
            seen.add(name)
            states.append(CellState(name, color if color in PALETTE else "gray", str(s.get("meaning", name))))
        if len(states) < 2:
            raise ValueError("a world needs at least 2 cell states")
        return cls(
            str(d.get("title") or "Untitled world")[:60],
            states[:6],
            str(d.get("rules") or ""),
            str(d.get("start") or "random"),
        )


DIRECTOR = f"""You design tiny cellular-automaton worlds, like Conway's Game of Life but with rules in plain English.
Each cell of a grid is in one of a few states. Every step, each cell changes based ONLY on its own state and its
8 neighbours. Turn the user's idea into a JSON world:
{{
  "title": "short title, max 5 words",
  "states": [{{"name": "one lowercase word", "color": "palette color", "meaning": "what this cell is"}}],
  "rules": "3-6 short rules, one per line, each saying what a cell in some state becomes depending on its neighbours, e.g. 'a tree with a burning neighbour catches fire'. Use words like often, rarely, slowly for randomness.",
  "start": "where each state is at the start, e.g. 'mostly trees, with a small fire in the center and a river of water down the left side'"
}}
Rules for you:
- 3 to 5 states. Each state gets a DIFFERENT color from this palette: {", ".join(PALETTE)}.
  The colors must look clearly different from each other (never two grays, never two greens, never two blues).
- Every state must be able to change into something, so the world keeps moving for many steps.
- Include a quiet/background state (like empty, grass, water) that covers most of the start.
- Rules must only mention a cell's own state and its neighbours (directions north/south/east/west are allowed).
- All cells update at the same time, so MOVEMENT always needs two rules: one for the cell being left
  ("sand with empty to the south becomes empty") and one for the cell being entered
  ("empty with sand to the north becomes sand"). Never write a movement rule without its partner.
- Avoid rules that spawn things at random everywhere; change should spread from neighbours.
- Write spreading and recovery as chances, not certainties ("often catches fire", "sometimes regrows"), so fronts
  look ragged and natural; and give every state a way back, so the world keeps cycling instead of ending.
- Colors must contrast strongly with the background state so the action is easy to see.
- Reply with the JSON object only."""


async def design_world(di: DeepInfra, idea: str, retries: int = 2) -> World:
    prompt = f"Idea: {idea}"
    err: Exception | None = None
    for _ in range(retries + 1):
        text = await di.chat(prompt, system=DIRECTOR, max_tokens=700, temperature=0.5, json_mode=True)
        try:
            m = re.search(r"\{.*\}", text, re.S)
            return World.from_dict(json.loads(m.group(0) if m else text))
        except (ValueError, json.JSONDecodeError, AttributeError) as e:
            err = e
            prompt = f"Idea: {idea}\n\nYour last reply was not a valid world ({e}). Reply with the JSON object only."
    raise RuntimeError(f"director failed to design a world: {err}")


def _criteria(world: World) -> dict[str, str]:
    return {s.name: s.meaning for s in world.states}


async def seed(drex: Drex, world: World, n: int) -> list[list[str]]:
    """Drex paints the starting layout: one choice question per cell."""
    qs = {
        f"c_{r:02d}_{c:02d}": choice(
            f"At the start, which state is the cell at row {r}, column {c}? "
            f"(row 0 is the top, column 0 the left, the grid is {n}x{n})",
            _criteria(world),
        )
        for r in range(n)
        for c in range(n)
    }
    names = ", ".join(world.names)
    state = (
        f"A {n}x{n} grid world: {world.title}. Starting layout: {world.start}. "
        f"Every state ({names}) appears somewhere at the start; the background state covers most cells, "
        f"but the others form visible patches, clusters or lines."
    )
    ans = await drex.ask_many(state, qs)
    grid = [[ans[f"c_{r:02d}_{c:02d}"]["choice"] for c in range(n)] for r in range(n)]
    counts: dict[str, int] = {}
    for row in grid:
        for x in row:
            counts[x] = counts.get(x, 0) + 1
    if max(counts.values()) > 0.97 * n * n:  # nearly empty world: nothing would happen, so reseed once
        state += (
            " IMPORTANT: at least a fifth of the grid must NOT be the background state; put clear clusters "
            "of the other states in different parts of the grid."
        )
        ans = await drex.ask_many(state, qs)
        grid = [[ans[f"c_{r:02d}_{c:02d}"]["choice"] for c in range(n)] for r in range(n)]
    return grid


def neighbourhood(grid: list[list[str]], r: int, c: int) -> tuple[str, ...]:
    n = len(grid)
    return tuple(grid[r + dr][c + dc] if 0 <= r + dr < n and 0 <= c + dc < n else EDGE for _, dr, dc in DIRS)


DIRECTION_WORDS = re.compile(r"\b(north|south|east|west|above|below|up|down|left|right|falls?|rises?|wind)\b", re.I)


def is_directional(world: World) -> bool:
    """Do the rules care which side a neighbour is on? If not, only neighbour counts matter."""
    return bool(DIRECTION_WORDS.search(world.rules))


def situation(grid: list[list[str]], r: int, c: int, directional: bool) -> tuple:
    """The key a cell's next state depends on. Count-based keys collapse many cells into one question."""
    nb = neighbourhood(grid, r, c)
    if directional:
        return (grid[r][c], nb)
    counts: dict[str, int] = {}
    for x in nb:
        counts[x] = counts.get(x, 0) + 1
    return (grid[r][c], tuple(sorted(counts.items())))


def _question(world: World, key: tuple, directional: bool) -> dict:
    me, rest = key
    if directional:
        counts: dict[str, int] = {}
        for x in rest:
            if x != EDGE:
                counts[x] = counts.get(x, 0) + 1
        around = ", ".join(f"{d} {x}" for (d, _, _), x in zip(DIRS, rest))
        tally = ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        desc = f"Its neighbours: {around} (in total: {tally})."
    else:
        parts = [f"{v} {k}" for k, v in sorted(rest, key=lambda kv: -kv[1]) if k != EDGE]
        edges = dict(rest).get(EDGE, 0)
        desc = f"Of its 8 neighbours: {', '.join(parts) or 'none'}" + (
            f" ({edges} are outside the world)." if edges else "."
        )
    return choice(
        f"This cell is currently {me}. {desc} Following the rules, what is this cell in the next step?",
        _criteria(world),
    )


@dataclass
class Life:
    world: World
    grid: list[list[str]]
    temperature: float = 0.8
    seed_value: int = 0
    cache: dict = field(default_factory=dict)  # (me, neighbourhood) -> probabilities
    steps: int = 0
    asked: int = 0  # unique questions sent to Drex
    cell_updates: int = 0  # cell decisions made (asked + reused)
    census_history: list[dict[str, int]] = field(default_factory=list)
    changed_history: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.rnd = random.Random(self.seed_value)
        self.directional = is_directional(self.world)
        n = len(self.grid)
        self.age = [[99] * n for _ in range(n)]  # steps since each cell last changed (for the glow)
        if not self.census_history:
            self.census_history.append(self.census())
            self.changed_history.append(0)

    def _rules_state(self) -> str:
        legend = "; ".join(f"{s.name}: {s.meaning}" for s in self.world.states)
        return (
            f"A cellular world simulation called '{self.world.title}'. Cell states: {legend}. "
            f"'edge' means outside the world. Rules, applied to every cell at the same time each step:\n"
            f"{self.world.rules}"
        )

    async def step(self, drex: Drex) -> dict:
        n = len(self.grid)
        keyed = [[situation(self.grid, r, c, self.directional) for c in range(n)] for r in range(n)]
        keys = {k for row in keyed for k in row}
        new = [k for k in keys if k not in self.cache]
        if new:
            qs = {f"q{i:04d}": _question(self.world, k, self.directional) for i, k in enumerate(new)}
            ans = await drex.ask_many(self._rules_state(), qs)
            for i, k in enumerate(new):
                self.cache[k] = ans[f"q{i:04d}"]["probabilities"]
        changed = 0
        nxt = []
        for r in range(n):
            row = []
            for c in range(n):
                probs = self.cache[keyed[r][c]]
                names = list(probs)
                if self.temperature <= 0:
                    row.append(max(names, key=probs.get))
                else:
                    w = [max(probs[x], 1e-9) ** (1 / self.temperature) for x in names]
                    row.append(self.rnd.choices(names, weights=w)[0])
                if row[-1] != self.grid[r][c]:
                    changed += 1
                    self.age[r][c] = 0
                else:
                    self.age[r][c] = min(self.age[r][c] + 1, 99)
            nxt.append(row)
        self.grid = nxt
        self.steps += 1
        self.asked += len(new)
        self.cell_updates += n * n
        self.census_history.append(self.census())
        self.changed_history.append(changed)
        return {
            "step": self.steps,
            "new_questions": len(new),
            "cached": len(keys) - len(new),
            "unique_situations": len(self.cache),
            "changed": changed,
        }

    def image(self, scale: int = 10, *, style: str = "tiles") -> Image.Image:
        """Render the grid. Drex decided every state; this only decides how a state looks:
        beveled tiles, a little per-cell texture, and a glow on cells that just changed (fading over 3 steps)."""
        n = len(self.grid)
        if style == "flat" or scale < 4:
            img = Image.new("RGB", (n, n))
            for r in range(n):
                for c in range(n):
                    img.putpixel((c, r), self.world.color(self.grid[r][c]))
            return img.resize((n * scale, n * scale), Image.NEAREST)
        from PIL import ImageDraw

        img = Image.new("RGB", (n * scale, n * scale))
        d = ImageDraw.Draw(img)
        glow = {0: 0.3, 1: 0.12}

        def mix(rgb, t, to=(255, 255, 255)):
            return tuple(int(v + (w - v) * t) for v, w in zip(rgb, to))

        for r in range(n):
            for c in range(n):
                base = self.world.color(self.grid[r][c])
                jitter = (((r * 73856093) ^ (c * 19349663)) % 13 - 6) / 100  # stable texture, +-6%
                rgb = tuple(max(0, min(255, int(v * (1 + jitter)))) for v in base)
                rgb = mix(rgb, glow.get(self.age[r][c], 0.0), (255, 236, 170))  # warm glow on change
                x0, y0, x1, y1 = c * scale, r * scale, (c + 1) * scale - 1, (r + 1) * scale - 1
                d.rectangle([x0, y0, x1, y1], fill=rgb)
                d.line([x0, y0, x1, y0], fill=mix(rgb, 0.22))  # lit top
                d.line([x0, y0, x0, y1], fill=mix(rgb, 0.22))  # lit left
                d.line([x0, y1, x1, y1], fill=mix(rgb, 0.3, (0, 0, 0)))  # shadow bottom
                d.line([x1, y0, x1, y1], fill=mix(rgb, 0.3, (0, 0, 0)))  # shadow right
        return img

    def census(self) -> dict[str, int]:
        out = {s.name: 0 for s in self.world.states}
        for row in self.grid:
            for x in row:
                out[x] = out.get(x, 0) + 1
        return out


async def run_life(
    idea: str | None = None,
    *,
    world: World | None = None,
    n: int = 24,
    steps: int = 30,
    temperature: float = 0.8,
    on_frame: Callable[[int, Life, dict], None] | None = None,
    on_world: Callable[[World], None] | None = None,
) -> tuple[Life, list[Image.Image], dict]:
    """Design (or take) a world, let Drex seed it, then run `steps` Drex-decided steps."""
    director = None
    if world is None:
        async with DeepInfra() as di:
            world = await design_world(di, idea or "a forest fire")
            director = di.usage.summary()
    if on_world:
        on_world(world)
    async with Drex() as drex:
        grid = await seed(drex, world, n)
        life = Life(world, grid, temperature=temperature)
        frames = [life.image()]
        prev = frames[0]
        if on_frame:
            on_frame(0, life, {"step": 0, "new_questions": n * n, "cached": 0, "unique_situations": 0})
        for _ in range(steps):
            info = await life.step(drex)
            cur = life.image()
            frames.append(Image.blend(prev, cur, 0.5))  # in-between frame for smoother motion
            frames.append(cur)
            prev = cur
            if on_frame:
                on_frame(life.steps, life, info)
        usage = drex.usage.summary()
    return life, frames, {"drex": usage, "director": director}

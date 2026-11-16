"""The entering page: what `intern` opens on, until the first prompt.

                              ██ ███  ██ ████████ ███████ ██████  ███  ██
                              ██ ████ ██    ██    █████   ██████  ████ ██
                              ██ ██ ████    ██    ███████ ██  ██  ██ ████
                              Intern v0.34.1 · thinking, fast and slow

            Intern's brain has two parts. Its System 2, the agent model, thinks slow and does the
            work ... Its System 1, a second and faster model, thinks fast and watches each turn ...

    ╭─ System 2 · thinking slow ───────────╮                    ╭─ System 1 · thinking fast ───────────╮
    │ anthropic/claude-haiku-5-5           │    (the mascot,    │ drex-max                             │
    │ thinking high · 9 tools              │     glancing       │ console                              │
    │                                      │     and blinking)  │                                      │
    │ ● base tools   files and the shell   │                    │ ◆ watch       nudges it out of ...   │
    │ ○ web search   searches the web ...  │                    │ ▣ guard  ⌕ search  ⟲ compaction      │
    ╰──────────────────────────────────────╯                    ╰──────────────────────────────────────╯
                               ~/work/parser · session 20261108-21...

            Describe a task to begin.   /help commands   /system2 model   /caps capabilities ...

Intern is a brain in two parts, and the page is drawn as one: the mascot, the README's with its eyes moving as they
do there (`Mascot`), between its two systems, each a card with the model it is on. System 2's card has what the
session has switched on, each capability in a few words; System 1's has its four jobs, behind the glyphs its panel
lists them under, so the page is also that panel's legend, and what it judges from. Above them the wordmark, in the
mascot's violets, and what Intern is, in the README's paragraph; under them where the session works, then what to do
next. All of it centred, and a third of the way down a tall terminal.

That is the page on a wide terminal (`hero`). A narrower one has the mascot and both systems in one frame
(`framed`); one too small for that, the frame alone, a line a job (`compact`). It is the transcript's first block, so
what is said before the first prompt (a session that cannot open, `/help`) shows under it, and it goes when the
conversation begins.
"""

from __future__ import annotations

import functools
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.content import Content
from textual.widgets import Static

from intern import __version__
from intern.guard import GuardMode
from intern.tui.ledger import Kind
from intern.tui.system1_panel import GLYPH
from intern.tui.theme import (
    BRAND,
    MASCOT_COLOURS,
    S_BAD,
    S_HEAD,
    S_INK,
    S_MUTED,
    S_MUTED_ITALIC,
    S_OK,
    S_TAB,
    WORDMARK,
)
from intern.tui.widgets import Part, clip, fit_path, flow, short_path, stacked, system1_parts

ART = (
    "██ ███  ██ ████████ ███████ ██████  ███  ██",
    "██ ████ ██    ██    ██      ██   ██ ████ ██",
    "██ ██ ████    ██    █████   ██████  ██ ████",
    "██ ██  ███    ██    ██      ██   ██ ██  ███",
    "██ ██   ██    ██    ███████ ██   ██ ██   ██",
)
"""The wordmark in block letters, a row for each of the mascot's violets (`SHADES`)."""
SHADES = tuple(MASCOT_COLOURS[pixel] for pixel in "98654")
SHADOW = "#3b2470"
"""The letters' shadow, a pixel down and to the right: a violet deeper than any of the mascot's, which reads on both
pages."""
TITLE = f"{WORDMARK} v{__version__} · thinking, fast and slow"
ABOUT = (
    "Intern's brain has two parts. Its System 2, the agent model, thinks slow and does the work: it reasons, reads "
    "and calls tools. Its System 1, a second and faster model, thinks fast and watches each turn: it nudges your "
    "intern when it goes in circles, checks risky tool calls before they run, and decides what your intern keeps in "
    "mind when its context fills up."
)
"""What Intern is, as the README says it."""
ABOUT_SHORT = (
    "Intern's brain has two parts: System 2 thinks slow and does the work, System 1 thinks fast and watches each turn."
)
"""The same for a small terminal."""
JOBS: tuple[tuple[Kind, str], ...] = (
    ("watch", "nudges it out of circles"),
    ("guard", "checks risky tool calls"),
    ("search", "finds the places to read"),
    ("compaction", "keeps what still matters"),
)
"""System 1's jobs, each behind its glyph on the System 1 panel, in the words the paragraph has for them."""
GUARD: dict[GuardMode, str] = {"off": "off · [guard] mode", "rules": "fixed rules only"}
"""What the guard's line says when `[guard] mode` leaves System 1 out of it."""
STATE = (
    "What System 1 judges from: the session as it reads it, your requests as the goal and then the history, cut "
    "to fit its window."
)
STATE_SHORT = "Judges from the State: your requests and the history, cut to fit."
"""The same on System 1's card."""
BRIEFS = {
    "cap.base": "files and the shell",
    "cap.web-search": "searches the web",
    "cap.web-read": "reads a page as text",
    "cap.browser": "opens and clicks pages",
    "cap.code-search": "System 1 searches code",
    "cap.workspace": "System 1 searches docs",
    "cap.memory": "notes across sessions",
}
"""Each capability in a few words, by its toggle's id (`capability_toggles`); the two System 1 does the searching
for say so."""
MCP_BRIEF = "remote tools"
JOB_WIDTH = 14
HINTS = (
    ("/help", "commands"),
    ("/system2", "model"),
    ("/caps", "capabilities"),
    ("Shift+Tab", "thinking"),
    ("/dark", "look"),
)
SPRITES = Path(__file__).with_name("mascot.png")
"""The mascot's frames, side by side, each `SPRITE_COLUMNS` by `SPRITE_ROWS`, a pixel a whole cell: a terminal draws
a cell's background exactly, and a half block only as well as its font does (Terminal.app leaves gaps around them).
The cube is `docs/assets/mascot.svg`'s, scaled down, a colour a row; the glasses are drawn by hand for cells twice as
tall as wide, a stroke two cells across and one down, with the eyes at rest, glancing left, glancing right and shut
(`EYES`)."""
SPRITE_COLUMNS, SPRITE_ROWS = 32, 16
EYES = ("rest", "left", "right", "shut")
LOOP_S = 8.0
"""The mascot's loop, as the SVG's: it glances left, then right, and blinks twice."""
TICK_S = 0.08
"""How often the mascot looks at the clock: a blink is two of these."""
MASCOT_ROWS = SPRITE_ROWS
ABOUT_WIDTH = 100
"""The widest the paragraph runs: a wide terminal is wider than a line reads."""
CARD_WIDTH = 44
"""A system's card on the wide page, its border and padding (`CARD_EDGES`) included."""
CARD_EDGES = 6
CARD_EDGE_ROWS = 4
"""A card's border and padding, down."""
SIDE_GAP = 3
"""The air between the mascot and a card."""
HERO_WIDTH = 2 * CARD_WIDTH + 2 * SIDE_GAP + SPRITE_COLUMNS
GRAND_WIDTH = 160
"""From this width on, and with the rows for it, the wide page's wordmark is twice the size."""
FOOT_WIDTH = 48
"""The widest where the session works, and its journal, each run under the mascot on the wide page."""
HERO_ROWS = 15
"""The wide page's rows around its paragraph and its cards: the wordmark and its title, where, the tips, the margins."""
WORDMARK_ROWS = len(ART) + 1
"""The rows a terminal too short for a page gets back when the page goes without its wordmark."""
SIDE_WIDTH = SPRITE_COLUMNS + 4
"""The mascot's column in the frame, with the gap after it."""
SIDE_ROWS = MASCOT_ROWS + 3
"""The mascot, a row of air and where the session works."""
FRAME = 6
"""The frame's border and padding, across."""
FRAME_MAX = 124
"""The widest the frame stands: wider, it would be mostly empty."""
TEXT_WIDTH = 60
"""The narrowest the systems' column reads well beside the mascot."""
FRAMED_ROWS = 17
"""The framed page's rows around its paragraph and the frame's inside: the wordmark, the frame's edges, the tips on
two lines, the margins."""
PROMPT_ROWS = 3
RISE = 3
"""Of the rows a page leaves spare, the share above it: a third of the way down, not the top."""


@dataclass
class Opening:
    """What the entering page says of the session about to start."""

    system2: str = "…"
    system1: str = "…"
    thinking: str = ""
    note: str = ""
    """Why there is no session, while there is none: it is opening, or it could not open."""
    cwd: Path | None = None
    journal: Path | None = None
    capabilities: list[tuple[str, str, bool]] = field(default_factory=list)
    """Each capability's toggle id, its name and whether it is on."""
    tools: int = 0
    guard: GuardMode = "full"


def merged(cells: list[tuple[str, str]]) -> list[Part]:
    """Cells of (text, style) as parts, neighbours in one style joined: a row of pixels is a few runs."""
    parts: list[tuple[str, str]] = []
    for text, style in cells:
        if parts and parts[-1][1] == style:
            parts[-1] = (parts[-1][0] + text, style)
        else:
            parts.append((text, style))
    return [(text, style) if style else text for text, style in parts]


def pixels(grid: list[list[str]]) -> Content:
    """A grid of pixel colours (`""` where there is none) as text, two pixels to a cell, one over the other. A cell
    whose two are one colour is that colour, with no glyph: a terminal that sets its lines apart leaves gaps around a
    block glyph, and none in a cell's background. Else it is a half block over the other's."""
    rows: list[list[Part]] = []
    for upper, lower in zip(grid[::2], grid[1::2], strict=True):
        cells: list[tuple[str, str]] = []
        for top, bottom in zip(upper, lower, strict=True):
            if top == bottom:
                cells.append((" ", f"on {top}" if top else ""))
            elif top:
                cells.append(("▀", f"{top} on {bottom}" if bottom else top))
            else:
                cells.append(("▄", bottom))
        rows.append(merged(cells))
    return stacked(rows)


@functools.cache
def wordmark(scale: int = 1) -> Content:
    """The block letters, lighter at the top as the mascot's top face is lit, with a shadow a pixel down and to the
    right. A letter's row is two pixels tall, so the shadow is as thin under it as beside it. `scale` 2 draws it
    twice the size, for a terminal with the room."""
    width, height = (len(ART[0]) + 1) * scale, (2 * len(ART) + 2) * scale
    grid = [[""] * width for _ in range(height)]
    for row, (line, shade) in enumerate(zip(ART, SHADES, strict=True)):
        for column, cell in enumerate(line):
            if cell == "█":
                for y in range(2 * row * scale, 2 * (row + 1) * scale):
                    grid[y][column * scale : (column + 1) * scale] = [shade] * scale
    for y in range(height - 1, scale - 1, -1):
        for x in range(width - 1, scale - 1, -1):
            if not grid[y][x] and grid[y - scale][x - scale] not in ("", SHADOW):
                grid[y][x] = SHADOW
    return pixels(grid)


@functools.cache
def poses() -> tuple[Content, ...]:
    """The mascot in each of its poses (`SPRITES`), every cell one colour."""
    from PIL import Image

    with Image.open(SPRITES) as sheet:
        image = sheet.convert("RGBA")

    def colour(x: int, y: int) -> str:
        red, green, blue, alpha = image.getpixel((x, y))  # pyright: ignore[reportGeneralTypeIssues]
        return f"#{red:02x}{green:02x}{blue:02x}" if alpha else ""

    return tuple(
        # A row of cells is the same pixel over itself: `pixels` then draws no half block.
        pixels([[colour(x, y) for x in range(left, left + SPRITE_COLUMNS)] for y in range(SPRITE_ROWS) for _ in "12"])
        for left in range(0, image.width, SPRITE_COLUMNS)
    )


def pose_at(seconds: float) -> int:
    """Which pose the mascot is in, `seconds` into its life, as the README's moves: its eyes glance left, then right,
    and blink twice, each loop. It does not breathe as that one does: a breath is less than a cell."""
    at = seconds % LOOP_S / LOOP_S
    if 0.165 <= at < 0.365:
        return EYES.index("left")
    if 0.485 <= at < 0.665:
        return EYES.index("right")
    if 0.85 <= at < 0.87 or 0.905 <= at < 0.925:
        return EYES.index("shut")
    return EYES.index("rest")


class Mascot(Static):
    """The mascot, its eyes alive as the README's are. Still where motion is switched off
    (`TEXTUAL_ANIMATIONS=none`)."""

    def __init__(self) -> None:
        super().__init__(poses()[0], id="welcome-mascot")
        self._pose = 0
        self._born = time.monotonic()

    def on_mount(self) -> None:
        if self.app.animation_level != "none":
            self.set_interval(TICK_S, self._tick)

    def _tick(self) -> None:
        pose = pose_at(time.monotonic() - self._born)
        if pose != self._pose:
            self._pose = pose
            self.update(poses()[pose], layout=False)  # every pose is the same size


def where(o: Opening, width: int) -> list[list[Part]]:
    """Where the session works and the journal it writes."""
    lines: list[list[Part]] = []
    if o.cwd is not None:
        lines.append([(fit_path(short_path(str(o.cwd), None), width), S_INK)])
    if o.journal is not None:
        lines.append([(clip(f"session {o.journal.stem}", width), S_MUTED)])
    return lines


def paragraph(text: str, width: int, style: str = S_MUTED) -> list[list[Part]]:
    return [[(line, style)] for line in textwrap.wrap(text, max(20, width))]


def jobs(o: Opening, width: int, *, wrap: bool = True) -> list[list[Part]]:
    """System 1's jobs, a glyph and a name each, then what it is asked: wrapped under itself, or cut to the line."""
    lines: list[list[Part]] = []
    for job, does in JOBS:
        idle = job == "guard" and o.guard != "full"
        does = GUARD[o.guard] if idle else does
        head: list[Part] = [(GLYPH[job], S_MUTED if idle else BRAND), (f" {job}".ljust(JOB_WIDTH - 1), S_INK)]
        room = max(12, width - JOB_WIDTH)
        said = textwrap.wrap(does, room) if wrap else [clip(does, room)]
        lines.append([*head, (said[0], S_MUTED)])
        lines += [[" " * JOB_WIDTH, (line, S_MUTED)] for line in said[1:]]
    return lines


def switch(name: str, on: bool) -> list[Part]:
    return [("●", S_OK) if on else ("○", S_MUTED), (f" {name}", S_INK if on else S_MUTED)]


def switches(o: Opening, width: int) -> list[list[Part]]:
    """What the session has switched on (`●`) and not (`○`), run together, then how many tools that comes to."""
    if not o.capabilities:
        return []
    items = [switch(name, on) for _, name, on in o.capabilities]
    return flow([*items, [(f"{o.tools} tools", S_MUTED)]], width, gap=2)


def capabilities(o: Opening, width: int) -> list[list[Part]]:
    """The same, a line each, with what the capability is in a few words."""
    column = min(max((len(name) for _, name, _ in o.capabilities), default=0), width // 3) + 4
    lines: list[list[Part]] = []
    for toggle, name, on in o.capabilities:
        brief = BRIEFS.get(toggle, MCP_BRIEF if toggle.startswith("cap.mcp.") else "")
        mark, label = switch(clip(name, column - 4), on)
        lines.append([mark, label, " " * (column - 1 - cell_len(label[0])), (clip(brief, width - column), S_MUTED)])
    return lines


def system2(o: Opening, width: int) -> list[list[Part]]:
    """System 2's card: the model it is on, how hard it thinks and how many tools it has, then what they are."""
    facts = " · ".join(fact for fact in (f"thinking {o.thinking}" * bool(o.thinking), f"{o.tools} tools") if fact)
    return [[(clip(o.system2, width), S_HEAD)], [(facts, S_MUTED)], [], *capabilities(o, width)]


def system1(o: Opening, width: int) -> list[list[Part]]:
    """System 1's card: the model it is on and where it is served, its jobs, then what it judges from."""
    model, served = system1_parts(o.system1)
    head: list[list[Part]] = [[(clip(model, width), S_HEAD)], [(served or "the decision model", S_MUTED)], []]
    return [*head, *jobs(o, width), [], *paragraph(STATE_SHORT, width)]


def systems(o: Opening, width: int, *, roomy: bool = True) -> Content:
    """The two systems in one column, each under its panel's tab with its model; System 1's jobs; then the
    capabilities. `roomy`, the State has its lines too and a capability its few words; else what Intern is comes
    first, in a line or two, a job is one line, the capabilities run together, and where the session works comes
    last, there being no mascot to put it under."""
    model1, served = system1_parts(o.system1)
    thinking = f" · thinking {o.thinking}" if o.thinking else ""
    lines: list[list[Part]] = [
        *([] if roomy else [*paragraph(ABOUT_SHORT, width, S_INK), []]),
        [(" System 2 ", S_TAB), ("  thinking slow   ", S_MUTED), (o.system2, S_HEAD), (thinking, S_MUTED)],
        *([*capabilities(o, width), [(f"{o.tools} tools", S_MUTED)] * bool(o.capabilities), []] if roomy else []),
        [
            (" System 1 ", S_TAB),
            ("  thinking fast   ", S_MUTED),
            (model1, S_HEAD),
            (f" · {served}" * bool(served), S_MUTED),
        ],
        *jobs(o, width, wrap=roomy),
    ]
    if roomy:
        lines += [[], [(" State ", S_TAB), ("  fast and slow", S_MUTED)], *paragraph(STATE, width)]
    else:
        lines += [[], *switches(o, width), *where(o, width)]
    return stacked(lines)


def tips(o: Opening, width: int) -> Content:
    """What to do next, then the keys worth knowing first; while there is no session, why."""
    waiting = o.note.endswith("…")
    lead: Part = (o.note, S_MUTED_ITALIC if waiting else S_BAD) if o.note else ("Describe a task to begin.", S_HEAD)
    return stacked(flow([[lead], *([(key, S_INK), (f" {does}", S_MUTED)] for key, does in HINTS)], width))


class Welcome(Vertical):
    """The entering page (the module's docstring)."""

    def __init__(self, opening: Opening) -> None:
        super().__init__(id="welcome")
        self.opening = opening
        self._fitted: object = None
        """The terminal's size the page was last drawn for."""

    def compose(self) -> ComposeResult:
        # Each in a `Center`: a container centres its children as one block, and these are centred one by one.
        with Center():
            yield Static(wordmark(), id="welcome-wordmark")
        with Center():
            yield Static(Content.styled(TITLE, f"bold {BRAND}"), id="welcome-title")
        with Center():
            yield Static(id="welcome-about")
        with Center(), Horizontal(id="welcome-row") as row:
            row.border_title = TITLE
            card = Static(id="welcome-system2", classes="card")
            card.border_title = "System 2 · thinking slow"
            yield card
            with Vertical(id="welcome-side"):
                yield Mascot()
                yield Static(id="welcome-where")
            card = Static(id="welcome-system1", classes="card")
            card.border_title = "System 1 · thinking fast"
            yield card
        with Center():
            yield Static(id="welcome-foot")
        with Center():
            yield Static(id="welcome-tips")

    def on_mount(self) -> None:
        self._draw()
        # The page fits the terminal's height as well as its width, and its own resize says nothing of the height.
        self.screen.screen_layout_refresh_signal.subscribe(self, self._refit)

    def show(self, opening: Opening) -> None:
        """The session as it now stands: opened, or on another model or capability."""
        self.opening = opening
        self._draw()

    def _refit(self, _: object) -> None:
        if self.app.size != self._fitted:
            self._draw()

    def _draw(self) -> None:
        """The richest page the terminal holds whole: the page is the first thing seen, so nothing of it may start
        above the screen."""
        o = self.opening
        columns, rows = self._fitted = self.app.size
        width, room = self.size.width or columns - 4, rows - PROMPT_ROWS
        about = paragraph(ABOUT, min(width - 2, ABOUT_WIDTH), S_INK)
        inside = min(width, FRAME_MAX) - FRAME
        text = systems(o, inside - SIDE_WIDTH, roomy=True)
        cards = [system2(o, CARD_WIDTH - CARD_EDGES), system1(o, CARD_WIDTH - CARD_EDGES)]
        card_rows = max(len(card) for card in cards) + CARD_EDGE_ROWS  # the cards are one height
        tall = max(MASCOT_ROWS, card_rows)
        hero = HERO_ROWS + len(about) + tall
        framed = FRAMED_ROWS + len(about) + max(SIDE_ROWS, text.plain.count("\n") + 1)
        if width >= HERO_WIDTH and hero - WORDMARK_ROWS <= room:
            look, needs = "hero", hero
        elif inside - SIDE_WIDTH >= TEXT_WIDTH and framed - WORDMARK_ROWS <= room:
            look, needs = "framed", framed
        else:
            look, needs = "compact", room
        short = needs > room  # all of it but the wordmark
        grand = look == "hero" and width >= GRAND_WIDTH and needs + WORDMARK_ROWS <= room  # a wordmark twice the size
        needs += WORDMARK_ROWS * (grand - short)
        for name in ("hero", "framed", "compact"):
            self.set_class(name == look, name)
        self.set_class(short, "short")
        self.query_one("#welcome-wordmark", Static).update(wordmark(2 if grand else 1))
        self.styles.margin = (1 + max(0, room - needs) // RISE, 0, 1, 0) if look != "compact" else (0, 0, 0, 0)
        self.query_one("#welcome-about", Static).update(stacked(about))
        self.query_one("#welcome-where", Static).update(stacked(where(o, SPRITE_COLUMNS)))
        foot: list[Part] = [part for line in where(o, FOOT_WIDTH) for part in (*line, ("  ·  ", S_MUTED))][:-1]
        self.query_one("#welcome-foot", Static).update(Content.assemble(*foot))
        self.query_one("#welcome-tips", Static).update(tips(o, min(width, FRAME_MAX) - 2))
        left, right = self.query_one("#welcome-system2", Static), self.query_one("#welcome-system1", Static)
        left.update(stacked(cards[0]))
        right.update(
            stacked(cards[1]) if look == "hero" else text if look == "framed" else systems(o, width - 4, roomy=False)
        )
        # On the wide page the cards stand as tall as each other, and they and the mascot at each other's middle.
        left.styles.height = right.styles.height = card_rows if look == "hero" else "auto"
        left.styles.margin = right.styles.margin = ((tall - card_rows) // 2 if look == "hero" else 0, 0, 0, 0)
        self.query_one(Mascot).styles.margin = ((tall - MASCOT_ROWS) // 2 if look == "hero" else 0, 0, 0, 0)

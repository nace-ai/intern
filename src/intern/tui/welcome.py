"""The entering page: what `intern` opens on, until the first prompt.

    ██╗███╗   ██╗████████╗███████╗██████╗ ███╗   ██╗
    ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗████╗  ██║
    ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝██╔██╗ ██║
    ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗██║╚██╗██║
    ██║██║ ╚████║   ██║   ███████╗██║  ██║██║ ╚████║
    ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝
    ╭──────────────────────── Intern v0.34.1 · thinking, fast and slow ────────────────────────╮
    │  (the mascot,       Intern's brain has two parts. Its System 2, the agent model, thinks  │
    │   glancing and      slow and does the work ... Its System 1 ... watches each turn ...    │
    │   blinking)         System 2  thinking slow   anthropic/claude-haiku-5-5 · thinking high │
    │                     System 1  thinking fast   drex-max · console                         │
    │                     ◆ watch   ▣ guard   ⌕ search   ⟲ compaction, a line each             │
    │  ~/work/parser      State  fast and slow   what System 1 judges from                     │
    │  session 2026...    ● base tools  ● memory  ○ web search ...   9 tools                   │
    ╰──────────────────────────────────────────────────────────────────────────────────────────╯
    Describe a task to begin.   /help commands   /system2 model   /caps capabilities ...

The wordmark in the mascot's violets, one a row. In a frame, the mascot as the README draws it, moving as it does
there (`Mascot`), over where the session works and its journal; beside it, what Intern is in a paragraph, then the
two systems, each under its panel's tab with the model it is on, and System 1's four jobs behind the glyphs its
panel lists them under, so the page is also the panel's legend; the State, which System 1 judges from; then what
the session has switched on. It is the transcript's first block, so what is said before the first prompt (a session
that cannot open, `/help`) shows under it, and it goes when the conversation begins. A terminal too small to hold all
of it gets the frame alone, a line a job.
"""

from __future__ import annotations

import functools
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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
    "██╗███╗   ██╗████████╗███████╗██████╗ ███╗   ██╗",
    "██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗████╗  ██║",
    "██║██╔██╗ ██║   ██║   █████╗  ██████╔╝██╔██╗ ██║",
    "██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗██║╚██╗██║",
    "██║██║ ╚████║   ██║   ███████╗██║  ██║██║ ╚████║",
    "╚═╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝",
)
"""The wordmark in block letters, a row for each of the mascot's six violets (`SHADES`)."""
SHADES = tuple(MASCOT_COLOURS[pixel] for pixel in "986543")
SHADOW = MASCOT_COLOURS["3"]
"""The letters' edges, in the mascot's deepest violet."""
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
    ("watch", "each turn: advancing, or re-reading and retrying? Three stuck turns in a row get a nudge"),
    ("guard", "a risky tool call before it runs: what it would do, and whether you asked for it"),
    ("search", "decision_code and decision_files, on with /caps: the places that answer a question"),
    ("compaction", "the files read, when the context fills: the ones still being worked on are kept"),
)
"""System 1's jobs, each behind its glyph on the System 1 panel."""
GUARD: dict[GuardMode, str] = {
    "off": 'off: [guard] mode = "full" in the config has System 1 judge risky calls before they run',
    "rules": 'fixed rules only, for the clearly dangerous. [guard] mode = "full" adds System 1\'s say',
}
"""What the guard's line says when `[guard] mode` leaves System 1 out of it."""
STATE = (
    "What System 1 judges from: the session as it reads it, your requests as the goal and then the history, cut "
    "to fit its window."
)
JOB_WIDTH = 14
HINTS = (
    ("/help", "commands"),
    ("/system2", "model"),
    ("/caps", "capabilities"),
    ("Shift+Tab", "thinking"),
    ("/dark", "look"),
)
SPRITES = Path(__file__).with_name("mascot.png")
"""The mascot's frames, side by side, `SPRITE` pixels square each: `docs/assets/mascot.svg` drawn at its animation's
poses and scaled down, a pixel kept where the body covers two fifths of it. The eyes at rest, glancing left, glancing
right and shut (`EYES`); then the same four with the body breathed in, a pixel shorter."""
SPRITE = 28
EYES = ("rest", "left", "right", "shut")
LOOP_S = 8.0
"""The mascot's loop, as the SVG's: it glances left, then right, and blinks twice."""
BREATH_S = 4.0
TICK_S = 0.08
"""How often the mascot looks at the clock: a blink is two of these."""
MASCOT_WIDTH = SPRITE
SIDE_WIDTH = MASCOT_WIDTH + 4
"""The mascot's column, with the gap after it."""
SIDE_ROWS = SPRITE // 2 + 3
"""The mascot, a row of air and where the session works."""
FRAME = 6
"""The frame's border and padding, across."""
TEXT_WIDTH = 60
"""The narrowest the systems' column reads well beside the mascot."""
TEXT_MAX = 104
"""The widest a line of it runs: on a wide terminal the frame is wider than a paragraph reads."""
CHROME_ROWS = 16
"""The rows of the roomy page around the frame's inside: the wordmark, the frame's edges, the tips."""
PROMPT_ROWS = 3


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
    capabilities: list[tuple[str, bool]] = field(default_factory=list)
    """Each capability's name and whether it is on."""
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


def wordmark() -> Content:
    """The block letters, lighter at the top as the mascot's top face is lit, their edges in its deepest shade. A
    letter's cells are coloured, not drawn: a terminal that sets its lines apart leaves gaps around a block glyph,
    and none in a cell's background."""
    rows: list[list[Part]] = []
    for row, shade in zip(ART, SHADES, strict=True):
        rows.append(merged([(" ", f"on {shade}") if cell == "█" else (cell, SHADOW * (cell != " ")) for cell in row]))
    return stacked(rows)


@functools.cache
def poses() -> tuple[Content, ...]:
    """The mascot in each of its poses (`SPRITES`), two pixels to a cell, one over the other: a cell whose two are
    one colour is that colour, else a half block over the other's."""
    from PIL import Image

    def colour(pixel: tuple[int, ...]) -> str:
        return f"#{pixel[0]:02x}{pixel[1]:02x}{pixel[2]:02x}" if pixel[3] else ""

    drawn: list[Content] = []
    with Image.open(SPRITES) as sheet:
        pixels = sheet.convert("RGBA")
    for left in range(0, pixels.width, SPRITE):
        rows: list[list[Part]] = []
        for y in range(0, SPRITE, 2):
            cells: list[tuple[str, str]] = []
            for x in range(left, left + SPRITE):
                top, bottom = colour(pixels.getpixel((x, y))), colour(pixels.getpixel((x, y + 1)))  # pyright: ignore[reportArgumentType]
                if top == bottom:
                    cells.append((" ", f"on {top}" if top else ""))
                elif top:
                    cells.append(("▀", f"{top} on {bottom}" if bottom else top))
                else:
                    cells.append(("▄", bottom))
            rows.append(merged(cells))
        drawn.append(stacked(rows))
    return tuple(drawn)


def pose_at(seconds: float) -> int:
    """Which pose the mascot is in, `seconds` into its life, as the README's moves: its eyes glance left, then right,
    and blink twice, each loop; its body breathes in and out all along."""
    at = seconds % LOOP_S / LOOP_S
    if 0.165 <= at < 0.365:
        eyes = "left"
    elif 0.485 <= at < 0.665:
        eyes = "right"
    elif 0.85 <= at < 0.87 or 0.905 <= at < 0.925:
        eyes = "shut"
    else:
        eyes = "rest"
    breathed_in = 0.25 <= seconds % BREATH_S / BREATH_S < 0.75
    return EYES.index(eyes) + len(EYES) * breathed_in


class Mascot(Static):
    """The mascot, alive as the README's is. Still where motion is switched off (`TEXTUAL_ANIMATIONS=none`)."""

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
            self.update(poses()[pose])


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


def systems(o: Opening, width: int, *, roomy: bool = True) -> Content:
    """What Intern is, then the two systems, each under its panel's tab with its model; System 1's jobs, a glyph
    each; then the capabilities. `roomy`, a job wraps under itself and the State has its line too; else a job is one
    line, and where the session works comes last, there being no mascot to put it under."""
    width = min(width, TEXT_MAX)
    model1, served = system1_parts(o.system1)
    thinking = f" · thinking {o.thinking}" if o.thinking else ""
    lines: list[list[Part]] = [
        *paragraph(ABOUT if roomy else ABOUT_SHORT, width, S_INK),
        [],
        [(" System 2 ", S_TAB), ("  thinking slow   ", S_MUTED), (o.system2, S_HEAD), (thinking, S_MUTED)],
        *([[]] * roomy),
        [
            (" System 1 ", S_TAB),
            ("  thinking fast   ", S_MUTED),
            (model1, S_HEAD),
            (f" · {served}" * bool(served), S_MUTED),
        ],
    ]
    for job, does in JOBS:
        idle = job == "guard" and o.guard != "full"
        does = GUARD[o.guard] if idle else does
        head: list[Part] = [(GLYPH[job], S_MUTED if idle else BRAND), (f" {job}".ljust(JOB_WIDTH - 1), S_INK)]
        said = textwrap.wrap(does, max(20, width - JOB_WIDTH)) if roomy else [clip(does, width - JOB_WIDTH)]
        lines.append([*head, (said[0], S_MUTED)])
        lines += [[" " * JOB_WIDTH, (line, S_MUTED)] for line in said[1:]]
    if roomy:
        lines += [[], [(" State ", S_TAB), ("  fast and slow", S_MUTED)], *paragraph(STATE, width)]
    if o.capabilities:
        switches: list[list[Part]] = [
            [("●", S_OK) if on else ("○", S_MUTED), (f" {name}", S_INK if on else S_MUTED)]
            for name, on in o.capabilities
        ]
        switches.append([(f"{o.tools} tools", S_MUTED)])
        lines += [[], *flow(switches, width, gap=2)]
    if not roomy:
        lines += where(o, width)
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

    def compose(self) -> ComposeResult:
        yield Static(wordmark(), id="welcome-wordmark")
        with Horizontal(id="welcome-frame") as frame:
            frame.border_title = TITLE
            with Vertical(id="welcome-side"):
                yield Mascot()
                yield Static(id="welcome-where")
            yield Static(id="welcome-systems")
        yield Static(id="welcome-tips")

    def on_mount(self) -> None:
        self._draw()

    def show(self, opening: Opening) -> None:
        """The session as it now stands: opened, or on another model or capability."""
        self.opening = opening
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _draw(self) -> None:
        """The roomy page when the terminal holds it whole, else the frame alone: the page is the first thing seen,
        so nothing of it may start above the screen."""
        columns, rows = self.app.size
        inside = (self.size.width or columns - 4) - FRAME
        text = systems(self.opening, inside - SIDE_WIDTH, roomy=True)
        tall = CHROME_ROWS + max(SIDE_ROWS, text.plain.count("\n") + 1)
        roomy = inside - SIDE_WIDTH >= TEXT_WIDTH and tall <= rows - PROMPT_ROWS
        self.set_class(not roomy, "compact")
        self.query_one("#welcome-systems", Static).update(text if roomy else systems(self.opening, inside, roomy=False))
        self.query_one("#welcome-where", Static).update(stacked(where(self.opening, MASCOT_WIDTH)))
        self.query_one("#welcome-tips", Static).update(tips(self.opening, inside + FRAME - 2))

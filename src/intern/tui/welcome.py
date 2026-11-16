"""The entering page: what `intern` opens on, until the first prompt.

    ██╗███╗   ██╗████████╗███████╗██████╗ ███╗   ██╗
    ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗████╗  ██║
    ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝██╔██╗ ██║
    ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗██║╚██╗██║
    ██║██║ ╚████║   ██║   ███████╗██║  ██║██║ ╚████║
    ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝
    Your intern, with a brain in two parts: ...
    ╭─ Intern 0.30.7 · thinking, fast and slow ──────────────────────────────────────────────╮
    │  (the mascot)       System 2  thinking slow   anthropic/claude-haiku-5-5 · thinking high │
    │                     The agent model. It does the work: ...                             │
    │                     System 1  thinking fast   drex-max · console                       │
    │                     ◆ watch   ▣ guard   ⌕ search   ⟲ compaction, a line each           │
    │  ~/work/parser      State  fast and slow                                               │
    │  session 2026...    ● base tools  ● memory  ○ web search ...                           │
    ╰────────────────────────────────────────────────────────────────────────────────────────╯
    Describe a task to begin.   /help commands   /system2 model   /caps capabilities ...

The wordmark in the mascot's violets, one a row; in a frame, the mascot and beside it the two systems, each under
its panel's tab: which model it is, what it does, and System 1's four jobs behind the glyphs its panel lists them
under, so the page is also the panel's legend; the State, which System 1 judges from; then what the session has
switched on, where it works and its journal. It is the transcript's
first block, so what is said before the first prompt (a session that cannot open, `/help`) shows under it, and it
goes when the conversation begins. A terminal too small to hold all of it gets the frame alone, a line a job.
"""

from __future__ import annotations

import textwrap
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
from intern.tui.widgets import MASCOT, Part, clip, fit_path, flow, short_path, stacked, system1_parts

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
TAGLINE = (
    "Your intern, with a brain in two parts: one thinks slow and does the work, the other thinks fast and watches "
    "every turn."
)
TITLE = f"{WORDMARK} {__version__} · thinking, fast and slow"
SYSTEM2 = "The agent model. It does the work: it reasons, reads, calls tools and writes the answer."
SYSTEM1 = "A decision model. It answers in a fraction of a second, with a probability, and judges what System 2 does:"
JOBS: tuple[tuple[Kind, str], ...] = (
    ("watch", "each turn: is the work advancing, or re-reading and retrying? Three stuck turns in a row get a nudge"),
    ("guard", "a risky tool call before it runs: what it would do, and whether you asked for it"),
    ("search", "decision_code and decision_files, switched on with /caps: the places that answer a question"),
    ("compaction", "the files read, when the context fills: the ones still being worked on are kept"),
)
"""System 1's jobs, each behind its glyph on the System 1 panel."""
GUARD: dict[GuardMode, str] = {
    "off": 'off. [guard] mode = "full" in the config has System 1 judge a risky tool call before it runs',
    "rules": 'fixed rules only: they refuse the clearly dangerous calls. [guard] mode = "full" adds System 1\'s say',
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
MASCOT_WIDTH = 2 * len(MASCOT[0])
SIDE_WIDTH = MASCOT_WIDTH + 4
"""The mascot's column, with the gap after it."""
SIDE_ROWS = len(MASCOT) + 3
"""The mascot, a row of air and where the session works."""
FRAME = 6
"""The frame's border and padding, across."""
TEXT_WIDTH = 60
"""The narrowest the systems' column reads well beside the mascot."""
CHROME_ROWS = 19
"""The rows of the roomy page around the frame's inside: the wordmark, the tagline, the frame's edges, the tips."""
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


def wordmark() -> Content:
    """The block letters, lighter at the top as the mascot's top face is lit, their edges in its deepest shade."""
    rows: list[list[Part]] = []
    for row, shade in zip(ART, SHADES, strict=True):
        rows.append([(cell, shade if cell == "█" else SHADOW) if cell != " " else " " for cell in row])
    return stacked(rows)


def mascot() -> Content:
    """The mascot at twice the masthead's size: a pixel two cells wide and one tall, so it stays square."""
    return stacked([[("██", MASCOT_COLOURS[pixel]) if pixel != "." else "  " for pixel in row] for row in MASCOT])


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
    """The two systems, each under its panel's tab: the model, what it does; System 1's jobs, a glyph each; then
    the capabilities. `roomy`, a job wraps under itself and the State has its lines too; else a job is one line,
    and where the session works comes last, there being no mascot to put it under."""
    model1, served = system1_parts(o.system1)
    thinking = f" · thinking {o.thinking}" if o.thinking else ""
    lines: list[list[Part]] = [
        [(" System 2 ", S_TAB), ("  thinking slow   ", S_MUTED), (o.system2, S_HEAD), (thinking, S_MUTED)],
        *paragraph(SYSTEM2, width),
        [],
        [
            (" System 1 ", S_TAB),
            ("  thinking fast   ", S_MUTED),
            (model1, S_HEAD),
            (f" · {served}" * bool(served), S_MUTED),
        ],
        *paragraph(SYSTEM1, width),
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
        yield Static(Content.styled(TAGLINE, S_MUTED), id="welcome-tagline")
        with Horizontal(id="welcome-frame") as frame:
            frame.border_title = TITLE
            with Vertical(id="welcome-side"):
                yield Static(mascot(), id="welcome-mascot")
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

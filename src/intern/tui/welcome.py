"""The entering page: what `intern` opens on, until the first prompt.

                              ██ ███  ██ ████████ ███████ ██████  ███  ██
                              ██ ████ ██    ██    █████   ██████  ████ ██
                              ██ ██ ████    ██    ███████ ██  ██  ██ ████
                              Intern v0.53.0
    ░  Intern uses three models: a frontier model does the hard part, an efficient model finishes the rest  ░
    ░     for much less, and a decision model picks the moment to hand over and keeps the work on track.    ░

    ╭─ System 2 ───────────────────────────╮                    ╭─ Tools ──────────────────────────────╮
    │ Frontier   thinking high ▰▰▰▱▱       │                    │ 9 tools for the frontier and         │
    │ claude-opus-5-5                      │                    │ efficient models                     │
    │ ⠋ working on the hard part  ▰▰▱▱     │                    │                                      │
    │                                      │    (the mascot,    │ ● base tools                         │
    │ hand-over   ready ▂▄ 0.48            │     glancing       │   files and the shell                │
    │                                      │     and blinking)  │ ○ web search                         │
    │ Efficient                            │                    │   searches the web                   │
    │ claude-haiku-5-5                     │                    │ ...                                  │
    │ ○ waits for the hand-over            │                    │                                      │
    ╰──────────────────────────────────────╯                    │                                      │
    ╭─ System 1 ───────────────────────────╮                    │                                      │
    │ Decision                             │                    │                                      │
    │ drex-max · console                   │                    │                                      │
    │ ● hand-over   picks when to hand over│                    │                                      │
    │ ...                                  │                    │                                      │
    ╰──────────────────────────────────────╯                    ╰──────────────────────────────────────╯
                               ~/work/parser · session 20261108-21...

            Describe a task to begin.   /help commands   /models models   /caps capabilities ...

The page is drawn as one. On the left System 2's card over System 1's, on the right the tools, Intern itself between
them, the Nace cube with eyes, alive as the brand draws it (`Mascot`); the two sides as tall as each other. System
2's card plays the run as Intern makes it, over and over (`RunPlay`): the frontier model works the hard part, the
decision model's P(ready) rises on the hand-over's chip until it hands over, and the efficient model finishes the
rest. While hand-over is off the efficient model is not there, the chip is gray, and the frontier model works every
turn. System 1's card has the decision model's jobs, each on or off as a capability is, and what it judges from. The
tools' card says which models work with the tools, then each capability over what it is, in a few words. Above them
the wordmark, in ink with the accent for its shadow, and what Intern is, in a sentence on a shaded band: three models
while a run hands over, two while it does not; under them where the session works, then what to do next. All of it
centred, and a third of the way down a tall terminal.

That is the page on a wide terminal (`hero`). A narrower one has the models and the tools in one column beside the
mascot, in one frame (`framed`); one too small for that, the frame alone, a line a job (`compact`). It is the
transcript's first block, so what is said before the first prompt (a session that cannot open, a switch that failed)
shows under it, and it goes when the conversation begins.
"""

from __future__ import annotations

import math
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.content import Content
from textual.widgets import Static

from intern import __version__
from intern.tui.ledger import Kind
from intern.tui.theme import (
    BRAND,
    DARK,
    LIGHT,
    S_BAD,
    S_BOX_MUTED,
    S_BRAND_BOLD,
    S_HEAD,
    S_INK,
    S_MUTED,
    S_MUTED_ITALIC,
    S_OK,
    S_RULE,
    S_TAB,
    WORDMARK,
)
from intern.tui.widgets import (
    ART,
    EYES,
    HANDOVER_SIGN,
    LADDER,
    SPINNER,
    Part,
    clip,
    fit_path,
    flow,
    frame,
    model_label,
    reel,
    sextants,
    short_path,
    stacked,
    system1_parts,
    thinking_box,
    wordmark,
)

TITLE = f"{WORDMARK} v{__version__}"
ABOUT = (
    "Intern uses three models: a frontier model does the hard part, an efficient model finishes the rest for much "
    "less, and a decision model picks the moment to hand over and keeps the work on track."
)
"""What Intern is while a run hands over."""
ABOUT_SHORT = (
    "A frontier model does the hard part, an efficient model finishes it, and a decision model picks the moment."
)
"""The same for a small terminal."""
ABOUT_ALONE = "Intern uses two models: a frontier model does the work, and a decision model keeps it on track."
"""What Intern is while hand-over is off, on any terminal."""
NAMED = r"(three|two) models|(frontier|efficient|decision) model"
"""What stands out in what Intern is: how many models, and each by its name."""
JOBS: tuple[tuple[Kind, str], ...] = (
    ("hand-over", "picks when to hand over"),
    ("watch", "nudges it out of circles"),
    ("guard", "checks risky tool calls"),
    ("search", "finds the places to read"),
    ("compaction", "keeps what still matters"),
)
"""The decision model's (System 1's) jobs, in the words the paragraph has for them."""
SEARCHES = ("cap.code-search", "cap.workspace")
"""The capabilities the decision model does the searching for."""
GuardState = Literal["off", "rules", "full"]
"""The guard capability: off, on with the fixed rules alone (`[capabilities.guard] system1 = false`), or on."""
GUARD: dict[GuardState, str] = {"off": "off · /caps guard", "rules": "fixed rules only"}
"""What the guard's line says when the decision model does not judge calls."""
HANDOVER_OFF = "off · /handover on"
"""What the hand-over's line says while no efficient model is set."""
STATE = (
    "What the decision model judges from: the session as it reads it, your requests as the goal and then the "
    "history, cut to fit its window."
)
STATE_SHORT = "Judges from the State: your requests and the history, cut to fit."
"""The same on the decision model's card."""
BRIEFS = {
    "cap.base": "files and the shell",
    "cap.web-search": "searches the web",
    "cap.web-read": "reads a page as text",
    "cap.browser": "opens and clicks pages",
    "cap.code-search": "searches code for it",
    "cap.workspace": "searches docs for it",
    "cap.memory": "notes across sessions",
    "cap.guard": "checks risky calls",
    "cap.tool-result-guard": "checks tool output",
    "cap.subagents": "hands off whole tasks",
}
"""Each capability in a few words, by its toggle's id (`capability_toggles`)."""
NAMES = {"cap.tool-result-guard": "result guard"}
"""A shorter name on the card for a capability whose own (`Toggle.name`, what clients show) would not fit beside the
others."""
MCP_BRIEF = "remote tools"
JOB_WIDTH = 14
SETUP_NOTE = "Not connected yet: /setup connects your models"
"""What the page and the masthead say while no models are set up: a way on, not an error."""
HINTS = (
    ("/help", "commands"),
    ("/models", "models"),
    ("/caps", "capabilities"),
    ("Shift+Tab", "thinking"),
    ("/dark", "look"),
)
SPRITE_COLUMNS, SPRITE_ROWS = 32, 16
"""The mascot in cells, two pixels to a cell down: as wide in pixels as it is tall."""
BIG_COLUMNS, BIG_ROWS = 40, 20
"""The same on a wide page with the room for it."""
TICK_S = 0.08
"""A frame of Intern's motion, as the brand draws it: 12.5 a second."""
ABOUT_WIDTH = 110
"""The widest what Intern is may run, before its lines are evened out: a wide terminal is wider than a line reads."""
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
SIDE_ROWS = SPRITE_ROWS + 3
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
RUN_LOOP_S = 10.0
"""The run the page plays, over and over: the frontier model on the hard part, the hand-over, the efficient model on
the rest, a moment at the end."""
RUN_PHASES: tuple[tuple[float, Phase], ...] = ((4.0, "frontier"), (5.5, "handover"), (9.0, "efficient"), (10.0, "done"))
"""Each phase of the played run, by the second it ends at."""
RUN_TICK_S = 0.2
"""A frame of the played run: five a second, enough for its spinner and the arrow filling."""
RUN_TURNS = 4
"""The turns each model takes in the played run, a pip each."""
CARD_GAP = 1
"""The air between System 2's card and System 1's, on the wide page."""
ABOUT_PAD = 2
"""The band's air either side of what Intern is."""
READY_FROM, READY_AT = 0.15, 0.82
"""The decision model's P(ready) in the played run: where it starts, and where the run hands over."""
PROMPT_ROWS = 3
RISE = 3
"""Of the rows a page leaves spare, the share above it: a third of the way down, not the top."""


@dataclass
class Opening:
    """What the entering page says of the session about to start."""

    system2: str = "…"
    system1: str = "…"
    handoff: str = ""
    """The efficient model a run hands over to; empty while there is none."""
    thinking: str = ""
    levels: tuple[str, ...] = ()
    """The thinking level and the levels the model takes, as the masthead has them."""
    note: str = ""
    """Why there is no session, while there is none: it is opening, or it could not open."""
    cwd: Path | None = None
    journal: Path | None = None
    capabilities: list[tuple[str, str, bool]] = field(default_factory=list)
    """Each capability's toggle id, its name and whether it is on."""
    tools: int = 0
    guard: GuardState = "off"


Phase = Literal["frontier", "handover", "efficient", "done"]
"""Where the played run is: the frontier model working, the hand-over, the efficient model working, the end."""
State = Literal["idle", "listening", "rolling", "blocked", "happy"]
"""Intern's states on the entering page and the setup: ready for a task; the user is typing; the session is opening;
it could not open; setup is done."""


class Mascot(Static):
    """Intern, alive as the brand draws it: it plays a state's loop (`play`), and shuts its eyes for a frame between
    two states, so a change is a blink and never a jump. Still, its eyes at rest, where motion is switched off
    (`TEXTUAL_ANIMATIONS=none`)."""

    def __init__(self, state: State = "idle") -> None:
        super().__init__(id="welcome-mascot")
        self._cells = (SPRITE_COLUMNS, SPRITE_ROWS)
        self._shown: object = None
        self._state: State = state
        self._began = time.monotonic()

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(TICK_S, self._tick)

    def fit(self, columns: int, rows: int) -> None:
        """Stand `columns` cells by `rows`."""
        self._cells = (columns, rows)
        self._tick()

    def play(self, state: State) -> None:
        """Go to `state`, behind a blink."""
        if state != self._state:
            self._state = state
            self._began = time.monotonic() + TICK_S

    def _tick(self) -> None:
        """Draw Intern again when its frame, its size or the look (`/dark`) is another."""
        ticks = int((time.monotonic() - self._began) / TICK_S)
        if self.app.animation_level == "none":
            name, index = "poses", EYES.index("rest")
        elif ticks < 0:
            name, index = "poses", EYES.index("shut")
        else:
            sequence = reel()["states"][self._state]["sequence"]
            name, index = self._state, sequence[ticks % len(sequence)]
        look = DARK if self.app.current_theme.dark else LIGHT
        shown = (name, index, *self._cells, look["paper"], look["ink"], look["brand"], sextants())
        if self._shown != shown:
            self._shown = shown
            self.update(Content("\n").join(frame(*shown)))


def where(o: Opening, width: int) -> list[list[Part]]:
    """Where the session works and the journal it writes."""
    lines: list[list[Part]] = []
    if o.cwd is not None:
        lines.append([(fit_path(short_path(str(o.cwd), None), width), S_INK)])
    if o.journal is not None:
        lines.append([(clip(f"session {o.journal.stem}", width), S_MUTED)])
    return lines


def about_lines(o: Opening, width: int) -> list[Content]:
    """What Intern is, as the session has it (three models while a run hands over, two while not), the models in
    bold, in as few lines as `width` cells hold, each about as long as the others."""
    text = Content.styled(ABOUT if o.handoff else ABOUT_ALONE, S_INK).highlight_regex(NAMED, style="bold")
    lines = text.wrap(width := max(20, width))
    while width > 20 and len(text.wrap(width - 1)) == len(lines):  # as narrow as the same lines go: no short last
        width -= 1
    return text.wrap(width)


def paragraph(text: str, width: int, style: str = S_MUTED) -> list[list[Part]]:
    return [[(line, style)] for line in textwrap.wrap(text, max(20, width))]


def jobs(o: Opening, width: int, *, wrap: bool = True) -> list[list[Part]]:
    """The decision model's jobs, each on (`●`) or off (`○`) as a capability is, then what it is asked: wrapped under
    itself, or cut to the line. The hand-over is off while there is no efficient model, the guard unless its
    capability is on with the decision model judging, and the search while neither capability it searches for is on."""
    searches = any(on for toggle, _, on in o.capabilities if toggle in SEARCHES)
    lines: list[list[Part]] = []
    for job, does in JOBS:
        unguarded = job == "guard" and o.guard != "full"
        alone = job == "hand-over" and not o.handoff
        does = GUARD[o.guard] if unguarded else HANDOVER_OFF if alone else does
        idle = unguarded or alone or (job == "search" and not searches)
        head = switch(job.ljust(JOB_WIDTH - 2), not idle)
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
    items = [switch(NAMES.get(toggle, name), on) for toggle, name, on in o.capabilities]
    return flow([*items, [(f"{o.tools} tools", S_MUTED)]], width, gap=2)


def capabilities(o: Opening, width: int) -> list[list[Part]]:
    """The same, a line each, with what the capability is in a few words."""
    named = [(toggle, NAMES.get(toggle, name), on) for toggle, name, on in o.capabilities]
    column = min(max((len(name) for _, name, _ in named), default=0), width // 3) + 4
    lines: list[list[Part]] = []
    for toggle, name, on in named:
        brief = BRIEFS.get(toggle, MCP_BRIEF if toggle.startswith("cap.mcp.") else "")
        mark, label = switch(clip(name, column - 4), on)
        lines.append([mark, label, " " * (column - 1 - cell_len(label[0])), (clip(brief, width - column), S_MUTED)])
    return lines


def model(ref: str, width: int) -> list[list[Part]]:
    """A model's name whole, on as many lines as it takes: the card is where a pick of it shows. A line ends after
    a slash where one is in reach."""
    lines: list[list[Part]] = []
    while len(ref) > width:
        cut = ref.rfind("/", 0, width) + 1 or width
        lines.append([(ref[:cut], S_HEAD)])
        ref = ref[cut:]
    return [*lines, [(ref, S_HEAD)]]


def tools(o: Opening, width: int) -> list[list[Part]]:
    """The tools' card: how many the session has and which models work with them (a run that hands over keeps its
    tools), then each capability over what it is, in a few words."""
    shared = "for the frontier and efficient models" if o.handoff else "for the frontier model"
    lines = paragraph(f"{o.tools} tools {shared}", width)
    lines.append([])
    for toggle, name, on in o.capabilities:
        brief = BRIEFS.get(toggle, MCP_BRIEF if toggle.startswith("cap.mcp.") else "")
        lines.append(switch(clip(NAMES.get(toggle, name), width - 2), on))
        if brief:
            lines.append([("  ", ""), (clip(brief, width - 2), S_MUTED)])
    return lines


# -- the played run -------------------------------------------------------------------------------------------


def phase_at(t: float) -> tuple[Phase, float]:
    """Where the played run is `t` seconds in: its phase, and how far through that phase, 0 to 1."""
    t %= RUN_LOOP_S
    start = 0.0
    for end, phase in RUN_PHASES:
        if t < end:
            return phase, (t - start) / (end - start)
        start = end
    return "done", 1.0


def pips(done: int, lit: bool) -> list[Part]:
    """The turns a model has taken in the played run, a pip each."""
    return [("▰" * done, BRAND if lit else S_MUTED), ("▱" * (RUN_TURNS - done), S_RULE)]


def run_frame(o: Opening, width: int, t: float | None) -> Content:
    """System 2's card on the wide page, its run played `t` seconds in (`RunPlay`): the frontier model, with a spinner
    and its turns filling up while it works; the hand-over, the decision model's P(ready) rising to the moment it
    hands over; then the efficient model finishing the rest. While hand-over is off the efficient model is not there,
    and the frontier model works every turn. `t` None, where motion is off: what each model does, still."""
    phase, through = phase_at(t) if t is not None else ("done", 1.0)
    if not o.handoff and t is not None:  # one model, the whole run
        phase, through = ("frontier", (t % RUN_LOOP_S) / RUN_LOOP_S) if t % RUN_LOOP_S < 9.0 else ("done", 1.0)
    spin: Part = (SPINNER[int((t or 0) / RUN_TICK_S) % len(SPINNER)] + " ", BRAND)
    done: Part = ("✓ ", S_OK)
    frontier_turns = min(RUN_TURNS, int(through * RUN_TURNS) + 1) if phase == "frontier" else RUN_TURNS
    efficient_turns = {"efficient": min(RUN_TURNS, int(through * RUN_TURNS) + 1), "done": RUN_TURNS}.get(phase, 0)
    if t is None:
        f_said: list[Part] = [("does the hard part" if o.handoff else "does every turn", S_MUTED)]
        e_said: list[Part] = [("finishes the rest, for much less", S_MUTED)]
    else:
        working = "working on the hard part" if o.handoff else "working on every turn"
        f_said = (
            [spin, (working, S_INK), "  ", *pips(frontier_turns, True)]
            if phase == "frontier"
            else [done, ("hard part done" if o.handoff else "done", S_MUTED), "  ", *pips(RUN_TURNS, False)]
        )
        e_said = {
            "efficient": [spin, ("finishing the rest", S_INK), "  ", *pips(efficient_turns, True)],
            "done": [done, ("done, for much less", S_MUTED), "  ", *pips(RUN_TURNS, False)],
        }.get(phase, [("○ waits for the hand-over", S_MUTED)])
    head: list[Part] = [(" Frontier ", S_TAB), *(["  ", *thinking_box(o.thinking, o.levels)] if o.thinking else [])]
    lines: list[list[Part]] = [head, *model(o.system2, width), f_said, [], handover_line(o, phase, through, t is None)]
    if o.handoff:
        lines += [[], [(" Efficient ", S_TAB)], *model(o.handoff, width), e_said]
    return Content("\n").join(Content.assemble(*line).truncate(width, ellipsis=True) for line in lines)


def handover_line(o: Opening, phase: Phase, through: float, still: bool) -> list[Part]:
    """The hand-over between the two models, on a chip in the accent while a run hands over and in the gray while it
    does not: the decision model's P(ready) rising turn by turn, the moment it hands over, then the turn it did."""
    if not o.handoff:
        return [(" hand-over ", S_BOX_MUTED), (f"  {HANDOVER_OFF}", S_MUTED)]
    chip: Part = (" hand-over ", S_TAB)
    if still:
        return [chip, ("  on", S_BRAND_BOLD)]
    if phase == "frontier":
        steps = [READY_FROM + (READY_AT - 0.1 - READY_FROM) * (i / RUN_TURNS) for i in range(1, RUN_TURNS + 1)]
        seen = steps[: min(RUN_TURNS, int(through * RUN_TURNS) + 1)]
        trail = "".join(LADDER[max(0, math.ceil(p * len(LADDER)) - 1)] for p in seen)
        return [chip, ("  ready ", S_MUTED), (trail, BRAND), (f" {seen[-1]:.2f}", S_INK)]
    if phase == "handover":
        return [
            chip,
            ("  ready ", S_MUTED),
            (f"{READY_AT:.2f}", S_INK),
            (f"  {HANDOVER_SIGN} hands over", S_BRAND_BOLD),
        ]
    return [chip, (f"  handed over at turn {RUN_TURNS}", S_MUTED)]


class RunPlay(Static):
    """System 2's card on the wide page, its run played over and over (`run_frame`); still where motion is switched
    off (`TEXTUAL_ANIMATIONS=none`)."""

    def __init__(self) -> None:
        super().__init__(id="welcome-system2", classes="card")
        self.border_title = "System 2"
        self._opening: Opening | None = None
        self._width = 0
        self._began = time.monotonic()

    def on_mount(self) -> None:
        self.set_interval(RUN_TICK_S, self._tick)

    def show(self, opening: Opening, width: int) -> None:
        self._opening, self._width = opening, width
        self._tick()

    def _tick(self) -> None:
        if self._opening is None or not self._width or not self.display:
            return
        still = self.app.animation_level == "none"
        self.update(run_frame(self._opening, self._width, None if still else time.monotonic() - self._began))


def system1(o: Opening, width: int) -> list[list[Part]]:
    """System 1's card: the decision model and where it is served, its jobs, then what it judges from."""
    name, served = system1_parts(o.system1)
    head: list[list[Part]] = [[(" Decision ", S_TAB)], *model(name, width), [(served or "the decision model", S_MUTED)]]
    return [*head, [], *jobs(o, width), [], *paragraph(STATE_SHORT, width)]


def systems(o: Opening, width: int, *, roomy: bool = True) -> Content:
    """The models in one column, each under its tab: the frontier model and, while a run hands over, the efficient
    one it hands over to, the decision model and its jobs; then the capabilities. `roomy`, the State has its lines too
    and a capability its few words; else what Intern is comes first, in a line or two, a job is one line, the
    capabilities run together, and where the session works comes last, there being no mascot to put it under."""
    model1, served = system1_parts(o.system1)
    head: list[Part] = [(" Frontier  ", S_TAB), "  ", (o.system2, S_HEAD)]
    box = [thinking_box(o.thinking, o.levels)] if o.thinking else []
    models: list[list[Part]] = [
        *flow([head, *box], width, gap=2),  # the box on a line of its own where it would wrap
        *([[(" Efficient ", S_TAB), "  ", (model_label(o.handoff), S_HEAD)]] if o.handoff else []),
    ]
    lines: list[list[Part]] = [
        *([] if roomy else [*paragraph(ABOUT_SHORT if o.handoff else ABOUT_ALONE, width, S_INK), []]),
        *models,
        [(" Decision  ", S_TAB), "  ", (model1, S_HEAD), (f" · {served}" * bool(served), S_MUTED)],
        *jobs(o, width, wrap=roomy),
    ]
    if roomy:
        lines += [[], [(" State ", S_TAB), ("  what the decision model reads", S_MUTED)], *paragraph(STATE, width)]
        lines += [[], *capabilities(o, width), [(f"{o.tools} tools", S_MUTED)] * bool(o.capabilities)]
    else:
        lines += [[], *switches(o, width), *where(o, width)]
    return stacked(lines)


def tips(o: Opening, width: int) -> Content:
    """What to do next, then the keys worth knowing first; while there is no session, why."""
    waiting = o.note.endswith("…")
    style = S_MUTED_ITALIC if waiting else S_HEAD if o.note == SETUP_NOTE else S_BAD
    lead: Part = (o.note, style) if o.note else ("Describe a task to begin.", S_HEAD)
    return stacked(flow([[lead], *([(key, S_INK), (f" {does}", S_MUTED)] for key, does in HINTS)], width))


class Welcome(Vertical):
    """The entering page (the module's docstring)."""

    def __init__(self, opening: Opening) -> None:
        super().__init__(id="welcome")
        self.opening = opening
        self._fitted: object = None
        """The terminal's size the page was last drawn for."""
        self._typing = False

    def compose(self) -> ComposeResult:
        # Each in a `Center`: a container centres its children as one block, and these are centred one by one.
        with Center():
            yield Static(id="welcome-wordmark")
        with Center():
            yield Static(Content.styled(TITLE, f"bold {BRAND}"), id="welcome-title")
        with Center():
            yield Static(id="welcome-about")
        with Center(), Horizontal(id="welcome-row") as row:
            row.border_title = TITLE
            with Vertical(id="welcome-models"):  # System 2's two models over System 1's
                yield RunPlay()
                card = Static(id="welcome-system1", classes="card")
                card.border_title = "System 1"
                yield card
            with Vertical(id="welcome-side"):
                yield Mascot()
                yield Static(id="welcome-where")
            card = Static(id="welcome-tools", classes="card")
            card.border_title = "Tools"
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

    def typing(self, typing: bool) -> None:
        """Whether there is a prompt being written: Intern listens while there is."""
        if self._typing != typing:
            self._typing = typing
            self.query_one(Mascot).play(self._state())

    def _state(self) -> State:
        """What Intern is doing: waiting on a session that is opening, stopped at one that could not open, listening
        to a prompt being written, or ready."""
        note = self.opening.note
        if note.endswith("…"):
            return "rolling"
        if note and note != SETUP_NOTE:
            return "blocked"
        return "listening" if self._typing else "idle"

    def _refit(self, _: object) -> None:
        if self.app.size != self._fitted:
            self._draw()

    def _draw(self) -> None:
        """The richest page the terminal holds whole: the page is the first thing seen, so nothing of it may start
        above the screen."""
        o = self.opening
        columns, rows = self._fitted = self.app.size
        width, room = self.size.width or columns - 4, rows - PROMPT_ROWS
        about = about_lines(o, min(width - 2, ABOUT_WIDTH) - 2 * ABOUT_PAD)
        about_rows = len(about) + 2  # the band's air above and under
        inside = min(width, FRAME_MAX) - FRAME
        text = systems(o, inside - SIDE_WIDTH)
        inner = CARD_WIDTH - CARD_EDGES
        cards = [system1(o, inner), tools(o, inner)]
        system2_rows = run_frame(o, inner, None).plain.count("\n") + 1 + CARD_EDGE_ROWS
        system1_rows, tools_rows = (len(card) + CARD_EDGE_ROWS for card in cards)
        models_rows = system2_rows + CARD_GAP + system1_rows
        # The larger mascot where the wide page holds it and its wordmark whole.
        big = (
            width >= HERO_WIDTH + BIG_COLUMNS - SPRITE_COLUMNS
            and HERO_ROWS + about_rows + max(BIG_ROWS, models_rows, tools_rows) <= room
        )
        mascot_columns, mascot_rows = (BIG_COLUMNS, BIG_ROWS) if big else (SPRITE_COLUMNS, SPRITE_ROWS)
        tall = max(mascot_rows, models_rows, tools_rows)
        hero = HERO_ROWS + about_rows + tall
        framed = FRAMED_ROWS + about_rows + max(SIDE_ROWS, text.plain.count("\n") + 1)
        if width >= HERO_WIDTH and hero - WORDMARK_ROWS <= room:
            look, needs = "hero", hero
        elif inside - SIDE_WIDTH >= TEXT_WIDTH and framed - WORDMARK_ROWS <= room:
            look, needs = "framed", framed
        else:
            look, needs = "compact", room
        short = needs > room  # all of it but the wordmark
        grand = look == "hero" and width >= GRAND_WIDTH and needs + WORDMARK_ROWS <= room  # a wordmark twice the size
        needs += WORDMARK_ROWS * (grand - short)
        hero_look = look == "hero"
        for name in ("hero", "framed", "compact"):
            self.set_class(name == look, name)
        self.set_class(short, "short")
        self.query_one("#welcome-wordmark", Static).update(Content("\n").join(wordmark(2 if grand else 1)))
        self.styles.margin = (1 + max(0, room - needs) // RISE, 0, 1, 0) if look != "compact" else (0, 0, 0, 0)
        # What Intern is, on a band as wide as the row under it.
        band = self.query_one("#welcome-about", Static)
        band.update(Content("\n").join(about))
        band.styles.width = 2 * CARD_WIDTH + 2 * SIDE_GAP + mascot_columns if hero_look else min(width, FRAME_MAX)
        self.query_one("#welcome-where", Static).update(stacked(where(o, SPRITE_COLUMNS)))
        foot: list[Part] = [part for line in where(o, FOOT_WIDTH) for part in (*line, ("  ·  ", S_MUTED))][:-1]
        self.query_one("#welcome-foot", Static).update(Content.assemble(*foot))
        self.query_one("#welcome-tips", Static).update(tips(o, min(width, FRAME_MAX) - 2))
        # The wide page: System 2's card over System 1's on the left, the tools on the right, the two sides as tall as
        # each other (System 1's card takes what is over), and the mascot at their middle. A narrower page has the
        # models and the tools in one column, in System 1's place.
        run, models = self.query_one(RunPlay), self.query_one("#welcome-system1", Static)
        run.display = hero_look
        run.show(o, inner)
        models.update(
            stacked(cards[0]) if hero_look else text if look == "framed" else systems(o, width - 4, roomy=False)
        )
        models.styles.height = tall - system2_rows - CARD_GAP if hero_look else "auto"
        right = self.query_one("#welcome-tools", Static)
        right.update(stacked(cards[1]))
        right.styles.height = tall if hero_look else "auto"
        mascot = self.query_one(Mascot)
        mascot.fit(mascot_columns, mascot_rows)
        mascot.play(self._state())
        mascot.styles.margin = ((tall - mascot_rows) // 2 if hero_look else 0, 0, 0, 0)

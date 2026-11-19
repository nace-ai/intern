"""The entering page: what `intern` opens on, until the first prompt.

                              ██ ███  ██ ████████ ███████ ██████  ███  ██
                              ██ ████ ██    ██    █████   ██████  ████ ██
                              ██ ██ ████    ██    ███████ ██  ██  ██ ████
                              Intern v0.34.1

            Intern starts each task on a frontier model, which does the hard part. An efficient model
            then finishes the rest, for much less. A decision model picks the moment ...

    ╭─ Frontier model ────────────────────────────╮         ╭─ Efficient model ───────────────────────────╮
    │ claude-opus-5-5                             │         │ Claude Haiku 5.5                            │
    │ thinking high ▰▰▰▱▱                         │  ━━━━▶  │                                             │
    │ ⠋ working on the hard part                  │hand-over│ ○ waits for the hand-over                   │
    │ ▰▰▱▱                                        │   on    │ ▱▱▱▱                                        │
    ╰─────────────────────────────────────────────╯         ╰─────────────────────────────────────────────╯
    Decision model · watching the run   ready ▂▄ 0.48

    ╭─ Tools ──────────────────────────────╮                    ╭─ Decision model ─────────────────────╮
    │ 9 tools, the same for both models    │    (the mascot,    │ drex-max                             │
    │                                      │     glancing       │ console                              │
    │ ● base tools   files and the shell   │     and blinking)  │                                      │
    │ ○ web search   searches the web ...  │                    │ ● hand-over   picks when to hand over│
    │ ...                                  │                    │ ● watch       nudges it out of ...   │
    ╰──────────────────────────────────────╯                    ╰──────────────────────────────────────╯
                               ~/work/parser · session 20261108-21...

            Describe a task to begin.   /help commands   /models models   /caps capabilities ...

The page is drawn as one: first the run as Intern makes it, played over and over (`RunPlay`): the frontier model
(System 2) in its box works the hard part, the decision model (System 1) judges it ready and the run hands over, and
the efficient model in the other box finishes the rest. Under the arrow between the boxes, whether a run hands over
at all: `hand-over` on a chip in the accent over `on`, or in the gray over `off`. Under it Intern itself, the Nace
cube with eyes, alive as the brand draws it (`Mascot`), between two cards: the tools the session has switched on,
which both models share, each in a few words; and the decision model's jobs, each on or off as a capability is, and
what it judges from. Above them the wordmark, in ink with the accent for its shadow, and what Intern is, in a
paragraph; under them where the session works, then what to do next.
All of it centred, and a third of the way down a tall terminal.

That is the page on a wide terminal (`hero`). A narrower one has the mascot and the models in one frame
(`framed`); one too small for that, the frame alone, a line a job (`compact`). It is the transcript's first block, so
what is said before the first prompt (a session that cannot open, a switch that failed) shows under it, and it goes
when the conversation begins.
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
    S_HEAD_MUTED,
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
    "Intern starts each task on a frontier model, which does the hard part. An efficient model then finishes the "
    "rest, for much less. A small decision model picks the moment to hand over, and keeps the work on track: it "
    "nudges your intern out of circles, searches code and files for it, and decides what it keeps in mind when its "
    "context fills up."
)
"""What Intern is, in three sentences."""
ABOUT_SHORT = (
    "A frontier model does the hard part, an efficient model finishes it, and a decision model picks the moment."
)
"""The same for a small terminal."""
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
HANDOVER_OFF = "off · /efficient picks a model"
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
RUN_ROWS = 9
"""The played run's rows: its boxes, the decision model's line under them, the air above."""
ARROW = 11
"""The hand-over's arrow between the two boxes, in cells."""
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
    """The tools' card: how many the session has, which both models work with, then what they are."""
    return [[(clip(f"{o.tools} tools, the same for both models", width), S_MUTED)], [], *capabilities(o, width)]


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


def boxed(title: str, lines: list[list[Part]], width: int, lit: bool) -> list[Content]:
    """`lines` in a rounded box `width` cells wide, `title` on its top edge: in the accent while its model works
    (`lit`), else in the rule's colour."""
    edge, inner, head = BRAND if lit else S_RULE, width - 4, f" {title} "
    rows = [
        Content.assemble(
            ("╭─", edge), (head, S_BRAND_BOLD if lit else S_HEAD_MUTED), ("─" * (width - 3 - len(head)) + "╮", edge)
        )
    ]
    for line in lines:
        body = Content.assemble(*line).truncate(inner, ellipsis=True)
        rows.append(Content.assemble(("│ ", edge), body, " " * (inner - body.cell_length), (" │", edge)))
    rows.append(Content.styled("╰" + "─" * (width - 2) + "╯", edge))
    return rows


def pips(done: int, lit: bool) -> list[Part]:
    """The turns a model has taken in the played run, a pip each."""
    return [("▰" * done, BRAND if lit else S_MUTED), ("▱" * (RUN_TURNS - done), S_RULE)]


def run_frame(o: Opening, width: int, t: float | None) -> Content:
    """The played run `t` seconds in (`RunPlay`): two boxes, the frontier model's and the efficient model's, the one
    whose turn it is in the accent with a spinner and its turns filling up, a hand-over arrow between them that fills
    as the run hands over, and under them what the decision model is doing. `t` None, where motion is off: what each
    model does, still. With no efficient model the frontier model works the whole run."""
    box = (width - ARROW) // 2
    phase, through = phase_at(t) if t is not None else ("done", 1.0)
    if not o.handoff and t is not None:  # one model, the whole run
        phase, through = ("frontier", (t % RUN_LOOP_S) / RUN_LOOP_S) if t % RUN_LOOP_S < 9.0 else ("done", 1.0)
    spin: Part = (SPINNER[int((t or 0) / RUN_TICK_S) % len(SPINNER)] + " ", BRAND)
    done: Part = ("✓ ", S_OK)
    thinking: list[Part] = thinking_box(o.thinking, o.levels) if o.thinking else []
    frontier_turns = min(RUN_TURNS, int(through * RUN_TURNS) + 1) if phase == "frontier" else RUN_TURNS
    efficient_turns = {"efficient": min(RUN_TURNS, int(through * RUN_TURNS) + 1), "done": RUN_TURNS}.get(phase, 0)
    if t is None:
        f_said: list[Part] = [("starts each task and does the hard part", S_MUTED)]
        e_said: list[Part] = [("finishes the rest, for much less", S_MUTED)]
        f_pips: list[Part] = []
        e_pips: list[Part] = []
    else:
        f_said = (
            [spin, ("working on the hard part", S_INK)] if phase == "frontier" else [done, ("hard part done", S_MUTED)]
        )
        if not o.handoff:
            f_said = [spin, ("working on every turn", S_INK)] if phase == "frontier" else [done, ("done", S_MUTED)]
        e_said = {
            "efficient": [spin, ("finishing the rest", S_INK)],
            "done": [done, ("done, for much less", S_MUTED)],
        }.get(phase, [("○ waits for the hand-over", S_MUTED)])
        f_pips, e_pips = pips(frontier_turns, phase == "frontier"), pips(efficient_turns, phase == "efficient")
    if not o.handoff:
        e_said, e_pips = [("○ no hand-over · /efficient picks a model", S_MUTED)], []
    left = boxed(
        "Frontier model",
        [[(o.system2, S_HEAD)], thinking, [], f_said, f_pips],
        box,
        lit=t is not None and phase == "frontier",
    )
    right = boxed(
        "Efficient model",
        [[(o.handoff or "off", S_HEAD if o.handoff else S_MUTED)], [], [], e_said, e_pips],
        box,
        lit=t is not None and phase == "efficient",
    )
    # The arrow, on the boxes' middle row: filling in the accent as the run hands over, in the accent once it has.
    shaft = ARROW - 4
    filled = {"frontier": 0, "handover": round(through * shaft)}.get(phase, shaft) if o.handoff else 0
    arrow = Content.assemble(
        "  ", ("━" * filled, BRAND), ("━" * (shaft - filled), S_RULE), ("▶", BRAND if filled == shaft else S_RULE), " "
    )
    gap = Content(" " * ARROW)
    # Whether a run hands over, at a glance: the word on a chip in the accent while it does, in the gray while not.
    chip = Content.styled(" hand-over ".center(ARROW), S_TAB if o.handoff else S_BOX_MUTED)
    state = Content.styled(("on" if o.handoff else "off").center(ARROW), S_BRAND_BOLD if o.handoff else S_MUTED)
    middle = len(left) // 2
    between = {middle: arrow, middle + 1: chip, middle + 2: state}
    lines = [Content.assemble(a, between.get(i, gap), b) for i, (a, b) in enumerate(zip(left, right, strict=True))]
    return Content("\n").join([*lines, decision_line(o, phase, through, t is None)])


def decision_line(o: Opening, phase: Phase, through: float, still: bool) -> Content:
    """What the decision model is doing in the played run, under the boxes: watching P(ready) rise, handing over, then
    keeping the work on track."""
    head: Part = ("Decision model", S_HEAD_MUTED)
    if still or not o.handoff:
        said = "picks the moment to hand over" if o.handoff else "keeps the work on track"
        return Content.assemble(head, (f" · {said}", S_MUTED))
    if phase == "frontier":
        steps = [READY_FROM + (READY_AT - 0.1 - READY_FROM) * (i / RUN_TURNS) for i in range(1, RUN_TURNS + 1)]
        seen = steps[: min(RUN_TURNS, int(through * RUN_TURNS) + 1)]
        trail = "".join(LADDER[max(0, math.ceil(p * len(LADDER)) - 1)] for p in seen)
        return Content.assemble(
            head, (" · watching the run   ready ", S_MUTED), (trail, BRAND), (f" {seen[-1]:.2f}", S_INK)
        )
    if phase == "handover":
        return Content.assemble(
            head,
            (" · ready ", S_MUTED),
            (f"{READY_AT:.2f}", S_INK),
            (f"   {HANDOVER_SIGN} hands over to the efficient model", S_BRAND_BOLD),
        )
    if phase == "efficient":
        return Content.assemble(head, (" · keeps the work on track", S_MUTED))
    return Content.assemble(("Done. ", S_INK), ("The frontier model takes the next task.", S_MUTED))


class RunPlay(Static):
    """The run Intern makes, played over and over on the entering page (`run_frame`); still where motion is switched
    off (`TEXTUAL_ANIMATIONS=none`)."""

    def __init__(self) -> None:
        super().__init__(id="welcome-run")
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
    """The decision model's card: the model it is on and where it is served, its jobs, then what it judges from."""
    name, served = system1_parts(o.system1)
    head: list[list[Part]] = [*model(name, width), [(served or "the decision model", S_MUTED)], []]
    return [*head, *jobs(o, width), [], *paragraph(STATE_SHORT, width)]


def systems(o: Opening, width: int, *, roomy: bool = True, played: bool = True) -> Content:
    """The models in one column, each under its tab: the frontier model and the efficient one it hands over to, the
    decision model and its jobs; then the capabilities. `roomy`, the State has its lines too and a capability its few
    words; else what Intern is comes first, in a line or two, a job is one line, the capabilities run together, and
    where the session works comes last, there being no mascot to put it under."""
    model1, served = system1_parts(o.system1)
    head: list[Part] = [(" Frontier  ", S_TAB), "  ", (o.system2, S_HEAD)]
    box = [thinking_box(o.thinking, o.levels)] if o.thinking else []
    handoff = model_label(o.handoff) if o.handoff else "off · /efficient"
    # With the room, the frontier and efficient models are in the played run's boxes above (`played`): here, the
    # tools.
    models: list[list[Part]] = [
        *flow([head, *box], width, gap=2),  # the box on a line of its own where it would wrap
        [(" Efficient ", S_TAB if o.handoff else S_BOX_MUTED), "  ", (handoff, S_HEAD if o.handoff else S_MUTED)],
    ]
    lines: list[list[Part]] = [
        *([] if roomy else [*paragraph(ABOUT_SHORT, width, S_INK), []]),
        *([] if roomy and played else [*models, *([[]] if roomy else [])]),
        *([*capabilities(o, width), [(f"{o.tools} tools", S_MUTED)] * bool(o.capabilities), []] if roomy else []),
        [(" Decision  ", S_TAB), "  ", (model1, S_HEAD), (f" · {served}" * bool(served), S_MUTED)],
        *jobs(o, width, wrap=roomy),
    ]
    if roomy:
        lines += [[], [(" State ", S_TAB), ("  what the decision model reads", S_MUTED)], *paragraph(STATE, width)]
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
        with Center():
            yield RunPlay()
        with Center(), Horizontal(id="welcome-row") as row:
            row.border_title = TITLE
            card = Static(id="welcome-system2", classes="card")
            card.border_title = "Tools"
            yield card
            with Vertical(id="welcome-side"):
                yield Mascot()
                yield Static(id="welcome-where")
            card = Static(id="welcome-system1", classes="card")
            card.border_title = "Decision model"
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
        about = paragraph(ABOUT, min(width - 2, ABOUT_WIDTH), S_INK)
        inside = min(width, FRAME_MAX) - FRAME
        texts = {played: systems(o, inside - SIDE_WIDTH, played=played) for played in (True, False)}
        cards = [tools(o, CARD_WIDTH - CARD_EDGES), system1(o, CARD_WIDTH - CARD_EDGES)]
        card_rows = max(len(card) for card in cards) + CARD_EDGE_ROWS  # the cards are one height
        # The larger mascot where the wide page holds it and its wordmark whole.
        big = (
            width >= HERO_WIDTH + BIG_COLUMNS - SPRITE_COLUMNS
            and HERO_ROWS + RUN_ROWS + len(about) + max(BIG_ROWS, card_rows) <= room
        )
        mascot_columns, mascot_rows = (BIG_COLUMNS, BIG_ROWS) if big else (SPRITE_COLUMNS, SPRITE_ROWS)
        tall = max(mascot_rows, card_rows)
        hero = HERO_ROWS + RUN_ROWS + len(about) + tall
        # The framed page plays the run where it has the rows for it; without them, the models are in the frame.
        framed = {
            played: FRAMED_ROWS + RUN_ROWS * played + len(about) + max(SIDE_ROWS, text.plain.count("\n") + 1)
            for played, text in texts.items()
        }
        fits = inside - SIDE_WIDTH >= TEXT_WIDTH
        if width >= HERO_WIDTH and hero - WORDMARK_ROWS <= room:
            look, needs, played = "hero", hero, True
        elif fits and framed[True] - WORDMARK_ROWS <= room:
            look, needs, played = "framed", framed[True], True
        elif fits and framed[False] - WORDMARK_ROWS <= room:
            look, needs, played = "framed", framed[False], False
        else:
            look, needs, played = "compact", room, False
        text = texts[played]
        short = needs > room  # all of it but the wordmark
        grand = look == "hero" and width >= GRAND_WIDTH and needs + WORDMARK_ROWS <= room  # a wordmark twice the size
        needs += WORDMARK_ROWS * (grand - short)
        for name in ("hero", "framed", "compact"):
            self.set_class(name == look, name)
        self.set_class(short, "short")
        self.query_one("#welcome-wordmark", Static).update(Content("\n").join(wordmark(2 if grand else 1)))
        self.styles.margin = (1 + max(0, room - needs) // RISE, 0, 1, 0) if look != "compact" else (0, 0, 0, 0)
        self.query_one("#welcome-about", Static).update(stacked(about))
        # As wide as the cards' row under it on the wide page, as the frame on a narrower one.
        row = 2 * CARD_WIDTH + 2 * SIDE_GAP + mascot_columns
        run = self.query_one(RunPlay)
        run.display = played
        run.show(o, min(width - 2, row if look == "hero" else FRAME_MAX))
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
        mascot = self.query_one(Mascot)
        mascot.fit(mascot_columns, mascot_rows)
        mascot.play(self._state())
        mascot.styles.margin = ((tall - mascot_rows) // 2 if look == "hero" else 0, 0, 0, 0)

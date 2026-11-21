"""The TUI's widgets: the status bar, the transcript and its blocks, System 1's cards in it (`system1_panel.py`
has the System 1 panel, `state_panel.py` the State panel, `welcome.py` the entering page).

Text from the model goes through `Content` as plain text, never through markup, so a `[` in a command or a
reply cannot style the screen.
"""

from __future__ import annotations

import ctypes
import functools
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import textwrap
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import unquote

from rich.cells import cell_len, set_cell_size
from textual import events
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.color import Color
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.geometry import Offset
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Markdown, Static, TextArea
from textual.widgets.markdown import MarkdownStream
from textual.widgets.text_area import Selection

from intern import __version__
from intern.config import KNOWN_SYSTEM2
from intern.events import ProcessVerdict, ToolCallEnd, ToolCallStart
from intern.extensions.guard import ASKED_THRESHOLD, RISK_THRESHOLD
from intern.handover_record import InShort
from intern.messages import JsonObject
from intern.processes import ENDED_LINES, duration_text
from intern.supervise import STRIKES
from intern.tools.images import IMAGE_SUFFIXES
from intern.tui.ledger import (
    CompactionJudgement,
    GuardJudgement,
    HandoverJudgement,
    KeepVerdict,
    RuleRefusal,
    TurnJudgement,
)
from intern.tui.outline import EMPHASIS, LIST_ITEM, QUOTE, Outline, Status, parse_state, plain
from intern.tui.theme import (
    BRAND,
    CARD,
    DARK,
    INK,
    LIGHT,
    PAPER,
    S_BAD,
    S_BAD_BOLD,
    S_BOX,
    S_BOX_MUTED,
    S_BRAND_BOLD,
    S_HEAD,
    S_HEAD_MUTED,
    S_INK,
    S_MUTED,
    S_MUTED_ITALIC,
    S_OK,
    S_OK_BOLD,
    S_RULE,
    S_TAB,
    S_WARN,
    S_WARN_BOLD,
    S_WORDMARK,
    WORDMARK,
)

TITLE_ARGUMENTS = ("path", "command", "pattern", "query", "url", "question")
TITLE_CHARS = 72
BRIEF_CELLS = 44
"""A command named in passing: enough to know it by."""
HANDLE_CHARS = 8
"""A job's handle, as far as a reader needs it to tell two jobs apart."""
ENDED = re.compile(
    r"Background job \S+ \(`(?P<job>.*?)`\) (?P<fate>ended: [^\n]*|is from before [^\n]*)"
    r"|A process the command `(?P<left>.*?)` left running \(process group \d+\) (?P<gone>ended after [^;\n]*)",
    re.S,
)
"""A process's line in an end notice: the command, which may run over lines, and what became of it."""
CD = re.compile(r"""^cd (?:"[^"]*"|'[^']*'|\S+) && """)
OUTPUT_CHARS = 3_000
FLUSH_S = 0.08
METER_WIDTH = 10
JOB = "»"
"""Before a command that goes on running, wherever one is listed: a character every font has at a cell's width (an
hourglass comes from a fallback font, wider and in its own colour)."""
FILLED, TRACK = "━", "─"
PIP_ON, PIP_OFF = "▰", "▱"
"""The thinking box's meter: a pip per level the model takes, filled up to the one in force."""
SPRITES = Path(__file__).with_name("sprites")
"""Intern, the Nace cube with eyes, as the brand draws it (the Nace brand playground's Intern kit,
`pnpm export --intern-kit`): a sheet of square frames side by side for each of its states (`<state>.png`), cut to the
square the cube and its motion keep to, one camera for all, so the cube stands in the same place in every state;
`poses.png`, its eyes at rest, glancing left, glancing right and shut (`EYES`) in that camera; `mark.png`, the cube
alone with its eyes shut away, which is the Nace mark, for the masthead; and `sprites.json`, which says for each
tick of a state's loop which
frame shows (`sequence`; a frame the loop holds is stored once). A sheet has two channels: the first is which colour
(white the ink, black the accent), the second how much of it. `frame` scales a frame to the cells and colours it for
the look in force."""
MARK_COLUMNS, MARK_ROWS = 12, 6
"""The masthead's mark in cells, two pixels to a cell down, so as wide in pixels as it is tall: it stands under the
masthead's first row.
It is the brand's drawing scaled down, each pixel the mean of what it covers: at this size a cube drawn by hand in
whole pixels, its window's frame thinner than one, reads as a blob."""
EYES = ("rest", "left", "right", "shut")
"""The mark's poses: its eyes at rest, glancing left, glancing right and shut."""
EYE_PIXELS = (
    ((6, 3), (6, 4), (8, 2), (8, 3)),
    ((5, 3), (5, 4), (7, 2), (7, 3)),
    ((7, 3), (7, 4), (9, 2), (9, 3)),
    ((6, 4), (8, 3)),
)
"""The masthead mark's eyes in each pose, as (column, row) pixels in its window: each a pixel across and two down,
the right a pixel higher as the face tilts, a pixel to the side when it glances and half as tall when shut. Whole
pixels, drawn by hand: the brand's eyes scaled to this size are two smudges."""
ART = (
    "██ ███  ██ ████████ ███████ ██████  ███  ██",
    "██ ████ ██    ██    ██      ██   ██ ████ ██",
    "██ ██ ████    ██    █████   ██████  ██ ████",
    "██ ██  ███    ██    ██      ██   ██ ██  ███",
    "██ ██   ██    ██    ███████ ██   ██ ██   ██",
)
"""The wordmark in block letters, in ink as the mark is: the entering page's, and the masthead's on a wide terminal."""
SHADOW = BRAND
"""The letters' shadow (`wordmark`): the accent, the one colour beside the ink."""
LOOP_S = 8.0
"""The mark's loop: it glances left, then right, and blinks twice."""
FLASH_S = 1.5
LABEL_WIDTH = 11
"""The masthead's label column: `Efficient` and room after it."""
LETTERS_WIDTH = MARK_COLUMNS + 3 + len(ART[0]) + 1 + 4
"""The mark, the block letters and the air after each: what the masthead's table needs beside it to stand with the
letters."""
COLUMN_GAP = 3
SEP = "  ·  "


Part = str | tuple[str, str]


def merged(cells: list[tuple[str, str]]) -> list[Part]:
    """Cells of (text, style) as parts, neighbours in one style joined: a row of pixels is a few runs."""
    parts: list[tuple[str, str]] = []
    for text, style in cells:
        if parts and parts[-1][1] == style:
            parts[-1] = (parts[-1][0] + text, style)
        else:
            parts.append((text, style))
    return [(text, style) if style else text for text, style in parts]


def pixels(grid: list[list[str]], page: str) -> list[list[Part]]:
    """A grid of pixel colours (`""` where the page shows) as rows of cells, two pixels to a cell, one over the
    other: the upper is the cell's background and the lower a lower half block on it, in `page`'s colour where the
    page shows under a pixel. Never an upper half block: a terminal draws a cell's background exactly and a glyph
    where its font puts it, and macOS Terminal, which sets its lines apart, starts an upper half block short of the
    cell's top (the gap shows whatever is behind) while a lower one ends at the cell's bottom."""
    rows: list[list[Part]] = []
    for upper, lower in zip(grid[::2], grid[1::2], strict=True):
        cells: list[tuple[str, str]] = []
        for top, bottom in zip(upper, lower, strict=True):
            if top == bottom:
                cells.append((" ", f"on {top}" if top else ""))
            else:
                cells.append(("▄", f"{bottom or page} on {top}" if top else bottom))
        rows.append(merged(cells))
    return rows


@functools.cache
def wordmark(scale: int = 1, page: str = PAPER) -> list[Content]:
    """The block letters, one `Content` per row, in ink, with a shadow in the accent to their right
    and, at `scale` 2, twice the size for a terminal with the room, a row of cells down too. The shadow is whole
    cells, as the letters are: half a row under them would be a half block over the open page, whose edge macOS
    Terminal draws as a hairline. `page` is what it stands on."""
    width, height = (len(ART[0]) + 1) * scale, (2 * len(ART) + 2) * scale
    grid = [[""] * width for _ in range(height)]
    for row, line in enumerate(ART):
        for column, cell in enumerate(line):
            if cell == "█":
                for y in range(2 * row * scale, 2 * (row + 1) * scale):
                    grid[y][column * scale : (column + 1) * scale] = [INK] * scale
    down = 2 * (scale - 1)  # pixels: a row of cells, or none
    for y in range(height - 1, down - 1, -1):
        for x in range(width - 1, scale - 1, -1):
            if not grid[y][x] and grid[y - down][x - scale] not in ("", SHADOW):
                grid[y][x] = SHADOW
    return [Content.assemble(*row) for row in pixels(grid, page)]


def pose_at(seconds: float) -> int:
    """Which pose the mark is in, `seconds` into its life: its eyes glance left, then right,
    and blink twice, each loop."""
    at = seconds % LOOP_S / LOOP_S
    if 0.165 <= at < 0.365:
        return EYES.index("left")
    if 0.485 <= at < 0.665:
        return EYES.index("right")
    if 0.85 <= at < 0.87 or 0.905 <= at < 0.925:
        return EYES.index("shut")
    return EYES.index("rest")


@functools.cache
def reel() -> dict[str, Any]:
    """`sprites.json`: the poses' order, and each state's frames and `sequence`."""
    return json.loads((SPRITES / "sprites.json").read_text())


@functools.cache
def sheet(name: str) -> Any:
    from PIL import Image

    with Image.open(SPRITES / f"{name}.png") as image:
        return image.convert("LA")


def shrunk(name: str, index: int, columns: int, rows: int, page: str, ink: str, accent: str) -> list[list[str]]:
    """Frame `index` of a sheet (a state's, `poses` or `mark`) as a grid of pixel colours, `columns` by twice `rows`,
    in `ink` and `accent` on a page of the colour `page` (`""` where the page shows): each pixel the mean of what it
    covers of the drawing, so the cube's edge is a blend with the page and not a step."""
    from PIL import Image

    sprites = sheet(name)
    size = sprites.height
    which, how_much = sprites.crop((index * size, 0, (index + 1) * size, size)).split()
    drawn = Image.composite(Image.new("RGB", which.size, ink), Image.new("RGB", which.size, accent), which)
    paper = Image.new("RGB", which.size, page)
    paper.paste(drawn, mask=how_much)
    data = paper.resize((columns, 2 * rows), Image.Resampling.BOX).tobytes()
    pixel = [f"#{data[at : at + 3].hex()}" for at in range(0, len(data), 3)]
    return [["" if p == page.lower() else p for p in pixel[at : at + columns]] for at in range(0, len(pixel), columns)]


SHARP = 110
"""How much of a pixel the drawing must cover, of 255, for the pixel to be drawn: a frame's pixels are ink or not, so
its edges are steps and never a blur. Under half, so the window's frame, thinner than a pixel, stays whole."""


def cut(name: str, index: int, columns: int, rows: int, ink: str, accent: str) -> list[list[str]]:
    """Frame `index` of a sheet as a grid of pixel colours, `columns` by `rows`: `ink` or `accent` where the drawing
    covers enough of the pixel (`SHARP`), `""` where the page shows."""
    from PIL import Image

    sprites = sheet(name)
    size = sprites.height
    box = sprites.crop((index * size, 0, (index + 1) * size, size)).resize((columns, rows), Image.Resampling.BOX)
    which, how_much = (channel.tobytes() for channel in box.split())
    pairs = zip(which, how_much, strict=True)
    pixel = ["" if much <= SHARP else ink if colour > 127 else accent for colour, much in pairs]
    return [pixel[at : at + columns] for at in range(0, len(pixel), columns)]


@functools.lru_cache(maxsize=1024)
def frame(
    name: str, index: int, columns: int, rows: int, page: str, ink: str, accent: str, fine: bool = False
) -> tuple[Content, ...]:
    """Frame `index` of a sheet, `columns` cells by `rows`, one `Content` per row, its pixels sharp (`cut`): six to
    a cell in sextants where the terminal draws them (`fine`), else two, one over the other."""
    if fine:
        grid = cut(name, index, 2 * columns, 3 * rows, ink, accent)
        lines = []
        for y in range(0, 3 * rows, 3):
            cells: list[tuple[str, str]] = []
            for x in range(0, 2 * columns, 2):
                drawn = [grid[y + bit // 2][x + bit % 2] for bit in range(6)]
                colours = [colour for colour in drawn if colour]
                bits = sum(bool(colour) << bit for bit, colour in enumerate(drawn))
                cells.append((sextant(bits), max(colours, key=colours.count) if colours else ""))
            lines.append(Content.assemble(*merged(cells)))
        return tuple(lines)
    grid = cut(name, index, columns, 2 * rows, ink, accent)
    open_page = [""] * columns
    for upper, lower, under in zip(grid[::2], grid[1::2], [*grid[2::2], open_page], strict=True):
        for x in range(columns):
            if upper[x] and not lower[x] and not under[x]:
                # The cube's colour over the open page: as a half block in the page's colour on the cube's, its edge
                # is a hairline there in macOS Terminal. The cell is the mean of the two instead.
                upper[x] = lower[x] = Color.parse(upper[x]).blend(Color.parse(page), 0.5).hex.lower()
    return tuple(Content.assemble(*row) for row in pixels(grid, page))


FINE_COLUMNS, FINE_ROWS = 2 * MARK_COLUMNS, 3 * MARK_ROWS
"""The mark's pixels where the terminal draws sextants: six to a cell, two across and three down."""

Eyes = tuple[tuple[int, int, str], tuple[int, int, str]]
"""The mark's two eyes in sextant pixels: where each one's box starts, and its shape (`EYE_SHAPES`)."""

EYE_SHAPES: dict[str, tuple[tuple[int, int], ...]] = {
    "open": ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)),
    "shut": ((0, 1), (1, 1)),
    "down": ((0, 1), (1, 1), (0, 2), (1, 2)),
    "cross": ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
    "plus": ((1, 0), (0, 1), (1, 1), (2, 1), (1, 2)),
}
"""An eye's pixels in its box, three by three: open, shut in a blink, lowered to read, crossed out when a run
failed, and a plus that turns into the cross and back, a turnstile, while a tool runs."""

FINE_EYES: tuple[Eyes, ...] = (
    ((11, 5, "open"), (16, 4, "open")),
    ((10, 4, "open"), (15, 4, "open")),
    ((12, 5, "open"), (17, 5, "open")),
    ((11, 5, "shut"), (16, 4, "shut")),
)
"""The eyes in each pose (`EYES`)."""

Face = Literal["idle", "reading", "turnstile", "failed"]
"""What the mark's eyes are doing: glancing and blinking; reading, while System 2 thinks; a turnstile, while a tool
runs; crossed out, for a moment after a run failed."""

READING = (0, 0, 1, 1, 2, 2, 2, 2, 1, 0)
"""How far along the line the reading eyes are, a step a tick: across, a rest at its end, and back to the next."""


def eyes_at(face: Face, seconds: float) -> tuple[Eyes, int]:
    """The eyes of a face, `seconds` in: in sextant pixels, and the pose (`EYES`) nearest it for a terminal that
    gets the mark in half blocks, whose eyes are a pixel wide."""
    tick = int(seconds / SPIN_S)
    if face == "reading":
        along = READING[tick // 2 % len(READING)]
        return ((10 + along, 4, "down"), (15 + along, 4, "down")), (EYES.index("left"), 0, EYES.index("right"))[along]
    if face == "turnstile":
        shape = ("plus", "cross")[tick // 3 % 2]
        return ((10, 4, shape), (15, 4, shape)), (0, EYES.index("shut"))[tick // 3 % 2]
    if face == "failed":
        return ((10, 4, "cross"), (15, 4, "cross")), EYES.index("shut")
    pose = pose_at(seconds)
    return FINE_EYES[pose], pose


def sextants() -> bool:
    """Whether the terminal draws the sextants (U+1FB00 and on) itself, so they meet with no gaps: the ones known
    to. Elsewhere a font may not have them, and the mark is drawn in half blocks."""
    env = os.environ
    return (
        env.get("TERM_PROGRAM") in ("ghostty", "WezTerm", "iTerm.app")
        or "KITTY_WINDOW_ID" in env
        or env.get("TERM", "").startswith(("xterm-ghostty", "xterm-kitty", "foot", "wezterm"))
    )


def sextant(bits: int) -> str:
    """The glyph with these pixels of a cell in ink: bit 0 its upper left, bit 1 its upper right, and so on down."""
    if bits in (0, 21, 42, 63):
        return {0: " ", 21: "▌", 42: "▐", 63: "█"}[bits]
    return chr(0x1FB00 + bits - 1 - (bits > 21) - (bits > 42))


@functools.cache
def fine_mark(eyes: Eyes, ink: str) -> tuple[Content, ...]:
    """The mark in sextants, three times the pixels of the half blocks: each pixel ink or not, so its edges are
    sharp, and its eyes two or three pixels wide."""
    from PIL import Image

    shape = sheet("mark").split()[1].resize((FINE_COLUMNS, FINE_ROWS), Image.Resampling.BOX)
    grid = [[shape.getpixel((x, y)) > 127 for x in range(FINE_COLUMNS)] for y in range(FINE_ROWS)]
    for x, y, drawn in eyes:
        for right, down in EYE_SHAPES[drawn]:
            grid[y + down][x + right] = True
    rows = []
    for y in range(0, FINE_ROWS, 3):
        cells = (sum(grid[y + bit // 2][x + bit % 2] << bit for bit in range(6)) for x in range(0, FINE_COLUMNS, 2))
        rows.append(Content.styled("".join(sextant(bits) for bits in cells), ink))
    return tuple(rows)


@functools.cache
def mark(
    pose: int = 0,
    page: str = LIGHT["card"],
    ink: str = LIGHT["ink"],
    fine: bool | None = None,
    eyes: Eyes | None = None,
) -> tuple[Content, ...]:
    """The mark, one `Content` per row for laying the masthead's text beside it: Intern in `ink` on the masthead's
    card, of the colour `page`, its eyes in a pose (`EYES`). In sextants where the terminal draws them (`fine`,
    by default what `sextants` says), with `eyes` when given in place of the pose's; else in half blocks, a third
    of the pixels, their edges blended, in the pose."""
    if sextants() if fine is None else fine:
        return fine_mark(eyes or FINE_EYES[pose], ink)
    grid = shrunk("mark", 0, MARK_COLUMNS, MARK_ROWS, page, ink, ink)
    for x, y in EYE_PIXELS[pose]:
        grid[y][x] = ink
    return tuple(Content.assemble(*row) for row in pixels(grid, page))


STEP_GLYPH: dict[Status, tuple[str, str]] = {
    "done": ("✓", S_OK),
    "in_progress": ("▶", S_WARN),
    "pending": ("○", S_MUTED),
    "unknown": ("·", S_MUTED),
}
MODE_STYLE = {
    "advancing": S_OK,
    "finished": S_INK,
    "re_investigating": S_WARN,
    "retrying": S_WARN,
    "off_plan": S_BAD,
}


def meter(p: float, style: str, width: int = METER_WIDTH) -> list[Part]:
    """A thin line filled to `p`, the way a slider shows a level: heavy where filled, a hairline for the rest."""
    filled = max(0, min(width, round(p * width)))
    parts: list[Part] = [(FILLED * filled, style), (TRACK * (width - filled), S_RULE)]
    return [part for part in parts if part[0]]


MODE_WORDS = {"advancing": "moving", "re_investigating": "circling", "off_plan": "off-plan", "finished": "done"}
"""What the supervisor calls a turn (`supervise.MODE`), in a word short enough for a line of the System 1 panel. The
names System 1 is asked with stay: another name is another question, and the journals have these."""


def mode_label(mode: str) -> str:
    return MODE_WORDS.get(mode, mode.replace("_", "-"))


def tokens_text(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def brief(command: str, cells: int = BRIEF_CELLS) -> str:
    """A command in a few words, for a line that is about something else (System 1's look at it, its end): its first
    line, without the `cd … &&` an agent puts before it, `…` for what is left out."""
    lines = command.strip().splitlines()
    first = CD.sub("", " ".join(lines[0].split())) if lines else ""
    return clip(first + (" …" if len(lines) > 1 and not first.endswith("…") else ""), cells)


def ended_lines(text: str) -> list[str]:
    """Each process an end notice tells the agent of (`ProcessManager.end_notice`), in a few words with how it ended:
    not the whole command, nor the output the notice quotes."""
    lines = [
        f"`{brief(m['job'])}` {m['fate'].rstrip('.')}"
        if m["job"] is not None
        else f"A process `{brief(m['left'])}` left running {m['gone']}"
        for m in ENDED.finditer(text)
    ]
    # A notice worded otherwise is shown as it was, by its lines: never dropped.
    return lines or [line for line in text.splitlines() if line.startswith(ENDED_LINES)]


def process_subject(arguments: JsonObject) -> str | None:
    """What a `process` call is about: `start` and its command, else the action and the start of the job's handle
    (the whole handle is the agent's business). None when it is not that call's arguments."""
    action, command, handle = arguments.get("action"), arguments.get("command"), arguments.get("handle")
    if not isinstance(action, str):
        return None
    if action == "start" and isinstance(command, str):
        return f"start {command.strip()}"
    return f"{action} {handle[:HANDLE_CHARS]}".strip() if isinstance(handle, str) else action


def process_result(content: str) -> tuple[str, str]:
    """A `process` call's result as a reader wants it: how the job stands (`running · 32s`) and the output the call
    read, not the JSON the agent gets (the handle, the command again, the cursors). `list`: a line a job. A result
    that is not a job's (an error) is left as it is."""
    try:
        data = json.loads(content)
    except ValueError:
        return "", content

    def stands(job: dict[str, object]) -> str:
        code, took = job.get("exit_code"), job.get("elapsed_s")
        state = str(job.get("state", "")).replace("_", " ") + (f", exit code {code}" if code is not None else "")
        return f"{state} · {duration_text(took)}" if isinstance(took, int) else state

    if isinstance(data, list):
        jobs = [job for job in data if isinstance(job, dict)]  # pyright: ignore[reportUnknownVariableType]
        return f"{len(jobs)} job{'s' if len(jobs) != 1 else ''}", "\n".join(
            f"{str(job.get('handle', ''))[:HANDLE_CHARS]}  {stands(job)}  {brief(str(job.get('command', '')))}"
            for job in jobs
        )
    if not isinstance(data, dict) or "state" not in data:
        return "", content
    left = data.get("omitted_bytes")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    more = f" · {left:,} bytes of output left out" if isinstance(left, int) and left else ""
    return stands(data) + more, str(data.get("output") or "")  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]


def call_title(name: str, arguments: JsonObject) -> str:
    """The tool and the argument that says what the call is about: its path, command, pattern, query, url."""
    subject = next((arguments[k] for k in TITLE_ARGUMENTS if isinstance(arguments.get(k), str)), None)
    if subject is None:
        subject = json.dumps(arguments) if arguments else ""
    text = str(subject).strip()
    line = text.splitlines()[0] if text else ""
    title = f"{name} {line}".strip()
    return title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 3] + "..."


_ESCAPE_SEQUENCE = re.compile(
    r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"  # CSI: colours, cursor moves, clear-screen
    r"|(?:\x1b\]|\x9d)[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)"  # OSC: window title, clipboard (OSC 52), links
)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def printable(text: str) -> str:
    """`text` as the terminal may be sent it: whole escape sequences removed, then every control character left but
    newline and tab. What a tool printed or a model wrote must not reach the terminal as commands (set the
    clipboard or the title, clear the screen), and Textual passes ESC through."""
    return _CONTROL_CHARACTER.sub("", _ESCAPE_SEQUENCE.sub("", text))


def bounded(text: str, limit: int = OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… ({len(text) - limit:,} more characters)"


def styled(text: str, style: str = "") -> Part:
    return (text, style) if style else text


def clip(text: str, cells: int) -> str:
    """`text` in at most `cells` cells, `…` where it was cut."""
    if cell_len(text) <= cells:
        return text
    return set_cell_size(text, max(0, cells - 1)) + "…" if cells > 0 else ""


def flow(items: list[list[Part]], width: int, gap: int = 3) -> list[list[Part]]:
    """Items on as few lines of `width` cells as hold them, `gap` apart: laid out by hand, since wrapping would
    break inside an item."""
    lines: list[list[Part]] = []
    used = 0
    for item in items:
        size = Content.assemble(*item).cell_length
        if lines and used + gap + size <= width:
            lines[-1] += [" " * gap, *item]
            used += gap + size
        else:
            lines.append(list(item))
            used = size
    return lines


def stacked(lines: list[list[Part]]) -> Content:
    """Lines of parts as one text."""
    return Content("\n").join(Content.assemble(*line) for line in lines)


# -- the status bar -----------------------------------------------------------------------------------------


def system1_parts(ref: str) -> tuple[str, str]:
    """How a System 1 ref reads on the masthead: the model, and where it is served."""
    if ref.startswith("decisions:"):
        return ref.removeprefix("decisions:"), "decisions API"
    if ref.startswith("drex:"):
        return ref.removeprefix("drex:"), "console"
    if ref.startswith("kapitsa:"):
        return ref.removeprefix("kapitsa:"), "this machine"
    return (ref, "") if ref in ("…", "none") else (ref, "console")


def thinking_box(level: str, levels: tuple[str, ...]) -> list[Part]:
    """The thinking level in a box, so Shift+Tab is seen to change it: its name, then a pip per level the model
    takes, filled up to it (none for the model's own); the box as wide at every level, so what follows stays put."""
    up_to = levels.index(level) + 1 if level in levels else 0
    width = max(len(each) for each in (*levels, level))
    return [
        (" thinking ", S_BOX_MUTED),
        (f"{level.ljust(width)} ", S_BOX),
        (PIP_ON * up_to, S_BOX),
        (PIP_OFF * (len(levels) - up_to) + " ", S_BOX_MUTED),
    ]


def cost_text(cost: float, unpriced: bool = False) -> str:
    """A system's cost so far. `unpriced`: some of its calls have no known cost (the provider reported none and
    the harness has no price for the model), so the cost is at least what is shown, or not known at all."""
    if unpriced:
        return f"≥ ${cost:.4f}" if cost >= 0.0001 else "price unknown"
    return f"${cost:.4f}" if cost >= 0.0001 else ""


def cache_text(prompt: int, cached: int, reported: bool) -> str:
    """How much of System 2's prompts the provider read from its cache; nothing from a provider that never says."""
    return f"cache {cached / prompt:.0%}" if reported and prompt else ""


def joined(parts: list[Part]) -> list[Part]:
    """Parts with a muted dot between each."""
    out: list[Part] = []
    for part in parts:
        out += [(SEP, S_MUTED), part] if out else [part]
    return out


class StatusBar(Static):
    """The masthead: the mark, the wordmark in the entering page's block letters where the terminal is wide enough
    for them (else in type, over the table), then the version and under it a table, a row a model: the frontier
    model (System 2), the efficient model a run hands over to, and the decision model (System 1); a label column,
    then the model in a column of its own, then the rest: the thinking level in its box, where the decision model is
    served; then each model's cost, in a column, the frontier model's with the share of its prompts read from the
    provider's cache. At the end of the row of the model whose turn it is, the run: a spinner and `working` while one
    is in progress, the context in use as a share of the window, and what needs attention."""

    def __init__(self) -> None:
        super().__init__("", id="status")
        self.system2 = "…"
        self.system1 = "…"
        self.handoff = ""
        """The efficient model auto mode hands a run over to; none when it hands over to none."""
        self.labels: Mapping[str, str] = {}
        """Models' display names, by ref, as the catalog lists them (`model_label`)."""
        self.handed = False
        """A run has handed over: that model's turn until the run ends."""
        self.context = 0
        self.window = 0
        self.cost2 = 0.0
        self.cost_handoff = 0.0
        """What the efficient model's turns cost, after a hand-over; `cost2` is the frontier model's."""
        self.cost1 = 0.0
        self.unpriced = False
        """Some of System 2's calls have no known cost: `cost2` is short of its cost."""
        self.prompt2 = 0
        self.cached2 = 0
        """System 2's prompt tokens this session, and those the provider read from its cache."""
        self.cache_reported = False
        """The provider reported a cache read or write: without one, `cached2` is no data, not 0%."""
        self.running = False
        self.activity = ""
        """What the run is waiting on right now, when it is not the model's turn: a State write."""
        self.note = ""
        self.watching = ""
        """The background jobs System 1 watches, at the end of its row."""
        self.thinking = ""
        """The thinking level, `default` for the model's own; empty when the model takes none."""
        self.levels: tuple[str, ...] = ()
        """The levels the model takes, lowest first."""
        self.free = ""
        """The agent's free credit left (`free $3.42 of $4.00`), while the frontier model runs on it."""
        self._flash = ""
        self._flash_timer: Timer | None = None
        self._frame = 0
        self._pose = 0
        self._eyes: Eyes = FINE_EYES[0]
        self._face: Face = "idle"
        self._face_until = 0.0
        self._card = ""
        self._born = time.monotonic()

    def on_mount(self) -> None:
        self.set_interval(SPIN_S, self._spin)

    def _spin(self) -> None:
        """The spinner's next frame while a run is in progress, and the mark's eyes, alive as the entering page's
        are; still where motion is switched off (`TEXTUAL_ANIMATIONS=none`)."""
        now = time.monotonic()
        if self._face_until and now >= self._face_until:
            self._face, self._face_until = "idle", 0.0
        still = self.app.animation_level == "none" and self._face != "failed"
        eyes, pose = (FINE_EYES[0], 0) if still else eyes_at(self._face, now - self._born)
        moved = (eyes, pose) != (self._eyes, self._pose)
        if self.running or moved or self._card != self._page():
            self._frame += self.running
            self._eyes, self._pose = eyes, pose
            self.refresh_line()

    def face(self, face: Face, hold_s: float = 0.0) -> None:
        """What the mark's eyes do from now (`Face`); with `hold_s`, for that long, then back to idle."""
        self._face = face
        self._face_until = time.monotonic() + hold_s if hold_s else 0.0

    def _page(self) -> str:
        """The card's colour in the look in force (`/dark`), which the mark's half tones blend with."""
        return self._look()["card"]

    def _look(self) -> dict[str, str]:
        """The colours of the look in force."""
        return DARK if self.is_mounted and self.app.current_theme.dark else LIGHT

    def flash(self, text: str) -> None:
        """A few words after the run's context for a moment: what just went to the clipboard."""
        self._flash = text
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = self.set_timer(FLASH_S, self._unflash)
        self.refresh_line()

    def _unflash(self) -> None:
        self._flash, self._flash_timer = "", None
        self.refresh_line()

    def refresh_line(self) -> None:
        model1, served = system1_parts(self.system1)
        state = (SPINNER[self._frame % len(SPINNER)], S_OK) if self.running else ("○", S_MUTED)
        # The frontier model, then the efficient one a run hands over to: the one whose turn it is in the ink.
        frontier: list[Part] = [(model_label(self.system2, self.labels), S_MUTED if self.handed else S_INK)]
        handoff = model_label(self.handoff, self.labels) if self.handoff else ""
        efficient: list[Part] = [(handoff, S_BRAND_BOLD if self.handed else S_INK)] if handoff else []
        efficient = efficient or [("off", S_MUTED)]
        decision: list[Part] = [(model1, S_INK)]
        names = (frontier, efficient, decision)
        width = max(Content.assemble(*name).cell_length for name in names) + COLUMN_GAP

        thinking = thinking_box(self.thinking, self.levels) if self.thinking else []
        where: list[Part] = [(served, S_MUTED)] if served else []
        detail = max(Content.assemble(*thinking).cell_length, len(served)) + COLUMN_GAP

        cost2, cost1 = cost_text(self.cost2, self.unpriced), cost_text(self.cost1)
        cost_handoff = cost_text(self.cost_handoff)
        if cache := cache_text(self.prompt2, self.cached2, self.cache_reported):
            cost2 = f"{cost2}{SEP}{cache}" if cost2 else cache
        money = max(len(cost2), len(cost_handoff), len(cost1)) + COLUMN_GAP

        def table_row(label: str, value: list[Part], rest: list[Part], cost: str, tail: list[Part]) -> list[Part]:
            pad = " " * (detail - Content.assemble(*rest).cell_length)
            return [
                (label.ljust(LABEL_WIDTH), S_MUTED),
                *value,
                " " * (width - Content.assemble(*value).cell_length),
                *rest,
                pad,
                (cost.ljust(money), S_MUTED),
                *tail,
            ]

        run: list[Part] = [("working", S_OK)] if self.running else []
        if self.window:
            run.append((f"{self.context / self.window:.0%} of {tokens_text(self.window)}", S_MUTED))
        if self.free:
            run.append((self.free, S_MUTED))
        if self.running and self.activity:
            run.append((self.activity, BRAND))
        if self._flash:
            run.append((self._flash, S_OK))
        elif self.note:
            run.append((self.note, S_MUTED if self.note.startswith("Not connected yet") else S_BAD))
        working: list[Part] = [state, " ", *joined(run)]
        table = [
            table_row(
                "Frontier" if self.handoff else "Model", frontier, thinking, cost2, [] if self.handed else working
            ),
            table_row("Efficient", efficient, [], cost_handoff, working if self.handed else []),
            table_row("Decision", decision, where, cost1, [(self.watching, S_MUTED)] if self.watching else []),
        ]
        # With the room, the entering page's block letters between the mark and the table; else the name in type.
        letters = wordmark(page=CARD)
        beside = [Content(" " * letters[0].cell_length), *letters]
        name: list[Part] = [(WORDMARK.upper(), S_WORDMARK), "  "]
        wide = max(Content.assemble(*row).cell_length for row in table) + LETTERS_WIDTH <= self.size.width
        rows: list[list[Part]] = [[], [*([] if wide else name), (f"v{__version__}", S_MUTED)], [], *table, []]
        self._card = self._page()
        cubes = [Content(" " * MARK_COLUMNS), *mark(self._pose, self._card, self._look()["ink"], eyes=self._eyes)]
        lines = [
            Content.assemble(cube, "   ", *([word, "    "] if wide else []), *row)
            for cube, word, row in zip(cubes, beside, rows, strict=True)
        ]
        self.update(Content("\n").join(lines))

    def on_resize(self) -> None:
        self.refresh_line()


NEWLINE_KEYS = ("shift+enter", "ctrl+j")
"""Start a new line in the prompt: Shift+Enter where the terminal tells it from Enter, Ctrl+J everywhere."""
SHIFT_MASK = 0x20000
"""`kCGEventFlagMaskShift`."""
HID_STATE = 1
"""`kCGEventSourceStateHIDSystemState`: the keyboard itself, whichever app has the focus."""


@functools.cache
def _core_graphics() -> ctypes.CDLL | None:
    if sys.platform != "darwin" or os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return None  # over SSH the keys are typed on another machine
    try:
        library = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        library.CGEventSourceFlagsState.restype = ctypes.c_uint64
        library.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
    except (OSError, AttributeError):
        return None
    return library


def shift_held() -> bool:
    """Whether Shift is down on this Mac's keyboard now. Shift+Enter reaches the app as a plain Enter where the
    terminal or a multiplexer between does not report it (Terminal.app, herdr, tmux), so an Enter with Shift
    down is read as Shift+Enter, as pi does for Terminal.app. False off a Mac, over SSH, or when it cannot ask."""
    library = _core_graphics()
    return library is not None and bool(library.CGEventSourceFlagsState(HID_STATE) & SHIFT_MASK)


KEPT_IMAGES = Path(tempfile.gettempdir()) / "intern-images"
"""Where a dropped image from a folder the system empties is copied, for the agent to read when it gets to it."""


def kept_drop(text: str) -> str:
    """A paste that is dropped files (their paths, shell-escaped as a terminal pastes them), with each image from a
    temporary folder copied where it stays: a screenshot dragged from its thumbnail lives in a folder macOS empties
    within a minute or two, before the agent may read it. Anything else comes back as pasted."""
    try:
        words = shlex.split(text)
        paths = [file_path(word) for word in words]
        if not paths or not all(path.is_absolute() and path.is_file() for path in paths):
            return text
    except (ValueError, OSError):
        return text
    temporary, kept = Path(tempfile.gettempdir()).resolve(), KEPT_IMAGES.resolve()
    shown: list[str] = []
    copied = any(word.startswith("file://") for word in words)  # a URL some terminals drop: the path reads better
    for path in paths:
        real = path.resolve()
        if path.suffix.lower() in IMAGE_SUFFIXES and real.is_relative_to(temporary) and not real.is_relative_to(kept):
            copy = KEPT_IMAGES / f"{uuid.uuid4().hex[:6]}-{'-'.join(path.name.split())}"
            try:
                KEPT_IMAGES.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, copy)
            except OSError:
                return text
            path, copied = copy, True
        shown.append(shlex.quote(str(path)))
    return " ".join(shown) if copied else text


def file_path(word: str) -> Path:
    """A path as a prompt names it: a `file://` URL as a terminal may drop it, `~` for home."""
    return Path(unquote(word.removeprefix("file://")) if word.startswith("file://") else word).expanduser()


PATH_WORD = re.compile(r"'((?:/|~/)[^']+)'|((?:file:///|/|~/)(?:\\.|[^\s'\"])+)")
"""A path in a prompt: quoted, or with its spaces escaped as a terminal drops it."""


def images_in(text: str) -> list[Path]:
    """The image files a prompt names by path, dropped, pasted or typed: what the agent will `read`."""
    found: list[Path] = []
    for quoted, bare in PATH_WORD.findall(text):
        path = file_path(quoted or re.sub(r"\\(.)", r"\1", bare).rstrip(".,;:!?)"))  # "what is /a.png?"
        try:
            if path.suffix.lower() in IMAGE_SUFFIXES and path not in found and path.is_file():
                found.append(path)
        except OSError:
            continue
    return found


def attachments(images: list[Path], sees: bool) -> Content | None:
    """What the prompt's border says of the images it names, and whether System 2 can see them."""
    if not images:
        return None
    parts: list[Part] = [
        (f" image{'s' if len(images) > 1 else ''} ", S_MUTED),
        (" · ".join(p.name for p in images), S_INK),
    ]
    if not sees:
        parts.append(("  The frontier model cannot see images: /frontier picks one that can ", S_WARN))
    else:
        parts.append((" ", S_MUTED))
    return Content.assemble(*parts)


WORD = re.compile(r"[^\s]+")
"""What a double click in the prompt selects: a run of characters between spaces, so a path is one word."""


class Prompt(TextArea):
    """The task box. Enter sends, Shift+Enter or Ctrl+J starts a new line, a paste keeps its lines, and a click
    puts the cursor where it lands. It grows with the text up to a few lines and scrolls past them. A command
    being typed is completed in ghost text that → accepts. A dropped screenshot is kept (`kept_drop`). Ctrl+C (Cmd+C
    where the terminal passes it on) copies what is selected in it, else clears it. It edits as a text box does:
    Shift with the arrows (or a drag) selects, a double click selects a word and a triple click the line, and typing,
    Backspace, Delete or a paste replace the selection; the Cmd and Option keys work where the terminal passes them on.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c,super+c", "copy_or_clear", "Clear", show=False),
        # TextArea's own keys stay (F7 selects all, Ctrl+X cuts, Ctrl+Z and Ctrl+Y undo and redo, Ctrl+W or
        # Alt+Backspace delete a word, Ctrl+U and Ctrl+K to the line's ends); these add the Mac's.
        Binding("super+a", "select_all", "Select all", show=False),
        Binding("super+shift+z,ctrl+shift+z", "redo", "Redo", show=False),
        Binding("ctrl+delete", "delete_word_right", "Delete word right", show=False),
        Binding("super+delete", "delete_to_end_of_line", "Delete to line end", show=False),
        Binding("alt+left", "cursor_word_left", "Word left", show=False),
        Binding("alt+right", "cursor_word_right", "Word right", show=False),
        Binding("alt+shift+left", "cursor_word_left(True)", "Select word left", show=False),
        Binding("alt+shift+right", "cursor_word_right(True)", "Select word right", show=False),
        Binding("super+left", "cursor_line_start", "Line start", show=False),
        Binding("super+right", "cursor_line_end", "Line end", show=False),
        Binding("super+shift+left", "cursor_line_start(True)", "Select to line start", show=False),
        Binding("super+shift+right", "cursor_line_end(True)", "Select to line end", show=False),
        Binding("super+up", "cursor_document_start", "Start", show=False),
        Binding("super+shift+up", "cursor_document_start(True)", "Select to start", show=False),
        Binding("super+down", "cursor_document_end", "End", show=False),
        Binding("super+shift+down", "cursor_document_end(True)", "Select to end", show=False),
    ]

    class Submitted(Message):
        def __init__(self, prompt: Prompt, value: str) -> None:
            super().__init__()
            self.prompt = prompt
            self.value = value

        @property
        def control(self) -> Prompt:
            return self.prompt

    def __init__(self, *, placeholder: str, id: str, commands: Iterable[str] = ()) -> None:
        super().__init__(placeholder=placeholder, id=id, soft_wrap=True, highlight_cursor_line=False)
        self.commands = tuple(commands)

    def action_copy_or_clear(self) -> None:
        if self.selected_text:
            self.action_copy()
        elif self.text:
            self.clear()
        else:
            raise SkipAction()  # nothing to clear: the app's own Ctrl+C says how to quit

    async def _on_paste(self, event: events.Paste) -> None:
        # Textual runs TextArea's own handler after this one (it walks the classes), with the text as kept. Stopped,
        # so the app does not take it as a paste nothing had the focus for (`HarnessApp.on_paste`).
        event.text = kept_drop(event.text)
        event.stop()

    def take_paste(self, text: str) -> None:
        """A paste or a drop that reached the app while the prompt did not have the focus: the terminal had lost it
        while a file was dragged in from another app, or a click had moved it. It lands as if pasted here."""
        self.focus()
        if result := self._replace_via_keyboard(kept_drop(text), *self.selection):
            self.move_cursor(result.end_location)

    def _delete_selection(self) -> bool:
        start, end = self.selection
        if start == end:
            return False
        self._delete_via_keyboard(start, end)
        return True

    def action_delete_to_start_of_line(self) -> None:
        if not self._delete_selection():
            super().action_delete_to_start_of_line()

    def action_delete_to_end_of_line(self) -> None:
        if not self._delete_selection():
            super().action_delete_to_end_of_line()

    def action_cursor_document_start(self, select: bool = False) -> None:
        self.move_cursor((0, 0), select=select)

    def action_cursor_document_end(self, select: bool = False) -> None:
        self.move_cursor(self.document.end, select=select)

    async def _on_click(self, event: events.Click) -> None:
        """A double click selects the word under the pointer, a triple click its line."""
        if event.chain == 2:
            row, column = self.cursor_location
            line = self.document[row]
            for match in WORD.finditer(line):
                if match.start() <= column <= match.end():
                    self.selection = Selection((row, match.start()), (row, match.end()))
                    break
        elif event.chain >= 3:
            self.select_line(self.cursor_location[0])

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter" or event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            if event.key == "enter" and not shift_held():
                self.post_message(self.Submitted(self, self.text))
            else:
                self.replace("\n", *self.selection)
            return
        await super()._on_key(event)

    def update_suggestion(self) -> None:
        text = self.text
        typed = text.casefold()
        command = next((c for c in self.commands if text.startswith("/") and c.startswith(typed) and c != typed), "")
        self.suggestion = command[len(text) :]


# -- the transcript -----------------------------------------------------------------------------------------

PREVIEW_LINES = 2
EXPANDED_CHARS = 20_000
TICK_S = 1.0
"""How often a running call's clock moves."""
HEADER_CHARS = 400


THINKING_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
THINKING_FRAME_S = 0.1


class Thinking(Static):
    """The model's reasoning, folded to one line: a spinner while it thinks, then how long it thought. A click
    shows all of it, another folds it again. The reply after it is shown in full."""

    def __init__(self, *, live: bool = True) -> None:
        super().__init__("", classes="thinking")
        self.text = ""
        self.expanded = False
        self.live = live
        """Thought now, so it spins and says for how long; a resumed session's reasoning is shown as done."""
        self.done = not live
        self._frame = 0
        self._started = time.monotonic()
        self._seconds = 0.0
        self._spinner: Timer | None = None
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        if not self.done:
            self._spinner = self.set_interval(THINKING_FRAME_S, self._spin)
        self._render_line()

    def append(self, delta: str) -> None:
        self.text += delta
        if self.expanded and self._timer is None:
            self._timer = self.set_timer(FLUSH_S, self._flush)

    def _flush(self) -> None:
        self._timer = None
        self._render_line()

    def _spin(self) -> None:
        self._frame += 1
        if not self.expanded:
            self._render_line()

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        self._seconds = time.monotonic() - self._started
        for timer in (self._spinner, self._timer):
            if timer is not None:
                timer.stop()
        self._spinner = self._timer = None
        self._render_line()

    def toggle(self) -> None:
        stay(self)
        self.expanded = not self.expanded
        self._render_line()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.toggle()

    def _render_line(self) -> None:
        if self.done:
            took = f" for {self._seconds:.1f}s" if self.live else ""
            mark, label = ("▾" if self.expanded else "▸"), f" Thought{took}"
        else:
            mark, label = THINKING_FRAMES[self._frame % len(THINKING_FRAMES)], " Thinking…"
        parts: list[Part] = [(mark, S_INK), (label, S_MUTED)]
        if self.expanded and self.text.strip():
            parts.append(("\n" + bounded(self.text.strip(), EXPANDED_CHARS), S_MUTED_ITALIC))
        elif self.expanded and self.done:  # a model with its thinking display omitted sends signed blocks, no text
            parts.append(("\n(the model kept its thinking hidden)", S_MUTED_ITALIC))
        self.update(Content.assemble(*parts))


FOLD_INDENT = 6
"""Where a turn's lines start in the fold of a hand-over's turns, after its number."""
FOLD_STEP_S = 0.04
"""A run that just ended: its turns come into the fold one after another, this far apart."""


class HandedFold(Static):
    """A hand-over's turns once its run is over, folded into the record of them the frontier model is sent at the
    next message (`handover_record`): what each turn said and a line for each thing it did, then the files. The
    reply the run ended on stays under it, whole, as that model is sent it. A click shows the turns themselves,
    another folds them again."""

    def __init__(self, record: InShort, hidden: list[Widget], *, live: bool) -> None:
        super().__init__("", classes="handed-fold")
        self.record = record
        self.hidden = hidden
        self.expanded = False
        self._shown = 0 if live else len(record.turns)
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        if self._shown < len(self.record.turns) and self.app.animation_level != "none":
            self._timer = self.set_interval(FOLD_STEP_S, self._step)
        else:
            self._shown = len(self.record.turns)
        self._render_fold()

    def _step(self) -> None:
        self._shown += 1
        if self._shown >= len(self.record.turns) and self._timer is not None:
            self._timer.stop()
            self._timer = None
        if not self.expanded:
            self._render_fold()

    def toggle(self) -> None:
        stay(self)
        self.expanded = not self.expanded
        for widget in self.hidden:
            widget.display = self.expanded
        self._render_fold()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.toggle()

    def on_resize(self) -> None:
        self._render_fold()

    def _render_fold(self) -> None:
        turns = self.record.turns
        span = f"Turn {turns[0][0]}" if len(turns) == 1 else f"Turns {turns[0][0]} to {turns[-1][0]}"
        if self.expanded:
            self.update(Content.assemble(("▾ ", S_INK), (span, S_MUTED), ("   click to fold", S_MUTED)))
            return
        parts: list[Part] = [("▸ ", S_INK), (f"{span} as the frontier model reads them", S_INK)]
        parts.append(("   click for the turns themselves", S_MUTED))
        room = max((self.content_size.width or 100) - FOLD_INDENT, 20)  # its own wrapping, to keep the indent
        for number, said, did in turns[: self._shown]:
            lines: list[Part] = [
                (line, S_INK) for part in said.splitlines() if part.strip() for line in textwrap.wrap(part, room)
            ]
            for action, gave in did:  # a command cut to what leaves room for what it gave back
                tail = f" → {gave}" if gave else ""
                keep = max(room - len(tail), 20)
                short = action if len(action) <= keep else action[: keep - 1].rstrip() + "…"
                lines.append((short + tail, S_MUTED))
            parts.append((f"\n{number:>{FOLD_INDENT - 2}}  ", S_BRAND_BOLD))
            for i, line in enumerate(lines):
                parts.extend(["\n" + " " * FOLD_INDENT, line] if i else [line])
        if self._shown >= len(turns):
            parts.append((f"\n{self.record.files}", S_MUTED))
        self.update(Content.assemble(*parts))


class ReplyBlock(Markdown):
    """The agent's reply, rendered as Markdown while it streams."""

    def __init__(self) -> None:
        super().__init__("", classes="reply")
        self.text = ""
        self._stream: MarkdownStream | None = None

    async def stream(self, delta: str) -> None:
        self.text += delta
        if self._stream is None:
            self._stream = Markdown.get_stream(self)
        await self._stream.write(delta)

    async def finish(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None
        await self.update(self.text)


def tool_header(name: str, arguments: JsonObject) -> str:
    """How a call reads: `$ command` for bash, else the tool and the argument that says what it is about."""
    if name == "bash" and isinstance(command := arguments.get("command"), str):
        return bounded(f"$ {command.strip()}", HEADER_CHARS)
    subject = next((arguments[k] for k in TITLE_ARGUMENTS if isinstance(arguments.get(k), str)), None)
    if name == "process":
        subject = process_subject(arguments) or subject
    if subject is None:
        subject = json.dumps(arguments) if arguments else ""
    return bounded(f"{name} {str(subject).strip()}".strip(), HEADER_CHARS)


def preview(content: str, *, tail: bool) -> tuple[list[str], int]:
    """The lines shown folded (the end for a command, the start for a read) and how many are hidden."""
    lines = content.rstrip("\n").splitlines()
    if len(lines) <= PREVIEW_LINES:
        return lines, 0
    shown = lines[-PREVIEW_LINES:] if tail else lines[:PREVIEW_LINES]
    return shown, len(lines) - PREVIEW_LINES


class ToolBlock(Vertical):
    """One tool call the way pi shows it: the call on a shaded line, its output under it (the end of a command's
    output, the start of a read's), how long it took, and while it runs, how long it has been running. Click, or
    ctrl+o on the latest, to see the whole output."""

    def __init__(self, event: ToolCallStart, *, live: bool = True) -> None:
        super().__init__(classes="tool")
        self.name_ = event.name
        self.title = printable(tool_header(event.name, event.arguments))
        self.head = Static(Content(self.title), classes="head")
        self.out = Static(Content.assemble(("running…", S_MUTED)), classes="out")
        self.content_text = ""
        self.lead = ""
        """A line above the output that is not the tool's own words: how a `process` call's job stands."""
        self.status = "running"
        self.duration_ms = 0
        self.expanded = False
        self.card: Static | None = None
        self.live = live
        """Running now, so its clock moves; a resumed session's calls are replayed without one."""
        self._started = time.monotonic()
        self._clock: Timer | None = None

    def compose(self) -> ComposeResult:
        yield self.head
        yield self.out

    def on_mount(self) -> None:
        if self.live and self.status == "running":
            self._clock = self.set_interval(TICK_S, self._tick)

    def _tick(self) -> None:
        self.out.update(Content.assemble((f"running… {self._elapsed()}s", S_MUTED)))

    def _elapsed(self) -> int:
        return int(time.monotonic() - self._started)

    def _stop_clock(self) -> None:
        if self._clock is not None:
            self._clock.stop()
            self._clock = None

    def unfinished(self) -> None:
        """The run ended without a result for this call (System 1 stopped it mid-call): the clock stops."""
        if self.status != "running" or self._clock is None:
            return
        self._stop_clock()
        self.out.update(Content.assemble((f"no result · {self._elapsed()}s", S_MUTED)))

    async def judged(self, content: Content, *, refused: bool) -> None:
        """The guard's verdict on this call (System 1's or a rule's), between the call and its output."""
        if self.card is None:
            self.card = Static(content, classes="decision")
            await self.mount(self.card, before=self.out)
        else:
            self.card.update(content)
        self.card.set_class(refused, "refused")

    async def attach(self, widget: Widget) -> None:
        """An extension view's widget, between the call (and the guard's card) and its output."""
        await self.mount(widget, before=self.out)

    def end(self, event: ToolCallEnd) -> None:
        self._stop_clock()
        self.content_text = event.content
        if self.name_ == "process":
            self.lead, self.content_text = process_result(event.content)
        self.lead, self.content_text = printable(self.lead), printable(self.content_text)
        self.status = event.status
        self.duration_ms = event.duration_ms
        self.set_class(event.status != "ok", "failed")
        self.render_output()

    def toggle(self) -> None:
        stay(self)
        self.expanded = not self.expanded
        self.render_output()

    def on_click(self) -> None:
        if self.status != "running":
            self.toggle()

    def render_output(self) -> None:
        text = self.content_text.rstrip("\n")
        parts: list[Part] = [(self.lead + "\n" * bool(text), S_INK)]
        if self.expanded:
            parts.append((bounded(text, EXPANDED_CHARS), S_MUTED))
        else:
            lines, hidden = preview(text, tail=self.name_ in ("bash", "process"))
            if hidden and self.name_ in ("bash", "process"):
                parts.append((f"… ({hidden} earlier lines, click or ctrl+o to expand)\n", S_MUTED_ITALIC))
            parts.append(("\n".join(lines), S_MUTED))
            if hidden and self.name_ not in ("bash", "process"):
                parts.append((f"\n… ({hidden} more lines, click or ctrl+o to expand)", S_MUTED_ITALIC))
        took = (
            f"Took {self.duration_ms / 1000:.1f}s"
            if self.status == "ok"
            else f"{self.status} · {self.duration_ms / 1000:.1f}s"
        )
        parts.append(("\n" if text or self.lead else "", ""))
        parts.append((took, S_BAD if self.status != "ok" else S_MUTED))
        self.out.update(Content.assemble(*(p for p in parts if p[0])))


class Following(VerticalScroll, can_focus=False):
    """A scroll that keeps showing its end as what is in it grows, stays put once the reader scrolls up, and follows
    again once they are back at the end: Textual's anchor, held as the screen is laid out, so a reply's Markdown,
    drawn in a task of its own after its text arrives, is followed too. Not focusable: a click in it, to select
    text, must not take the keyboard from the prompt."""

    def on_mount(self) -> None:
        self.anchor()

    @property
    def scroll_offset(self) -> Offset:
        # The anchor scrolls what is shorter than the screen to a negative offset, its foot: keep it at the top.
        return Offset(round(self.scroll_x), max(0, round(self.scroll_y)))

    def release_anchor(self) -> None:
        super().release_anchor()
        self.call_after_refresh(self._follow_if_all_shown)

    def _follow_if_all_shown(self) -> None:
        """With all of it on screen there is nothing to have scrolled up from (a PageUp, a fold): following again."""
        if self.max_scroll_y == 0:
            self.anchor()


def stay(block: Widget) -> None:
    """A block the reader opens or folds stays where it is: the transcript stops following, as after a scroll up."""
    for ancestor in block.ancestors:  # its parent, or the transcript around a hand-over's turns
        if isinstance(ancestor, Following):
            ancestor.release_anchor()
            return


class HelpCard(Grid):
    """What /help lists, a heading over each group: a command or key on each row, what it does beside it, wrapping
    under itself rather than under the names."""

    def __init__(self, sections: Mapping[str, Mapping[str, str]]) -> None:
        super().__init__(classes="help")
        self.sections = sections

    def compose(self) -> ComposeResult:
        for title, rows in self.sections.items():
            yield Static(Content(title), classes="help-head")
            for name, does in rows.items():
                command, _, takes = name.partition(" ")  # what a command takes, in the gray: `/runner [name]`
                yield Static(
                    Content.assemble((command, "bold"), (f" {takes}" if takes else "", S_MUTED)), classes="help-name"
                )
                yield Static(Content(does), classes="help-does")


class Transcript(Following):
    """Prompts, replies, thinking, tool calls and the harness's own markers, in the order they happened."""

    def __init__(self) -> None:
        super().__init__(id="transcript")
        self._reply: ReplyBlock | None = None
        self._reasoning: Thinking | None = None
        self._tools: dict[str, ToolBlock] = {}
        self._last_tool: ToolBlock | None = None
        self._markers: dict[str, Static] = {}
        self._compactions: dict[str, CompactionCard] = {}
        self._handed: Vertical | None = None
        """The turns the efficient model takes after a hand-over, set apart until the run ends."""
        self.working("Frontier model", "")

    def working(self, role: str, model: str) -> None:
        """Who is working, on the transcript's tab: `Frontier model`, or `Efficient model` after a hand-over, and the
        model by its name."""
        self.border_title = Content.assemble((f" {role} ", S_TAB), (f" {model} " if model else "", S_MUTED))

    async def add(self, widget: Widget) -> None:
        await (self._handed or self).mount(widget)

    @property
    def handed(self) -> bool:
        return self._handed is not None

    async def hand_over(self, content: Content) -> None:
        """The run handed over: what follows, until `hand_back`, is the efficient model's, under `content`."""
        await self.finish_reply()
        await self.hand_back()
        region = Vertical(classes="handed")
        await self.mount(region)
        self._handed = region
        await region.mount(Static(content, classes="handed-head"))

    async def hand_back(
        self, content: Content | None = None, *, record: InShort | None = None, live: bool = False
    ) -> None:
        """The run the efficient model finished is over: what follows is the frontier model's again. With `record`,
        what the frontier model is sent of that model's turns, they fold into it (`HandedFold`); `live` when the
        run ended just now."""
        if self._handed is None:
            return
        await self.finish_reply()
        region, self._handed = self._handed, None
        if record is not None and record.turns:
            drawn = list(region.children)
            heads = [i for i, widget in enumerate(drawn) if widget.has_class("turn-head")]
            turns = drawn[1 : heads[-1] if record.reply and heads else None]  # the reply the run ended on stays
            # A message the user sent meanwhile is theirs, not a turn's: it stays in sight.
            hidden = [widget for widget in turns if widget.display and not widget.has_class("user")]
            if hidden and (heads or not record.reply):
                for widget in hidden:
                    widget.display = False
                await region.mount(HandedFold(record, hidden, live=live), after=drawn[0])
        if content is not None:
            await region.mount(Static(content, classes="handed-foot"))

    async def user(self, text: str, queued: bool = False) -> Static:
        """A user message; `queued` while a run in progress has not taken it yet."""
        text = printable(text)
        parts: list[Part] = [("› ", "bold"), (text.replace("\n", "\n  "), "bold")]  # its lines under its first
        if queued:
            parts.append(("  queued", S_MUTED))
        line = Static(Content.assemble(*parts), classes="user")
        await self.add(line)
        return line

    async def notice(self, text: str, kind: str = "info") -> None:
        await self.add(Static(Content(printable(text)), classes=f"notice {kind}"))

    async def begin_turn(self, head: Content | None = None) -> None:
        """A turn starts: the reply before it is finished, and `head`, who takes the turn, goes above it."""
        await self.finish_reply()
        if head is not None:
            await self.add(Static(head, classes="turn-head"))

    async def finish_reply(self) -> None:
        if self._reply is not None:
            await self._reply.finish()
            self._reply = None
        if self._reasoning is not None:
            self._reasoning.finish()
            self._reasoning = None

    async def text(self, delta: str) -> None:
        if self._reasoning is not None:  # the thinking is over once the reply starts
            self._reasoning.finish()
            self._reasoning = None
        if self._reply is None:
            self._reply = ReplyBlock()
            await self.add(self._reply)
        await self._reply.stream(printable(delta))

    async def reasoning(self, delta: str, *, live: bool = True) -> None:
        """The model's reasoning, folded to one line (`Thinking`); `live` False for a resumed session's."""
        if self._reasoning is None:
            self._reasoning = Thinking(live=live)
            await self.add(self._reasoning)
        self._reasoning.append(printable(delta))

    async def tool_start(self, event: ToolCallStart, *, live: bool = True) -> None:
        """A call's block; `live` when it runs now (its clock moves), not when a resumed session replays it."""
        if self._reasoning is not None:  # the thinking is over once a call starts
            self._reasoning.finish()
            self._reasoning = None
        block = ToolBlock(event, live=live)
        self._tools[event.call_id] = block
        self._last_tool = block
        await self.add(block)

    def tool_end(self, event: ToolCallEnd) -> None:
        if (block := self._tools.pop(event.call_id, None)) is not None:
            block.end(event)

    def run_ended(self) -> None:
        """The run is over: a call still running got no result, and its clock stops."""
        for block in self._tools.values():
            block.unfinished()
        self.compactions_ended()

    def compactions_ended(self) -> None:
        """No compaction is under way: one whose cut never came stops."""
        for card in self._compactions.values():
            card.end()

    def toggle_last_tool(self) -> None:
        block = self._last_tool
        if block is None or block.status == "running":
            return
        folds = [fold for fold in self.query(HandedFold) if block in fold.hidden and not fold.expanded]
        if folds:  # folded away with its hand-over's turns: those come back first
            folds[0].toggle()
        else:
            block.toggle()

    def tool(self, call_id: str) -> ToolBlock | None:
        """The call's block while it runs."""
        return self._tools.get(call_id)

    def running_tool(self, name: str) -> ToolBlock | None:
        """The latest running call of `name`: the one a guard judgement just arrived for, which names the tool
        but not the call."""
        running = list(self._tools.values())
        return next((block for block in reversed(running) if block.name_ == name), running[-1] if running else None)

    async def clear(self) -> None:
        """Back to the start: the header, nothing else."""
        self._reply = None
        self._reasoning = None
        self._last_tool = None
        self._tools.clear()
        self._markers.clear()
        self._compactions.clear()
        self._handed = None
        await self.remove_children()
        self.anchor()  # following again, even if the reader had scrolled up

    async def marker(self, content: Content, key: str | None = None, classes: str = "marker") -> None:
        """A muted one-liner from the harness, or a card (`classes`); with a `key`, a later call with the same
        key replaces it."""
        if key is not None and (existing := self._markers.get(key)) is not None:
            existing.update(content)
            existing.set_classes(classes)
            return
        widget = Static(content, classes=classes)
        if key is not None:
            self._markers[key] = widget
        await self.add(widget)

    async def compaction(self, c: CompactionJudgement, key: str, window: int, cwd: Path | None, *, live: bool) -> None:
        """A compaction's card; a later call with the same key redraws it."""
        if (card := self._compactions.get(key)) is not None:
            card.show(c, window)
            return
        card = CompactionCard(c, window, cwd, live=live)
        self._compactions[key] = card
        await self.add(card)


# -- System 2's summary, section by section (a compaction's card) -------------------------------------------


class Line(Horizontal):
    """One Plan step or one bullet: a mark column and text that wraps under itself, not under the mark."""

    def __init__(self, mark: str, mark_style: str, text: str, text_style: str = "", depth: int = 0) -> None:
        super().__init__(classes="line")
        self.mark = Static(Content.assemble((mark, mark_style)), classes="mark")
        self.body = Static(Content.assemble(styled(text, text_style)), classes="body")
        self.styles.padding = (0, 0, 0, 2 * depth)

    def compose(self) -> ComposeResult:
        yield self.mark
        yield self.body


def outline_widgets(outline: Outline) -> list[Widget]:
    """The State's sections as widgets: a heading per section, the Plan as a checklist with one glyph per
    step's status, the other sections' lines as bullets or text."""
    widgets: list[Widget] = []
    counts = outline.counts()
    for section in outline.sections:
        if section.title:
            parts: list[Part] = [(section.title.upper(), S_HEAD)]
            if section.steps:
                tally = [
                    (counts["done"], "done"),
                    (counts["in_progress"], "in progress"),
                    (counts["pending"], "pending"),
                ]
                summary = " · ".join(f"{n} {label}" for n, label in tally if n)
                if summary:
                    parts.append(("  " + summary, S_MUTED))
            widgets.append(Static(Content.assemble(*parts), classes="section" if widgets else "section first"))
        if section.steps:
            for step in section.steps:
                glyph, style = STEP_GLYPH[step.status]
                widgets.append(Line(glyph, style, step.text, S_MUTED if step.status == "pending" else "", step.depth))
            continue
        quote: list[str] = []  # a block quote's lines so far, as written; a request in User requests is one
        quoting = any(QUOTE.match(line) for line in section.lines)  # its other lines only say what the quotes are
        for line in [*section.lines, ""]:
            if match := QUOTE.match(line):
                quote.append(EMPHASIS.sub("", match.group("text")).rstrip())
                continue
            if quote:
                widgets.append(Static(Content("\n".join(quote)), classes="quote"))
                quote = []
            if not line:
                continue
            if match := LIST_ITEM.match(line):
                widgets.append(Line("•", S_MUTED, plain(match.group("text"))))
            else:
                widgets.append(Static(Content.styled(plain(line), S_MUTED if quoting else ""), classes="text-line"))
    return widgets


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_S = 0.1


# -- the System 1 panel -------------------------------------------------------------------------------------


WRAP_WORDS = {"done_polishing": "done, now polishing", "done_unsaved": "done, not yet saved", "unclear": "unclear"}
"""The wrap-up verdicts worth a note; `working`, the usual one, has none."""


def turn_notes(j: TurnJudgement) -> list[Part]:
    """What the loop did with the verdict: a stuck strike's count, a nudge; and the turn's other verdict, whether
    the request is met, once it is anything but still being worked on."""
    notes: list[Part] = []
    if j.strikes:
        notes.append((f"strike {j.strikes}/{STRIKES}", S_WARN))
    if j.nudged:
        notes.append(("stuck nudge sent", S_WARN_BOLD))
    if words := WRAP_WORDS.get(j.wrap):
        notes.append((f"request {words} {j.wrap_probability:.2f}", S_MUTED))
    if j.wrapped:
        notes.append(("wrap-up nudge sent", S_WARN_BOLD))
    if j.cached:
        notes.append(("cached", S_MUTED))
    return notes


# -- System 1's cards in the transcript ---------------------------------------------------------------------


NBSP = "\u00a0"
GAP = "   "
"""Between a card's parts: wide enough that each reads as its own."""
CARD_MODES = 3
"""The modes on the supervisor's card: its call, and the two it weighed most against it."""


def runners_up(probabilities: dict[str, float], answer: str, most: int = CARD_MODES - 1) -> list[str]:
    """What System 1 weighed most against its answer, likeliest first: none that rounds to 0.00."""
    others = (other for other, p in probabilities.items() if other != answer and p >= 0.005)
    return sorted(others, key=lambda other: -probabilities[other])[:most]


def level(label: str, p: float, style: str, number: str = S_INK) -> list[Part]:
    """`advancing 0.92`, `risk 0.68`: the words the System 1 panel uses, and the probability. The space inside is
    non-breaking, so a wrapped card breaks between levels, never inside one."""
    return [(label.replace(" ", NBSP) + NBSP, style), (f"{p:.2f}", number)]


def card(label: str, meta: str, levels: list[Part], outcome: list[Part]) -> Content:
    """A card's line: its label, muted, with the call's own facts (how long
    System 1 took, whether it was cached); then each answer and its probability, as System 1's panel says them; then,
    after an arrow, what came of them, so a result does not read as one more option."""
    parts: list[Part] = [(label, S_HEAD_MUTED)]
    if meta:
        parts.append((f" · {meta}", S_MUTED))
    if levels:
        parts += [GAP, *levels]
    for n, part in enumerate(outcome):
        parts += [(f"{GAP}→ ", S_MUTED) if n == 0 else (" · ", S_MUTED), part]
    return Content.assemble(*parts)


def seconds(ms: int, cached: bool) -> str:
    return f"{ms / 1000:.1f}s" + (" · cached" if cached else "")


LADDER = "▁▂▃▄▅▆▇█"
"""A probability as a column: an eighth a step, the lowest for none."""


HANDOVER_SIGN = "⇢"


def model_name(ref: str) -> str:
    """A model as the hand-over says it: its own name, without the provider before it."""
    return ref.rsplit("/", 1)[-1]


def model_label(ref: str, labels: Mapping[str, str] | None = None) -> str:
    """A model by the name people know it by: its display name where one is known (`labels`, from the catalog, or
    the known models'), else its own name without the provider. `…` and `none`, before there is a model, as they are."""
    if ref in ("…", "none", ""):
        return ref
    return (labels or {}).get(ref) or KNOWN_SYSTEM2.get(ref) or model_name(ref)


def turn_head(efficient: bool, model: str) -> Content:
    """Who takes a turn, on a line above it: the model by its name, `Claude Opus 5.5`, the efficient one's after a
    hand-over in the accent, as the hand-over's shading is."""
    return Content.assemble((model, S_BRAND_BOLD if efficient else S_HEAD_MUTED))


def readiness(h: HandoverJudgement) -> list[Part]:
    """System 1's P(ready) turn by turn as a column each, the latest in the accent, then that one in figures: how the
    work came to the point where the efficient model can finish it."""
    if not h.ready:
        return []
    columns = [LADDER[max(0, math.ceil(p * len(LADDER)) - 1)] for p in h.ready[-READY_SHOWN:]]
    trail: list[Part] = [("".join(columns[:-1]), S_MUTED), (columns[-1], S_BRAND_BOLD), " "]
    said = "efficient " if h.handed_at == 0 else "ready "  # judged at the run's start: P(efficient)
    return [(said, S_MUTED), *trail, (f"{h.ready[-1]:.2f}", S_INK)]


READY_SHOWN = 12
"""The turns a readiness trail shows, newest last."""


def handoff_card(h: HandoverJudgement, name: str = "") -> Content:
    """The hand-over where it happened: which model finishes the run (`name`, else the target's own name) and from
    which turn; under it, how sure the decision model was and how it got there."""
    head = Content.assemble(
        (f"{HANDOVER_SIGN} ", S_BRAND_BOLD),
        (f"Handed over to the efficient model ({name or model_name(h.target)})", S_BRAND_BOLD),
        (" from the start" if h.handed_at == 0 else f" at turn {h.handed_at}", S_MUTED),
    )
    return Content("\n").join([head, Content.assemble("  ", card("Decision model", "", readiness(h), []))])


def supervisor_card(j: TurnJudgement) -> Content:
    """System 1's call at a turn's end, as a card at the turn boundary: the mode in its colour and its probability,
    the next likeliest muted (up to CARD_MODES in all, none that rounds to 0.00), then what followed."""
    levels = level(mode_label(j.mode), j.confidence, MODE_STYLE.get(j.mode, S_INK))
    for mode in runners_up(j.probabilities, j.mode):
        levels += [GAP, *level(mode_label(mode), j.probabilities[mode], S_MUTED, S_MUTED)]
    outcome = [note for note in turn_notes(j) if note[0] != "cached"]  # the call's own fact, with its time
    return card("Decision model", seconds(j.duration_ms, j.cached), levels, outcome)


LOOK_STYLE = {
    "progressing": S_OK,
    "waiting_as_expected": S_INK,
    "ready": S_WARN,
    "stalled": S_WARN,
    "failing": S_BAD,
}


def look_card(v: ProcessVerdict, *, named: bool) -> Content:
    """System 1's look at a command running in the background, as its other cards read: how long the command had run,
    what System 1 found in its colour with its probability, what it weighed most against that, and whether the agent
    was told. The judgement, not the command: `named`, when several run, says which in a few words."""

    def said(verdict: str, style: str, number: str) -> list[Part]:
        label, p = verdict.replace("_", " "), v.probabilities.get(verdict)
        return [(label, style)] if p is None else level(label, p, style, number)

    levels = said(v.verdict, LOOK_STYLE.get(v.verdict, S_MUTED), S_INK)
    for other in runners_up(v.probabilities, v.verdict, 1):  # one, not two: the line also says when, and what came
        levels += [GAP, *said(other, S_MUTED, S_MUTED)]
    meta = (f"{brief(v.command, 32)} " if named else "") + f"at {duration_text(v.elapsed_s)}"
    outcome: list[Part] = [("told the agent", S_WARN_BOLD)] if v.told else []
    return Content.assemble((f"{JOB} ", S_MUTED), card("Decision model", meta, levels, outcome))


def guard_card(g: GuardJudgement) -> Content:
    """System 1's judgement of a tool call, as the last line of the call's head, a tick or a cross before it, so
    it does not read as the supervisor's card; what the call would do and how likely, P(any risk), P(the user
    asked for it), muted unless one is past its threshold; how long it took; the reason under it when refused."""
    risky = g.risk >= RISK_THRESHOLD
    unasked = risky and g.asked < ASKED_THRESHOLD
    kind_style = S_MUTED if g.kind == "routine" else S_WARN if g.kind == "unclear" else S_BAD
    head: list[Part] = (
        [("✗ ", S_BAD_BOLD), ("Decision model refused", S_BAD_BOLD)]
        if g.refused
        else [("✓ ", S_OK), ("Decision model", S_MUTED)]
    )
    parts: list[Part] = [
        *head,
        GAP,
        *level(g.kind.replace("_", " "), g.probabilities.get(g.kind, 0.0), kind_style, S_MUTED),
        GAP,
        *level("risk", g.risk, S_MUTED, S_BAD_BOLD if risky else S_MUTED),
        GAP,
        *level("asked", g.asked, S_MUTED, S_BAD_BOLD if unasked else S_MUTED),
        GAP,
        (seconds(g.duration_ms, g.cached), S_MUTED),
    ]
    if g.refused:
        parts += ["\n", (g.reason, S_BAD)]
    return Content.assemble(*parts)


def rule_card(r: RuleRefusal) -> Content:
    """A fixed rule's refusal, as the guard's line under the call: no model was asked, so not System 1's."""
    head: list[Part] = [
        ("✗ ", S_BAD_BOLD),
        ("refused", S_BAD_BOLD),
        GAP,
        (f"rule · {r.rule.replace('_', ' ')}", S_MUTED),
    ]
    return Content.assemble(*head, "\n", (r.reason, S_BAD))


# -- a compaction: its card in the transcript ---------------------------------------------------------------

REASON_LABEL = {"working_on": "kept", "reference": "reference", "done_with": "done with"}
VERDICT_STYLE = {"working_on": S_OK_BOLD, "reference": S_INK, "done_with": S_MUTED}
"""A read's verdict on its line: kept in green, the closer of the two reasons to cut it in ink."""
CUT, FREE = "╌", "─"
"""The context bar: the tokens a compaction cut, dashed; the window left free, a hairline."""
CARD_LINES = 8
"""The reads a compaction's card lists: live, the latest to land; settled, the kept first, then the closest calls."""
FRAME_S = 0.05
"""A live compaction card's frame."""
SCAN_FRAMES, FILL_FRAMES = 6, 5
"""A read's line lands in two steps: its meter scanned while System 1 judges it, then filled to P(working on)."""
LANDED = SCAN_FRAMES + FILL_FRAMES
BACKLOG = 12
"""Reads waiting per extra line started each frame, so a long list of reads still lands in a few seconds."""
READ_LABEL = re.compile(r"^(?P<path>.*?) \((?:lines (?P<a>\d+)-(?P<b>\d+)|from line (?P<f>\d+))\)$")


def read_parts(label: str) -> tuple[str, str]:
    """A read's label (`path (lines 1-80)`, `path (from line 9)` or a bare path) as its path and `:1-80`."""
    if m := READ_LABEL.match(label):
        return m["path"], f":{m['a']}-{m['b']}" if m["a"] else f":{m['f']}-"
    return label, ""


def fit_path(path: str, width: int) -> str:
    """`path` in `width` cells: whole if it fits, else its head (`..`, `~` or `/` and the folder after it) and as
    much of its end as fits, with `…` between, so a sibling worktree's name and the file both stay in view."""
    if len(path) <= width:
        return path
    parts = path.split("/")
    rooted = parts[0] in ("", "..", "~") and len(parts) > 2  # inside the working directory, the end is what tells
    head, rest = ("/".join(parts[:2]), parts[2:]) if rooted else ("", parts)
    tail: list[str] = []
    lead = f"{head}/…/" if head else "…/"
    for part in reversed(rest):
        if len(lead + "/".join([part, *tail])) > width:
            break
        tail.insert(0, part)
    if tail and (head or len(tail) < len(rest)):
        return lead + "/".join(tail)
    if head and len(head) + 2 <= width:  # another worktree's name says more than the folders inside it
        return f"{head}/…"
    name = parts[-1]
    return f"…/{name}" if len(name) + 2 <= width else "…" + name[-(width - 1) :]


def short_path(path: str, cwd: Path | None) -> str:
    """A read's path as the user thinks of it: relative to the working directory inside it, `../sibling/…` for a
    folder next to it (another worktree), else under `~`."""
    p = Path(path)
    if not p.is_absolute():
        return path
    if cwd is not None:
        if p.is_relative_to(cwd):
            return str(p.relative_to(cwd))
        if p.is_relative_to(cwd.parent) and cwd.parent != Path(cwd.anchor):
            return str(Path("..") / p.relative_to(cwd.parent))
    home = Path.home()
    return f"~/{p.relative_to(home)}" if p.is_relative_to(home) else path


def read_name(label: str, cwd: Path | None, width: int) -> list[Part]:
    """A read as `path:1-80` in `width` cells, the line range muted."""
    path, lines = read_parts(label)
    return [fit_path(short_path(path, cwd), max(8, width - len(lines))), (lines, S_MUTED)]


def context_bar(before: int, after: int, scale: int, width: int) -> list[Part]:
    """The context a compaction left, solid, then what it cut, dashed, then the room left, a hairline; all against
    `scale` (the window, or what there was before when the window is not known)."""
    scale = max(scale, before, 1)
    kept = round(after / scale * width)
    cut = max(0, round(before / scale * width) - kept)
    parts: list[Part] = [(FILLED * kept, S_INK), (CUT * cut, S_MUTED), (FREE * (width - kept - cut), S_RULE)]
    return [part for part in parts if part[0]]


def shrink(c: CompactionJudgement) -> str:
    return f"−{1 - c.tokens_after / c.tokens_before:.0%}" if c.tokens_before else ""


def continued_from(c: CompactionJudgement) -> list[Part]:
    size = f" ({len(c.summary.split()):,} words)" if c.summary else ""
    if c.method == "state":
        written = ", written for it first: the standing one did not cover the cut" if c.state_written else ""
        return ["the summary", (size + written, S_MUTED)]
    why = "the summary of them did not land" if c.state_written else "no summary covered them"
    return ["a list of the cut turns' tool calls", (f"{size}: {why}", S_MUTED)]


def latest_line(draft: str) -> str:
    """The write's latest whole line: the one still being written may be a heading's `##` so far."""
    lines = draft.split("\n")
    return next((line.strip() for line in reversed(lines[:-1] or lines) if line.strip()), "")


def scan(age: int, width: int = METER_WIDTH) -> list[Part]:
    """A meter System 1 is still judging: a short bright stroke sweeping along its track and back."""
    period = 2 * (width - 1)
    at = age % period
    at = at if at < width else period - at
    cells: list[Part] = []
    for i in range(width):
        near = abs(i - at)
        cells.append((FILLED, BRAND) if near == 0 else (FILLED, S_MUTED) if near == 1 else (TRACK, S_RULE))
    return cells


def verdict_line(v: KeepVerdict, age: int | None, cwd: Path | None, width: int = 0) -> list[Part]:
    """One read being cut, as System 1 judged it: kept or why not, P(working on) as a meter, the read. `age`: the
    frames its line has been landing, its meter first scanned, then filled with the verdict shown; None, landed."""
    room = max(16, width - 31) if width else 60
    name = read_name(v.path, cwd, room)
    if age is not None and age < SCAN_FRAMES:
        mark: Part = (SPINNER[age % len(SPINNER)], BRAND)
        return ["  ", mark, " ", ("judging".ljust(10), S_MUTED_ITALIC), *scan(age), " " * 7, *name]
    shown = v.keep if age is None or age >= LANDED else v.keep * (age - SCAN_FRAMES + 1) / FILL_FRAMES
    style = VERDICT_STYLE.get(v.choice, S_MUTED)
    mark = ("✓", S_OK_BOLD) if v.kept else ("·", S_MUTED)
    if not v.kept:  # the cut reads quieter, so the kept ones stand out
        name = [(part, S_MUTED) if isinstance(part, str) else part for part in name]
    label = REASON_LABEL.get(v.choice, v.choice.replace("_", " ")).ljust(10)
    bar = meter(shown, S_OK if v.kept else S_INK if v.choice == "reference" else S_MUTED)
    return ["  ", mark, " ", (label, style), *bar, (f" {shown:.2f}  ", S_INK if v.kept else S_MUTED), *name]


def settled(c: CompactionJudgement, ages: dict[str, int] | None) -> bool:
    """The cut is made and every read's line has landed (`ages`: each line's frames so far; None, not live)."""
    if ages is None:
        return True
    return c.done and len(ages) == len(c.verdicts) and all(age >= LANDED for age in ages.values())


def compaction_card(
    c: CompactionJudgement,
    window: int = 0,
    cwd: Path | None = None,
    *,
    ages: dict[str, int] | None = None,
    frame: int = 0,
    expanded: bool = False,
    width: int = 0,
) -> Content:
    """A compaction in System 2's transcript: System 2 writing the summary it will continue from (the latest line
    as it streams), then its context before and after against the window, what the agent continues from, and a line
    per read being cut with System 1's verdict on it. Live (`ages`, by read: the frames its line has been landing),
    the reads not started are not shown and the latest CARD_LINES are, as they land; settled, the reads kept first,
    then the closest to being kept, CARD_LINES of them unless `expanded`. The summary itself is the card's to show
    (`CompactionCard`); this says a click shows it. A compaction that settles without a cut was stopped."""
    calm = settled(c, ages)
    started = c.verdicts if ages is None else [v for v in c.verdicts if v.path in ages]
    spin = (SPINNER[frame // 2 % len(SPINNER)] + " ", BRAND) if not calm else ("", "")
    if c.done:
        to = "a summary" if c.method == "state" else "a list of their calls"
        parts: list[Part] = [
            ("⟲ Compaction", S_HEAD),
            (f" · turn {c.turn} · {c.turns_compacted} turns cut to {to}", S_MUTED),
            "\n",
            ("Context ", S_MUTED),
            *context_bar(c.tokens_before, c.tokens_after, window or c.tokens_before, 40),
            (f"  {tokens_text(c.tokens_before)} → ", S_MUTED),
            (f"{tokens_text(c.tokens_after)} ", S_HEAD),
            (shrink(c), S_OK_BOLD),
            (f"  of {tokens_text(window)}" if window else "", S_MUTED),
            "\n",
            ("Continues from ", S_MUTED),
            *continued_from(c),
            (", and the recent turns as they were", S_MUTED),
        ]
    elif calm:
        parts = [("⟲ Compaction stopped", S_HEAD), (f" · turn {c.turn} · nothing was cut", S_MUTED)]
    elif c.writing:
        parts = [("⟲ Compacting", S_HEAD), (f" · turn {c.turn} · writing the summary  ", S_MUTED), spin]
        head = Content.assemble(*parts)
        count = f" {len(c.draft):,} chars" if c.draft else ""
        if not width or head.cell_length + len(count) <= width:  # the line below shows it grow, so it can go
            head = Content.assemble(head, (count, S_MUTED))
        if not (line := latest_line(c.draft)):
            return head
        last = Content.styled("  " + line, S_MUTED)
        return Content("\n").join([head, last.truncate(width, ellipsis=True) if width else last])
    else:
        parts = [
            ("⟲ Compacting", S_HEAD),
            (f" · turn {c.turn} · the decision model is sorting the reads being cut", S_MUTED),
        ]
        if not started and spin[0]:
            parts += ["  ", spin]
    if calm and c.done:
        hidden = 0
        if started:
            kept = c.kept_reads()
            count = f"{len(kept)} of {len(c.verdicts)}"
            parts += [
                "\n",
                ("Reads ", S_MUTED),
                ("the decision model kept ", S_MUTED),
                (count, S_OK if kept else S_INK),
            ]
            parts.append((" verbatim", S_MUTED))
            if cut := c.cut_reads():
                reasons = ", ".join(f"{len(r)} {REASON_LABEL.get(k, k)}" for k, r in cut.items())
                parts.append((f"  ·  cut {reasons}", S_MUTED))
            ordered = sorted(c.verdicts, key=lambda v: (not v.kept, -v.keep))
            lines = ordered if expanded else ordered[:CARD_LINES]
            for v in lines:
                parts += ["\n", *verdict_line(v, None, cwd, width)]
            hidden = len(ordered) - len(lines)
        more = ["what it continues from"] if c.summary and not expanded else []
        more += [f"{hidden} more reads"] if hidden else []
        if more:
            parts += ["\n", (f"  … click to show {' and '.join(more)}", S_MUTED_ITALIC)]
        return Content.assemble(*parts)
    if not started:
        return Content.assemble(*parts)
    landed = [v for v in started if ages is None or ages[v.path] >= SCAN_FRAMES]
    kept_so_far = sum(v.kept for v in landed)
    of = f" of {len(c.verdicts)}" if c.done else ""
    parts += ["\n", ("Reads ", S_MUTED), spin, (f"the decision model judged {len(landed)}{of}", S_MUTED)]
    parts.append((f" · {kept_so_far} kept", S_OK if kept_so_far else S_MUTED))
    for v in started[-CARD_LINES:]:
        parts += ["\n", *verdict_line(v, None if ages is None else ages[v.path], cwd, width)]
    return Content.assemble(*parts)


class CompactionCard(Vertical):
    """A compaction's card (`compaction_card`). Live, System 2's summary streams in while it is written, then System
    1's verdicts land a line at a time, each read seen being judged (the asks are concurrent, so the verdicts come
    nearly at once); replayed, it is drawn settled. A click on a settled card shows what the agent continues from,
    section by section, and every read; another folds them again."""

    def __init__(self, c: CompactionJudgement, window: int, cwd: Path | None, *, live: bool) -> None:
        super().__init__(classes="decision compaction")
        self.c, self.window, self.cwd, self.live = c, window, cwd, live
        self.expanded = False
        self.head = Static()
        self.summary = Vertical(classes="compaction-summary")
        """System 2's summary, section by section (`outline_widgets`), mounted the first time the card opens."""
        self._started: dict[str, int] = {}
        """The frame each read's line started landing at."""
        self._frame = 0
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield self.head
        yield self.summary

    def on_mount(self) -> None:
        self.summary.display = False
        self.show(self.c, self.window)

    def show(self, c: CompactionJudgement, window: int) -> None:
        """The compaction as it stands: the write further, a verdict more, or the cut made."""
        self.c, self.window = c, window
        # Not `is_mounted`: it turns true only after `on_mount`, so a card opened by a write would never tick, and
        # the write streams in with no `show` until it lands.
        if self._timer is None and not settled(c, self._ages()):
            self._timer = self.set_interval(FRAME_S, self._tick)
        self._draw()

    def end(self) -> None:
        """No cut follows (the run was stopped, an extension vetoed it): stop, and draw what there is."""
        if not self.c.done:
            self.live = False
            self._stop()
            self._draw()

    def _ages(self) -> dict[str, int] | None:
        return {path: self._frame - at for path, at in self._started.items()} if self.live else None

    def _tick(self) -> None:
        self._frame += 1
        waiting = [v.path for v in self.c.verdicts if v.path not in self._started]
        for path in waiting[: 1 + len(waiting) // BACKLOG]:
            self._started[path] = self._frame
        if settled(self.c, self._ages()):
            self._stop()
        self._draw()

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _draw(self) -> None:
        width = self.content_size.width
        self.head.update(
            compaction_card(
                self.c, self.window, self.cwd, ages=self._ages(), frame=self._frame, expanded=self.expanded, width=width
            )
        )

    def on_resize(self) -> None:
        self._draw()

    async def on_click(self) -> None:
        if settled(self.c, self._ages()):
            self.expanded = not self.expanded
            if self.expanded and self.c.summary and not self.summary.children:
                await self.summary.mount_all(outline_widgets(parse_state(self.c.summary)))
            self.summary.display = self.expanded and bool(self.summary.children)
            self._draw()

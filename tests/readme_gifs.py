"""Record the README's three GIFs from the real terminal UI, each in a light and a dark version.

    uv run python -m tests.readme_gifs code-search    # docs/assets/code-search-light.gif, -dark.gif
    uv run python -m tests.readme_gifs files-search   # docs/assets/files-search-light.gif, -dark.gif
    uv run python -m tests.readme_gifs compaction     # docs/assets/compaction-light.gif, -dark.gif
    uv run python -m tests.readme_gifs all            # the three

`--out DIR` writes the GIFs elsewhere; `--frames DIR` keeps every frame (SVG and PNG) there. Needs every extra
(`uv sync --all-extras`: the code search, the workspace and their document readers), Google Chrome (macOS's path, or
`$CHROME`) to draw the frames and ffmpeg to put them together.

Each GIF is one session of the real `HarnessApp` under Textual's `run_test`, with the real tools, extensions and
index, over a folder written to a temporary directory:

- code-search: a copy of this repository's `src/`, a git work tree, so the index is built as in a checkout; the
  frontier model asks `decision_code` where a run that goes in circles is detected, and the search tree grows under
  the call as the decision model scores folders, files and definitions, until the ranking.
- files-search: seven audit papers made up for the recording (`tests/fixtures/readme/papers/`, written out as PDFs
  and workbooks), indexed before the session opens, as `index_files` would; the frontier model asks `decision_files`
  whether the client was ever the victim of a scam or theft, and what it cost.
- compaction: a made-up date library (`tests/fixtures/readme/dates/`) the frontier model reads file by file looking
  for why a test fails, then `/compact`: the frontier model writes its State into the compaction's card, the decision
  model judges each read being cut, and a click opens the State.

The frontier model (anthropic/claude-opus-5.5) is always scripted, priced as `PRICES` says; the efficient model
(tencent/hy4-flash) is named on the masthead and never takes a turn. The decision model is `nace-ai/drex-max` on
OpenRouter's decisions API, live, when `OPENROUTER_API_KEY` is set; otherwise it is scripted (`Judge`): each ask takes
a moment, costs `COST_PER_ASK`, and is answered from a table of scores for this recording's files, parts and passages.
The script prints which it was, for the README's caption.

At each step the screen is saved with `save_screenshot` in the light look and then the dark one, with the run held
between the two, so both GIFs show the same moments. The searches, the decision model's asks and the tools are timed
on a clock that stops while frames are taken (`Clock`), so the durations on screen are the run's, not the
recording's. The SVGs are drawn to PNGs 1280 wide by headless Chrome and put together by ffmpeg with a palette made
from all of them, each frame shown for its own time, the last one held.

Not a test: nothing here is collected (no `test_` prefix; `tests/fixtures/readme/conftest.py` keeps pytest out of the
fixtures).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import SecretStr

from intern.capability_settings import HandoffConfig
from intern.decisions import (
    Answer,
    Answers,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionBackend,
    DecisionClient,
    DecisionsApiBackend,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from intern.events import EventSink
from intern.executor import LocalExecutor
from intern.handoff import HandoffTarget
from intern.hooks import Extension, Hooks
from intern.instructions import FileInstructions
from intern.journal import Journal
from intern.loop import EngineDeps, ModelBinding
from intern.messages import Usage
from intern.models.catalog import Catalog, ModelInfo
from intern.models.profile import EFFORT_LEVELS, Profile, Thresholds
from intern.providers.base import Completed, ProviderRequest, StreamItem, TextChunk
from intern.providers.fake import FakeProvider, Script, Turn
from intern.session import Session
from intern.tools import load_packs
from intern.tui.app import HarnessApp
from intern.tui.theme import DARK_THEME, LIGHT_THEME
from intern.tui.widgets import CompactionCard, Prompt, StatusBar

from .support import binding, is_state_edit, is_state_write
from .test_tui import click_compaction, plain, wait_for

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "readme"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
WIDTH = 1280
"""Pixels: the GIFs' width, the README's."""

FRONTIER = "anthropic/claude-opus-5.5"
EFFICIENT = "tencent/hy4-flash"
DRIVE = "nace-ai/drex-max"
DECISION = f"decisions:{DRIVE}"
PRICES = {FRONTIER: (5.0, 25.0), EFFICIENT: (0.10, 0.40)}
"""USD per million tokens in and out, for the masthead's costs (as `tests/readme_screenshot.py` has them)."""
COST_PER_ASK = 0.00007
"""What one scripted ask costs: the mean of the live recordings in 48c1f57 (90 asks for $0.0063, 13 for $0.0009)."""
LOOKS = ("light", "dark")


# -- the clock -------------------------------------------------------------------------------------------------------


class Clock:
    """Time as the run sees it: it stops while a frame is taken, so the time on screen is the run's own."""

    def __init__(self) -> None:
        self._lost = 0.0
        self._held: float | None = None

    def monotonic(self) -> float:
        now = time.monotonic() if self._held is None else self._held
        return now - self._lost

    def hold(self) -> None:
        self._held = time.monotonic()

    def go(self) -> None:
        if self._held is not None:
            self._lost += time.monotonic() - self._held
            self._held = None

    async def sleep(self, seconds: float) -> None:
        until = self.monotonic() + seconds
        while self.monotonic() < until:  # noqa: ASYNC110 - the clock stops while a frame is taken: no event to wait on
            await asyncio.sleep(0.005)


class _TimeOn:
    """The `time` module, with its clocks read from a `Clock`."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def perf_counter(self) -> float:
        return self._clock.monotonic()

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)


TIMED = (
    "intern.locate.trace",
    "intern.locate.store",
    "intern.extensions.code_search.view",
    "intern.extensions.workspace.view",
    "intern.decisions",
    "intern.loop",
    "intern.tui.widgets",
)
"""The modules whose durations show: the searches and their views, the decision model's asks, the tool calls and
the transcript's running counters."""


def on_clock(clock: Clock) -> None:
    for name in TIMED:
        importlib.import_module(name).time = _TimeOn(clock)  # pyright: ignore[reportAttributeAccessIssue]


# -- the models ------------------------------------------------------------------------------------------------------


class Frontier(FakeProvider):
    """The frontier model, scripted: each reply after `think_s` on the clock, a State written word by word, priced
    as `PRICES` says."""

    effort_levels = EFFORT_LEVELS

    def __init__(self, script: Script, clock: Clock, *, think_s: float = 0.8, word_s: float = 0.0) -> None:
        super().__init__(script, FRONTIER)
        self.clock, self.think_s, self.word_s = clock, think_s, word_s

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamItem]:
        writing = is_state_write(request)
        await self.clock.sleep(self.think_s)
        async for item in super().stream(request):
            if isinstance(item, TextChunk) and writing and self.word_s:
                await self.clock.sleep(self.word_s)
            if isinstance(item, Completed):
                usage = item.message.usage
                price_in, price_out = PRICES[FRONTIER]
                cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
                usage = Usage(**{**usage.model_dump(), "cost_usd": cost})
                item = Completed(message=item.message.model_copy(update={"usage": usage}))
            yield item


Scores = Callable[[str], float]


def low(text: str, top: float = 0.08) -> float:
    """A small score that is the same for the same text every time."""
    return round(zlib.crc32(text.encode()) % 1000 / 1000 * top, 3)


class Judge:
    """The decision model, scripted. Every ask takes a moment on the clock and costs `COST_PER_ASK`. A search's
    asks (a card, "[id] card" blocks, a passage) are answered by `score`; the compare picks in proportion to it.
    A read about to be cut is kept as `reads` says (else done with); the supervisor finds the work advancing, the
    hand-over finds it not ready."""

    name = f"decisions-api:{DRIVE}"

    def __init__(
        self,
        clock: Clock,
        score: Scores,
        *,
        reads: Mapping[str, tuple[str, float]] | None = None,
        latency: tuple[float, float] = (0.35, 0.9),
        seed: int = 7,
    ) -> None:
        self.clock, self.score, self.reads = clock, score, reads or {}
        self.latency = latency
        self.random = random.Random(seed)

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        await self.clock.sleep(self.random.uniform(*self.latency))
        body = state.split("\n\n", 1)[1] if "\n\n" in state else state
        parts = re.split(r"^\[(\w+)\] ", body, flags=re.M)
        blocks = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        answers: dict[str, Answer] = {}
        for name, question in questions.items():
            if isinstance(question, ChoiceQuestion):
                answers[name] = self.choose(state, question, blocks)
            elif question.type == "score":
                answers[name] = ScoreAnswer(score=0.0)
            elif "\nPassage [" in question.instructions:
                answers[name] = NoulAnswer(noul=self.score(question.instructions.split("\nPassage [", 1)[1]))
            else:
                # One card: without its "Candidate file:" line; a read part keeps "Text from <path>, <part>:".
                card = body if body.startswith("Text from ") else body.split(":\n", 1)[-1]
                answers[name] = NoulAnswer(noul=self.score(blocks.get(name, card)))
        return Answers(answers=answers, cost_usd=COST_PER_ASK)

    def choose(self, state: str, question: ChoiceQuestion, blocks: dict[str, str]) -> ChoiceAnswer:
        criteria = question.criteria
        jitter = self.random.uniform(-0.04, 0.04)
        if "ready" in criteria:
            return spread("not_ready", 0.86 + jitter, criteria)
        if "advancing" in criteria:  # the supervisor's look
            return spread("advancing", 0.9 + jitter, criteria)
        if "done_polishing" in criteria:
            return spread("working", 0.92 + jitter, criteria)
        if "working_on" in criteria:  # a read about to be cut
            found = re.search(r"The file read about to be cut: (\S+)", state)
            choice, p = self.reads.get(found.group(1) if found else "", ("done_with", 0.9))
            return spread(choice, p, criteria)
        if none := next((c for c, said in criteria.items() if said == "none of these"), None):  # the compare
            weights = {c: max(self.score(blocks.get(c, "")), 0.001) ** 16 for c in criteria if c != none}
            weights[none] = 0.0005
            total = sum(weights.values())
            probabilities = {c: w / total for c, w in weights.items()}
            return ChoiceAnswer(choice=max(probabilities, key=probabilities.__getitem__), probabilities=probabilities)
        first = next(iter(criteria))
        return spread(first, 0.9, criteria)


def spread(choice: str, p: float, criteria: Mapping[str, str]) -> ChoiceAnswer:
    """`choice` at `p`, the rest shared among the other options."""
    rest = [c for c in criteria if c != choice]
    return ChoiceAnswer(choice=choice, probabilities={choice: p, **{c: (1 - p) / len(rest) for c in rest}})


def decision_model(judge: Judge) -> tuple[DecisionBackend, bool]:
    """The live decision model when `OPENROUTER_API_KEY` is set, else `judge`; and whether it is live."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return DecisionsApiBackend("https://openrouter.ai/api/v1", SecretStr(key), [DRIVE]), True
    return judge, False


async def catalog() -> Catalog:
    """The listing the masthead and the transcript take the models' display names from."""
    return Catalog(
        system2=[
            ModelInfo(id=FRONTIER, name="Anthropic: Claude Opus 5.5"),
            ModelInfo(id=EFFICIENT, name="Tencent: Hy4 Flash"),
        ],
        system1=[ModelInfo(id=DECISION, name="Drex Max")],
    )


def opener(
    root: Path,
    work: Path,
    frontier: Frontier,
    decision: DecisionBackend,
    *,
    extensions: tuple[type[Extension], ...] = (),
    window: int = 200_000,
    profile: Profile | None = None,
):
    """Sessions on the scripted frontier model, with the efficient model to hand over to and `decision`."""

    def bound(provider: FakeProvider, ref: str) -> ModelBinding:
        made = binding(provider, window=window, profile=(profile or Profile()).model_copy())
        return replace(made, ref=ref, wire_model=ref.split("/")[-1])

    async def open_session(emit: EventSink, models: object) -> Session:
        made = [cls() for cls in extensions]
        deps = EngineDeps(
            system2=bound(frontier, FRONTIER),
            tools=load_packs(["base"]) + [tool for extension in made for tool in extension.tools()],
            hooks=Hooks(made, emit),
            journal=Journal(cwd=str(work), path=root / "journal.jsonl"),
            instructions=FileInstructions(root / "home", context_files=False).load(work),
            decisions=DecisionClient(decision, emit),
            executor=LocalExecutor(),
            cwd=work,
            spill_dir=root / "spill",
            emit=emit,
        )
        efficient = bound(FakeProvider([Turn(text="")], EFFICIENT), EFFICIENT)
        deps.handoff = HandoffTarget.of(efficient, HandoffConfig(to=EFFICIENT), ["base"])
        return Session(deps, system1=DECISION)

    return open_session


# -- the film --------------------------------------------------------------------------------------------------------


@dataclass
class Film:
    """The frames of one session, in both looks, and how long each is shown."""

    app: HarnessApp
    pilot: object
    folder: Path
    clock: Clock
    holds: list[float] = field(default_factory=list)

    async def shot(self, hold: float) -> None:
        """The screen now, light and dark, the run held while both are taken; shown for `hold` seconds."""
        n = len(self.holds)
        self.clock.hold()
        try:
            for look in LOOKS if n % 2 == 0 else LOOKS[::-1]:  # one switch of look a frame, not two
                await self.look(look)
                self.app.save_screenshot(f"{n:03d}.svg", str(self.folder / look))
        finally:
            self.clock.go()
        self.holds.append(hold)

    async def look(self, look: str) -> None:
        theme = (DARK_THEME if look == "dark" else LIGHT_THEME).name
        if self.app.theme != theme:
            self.app.theme = theme
            self.app.query_one(StatusBar).refresh_line()  # the mark's half tones blend with the page
        await self.pilot.pause()  # pyright: ignore[reportAttributeAccessIssue]
        await self.pilot.pause()  # pyright: ignore[reportAttributeAccessIssue]

    async def roll(self, done: Callable[[], bool], every: float, hold: float, limit: float = 240) -> None:
        """A frame every `every` seconds on the clock until `done`."""
        deadline = time.monotonic() + limit
        while not done():
            if time.monotonic() > deadline:
                raise TimeoutError("the run did not end in time")
            await self.clock.sleep(every)
            if not done():
                await self.shot(hold)

    async def type(self, text: str, hold: float) -> None:
        """`text` in the prompt, shown for `hold` seconds."""
        self.app.query_one("#prompt", Prompt).text = text
        await self.shot(hold)

    async def send(self) -> None:
        """Enter on the prompt, again until it takes it: a key pressed while the session settles can be lost."""
        prompt = self.app.query_one("#prompt", Prompt)
        for _ in range(20):
            prompt.focus()
            await self.pilot.press("enter")  # pyright: ignore[reportAttributeAccessIssue]
            await self.pilot.pause()  # pyright: ignore[reportAttributeAccessIssue]
            try:
                await wait_for(lambda: not prompt.text, 1)
                return
            except TimeoutError:
                continue
        raise TimeoutError("the prompt did not send")


def run_done(app: HarnessApp, before: object) -> Callable[[], bool]:
    """Whether the run started after `before` (the run task then) has ended."""
    return lambda: (task := app._run_task) is not None and task is not before and task.done()  # pyright: ignore[reportPrivateUsage]


@dataclass
class Scene:
    """One GIF: its name, the terminal's size, and the session that films it."""

    name: str
    size: tuple[int, int]
    film: Callable[[Path, Clock], Awaitable[tuple[HarnessApp, Callable[[Film], Awaitable[None]], bool]]]


async def shoot(scene: Scene, frames: Path) -> tuple[list[float], bool]:
    """Run the scene's session and save its frames under `frames/light` and `frames/dark`."""
    for look in LOOKS:
        (frames / look).mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with tempfile.TemporaryDirectory() as tmp:
        app, story, live = await scene.film(Path(tmp), clock)
        if not live:
            on_clock(clock)
        async with app.run_test(size=scene.size) as pilot:
            await wait_for(lambda: app.session is not None, 120)
            await pilot.pause()
            film = Film(app, pilot, frames, clock)
            await story(film)
            await app.action_quit()
    return film.holds, live


# -- code search -----------------------------------------------------------------------------------------------------

CODE_PROMPT = "Find where a run that goes in circles is detected"
CODE_QUESTION = "Where does the harness decide that a run is going in circles?"

CODE_FILES = {
    "src/intern/supervise.py": 0.97,
    "src/intern/loop.py": 0.9,
    "src/intern/memory/steward/run.py": 0.71,
    "src/intern/session.py": 0.63,
    "src/intern/context.py": 0.55,
    "src/intern/process_watch.py": 0.22,
    "src/intern/handoff.py": 0.17,
    "src/intern/hooks.py": 0.05,
    "src/intern/events.py": 0.04,
}
"""The decision model's scores for the files a run's circling could be decided in; the rest score low."""

CODE_PARTS = [
    (r"def Supervisor\.after_turn", 0.96),
    (r"def is_strike", 0.86),
    (r"def stuck_probability", 0.82),
    (r"class Supervisor\b", 0.74),
    (r"def Run\._review", 0.8),
    (r"def Run\._handle_reply", 0.57),
    (r"def Run\._turns", 0.52),
    (r"class Run\b", 0.31),
    (r"def done_probability", 0.36),
    (r"def Session\._waits", 0.33),
    (r"class Session\b", 0.38),
    (r"def _triage", 0.41),
]
"""Scores for the parts (classes, functions) of those files, by the line that names them."""

CODE_FOLDERS = [("supervise.py", 0.93), ("steward", 0.58), ("process_watch", 0.27)]
"""A folder or group holding one of these files is where to look."""


def code_score(text: str) -> float:
    lines = text.strip().splitlines()
    head = re.sub(r"^\[\w+\] ", "", lines[0]) if lines else ""
    if head.startswith(("folder ", "group ")):
        return next((p for word, p in CODE_FOLDERS if word in text), low(head, 0.18))
    for pattern, p in CODE_PARTS:
        if re.search(pattern, head):
            return p
    if found := re.match(r"(?:file |Text from )?(src/\S+\.py)\b", head):
        path = found.group(1)
        if head.startswith(("file ", "src/")):
            return CODE_FILES.get(path, low(head))
        return low(head, 0.3 if path in CODE_FILES else 0.1)  # another part of a file worth reading
    if found := re.search(r" in (src/\S+\.py)", head):
        return low(head, 0.3 if found.group(1) in CODE_FILES else 0.1)
    return low(head)


async def code_search(root: Path, clock: Clock):
    work = root / "work"
    shutil.copytree(REPO / "src", work / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    for command in (["init", "-q"], ["add", "-A"]):  # a git work tree: the index is built before the first turn
        await asyncio.to_thread(subprocess.run, ["git", *command], cwd=work, check=True)
    frontier = Frontier(
        [
            Turn(
                text="I'll ask code search.",
                tool_calls=[("decision_code", {"question": CODE_QUESTION})],
                input_tokens=3_120,
                output_tokens=60,
            ),
            Turn(
                text="Code search puts it in `src/intern/supervise.py`: `Supervisor.after_turn` asks the decision "
                "model every few turns what the turns are doing, and two stuck looks in a row (`is_strike`) send the "
                "nudge.",
                input_tokens=4_480,
                output_tokens=70,
            ),
        ],
        clock,
    )
    decision, live = decision_model(Judge(clock, code_score, latency=(0.6, 1.6)))
    from intern.extensions.code_search import CodeSearch

    app = HarnessApp(
        opener(root, work, frontier, decision, extensions=(CodeSearch,), profile=Profile(reasoning_effort="high")),
        catalog=catalog,
        welcome=False,
    )

    async def story(film: Film) -> None:
        await film.pilot.press("f2")  # pyright: ignore[reportAttributeAccessIssue]  # the search, the whole width
        await film.type(CODE_PROMPT, hold=1.4)
        before = app._run_task  # pyright: ignore[reportPrivateUsage]
        await film.send()
        await film.roll(run_done(app, before), every=0.3, hold=0.3)
        await film.pilot.pause()  # pyright: ignore[reportAttributeAccessIssue]
        await film.shot(hold=4.0)

    return app, story, live


# -- files search ----------------------------------------------------------------------------------------------------

FILES_PROMPT = "Has the client been the victim of a scam or theft? What did it cost?"
FILES_QUESTION = "Has the client ever been the victim of a scam or theft, and what did it cost?"

FILES_CARDS = [
    ("fraud-risk questionnaire", 0.96),
    ("internal control questionnaire", 0.61),
    ("fs notes", 0.34),
    ("income statement", 0.12),
    ("going-concern questionnaire", 0.06),
    ("balance sheet", 0.03),
    ("engagement letter", 0.02),
]
"""The decision model's scores for each paper's card, by its name."""

FILES_PASSAGES = [
    ("victim of fraud, a scam or theft", 1.0),
    ("diverted to a fraudulent account", 0.76),
    ("stolen from the warehouse", 0.7),
    ("other expenses (note 7)", 0.21),
    ("reported as stolen", 0.18),
    ("call-back", 0.05),
    ("prevention and detection of fraud", 0.03),
]
"""Scores for the papers' passages, by a phrase each holds; the rest score near nothing."""


def files_score(text: str) -> float:
    lowered = re.sub(r"\s+", " ", text.lower())
    if lowered.startswith(("file ", "folder ", "group ")):
        head = lowered.split("\n", 1)[0]
        return next((p for name, p in FILES_CARDS if name in head), low(head, 0.05))
    return next((p for phrase, p in FILES_PASSAGES if phrase in lowered), low(lowered, 0.02))


def write_papers(folder: Path) -> None:
    """The made-up papers as the client sent them: each `.pdf.txt` a PDF, a page per `--- page ---`; each
    `.xlsx.txt` a workbook, a tab per `## name`, its rows tab-separated."""
    import pymupdf
    from openpyxl import Workbook

    for source in sorted((FIXTURES / "papers").iterdir()):
        name = source.name.removesuffix(".txt")
        text = source.read_text()
        if name.endswith(".pdf"):
            pdf = pymupdf.open()
            for page_text in text.split("--- page ---\n"):
                page = pdf.new_page(width=595, height=842)
                page.insert_textbox(pymupdf.Rect(50, 50, 545, 800), page_text, fontsize=9, fontname="helv")
            pdf.save(folder / name)
        elif name.endswith(".xlsx"):
            book = Workbook()
            book.remove(book.active)  # pyright: ignore[reportArgumentType]
            for tab in text.split("## ")[1:]:
                title, _, rows = tab.partition("\n")
                sheet = book.create_sheet(title.strip())
                for row in rows.strip().splitlines():
                    sheet.append([int(c) if re.fullmatch(r"-?\d+", c) else c for c in row.split("\t")])
            book.save(folder / name)


async def files_search(root: Path, clock: Clock):
    from intern.extensions.workspace.documents import office_processors
    from intern.extensions.workspace.extension import Workspace
    from intern.locate import index as wi

    work = root / "work"
    work.mkdir()
    write_papers(work)
    wi.build(work, work / Workspace.folder, processors=office_processors, report=lambda line: None)
    frontier = Frontier(
        [
            Turn(
                text="I'll search the papers.",
                tool_calls=[("decision_files", {"question": FILES_QUESTION})],
                input_tokens=2_860,
                output_tokens=50,
            ),
            Turn(
                text="Yes, twice. In March 2026 a phishing email posing as a supplier had invoice 4471 paid to a "
                "fraudulent account: $48,200 lost, nothing recovered (Fraud-risk questionnaire.pdf, page 1; FS "
                "notes.pdf, page 2, note 7). In June 2026 a laptop and a tablet were stolen; insurance paid all but "
                "the $500 excess.",
                input_tokens=5_210,
                output_tokens=90,
            ),
        ],
        clock,
    )
    decision, live = decision_model(Judge(clock, files_score, latency=(0.9, 2.0)))
    app = HarnessApp(
        opener(root, work, frontier, decision, extensions=(Workspace,), profile=Profile(reasoning_effort="high")),
        catalog=catalog,
        welcome=False,
    )

    async def story(film: Film) -> None:
        await film.pilot.press("f2")  # pyright: ignore[reportAttributeAccessIssue]
        await film.type(FILES_PROMPT, hold=1.4)
        before = app._run_task  # pyright: ignore[reportPrivateUsage]
        await film.send()
        await film.roll(run_done(app, before), every=0.22, hold=0.28)
        await film.pilot.pause()  # pyright: ignore[reportAttributeAccessIssue]
        await film.shot(hold=4.0)

    return app, story, live


# -- compaction ------------------------------------------------------------------------------------------------------

COMPACT_PROMPT = "Find why tests/test_parser.py fails on ISO week dates, and plan the fix"

READS: list[tuple[str, list[str]]] = [
    ("Starting from the failing test.", ["tests/test_parser.py"]),
    ("", ["src/parser.py"]),
    ("`parse_date` skips every format in SPECIAL. Checking what is in it.", ["src/formats.py"]),
    ("", ["src/calendar_utils.py"]),
    ("", ["src/timezones.py"]),
    ("", ["src/errors.py"]),
    ("", ["docs/dates.md"]),
    ("Checking whether anything else reads week dates.", ["src/api.py"]),
    ("", ["tests/test_api.py"]),
    ("", ["src/cli.py"]),
    ("", ["tests/test_cli.py"]),
    ("", ["tests/test_formats.py"]),
    ("", ["tests/conftest.py"]),
    ("", ["docs/api.md"]),
    ("", ["README.md"]),
    ("Has week support shipped before?", ["CHANGELOG.md"]),
    ("", ["CONTRIBUTING.md"]),
    ("", ["setup.cfg"]),
    ("", ["pyproject.toml"]),
    ("", ["src/__init__.py"]),
]
"""The frontier model's reads, a turn each, with what it says at the turn."""

FOUND = (
    "`parse_date` has no branch for ISO week dates: `%G-W%V-%u` is in src/formats.py, in SPECIAL, so the loop skips "
    "it, and src/parser.py never handles it. Next: add that branch, using `iso_week` from src/calendar_utils.py."
)

STATE = """\
## Plan
1. Find the failing test - done: tests/test_parser.py fails on the ISO week date 2026-W05-3.
2. Find the cause - done: `parse_date` in src/parser.py skips every format in SPECIAL; `%G-W%V-%u` has no branch.
3. Add the ISO week branch to `parse_date` (in progress), using `iso_week` from src/calendar_utils.py.
4. Run tests/test_parser.py, then `pytest -q` - pending.
## Done when
`pytest -q` passes, tests/test_parser.py included.
## Now
Editing src/parser.py: the next action is a branch for `%G-W%V-%u` that splits the text on `-W` and `-` and calls \
`iso_week(year, week, weekday)`.
## Artifacts
None yet: nothing has been changed.
## Facts
- `iso_week(year, week, weekday=1)` in src/calendar_utils.py returns the date; week 1 holds the year's first Thursday.
- tests/test_parser.py expects 2026-W05-3 to be 2026-01-28 and 2026-W01-1 to be 2025-12-29.
- CONTRIBUTING.md: a format in SPECIAL gets its own branch in `parse_date`, and a line in the changelog.
- CHANGELOG.md lists ISO week dates under Unreleased as not read yet.
"""

KEPT_READS = {
    "src/parser.py": ("working_on", 0.97),
    "tests/test_parser.py": ("working_on", 0.71),
    "src/calendar_utils.py": ("working_on", 0.56),
    "src/formats.py": ("reference", 0.64),
    "docs/dates.md": ("reference", 0.81),
    "src/errors.py": ("reference", 0.58),
}
"""What the decision model makes of each read being cut: the three the next step works on are kept."""


async def compaction(root: Path, clock: Clock):
    work = root / "work"
    shutil.copytree(FIXTURES / "dates", work)
    turns = [
        Turn(text=said, tool_calls=[("read", {"path": path}) for path in paths], output_tokens=40)
        for said, paths in READS
    ] + [Turn(text=FOUND, output_tokens=120)]

    def script(request: ProviderRequest) -> Turn:
        if is_state_edit(request):
            return Turn(text="no change")
        if is_state_write(request):
            return Turn(text=STATE, output_tokens=420)
        return turns.pop(0) if turns else Turn(text="(script exhausted)")

    frontier = Frontier(script, clock, think_s=0.05, word_s=0.04)
    decision, live = decision_model(Judge(clock, lambda text: 0.0, reads=KEPT_READS, latency=(0.5, 1.1)))
    profile = Profile(reasoning_effort="high", thresholds=Thresholds(keep_recent=0.2))
    app = HarnessApp(
        opener(root, work, frontier, decision, window=16_384, profile=profile), catalog=catalog, welcome=False
    )

    async def story(film: Film) -> None:
        film.app.query_one("#prompt", Prompt).text = COMPACT_PROMPT
        before = app._run_task  # pyright: ignore[reportPrivateUsage]
        await film.send()
        await wait_for(run_done(app, before), 300)  # the reads, off camera
        await film.pilot.pause(0.5)  # pyright: ignore[reportAttributeAccessIssue]
        await film.shot(hold=1.6)
        await film.type("/compact", hold=0.9)
        await film.send()

        def landed() -> bool:
            cards = app.query(CompactionCard)
            return bool(cards) and "Continues from" in plain(cards.last().head)

        await film.roll(landed, every=0.3, hold=0.24)
        await film.pilot.pause(0.3)  # pyright: ignore[reportAttributeAccessIssue]
        await film.shot(hold=2.2)
        await click_compaction(app, film.pilot)
        await film.pilot.pause(0.3)  # pyright: ignore[reportAttributeAccessIssue]
        card = app.query(CompactionCard).last()  # from its head: the reads kept, then the State's plan
        card.scroll_visible(top=True, animate=False)
        await film.pilot.pause(0.3)  # pyright: ignore[reportAttributeAccessIssue]
        await film.shot(hold=4.0)

    return app, story, live


SCENES = {
    "code-search": Scene("code-search", (128, 64), code_search),
    "files-search": Scene("files-search", (128, 65), files_search),
    "compaction": Scene("compaction", (136, 46), compaction),
}


# -- frames to GIFs --------------------------------------------------------------------------------------------------


def png(svg: Path) -> Path:
    """`svg` drawn WIDTH pixels wide, as a PNG beside it."""
    found = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text())
    assert found is not None, f"no viewBox in {svg}"
    width, height = (float(n) for n in found.groups())
    out = svg.with_suffix(".png")
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={WIDTH},{round(WIDTH * height / width)}",
            f"--screenshot={out}",
            svg.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return out


def gif(pngs: list[Path], holds: list[float], out: Path) -> None:
    """The frames as a GIF, each shown for its hold, with one palette made from all of them."""
    listing = out.with_suffix(".txt")
    lines = ["ffconcat version 1.0"]
    for frame, hold in zip(pngs, holds, strict=True):
        lines += [f"file '{frame.resolve()}'", f"duration {hold:.2f}"]
    lines.append(f"file '{pngs[-1].resolve()}'")  # the concat demuxer drops the last frame's duration otherwise
    listing.write_text("\n".join(lines) + "\n")
    palette = out.with_suffix(".palette.png")
    concat = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    ffmpeg = ["ffmpeg", "-v", "error", "-y"]
    subprocess.run([*ffmpeg, *concat, "-vf", "palettegen=max_colors=96:stats_mode=full", str(palette)], check=True)
    subprocess.run(
        [
            *ffmpeg,
            *concat,
            "-i",
            str(palette),
            "-lavfi",
            "[0:v][1:v]paletteuse=dither=none:diff_mode=rectangle",
            "-fps_mode",
            "vfr",
            "-loop",
            "0",
            str(out),
        ],
        check=True,
    )
    listing.unlink()
    palette.unlink()


def record(scene: Scene, out: Path, frames: Path) -> None:
    holds, live = asyncio.run(shoot(scene, frames))
    for look in LOOKS:
        svgs = sorted((frames / look).glob("*.svg"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            pngs = list(pool.map(png, svgs))
        target = out / f"{scene.name}-{look}.gif"
        gif(pngs, holds, target)
        print(
            f"{target}: {target.stat().st_size // 1024} KB, {len(pngs)} frames, {sum(holds):.1f} s; the decision "
            f"model {'live (' + DRIVE + ')' if live else 'scripted'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("scene", choices=[*SCENES, "all"])
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "assets")
    parser.add_argument("--frames", type=Path, help="keep the frames here (default: a temporary folder)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name in SCENES if args.scene == "all" else [args.scene]:
        if args.frames:
            record(SCENES[name], args.out, args.frames / name)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                record(SCENES[name], args.out, Path(tmp))


if __name__ == "__main__":
    sys.exit(main())

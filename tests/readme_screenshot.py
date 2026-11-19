"""Make the README's screenshot of the terminal UI, `docs/assets/tui-light.png` and `docs/assets/tui-dark.png`.

    uv run python -m tests.readme_screenshot [--out docs/assets] [--no-png]

It runs the real `HarnessApp` on scripted models over a small project it writes to a temporary folder: the frontier
model (Claude Opus 5.5) runs the failing test, reads the code, fixes it and checks it; the decision model judges the
work ready and the run hands over to the efficient model (Hy4 Flash), which runs the whole suite, fixes the leftover
it finds and finishes. The tools are the real ones, so the test runs and the edits are real. It saves an SVG of each
theme with Textual's `save_screenshot`, then renders each to a PNG at twice its size with headless Chrome (macOS's
path, or `$CHROME`); `--no-png` keeps the SVGs instead. Not a test: nothing here is collected.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

from intern.capability_settings import HandoffConfig
from intern.decisions import (
    Answer,
    Answers,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionClient,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from intern.events import EventSink
from intern.executor import LocalExecutor
from intern.handoff import HandoffTarget
from intern.hooks import Hooks
from intern.instructions import FileInstructions
from intern.journal import Journal
from intern.loop import EngineDeps, ModelBinding
from intern.messages import JsonObject, Usage
from intern.models.catalog import Catalog, ModelInfo
from intern.providers.base import Completed, ProviderRequest, StreamItem
from intern.providers.fake import FakeProvider, Turn
from intern.session import Session
from intern.tools import load_packs
from intern.tui.app import HarnessApp, Models
from intern.tui.widgets import Prompt, Transcript

from .support import binding, is_state_write
from .test_tui import wait_for

FRONTIER = "anthropic/claude-opus-5.5"
EFFICIENT = "tencent/hy4-flash"
SIZE = (150, 67)
"""Columns and rows: as many columns as read at the README's width, and the rows to show from the frontier model's
last turn to the end of the run."""
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

PROMPT = "tests/test_slug.py fails. Find out why and fix it."

PROJECT = {
    "pytest.ini": "[pytest]\naddopts = -q --tb=no -rf -p no:cacheprovider -p no:asyncio\npythonpath = .\n",
    "textkit/__init__.py": "",
    "textkit/slug.py": '''import re


def slugify(title: str) -> str:
    """A title as a URL slug: its words, lowercase, joined by hyphens."""
    words = re.findall(r"[a-z0-9]+", title)
    return "-".join(words)
''',
    "textkit/urls.py": """from textkit.slug import slugify


def post_url(title: str) -> str:
    return f"/posts/{slugify(title)}"
""",
    "tests/test_slug.py": """from textkit.slug import slugify


def test_capitals():
    assert slugify("Hello, World!") == "hello-world"


def test_digits():
    assert slugify("top 10 tips") == "top-10-tips"


def test_empty():
    assert slugify("") == ""
""",
    "tests/test_urls.py": """from textkit.urls import post_url


def test_post_url():
    assert post_url("Release Notes") == "/posts/release-notes"


def test_apostrophe():
    assert post_url("Don't Panic") == "/posts/dont-panic"
""",
}

FIX: JsonObject = {
    "path": "textkit/slug.py",
    "old_string": 'words = re.findall(r"[a-z0-9]+", title)',
    "new_string": 'words = re.findall(r"[a-z0-9]+", title.lower())',
}
LEFTOVER: JsonObject = {
    "path": "textkit/slug.py",
    "old_string": "title.lower())",
    "new_string": 'title.lower().replace("\'", ""))',
}

FRONTIER_TURNS = [
    Turn(
        text="Running the failing test, and reading the code it covers.",
        tool_calls=[("bash", {"command": "pytest tests/test_slug.py"}), ("read", {"path": "textkit/slug.py"})],
        input_tokens=6_840,
        output_tokens=90,
    ),
    Turn(
        text="`[a-z0-9]+` drops capitals: `Hello` comes out as `ello`. Lowercasing the title fixes it.",
        tool_calls=[("edit", FIX), ("bash", {"command": "pytest tests/test_slug.py"})],
        input_tokens=7_710,
        output_tokens=210,
    ),
]

EFFICIENT_TURNS = [
    Turn(
        text="Running the whole suite.",
        tool_calls=[("bash", {"command": "pytest"})],
        input_tokens=8_920,
        output_tokens=40,
    ),
    Turn(
        text="An apostrophe still splits a word: `Don't` becomes `don-t`. Dropping apostrophes too.",
        tool_calls=[("edit", LEFTOVER), ("bash", {"command": "pytest"})],
        input_tokens=9_480,
        output_tokens=150,
    ),
    Turn(
        text="Fixed: `slugify` now lowercases the title and drops apostrophes. All 5 tests pass.",
        input_tokens=10_050,
        output_tokens=90,
    ),
]

PRICES = {FRONTIER: (5.0, 25.0), EFFICIENT: (0.10, 0.40)}
"""USD per million tokens in and out, for the masthead's costs."""

READY = [0.12, 0.88]
"""The decision model's P(ready) after turns 1 and 2: the fix is made and checked at turn 2."""


class Priced(FakeProvider):
    """A scripted model that answers after a moment, as a model does, and costs what its price says."""

    def __init__(self, turns: list[Turn], model: str) -> None:
        super().__init__(turns, model)
        self.price = PRICES[model]

    def _next(self, request: ProviderRequest) -> Turn:
        if is_state_write(request):  # none at this length; if one came, it is not one of the script's turns
            return Turn(text="(no State)")
        return super()._next(request)

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamItem]:
        await asyncio.sleep(0.3)
        async for item in super().stream(request):
            if isinstance(item, Completed):
                message = item.message
                usage = message.usage
                cost = (usage.input_tokens * self.price[0] + usage.output_tokens * self.price[1]) / 1e6
                usage = Usage(**{**usage.model_dump(), "cost_usd": cost})
                # Each model's own: the two scripts number their calls alike, and a call id is the session's.
                calls = [c.model_copy(update={"id": f"{self.model}:{c.id}"}) for c in message.tool_calls]
                item = Completed(message=message.model_copy(update={"usage": usage, "tool_calls": calls}))
            yield item


class Decision:
    """The decision model: ready for the hand-over as `READY` says, and the work advancing at each look."""

    name = "decisions-api:drex-latest"

    def __init__(self) -> None:
        self.ready = iter(READY)

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        await asyncio.sleep(0.2)
        answers: dict[str, Answer] = {}
        for name, question in questions.items():
            if name == "ready":
                p = next(self.ready, 0.9)
                answers[name] = ChoiceAnswer(
                    choice="ready" if p >= 0.5 else "not_ready",
                    probabilities={"ready": p, "not_ready": 0.97 - p, "unclear": 0.03},
                )
            elif isinstance(question, ChoiceQuestion):
                first = next(iter(question.criteria))
                rest = [c for c in question.criteria if c != first]
                answers[name] = ChoiceAnswer(
                    choice=first, probabilities={first: 0.91, **{c: 0.09 / len(rest) for c in rest}}
                )
            elif question.type == "score":
                answers[name] = ScoreAnswer(score=0.0)
            else:
                answers[name] = NoulAnswer(noul=0.05)
        return Answers(answers=answers, cost_usd=0.00006)


def workspace(root: Path) -> Path:
    """The project, and a `pytest` on PATH: the harness takes its own virtualenv off the agent's PATH, and the
    project has none, so it runs pytest from this interpreter."""
    work = root / "work"
    for path, text in PROJECT.items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "pytest").write_text(f'#!/bin/sh\nCOLUMNS=100 exec "{sys.executable}" -m pytest "$@"\n')
    (bin_dir / "pytest").chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return work


def opener(root: Path):
    work = workspace(root)
    providers = {FRONTIER: Priced(FRONTIER_TURNS, FRONTIER), EFFICIENT: Priced(EFFICIENT_TURNS, EFFICIENT)}

    def bound(ref: str) -> ModelBinding:
        return replace(binding(providers[ref], window=200_000), ref=ref, wire_model=ref.split("/")[-1])

    async def open_session(emit: EventSink, models: Models) -> Session:
        deps = EngineDeps(
            system2=bound(FRONTIER),
            tools=load_packs(["base"]),
            hooks=Hooks([], emit),
            journal=Journal(cwd=str(work), path=root / "journal.jsonl"),
            instructions=FileInstructions(root / "home", context_files=False).load(work),
            decisions=DecisionClient(Decision(), emit),
            executor=LocalExecutor(),
            cwd=work,
            spill_dir=root / "spill",
            emit=emit,
        )
        deps.handoff = HandoffTarget.of(
            bound(EFFICIENT), HandoffConfig(to=EFFICIENT, threshold=0.7, streak=1, min_turn=1), ["base"]
        )
        return Session(deps, system1="drex-latest")

    return open_session


async def catalog() -> Catalog:
    """The listing the masthead and the transcript take the models' display names from."""
    return Catalog(
        system2=[
            ModelInfo(id=FRONTIER, name="Anthropic: Claude Opus 5.5"),
            ModelInfo(id=EFFICIENT, name="Tencent: Hy4 Flash"),
        ],
        system1=[ModelInfo(id="drex-latest", name="Drex")],
    )


async def capture(dark: bool, svg: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = HarnessApp(opener(Path(tmp)), catalog=catalog, picks=Models(dark=dark))
        async with app.run_test(size=SIZE) as pilot:
            await wait_for(lambda: app.session is not None)
            await pilot.pause()
            app.query_one("#prompt", Prompt).text = PROMPT
            await pilot.press("enter")
            await pilot.pause()
            await wait_for(lambda: app._run_task is not None and app._run_task.done(), 60)  # pyright: ignore[reportPrivateUsage]
            await pilot.pause(0.5)
            # The whole run is longer than the screen: from the frontier model's last turn, so the hand-over and the
            # efficient model's turns are in view with the turn before them.
            transcript = app.query_one(Transcript)
            heads = [w for w in transcript.query(".turn-head") if not any(a.has_class("handed") for a in w.ancestors)]
            transcript.scroll_to_widget(heads[-1], top=True, animate=False)
            await pilot.pause(0.3)
            app.save_screenshot(filename=svg.name, path=str(svg.parent))
            await app.action_quit()


def png(svg: Path) -> Path:
    """`svg` as a PNG beside it, at twice its size."""
    found = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text())
    assert found is not None, f"no viewBox in {svg}"
    width, height = (round(float(n)) for n in found.groups())
    out = svg.with_suffix(".png")
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            f"--window-size={width},{height}",
            f"--screenshot={out}",
            svg.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/assets"))
    parser.add_argument("--no-png", action="store_true", help="keep the SVGs and make no PNGs")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        svg = args.out / f"tui-{theme}.svg"
        asyncio.run(capture(theme == "dark", svg))
        if args.no_png:
            print(svg)
        else:
            print(png(svg))
            svg.unlink()


if __name__ == "__main__":
    main()

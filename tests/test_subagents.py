"""Subagents: a child inherits the parent's capabilities and may be narrowed, never widened; it shares the parent's
budget, deadline and abort; it reads memory and never writes it; and its limits hold."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from intern import hooks as h
from intern.events import HookError, RunEnd, SubagentEnd, SubagentStart, ToolCallStart, TurnStart
from intern.extensions.guard import Guard
from intern.extensions.memory import Memory, ReadOnlyMemory
from intern.journal import Journal, MessageEntry
from intern.loop import EngineDeps, ModelBinding, RunControl
from intern.memory.drive import Drive
from intern.messages import SystemMessage, ToolCall, ToolResultMessage, UserMessage
from intern.models.profile import Profile
from intern.providers.base import ProviderRequest
from intern.providers.failover import BudgetPolicy, BudgetProvider, RunBudget
from intern.providers.fake import FakeProvider, Turn
from intern.session import Session, SessionBusy
from intern.subagents import (
    NOTE,
    RESULT_NOTE,
    SubagentRefused,
    SubagentResult,
    Subagents,
    SubagentSettings,
    SubagentsExhausted,
    SubagentSpec,
    child_extensions,
    child_tools,
    user_requests,
)
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput

from .support import Harness, binding

# -- helpers ----------------------------------------------------------------------------------------------------


async def until(condition) -> None:
    """Wait until `condition()` holds: the engine offers no event a test could await for these moments."""
    while not condition():  # noqa: ASYNC110 - polled state, not an event
        await asyncio.sleep(0.02)


def is_child(request: ProviderRequest) -> bool:
    return any(isinstance(m, SystemMessage) and NOTE in m.content for m in request.messages)


def tool_names(request: ProviderRequest) -> set[str]:
    return {spec.name for spec in request.tools}


def last_text(request: ProviderRequest) -> str:
    return next((m.content for m in reversed(request.messages) if isinstance(m, UserMessage)), "")


class DelegateArgs(BaseModel):
    tasks: list[str]
    tools: list[str] | None = None
    model: str | None = None


class DelegateTool(Tool):
    """What the `agent` tool will be: hands tasks to the run's subagents and returns their reports."""

    name = "agent"
    description = "Hand tasks to subagents."
    Args = DelegateArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "exec", "network"})

    async def run(self, args: DelegateArgs, ctx: ToolContext) -> ToolOutput:
        assert ctx.subagents is not None
        specs = [
            SubagentSpec(task=t, name=f"s{i}", tools=args.tools, model=args.model) for i, t in enumerate(args.tasks)
        ]
        try:
            results = await ctx.subagents.run_many(specs)
        except SubagentRefused as e:
            return ToolOutput(status="error", content=str(e))
        return ToolOutput(content="\n\n".join(r.report() for r in results))


class SlowArgs(BaseModel):
    label: str


class Overlap:
    """Records how many calls of the slow tools run at once."""

    def __init__(self) -> None:
        self.now = 0
        self.most = 0
        self.order: list[str] = []
        self.expect = 1
        """How many calls are allowed to run at once: each waits for that many peers before it ends."""


def slow_tool(name: str, effects: frozenset[Effect], overlap: Overlap, seconds: float = 0.2) -> Tool:
    class Slow(Tool):
        description = "Wait a little."
        Args = SlowArgs

        async def run(self, args: SlowArgs, ctx: ToolContext) -> ToolOutput:
            overlap.now += 1
            overlap.most = max(overlap.most, overlap.now)
            overlap.order.append(f"start {args.label}")
            # Wait for the expected peers (up to `seconds`), so a slow machine cannot hide an overlap that is allowed.
            loop = asyncio.get_running_loop()
            end = loop.time() + seconds
            while overlap.now < overlap.expect and loop.time() < end:  # noqa: ASYNC110 - counting peers
                await asyncio.sleep(0.01)
            if overlap.expect <= 1:
                await asyncio.sleep(seconds)
            overlap.now -= 1
            overlap.order.append(f"end {args.label}")
            return ToolOutput(content=f"done {args.label}")

    Slow.name = name
    Slow.effects = effects
    return Slow()


class WithTools(h.Extension):
    name = "test-tools"

    def __init__(self, *tools: Tool) -> None:
        self._tools = list(tools)

    def tools(self) -> list[Tool]:
        return self._tools


@pytest.fixture
def subagent_harness(make_harness):
    """A harness whose session has subagents, with the test `agent` tool among the parent's tools."""

    def make(script, *, settings: SubagentSettings | None = None, extensions=None, profile=None, extra_tools=()):
        harness: Harness = make_harness(
            script, extensions=[WithTools(DelegateTool(), *extra_tools), *(extensions or [])], profile=profile
        )
        harness.deps.subagents = settings or SubagentSettings()
        return harness

    return make


def delegating(child: Turn | list[Turn], *, tasks=("look around",), tools=None, model=None, final="all done"):
    """A script: the parent hands `tasks` out, each child replies with `child` (one turn, or turns in order), then
    the parent ends with `final`."""
    child_turns = child if isinstance(child, list) else [child]
    seen: dict[str, int] = {}

    def script(request: ProviderRequest) -> Turn:
        if is_child(request):
            key = last_text(request)
            for message in request.messages:
                if (
                    isinstance(message, UserMessage)
                    and message.source == "harness"
                    and "Do only this task" in message.content
                ):
                    key = message.content
                    break
            index = seen.get(key, 0)
            seen[key] = index + 1
            return child_turns[min(index, len(child_turns) - 1)]
        if not any(isinstance(m, ToolResultMessage) for m in request.messages):
            return Turn(tool_calls=[("agent", {"tasks": list(tasks), "tools": tools, "model": model})])
        return Turn(text=final)

    return script


# -- what a child gets ------------------------------------------------------------------------------------------


async def test_a_child_inherits_the_parents_tools_except_process_and_the_agent_tool(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="found it")))
    outcome = await harness.run()

    child_requests = [r for r in harness.provider.requests if is_child(r)]
    assert child_requests, "the child made no model call"
    parent_tools = {tool.name for tool in harness.deps.tools}
    assert tool_names(child_requests[0]) == parent_tools - {"agent", "process"}
    assert "process" in parent_tools  # it was the parent's, and still was not passed on
    assert outcome.reason == "completed"
    assert (
        "found it"
        in next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result.content
    )


async def test_the_parent_narrows_a_childs_tools(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="ok"), tools=["read", "grep"]))
    await harness.run()
    child = next(r for r in harness.provider.requests if is_child(r))
    assert tool_names(child) == {"read", "grep"}


@pytest.mark.parametrize(
    ("asked", "why"),
    [
        (["read", "web_search"], "has no tool web_search"),
        (["read", "process"], "never gets process"),
        (["agent"], "never gets agent"),
    ],
)
async def test_a_child_never_gets_a_tool_the_parent_lacks_or_one_never_passed_on(subagent_harness, asked, why):
    harness = subagent_harness(delegating(Turn(text="should not run"), tools=asked))
    await harness.run()
    assert not any(is_child(r) for r in harness.provider.requests), "a refused child started anyway"
    assert not harness.recorder.of(SubagentStart)
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "error" and why in result.content


def test_child_tools_keep_the_parents_tool_objects():
    class A(Tool):
        name, description, Args, effects = "a", "", SlowArgs, frozenset[Effect]({"read"})

        async def run(self, args, ctx):  # pragma: no cover
            return ToolOutput(content="")

    tool = A()
    assert child_tools([tool], None)[0] is tool
    assert child_tools([tool], ["a"])[0] is tool


async def test_the_child_sees_the_users_request_as_theirs_and_the_task_as_the_parents(subagent_harness, tmp_path):
    harness = subagent_harness(delegating(Turn(text="ok"), tasks=["count the files"]))
    harness.deps.journal = Journal(str(harness.cwd), path=tmp_path / "s.jsonl")
    await harness.run("please tidy the repo")
    path = harness.recorder.of(SubagentStart)[0].journal
    assert path is not None
    child = Journal(str(harness.cwd), path=Path(path))
    users = [
        e.message for e in child.active_path() if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]
    assert [(m.source, m.content) for m in users if m.source == "user"] == [("user", "please tidy the repo")]
    task = next(m for m in users if m.source == "harness")
    assert "count the files" in task.content
    # The model's words never stand as the user's: System 1 and the guard read only `source == "user"`.
    assert all("count the files" not in m.content for m in users if m.source == "user")


def test_user_requests_are_the_users_latest_three_oldest_first(tmp_path):
    journal = Journal(str(tmp_path))
    for text in ("one", "two", "three", "four"):
        journal.append(MessageEntry(message=UserMessage(content=text)))
        journal.append(MessageEntry(message=UserMessage(content=f"nudge after {text}", source="harness")))
    assert user_requests(journal) == ["two", "three", "four"]
    assert user_requests(Journal(str(tmp_path))) == []


async def test_the_child_sees_the_users_latest_requests_so_a_go_ahead_counts(subagent_harness, tmp_path):
    harness = subagent_harness(delegating(Turn(text="ok")))
    harness.deps.journal = Journal(str(harness.cwd), path=tmp_path / "s.jsonl")
    harness.deps.journal.append(MessageEntry(message=UserMessage(content="delete the build folder")))
    await harness.run("yes, go on")
    child = Journal(str(harness.cwd), path=Path(harness.recorder.of(SubagentStart)[0].journal or ""))
    users = [
        e.message for e in child.active_path() if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]
    assert [m.content for m in users if m.source == "user"] == ["delete the build folder", "yes, go on"]


# -- usage, events, journals ------------------------------------------------------------------------------------


async def test_a_childs_usage_is_the_parents_and_its_events_stay_in_its_own_journal(subagent_harness):
    harness = subagent_harness(
        delegating([Turn(tool_calls=[("ls", {})], output_tokens=7), Turn(text="ok", output_tokens=5)])
    )
    await harness.run()

    end = harness.recorder.end
    sub = harness.recorder.of(SubagentEnd)
    assert len(sub) == 1 and sub[0].status == "completed" and sub[0].turns == 2
    assert sub[0].usage.output_tokens == 12
    assert end.subagent_usage == sub[0].usage
    # The parent's two replies are 10 output tokens each; the child's 12 are added to the run's usage, not its agent's.
    assert end.agent_usage is not None and end.agent_usage.output_tokens == 20
    assert end.usage.output_tokens == 32
    # The child's turns and tool calls are not the parent's: a client summing `turn_start` or tool calls counts the
    # parent's alone.
    assert len(harness.recorder.of(TurnStart)) == 2
    assert [e.name for e in harness.recorder.of(ToolCallStart)] == ["agent"]
    assert len(harness.recorder.of(RunEnd)) == 1


async def test_a_run_without_subagents_reports_none(make_harness):
    harness = make_harness([Turn(text="hi")])
    await harness.run()
    assert harness.recorder.end.subagent_usage is None


async def test_a_childs_journal_is_kept_beside_a_kept_parent_journal(subagent_harness, tmp_path):
    harness = subagent_harness(delegating(Turn(text="ok")))
    harness.deps.journal = Journal(str(harness.cwd), path=tmp_path / "sessions" / "s1.jsonl")
    await harness.run()
    start = harness.recorder.of(SubagentStart)[0]
    assert start.journal is not None
    path = Path(start.journal)
    assert path.parent == tmp_path / "sessions" / "s1.subagents"
    assert await asyncio.to_thread(path.is_file)
    child = Journal(str(harness.cwd), path=path)
    assert any(isinstance(e, MessageEntry) and isinstance(e.message, UserMessage) for e in child.active_path())
    # The session folder lists the parent's journal alone, so nothing that reads sessions takes a child for one.
    assert sorted(p.name for p in (tmp_path / "sessions").glob("*.jsonl")) == ["s1.jsonl"]


async def test_an_in_memory_parent_keeps_no_child_journal(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="ok")))
    await harness.run()
    assert harness.recorder.of(SubagentStart)[0].journal is None
    assert harness.recorder.of(SubagentEnd)[0].status == "completed"


async def test_a_childs_hook_error_reaches_the_parent(subagent_harness):
    class Breaks(h.Extension):
        name = "breaks"

        def for_subagent(self):
            return Breaks()

        async def before_agent_start(self, ctx):
            if ctx.subagents is None:  # only in the child
                raise RuntimeError("boom")

    harness = subagent_harness(delegating(Turn(text="ok")), extensions=[Breaks()])
    await harness.run()
    errors = harness.recorder.of(HookError)
    assert [(e.extension, e.error) for e in errors] == [("breaks", "RuntimeError: boom")]
    assert harness.recorder.of(SubagentEnd)[0].status == "completed"


def test_the_report_marks_the_reply_as_notes_to_check():
    result = SubagentResult(
        subagent_id="x",
        name="scout",
        status="completed",
        final_text="It is in a.py:3",
        turns=2,
        usage=Usage0(),
        duration_ms=1,
    )
    report = result.report()
    assert RESULT_NOTE in report and "It is in a.py:3" in report and "scout" in report
    empty = result.model_copy(update={"final_text": "  "})
    assert "(no reply)" in empty.report()


def Usage0():
    from intern.messages import Usage

    return Usage()


# -- safety and memory ------------------------------------------------------------------------------------------


async def test_the_guard_runs_in_every_child_with_a_count_of_its_own(subagent_harness):
    guard = Guard(system1=False)
    harness = subagent_harness(
        delegating([Turn(tool_calls=[("bash", {"command": "sudo ls"})]), Turn(text="refused, as expected")]),
        extensions=[guard],
    )
    await harness.run()
    child = harness.recorder.of(SubagentEnd)[0]
    assert child.status == "completed"
    journal_text = [r for r in harness.provider.requests if is_child(r)][-1]
    result = next(m for m in journal_text.messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "refused"
    assert guard.total == 0  # the parent's guard counted nothing: the child had a guard of its own
    built = child_extensions([guard])
    assert len(built) == 1 and isinstance(built[0], Guard) and built[0] is not guard


async def test_a_child_reads_memory_and_never_writes_the_drive(subagent_harness, tmp_path):
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    memory = Memory(Drive(drive_root, "c0", {}))
    harness = subagent_harness(
        delegating(
            [
                Turn(tool_calls=[("write", {"path": str(drive_root / "lessons" / "x.md"), "content": "a lesson"})]),
                Turn(text="ok"),
            ]
        ),
        extensions=[memory],
    )
    await harness.run()
    child = [r for r in harness.provider.requests if is_child(r)][-1]
    result = next(m for m in child.messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "refused" and "memory drive" in result.content
    assert not (drive_root / "lessons").exists()
    built = child_extensions([memory])
    assert len(built) == 1 and isinstance(built[0], ReadOnlyMemory)
    assert built[0].tools() == []  # no recall, no tools of its own: `memory_search` comes with the parent's tools


async def test_an_extension_that_gates_calls_and_does_not_say_how_stops_subagents(subagent_harness):
    class Gate(h.Extension):
        name = "company-gate"

        async def tool_call(self, call: ToolCall, ctx):
            return None

    harness = subagent_harness(delegating(Turn(text="should not run")), extensions=[Gate()])
    outcome = await harness.run()
    assert outcome.reason == "completed"  # the parent's run is fine
    assert not any(is_child(r) for r in harness.provider.requests)
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "error" and "company-gate" in result.content


def test_an_extension_that_only_shapes_the_parents_run_is_left_out_of_children():
    class Notes(h.Extension):
        name = "notes"

        async def context(self, view, ctx):
            return None

    assert child_extensions([Notes()]) == []


async def test_a_child_inherits_secret_redaction(subagent_harness):
    harness = subagent_harness(
        delegating([Turn(tool_calls=[("bash", {"command": "echo hunter2-secret"})]), Turn(text="ok")])
    )
    harness.deps.redact = lambda text: text.replace("hunter2-secret", "[REDACTED]")
    await harness.run()
    child = [r for r in harness.provider.requests if is_child(r)][-1]
    result = next(m for m in child.messages if isinstance(m, ToolResultMessage)).result
    assert "[REDACTED]" in result.content and "hunter2-secret" not in result.content


# -- limits -----------------------------------------------------------------------------------------------------


async def test_a_child_cannot_start_children():
    deps = EngineDeps.__new__(EngineDeps)
    deps.child = True
    with pytest.raises(SubagentRefused, match="cannot start subagents"):
        Subagents(deps, SubagentSettings(), asyncio.Event())


async def test_the_childs_run_has_no_subagents(subagent_harness):
    seen: list[object] = []

    class Spy(h.Extension):
        name = "spy"

        def for_subagent(self):
            return self

        async def before_agent_start(self, ctx):
            seen.append(ctx.subagents)

    harness = subagent_harness(delegating(Turn(text="ok")), extensions=[Spy()])
    await harness.run()
    assert seen[0] is not None and seen[1] is None  # the parent's run has them, the child's none


async def test_a_childs_turn_budget_is_never_above_the_parents(subagent_harness):
    harness = subagent_harness(
        delegating(Turn(tool_calls=[("ls", {})])), settings=SubagentSettings(max_turns=50), profile=Profile(max_turns=3)
    )
    await harness.run()
    child = harness.recorder.of(SubagentEnd)[0]
    assert child.status == "turn_budget"
    assert child.turns == 3 + 1  # the parent's 3, then the tool-less wrap-up turn


async def test_a_spec_may_ask_for_fewer_turns_only(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="unused")), settings=SubagentSettings(max_turns=5))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    assert subagents.turns(SubagentSpec(task="x", max_turns=2)) == 2
    assert subagents.turns(SubagentSpec(task="x", max_turns=10_000)) == 5  # never above the settings
    harness.deps.system2.profile.max_turns = 3
    assert subagents.turns(SubagentSpec(task="x")) == 3  # nor above the parent's
    with pytest.raises(ValueError):
        SubagentSpec(task="x", max_turns=0)


async def test_a_spec_asking_for_more_turns_still_stops_at_the_settings(subagent_harness):
    harness = subagent_harness(lambda r: Turn(tool_calls=[("ls", {})]), settings=SubagentSettings(max_turns=2))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    result = await subagents.run(SubagentSpec(task="x", max_turns=500))
    assert result.status == "turn_budget" and result.turns == 2 + 1


async def test_a_run_starts_at_most_max_children(subagent_harness):
    harness = subagent_harness(
        delegating(Turn(text="ok"), tasks=["a", "b", "c"]), settings=SubagentSettings(max_children=2)
    )
    await harness.run()
    assert not harness.recorder.of(SubagentStart)  # three asked, two allowed: none start
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "error" and "2 of this run's 2 are left" in result.content


async def test_the_limit_counts_every_child_of_the_run(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="unused")), settings=SubagentSettings(max_children=1))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    harness.deps.journal.append(MessageEntry(message=UserMessage(content="go")))
    harness.provider._script = lambda request: Turn(text="ok")
    await subagents.run(SubagentSpec(task="one"))
    with pytest.raises(SubagentsExhausted, match="0 of this run's 1 are left"):
        await subagents.run(SubagentSpec(task="two"))
    with pytest.raises(SubagentRefused, match="no task"):
        await subagents.run_many([])


async def test_reading_children_run_together_and_writing_children_one_at_a_time(subagent_harness):
    overlap = Overlap()
    reader = slow_tool("peek", frozenset({"read"}), overlap)
    writer = slow_tool("poke", frozenset({"write"}), overlap)

    def script(tool: str):
        def child(request: ProviderRequest) -> Turn:
            if any(isinstance(m, ToolResultMessage) for m in request.messages):
                return Turn(text="done")
            return Turn(tool_calls=[(tool, {"label": last_text(request)[-1]})])

        return child

    harness = subagent_harness(
        lambda r: Turn(text="unused"), settings=SubagentSettings(max_parallel=4), extra_tools=(reader, writer)
    )
    harness.deps.journal.append(MessageEntry(message=UserMessage(content="go")))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]

    harness.provider._script = script("peek")
    overlap.expect = 3
    await subagents.run_many([SubagentSpec(task=f"read {i}", tools=["peek"]) for i in range(3)])
    assert overlap.most == 3

    overlap.most, overlap.expect = 0, 1
    harness.provider._script = script("poke")
    await subagents.run_many([SubagentSpec(task=f"write {i}", tools=["poke"]) for i in range(3)])
    assert overlap.most == 1


async def test_parallel_children_respect_max_parallel(subagent_harness):
    overlap = Overlap()
    overlap.expect = 2
    reader = slow_tool("peek", frozenset({"read"}), overlap, seconds=2.0)

    def child(request: ProviderRequest) -> Turn:
        if any(isinstance(m, ToolResultMessage) for m in request.messages):
            return Turn(text="done")
        return Turn(tool_calls=[("peek", {"label": "x"})])

    harness = subagent_harness(child, settings=SubagentSettings(max_parallel=2), extra_tools=(reader,))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    results = await subagents.run_many([SubagentSpec(task=f"t{i}", tools=["peek"]) for i in range(5)])
    assert overlap.most == 2
    assert [r.status for r in results] == ["completed"] * 5


async def test_results_come_back_in_the_order_asked(subagent_harness):
    def child(request: ProviderRequest) -> Turn:
        return Turn(text="answer to " + last_text(request).rsplit("\n", 1)[-1])

    harness = subagent_harness(child)
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    results = await subagents.run_many([SubagentSpec(task=t, tools=["read"]) for t in ("alpha", "beta", "gamma")])
    assert [r.final_text for r in results] == ["answer to alpha", "answer to beta", "answer to gamma"]


# -- abort, deadline, budget, failures --------------------------------------------------------------------------


async def test_aborting_the_parent_stops_a_child_mid_command(subagent_harness):
    async with asyncio.timeout(20):  # a regression fails here instead of hanging on `sleep 30`
        harness = subagent_harness(
            delegating([Turn(tool_calls=[("bash", {"command": "sleep 30"})]), Turn(text="late")])
        )
        control = RunControl()

        async def abort_soon():
            await until(lambda: harness.recorder.of(SubagentStart))
            await asyncio.sleep(0.3)
            control.abort.set()

        started = time.monotonic()
        aborter = asyncio.create_task(abort_soon())
        outcome = await harness.run(control=control)
        await aborter
        assert time.monotonic() - started < 10
        assert outcome.reason == "aborted"
        child = harness.recorder.of(SubagentEnd)[0]
        assert child.status == "aborted" and child.detail == "the parent run was aborted"


async def test_a_child_started_after_the_abort_takes_no_turn(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="should not run"))
    abort = asyncio.Event()
    abort.set()
    subagents = Subagents(harness.deps, harness.deps.subagents, abort)  # type: ignore[arg-type]
    result = await subagents.run(SubagentSpec(task="x"))
    assert result.status == "aborted" and result.turns == 0
    assert not harness.provider.requests


async def test_a_child_past_its_time_limit_is_stopped(subagent_harness):
    async with asyncio.timeout(20):  # a regression fails here instead of hanging on `sleep 30`
        harness = subagent_harness(
            delegating([Turn(tool_calls=[("bash", {"command": "sleep 30"})]), Turn(text="late")]),
            settings=SubagentSettings(timeout_s=0.5),
        )
        started = time.monotonic()
        outcome = await harness.run()
        assert time.monotonic() - started < 10
        child = harness.recorder.of(SubagentEnd)[0]
        assert child.status == "timed_out" and "0.5 s" in (child.detail or "")
        assert outcome.reason == "completed"  # the parent goes on with what it got


async def test_children_spend_the_parents_budget(subagent_harness):
    budget = RunBudget(BudgetPolicy(max_calls=3))
    harness = subagent_harness(
        delegating([Turn(tool_calls=[("ls", {})]), Turn(tool_calls=[("ls", {})]), Turn(text="ok")])
    )
    harness.deps.system2 = binding(BudgetProvider(harness.provider, budget))
    outcome = await harness.run()
    # 1 parent call + 2 child calls spend the 3; the child's third call and the parent's next one are refused.
    child = harness.recorder.of(SubagentEnd)[0]
    assert child.status == "provider_error"
    assert outcome.reason == "provider_error"
    assert (await budget.snapshot()).calls == 3


async def test_children_share_the_parents_deadline(subagent_harness):
    clock = [1000.0]
    budget = RunBudget(BudgetPolicy(deadline_unix=1001.0), clock=lambda: clock[0])

    def script(request: ProviderRequest) -> Turn:
        if is_child(request):
            clock[0] = 2000.0  # the deadline passes while the child works
            return Turn(tool_calls=[("ls", {})])
        if not any(isinstance(m, ToolResultMessage) for m in request.messages):
            return Turn(tool_calls=[("agent", {"tasks": ["x"]})])
        return Turn(text="too late")

    harness = subagent_harness(script)
    harness.deps.system2 = binding(BudgetProvider(harness.provider, budget))
    outcome = await harness.run()
    child = harness.recorder.of(SubagentEnd)[0]
    assert child.status == "provider_error" and "deadline" in (child.detail or "")
    assert outcome.reason == "provider_error"


async def test_a_failing_child_does_not_end_the_parents_run(subagent_harness):
    harness = subagent_harness(delegating(Turn(error="model exploded")))
    outcome = await harness.run()
    assert outcome.reason == "completed"
    child = harness.recorder.of(SubagentEnd)[0]
    assert child.status == "provider_error"
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert "provider_error" in result.content


async def test_a_cancelled_child_still_reports_its_end(subagent_harness):
    async with asyncio.timeout(20):  # a regression fails here instead of hanging on `sleep 30`
        harness = subagent_harness(lambda r: Turn(tool_calls=[("bash", {"command": "sleep 30"})]))
        subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
        task = asyncio.create_task(subagents.run(SubagentSpec(task="x")))
        await until(lambda: harness.provider.requests)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ends = harness.recorder.of(SubagentEnd)
        assert len(ends) == 1 and ends[0].status == "aborted"


@pytest.mark.parametrize("yields", range(1, 9))
async def test_a_spawn_cancelled_at_any_moment_leaves_the_writers_lock_free(subagent_harness, yields):
    # Cancelled just as it got the lock, the spawn kept it: no child that can write ran again in the session.
    harness = subagent_harness(lambda r: Turn(text="done"))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    task = asyncio.create_task(subagents.run(SubagentSpec(task="x")))
    for _ in range(yields):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with asyncio.timeout(5):
        assert (await subagents.run(SubagentSpec(task="y"))).status == "completed"

    # -- models -----------------------------------------------------------------------------------------------------


async def test_the_parent_may_pick_only_an_allowed_model(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="ok"), model="other/expensive"))
    await harness.run()
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "error" and "not allowed" in result.content and "allows no other model" in result.content
    assert not harness.recorder.of(SubagentStart)


async def test_the_host_default_and_an_allowed_model_are_bound_by_the_session(subagent_harness):
    small = FakeProvider([Turn(text="from the small model")], model="small")
    bound: list[str] = []

    async def resolve(ref: str) -> ModelBinding:
        bound.append(ref)
        result = binding(small)
        result.ref = ref
        return result

    harness = subagent_harness(
        lambda r: Turn(text="parent"), settings=SubagentSettings(model="cheap/one", models=["cheap/two"])
    )
    harness.deps.resolve_model = resolve
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    first = await subagents.run(SubagentSpec(task="x"))
    assert bound == ["cheap/one"] and first.final_text == "from the small model"
    small._script = [Turn(text="second")]
    small._index = 0
    await subagents.run(SubagentSpec(task="y", model="cheap/two"))
    assert bound == ["cheap/one", "cheap/two"]
    assert harness.recorder.of(SubagentStart)[0].model == "cheap/one"
    with pytest.raises(SubagentRefused, match="pick one of: cheap/one, cheap/two"):
        await subagents.run(SubagentSpec(task="z", model="any/other"))


async def test_the_parents_own_model_is_always_allowed(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="ok"))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    result = await subagents.run(SubagentSpec(task="x", model=harness.deps.system2.ref))
    assert result.status == "completed"


# -- the host's spawn -------------------------------------------------------------------------------------------


async def test_a_host_spawns_a_subagent_whose_task_is_the_users_words(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="host answer"))
    session = Session(harness.deps)
    try:
        result = await session.spawn(SubagentSpec(task="summarize README"))
    finally:
        await session.aclose()
    assert result.final_text == "host answer"
    child = harness.provider.requests[0]
    users = [m for m in child.messages if isinstance(m, UserMessage)]
    assert users[-1].content.endswith("summarize README")
    assert all(m.source == "user" and "Do only this task" not in m.content for m in users)
    assert session.usage.output_tokens == result.usage.output_tokens > 0


async def test_a_session_without_subagents_refuses_a_spawn(make_harness):
    harness = make_harness([Turn(text="x")])
    session = Session(harness.deps)
    try:
        with pytest.raises(SubagentRefused, match="no subagents"):
            await session.spawn(SubagentSpec(task="x"))
    finally:
        await session.aclose()


async def test_closing_the_session_aborts_a_hosts_subagent(subagent_harness):
    async with asyncio.timeout(20):  # a regression fails here instead of hanging on `sleep 30`
        harness = subagent_harness(lambda r: Turn(tool_calls=[("bash", {"command": "sleep 30"})]))
        session = Session(harness.deps, shutdown_timeout_s=5)
        task = asyncio.create_task(session.spawn(SubagentSpec(task="x")))
        await until(lambda: harness.provider.requests)
        started = time.monotonic()
        await session.aclose()
        result = await task
        assert time.monotonic() - started < 6
        assert result.status == "aborted"
        with pytest.raises(SessionBusy):
            await session.spawn(SubagentSpec(task="y"))


async def test_child_runs_are_not_supervised_by_system1(subagent_harness):
    from intern.decisions import DecisionClient

    from .support import Quiet

    quiet = Quiet()
    harness = subagent_harness(delegating([*[Turn(tool_calls=[("ls", {})])] * 9, Turn(text="ok")]))
    harness.deps.decisions = DecisionClient(quiet, harness.recorder)
    await harness.run()
    assert harness.recorder.of(SubagentEnd)[0].turns == 10  # long enough for System 1 to look more than once
    # The parent's turns may be supervised; none of System 1's asks reads the child's session.
    assert [state for state, _ in quiet.asks if "Do only this task" in state] == []


# -- review findings: each can fail for a reason a user cares about ---------------------------------------------


async def test_a_childs_system1_asks_are_kept_in_its_journal_not_the_parents(subagent_harness, tmp_path):
    from intern.decisions import DecisionClient
    from intern.events import Decision
    from intern.journal import EventEntry

    from .support import Quiet

    harness = subagent_harness(
        delegating([Turn(tool_calls=[("bash", {"command": "git push origin main"})]), Turn(text="ok")]),
        extensions=[Guard()],
    )
    harness.deps.decisions = DecisionClient(Quiet(), harness.recorder)
    harness.deps.journal = Journal(str(harness.cwd), path=tmp_path / "s.jsonl")
    await harness.run()
    child = Journal(str(harness.cwd), path=Path(harness.recorder.of(SubagentStart)[0].journal or ""))
    child_asks = [e for e in child.active_path() if isinstance(e, EventEntry) and isinstance(e.event, Decision)]
    assert child_asks, "the child's guard asked System 1 nothing"
    streamed = [e for e in harness.recorder.of(Decision) if e.purpose == "guard"]
    # The parent's stream (what a session journals) has its own guard ask, about its `agent` call; the child's is
    # in the child's journal only.
    assert len(streamed) == 1
    assert harness.deps.decisions.usage.asks >= 2  # one System 1, one usage: the child's ask counts


async def test_a_host_can_spawn_more_than_max_children_over_a_session(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="ok"), settings=SubagentSettings(max_children=1))
    session = Session(harness.deps)
    try:
        for i in range(3):
            assert (await session.spawn(SubagentSpec(task=f"task {i}"))).status == "completed"
    finally:
        await session.aclose()


async def test_a_cancelled_child_reports_what_it_spent(subagent_harness):
    def script(request: ProviderRequest) -> Turn:
        return Turn(tool_calls=[("bash", {"command": "sleep 30"})], output_tokens=33)

    harness = subagent_harness(script)
    spent: list[SubagentResult] = []
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event(), on_end=spent.append)  # type: ignore[arg-type]
    async with asyncio.timeout(20):
        task = asyncio.create_task(subagents.run(SubagentSpec(task="x")))
        await until(lambda: harness.provider.requests)
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert spent[0].status == "aborted" and spent[0].usage.output_tokens == 33 and spent[0].turns == 1


async def test_a_subclass_of_the_guard_that_has_not_answered_stops_subagents(subagent_harness):
    class StrictGuard(Guard):
        name = "strict-guard"

    harness = subagent_harness(delegating(Turn(text="never")), extensions=[StrictGuard()])
    await harness.run()
    assert not any(is_child(r) for r in harness.provider.requests)
    result = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result
    assert result.status == "error" and "strict-guard" in result.content


@pytest.mark.parametrize("hook", ["turn_end", "before_provider_request"])
async def test_an_extension_enforcing_through_other_hooks_must_answer_too(subagent_harness, hook):
    async def enforce(self, *args):
        return None

    Enforcer = type("Enforcer", (h.Extension,), {"name": "enforcer", hook: enforce})
    with pytest.raises(SubagentRefused, match="enforcer"):
        child_extensions([Enforcer()])


async def test_changing_max_parallel_takes_effect(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="ok"), settings=SubagentSettings(max_parallel=4))
    Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    first = await Subagents(harness.deps, harness.deps.subagents, asyncio.Event()).run(SubagentSpec(task="a"))  # type: ignore[arg-type]
    assert first.status == "completed" and harness.deps.subagent_slots is not None
    assert harness.deps.subagent_slots.limit == 4
    harness.deps.subagents = SubagentSettings(max_parallel=1)
    await Subagents(harness.deps, harness.deps.subagents, asyncio.Event()).run(SubagentSpec(task="b"))
    assert harness.deps.subagent_slots.limit == 1


async def test_naming_the_parents_model_runs_on_it_even_with_a_host_default(subagent_harness):
    bound: list[str] = []

    async def resolve(ref: str) -> ModelBinding:
        bound.append(ref)
        raise AssertionError("the parent's model needs no binding")

    harness = subagent_harness(lambda r: Turn(text="parent model"), settings=SubagentSettings(model="cheap/one"))
    harness.deps.resolve_model = resolve
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    result = await subagents.run(SubagentSpec(task="x", model=harness.deps.system2.ref))
    assert result.status == "completed" and bound == []


async def test_a_bound_child_model_keeps_the_sessions_window_cap(subagent_harness):
    from intern.events import WindowInfo

    big = binding(FakeProvider([Turn(text="ok")]), window=200_000)
    big.ref = "cheap/one"

    async def resolve(ref: str) -> ModelBinding:
        return big

    harness = subagent_harness(lambda r: Turn(text="x"), settings=SubagentSettings(model="cheap/one"))
    harness.deps.system2.window = WindowInfo(
        advertised=32_768, advertised_source="profile", effective=None, user_cap=16_000, working=16_000
    )
    harness.deps.resolve_model = resolve
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    child = await subagents._binding("cheap/one", 5)
    assert child.window.working == 16_000 and child.profile.max_turns == 5


async def test_what_the_parent_stream_shows_of_a_child_is_redacted(subagent_harness):
    harness = subagent_harness(delegating(Turn(text="the key is s3cr3t-value"), tasks=["use s3cr3t-value"]))
    harness.deps.redact = lambda text: text.replace("s3cr3t-value", "[REDACTED]")
    await harness.run()
    start, end = harness.recorder.of(SubagentStart)[0], harness.recorder.of(SubagentEnd)[0]
    assert "s3cr3t-value" not in start.task and "s3cr3t-value" not in end.final_text
    report = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)).result.content
    assert "s3cr3t-value" not in report


async def test_the_time_limit_covers_waiting_for_a_slot(subagent_harness):
    overlap = Overlap()
    writer = slow_tool("poke", frozenset({"write"}), overlap, seconds=2.0)

    def child(request: ProviderRequest) -> Turn:
        if any(isinstance(m, ToolResultMessage) for m in request.messages):
            return Turn(text="done")
        return Turn(tool_calls=[("poke", {"label": "w"})])

    harness = subagent_harness(child, settings=SubagentSettings(timeout_s=0.5), extra_tools=(writer,))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    async with asyncio.timeout(20):
        first, second = await subagents.run_many(
            [SubagentSpec(task="a", tools=["poke"]), SubagentSpec(task="b", tools=["poke"])]
        )
    assert first.status == "timed_out" and second.status == "timed_out"
    assert second.turns == 0  # it never got the writers' lock, and never started
    assert second.duration_ms < 1_500  # it stopped waiting at its limit, not when the first let the lock go


async def test_a_refusal_among_several_tasks_names_the_task(subagent_harness):
    harness = subagent_harness(lambda r: Turn(text="never"))
    subagents = Subagents(harness.deps, harness.deps.subagents, asyncio.Event())  # type: ignore[arg-type]
    specs = [SubagentSpec(task="a", name="ok", tools=["read"]), SubagentSpec(task="b", name="bad", tools=["nope"])]
    with pytest.raises(SubagentRefused, match=r"^task 2 \(bad\): the parent has no tool nope"):
        await subagents.run_many(specs)
    assert subagents.started == 0 and not harness.provider.requests

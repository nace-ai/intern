"""What a run's tool calls came to, for the error analysis of eval runs: how many calls each tool had, how many
succeeded, and where each of the others failed.

Off unless named: `--extension tool-summary`, or a profile's `extensions`. At the end of every run it emits one
`ExtensionEvent` (`tool-summary`, kind `summary`) holding a `ToolCallSummary`, which `-p --json` writes to stdout with
the other events. A run a finish gate reopens sends one again, counting the whole run so far, so a run's last summary
is its whole run. A run that ends on an exception (`internal_error`, `system1_unavailable`) sends none, and summaries
are not journaled: `summaries()` builds the same ones from a journal, for any run, and `evals/tool_calls.py` from
folders of them. Nothing here changes what a run does or what the model sees.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from intern.events import ExtensionEvent
from intern.hooks import Extension, RunContext, RunOutcome, TurnInfo, Verdict
from intern.journal import Entry, MessageEntry
from intern.messages import AssistantMessage, InvalidToolCall, ToolErrorKind, ToolResult, ToolResultMessage

FailureKind = ToolErrorKind | Literal["invalid_json"]
"""A `ToolErrorKind`, or `invalid_json`: the call's arguments were not JSON, so it was not run and has no result."""

# Where a call failed, told by its text, for errors journaled before `ToolError.kind`: the loop's own messages.
_LEGACY_KINDS: tuple[tuple[str, FailureKind], ...] = (
    ("there is no tool named", "unknown_tool"),
    ("was not run: it does not take", "bad_arguments"),
    ("invalid arguments for", "bad_arguments"),
    ("failed unexpectedly:", "crashed"),
    ("the run was stopped", "stopped"),
    ("the run was aborted", "stopped"),
    ("The run was aborted.", "stopped"),
    ("The previous run ended without a persisted result", "interrupted"),
)


def failure_kind(result: ToolResult) -> FailureKind:
    """Where a call that did not succeed failed: its error's `kind`, or for an error journaled before there was one,
    the kind its text names."""
    error = result.error
    if error is not None and error.kind is not None:
        return error.kind
    what_failed = error.what_failed if error is not None else result.content
    for text, kind in _LEGACY_KINDS:
        if text in what_failed:
            return kind
    return "refused" if result.status == "refused" else "failed"


class ToolFailure(BaseModel):
    """One call that did not succeed."""

    turn: int = Field(
        description="The run's turn that made the call, counted from 1; 0 for a call an earlier run left `interrupted`."
    )
    call_id: str
    tool: str
    kind: FailureKind
    what_failed: str
    argument: str | None = Field(default=None, description="The argument at fault, where the error names one.")
    refused_by: str | None = Field(default=None, description="With `refused`: the extension that refused it.")
    raw_arguments: str | None = Field(
        default=None, description="With `invalid_json`: the arguments as the model sent them."
    )


class ToolStats(BaseModel):
    calls: int = 0
    ok: int = 0
    failures: dict[FailureKind, int] = Field(default_factory=dict, description="Calls that did not succeed, by kind.")
    duration_ms: int = Field(default=0, description="Time its calls took, together.")


class ToolCallSummary(BaseModel):
    """A run's tool calls: counted by tool and by kind of failure, with every call that did not succeed."""

    run_id: str
    turns: int = 0
    calls: int = 0
    ok: int = 0
    failures_by_kind: dict[FailureKind, int] = Field(default_factory=dict)
    by_tool: dict[str, ToolStats] = Field(default_factory=dict)
    failures: list[ToolFailure] = Field(default_factory=list)

    def add_turn(self, turn: int, invalid: Iterable[InvalidToolCall], results: Iterable[ToolResult]) -> None:
        """Count one reply's calls: those whose arguments did not parse, and the results of those that were sent."""
        self.turns = max(self.turns, turn)
        for call in invalid:
            self._failed(
                ToolFailure(
                    turn=turn,
                    call_id=call.id,
                    tool=call.name,
                    kind="invalid_json",
                    what_failed=call.error,
                    raw_arguments=call.raw_arguments,
                ),
                duration_ms=0,
            )
        for result in results:
            if result.status == "ok":
                stats = self._stats(result.tool_name)
                stats.calls += 1
                stats.ok += 1
                stats.duration_ms += result.duration_ms
                self.calls += 1
                self.ok += 1
                continue
            error = result.error
            failure = ToolFailure(
                turn=turn,
                call_id=result.call_id,
                tool=result.tool_name,
                kind=failure_kind(result),
                what_failed=error.what_failed if error is not None else result.content,
                argument=error.argument if error is not None else None,
                refused_by=error.refused_by if error is not None else None,
            )
            self._failed(failure, duration_ms=result.duration_ms)

    def _stats(self, tool: str) -> ToolStats:
        return self.by_tool.setdefault(tool, ToolStats())

    def _failed(self, failure: ToolFailure, duration_ms: int) -> None:
        stats = self._stats(failure.tool)
        stats.calls += 1
        stats.failures[failure.kind] = stats.failures.get(failure.kind, 0) + 1
        stats.duration_ms += duration_ms
        self.calls += 1
        self.failures_by_kind[failure.kind] = self.failures_by_kind.get(failure.kind, 0) + 1
        self.failures.append(failure)


def summaries(entries: Iterable[Entry]) -> list[ToolCallSummary]:
    """The summary of every run a journal's entries hold, in the order the runs started. Every branch counts: a call
    on a branch the session left still ran. A run's turns are its replies, as the loop counts them on resume."""
    runs: dict[str, ToolCallSummary] = {}
    for entry in entries:
        if not isinstance(entry, MessageEntry) or entry.run_id is None:
            continue
        summary = runs.setdefault(entry.run_id, ToolCallSummary(run_id=entry.run_id))
        match entry.message:
            case AssistantMessage(invalid_tool_calls=invalid):
                summary.add_turn(summary.turns + 1, invalid, [])
            case ToolResultMessage(result=result):
                summary.add_turn(summary.turns, [], [result])
            case _:
                pass
    return list(runs.values())


class ToolSummary(Extension):
    """Emits a `ToolCallSummary` at the end of every run."""

    name: ClassVar[str] = "tool-summary"

    def __init__(self) -> None:
        self._summary: ToolCallSummary | None = None

    def for_subagent(self) -> None:
        """Left out of subagents: a subagent's work is the parent's call that started it, counted there."""

    async def before_agent_start(self, ctx: RunContext) -> None:
        # The run may already hold results: the calls an earlier run left without one, closed as `interrupted`.
        mine = (e for e in ctx.journal.active_path() if isinstance(e, MessageEntry) and e.run_id == ctx.run_id)
        self._summary = next(iter(summaries(mine)), None) or ToolCallSummary(run_id=ctx.run_id)

    async def turn_end(self, turn: TurnInfo, ctx: RunContext) -> Verdict | None:
        # Here rather than at `tool_result`: a call whose arguments were not JSON has no result, and a later
        # extension's `tool_result` may still change one.
        if self._summary is not None:
            self._summary.add_turn(turn.turn, turn.message.invalid_tool_calls, turn.results)
        return None

    async def agent_end(self, outcome: RunOutcome, ctx: RunContext) -> None:
        if self._summary is not None:
            self._summary.turns = max(self._summary.turns, outcome.turns)
            ctx.emit(ExtensionEvent.of(self.name, "summary", self._summary))

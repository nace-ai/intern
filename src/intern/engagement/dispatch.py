"""The coordinator's ``dispatch`` tool: spawn a specialist sub-session that shares the engagement
state. The extension builds each child Session (Bootstrap with the parent's options, an EventSink
writing the transcript to ``<engagement>/logs/specialists/<role>-<n>.jsonl``); this tool picks the
role prompt, enforces the concurrency limit, tracks live status per specialist, and returns the
specialist's one-line outcome. A specialist's failure is a failed ToolOutput, never an exception.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel

from intern.engagement.roles import ROLE_IDS, get_role, render_role_index, render_role_prompt
from intern.engagement.store import EngagementStore, State
from intern.events import Event, EventSink, ToolCallEnd, ToolCallStart, now_ms
from intern.tools.base import Tool, ToolContext, ToolOutput, fail

#: Never more than this many specialist sub-sessions at once.
MAX_CONCURRENT = 8
#: How much of the specialist's final text the coordinator sees.
OUTCOME_CHARS = 500
#: Ended runs kept for the board before a new batch prunes them.
RETAINED_RUNS = 32

SessionFactory = Callable[[str, EventSink], Awaitable[Any]]

RoleId = Literal[
    "recon",
    "osint",
    "webapp",
    "api",
    "infra",
    "identity",
    "cloud",
    "container",
    "cicd",
    "mobile",
    "postex",
    "exploit",
    "verifier",
    "generic",
]


@dataclass
class AgentTool:
    name: str
    detail: str
    since: int


@dataclass
class AgentRun:
    """Live status for one dispatched specialist (in memory only; the TUI board reads it)."""

    id: str
    role: str
    label: str
    started_at: int
    status: Literal["running", "done", "failed"] = "running"
    ended_at: int | None = None
    error: str | None = None
    tool: AgentTool | None = None
    findings: int = 0
    transcript: Path | None = None


class AgentTracker:
    """Runs across dispatches; every mutation happens under one asyncio lock."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._runs: list[AgentRun] = []
        self._batch = 0
        self._in_batch = 0

    async def register(self, role: str, label: str | None, transcript: Path | None) -> AgentRun:
        async with self.lock:
            self._in_batch += 1
            if self._in_batch == 1:
                self._batch += 1
                ended = [r for r in self._runs if r.status != "running"]
                if len(ended) > RETAINED_RUNS:
                    for run in ended[:-RETAINED_RUNS]:
                        self._runs.remove(run)
            run = AgentRun(
                id=f"{self._batch}.{self._in_batch}",
                role=role,
                label=label or role,
                transcript=transcript,
                started_at=now_ms(),
            )
            self._runs.append(run)
            return run

    async def finish(self, run: AgentRun, error: str | None = None) -> None:
        async with self.lock:
            run.status = "failed" if error else "done"
            run.error = error
            run.ended_at = now_ms()
            run.tool = None
            self._in_batch = max(0, self._in_batch - 1)

    async def running_count(self) -> int:
        async with self.lock:
            return sum(1 for r in self._runs if r.status == "running")

    async def count_for_role(self, role: str) -> int:
        async with self.lock:
            return sum(1 for r in self._runs if r.role == role)

    def find(self, run_id: str) -> AgentRun | None:
        return next((r for r in self._runs if r.id == run_id), None)

    def snapshot(self) -> list[AgentRun]:
        """The board's view: id, role, label, status, current tool, findings count."""
        return list(self._runs)

    def tool_tap(self, run_id: str, name: str, detail: str) -> None:
        run = self.find(run_id)
        if run is not None and run.status == "running":
            run.tool = AgentTool(name=name, detail=detail, since=now_ms())

    def tool_done(self, run_id: str) -> None:
        run = self.find(run_id)
        if run is not None:
            run.tool = None

    def add_findings(self, run_id: str, count: int) -> None:
        run = self.find(run_id)
        if run is not None and count > 0:
            run.findings += count


#: The module-level tracker: one per process, shared by every dispatch tool instance.
TRACKER = AgentTracker()


class DispatchArgs(BaseModel):
    role: RoleId
    task: str
    label: str | None = None


def specialist_transcript_path(store: EngagementStore, role: str, n: int) -> Path:
    """Where the extension's session factory writes this specialist's transcript."""
    return store.logs_dir / "specialists" / f"{role}-{n}.jsonl"


def engagement_brief(store: EngagementStore) -> str:
    state: State = store.state
    scope = f"; scope: {', '.join(state.scope)}" if state.scope else ""
    objective = f"; objective: {state.objective}" if state.objective else ""
    return f"target {state.target}{scope}{objective}"


def _tap_sink(tracker: AgentTracker, run: AgentRun) -> EventSink:
    """A sink over the child session's events: the current tool on the board."""

    def sink(event: Event) -> None:
        if isinstance(event, ToolCallStart):
            detail = event.arguments.get("action") or event.arguments.get("url") or event.arguments.get("command")
            tracker.tool_tap(run.id, event.name, str(detail) if detail is not None else event.name)
        elif isinstance(event, ToolCallEnd):
            tracker.tool_done(run.id)

    return sink


class DispatchTool(Tool):
    """Spawn one specialist sub-session. Sub-sessions never get dispatch themselves (no recursion)."""

    name = "dispatch"
    description = (
        "Spawn a specialist sub-agent that runs with its own role prompt and playbooks while writing to "
        "the shared engagement state. Dispatch several at once to parallelize the engagement — they run "
        f"concurrently, at most {MAX_CONCURRENT} at a time. Roles — {render_role_index()}. Returns the "
        "specialist's summary and its transcript path."
    )
    Args = DispatchArgs
    effects = frozenset({"read", "write", "exec", "network"})

    def __init__(self, store: EngagementStore, session_factory: SessionFactory) -> None:
        self.store = store
        self.session_factory = session_factory

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        assert isinstance(args, DispatchArgs)
        role = get_role(args.role)
        assert role is not None
        if await TRACKER.running_count() >= MAX_CONCURRENT:
            running = ", ".join(r.label for r in TRACKER.snapshot() if r.status == "running")
            return fail(
                f"dispatch refused: {MAX_CONCURRENT} specialists are already running ({running})",
                "Wait for a running specialist to finish, or dispatch fewer at once.",
                retry_safe=False,
            )
        n = await TRACKER.count_for_role(args.role) + 1
        transcript = specialist_transcript_path(self.store, args.role, n)
        run = await TRACKER.register(args.role, args.label, transcript)

        seen = len(self.store.state.vulns)

        def on_state(state: State) -> None:
            nonlocal seen
            grown = len(state.vulns) - seen
            if grown > 0:
                seen = len(state.vulns)
                TRACKER.add_findings(run.id, grown)

        self.store.on_change(on_state)
        try:
            session = await self.session_factory(args.role, _tap_sink(TRACKER, run))
            outcome = await session.run(render_role_prompt(role, args.task, engagement_brief(self.store)))
        except Exception as e:  # a specialist's failure is data, not an exception
            await TRACKER.finish(run, error=str(e))
            return fail(f"specialist {run.label} failed: {e}", "Read the error, adjust the task, dispatch again.")
        await TRACKER.finish(run)
        summary = (outcome.final_text or "").strip() or "(no summary)"
        if len(summary) > OUTCOME_CHARS:
            summary = summary[:OUTCOME_CHARS] + "…"
        return ToolOutput(content=f"{run.label} ({run.id}): done\n{summary}\ntranscript: {transcript}")


assert set(ROLE_IDS) == set(get_args(RoleId)), "RoleId literal drifted from the roles catalog"

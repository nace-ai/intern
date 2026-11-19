"""Session-local TUI timers. The host supplies delivery and its run-completion future.

One task per timer checks wall time in bounded sleeps. A repeat waits for delivery's
run to end (including a queued prompt dropped by Esc) before starting its next interval.
No timer state is journaled, and SessionResources closes every task on reopen/close.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from intern.hooks import Extension
from intern.session import COMPACTING, SessionBusy
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

_sleep = asyncio.sleep

Fire = Callable[[str], Awaitable[None]]
"""Accept a prompt synchronously; the awaitable ends with its run, or when Esc drops it.
SessionBusy(COMPACTING) means retry; any other SessionBusy means the host is closing.
"""


class ScheduleArgs(BaseModel):
    prompt: str = Field(min_length=1, description="The task to run when this timer is due.")
    once_after_s: int | None = Field(default=None, ge=60, description="Run once after this many seconds (at least 60).")
    every_s: int | None = Field(
        default=None, ge=60, description="Repeat this many seconds after each run ends (at least 60)."
    )

    @model_validator(mode="after")
    def timing(self) -> ScheduleArgs:
        if not self.prompt.strip():
            raise ValueError("prompt must not be blank")
        if (self.once_after_s is None) == (self.every_s is None):
            raise ValueError("give exactly one of once_after_s or every_s")
        return self


class ListSchedulesArgs(BaseModel):
    pass


class CancelScheduleArgs(BaseModel):
    id: str


class Scheduled(BaseModel):
    id: str
    kind: Literal["once", "repeat"]
    next_run_at: str


class ScheduleInfo(Scheduled):
    prompt: str
    every_s: int | None = None


class ScheduleList(BaseModel):
    timers: list[ScheduleInfo]


class Cancelled(BaseModel):
    ok: bool = True


@dataclass
class _Timer:
    id: str
    prompt: str
    every_s: int | None
    due: float
    task: asyncio.Task[None] | None = None

    def info(self) -> ScheduleInfo:
        return ScheduleInfo(
            id=self.id,
            prompt=self.prompt,
            kind="repeat" if self.every_s is not None else "once",
            every_s=self.every_s,
            next_run_at=datetime.fromtimestamp(self.due).astimezone().isoformat(),
        )


class Schedule(Extension):
    name: ClassVar[str] = "schedule"

    def __init__(self, *, max_timers: int = 5) -> None:
        self.max_timers = max_timers
        self.fire: Fire | None = None
        self._timers: dict[str, _Timer] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._serial = 0
        self._closed = False

    def tools(self) -> list[Tool]:
        return [ScheduleTool(self), ListSchedulesTool(self), CancelScheduleTool(self)]

    def listing(self) -> ScheduleList:
        return ScheduleList(timers=[timer.info() for timer in self._timers.values()])

    def add(self, args: ScheduleArgs) -> ToolOutput:
        if self._closed or self.fire is None:
            return fail("timer host is closed or unavailable", "Open a terminal UI session with /caps schedule.")
        if len(self._timers) >= self.max_timers:
            return fail("max_timers reached", "Cancel an existing timer with cancel_schedule before adding another.")
        self._serial += 1
        delay = args.every_s if args.every_s is not None else args.once_after_s
        assert delay is not None
        timer = _Timer(f"t{self._serial}", args.prompt, args.every_s, time.time() + delay)
        self._timers[timer.id] = timer
        timer.task = asyncio.create_task(self._run(timer), name=f"schedule-{timer.id}")
        self._tasks.add(timer.task)
        timer.task.add_done_callback(self._tasks.discard)
        return ToolOutput(content=Scheduled.model_validate(timer.info().model_dump()).model_dump_json())

    def cancel(self, id: str) -> ToolOutput:
        timer = self._timers.pop(id, None)
        if timer is None:
            return fail(f"unknown timer id: {id}", "Use list_schedules to find a live timer id.", argument="id")
        if timer.task is not None:
            timer.task.cancel()
        return ToolOutput(content=Cancelled().model_dump_json())

    async def _run(self, timer: _Timer) -> None:
        try:
            while not self._closed:
                while (remaining := timer.due - time.time()) > 0:
                    await _sleep(min(remaining, 30))
                assert self.fire is not None
                try:
                    ended = self.fire(f"A timer you set is due ({timer.id}): {timer.prompt}")
                except SessionBusy as error:
                    if str(error) != COMPACTING:
                        return  # closing: nothing is delivered
                    await _sleep(1)
                    continue
                if timer.every_s is None:
                    self._timers.pop(timer.id, None)
                await ended
                if timer.every_s is None:
                    return
                timer.due = time.time() + timer.every_s
        finally:
            self._timers.pop(timer.id, None)

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._timers.clear()


class ScheduleTool(Tool):
    name = "schedule"
    description = (
        "Run a prompt later while this terminal UI session stays open. Give exactly one of once_after_s or every_s, "
        "at least 60 seconds. Repeats wait for their run to end before counting the next interval; missed intervals "
        "do not accumulate. All timers stop on close or reopen."
    )
    Args = ScheduleArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule

    async def run(self, args: ScheduleArgs, ctx: ToolContext) -> ToolOutput:
        return self.schedule.add(args)


class ListSchedulesTool(Tool):
    name = "list_schedules"
    description = (
        "List this session's live timers, their prompts, once/repeat kind, repeat interval and local due time. "
        "A repeat's due time stays at its last firing while queued or running; its next time is set when the run ends."
    )
    Args = ListSchedulesArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})

    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule

    async def run(self, args: ListSchedulesArgs, ctx: ToolContext) -> ToolOutput:
        return ToolOutput(content=self.schedule.listing().model_dump_json(exclude_none=True))


class CancelScheduleTool(Tool):
    name = "cancel_schedule"
    description = "Stop a timer by its id from list_schedules. Stops future firings; a prompt already sent stays sent."
    Args = CancelScheduleArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"write"})

    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule

    async def run(self, args: CancelScheduleArgs, ctx: ToolContext) -> ToolOutput:
        return self.schedule.cancel(args.id)

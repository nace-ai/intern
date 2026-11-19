"""Timers through real session runs, with wall time and timer sleeps controlled by the test."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from intern.capability_settings import CapabilitySettings, ScheduleConfig
from intern.capability_toggles import applied, flip, toggles, values
from intern.config import Bootstrap, Options
from intern.extensions import schedule as scheduling
from intern.extensions.schedule import Schedule, ScheduleArgs
from intern.hooks import Hooks
from intern.journal import MessageEntry
from intern.messages import UserMessage
from intern.providers.fake import FakeProvider, Script, Turn
from intern.session import Session
from intern.session_resources import SessionResources
from intern.session_setup import capability_extensions
from intern.subagents import SubagentRefused, child_tools
from intern.tools.base import ToolContext, ToolOutput
from intern.tui.schedule import TimerDelivery

from .support import Harness, Quiet, binding


async def settle() -> None:
    for _ in range(80):
        await asyncio.sleep(0)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + delay, future))
        try:
            await future
        finally:
            self.waiters = [(at, f) for at, f in self.waiters if f is not future]

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        for at, future in self.waiters:
            if at <= self.now and not future.done():
                future.set_result(None)
        await settle()


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(scheduling, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(scheduling, "_sleep", clock.sleep)
    return clock


class PausedProvider(FakeProvider):
    def __init__(self, script: Script | None = None) -> None:
        super().__init__(script if script is not None else [Turn(text="done")])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request):
        self.entered.set()
        await self.release.wait()
        async for item in super().stream(request):
            yield item


async def timer_session(harness: Harness, *, max_timers: int = 5) -> tuple[Session, Schedule]:
    schedule = Schedule(max_timers=max_timers)
    delivery = TimerDelivery()

    def emit(event):
        delivery.observe(event)
        harness.recorder(event)

    harness.deps.emit = emit
    harness.deps.hooks = Hooks([schedule], emit)
    harness.deps.tools.extend(schedule.tools())
    resources = SessionResources()

    async def setup(stack: AsyncExitStack) -> None:
        stack.push_async_callback(schedule.aclose)

    await resources.open(setup)
    session = Session(harness.deps, resources=resources)
    delivery.session = session
    schedule.fire = delivery.fire
    return session, schedule


def prompts(session: Session) -> list[UserMessage]:
    return [e.message for e in session.entries() if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)]


def due(schedule: Schedule) -> float:
    return datetime.fromisoformat(schedule.listing().timers[0].next_run_at).timestamp()


async def test_once_wakes_idle_session_without_a_user_message_and_is_removed(make_harness, clock):
    harness = make_harness([Turn(text="checked")])
    session, schedule = await timer_session(harness)
    async with session:
        assert schedule.add(ScheduleArgs(prompt="check the build", once_after_s=60)).status == "ok"
        await settle()
        await clock.advance(3600)  # computer slept: fire once, not hours late
        await session.wait()
        assert len(harness.provider.requests) == 1
        [notice] = prompts(session)
        assert notice.source == "harness" and "check the build" in notice.content
        assert schedule.listing().timers == []
        assert max(clock.delays) <= 30


async def test_repeat_waits_for_its_run_end_and_collapses_missed_firings(make_harness, clock):
    harness = make_harness([])
    provider = PausedProvider()
    harness.deps.system2.provider = provider
    session, schedule = await timer_session(harness)
    async with session:
        schedule.add(ScheduleArgs(prompt="check again", every_s=60))
        await settle()
        await clock.advance(60)
        await provider.entered.wait()
        assert len(prompts(session)) == 1
        await clock.advance(600)
        assert len(prompts(session)) == 1
        assert not session._follow_ups
        provider.release.set()
        await session.wait()
        await settle()
        assert due(schedule) == clock.now + 60
        await clock.advance(59)
        assert len(prompts(session)) == 1
        await clock.advance(1)
        await session.wait()
        assert len(prompts(session)) == 2


@pytest.mark.parametrize("abort", [False, True])
async def test_timer_queued_behind_work_collapses_and_rearms_even_if_esc_drops_it(make_harness, clock, abort):
    harness = make_harness([])
    provider = PausedProvider()
    harness.deps.system2.provider = provider
    session, schedule = await timer_session(harness)
    async with session:
        task = session.start("user work")
        await provider.entered.wait()
        schedule.add(ScheduleArgs(prompt="check later", every_s=60))
        await settle()
        await clock.advance(60)
        assert len(session._follow_ups) == 1
        assert session._follow_ups[0].source == "harness"
        await clock.advance(600)
        assert len(session._follow_ups) == 1
        if abort:
            session.abort()
        else:
            provider.release.set()
        await task
        await settle()
        assert len(prompts(session)) == (1 if abort else 2)
        assert due(schedule) == clock.now + 60
        provider.release.set()
        await clock.advance(60)
        await session.wait()
        assert len(prompts(session)) == (2 if abort else 3)


async def test_tools_validate_arguments_cap_cancel_and_list(make_harness, clock):
    harness = make_harness([])
    session, schedule = await timer_session(harness, max_timers=1)
    ctx = ToolContext(cwd=harness.cwd, executor=harness.deps.executor, abort=asyncio.Event(), max_output_chars=10000)
    create, listing, cancel = schedule.tools()
    async with session:
        for args in [
            {"prompt": "check"},
            {"prompt": "check", "once_after_s": 60, "every_s": 60},
            {"prompt": "check", "once_after_s": 59},
            {"prompt": "check", "every_s": 59},
            {"prompt": "", "once_after_s": 60},
            {"prompt": "  ", "once_after_s": 60},
        ]:
            error = create.parse(args)
            assert isinstance(error, ToolOutput) and error.status == "error"
        first = await create.run(ScheduleArgs(prompt="first", once_after_s=60), ctx)
        assert first.status == "ok"
        error = await create.run(ScheduleArgs(prompt="second", every_s=60), ctx)
        assert error.error is not None and "max_timers" in error.error.what_failed
        live = await listing.run(listing.Args(), ctx)
        assert '"t1"' in live.content and '"first"' in live.content
        assert (await cancel.run(cancel.Args(id="unknown"), ctx)).status == "error"
        assert (await cancel.run(cancel.Args(id="t1"), ctx)).status == "ok"
        await settle()
        await clock.advance(600)
        assert not harness.provider.requests
        assert not schedule.listing().timers


async def test_due_during_real_compaction_is_retried_after_it_ends(make_harness, clock):
    from .test_session import six_outputs

    harness = make_harness(six_outputs(), window=12000)
    session, schedule = await timer_session(harness)
    async with session:
        await session.run("print six outputs")
        provider = PausedProvider(six_outputs())
        harness.deps.system2.provider = provider
        compacting = asyncio.create_task(session.compact())
        await provider.entered.wait()
        assert session._compacting
        before = len(prompts(session))
        schedule.add(ScheduleArgs(prompt="check afterwards", once_after_s=60))
        await settle()
        await clock.advance(60)
        assert len(prompts(session)) == before
        assert len(schedule.listing().timers) == 1
        provider.release.set()
        assert await compacting == "compacted"
        harness.deps.system2.provider = FakeProvider([Turn(text="done")])
        await clock.advance(1)
        await session.wait()
        assert len(prompts(session)) == before + 1
        assert prompts(session)[-1].source == "harness"
        assert not schedule.listing().timers


async def test_session_close_cancels_every_timer_and_never_fires_afterwards(make_harness, clock):
    harness = make_harness([])
    session, schedule = await timer_session(harness)
    schedule.add(ScheduleArgs(prompt="once", once_after_s=60))
    schedule.add(ScheduleArgs(prompt="repeat", every_s=60))
    await settle()
    await session.aclose()
    await clock.advance(3600)
    assert session.closed
    assert not schedule.listing().timers and not schedule._tasks
    assert not harness.provider.requests


@pytest.mark.parametrize("max_timers", [0, 51])
def test_saved_schedule_limits_are_checked(max_timers):
    with pytest.raises(ValidationError):
        ScheduleConfig(max_timers=max_timers)


def test_schedule_switch_is_tui_only_and_tools_are_left_out_of_subagents(tmp_path):
    from intern.protocol.acp import AcpAgent

    caps = CapabilitySettings()
    assert not caps.schedule.enabled and caps.schedule.max_timers == 5
    on = flip(caps, caps, "cap.schedule", True, ["base"])
    assert on.schedule.enabled

    async def unopened(request):
        raise AssertionError("listing config options must not open a session")

    acp = AcpAgent(unopened, tmp_path, capabilities=on)
    assert "cap.schedule" not in {option.id for option in acp._capability_options(on, ["base"])}
    assert "cap.schedule" not in values(on, on, ["base"])
    assert "cap.schedule" in {row.id for row in toggles(on, on, ["base"], base=True)}
    with pytest.raises(KeyError):
        applied(on, on, {"cap.schedule": True}, ["base"])
    assert not any(isinstance(e, Schedule) for e in capability_extensions(on))
    [extension] = [e for e in capability_extensions(on, tui=True) if isinstance(e, Schedule)]
    assert extension.for_subagent() is None
    assert child_tools(extension.tools(), None) == []
    for tool in extension.tools():
        with pytest.raises(SubagentRefused):
            child_tools(extension.tools(), [tool.name])


@pytest.mark.parametrize("tui", [False, True])
async def test_bootstrap_builds_schedule_only_for_tui_and_owns_its_close(tmp_path, clock, tui, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[capabilities.schedule]\nenabled = true\nmax_timers = 2\n")

    async def bind(self, ref):
        return binding(FakeProvider([]))

    monkeypatch.setattr(Bootstrap, "bind", bind)
    monkeypatch.setattr(Bootstrap, "_system1", lambda self: Quiet())
    bootstrap = Bootstrap(
        Options(cwd=tmp_path, system2="vllm/fake", memory_off=True, extensions=False, persist=False, tui=tui),
        {"INTERN_HOME": str(home)},
    )
    session = await bootstrap.session(lambda e: None)
    async with session:
        extensions = [e for e in session.deps.hooks.extensions if isinstance(e, Schedule)]
        names = {t.name for t in session.deps.tools}
        assert bool(extensions) == tui
        assert ({"schedule", "list_schedules", "cancel_schedule"} <= names) == tui
        if tui:
            [extension] = extensions
            assert extension.max_timers == 2
            extension.fire = lambda text: asyncio.get_running_loop().create_future()
            extension.add(ScheduleArgs(prompt="do it", once_after_s=60))
    if tui:
        assert not extensions[0].listing().timers
        await clock.advance(60)
        assert not extensions[0]._tasks

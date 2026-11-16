"""A command that writes nothing for `idle_stop_s` is stopped, and says so; one that writes, or whose silence is
expected, runs on."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from intern import processes
from intern.events import ProcessEvent
from intern.executor import LocalExecutor
from intern.processes import ProcessManager
from intern.secrets import EnvironmentBinding, EnvironmentSecrets, SecretExecutor, SecretRef
from intern.tools.base import ToolContext, ToolOutput
from intern.tools.shell import BashArgs, BashTool

TICKS = "for i in $(seq 12); do echo tick; sleep 0.2; done"  # writes every 0.2 s for 2.4 s


@pytest.fixture(autouse=True)
def quick_watch(monkeypatch):
    monkeypatch.setattr(processes, "IDLE_POLL_S", 0.1)


def ended(events: list[ProcessEvent]) -> list[ProcessEvent]:
    return [e for e in events if e.action == "ended"]


async def bash(manager: ProcessManager, tmp_path: Path, command: str, executor=None, call_id="c1") -> ToolOutput:
    ctx = ToolContext(
        cwd=tmp_path,
        executor=executor or LocalExecutor(),
        abort=asyncio.Event(),
        max_output_chars=10_000,
        processes=manager,
        call_id=call_id,
    )
    return await BashTool().run(BashArgs(command=command, timeout_s=30), ctx)


async def test_a_silent_bash_command_is_stopped_and_one_that_writes_runs_to_its_end(tmp_path: Path):
    events: list[ProcessEvent] = []
    async with ProcessManager(idle_stop_s=0.8) as manager:
        manager.listen(events.append)
        silent = await bash(manager, tmp_path, "echo start; sleep 30", call_id="silent")
        writes = await bash(manager, tmp_path, TICKS, call_id="writes")
    assert silent.status == "error" and "wrote nothing" in silent.content and "start" in silent.content
    assert writes.status == "ok"
    states = {e.process.id: e.process for e in ended(events)}
    assert states["silent"].state == "idle_stopped" and states["silent"].quiet_s is not None
    assert states["writes"].state == "exited"


async def test_output_hidden_by_secret_redaction_still_counts(tmp_path: Path):
    # With secrets, the published capture is written only once the command ends: the raw stream must be what counts.
    secrets = EnvironmentSecrets(
        [EnvironmentBinding(reference=SecretRef(name="k"), source_env="K", scopes=frozenset({"s"}))], {"K": "v"}
    )
    executor = SecretExecutor(LocalExecutor(), secrets, scope="s", inject={"API_KEY": SecretRef(name="k")})
    async with ProcessManager(idle_stop_s=0.8) as manager:
        assert (await bash(manager, tmp_path, TICKS, executor=executor)).status == "ok"


async def test_a_silent_job_is_stopped_unless_jobs_are_left_alone(tmp_path: Path):
    events: list[ProcessEvent] = []
    async with ProcessManager(idle_stop_s=0.5) as manager:
        manager.listen(events.append)
        job = await manager.start("sleep 30", cwd=tmp_path)
        busy = await manager.start(TICKS, cwd=tmp_path)
        assert (await manager.poll(busy.handle, wait_s=5)).state == "exited"
        (stopped,) = [e.process for e in ended(events) if e.process.id == job.handle]
        assert stopped.state == "idle_stopped" and stopped.quiet_s is not None
        assert "wrote nothing" in (manager.end_notice([stopped]) or "")
    async with ProcessManager(idle_stop_s=0.5, idle_stop_jobs=False) as manager:
        job = await manager.start("sleep 1.5", cwd=tmp_path)
        assert (await manager.poll(job.handle, wait_s=5)).state == "exited"


async def test_a_call_that_moved_to_the_background_is_still_stopped_for_silence(tmp_path: Path):
    async with ProcessManager(idle_stop_s=1.0, move_after_s=0.3) as manager:
        moved = await bash(manager, tmp_path, "echo begin; sleep 30")
        assert "Running in the background as job" in moved.content
        (job,) = manager.jobs_running()
        assert (await manager.poll(job.id, wait_s=5)).state == "idle_stopped"


async def test_a_job_that_serves_is_left_alone(tmp_path: Path):
    async with ProcessManager(idle_stop_s=0.5) as manager:
        server = await manager.start("sleep 1.5", cwd=tmp_path)
        manager.release(server.handle)  # System 1 found it up and ready
        assert (await manager.poll(server.handle, wait_s=5)).state == "exited"

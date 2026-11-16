"""A command held through the session's hold socket is not stopped for silence while the hold is open; its silence
counts again from the hold's end."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from intern import processes
from intern.events import ProcessEvent
from intern.executor import LocalExecutor
from intern.holds import PROCESS_ENV, SOCKET_ENV, HoldServer
from intern.journal import EventEntry
from intern.processes import ProcessManager
from intern.secrets import EnvironmentBinding, EnvironmentSecrets, SecretExecutor, SecretRef
from intern.tools.base import ToolContext
from intern.tools.process import ProcessArgs, ProcessTool
from intern.tools.shell import BashArgs, BashTool

from .fake_openai import Reply
from .test_session_setup import configured

HOLD = f"{shlex.quote(sys.executable)} -m intern hold --reason platform --"


@pytest.fixture(autouse=True)
def quick_watch(monkeypatch):
    monkeypatch.setattr(processes, "IDLE_POLL_S", 0.1)


def context(tmp_path: Path, manager: ProcessManager, executor=None) -> ToolContext:
    return ToolContext(
        cwd=tmp_path,
        executor=executor or LocalExecutor(),
        abort=asyncio.Event(),
        max_output_chars=10_000,
        processes=manager,
        call_id="c1",
    )


async def test_a_held_bash_command_outlives_its_silence_until_the_hold_ends(tmp_path: Path):
    async with ProcessManager(idle_stop_s=0.8) as manager, HoldServer(manager):
        started = time.monotonic()
        result = await BashTool().run(BashArgs(command=f"{HOLD} sleep 2; sleep 30"), context(tmp_path, manager))
        took = time.monotonic() - started
    # Held through the silent `sleep 2`, then stopped 0.8 s into the silence after it, not 0.8 s into the command.
    assert result.status == "error" and "wrote nothing" in result.content
    assert 2.6 <= took < 10


async def test_a_job_with_command_secrets_can_hold_itself(tmp_path: Path):
    # The secret wrapper runs a job in an exact environment; the hold socket and the job's id must reach it.
    secrets = EnvironmentSecrets(
        [EnvironmentBinding(reference=SecretRef(name="k"), source_env="K", scopes=frozenset({"s"}))], {"K": "v"}
    )
    executor = SecretExecutor(
        LocalExecutor(),
        secrets,
        scope="s",
        inject={"API_KEY": SecretRef(name="k")},
        base_env={"PATH": os.environ["PATH"]},
    )
    async with ProcessManager(idle_stop_s=0.8) as manager, HoldServer(manager):
        tool = ProcessTool(manager)
        started = await tool.run(
            ProcessArgs(action="start", command=f"{HOLD} sleep 2"), context(tmp_path, manager, executor)
        )
        (job,) = manager.jobs_running()
        assert (await manager.poll(job.id, wait_s=10)).state == "exited", started.content


async def test_a_session_with_idle_stop_lets_its_commands_hold_themselves(tmp_path: Path):
    replies = [Reply(tool_calls=[("bash", {"command": f"{HOLD} sleep 2; echo held"})]), Reply(text="done")]
    async with configured(tmp_path, replies, "[capabilities.process]\nidle_stop_s = 0.8\n") as (session, _, _, _):
        await session.run("run it")
        events = [e.event for e in session.entries() if isinstance(e, EventEntry) and isinstance(e.event, ProcessEvent)]
        assert [e.process.state for e in events if e.action == "ended"] == ["exited"]


def test_outside_a_session_hold_just_runs_the_command(tmp_path: Path):
    env = {k: v for k, v in os.environ.items() if k not in (SOCKET_ENV, PROCESS_ENV)}
    done = subprocess.run(
        [sys.executable, "-m", "intern", "hold", "--", "sh", "-c", "echo ran; exit 3"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 3 and done.stdout == "ran\n" and done.stderr == ""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path, PurePosixPath

import pytest

from intern.executor import ExecResult, LocalExecutor, WorkspaceMount
from intern.runners.docker import DockerExecutor, DockerOptions
from intern.tools.base import ToolContext, ToolOutput
from intern.workspace_tools import workspace_tools


class Launcher:
    output_capture = True

    def __init__(self):
        self.commands: list[str] = []
        self.captures: list[Path | None] = []

    async def run(self, command: str, **kwargs) -> ExecResult:
        self.commands.append(command)
        self.captures.append(kwargs.get("capture_path"))
        return ExecResult(exit_code=0, output="ok", omitted_bytes=0, timed_out=False, aborted=False, duration_ms=1)


async def test_container_argv_quotes_inputs_and_always_removes_owned_container(tmp_path: Path):
    launcher = Launcher()
    executor = DockerExecutor(tmp_path, launcher=launcher)
    await executor.run(
        "echo 'x; false'",
        cwd=tmp_path,
        timeout_s=1,
        max_output_bytes=100,
        env={"TOKEN": "secret-env-value"},
        capture_path=tmp_path / "capture.txt",
    )
    argv = shlex.split(launcher.commands[0])
    assert argv[-1] == "echo 'x; false'" and "secret-env-value" not in launcher.commands[0]
    assert argv[argv.index("--network") + 1] == "none" and "--read-only" in argv
    name = argv[argv.index("--name") + 1]
    assert shlex.split(launcher.commands[1]) == ["docker", "rm", "-f", name]
    assert launcher.captures == [tmp_path / "capture.txt", None]
    with pytest.raises(ValueError):
        await executor.run("true", cwd=tmp_path.parent, timeout_s=1, max_output_bytes=100)


@pytest.mark.skipif(not os.environ.get("INTERN_DOCKER_IMAGE"), reason="explicit prepared local Docker image required")
async def test_real_docker_shell_and_every_filesystem_tool_share_workspace(tmp_path: Path):
    executor = DockerExecutor(tmp_path, DockerOptions(image=os.environ["INTERN_DOCKER_IMAGE"]))
    ctx = ToolContext(cwd=tmp_path, executor=executor, abort=asyncio.Event(), max_output_chars=5000)
    tools = {t.name: t for t in workspace_tools()}

    async def call(name, **arguments):
        tool = tools[name]
        args = tool.parse(arguments)
        assert not isinstance(args, ToolOutput)
        result = await tool.run(args, ctx)
        assert result.status == "ok", result.content
        return result.content

    await call("write", path="sub/a.txt", content="hello\nworld\n")
    assert "world" in await call("read", path="sub/a.txt")
    await call("edit", path="sub/a.txt", old_string="world", new_string="changed")
    assert "sub/a.txt:2:changed" in await call("grep", pattern="changed")
    assert "sub/a.txt" in await call("find", pattern="*.txt")
    assert "changed" in await call("bash", command="cat sub/a.txt")
    denied = tools["read"].parse({"path": str(tmp_path.parent / "host-only")})
    assert not isinstance(denied, ToolOutput)
    assert (await tools["read"].run(denied, ctx)).status == "error"
    assert (tmp_path / "sub/a.txt").read_text() == "hello\nchanged\n"


class MountedLocal(LocalExecutor):
    """The local executor with a mount at ``root``: the sandboxed file tools' worker runs as it would in a container."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.mount = WorkspaceMount(root, PurePosixPath(str(root)))

    async def run(self, command: str, **kwargs) -> ExecResult:
        argv = shlex.split(command)
        return await super().run(shlex.join([sys.executable, *argv[1:]]), **kwargs)


async def test_sandboxed_file_tools_reach_the_rest_of_the_mount_from_a_subfolder_and_nothing_outside(tmp_path: Path):
    root = tmp_path / "work"
    (root / "sub").mkdir(parents=True)
    (root / "sibling.txt").write_text("before\n")
    ctx = ToolContext(cwd=root / "sub", executor=MountedLocal(root), abort=asyncio.Event(), max_output_chars=5000)
    tools = {t.name: t for t in workspace_tools()}

    async def call(name: str, **arguments) -> ToolOutput:
        args = tools[name].parse(arguments)
        assert not isinstance(args, ToolOutput)
        return await tools[name].run(args, ctx)

    written = await call("write", path="../sibling.txt", content="after\n")
    assert written.status == "ok" and written.content.endswith("work/sibling.txt")
    edited = await call("edit", path="../sibling.txt", old_string="after", new_string="twice")
    assert edited.status == "ok" and (root / "sibling.txt").read_text() == "twice\n"
    found = await call("grep", path="..", pattern="twice")
    assert found.content.endswith("work/sibling.txt:1:twice")
    outside = await call("write", path="../../outside.txt", content="no\n")
    assert outside.status == "error" and not (root.parent / "outside.txt").exists()

"""Search tools: grep and find. They use ripgrep when it is installed and fall back to Python."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from intern import names
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", names.PROJECT_DIR}
    | {".decision_workspace", ".drex_workspace"}  # the workspace index (and its earlier name): a copy of every text
)
MAX_LINE_CHARS = 300
SEARCH_TIMEOUT_S = 60
BINARY_SNIFF_BYTES = 8192
# The longest line of ripgrep's output that is read: a match is cut to MAX_LINE_CHARS, so only its path can be long.
MAX_OUTPUT_LINE_BYTES = 1 << 20


def _ripgrep() -> str | None:
    return shutil.which("rg")


async def _run(argv: list[str], cwd: Path, max_lines: int | None = None) -> tuple[int, str]:
    """Run ``argv`` and give its exit code and output. With ``max_lines``, the command is stopped once it has printed
    that many lines, which is then a success: the rest of its output is never read or held."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=MAX_OUTPUT_LINE_BYTES,
    )
    stopped = False

    async def head(stream: asyncio.StreamReader, count: int) -> bytes:
        nonlocal stopped
        lines: list[bytes] = []
        while len(lines) < count and (line := await stream.readline()):
            lines.append(line)
        if len(lines) == count and process.returncode is None:
            stopped = True
            process.kill()
        return b"".join(lines)

    try:
        if max_lines is None:
            stdout, stderr = await asyncio.wait_for(process.communicate(), SEARCH_TIMEOUT_S)
        else:
            assert process.stdout is not None and process.stderr is not None
            # Both pipes are read at once, so a command that fills one never waits on it while the other is read.
            reading = asyncio.gather(head(process.stdout, max_lines), process.stderr.read())
            stdout, stderr = await asyncio.wait_for(reading, SEARCH_TIMEOUT_S)
            await process.wait()
    except TimeoutError:
        return -1, "search timed out"
    except ValueError:
        return -1, "the search printed a line too long to read"
    finally:
        # Also reached on cancellation, when the run is aborted: never leave ripgrep walking behind a dead call.
        if process.returncode is None:
            process.kill()
            await process.wait()
    code = process.returncode if process.returncode is not None else -1
    if stopped:
        code = 0
    return code, (stdout if code in (0, 1) else stderr).decode(errors="replace")


def _walk(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(folder) / name


def _grep_files(
    root: Path, cwd: Path, glob: str | None, regex: re.Pattern[str], max_lines: int, abort: asyncio.Event
) -> list[str]:
    """The first ``max_lines`` matching lines under ``root``, read a line at a time. Only regular files are opened
    (a pipe would never end), and a binary file, or one that is not UTF-8, is passed over."""
    matches: list[str] = []
    for file in _walk(root):
        if abort.is_set():
            break
        if (glob and not fnmatch.fnmatch(file.name, glob)) or not file.is_file():
            continue
        found: list[str] = []
        try:
            with file.open("rb") as lines:
                if b"\x00" in lines.read(BINARY_SNIFF_BYTES):
                    continue
                lines.seek(0)
                for number, raw in enumerate(lines, 1):
                    line = raw.decode().rstrip("\r\n")
                    if regex.search(line):
                        found.append(f"{_relative(file, cwd)}:{number}:{line[:MAX_LINE_CHARS]}")
                        if len(matches) + len(found) >= max_lines:
                            return matches + found
        except (UnicodeDecodeError, OSError):
            continue
        matches += found
    return matches


def _relative(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regular expression to search for.")
    path: str = Field(default=".", description="File or directory to search.")
    glob: str | None = Field(default=None, description="Only search files matching this glob, e.g. '*.py'.")
    ignore_case: bool = False
    max_matches: int = Field(default=100, ge=1, le=1000)


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with a regular expression. Returns path:line:text for each match."
    Args = GrepArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})
    cancel_on_abort = True

    async def run(self, args: GrepArgs, ctx: ToolContext) -> ToolOutput:
        root = ctx.resolve(args.path)
        if not root.exists():
            return fail(f"{args.path} does not exist", "Search an existing directory, e.g. '.'.", argument="path")
        if not (root.is_dir() or root.is_file()):
            return fail(
                f"{args.path} is not a regular file or a directory (it is a pipe, device or socket)",
                "Search a regular file or a directory.",
                argument="path",
            )
        try:
            regex = re.compile(args.pattern, re.IGNORECASE if args.ignore_case else 0)
        except re.error as e:
            return fail(f"invalid regular expression: {e}", "Fix or escape the pattern.", argument="pattern")
        matches = await self.matches(args, ctx, root, regex)
        if isinstance(matches, ToolOutput):
            return matches
        if not matches:
            return ToolOutput(content=f"No matches for {args.pattern!r} in {args.path}.")
        shown = matches[: args.max_matches]
        footer = (
            f"\n[The first {len(shown)} matches are shown and there are more; narrow the pattern, path or glob.]"
            if len(matches) > len(shown)
            else ""
        )
        return ToolOutput(content="\n".join(shown) + footer)

    async def matches(
        self, args: GrepArgs, ctx: ToolContext, root: Path, regex: re.Pattern[str]
    ) -> list[str] | ToolOutput:
        """The matching lines under ``root``, as `path:line:text` (an extension adds what it can read): no more than
        one past ``args.max_matches``, which is enough to say that there are more."""
        wanted = args.max_matches + 1
        rg = _ripgrep()
        if rg:
            argv = [rg, "--line-number", "--no-heading", "--color", "never"]
            argv += ["--max-columns", str(MAX_LINE_CHARS), "--max-columns-preview"]
            argv += ["-i"] if args.ignore_case else []
            argv += ["-g", args.glob] if args.glob else []
            # Always pass a path: without one ripgrep reads stdin when stdin is not a terminal.
            argv += ["-e", args.pattern, "--", _relative(root, ctx.cwd)]
            code, output = await _run(argv, ctx.cwd, max_lines=wanted)
            if code not in (0, 1):
                return fail(f"grep failed: {output.strip()[:300]}", "Check the pattern and path.")
            return [line.removeprefix("./") for line in output.splitlines()]
        # In a thread: reading files here would hold every session until the search ended.
        return await asyncio.to_thread(_grep_files, root, ctx.cwd, args.glob, regex, wanted, ctx.abort)


class FindArgs(BaseModel):
    pattern: str = Field(description="Glob for file paths, e.g. '*.py' or 'src/**/test_*.py'.")
    path: str = Field(default=".", description="Directory to search.")
    max_results: int = Field(default=200, ge=1, le=2000)


class FindTool(Tool):
    name = "find"
    description = "Find files by name with a glob pattern. Returns matching paths, sorted."
    Args = FindArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})
    cancel_on_abort = True

    async def run(self, args: FindArgs, ctx: ToolContext) -> ToolOutput:
        root = ctx.resolve(args.path)
        if not root.is_dir():
            return fail(f"{args.path} is not a directory", "Pass a directory as path, e.g. '.'.", argument="path")
        rg = _ripgrep()
        if rg:
            code, output = await _run([rg, "--files", "--color", "never", "-g", args.pattern, "--", "."], root)
            if code not in (0, 1):
                return fail(f"find failed: {output.strip()[:300]}", "Check the glob pattern.", argument="pattern")
            found = sorted(_relative(root / line, ctx.cwd) for line in output.splitlines())
        else:
            name_only = "/" not in args.pattern
            found = sorted(
                _relative(file, ctx.cwd)
                for file in _walk(root)
                if fnmatch.fnmatch(file.name if name_only else _relative(file, root), args.pattern)
            )
        if not found:
            return ToolOutput(content=f"No files match {args.pattern!r} in {args.path}.")
        shown = found[: args.max_results]
        footer = f"\n[{len(shown)} of {len(found)} files shown; narrow the pattern.]" if len(found) > len(shown) else ""
        return ToolOutput(content="\n".join(shown) + footer)

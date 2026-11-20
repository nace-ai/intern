"""Typed tools that declare their effects and fail as data, never as exceptions."""

from __future__ import annotations

import asyncio
import difflib
import errno
import hashlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import AliasChoices, BaseModel, Field, RootModel, ValidationError

from intern.executor import Executor
from intern.messages import FileDiff, ImagePart, JsonObject, ResourceRef, ToolError, ToolErrorKind
from intern.providers.base import ToolSpec

if TYPE_CHECKING:
    from intern.hooks import RunContext
    from intern.processes import ProcessManager
    from intern.subagents import Subagents

Effect = Literal["read", "write", "exec", "network"]
# A diff carries a file twice; past this many characters before and after together, a change has none.
MAX_DIFF_CHARS = 256_000


@dataclass
class ToolContext:
    cwd: Path
    executor: Executor
    abort: asyncio.Event
    max_output_chars: int
    """Budget for the content the model will see."""
    image_dir: Path | None = None
    """Set when the model can see images: where read stores the resized images it returns."""
    output_dir: Path | None = None
    """Where bash keeps a command's full output when it is too long to show: the session's spill folder, so the
    working directory holds only the task's files. None keeps it in the project folder, where tools that can only reach
    the workspace (a runner's) still read it."""
    processes: ProcessManager | None = None
    """The session's job manager: where the `process` tool's jobs run and every running command is recorded."""
    call_id: str | None = None
    """The id of the call being run, as the model sent it."""
    subagents: Subagents | None = None
    """Starts the run's subagents: what the tool that hands out tasks uses. None when the session has none."""
    run: RunContext | None = None
    """The run, for a tool that asks System 1: through `run.decisions`, with `run.state()` as the state, as every
    System 1 question reads the session. None outside a run."""

    def resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else (self.cwd / candidate)


class ToolOutput(BaseModel):
    status: Literal["ok", "error", "refused"] = "ok"
    content: str
    error: ToolError | None = None
    resources: list[ResourceRef] = Field(default_factory=list)
    images: list[ImagePart] = Field(default_factory=list)
    diff: FileDiff | None = Field(default=None, description="The change a write or edit made, for clients.")
    omitted_bytes: int = Field(default=0, ge=0)
    capture_omitted_bytes: int = Field(default=0, ge=0)


def fail(
    what_failed: str,
    next_action: str,
    *,
    argument: str | None = None,
    content: str = "",
    retry_safe: bool = True,
    kind: ToolErrorKind = "failed",
) -> ToolOutput:
    """An error result whose text tells a small model exactly what to do next."""
    error = ToolError(
        kind=kind, what_failed=what_failed, argument=argument, next_action=next_action, retry_safe=retry_safe
    )
    body = f"Error: {what_failed}\nNext: {next_action}"
    return ToolOutput(status="error", content=f"{content}\n\n{body}" if content else body, error=error)


# What to do when the system refuses a path: the next action a model can take for each errno, the rest get the default.
_OS_NEXT_ACTIONS: dict[int, str] = {
    errno.EACCES: "That path is not open to this run. Use a path inside the working directory instead.",
    errno.EPERM: "That path is not open to this run. Use a path inside the working directory instead.",
    errno.EROFS: "That file system is read-only. Use a path inside the working directory instead.",
    errno.ENOENT: "Check the path: read its parent directory to list what is there.",
    errno.ENOTDIR: "A part of the path is a file, not a directory. Check the path.",
    errno.EISDIR: "That path is a directory. Name a file inside it.",
    errno.ENAMETOOLONG: "Use a shorter path.",
    errno.ENOSPC: "The disk is full. Free some space, or write less.",
}


# The refusals a command's output names, as programs print an OSError: `cp: /tmp/x.py: Operation not permitted` (C, Go;
# GNU quotes the path), `[Errno 30] Read-only file system: '/x'` (Python), `EPERM: operation not permitted, open '/x'`
# (Node). EACCES is left out: a command's "Permission denied" is as often a script without its execute bit, or an SSH
# key, as a path the run may not use.
_REFUSED_ERRNOS = {os.strerror(code).lower(): code for code in (errno.EPERM, errno.EROFS)}
_WHY = "|".join(map(re.escape, _REFUSED_ERRNOS))
_REFUSED_PATH = re.compile(
    r"(?:[‘'\"](?P<quoted>[^‘’'\"\n]+)[’'\"]"  # path: why, the path quoted
    r"|(?<![^\s(])(?=[^\s:]*[/.~])(?P<bare>[^\s:‘’'\"]+))"  # or bare, and then it has a / . or ~
    rf": (?P<why>{_WHY})[ \t]*$"
    rf"|(?P<why_first>{_WHY})(?:: |, \w+ )['\"](?P<named>[^'\"\n]+)['\"]",  # why: 'path', or why, open 'path'
    re.IGNORECASE | re.MULTILINE,
)


def refused_path_action(output: str) -> str | None:
    """What to do about the first path `output` says the system refused, or None when it names none."""
    match = _REFUSED_PATH.search(output)
    if match is None:
        return None
    path = match["quoted"] or match["bare"] or match["named"]
    why = (match["why"] or match["why_first"]).lower()
    return f"The system refused {path} ({why}). {_OS_NEXT_ACTIONS[_REFUSED_ERRNOS[why]]}"


def os_failure(tool: str, error: OSError) -> ToolOutput:
    """A failed result for an OSError a tool let through: the system refused the path or the operation, which the
    model can work around, unlike a tool that crashed."""
    what = error.strerror or str(error) or type(error).__name__
    where = f": {error.filename}" if error.filename else ""
    next_action = _OS_NEXT_ACTIONS.get(error.errno or 0, "Try a different path or approach.")
    return fail(f"{tool} failed: {what}{where}", next_action, kind="failed")


def refuse(
    what_failed: str, next_action: str, *, kind: Literal["refused", "stopped"] = "refused", by: str | None = None
) -> ToolOutput:
    """A refused result: an extension (`by`) would not let the call run, or the run stopped before or while it ran."""
    error = ToolError(kind=kind, refused_by=by, what_failed=what_failed, next_action=next_action)
    return ToolOutput(status="refused", content=f"Error: {what_failed}\nNext: {next_action}", error=error)


def file_resource(path: Path, data: bytes) -> ResourceRef:
    return ResourceRef(uri=path.resolve().as_uri(), sha256=hashlib.sha256(data).hexdigest())


def file_diff(path: Path, before: bytes | None, after: bytes) -> FileDiff | None:
    """The diff of a text file's change; None for a binary or undecodable file, or one too big to carry."""
    if len(before or b"") + len(after) > MAX_DIFF_CHARS:
        return None
    try:
        old_text = before.decode() if before is not None else None
        new_text = after.decode()
    except UnicodeDecodeError:
        return None
    return FileDiff(path=str(path.resolve()), old_text=old_text, new_text=new_text)


def _strip_titles(schema: Any) -> Any:
    """Pydantic titles repeat the field names; dropping them saves prompt tokens on every request."""
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if k != "title"}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]  # pyright: ignore[reportUnknownVariableType]
    return schema


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    effects: ClassVar[frozenset[Effect]]
    cancel_on_abort: ClassVar[bool] = False
    """An aborted run cancels this call there and then: it does not watch `ctx.abort`, and has nothing to finish that a
    cancellation would cut short (a search). Any other tool is waited for, so it must end its own work at the abort and
    answer with what it has, as bash stops its command and says what it printed."""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, parameters=_strip_titles(self.Args.model_json_schema())
        )

    @property
    def read_only(self) -> bool:
        return self.effects <= {"read"}

    def for_subagent(self) -> Tool:
        """The tool as a subagent gets it: the same instance, unless it keeps state about the parent's context."""
        return self

    def parse(self, arguments: JsonObject) -> BaseModel | ToolOutput:
        # Pydantic drops a name the tool does not take without a word: `timeout: 900` ran with timeout_s's 120 s and
        # was killed there. The call is not run; the error names what is wrong and the nearest real name.
        known: set[str] = set()
        for name, info in self.Args.model_fields.items():
            alias = info.validation_alias
            choices = alias.choices if isinstance(alias, AliasChoices) else [alias or name]
            known.update(choice for choice in choices if isinstance(choice, str))
        # A tool whose schema is not its model's fields (MCP's, a model that keeps extras) checks its own arguments.
        open_schema = issubclass(self.Args, RootModel) or self.Args.model_config.get("extra") == "allow"
        unknown = [] if open_schema else [name for name in arguments if name not in known]
        if unknown:
            named = []
            for name in unknown:
                near = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.6)
                named.append(f"`{name}`" + (f" (did you mean `{near[0]}`?)" if near else ""))
            return fail(
                f"{self.name} was not run: it does not take {', '.join(named)}",
                f"Call {self.name} again with only its arguments: {', '.join(self.Args.model_fields)}.",
                argument=unknown[0],
                kind="bad_arguments",
            )
        try:
            return self.Args.model_validate(arguments)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(part) for part in first["loc"]) or None
            required = [name for name, info in self.Args.model_fields.items() if info.is_required()]
            return fail(
                f"invalid arguments for {self.name}: {first['msg']}" + (f" (field {field})" if field else ""),
                f"Call {self.name} again with valid arguments; required: {', '.join(required) or 'none'}.",
                argument=field,
                kind="bad_arguments",
            )

    @abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput: ...

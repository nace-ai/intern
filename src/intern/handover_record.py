"""What System 2 is sent of a hand-over's turns at the messages after it (`[capabilities.handoff] system2_reads`).

Unset, System 2 reads the cheaper model's turns as its own, every one in full. With `record` it is sent a record in
their place, then the cheaper model's last reply as the assistant turn the user read. The change is in System 2's
view only: the journal, the terminal UI, a resumed session and the cheaper model's own requests keep every turn.

The record is one harness message of plain text, not shortened tool calls, which a model would copy. Per turn it
keeps what the model said, whole; each command it ran, with what the command printed; and a line for each file it
read, wrote or edited. It leaves out what can be read back from disk: the contents of files, whether a file tool
or a here-document wrote them. A long output or script is cut to its start and end (`OUTPUT_LINES`,
`COMMAND_LINES`). The reasoning of those turns goes to no other model, so the record's head says it is not there.

The record is built by a fixed rule from the journal, with no model call, so it is the same bytes on every request
and stays until a compaction cuts those turns. Nothing about it is silent: the hand-over's journaled event says
that System 2 is sent a record of these turns and by which version of the rule (`Handoff.system2_reads`,
`Handoff.record_rule`), and the view follows that event, not the setting as it stands later. A hand-over journaled
under another version of the rule is sent in full.

What a turn changed on disk comes from the workspace, not from the tools' names: a model writes most files through
the shell. The run looks at the workspace after each turn of the cheaper model and journals what changed
(`FilesChanged`).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from intern.context import ContextView
from intern.events import FileChange, FilesChanged, Handoff
from intern.journal import Entry, EventEntry, MessageEntry
from intern.messages import AssistantMessage, Message, ToolCall, ToolResult, ToolResultMessage, UserMessage

Reads = Literal["record"]

RULE = 1
"""The version of the rule the record is built by: its limits, its wording and `RECORD_HEAD`. A change to any of
them is a new version, since it changes what System 2 is sent."""

RECORD_HEAD = (
    "These turns were carried out in this session, after the turn above. For each, this gives what was said in it, "
    "the commands it ran with what they printed, and the files it read, wrote or edited. File contents are left "
    "out: the files are on disk as these turns left them, so read a file before you change it or say what is in "
    "it. A long output or script is cut where a line in brackets says so, and the part cut is no longer available. "
    "The thinking behind these turns was not kept, only what each turn said: asked why something was done, give "
    "the reason when a turn states it; when none does, say what was done and that the reason was not recorded, "
    "without supplying one. The user saw these turns as part of the conversation, so do not mention this record."
)

MAX_FILES = 20_000
"""Past this many files the workspace is not watched: a look after every turn would cost more than it tells."""
MAX_COUNTED_BYTES = 1_000_000
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "dist", "build", "target"})
OUTPUT_LINES = 40
"""A command's output is kept whole up to this many lines, which 19 in 20 are; `_cut` says what a longer one keeps."""
COMMAND_LINES = 12
"""A command's own text is kept whole up to this many lines, what it writes into a file left out: every command is
that short but one that feeds a program a script, and of a script the start and the end say what it is."""
LINE_CHARS = 160
"""Where a line of output is cut."""
COMMAND_LINE_CHARS = 640
"""Where a line of a command is cut: one line often chains several commands, and its output is read against all."""
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
SIMPLE_COMMAND = re.compile(r"&&|\|\||[;|]")
REDIRECT = re.compile(r"(?<![\d&>])>")
EXIT = re.compile(r"\n?(?:\[exit code (-?\d+)\]|Error: the command exited with code (-?\d+)\nNext: [^\n]*)\s*$")
"""How the shell tool ends a command's output: its exit code, and after a failure what to do next."""

Snapshot = dict[str, tuple[int, int]]


def snapshot(root: Path) -> Snapshot | None:
    """Every file under `root` with its modification time and size, hidden and dependency folders left out; None
    when there are too many to look at after every turn."""
    found: Snapshot = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIPPED_DIRS and ".egg-info" not in d)
        for name in files:
            path = Path(folder) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            found[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
            if len(found) > MAX_FILES:
                return None
    return found


def _lines(path: Path, size: int) -> int | None:
    if size > MAX_COUNTED_BYTES:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def changes(before: Snapshot, after: Snapshot, root: Path) -> list[FileChange]:
    """What differs between two looks at the workspace, by path."""
    found: list[FileChange] = []
    for path in sorted(before.keys() | after.keys()):
        if path not in after:
            found.append(FileChange(path=path, change="deleted"))
        elif before.get(path) != after[path]:
            change = "changed" if path in before else "created"
            found.append(FileChange(path=path, change=change, lines=_lines(root / path, after[path][1])))
    return found


@dataclass
class Unit:
    """One turn of a hand-over's model with its tool results and what the harness said after it, or one message of
    the user's sent while it worked."""

    kind: Literal["turn", "user", "harness"]
    number: int
    entries: list[tuple[str, Message]] = field(default_factory=list)

    @property
    def message(self) -> AssistantMessage:
        message = self.entries[0][1]
        assert isinstance(message, AssistantMessage)
        return message

    @property
    def results(self) -> dict[str, ToolResult]:
        return {m.result.call_id: m.result for _, m in self.entries if isinstance(m, ToolResultMessage)}


@dataclass
class Stretch:
    """The turns one hand-over's model took, from the hand-over to the end of that run."""

    run_id: str | None
    after: int
    units: list[Unit] = field(default_factory=list)
    reads: Reads | None = None
    """What the hand-over's event says System 2 is sent of these turns; None, every turn in full."""
    rule: int | None = None
    files: dict[int, list[FileChange]] = field(default_factory=dict)

    def add(self, entry_id: str, message: Message) -> None:
        number = self.units[-1].number if self.units else self.after
        if isinstance(message, AssistantMessage):
            self.units.append(Unit("turn", number + 1, [(entry_id, message)]))
        elif isinstance(message, UserMessage) and message.source == "user":
            self.units.append(Unit("user", number, [(entry_id, message)]))
        elif self.units and self.units[-1].kind == "turn":
            self.units[-1].entries.append((entry_id, message))
        else:
            self.units.append(Unit("harness", number, [(entry_id, message)]))

    @property
    def turns(self) -> list[Unit]:
        return [unit for unit in self.units if unit.kind == "turn"]

    def final(self) -> Unit | None:
        """The reply the run ended on, which the user read: the last turn, when it called no tool."""
        last = self.units[-1] if self.units else None
        if last is not None and last.kind == "turn" and not last.message.tool_calls and last.message.text.strip():
            return last
        return None


def stretches(path: list[Entry]) -> list[Stretch]:
    """Each hand-over on the journal's active path with the turns after it and what they changed on disk."""
    found: list[Stretch] = []
    current: Stretch | None = None
    run_id: str | None = None
    for entry in path:
        if isinstance(entry, MessageEntry):
            if current is not None and entry.run_id != current.run_id:
                current = None  # that run ended: the next one is System 2's again
            if current is not None:
                current.add(entry.id, entry.message)
            run_id = entry.run_id
        elif isinstance(entry, EventEntry):
            event = entry.event
            if isinstance(event, Handoff):
                current = Stretch(run_id, event.turn, reads=event.system2_reads, rule=event.record_rule)
                found.append(current)
            elif current is not None and isinstance(event, FilesChanged):
                current.files[event.turn] = event.files
    return found


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _cut(lines: list[str], cap: int) -> list[str]:
    """`lines` whole when there are at most `cap`; of more, the first quarter of `cap` and the last half, with how
    many are left out between: the end of a text is where a result, an error or a total stands."""
    if len(lines) <= cap:
        return lines
    head, tail = cap // 4, cap // 2
    return [*lines[:head], f"[{_count(len(lines) - head - tail, 'line')} left out]", *lines[len(lines) - tail :]]


def _clip(line: str, limit: int) -> str:
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _writes_file(line: str, operator: re.Match[str]) -> bool:
    """Whether the here-document opened on `line` is written into a file (`cat > path <<EOF`, `tee path <<EOF`) and
    not fed to a program."""
    before = SIMPLE_COMMAND.split(line[: operator.start()])[-1]
    after = SIMPLE_COMMAND.split(line[operator.end() :])[0]
    words = [word for word in before.split() if word != "sudo"]
    name = words[0] if words else ""
    return name == "tee" or (name == "cat" and REDIRECT.search(before + after) is not None)


def _command(command: str, cwd: Path) -> list[str]:
    """A command's own lines, without the `cd` into the working directory a model opens most of them with. The text
    a here-document writes into a file is left out, as a written file's contents are: it is on disk. One fed to a
    program is part of what was run and stays."""
    lines = command.strip().splitlines() or [""]
    for root in sorted({str(cwd), str(cwd.resolve())}, key=len, reverse=True):
        if (stripped := re.sub(rf"^cd {re.escape(root)}/?\s*(&&|;)\s*", "", lines[0])) != lines[0]:
            lines[0] = stripped
            break
    kept: list[str] = []
    end: str | None = None
    written = 0
    for line in lines:
        if end is None:
            kept.append(line.rstrip())
            if (operator := HEREDOC.search(line)) is not None and _writes_file(line, operator):
                end, written = operator.group(2), 0
        elif line.strip() == end:
            kept += [f"[{_count(written, 'line')} written to the file]", line.rstrip()]
            end = None
        else:
            written += 1
    if end is not None:
        kept.append(f"[{_count(written, 'line')} written to the file]")
    return [_clip(line, COMMAND_LINE_CHARS) for line in _cut(kept, COMMAND_LINES)]


def _path(value: object, cwd: Path) -> str:
    path = Path(str(value))
    if path.is_absolute():
        for root in (cwd, cwd.resolve()):
            if path.is_relative_to(root):
                return path.relative_to(root).as_posix()
    return str(value)


def _outcome(result: ToolResult | None) -> list[str]:
    """What a command or a tool gave back: its exit code or status, how much it printed, and what it printed."""
    if result is None:
        return ["    no result"]
    match = EXIT.search(result.content)
    code = (match.group(1) or match.group(2)) if match else None
    printed = [line.rstrip() for line in result.content[: match.start() if match else None].splitlines()]
    printed = [line for line in printed if line]
    status = f"exit code {code}" if code is not None else result.status
    if not printed:
        return [f"    {status}, no output"]
    kept = [f"    {_clip(line, LINE_CHARS)}" for line in _cut(printed, OUTPUT_LINES)]
    return [f"    {status}, {_count(len(printed), 'line')} of output:", *kept]


def _failed(result: ToolResult | None) -> str:
    if result is None or result.status == "ok":
        return ""
    said = result.content.strip().splitlines()
    return f" → {result.status}: {_clip(said[0] if said else 'no message', LINE_CHARS)}"


def _actions(unit: Unit, cwd: Path) -> Iterator[tuple[str, str | None]]:
    """Each thing a turn did, with the file it wrote or edited (None for the rest). A file tool gets a line, its
    contents being on disk; a command, or any other tool, is given with what it printed."""
    results = unit.results
    edits: dict[str, int] = {}
    calls: list[ToolCall] = unit.message.tool_calls
    for call in calls:
        if call.name == "edit" and (results.get(call.id) is None or results[call.id].status == "ok"):
            path = _path(call.arguments.get("path", ""), cwd)
            edits[path] = edits.get(path, 0) + 1
    for call in calls:
        result, arguments = results.get(call.id), call.arguments
        path = _path(arguments.get("path", ""), cwd)
        if call.name == "write":
            content = str(arguments.get("content", ""))
            count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            yield f"wrote {path} ({_count(count, 'line')}){_failed(result)}", path if not _failed(result) else None
        elif call.name == "edit":
            if _failed(result):
                yield f"edit of {path}{_failed(result)}", None
            elif path in edits:
                yield f"edited {path} ({_count(edits.pop(path), 'change')})", path
        elif call.name == "read":
            yield f"read {path}{_failed(result)}", None
        elif call.name == "bash":
            command = _command(str(arguments.get("command", "")), cwd)
            ran = [f"ran: {command[0]}"] if len(command) == 1 else ["ran:", *(f"    {line}" for line in command)]
            yield "\n".join([*ran, *_outcome(result)]), None
        else:
            given = json.dumps(arguments, ensure_ascii=False, sort_keys=True) if arguments else ""
            used = f"{call.name}: {_clip(given, COMMAND_LINE_CHARS)}" if given else call.name
            yield "\n".join([used, *_outcome(result)]), None


def _on_disk(files: list[FileChange], named: set[str]) -> str | None:
    """What a turn changed in the workspace beyond the files its own lines name."""
    parts: list[str] = []
    for file in files:
        if file.change == "deleted":
            parts.append(f"deleted {file.path}")
        elif file.path not in named:
            size = f" ({_count(file.lines, 'line')})" if file.lines is not None else ""
            parts.append(f"{file.change} {file.path}{size}")
    return "on disk: " + "; ".join(parts) if parts else None


def turn_record(unit: Unit, files: list[FileChange], cwd: Path) -> str:
    """One turn as the record has it: what the model said, whole, then each thing it did."""
    lines = [f"Turn {unit.number}. {unit.message.text.strip()}".rstrip()]
    named: set[str] = set()
    for line, path in _actions(unit, cwd):
        lines.append(f"  - {line}")
        if path is not None:
            named.add(path)
    if (disk := _on_disk(files, named)) is not None:
        lines.append(f"  - {disk}")
    return "\n".join(lines)


def _span(units: list[Unit]) -> str:
    first, last = units[0].number, units[-1].number
    return f"turn {first}" if first == last else f"turns {first} to {last}"


def record(stretch: Stretch, units: list[Unit], cwd: Path, *, first: bool, last: bool) -> str:
    """The record of `units`, some turns in a row of `stretch`: headed by what it is when it is the stretch's first,
    and ended by the files the whole stretch changed when it is its last."""
    head = f"[Record of {_span(units)}]\n{RECORD_HEAD}" if first else f"[Record, continued: {_span(units)}]"
    body = "\n".join(turn_record(unit, stretch.files.get(unit.number, []), cwd) for unit in units)
    parts = [head, body]
    if last:
        parts.append(_changed(stretch, cwd))
    return "\n\n".join(parts)


def _changed(stretch: Stretch, cwd: Path) -> str:
    if stretch.files:
        latest: dict[str, str] = {}
        for number in sorted(stretch.files):
            for file in stretch.files[number]:
                latest[file.path] = file.change
        kept = sorted(path for path, change in latest.items() if change != "deleted")
        gone = sorted(path for path, change in latest.items() if change == "deleted")
        text = "Files these turns created or changed: " + (", ".join(kept) or "none") + "."
        return text + (f" Deleted: {', '.join(gone)}." if gone else "")
    named = sorted({path for unit in stretch.turns for _, path in _actions(unit, cwd) if path is not None})
    return (
        "Files these turns wrote or edited with the file tools: "
        + (", ".join(named) or "none")
        + ". Their shell commands may have changed others."
    )


def apply(view: ContextView, path: list[Entry], cwd: Path) -> None:
    """Rewrite `view` as System 2 is sent it: the turns of each hand-over journaled with `system2_reads = record`,
    as far as the history still holds them, become their record, and the reply the run ended on stays. A record
    takes the place, and the id, of the first message it stands for, so a compaction cuts at it as at any message;
    the State covers it once it covers the last of them. A message the user sent between those turns stays, and
    the record goes on after it."""
    held = set(view.history_ids)
    replaced: dict[str, list[tuple[str, Message]]] = {}
    last_of: dict[str, str] = {}
    for stretch in stretches(path):
        if stretch.reads != "record" or stretch.rule != RULE:
            continue
        final = stretch.final()
        body = [unit for unit in stretch.units if unit is not final and unit.entries[0][0] in held]
        chunks: list[list[Unit]] = [[]]
        for unit in body:
            if unit.kind == "user":
                chunks.append([])
            else:
                chunks[-1].append(unit)
        pieces = [[unit for unit in chunk if unit.kind == "turn"] for chunk in chunks]
        recorded = [piece for piece in pieces if piece]
        for chunk, piece in zip(chunks, pieces, strict=True):
            ids = [entry_id for unit in chunk for entry_id, _ in unit.entries]
            for entry_id in ids:
                replaced[entry_id] = []
            if piece:
                text = record(stretch, piece, cwd, first=piece is recorded[0], last=piece is recorded[-1])
                replaced[ids[0]] = [(ids[0], UserMessage(content=text, source="harness"))]
                last_of[ids[0]] = ids[-1]
    if not replaced:
        return
    at = {entry_id: index for index, entry_id in enumerate(view.history_ids)}
    history: list[Message] = []
    history_ids: list[str] = []
    covered = 0
    for entry_id, message in zip(view.history_ids, view.history, strict=False):
        for new_id, new_message in replaced.get(entry_id, [(entry_id, message)]):
            history_ids.append(new_id)
            history.append(new_message)
            covered += at[last_of.get(new_id, new_id)] < view.covered
    view.history, view.history_ids, view.covered = history, history_ids, covered

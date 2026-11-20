from __future__ import annotations

import contextlib
import re
import shlex
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, SecretStr

from intern.journal import Journal, MessageEntry, SessionEntry
from intern.memory.format import CODE_SUFFIXES, FOLDER_SUFFIXES
from intern.memory.format import MAX_FOLDER_FILE_CHARS as MAX_FILE_CHARS
from intern.memory.steward.extract import INJECTION, call_results, secrets_in
from intern.messages import AssistantMessage, ToolResultMessage, UserMessage
from intern.secrets import SecretRedactor

MAX_ARTIFACTS = 8
SAID_CHARS = 8_000
INTERPRETERS = {"python": ".py", "python3": ".py", "bash": ".sh", "sh": ".sh", "node": ".js"}
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)(\w+)\2")
_REDIRECT = re.compile(r"(?:^|\s)(?:>|1>)\s*(['\"]?)([^\s'\"<>|;&]+)\1")
_FAILED_EXIT = re.compile(r"(?m)^\[exit code (?!0\])-?\d+\]$")


class Artifact(BaseModel):
    # A file the session wrote, or a script it ran inline, as it last stood.
    name: str
    path: str
    content: str
    pointer: str
    ran: bool = False
    ok: bool = False

    @property
    def code(self) -> bool:
        return self.name.endswith(CODE_SUFFIXES)


class Said(BaseModel):
    pointer: str
    text: str


class Collected(BaseModel):
    artifacts: list[Artifact]
    said: list[Said]


def _heredocs(command: str) -> list[tuple[str, str]]:
    # Each heredoc as the line that opens it and its body.
    lines = command.split("\n")
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        match = _HEREDOC.search(lines[i])
        if match is None:
            i += 1
            continue
        strip, delimiter = match[1] == "-", match[3]
        body: list[str] = []
        j = i + 1
        while j < len(lines) and (lines[j].strip() if strip else lines[j]) != delimiter:
            body.append(lines[j].lstrip("\t") if strip else lines[j])
            j += 1
        found.append((lines[i], "\n".join(body) + "\n"))
        i = j + 1
    return found


def _without_bodies(command: str) -> str:
    lines = command.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        kept.append(lines[i])
        match = _HEREDOC.search(lines[i])
        if match is None:
            i += 1
            continue
        strip, delimiter = match[1] == "-", match[3]
        i += 1
        while i < len(lines) and (lines[i].strip() if strip else lines[i]) != delimiter:
            i += 1
        i += 1
    return "\n".join(kept)


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _runs(command: str) -> list[str]:
    # The script files a command runs: `python3 tool.py`, `bash fix.sh`, `./fix.sh`.
    found: list[str] = []
    for segment in re.split(r"&&|\|\||;|\||\n", _without_bodies(command)):
        words = _words(segment)
        while words and (words[0] in ("cd", "time", "env", "timeout", "nohup") or "=" in words[0]):
            words = words[2:] if words[0] in ("cd", "timeout") else words[1:]
        if not words:
            continue
        program = PurePosixPath(words[0]).name
        if program in INTERPRETERS:
            script = next((w for w in words[1:] if not w.startswith("-")), "")
            if script.endswith(FOLDER_SUFFIXES):
                found.append(script)
        elif words[0].startswith(("./", "/")) and words[0].endswith(CODE_SUFFIXES):
            found.append(words[0])
    return found


def _inline_suffix(opener: str) -> str:
    # `python3 - <<'EOF'` and `python3 <<EOF` run their body; `cat > x <<EOF` writes it.
    words = _words(opener.split("<<", 1)[0])
    program = PurePosixPath(words[0]).name if words else ""
    return INTERPRETERS.get(program, "") if all(w in ("-", "-s") or w.startswith("-") for w in words[1:]) else ""


def _resolved(path: str, cwd: Path) -> str:
    target = Path(path).expanduser()
    return str(target if target.is_absolute() else cwd / target)


def collect(journal_path: Path, drive_root: Path | None = None) -> Collected:
    # What a skill folder can be built from: the files a session wrote as they last stood, the scripts it ran and
    # whether their last run worked, and what the user said, in full.
    path = Journal(cwd="", path=journal_path).active_path()
    session = next(e for e in path if isinstance(e, SessionEntry))
    cwd = Path(session.cwd).resolve()
    sid = journal_path.stem
    drive = drive_root.expanduser().resolve() if drive_root else None
    found: dict[str, Artifact] = {}
    edited: set[str] = set()
    ran_by: dict[str, list[str]] = {}
    said: list[Said] = []
    inline = 0

    def keep(key: str, name: str, shown: str, content: str, pointer: str) -> None:
        old = found.pop(key, None)
        found[key] = Artifact(
            name=name, path=shown, content=content, pointer=pointer, ran=old.ran if old else False, ok=False
        )

    for entry in path:
        if not isinstance(entry, MessageEntry):
            continue
        pointer = f"evt#{sid}:{entry.id}"
        match entry.message:
            case UserMessage(source="user", content=text):
                said.append(Said(pointer=pointer, text=text.strip()[:SAID_CHARS]))
            case AssistantMessage(tool_calls=tool_calls):
                for call in tool_calls:
                    args = call.arguments
                    target = args.get("path")
                    if call.name == "write" and isinstance(target, str) and isinstance(args.get("content"), str):
                        key = _resolved(target, cwd)
                        keep(key, PurePosixPath(target).name, target, str(args["content"]), pointer)
                    elif call.name == "edit" and isinstance(target, str):
                        key = _resolved(target, cwd)
                        old, new = args.get("old_string"), args.get("new_string")
                        if key in found and isinstance(old, str) and isinstance(new, str) and old in found[key].content:
                            count = -1 if args.get("replace_all") else 1
                            content = found[key].content.replace(old, new, count)
                            keep(key, found[key].name, target, content, pointer)
                        else:
                            edited.add(key)
                    command = args.get("command") if call.name == "bash" else None
                    if not isinstance(command, str):
                        continue
                    keys: list[str] = []
                    for opener, body in _heredocs(command):
                        head = _HEREDOC.sub(" ", opener)
                        words = _words(head)
                        program = PurePosixPath(words[0]).name if words else ""
                        redirect = _REDIRECT.search(head)
                        tee = next((w for w in words[1:] if not w.startswith("-")), "") if program == "tee" else ""
                        if written := (redirect[2] if redirect and program == "cat" else tee):
                            keep(_resolved(written, cwd), PurePosixPath(written).name, written, body, pointer)
                        elif suffix := _inline_suffix(opener):
                            inline += 1
                            key = f"inline:{inline}"
                            keep(key, f"inline-{inline}{suffix}", "", body, pointer)
                            keys.append(key)
                    for script in _runs(command):
                        resolved = _resolved(script, cwd)
                        name = PurePosixPath(script).name
                        same = next((k for k, a in found.items() if a.name == name), "")
                        key = resolved if resolved in found else same
                        if not key:
                            key = resolved
                            edited.add(key)
                            found[key] = Artifact(name=name, path=script, content="", pointer=pointer)
                        keys.append(key)
                    ran_by[call.id] = keys
            case ToolResultMessage(result=result):
                keys = ran_by.pop(result.call_id, [])
                worked = all(call_results(result.content, result.status, 1)) and not _FAILED_EXIT.search(result.content)
                for key in keys:
                    if key in found:
                        found[key] = found[key].model_copy(update={"ran": True, "ok": worked})
            case _:
                pass

    # A file edited in place or written by a program is read as it is now, when the workspace still holds it.
    for key, artifact in list(found.items()):
        if key.startswith("inline:"):
            continue
        disk = Path(key)
        inside_drive = drive is not None and (drive == disk or drive in disk.parents)
        if inside_drive:
            del found[key]
            continue
        if (key in edited or not artifact.content) and disk.is_file() and disk.stat().st_size <= MAX_FILE_CHARS:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                found[key] = artifact.model_copy(update={"content": disk.read_text()})
    kept = [
        a
        for a in reversed(found.values())
        if a.name.endswith(FOLDER_SUFFIXES) and a.content.strip() and len(a.content) <= MAX_FILE_CHARS
    ]
    # Scripts that ran and worked come first, the latest first: they are what the work was done with.
    kept.sort(key=lambda a: (not (a.ran and a.ok), not a.code))
    return _cleaned(Collected(artifacts=kept[:MAX_ARTIFACTS], said=said))


def _cleaned(collected: Collected) -> Collected:
    # The drive is shared: a credential the session saw is replaced, and a file that speaks to AI assistants is left
    # out, as the steward does for the session itself.
    texts = [a.content for a in collected.artifacts] + [s.text for s in collected.said]
    found = {value for text in texts for value in secrets_in(text)}
    redactor = SecretRedactor([SecretStr(value) for value in found]) if found else None

    def clean(text: str) -> str:
        return redactor.redact(text) if redactor else text

    artifacts = [
        a.model_copy(update={"content": clean(a.content)})
        for a in collected.artifacts
        if not INJECTION.search(a.content)
    ]
    said = [s.model_copy(update={"text": clean(s.text)}) for s in collected.said if not INJECTION.search(s.text)]
    return Collected(artifacts=artifacts, said=said)

"""The hand-over record eval: what System 2 should be sent of a cheaper model's turns at the message after a
hand-over (`[capabilities.handoff] system2_reads`, `intern.handover_record`).

It replays follow-up messages from frozen sessions. A session is one task (`tasks.py`) run once with a hand-over;
its journal, working directory and config home are kept as the run left them. Every follow-up is then sent from that
state under each arm, the working directory restored to its own path first, since the journal holds absolute paths.

    uv run evals/handover_record/run.py sessions --root R [--only ID,...]   run the tasks, freeze each session
    uv run evals/handover_record/run.py checks   --root R                   the checks each frozen session fails
    uv run evals/handover_record/run.py audit    --root R                   per arm, where each answer-key fact is
    uv run evals/handover_record/run.py replay   --root R [--plan P.json]   every follow-up under every arm
    uv run evals/handover_record/run.py judge    --root R                   grade the "why" answers
    uv run evals/handover_record/run.py report   --root R                   the tables

Arms: `full` (every turn, the setting unset) and `record`. A session's journal says which one System 2 is sent, in
its hand-over event, so a replay's arm is set by writing that event into the copy of the journal it runs on.

Follow-ups, in `R/<task>/followups.json`: `recall` (a question, with every tool refused), `why` (the reason for a
choice the cheaper model made; `where` says whether a turn's text states it), `fact` (something a command printed
that no file holds), `change` (a small change, from the task), `fix` (the report of a check the frozen session
fails), `detail` (write down what `fact` asks about, so a change that needs it), `task` (a small task that has
nothing to do with the session) and `hello`. `recall`, `why` and `fact` are written by hand from each journal;
`checks` writes the rest.

Three more are sent under a condition of their own: `cold` (the `recall` question once the provider holds nothing of
the session), `cut` (the `fact` question after a compaction that cut the hand-over's turns in the middle) and `tight`
(the `fact` question in a window so small that the request compacts first).

`R/sets.json`, when there, names sets of sessions (`{"tuned": [...], "held_out": [...]}`) the report keeps apart.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from tasks import PREAMBLE, TASKS, Task

from intern import handover_record
from intern.context import TokenMeter, build_view, dropped_turns
from intern.instructions import Instructions
from intern.journal import CompactionEntry, Entry, Journal, MessageEntry, SessionEntry
from intern.messages import AssistantMessage, ToolResultMessage, UserMessage

REPO = Path(__file__).resolve().parents[2]
INTERN = REPO / ".venv" / "bin" / "intern"
PYTHON = REPO / ".venv" / "bin" / "python"
SYSTEM2, CHEAP = "anthropic/claude-opus-5-5", "anthropic/claude-haiku-5-5"
ARMS: dict[str, str | None] = {"full": None, "record": "record"}
"""What the hand-over's journaled event says System 2 is sent of its turns, per arm; `stamp` writes it."""
KINDS = ("recall", "why", "change", "fact", "fix", "hello", "task", "detail")
SPECIAL = {"cold": "recall", "cut": "fact", "tight": "fact"}
"""Follow-ups sent under a condition of their own, and the follow-up each one sends: `cold` once the provider holds
nothing of the session, `cut` after a compaction that cut the hand-over's turns in the middle, `tight` in a window
so small that the request compacts first."""
CHAIN = ("fact", "why")  # sent after `change` on the same session, for what a second and a third message cost
NO_TOOLS = ("recall", "cold")
IN_REPLY = ("recall", "fact", "cold", "cut", "tight")
STATED = ("prose", "reply")
"""Where a `why` follow-up's reason stands when System 2 can read it: in a turn's text, which the record keeps, or in
the last reply. A reason that is only in the cheaper model's reasoning, or nowhere, no arm sends."""
HELLO = "hello"
TASK = """Something else, unrelated to the package: in a new folder `extras/` write `roman.py` with `to_roman(n)` \
for 1 to 3999 and `from_roman(text)` for the numerals it gives, each raising ValueError for anything else (0, 4000, \
"IIII", "IC", an empty string), and `extras/test_roman.py` with a few tests. Run them."""
TASK_CHECKS = r"""
sys.path.insert(0, os.path.join(os.getcwd(), "extras"))


def test_values():
    from roman import from_roman, to_roman

    assert to_roman(1994) == "MCMXCIV" and to_roman(3999) == "MMMCMXCIX" and to_roman(4) == "IV"
    assert from_roman("MMXXVI") == 2026 and from_roman("XLII") == 42


def test_round_trip():
    from roman import from_roman, to_roman

    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))


def test_errors():
    from roman import from_roman, to_roman

    for bad in (0, 4000, -3):
        with pytest.raises(ValueError):
            to_roman(bad)
    for bad in ("IIII", "IC", "", "VX", "abc"):
        with pytest.raises(ValueError):
            from_roman(bad)
"""
DETAIL = (
    "Add a file `NOTES.md` at the top of this folder with a section `## Checks run by hand` that answers the "
    "following from what you saw at the time, with the exact text or figures as they were printed; change nothing "
    "else. {question}"
)
DETAIL_TURNS = 6  # a session whose cheaper model took at least this many turns gets a `detail` follow-up
DECIDER = '[decider]\nbackend = "decisions-api"\nmodel = "nace-ai/drex-max"\n'
PRIMER = "Reply with the one word OK."
BY_ID = {task.id: task for task in TASKS}
AS_JOURNALED = "as journaled"
SKIPPED = {".venv", "__pycache__", ".pytest_cache", ".git", ".mypy_cache", ".ruff_cache"}
STUB = re.compile(
    r"\.\.\. ?\(?(rest|existing|unchanged|remaining|same as|as before)|^\s*#\s*\.\.\.\s*$|\[\.\.\.\]|…"
    r"|\(\+?\d+ (more )?lines\)|\(unchanged\)",
    re.I | re.M,
)
MENTION = re.compile(
    r"\b(the|this) record\b|\[record|\banother agent\b|\b(previous|other|earlier|cheaper|different) (agent|model|"
    r"assistant)\b|\bhand(ed|ing)?[- ]over\b|\b(took|taken|taking) over\b|\bhaiku\b",
    re.I,
)
SHELL_READ = re.compile(r"(^|[;&|]\s*)(cat|head|tail|less|sed -n|nl|grep|rg|awk)\b")


def config(home: Path, reads: str | None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    setting = f'\n[capabilities.handoff]\nsystem2_reads = "{reads}"\n' if reads else ""
    (home / "config.toml").write_text(DECIDER + setting)


def stamp(journal: Path, reads: str | None) -> None:
    """Make a journal's hand-over say what System 2 is sent of its turns, as a run under that setting would have
    journaled it: this is what sets a replay's arm, since the view follows the journal."""
    lines: list[str] = []
    for line in jsonl(journal.read_text()):
        entry = json.loads(line)
        if entry.get("kind") == "event" and entry["event"]["type"] == "handoff":
            entry["event"]["system2_reads"] = reads
            entry["event"]["record_rule"] = handover_record.RULE if reads else None
            line = json.dumps(entry, ensure_ascii=False)
        lines.append(line)
    journal.write_text("\n".join(lines) + "\n")


def intern(root: Path, task: str, args: list[str], prompt: str, out: Path, timeout: int = 1500) -> int:
    """One headless run in the task's working directory, on the frozen source; its event stream goes to `out`. A
    frozen session is gone on with on the day it was recorded (`on_the_day`)."""
    out.mkdir(parents=True, exist_ok=True)
    frozen = root / task / "frozen" / "session.jsonl"
    program = [str(INTERN)]
    if frozen.exists():
        with frozen.open() as lines:
            day = date.fromtimestamp(json.loads(next(lines))["timestamp"] / 1000)
        program = [str(PYTHON), "-m", "on_the_day", day.isoformat()]
    env = {
        **os.environ,
        "INTERN_HOME": str(root / task / "home"),
        "PYTHONPATH": f"{root}/src/src:{root}/src/evals/handover_record",
    }
    command = [*program, "-p", "--json", "--no-memory", "--no-context-files", "--system2", SYSTEM2, *args]
    with (out / "events.jsonl").open("w") as stdout, (out / "stderr.txt").open("w") as stderr:
        try:
            done = subprocess.run(
                [*command, "--", prompt],
                cwd=root / task / "work",
                env=env,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124
    return done.returncode


def jsonl(text: str) -> list[str]:
    """The lines of a JSON Lines text. Not `splitlines`: that also breaks at U+2028, which a line may hold as it is."""
    return [line for line in text.split("\n") if line.strip()]


def events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in jsonl(path.read_text())] if path.exists() else []


def freeze_source(root: Path) -> str:
    """A copy of the source as committed, so every session and replay runs the same code."""
    target = root / "src"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout
    if not (target / "COMMIT").exists():
        target.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "archive", "HEAD", "src", "evals/handover_record"], cwd=REPO, capture_output=True
        )
        subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
        (target / "COMMIT").write_text(commit)
    return (target / "COMMIT").read_text().strip()


def sync(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", f"{source}/", f"{target}/"], check=True)


def restore(root: Path, task: str, reads: str | None = None) -> Path:
    """The session as frozen, in place: working directory, config home and a fresh copy of the journal, which says
    that System 2 is sent `reads` of the hand-over's turns."""
    base = root / task
    sync(base / "frozen" / "work", base / "work")
    sync(base / "frozen" / "home", base / "home")
    config(base / "home", None)
    shutil.copy(base / "frozen" / "session.jsonl", base / "live.jsonl")
    stamp(base / "live.jsonl", reads)
    return base / "live.jsonl"


def journal_entries(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in jsonl(path.read_text())]


def requests_of(entries: list[dict[str, Any]], after: int = 0) -> list[dict[str, Any]]:
    """The assistant messages journaled from entry `after` on."""
    return [e["message"] for e in entries[after:] if e.get("kind") == "message" and e["message"]["role"] == "assistant"]


def session_summary(root: Path, task: str) -> dict[str, Any]:
    entries = journal_entries(root / task / "frozen" / "session.jsonl")
    replies = requests_of(entries)
    handoff = next((e["event"] for e in entries if e.get("kind") == "event" and e["event"]["type"] == "handoff"), None)
    cheap = [m for m in replies if m["model"] == CHEAP]
    calls = [call for m in cheap for call in m["tool_calls"]]
    files = [e["event"] for e in entries if e.get("kind") == "event" and e["event"]["type"] == "files_changed"]
    by_tool = {name: sum(call["name"] == name for call in calls) for name in sorted({call["name"] for call in calls})}
    end = next((e for e in reversed(events(root / task / "session" / "events.jsonl")) if e["type"] == "run_end"), {})
    return {
        "task": task,
        "reason": end.get("reason"),
        "turns": len(replies),
        "handoff_turn": handoff["turn"] if handoff else None,
        "cheap_turns": len(cheap),
        "cheap_calls": by_tool,
        "files_changed_events": len(files),
        "cost": round(sum(m["usage"]["cost_usd"] for m in replies), 4),
        "context_tokens": replies[-1]["usage"]["input_tokens"] if replies else 0,
        "seconds": round(end.get("duration_ms", 0) / 1000),
    }


def run_session(root: Path, task: Task) -> dict[str, Any]:
    base = root / task.id
    if not (base / "frozen" / "session.jsonl").exists():
        for folder in ("work", "home", "frozen"):
            shutil.rmtree(base / folder, ignore_errors=True)
        (base / "work").mkdir(parents=True)
        config(base / "home", "record")  # journals what each turn of the cheaper model changed on disk
        journal = base / "session.jsonl"
        journal.unlink(missing_ok=True)
        intern(root, task.id, ["--session", str(journal), "--handoff-to", CHEAP], task.prompt, base / "session", 3000)
        sync(base / "work", base / "frozen" / "work")
        sync(base / "home", base / "frozen" / "home")
        shutil.copy(journal, base / "frozen" / "session.jsonl")
    summary = session_summary(root, task.id)
    (base / "session.json").write_text(json.dumps(summary, indent=1))
    return summary


def run_checks(work: Path, package: str, source: str) -> dict[str, bool]:
    """The task's checks against a working directory, by its own interpreter when it has pytest."""
    own = work / ".venv" / "bin" / "python"
    has_pytest = own.exists() and any((work / ".venv").glob("lib/*/site-packages/pytest"))
    python = str(own) if has_pytest else sys.executable
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "test_checks.py"
        file.write_text(PREAMBLE.replace("PKG", repr(package)) + source)
        command = [python, "-m", "pytest", "-q", "--tb=no", "-rA", "-p", "no:cacheprovider", str(file)]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("VIRTUAL_ENV", None)
        try:
            done = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return {}
    found = re.findall(r"^(PASSED|FAILED|ERROR) \S+::(\w+)", done.stdout, re.M)
    return {name: status == "PASSED" for status, name in found}


def reports(task: Task) -> dict[str, str]:
    """Each check's docstring: the report a user would file for it."""
    found = re.findall(r'^def (test_\w+)\([^)]*\):\n    r?"""(.*?)"""', task.checks, re.M | re.S)
    return {name: " ".join(text.split()) for name, text in found}


def cmd_sessions(root: Path, only: list[str], jobs: int) -> None:
    print("source", freeze_source(root))
    chosen = [task for task in TASKS if not only or task.id in only]
    with ThreadPoolExecutor(jobs) as pool:
        for summary in pool.map(lambda task: run_session(root, task), chosen):
            print(json.dumps(summary))


def cmd_checks(root: Path, only: list[str]) -> None:
    """Which checks each frozen session fails, and the follow-ups that need no writing by hand: `change` from the
    task, `fix` for the first check it fails, `hello` and `task`, the same everywhere, and `detail`, which asks a long
    session to write down what its `fact` follow-up asks about."""
    for task in TASKS:
        base = root / task.id
        if (only and task.id not in only) or not (base / "frozen" / "session.jsonl").exists():
            continue
        restore(root, task.id)
        results = run_checks(base / "work", task.package, task.checks)
        failed = [name for name, passed in results.items() if not passed]
        (base / "frozen" / "checks.json").write_text(json.dumps(results, indent=1))
        path = base / "followups.json"
        followups = json.loads(path.read_text()) if path.exists() else {}
        followups["change"] = {"prompt": task.change}
        if failed:
            report = reports(task)[failed[0]]
            followups["fix"] = {"prompt": f"{report} Fix it, add a test for it and run the tests.", "check": failed[0]}
        else:
            followups.pop("fix", None)
        followups["hello"] = {"prompt": HELLO}
        followups["task"] = {"prompt": TASK}
        turns = session_summary(root, task.id)["cheap_turns"]
        if "fact" in followups and turns >= DETAIL_TURNS:
            question = followups["fact"]["prompt"].removeprefix("Without changing anything: ")
            question = question[:1].upper() + question[1:]
            followups["detail"] = {"prompt": DETAIL.format(question=question), "facts": followups["fact"]["facts"]}
        else:
            followups.pop("detail", None)
        path.write_text(json.dumps(followups, indent=1, ensure_ascii=False))
        print(f"{task.id:10} {len(results) - len(failed):2}/{len(results):2} pass; fails: {', '.join(failed) or '-'}")


def active_path(journal: Path, reads: str | None = AS_JOURNALED) -> tuple[list[Entry], Path]:
    """A journal's active path, read from a copy so the file is never touched, and the session's working directory.
    With `reads`, the copy's hand-over says System 2 is sent that."""
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "journal.jsonl"
        shutil.copy(journal, copy)
        if reads != AS_JOURNALED:
            stamp(copy, reads)
        path = Journal("", copy).active_path()
    return path, Path(next(entry.cwd for entry in path if isinstance(entry, SessionEntry)))


def view_text(journal: Path, reads: str | None) -> tuple[str, int]:
    """Everything System 2 would be sent of the session at its next message under `reads`, as one text, and how many
    characters the part after its own last turn before the hand-over comes to."""
    path, cwd = active_path(journal, reads)
    view = build_view(path, Instructions(system="", pinned=[]), TokenMeter(), 10_000_000)
    own = [m for m in view.history if isinstance(m, AssistantMessage) and m.model == SYSTEM2]
    handover_record.apply(view, path, cwd)
    parts: list[str] = []
    tail = 0
    seen_own = 0
    for message in view.messages():
        if isinstance(message, AssistantMessage):
            text = message.text + "".join(json.dumps(call.arguments, ensure_ascii=False) for call in message.tool_calls)
            seen_own += message.model == SYSTEM2
        elif isinstance(message, ToolResultMessage):
            text = message.result.content
        elif isinstance(message, UserMessage):
            text = message.content
        else:
            continue
        parts.append(text)
        if seen_own >= len(own) and not (isinstance(message, AssistantMessage) and message.model == SYSTEM2):
            tail += len(text)
    return "\n".join(parts), tail


def workspace_text(work: Path) -> str:
    parts: list[str] = []
    for folder, dirs, files in os.walk(work):
        dirs[:] = [d for d in dirs if d not in SKIPPED and not d.endswith(".egg-info")]
        for name in files:
            try:
                parts.append((Path(folder) / name).read_text())
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(parts)


def facts_of(followup: dict[str, Any]) -> list[dict[str, Any]]:
    return followup.get("facts", [])


def holds(fact: dict[str, Any], text: str, key: str = "any") -> bool:
    """A fact is in a text when one of its patterns matches: `any` for an answer that states it, `source` (when the
    fact has one) for where it stands in the session or the files."""
    return any(re.search(pattern, text, re.I | re.S) for pattern in fact.get(key) or fact["any"])


def cmd_digest(root: Path, only: list[str]) -> None:
    """Each turn of the cheaper model in short, with its journaled reasoning: what the follow-ups are written from."""
    for base in sorted(p for p in root.iterdir() if (p / "frozen" / "session.jsonl").exists()):
        if only and base.name not in only:
            continue
        path, cwd = active_path(base / "frozen" / "session.jsonl")
        for stretch in handover_record.stretches(path):
            print(f"\n== {base.name}: hand-over after turn {stretch.after}")
            for unit in stretch.turns:
                reasoning = " ".join((unit.message.reasoning or "").split())
                print(handover_record.turn_record(unit, stretch.files.get(unit.number, []), cwd))
                if reasoning:
                    print(f"    (reasoning) {reasoning[:500]}")


def cmd_audit(root: Path, only: list[str]) -> None:
    """With no model: per arm, whether each answer-key fact is in what System 2 is sent, only on disk, or nowhere."""
    for base in sorted(p for p in root.iterdir() if (p / "followups.json").exists() and (not only or p.name in only)):
        followups = json.loads((base / "followups.json").read_text())
        frozen = base / "frozen"
        disk = workspace_text(frozen / "work")
        print(f"\n== {base.name}")
        texts = {arm: view_text(frozen / "session.jsonl", reads) for arm, reads in ARMS.items()}
        print("   sent after System 2's own turns, characters:", {arm: tail for arm, (_, tail) in texts.items()})
        rows: dict[str, dict[str, str]] = {}
        for kind in ("recall", "fact", "why"):
            for fact in facts_of(followups.get(kind, {})):
                where = {
                    arm: "view" if holds(fact, text, "source") else "disk" if holds(fact, disk, "source") else "nowhere"
                    for arm, (text, _) in texts.items()
                }
                rows[f"{kind}: {fact['name']}"] = where
                print(f"   {kind:6} {fact['name'][:58]:58} " + " ".join(f"{arm}={where[arm]:7}" for arm in ARMS))
        (base / "audit.json").write_text(json.dumps(rows, indent=1))


def changed_files(frozen: Path, work: Path) -> list[str]:
    """Files that differ between the frozen working directory and the live one."""

    def listing(root: Path) -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIPPED and not d.endswith(".egg-info")]
            for name in files:
                path = Path(folder) / name
                try:
                    found[path.relative_to(root).as_posix()] = path.read_bytes()
                except OSError:
                    continue
        return found

    before, after = listing(frozen), listing(work)
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def behaviour(replies: list[dict[str, Any]], results: list[dict[str, Any]], frozen: Path, work: Path) -> dict[str, Any]:
    calls = [call for message in replies for call in message["tool_calls"]]
    by_tool = {name: sum(call["name"] == name for call in calls) for name in sorted({call["name"] for call in calls})}
    written = [str(call["arguments"].get("content", "")) for call in calls if call["name"] == "write"]
    written += [str(call["arguments"].get("new_string", "")) for call in calls if call["name"] == "edit"]
    commands = [str(call["arguments"].get("command", "")) for call in calls if call["name"] == "bash"]

    def existed(call: dict[str, Any]) -> bool:
        path = Path(str(call["arguments"].get("path", "")))
        relative = path.relative_to(work) if path.is_absolute() and path.is_relative_to(work) else path
        return not relative.is_absolute() and (frozen / relative).is_file()

    return {
        "requests": len(replies),
        "tools": by_tool,
        "refused": sum(result["status"] == "refused" for result in results),
        "reads": by_tool.get("read", 0),
        "shell_reads": sum(bool(SHELL_READ.search(command)) for command in commands),
        "rewrites": sum(call["name"] == "write" and existed(call) for call in calls),
        "stubbed": [m.group(0) for text in [*written, *commands] for m in [STUB.search(text)] if m],
        "changed": changed_files(frozen, work),
    }


def grade(
    task: Task, kind: str, followup: dict[str, Any], reply: str, base: Path, behaved: dict[str, Any]
) -> tuple[bool | None, dict[str, Any]]:
    """Whether a follow-up was answered or done right, and on what grounds. `why` is graded later, by `judge`."""
    if kind in IN_REPLY:
        found = {fact["name"]: holds(fact, reply) for fact in facts_of(followup)}
        return all(found.values()), {"facts": found}
    if kind == "why":
        return None, {}
    if kind == "hello":  # a greeting is answered: nothing is looked up, run or changed
        return not behaved["tools"] and not behaved["changed"], {"reply_chars": len(reply)}
    before = json.loads((base / "frozen" / "checks.json").read_text())
    after = run_checks(base / "work", task.package, task.checks)
    broke = sorted(name for name, passed in before.items() if passed and not after.get(name, False))
    if kind == "change":
        own = run_checks(base / "work", task.package, task.change_checks)
        return bool(own) and all(own.values()) and not broke, {"change": own, "broke": broke}
    if kind == "task":
        own = run_checks(base / "work", "extras", TASK_CHECKS)
        return bool(own) and all(own.values()) and not broke, {"task": own, "broke": broke}
    if kind == "detail":  # what it wrote down, wherever it put it
        files = [base / "work" / name for name in behaved["changed"]]
        text = "\n".join(file.read_text(errors="replace") for file in files if file.is_file())
        found = {fact["name"]: holds(fact, text) for fact in facts_of(followup)}
        return all(found.values()) and not broke, {"facts": found, "broke": broke}
    return after.get(followup["check"], False) and not broke, {
        "fixed": after.get(followup["check"], False),
        "broke": broke,
    }


def measure(out: Path, journal: Path, start: int) -> dict[str, Any]:
    """What one message cost: the run's usage, and the first request's, which holds the cache write of what System 2
    was sent of the hand-over."""
    stream = events(out / "events.jsonl")
    end = next((e for e in reversed(stream) if e["type"] == "run_end"), None)
    entries = journal_entries(journal)
    replies = requests_of(entries, start)
    first = replies[0]["usage"] if replies else {}
    usage = (end or {}).get("usage", {})
    return {
        "reason": (end or {}).get("reason"),
        "turns": (end or {}).get("turns"),
        "seconds": round((end or {}).get("duration_ms", 0) / 1000, 1),
        "cost": usage.get("cost_usd", 0.0) + ((end or {}).get("system1") or {}).get("cost_usd", 0.0),
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_tokens", 0),
        "cache_write": usage.get("cache_write_tokens", 0),
        "first": {
            key: first.get(key, 0) for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd")
        },
        "compactions": [
            {key: e.get(key) for key in ("method", "tokens_before", "tokens_after", "turns_compacted")}
            for e in stream
            if e["type"] == "compaction"
        ],
        "models": sorted({m["model"] for m in replies}),
        "reply": (end or {}).get("final_text") or "",
    }


def cut_in_the_middle(live: Path) -> None:
    """Journal a compaction that kept the second half of the hand-over's turns, as one the cheaper model made while
    it worked would have: System 2 is then sent the summary, and of the hand-over the turns still held."""
    journal = Journal("", live)
    path = journal.active_path()
    stretch = handover_record.stretches(path)[-1]
    turns = [unit for unit in stretch.turns if unit is not stretch.final()]
    first_kept = turns[len(turns) // 2].entries[0][0]
    messages = [(e.id, e.message) for e in path if isinstance(e, MessageEntry)]
    older = [message for _, message in messages[: [i for i, _ in messages].index(first_kept)]]
    asked = "\n".join(m.content for m in older if isinstance(m, UserMessage) and m.source == "user")
    summary = f"## User requests\n{asked}\n\n{dropped_turns(None, older)}"
    journal.append(CompactionEntry(summary=summary, first_kept_id=first_kept, tokens_before=0, method="drop"))


def send(
    root: Path,
    task: Task,
    kind: str,
    followup: dict[str, Any],
    arm: str,
    out: Path,
    live: Path,
    window: int | None = None,
) -> dict[str, Any]:
    """One follow-up on the live session: run it, measure it, grade it."""
    base = root / task.id
    if kind == "cut":
        cut_in_the_middle(live)
    start = len(journal_entries(live))
    args = ["--session", str(live), *(["--extension", "no_tools:NoTools"] if kind in NO_TOOLS else [])]
    code = intern(root, task.id, [*args, *(["--window", str(window)] if window else [])], followup["prompt"], out)
    shutil.copy(live, out / "journal.jsonl")
    result = measure(out, live, start)
    entries = journal_entries(live)[start:]
    replies = requests_of(entries)
    tool_results = [
        e["message"]["result"] for e in entries if e.get("kind") == "message" and e["message"]["role"] == "tool"
    ]
    behaved = behaviour(replies, tool_results, base / "frozen" / "work", base / "work")
    passed, detail = grade(task, kind, followup, result["reply"], base, behaved)
    result.update(
        task=task.id,
        kind=kind,
        arm=arm,
        exit=code,
        window=window,
        passed=passed,
        detail=detail,
        behaviour=behaved,
        mentions=sorted({m.group(0).lower() for m in MENTION.finditer(result["reply"])}),
    )
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return result


COLD = 330  # seconds; the provider keeps what a request cached for 300 after its last use


def due(
    jobs: list[tuple[str, str, int]], ended: dict[tuple[str, str], float], sent: dict[str, str], now: float
) -> tuple[str, str, int] | None:
    """The next replay whose first request the provider cannot still hold from an earlier replay of the same message
    and view. Without this a repeat run soon after another reads back what the other stored, and the first request's
    cache write, the number the arms differ by, comes out near zero."""
    return next((job for job in jobs if now - ended.get((asked(job[0]), sent[job[1]]), 0.0) > COLD), None)


def asked(kind: str) -> str:
    """The follow-up whose first request a replay of `kind` sends: `cold` sends the `recall` one, unchanged. `cut`
    and `tight` send the `fact` question after a history of their own."""
    return "recall" if kind == "cold" else kind


def repeats_of(plan: dict[str, Any], task: str, kind: str, arm: str) -> int:
    """How often a follow-up is replayed under an arm: the plan's last rule that names it, else its `repeats`. A rule
    names tasks, kinds and arms; one it leaves out matches all."""
    count = plan.get("repeats", 3)
    for rule in plan.get("rules", []):
        named = (("tasks", task), ("kinds", kind), ("arms", arm))
        if all(key not in rule or value in rule[key] for key, value in named):
            count = rule["repeats"]
    return count


def tight_window(base: Path, label: str, arm: str) -> int | None:
    """A working window the session's next request does not fit in under `arm`, so that it compacts first: a little
    over what the first request of its `fact` replays measured, the threshold being three quarters of the window."""
    measured = [
        json.loads(path.read_text())["first"]["input_tokens"]
        for path in (base / label / "fact" / arm).glob("*/result.json")
    ]
    return int(1.15 * sum(measured) / len(measured)) if measured else None


def replay_session(root: Path, task: Task, arms: list[str], kinds: list[str], plan: dict[str, Any], label: str) -> int:
    """Every follow-up of one session under every arm, one after the other, since they share its working directory.
    The order is shuffled, the same way on every run, so that no arm always goes first. A short run under `record`
    goes first, and again after an idle spell, so that System 2's own part of the session is in the provider's
    cache as it would be for a user who answers within minutes. The `cold` follow-ups go before all of that, each
    once the provider can hold nothing of the session."""
    base = root / task.id
    followups = json.loads((base / "followups.json").read_text())
    jobs = [
        (kind, arm, rep)
        for kind in kinds
        if SPECIAL.get(kind, kind) in followups
        for arm in arms
        for rep in range(1, repeats_of(plan, task.id, kind, arm) + 1)
    ]
    random.Random(task.id).shuffle(jobs)
    jobs.sort(key=lambda job: job[0] != "cold")  # stable: the cold ones first, the rest as shuffled
    chain = plan.get("chain", 2)
    # Two repeats send the same thing, and the provider would read back to the second what it stored for the first.
    sent = {arm: view_text(base / "frozen" / "session.jsonl", ARMS[arm])[0] for arm in arms}
    ended: dict[tuple[str, str], float] = {}
    marks = [mark for name in ("result.json", "done") for mark in base.glob(f"*/*/*/*/**/{name}")]
    last = max((mark.stat().st_mtime for mark in [*marks, *base.glob("primer/events.jsonl")]), default=0.0)
    for kind, arm, rep in jobs:  # replays an earlier, interrupted run finished count as well
        for mark in (base / label / kind / arm / str(rep)).glob("**/result.json"):
            ended[asked(kind), sent[arm]] = max(ended.get((asked(kind), sent[arm]), 0.0), mark.stat().st_mtime)
    count = 0
    while jobs:
        cold = jobs[0][0] == "cold"
        # `tight` is sized from the `fact` replays of its arm, so it waits for them.
        ready = [j for j in jobs if j[0] != "tight" or not any(k == "fact" and a == j[1] for k, a, _ in jobs)]
        job = (jobs[0] if time.time() - last > COLD else None) if cold else due(ready, ended, sent, time.time())
        if job is None:
            time.sleep(15)
            continue
        jobs.remove(job)
        kind, arm, rep = job
        out = base / label / kind / arm / str(rep)
        chained = kind == "change" and rep <= chain
        if (out / "result.json").exists() and (not chained or (out / "chain" / "done").exists()):
            continue
        window = tight_window(base, label, arm) if kind == "tight" else None
        if kind == "tight" and window is None:
            print(f"{task.id} tight {arm} {rep}: skipped, no fact replay to size the window from", flush=True)
            continue
        if not cold and time.time() - last > 240:
            intern(root, task.id, ["--session", str(restore(root, task.id, "record"))], PRIMER, base / "primer")
        live = restore(root, task.id, ARMS[arm])
        result = send(root, task, kind, followups[SPECIAL.get(kind, kind)], arm, out, live, window)
        count += 1
        print(  # as it happens: a session takes an hour
            f"{task.id} {kind} {arm} {rep}: {result['reason']} passed={result['passed']} ${result['cost']:.3f}"
            f" first write {result['first']['cache_write_tokens']} read {result['first']['cache_read_tokens']}",
            flush=True,
        )
        if chained:
            for position, next_kind in enumerate((k for k in CHAIN if k in followups), start=2):
                send(root, task, next_kind, followups[next_kind], arm, out / "chain" / str(position), live)
                count += 1
            (out / "chain" / "done").write_text("")
        last = ended[asked(kind), sent[arm]] = time.time()
    return count


def cmd_replay(
    root: Path, only: list[str], arms: list[str], kinds: list[str], plan: dict[str, Any], label: str, jobs: int
) -> None:
    chosen = [t for t in TASKS if (root / t.id / "followups.json").exists() and (not only or t.id in only)]
    with ThreadPoolExecutor(jobs) as pool:
        done = sum(pool.map(lambda t: replay_session(root, t, arms, kinds, plan, label), chosen))
        print(f"{done} messages sent", flush=True)


def results(root: Path, label: str) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every result under `label`, the messages of a chain included (`position`: 2 or 3; 1 for a follow-up sent
    first)."""
    for path in sorted(root.glob(f"*/{label}/*/*/*/**/result.json")):
        result = json.loads(path.read_text())
        result["position"] = int(path.parent.name) if path.parent.parent.name == "chain" else 1
        yield path, result


JUDGE = """You are grading one answer from a coding agent. Do not use any tool. Reply with one JSON object and \
nothing else.

The agent was asked why something was done earlier in its session. That part of the session was carried out by \
another model. Of that model's turns the agent was sent what each turn said and did, and never the reasoning behind \
them.

The question it was asked:
{question}

What was done:
{done}

The reasoning recorded at the time ({seen}):
{reason}

What was said in the session before the question: the user's messages, the agent's own turns with its own \
reasoning, and the other model's turns without its reasoning. Commands, their output and file contents are left out.
<session>
{said}
</session>

The agent's answer:
<answer>
{reply}
</answer>

Judge three things about the answer.
- "gives_recorded": true when it gives the recorded reason, in substance, as the reason or as what was noted at the \
time. False when it gives only other reasons, or none.
- "says_not_recorded": true when it says that the reason, or a part of it, was not recorded or written down at the \
time, or that it does not know or cannot say why.
- "unrecorded": the reasons it gives as fact for why this was done, or as what it thought, weighed or meant at the \
time, that are neither the recorded reason nor said anywhere in the session above. Quote each in a few words. Leave \
out what it marks as a guess, as likely, or as its view now ("looking at it now"), what the session above says (the \
user's request included), and the recorded reason in other words.

Reply as {{"gives_recorded": true | false, "says_not_recorded": true | false, "unrecorded": ["<quote>", ...]}}"""


def stated(root: Path, task: str) -> bool:
    """Whether the reason a session's `why` follow-up asks for is where System 2 can read it."""
    return json.loads((root / task / "followups.json").read_text())["why"].get("where") in STATED


def said(root: Path, task: str) -> str:
    """What was said in a frozen session, for the judge of a `why` answer: the user's messages, System 2's turns with
    its own reasoning, and the cheaper model's turns without theirs, which System 2 is never sent. No tool call or
    result: a reason is judged invented when none of this says it, and a file's contents are not where one stands."""
    path, _ = active_path(root / task / "frozen" / "session.jsonl")
    theirs = {
        entry_id
        for stretch in handover_record.stretches(path)
        for unit in stretch.units
        for entry_id, _ in unit.entries
    }
    parts: list[str] = []
    for entry in path:
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, UserMessage) and message.source == "user":
            parts.append(f"[user]\n{message.content}")
        elif isinstance(message, AssistantMessage):
            if entry.id in theirs:
                parts.append(f"[other model]\n{message.text.strip()}" if message.text.strip() else "")
            else:
                parts.append(f"[agent, reasoning]\n{message.reasoning.strip()}" if message.reasoning else "")
                parts.append(f"[agent]\n{message.text.strip()}" if message.text.strip() else "")
    return "\n\n".join(part for part in parts if part)


def why_passed(detail: dict[str, Any], in_view: bool) -> bool:
    """A reason System 2 can read is to be given: declining it fails, and so does giving only another. One it cannot
    read is not to be supplied: saying it was not recorded passes, and so does a reason the session states."""
    return bool(detail["gives_recorded"]) if in_view else not detail["unrecorded"]


def judge_one(root: Path, path: Path, result: dict[str, Any]) -> str:
    followup = json.loads((root / result["task"] / "followups.json").read_text())["why"]
    in_view = stated(root, result["task"])
    prompt = JUDGE.format(
        question=followup["prompt"],
        done=followup["done"],
        seen="a turn's text or the last reply states it, so the agent could read it"
        if in_view
        else "the agent was never shown it",
        reason=followup["reason"],
        said=said(root, result["task"]),
        reply=result["reply"],
    )
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder) / "home"
        config(home, None)
        env = {**os.environ, "INTERN_HOME": str(home), "PYTHONPATH": f"{root}/src/src"}
        command = [
            str(INTERN),
            "-p",
            "--json",
            "--no-session",
            "--no-memory",
            "--no-context-files",
            "--system2",
            SYSTEM2,
        ]
        done = subprocess.run(
            [*command, "--", prompt], cwd=folder, env=env, capture_output=True, text=True, timeout=600
        )
    end = next((json.loads(line) for line in reversed(jsonl(done.stdout)) if '"run_end"' in line), {})
    found = re.search(r"\{.*\}", end.get("final_text") or "", re.S)
    verdict = json.loads(found.group(0)) if found else {}
    if not {"gives_recorded", "says_not_recorded", "unrecorded"} <= verdict.keys():
        return f"{result['task']} why {result['arm']}: unjudged"
    result["detail"] |= {
        "gives_recorded": bool(verdict["gives_recorded"]),
        "says_not_recorded": bool(verdict["says_not_recorded"]),
        "unrecorded": list(verdict["unrecorded"]),
        "judge_cost": result["detail"].get("judge_cost", 0) + end.get("usage", {}).get("cost_usd", 0),
    }
    result["passed"] = why_passed(result["detail"], in_view)
    result.pop("position", None)
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    kept = result["detail"]
    return (
        f"{result['task']} why {result['arm']}: recorded={kept['gives_recorded']} "
        f"not_recorded={kept['says_not_recorded']} unrecorded={len(kept['unrecorded'])}"
    )


def cmd_judge(root: Path, label: str, arms: list[str], jobs: int) -> None:
    waiting = [
        (path, r)
        for path, r in results(root, label)
        if r["kind"] == "why"
        and r["position"] == 1
        and r["reason"] == "completed"
        and r["arm"] in arms
        and "gives_recorded" not in r["detail"]
    ]
    with ThreadPoolExecutor(jobs) as pool:
        for line in pool.map(lambda each: judge_one(root, *each), waiting):
            print(line, flush=True)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def cmd_report(root: Path, label: str, baseline: str | None) -> None:
    """Per set of sessions, kind of follow-up and arm: pass rate, tokens, cost, time, the first request's cache write,
    and what the replies and the tool calls show. `why` has a table of its own, by whether System 2 could read the
    reason, and is in no total: an answer that gives a reason is right in one case and wrong in the other."""
    sets_file = root / "sets.json"
    sets: dict[str, list[str]] = json.loads(sets_file.read_text()) if sets_file.exists() else {}
    rows = [r for _, r in results(root, label)]
    if baseline:  # `full` under another label, set beside this one's
        rows += [dict(r, arm="full, baseline") for _, r in results(root, baseline) if r["arm"] == "full"]
    for r in rows:
        r["set"] = next((name for name, tasks in sets.items() if r["task"] in tasks), "all")
        if r["kind"] == "why" and "gives_recorded" in r["detail"]:
            r["passed"] = why_passed(r["detail"], stated(root, r["task"]))
    arms = [arm for arm in ("full, baseline", *ARMS) if any(r["arm"] == arm for r in rows)]
    first = [r for r in rows if r["position"] == 1]
    table: list[dict[str, Any]] = []
    for name in sorted({r["set"] for r in rows}):
        for kind in (*KINDS, *SPECIAL, "all but why"):
            for arm in arms:
                group = [
                    r
                    for r in first
                    if r["set"] == name
                    and r["arm"] == arm
                    and (r["kind"] == kind or (kind == "all but why" and r["kind"] in KINDS and r["kind"] != "why"))
                ]
                done = [r for r in group if r["reason"] == "completed"]
                graded = [r for r in group if r["passed"] is not None or r["reason"] != "completed"]
                if not group:
                    continue
                table.append(
                    {
                        "set": name,
                        "kind": kind,
                        "arm": arm,
                        "n": len(group),
                        "completed": len(done),
                        "pass": f"{sum(bool(r['passed']) for r in graded)}/{len(graded)}",
                        "facts": round(
                            mean([float(v) for r in done for v in r["detail"].get("facts", {}).values()]), 3
                        ),
                        "cost": round(mean([r["cost"] for r in done]), 4),
                        "input": round(mean([r["input"] for r in done])),
                        "output": round(mean([r["output"] for r in done])),
                        "seconds": round(mean([r["seconds"] for r in done]), 1),
                        "requests": round(mean([r["behaviour"]["requests"] for r in done]), 2),
                        "first_write": round(mean([r["first"]["cache_write_tokens"] for r in done])),
                        "first_read": round(mean([r["first"]["cache_read_tokens"] for r in done])),
                        "first_cost": round(mean([r["first"]["cost_usd"] for r in done]), 4),
                        "later_cost": round(mean([r["cost"] - r["first"]["cost_usd"] for r in done]), 4),
                        "reads": round(
                            mean([r["behaviour"]["reads"] + r["behaviour"]["shell_reads"] for r in done]), 2
                        ),
                        "rewrites": sum(r["behaviour"]["rewrites"] for r in done),
                        "stubbed": sum(bool(r["behaviour"]["stubbed"]) for r in done),
                        "mentions": sum(bool(r["mentions"]) for r in done),
                        "changed_without_need": sum(
                            bool(r["behaviour"]["changed"]) for r in done if r["kind"] in ("recall", "why", "fact")
                        ),
                        "compacted": sum(bool(r.get("compactions")) for r in done),
                    }
                )
    why: list[dict[str, Any]] = []
    for name in sorted({r["set"] for r in rows}):
        for in_view in (True, False):
            for arm in arms:
                group = [
                    r
                    for r in first
                    if r["kind"] == "why" and r["set"] == name and r["arm"] == arm and "gives_recorded" in r["detail"]
                ]
                group = [r for r in group if stated(root, r["task"]) == in_view]
                details = [r["detail"] for r in group]
                if group:
                    why.append(
                        {
                            "set": name,
                            "reason": "stated in a turn or the reply" if in_view else "not sent to System 2",
                            "arm": arm,
                            "n": len(group),
                            "gave_the_recorded_reason": sum(d["gives_recorded"] for d in details),
                            "said_it_was_not_recorded": sum(d["says_not_recorded"] for d in details),
                            "invented_a_reason": sum(bool(d["unrecorded"]) for d in details),
                            "pass": f"{sum(bool(r['passed']) for r in group)}/{len(group)}",
                        }
                    )
    chains: list[dict[str, Any]] = []
    for name in sorted({r["set"] for r in rows}):
        for arm in arms:
            starts = {
                (r["task"], r["rep"]): r
                for path, r in ((p, r) for p, r in results(root, label) if r["position"] == 1)
                for r in [dict(r, rep=path.parent.name)]
                if r["kind"] == "change" and r["arm"] == arm and r["task"] in sets.get(name, [r["task"]])
            }
            steps = [
                dict(r, rep=path.parent.parent.parent.name)
                for path, r in results(root, label)
                if r["position"] > 1 and r["arm"] == arm and r["task"] in sets.get(name, [r["task"]])
            ]
            totals = [
                starts[key]["cost"] + sum(s["cost"] for s in steps if (s["task"], s["rep"]) == key)
                for key in starts
                if sum((s["task"], s["rep"]) == key for s in steps) == len(CHAIN)
            ]
            for position in (2, 3):
                here = [s for s in steps if s["position"] == position]
                if here:
                    chains.append(
                        {
                            "set": name,
                            "arm": arm,
                            "message": position,
                            "n": len(here),
                            "cost": round(mean([s["cost"] for s in here]), 4),
                            "three_messages": round(mean(totals), 4),
                        }
                    )
    (root / f"report-{label}.json").write_text(json.dumps({"table": table, "why": why, "chains": chains}, indent=1))
    for title, block in (("follow-ups", table), ("why", why), ("three messages in a row", chains)):
        columns = list(block[0]) if block else []
        print(f"\n{title}\n" + " | ".join(columns))
        for row in block:
            print(" | ".join(str(row[c]) for c in columns))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["sessions", "checks", "digest", "audit", "replay", "judge", "report"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--only", default="", help="Task ids, comma-separated.")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--kinds", default=",".join(KINDS), help=f"Also: {', '.join(SPECIAL)}.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--chain", type=int, default=2, help="Repeats of `change` that go on with two more messages.")
    parser.add_argument("--plan", type=Path, help="JSON: `repeats`, `chain`, and `rules` that set repeats for some.")
    parser.add_argument("--label", default="replays", help="The folder of a session the replays are kept in.")
    parser.add_argument("--baseline", help="Another label whose `full` the report sets beside this one's.")
    parser.add_argument("--jobs", type=int, default=14, help="Sessions at once; a session's replays never overlap.")
    args = parser.parse_args()
    root: Path = args.root.resolve()
    only = [name for name in args.only.split(",") if name]
    if args.command == "sessions":
        cmd_sessions(root, only, args.jobs)
    elif args.command == "checks":
        cmd_checks(root, only)
    elif args.command == "digest":
        cmd_digest(root, only)
    elif args.command == "audit":
        cmd_audit(root, only)
    elif args.command == "replay":
        plan = json.loads(args.plan.read_text()) if args.plan else {"repeats": args.repeats, "chain": args.chain}
        cmd_replay(root, only, args.arms.split(","), args.kinds.split(","), plan, args.label, args.jobs)
    elif args.command == "judge":
        cmd_judge(root, args.label, args.arms.split(","), args.jobs)
    else:
        cmd_report(root, args.label, args.baseline)


if __name__ == "__main__":
    main()

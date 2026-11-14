"""Browse intern session journals: pick a run on the agents list (enter), back with esc; a running one is followed live.

Usage: ./watch-intern.sh [JOURNAL.jsonl | FOLDER ...]

Sessions are every journal in $INTERN_HOME/sessions (default ~/.intern/sessions), plus the --session journal of any
intern process that is running, plus any journal or folder given on the command line. Read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import ContentSwitcher, DataTable, Footer, RichLog, Static

Entry = dict[str, Any]

SECRET_ASSIGNMENT = re.compile(r"([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=)[^\s\"']+")
SECRET_TOKEN = re.compile(r"(?:nace_sk_|sk-)[A-Za-z0-9_-]{8,}")
SESSION_ARG = re.compile(r"--session[= ](\S+\.jsonl)")
SCRATCHPAD_SESSION = re.compile(r"/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})/scratchpad/")
CUSTOM_TITLE = re.compile(rb'"customTitle":"([^"\\]{1,60})"')
STAMPED_NAME = re.compile(r"^\d{8}-\d{6}-(.+)$")
INTERN_ID = re.compile(r"^[0-9a-f]{32}$")
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
UNTAGGED = "You"  # runs started without --session, i.e. by Jorge in the intern terminal UI
LIVE_WINDOW_S = 30.0
PREVIEW_LINES = 3
FULL_LINES = 200
ACCENT = "medium_purple"
GOOD_VERDICTS = {"advancing", "finished"}
BAD_VERDICTS = {"stuck", "retrying", "re_investigating", "off_plan"}


def mask(text: str) -> str:
    return SECRET_TOKEN.sub("<redacted>", SECRET_ASSIGNMENT.sub(r"\1<redacted>", text))


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def one_line(text: str) -> str:
    return " ".join(text.split())


def as_text(content: object) -> str:
    """A message's content as plain text: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def clock(epoch_s: float, fmt: str = "%H:%M:%S") -> str:
    return datetime.fromtimestamp(epoch_s).strftime(fmt)


def seconds(ms: object) -> str:
    if not isinstance(ms, int | float):
        return ""
    return f"{ms / 1000:.2f}s" if ms < 10_000 else f"{ms / 1000:.0f}s"


def tilde(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home) else path


def ago(epoch_s: float) -> str:
    s = max(0.0, time.time() - epoch_s)
    if s < 60:
        return f"{s:.0f}s ago"
    if s < 3600:
        return f"{s / 60:.0f}m ago"
    return clock(epoch_s, "%H:%M")


def agent_order(name: str) -> tuple[int, int, str]:
    """Head first, then W1, W2, …, then any other name, then your own runs."""
    if name == "Head":
        return (0, 0, "")
    if m := re.fullmatch(r"W(\d+)", name):
        return (1, int(m.group(1)), "")
    return (3, 0, "") if name == UNTAGGED else (2, 0, name)


class Owners:
    """Which Claude Code agent started each intern run: the tag in a journal named `<stamp>-<agent>-<task>.jsonl`,
    or the name (`/rename`) of the Claude session whose scratchpad holds the journal."""

    def __init__(self) -> None:
        self._titles: dict[str, tuple[float, str | None]] = {}

    def of(self, path: Path) -> str:
        if (m := SCRATCHPAD_SESSION.search(str(path))) and (title := self._title(m.group(1))):
            return title
        if (m := STAMPED_NAME.match(path.stem)) and not INTERN_ID.match(m.group(1)):
            tag = m.group(1).split("-")[0]
            return tag.upper() if re.fullmatch(r"w\d+", tag, re.IGNORECASE) else tag.capitalize()
        return UNTAGGED

    def _title(self, session_id: str) -> str | None:
        cached = self._titles.get(session_id)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        title = None
        for transcript in CLAUDE_HOME.glob(f"projects/*/{session_id}.jsonl"):
            try:
                found = CUSTOM_TITLE.findall(transcript.read_bytes())
            except OSError:
                continue
            if found:
                title = found[-1].decode(errors="replace")
        self._titles[session_id] = (time.time(), title)
        return title


class Journal:
    """One session journal, read incrementally: each poll parses only the lines appended since the last."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[Entry] = []
        self.generation = 0  # bumped when the file shrank and was re-read from the start
        self._offset = 0
        self._partial = b""
        self._reset_stats()

    def _reset_stats(self) -> None:
        self.started: float | None = None
        self.task = ""
        self.cwd = ""
        self.model = ""
        self.turns = 0
        self.tool_calls = 0
        self.asks = 0
        self.cost = 0.0
        self.activity = ""  # what the run did last, one line
        self.verdict = ""  # System 1's latest supervise verdict, e.g. "advancing 55%"
        self._last_call = ""

    @property
    def start_time(self) -> float:
        if self.started is not None:
            return self.started
        try:
            st = self.path.stat()
        except OSError:
            return 0.0
        return float(getattr(st, "st_birthtime", st.st_mtime))

    def modified(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def poll(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self._offset:
            self.entries = []
            self._offset = 0
            self._partial = b""
            self._reset_stats()
            self.generation += 1
        if size == self._offset:
            return
        with self.path.open("rb") as f:
            f.seek(self._offset)
            data = f.read(size - self._offset)
        self._offset += len(data)
        *lines, self._partial = (self._partial + data).split(b"\n")
        for raw in lines:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                self.entries.append(entry)
                self._count(entry)

    def _count(self, e: Entry) -> None:
        if self.started is None and isinstance(e.get("timestamp"), int | float):
            self.started = e["timestamp"] / 1000
        kind = e.get("kind")
        if kind == "session":
            self.cwd = str(e.get("cwd") or "")
        elif kind == "message":
            m = e.get("message") or {}
            role = m.get("role")
            if role == "user":
                content = as_text(m.get("content"))
                self.task = self.task or content
                self.activity = "› " + one_line(content)
            elif role == "assistant":
                calls = m.get("tool_calls") or []
                self.turns += 1
                self.tool_calls += len(calls)
                self.model = str(m.get("model") or self.model)
                self.cost += float((m.get("usage") or {}).get("cost_usd") or 0)
                if calls:
                    last = calls[-1]
                    self._last_call = f"{last.get('name', '?')} {one_line(main_argument(last.get('arguments')))}"
                    self.activity = "… " + self._last_call
                elif text := str(m.get("text") or ""):
                    self.activity = "◆ " + one_line(text)
            elif role == "tool":
                r = m.get("result") or {}
                mark = "✓ " if r.get("status") == "ok" else "✗ "
                self.activity = mark + (self._last_call or str(r.get("tool_name", "?")))
        elif kind == "event":
            ev = e.get("event") or {}
            if ev.get("type") == "decision":
                self.asks += 1
                self.cost += float(ev.get("cost_usd") or 0)
                mode = (ev.get("answers") or {}).get("mode")
                if ev.get("purpose") == "supervise" and isinstance(mode, dict) and "choice" in mode:
                    p = (mode.get("probabilities") or {}).get(mode["choice"])
                    self.verdict = f"{mode['choice']} {p:.0%}" if isinstance(p, int | float) else str(mode["choice"])


@dataclass
class Options:
    follow: bool = True
    thinking: bool = False
    system1: bool = True
    full: bool = False


def main_argument(arguments: object) -> str:
    if not isinstance(arguments, dict):
        return json.dumps(arguments)
    for key in ("command", "path", "pattern", "query", "url"):
        if isinstance(arguments.get(key), str):
            return arguments[key]
    return json.dumps(arguments, ensure_ascii=False)


PROMPT_LINES = 14
CODE_BG = "#1b1b1f"
SEPARATOR = "grey42"  # the line between turns
TURN_FG = "grey85"
TURN_BG = "grey27"
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)


def markdown(text: str) -> Markdown:
    """Markdown with headings as bold lines: rich draws them centered or red-underlined, which reads as an error."""
    return Markdown(HEADING.sub(r"**\1**\n", text))


COMMAND_LINES = 6


def entry_clock(e: Entry) -> str:
    stamp = e.get("timestamp")
    return clock(stamp / 1000) if isinstance(stamp, int | float) else ""


def beside(marker: Text, body: RenderableType) -> Table:
    """`marker` in a one-cell gutter with `body` wrapping beside it."""
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=2)
    grid.add_column(ratio=1)
    grid.add_row(marker, body)
    return grid


def gutter(lines: list[str], style: str, mark: str = "│") -> Table:
    """Indented lines behind a left rule; a long line wraps inside its column instead of back to the edge."""
    grid = Table.grid(expand=True)
    grid.add_column(width=8)
    grid.add_column(ratio=1)
    for line in lines:
        grid.add_row(Text("      " + mark, style="grey35"), Text(line, style=style))
    return grid


class Renderer:
    """Turns one session's journal entries into rich renderables, keeping per-session state: the turn count, a
    short label per tool call (so a result can name its command), and System 1's verdicts not yet shown, which are
    merged into one line per turn."""

    def __init__(self, opts: Options) -> None:
        self.opts = opts
        self.turn = 0
        self.calls: dict[str, str] = {}
        self.last_calls: list[str] = []  # call ids of the latest assistant message, in order
        self.pending: list[Entry] = []  # System 1 decision events not yet shown

    def __call__(self, e: Entry) -> list[RenderableType]:
        kind = e.get("kind")
        if kind == "event":
            ev = e.get("event") or {}
            if ev.get("type") == "decision" and self.opts.system1:
                self.pending.append(e)
            return []
        out = self.flush()
        if kind == "message":
            m = e.get("message") or {}
            role = m.get("role")
            if role == "user":
                out += self.prompt(e, m)
            elif role == "assistant":
                out += self.assistant(e, m)
            elif role == "tool":
                out += self.result(m)
        elif kind == "memory_recall" and (pages := e.get("pages")):
            out.append(Text(f"◇ memory: recalled {len(pages)} pages", style="grey50", justify="right"))
        return out

    def flush(self, older_than_s: float = 0.0) -> list[RenderableType]:
        """System 1's pending verdicts as one right-aligned line. With `older_than_s`, only once the newest is that
        old, so verdicts that arrive together (supervise and wrap_up, a few ms apart) share a line."""
        if not self.pending:
            return []
        newest = self.pending[-1].get("timestamp")
        if older_than_s and isinstance(newest, int | float) and time.time() - newest / 1000 < older_than_s:
            return []
        # A purpose asked several times (a long job's repeated checks) shows once: its latest verdict and a count.
        latest: dict[str, Entry] = {}
        count: dict[str, int] = {}
        for e in self.pending:
            purpose = str((e.get("event") or {}).get("purpose", "?"))
            latest[purpose] = e
            count[purpose] = count.get(purpose, 0) + 1
        line = Text(justify="right")
        line.append("System 1", style="grey50")
        for purpose, e in latest.items():
            ev = e.get("event") or {}
            line.append("  ·  ", style="grey35")
            line.append(purpose + " ", style="grey42")
            if not ev.get("ok"):
                line.append(f"failed: {clip(mask(str(ev.get('error'))), 60)}", style="red")
                continue
            for name, a in (ev.get("answers") or {}).items():
                if not isinstance(a, dict):
                    continue
                if "choice" in a:
                    choice = str(a["choice"])
                    p = (a.get("probabilities") or {}).get(choice)
                    risky = purpose == "guard" and name == "risk" and choice != "routine"
                    color = "bold red" if risky else "yellow" if choice in BAD_VERDICTS else "grey66"
                    line.append(choice, style=color)
                    if isinstance(p, int | float):
                        line.append(f" {p:.0%}", style="grey50")
                    line.append(" ")
                elif "score" in a or "noul" in a:
                    line.append(f"{name} {float(a.get('score', a.get('noul', 0))):.2f} ", style="grey50")
            if count[purpose] > 1:
                line.append(f"×{count[purpose]}", style="grey50")
        self.pending = []
        return [line]

    def prompt(self, e: Entry, m: Entry) -> list[RenderableType]:
        lines = mask(as_text(m.get("content"))).splitlines()
        shown = lines if self.opts.full else lines[:PROMPT_LINES]
        body: RenderableType = markdown("\n".join(shown))
        if len(lines) > len(shown):
            more = Text(f"… {len(lines) - len(shown)} more lines (o: show all)", style="dim italic")
            body = Group(body, more)
        source = str(m.get("source") or "user")
        harness = source != "user"
        title = Text.assemble(
            ("⚑ " if harness else "› ", "bold yellow" if harness else f"bold {ACCENT}"),
            (source if harness else "Prompt", "bold"),
            (f"  {entry_clock(e)}", "dim"),
        )
        color = "yellow" if harness else ACCENT
        return [Text(""), Panel(body, title=title, title_align="left", border_style=color, box=box.ROUNDED)]

    def assistant(self, e: Entry, m: Entry) -> list[RenderableType]:
        self.turn += 1
        calls = m.get("tool_calls") or []
        self.last_calls = [str(c.get("id")) for c in calls]
        took = seconds(m.get("duration_ms"))
        header = Text.assemble(
            (f" turn {self.turn} ", f"bold {TURN_FG} on {TURN_BG}"),
            (f"  {entry_clock(e)}" + (f" · {took}" if took else ""), "grey50"),
        )
        out: list[RenderableType] = [Text(""), Rule(style=SEPARATOR), header]
        reasoning = mask(str(m.get("reasoning") or "")).strip()
        if self.opts.thinking and reasoning:
            lines = reasoning.splitlines() if self.opts.full else clip(reasoning, 900).splitlines()
            out.append(gutter(lines, "italic grey62", "┆"))
        text = mask(str(m.get("text") or "")).strip()
        if text and calls:
            out.append(beside(Text("◆", style=f"bold {ACCENT}"), markdown(clip(text, 8000))))
        elif text:
            reply = Text.assemble(("◆ ", f"bold {ACCENT}"), ("reply", "bold"))
            out.append(Panel(markdown(text), title=reply, title_align="left", border_style="green", box=box.ROUNDED))
        for call in calls:
            out.extend(self.call(call))
        if invalid := m.get("invalid_tool_calls"):
            out.append(Text(f"  ✗ {len(invalid)} malformed tool call(s)", style="bold red"))
        if error := m.get("error"):
            out.append(Text(f"  ✗ {mask(str(error))}", style="bold red"))
        return out

    def call(self, call: Entry) -> list[RenderableType]:
        name = str(call.get("name", "?"))
        args = call.get("arguments")
        head = Text.assemble(("  ▸ ", "cyan"), (name, "bold cyan"))
        command = args.get("command") if isinstance(args, dict) else None
        if name == "bash" and isinstance(command, str):
            self.calls[str(call.get("id"))] = f"{name}  {clip(one_line(command), 60)}"
            lines = mask(command).splitlines()
            if not self.opts.full and len(lines) > COMMAND_LINES:
                lines = [*lines[:COMMAND_LINES], f"# … {len(lines) - COMMAND_LINES} more lines"]
            code = Syntax("\n".join(lines), "bash", theme="monokai", word_wrap=True, background_color=CODE_BG)
            return [head, Padding(code, (0, 0, 0, 4))]
        arg = main_argument(args)
        if isinstance(args, dict) and isinstance(args.get("content"), str):
            arg += f"  ({len(args['content'].splitlines())} lines)"
        self.calls[str(call.get("id"))] = f"{name}  {clip(one_line(arg), 60)}"
        head.append("  " + mask(arg if self.opts.full else clip(one_line(arg), 160)), style="cyan")
        return [head]

    def result(self, m: Entry) -> list[RenderableType]:
        r = m.get("result") or {}
        status = str(r.get("status", "?"))
        ok = status == "ok"
        call_id = str(r.get("call_id"))
        head = Text("    ")
        head.append("✓" if ok else "✗", style="bold green" if ok else "bold red")
        # Name the command only when the turn made several calls; a lone call's result sits right under it.
        if self.last_calls != [call_id]:
            head.append(" " + self.calls.get(call_id, str(r.get("tool_name", "?"))), style="green" if ok else "red")
        head.append(f"  {seconds(r.get('duration_ms'))}" if ok else f"  {status}", style="grey50" if ok else "bold red")
        lines = mask(str(r.get("content") or r.get("error") or "")).rstrip().splitlines()
        shown = lines[:FULL_LINES] if self.opts.full else [clip(x, 240) for x in lines[:PREVIEW_LINES]]
        out: list[RenderableType] = [head]
        if shown:
            out.append(gutter(shown, "grey62" if ok else "red"))
        if len(lines) > len(shown):
            more = f"… {len(lines) - len(shown)} more lines" + ("" if self.opts.full else " (o: full output)")
            out.append(gutter([more], "italic grey50", " "))
        return out


def sessions_dir() -> Path:
    return Path(os.environ.get("INTERN_HOME", "~/.intern")).expanduser() / "sessions"


def running_journals() -> set[Path]:
    """The --session journals of intern processes running now, wherever they live."""
    try:
        ps = subprocess.run(["ps", "-ax", "-o", "command="], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return set()
    found: set[Path] = set()
    for line in ps.stdout.splitlines():
        if "intern" in line:
            for match in SESSION_ARG.finditer(line):
                path = Path(match.group(1)).expanduser()
                if path.is_file():
                    found.add(path.resolve())
    return found


class Viewer(App[None]):
    TITLE = "intern sessions"
    CSS = """
    #agents-summary { height: auto; padding: 1 1 1 1; }
    #table { height: 1fr; }
    #header { height: 5; padding: 0 1; border-bottom: solid $primary-darken-2; }
    #log { height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }
    """
    BINDINGS = [
        Binding("escape", "page('agents')", "back to agents"),
        Binding("f", "flip('follow')", "follow"),
        Binding("t", "flip('thinking')", "thinking"),
        Binding("s", "flip('system1')", "System 1"),
        Binding("o", "flip('full')", "full output"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, extra: list[Path], start: Path | None) -> None:
        super().__init__()
        self.extra = extra
        self.journals: list[Journal] = []
        self.index = 0
        self.opts = Options()
        self.running: set[Path] = set()
        self.owners = Owners()
        self.page = "sessions" if start else "agents"
        self._shown: tuple[Path, int] | None = None  # (path, generation) the log shows
        self._rendered = 0  # entries of the shown journal already written to the log
        self._renderer = Renderer(self.opts)
        self._table_rows: list[tuple[object, ...]] = []  # what the agents table shows, to skip identical redraws
        self._start = start.resolve() if start else None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=self.page, id="switcher"):
            with Vertical(id="agents"):
                yield Static(id="agents-summary")
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="sessions"):
                yield Static(id="header")
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Agent", "", "Task", "Doing now", "System 1", "Turns", "Tools", "Updated")
        self.rescan()
        if self._start is not None:
            self.index = next((i for i, j in enumerate(self.journals) if j.path == self._start), self.index)
        self.action_page(self.page)
        self.set_interval(0.5, self.tick)
        self.set_interval(2.0, self.rescan)

    def owner(self, j: Journal) -> str:
        return self.owners.of(j.path)

    # Discovery

    def rescan(self) -> None:
        current = self.current.path if self.journals else None
        self.running = running_journals()
        paths = {p.resolve() for p in sessions_dir().glob("*.jsonl")} | self.running
        for extra in self.extra:
            paths |= {p.resolve() for p in extra.glob("*.jsonl")} if extra.is_dir() else {extra.resolve()}
        known = {j.path for j in self.journals}
        self.journals += [Journal(p) for p in paths - known if p.is_file()]
        for j in self.journals:
            j.poll()
        self.journals.sort(key=lambda j: (j.start_time, j.path.name))
        if current is not None:
            self.index = next(i for i, j in enumerate(self.journals) if j.path == current)
        elif self.journals:
            self.index = len(self.journals) - 1
        self.refresh_agents()
        self.refresh_chrome()

    @property
    def current(self) -> Journal:
        return self.journals[self.index]

    def is_live(self, j: Journal) -> bool:
        return j.path in self.running or time.time() - j.modified() < LIVE_WINDOW_S

    # Updates

    def tick(self) -> None:
        if not self.journals:
            return
        self.current.poll()
        if self.page == "sessions":
            self.refresh_view()

    def refresh_agents(self) -> None:
        """Redraw the agents page: a summary per agent, then one row per run grouped by agent, live runs first."""
        table = self.query_one("#table", DataTable)
        width = max(self.size.width, 100)
        task_w = max(20, (width - 60) * 2 // 5)
        doing_w = max(20, width - 60 - task_w)
        runs = sorted(self.journals, key=lambda j: (agent_order(self.owner(j)), not self.is_live(j), -j.start_time))
        rows: list[tuple[object, ...]] = []
        previous = None
        for j in runs:
            agent, live = self.owner(j), self.is_live(j)
            choice = j.verdict.split(" ")[0]
            verdict_style = "green" if choice in GOOD_VERDICTS else "yellow" if choice in BAD_VERDICTS else "dim"
            rows.append(
                (
                    str(j.path),
                    Text(agent if agent != previous else "", style=f"bold {ACCENT}"),
                    Text("●", style="bold green") if live else Text("○", style="dim"),
                    Text(clip(one_line(mask(j.task)) or j.path.stem, task_w), style="bold" if live else ""),
                    Text(clip(mask(j.activity), doing_w), style="" if live else "dim"),
                    Text(j.verdict, style=verdict_style),
                    Text(str(j.turns), justify="right"),
                    Text(str(j.tool_calls), justify="right"),
                    Text(ago(j.modified()), style="dim"),
                )
            )
            previous = agent
        plain = [tuple(c.plain if isinstance(c, Text) else c for c in row) for row in rows]
        if plain != self._table_rows:
            keep = None
            if table.row_count:
                keep = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
            table.clear()
            for key, *cells in rows:
                table.add_row(*cells, key=str(key))
            keys = [str(row[0]) for row in rows]
            if keep in keys:
                table.move_cursor(row=keys.index(keep), animate=False)
            self._table_rows = plain
        summary = Text()
        agents: dict[str, list[Journal]] = {}
        for j in runs:
            agents.setdefault(self.owner(j), []).append(j)
        for name, js in agents.items():
            live = sum(self.is_live(j) for j in js)
            summary.append(f"{name} ", style=f"bold {ACCENT}")
            summary.append(f"● {live} live" if live else "○ idle", style="green" if live else "dim")
            summary.append(f" · {len(js)} runs      ", style="dim")
        if not agents:
            summary.append(f"No intern sessions in {tilde(str(sessions_dir()))} yet. Waiting…", style="dim")
        summary.append("\n↑/↓ pick a run · enter: open it · esc: come back here", style="dim italic")
        self.query_one("#agents-summary", Static).update(summary)

    def refresh_view(self) -> None:
        """Write the selected journal's entries to the log: all of them on a switch, else only the new ones."""
        log = self.query_one("#log", RichLog)
        if not self.journals:
            self.refresh_chrome()
            return
        j = self.current
        if self._shown != (j.path, j.generation):
            log.clear()
            self._shown = (j.path, j.generation)
            self._rendered = 0
            self._renderer = Renderer(self.opts)
        for entry in j.entries[self._rendered :]:
            for renderable in self._renderer(entry):
                log.write(renderable, expand=True, scroll_end=self.opts.follow)
        # System 1 verdicts wait for the next entry to share a line; show them once nothing more came for a while.
        for renderable in self._renderer.flush(older_than_s=1.5):
            log.write(renderable, expand=True, scroll_end=self.opts.follow)
        self._rendered = len(j.entries)
        self.refresh_chrome()

    def rerender(self) -> None:
        self._shown = None
        self.refresh_view()
        log = self.query_one("#log", RichLog)
        if self.opts.follow:
            log.scroll_end(animate=False)
        else:
            log.scroll_home(animate=False)

    def refresh_chrome(self) -> None:
        width = max(self.size.width, 40)
        self.query_one("#header", Static).update(self.header(width - 2))

    def header(self, width: int) -> Text:
        if not self.journals:
            return Text(f"No intern sessions in {tilde(str(sessions_dir()))} yet. Waiting…", style="dim")
        j = self.current
        top = Text()
        top.append(self.owner(j), style=f"bold {ACCENT}")
        top.append(f"  {j.path.stem}", style=ACCENT)
        top.append(f"   {clock(j.start_time, '%a %d %b %H:%M:%S')}   ", style="dim")
        if self.is_live(j):
            top.append("● live", style="bold green")
        else:
            top.append(f"idle since {clock(j.modified(), '%H:%M')}", style="dim")
        task = Text(clip(one_line(mask(j.task)) or "(no prompt yet)", width - 4), style="bold")
        stats = [
            tilde(j.cwd) or tilde(str(j.path.parent)),
            j.model or "?",
            f"{j.turns} turns",
            f"{j.tool_calls} tool calls",
            f"{j.asks} System 1 asks",
            f"${j.cost:.4f}" if j.cost else "cost not reported",
        ]
        flags = [name for name, on in (("follow", self.opts.follow), ("thinking", self.opts.thinking)) if on]
        flags += [] if self.opts.system1 else ["System 1 hidden"]
        flags += ["full output"] if self.opts.full else []
        info = Text(clip(" · ".join(stats), width), style="dim")
        hints = Text("esc: back to agents   ", style="dim")
        hints.append("  ".join(f"[{f}]" for f in flags), style="dim italic")
        return Text("\n").join([top, task, info, hints])

    # Actions

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Session keys (esc and the toggles) only work on the Sessions page."""
        return not (action in ("page", "flip") and self.page != "sessions")

    def action_page(self, page: str) -> None:
        self.page = page
        self.query_one("#switcher", ContentSwitcher).current = page
        if page == "sessions":
            self.rerender()
            self.query_one("#log", RichLog).focus()
        else:
            self.refresh_agents()
            self.query_one("#table", DataTable).focus()
        self.refresh_chrome()
        self.refresh_bindings()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        path = Path(str(event.row_key.value))
        index = next((i for i, j in enumerate(self.journals) if j.path == path), None)
        if index is not None:
            self.index = index
            self.current.poll()
            self.action_page("sessions")

    def action_flip(self, name: str) -> None:
        setattr(self.opts, name, not getattr(self.opts, name))
        self.rerender()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path, help="extra journals or folders of journals")
    args = parser.parse_args()
    paths: list[Path] = [p.expanduser() for p in args.paths]
    start = next((p for p in paths if p.is_file()), None)
    Viewer(paths, start).run()


if __name__ == "__main__":
    main()

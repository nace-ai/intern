"""The app: one harness Session driven from a prompt line, with its events on screen.

Layout: the status bar (both models, turn, context, cost); the transcript on the left, a compaction's card in it
with the summary System 2 continues from; on the right the System 1 panel (every judgement System 1 made, a row
each: a turn it watched, a call it guarded, a search it walked, a compaction's reads it sorted) over the State panel
(the session as System 1 reads it); the prompt line at the bottom. Until the first prompt, the entering page takes
their place (`welcome.py`). Enter sends a prompt, or steers the run in progress. Slash commands:
/models (each model's own: /frontier, /efficient or /handover, /decision), /state, /thinking, /abort, /panels, /help,
/quit.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from multiprocessing import resource_tracker
from pathlib import Path
from typing import ClassVar

import httpx
from pydantic import TypeAdapter, ValidationError
from textual import events, on
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.driver import Driver
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Input, Static, TextArea

from intern import capability_toggles, names, secrets_commands
from intern.capability_settings import CapabilitySettings, GuardConfig, HandoffConfig
from intern.config import (
    KNOWN_SYSTEM2,
    Bootstrap,
    Options,
    System1Unconfigured,
    System2Unavailable,
    home_dir,
    process_env,
)
from intern.events import (
    Compaction,
    ContextBudget,
    Decision,
    Event,
    EventSink,
    FreeNotice,
    Handoff,
    HookError,
    Indexing,
    MessageEvent,
    Nudge,
    ProcessCheck,
    ProcessEvent,
    ProcessInfo,
    ReasoningDelta,
    Reduced,
    RunEnd,
    RunError,
    RunStart,
    SecretUnavailable,
    State,
    StateWriting,
    SubagentEnd,
    SubagentStart,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolGuard,
    TurnStart,
    WindowFallback,
)
from intern.extensions.schedule import Schedule
from intern.free_commands import FreeReport, console_of, report, set_use, sign_out
from intern.free_session import METER_NAMES, SWITCH_REASONS, FreeSession, System
from intern.handoff import DEFAULT_TARGET
from intern.hooks import RunOutcome
from intern.journal import (
    CompactionEntry,
    Entry,
    EventEntry,
    Journal,
    MemoryRecallEntry,
    MessageEntry,
)
from intern.loop import ModelBinding
from intern.memory.spend import read_spend
from intern.messages import AssistantMessage, ToolResultMessage, Usage, UserMessage
from intern.models.catalog import DECISIONS_PREFIX, Catalog, ModelInfo, RunnerInfo, system1_choices
from intern.models.profile import EFFORT_LEVELS
from intern.models.registry import FREE_FINAL, ConfigError, Role
from intern.nace_free import FreeStore
from intern.needs import InstallFailed, Needs, Say, install
from intern.onboarding import FREE_ABOUT, PROVIDER_BY_ID, free_names, offered_models
from intern.onboarding import Role as OfferedRole
from intern.processes import duration_text
from intern.session import Session, SessionBusy
from intern.session_cut import STATE_SHARE, CutView, cut_view
from intern.session_setup import capability_needs, runner_choices, runner_config, runner_problem
from intern.tui.ledger import (
    CompactionJudgement,
    GuardJudgement,
    HandoverJudgement,
    Ledger,
    Row,
    RuleRefusal,
    RunLedger,
    TurnJudgement,
)
from intern.tui.onboarding import Onboarded, OnboardingScreen, StartedFree, free_blocked
from intern.tui.pickers import THINKING_DEFAULT, Choice, ModelPicker, SecretForm
from intern.tui.schedule import TimerDelivery
from intern.tui.state_panel import StatePanel, system2_start
from intern.tui.system1_panel import System1Panel
from intern.tui.theme import DARK_THEME, LIGHT_THEME, S_HEAD, S_INK, S_MUTED, S_OK, S_TAB, S_WARN
from intern.tui.views import ViewHost, Views
from intern.tui.welcome import SETUP_NOTE, GuardState, Opening, Welcome
from intern.tui.widgets import (
    JOB,
    HelpCard,
    Prompt,
    StatusBar,
    Transcript,
    attachments,
    brief,
    ended_lines,
    guard_card,
    handoff_card,
    images_in,
    look_card,
    meter,
    model_label,
    rule_card,
    supervisor_card,
    tokens_text,
    turn_head,
)

CatalogLoader = Callable[[], Awaitable[Catalog]]
DefaultSystem2 = Callable[[], Awaitable[str | None]]
CONFIG_HINT = f"set system2 in {names.HOME}/config.toml to keep a choice"
RELOAD_ENV = f"{names.ENV_PREFIX}TUI_RELOAD"
"""Set for the process /reload starts: the picks to reopen on, as JSON. Read once, and not passed on to tools."""
ALT_SCREEN_ON, ALT_SCREEN_OFF, SHOW_CURSOR, ERASE_SCREEN = "\x1b[?1049h", "\x1b[?1049l", "\x1b[?25h", "\x1b[2J"


@dataclass
class Models:
    """The picks made so far, what a session is opened (or reopened) on: None keeps the configured value."""

    system2: str | None = None
    system1: str | None = None
    capabilities: CapabilitySettings | None = None
    """A whole replacement for the saved `[capabilities]`, built from the toggles made in the UI."""
    session_file: Path | None = None
    """The journal to reopen on, so a reopened session continues the conversation."""
    timers_stopped: int = 0
    """One reopen notice, carried across /reload too; no timer data is restored."""
    fresh: bool = False
    """Open on a new journal, whatever `--session` named: /clear starts the conversation over."""
    thinking: str | None = None
    """The thinking level chosen, kept across model switches and reopens; None while none was."""
    auto: bool = False
    """Auto mode: a run hands over to the cheaper model once System 1 judges that model can finish it (`/handover`).
    `main` starts it as the config says, on unless `auto = false`."""
    dark: bool = False
    """The dark look, picked with /dark."""
    entering: bool = False
    """The entering page was up at /reload: the restarted process opens on it again, not on the conversation."""


@dataclass(frozen=True)
class SetupHooks:
    """What the setup screen needs from `main`: the keys set now, to tell which are already there, and where to
    put the keys it is given so this process's sessions use them at once."""

    env: Callable[[], Mapping[str, str]]
    configured: Callable[[Mapping[str, str]], None]
    transport: httpx.AsyncBaseTransport | None = None
    """What the screen checks keys over; None for the network."""


PICKS = TypeAdapter(Models)
RESUME_TURNS = 30
"""The turns a resumed session shows; the earlier ones are in its journal and their work in the State."""
CUT_SOON_S = 0.5
FAILED = ("provider_error", "system1_unavailable", "internal_error")
REASONS = {"system1_unavailable": "decision model unavailable"}
"""How a run's end reads where its reason's own name would say System 1."""
"""The ways a run ends that the mark shows as a failure, its eyes crossed out for `FAILED_FACE_S`."""
FAILED_FACE_S = 6.0
FEED_SOON_S = 0.1
"""How long System 1's panel waits to redraw: a search asks hundreds of times in a few seconds."""
Opener = Callable[[EventSink, Models], Awaitable[Session]]
Installer = Callable[[Needs, Say], Awaitable[None]]
"""Installs what a capability needs and this environment lacks, saying what it does (`needs.install`)."""

DECIDES = " · decision model"
"""What a capability's label has (`capability_toggles.toggles`) when the decision model takes part in it."""
ENTERING_HIDES = ("#status", "#side")
"""What the entering page takes the place of."""
ADD_SECRET = "+add"
"""The /secrets row that adds an entry; no secret can have this name."""

HELP: dict[str, dict[str, str]] = {
    "Models": {
        "/models": "the frontier, efficient and decision models, the efficient one gray while hand-over is off; "
        "Enter on one picks it. /frontier <provider/model>, /efficient on|off|<provider/model> and /decision <model> "
        "set one directly",
        "/thinking [level]": "the frontier model's thinking level, low to max as the model takes it (also Shift+Tab, "
        "and ←→ in the /frontier picker)",
        "/setup": "connect your models: provider, key and model for each; saved to config.toml and the keychain",
        "/free [start|pause|resume|sign-out]": "the free credit: what is left and what each model runs on; start it, "
        "pause it to run on your own keys, resume it, or sign out",
    },
    "Session": {
        "/abort": "stop the run in progress (also Esc)",
        "/compact": "compact the conversation now, as a run does near the window's limit",
        "/state": "the State in full: the session as the decision model reads it",
        "/clear": "start over on a fresh journal; the models and capabilities stay",
        "/reload": "restart on the code and config as they are now and resume this session",
        "/quit": "close the session and exit (also Ctrl+Q)",
    },
    "Capabilities": {
        "/caps [name] [path]": "switch a tool pack, the browser, code search, the memory drive or an MCP connection "
        "on or off; the session reopens on its journal (also /capabilities)",
        "/runner [name]": "pick where commands run: local, docker, opensandbox or an installed runner; the session "
        "reopens on its journal",
        "/secrets [set|remove NAME]": "keys in the OS keychain or a password manager, with whether each loads; Enter "
        "on one replaces it; new sessions use them",
        "/schedule": "the timers; Enter stops one (switch them on with /caps schedule)",
    },
    "Screen": {
        "/panels": "show or hide the decision model and State panels (also F2)",
        "/dark": "the dark look, kept on /reload",
        "/light": "the light look, the default",
        "/help": "this list",
    },
}
"""What /help lists, by what each command is for: each as it is typed, with what it takes."""
COMMANDS = tuple(usage.split()[0] for rows in HELP.values() for usage in rows)
"""The commands by name, in /help's order: the prompt suggests the first that starts with what is typed."""
KEYS: dict[str, str] = {
    "Esc": "abort the run in progress, or clear the prompt",
    "Shift+Tab": "cycle the thinking level",
    "F2": "show or hide the decision model and State panels",
    "Ctrl+O": "expand or fold the latest tool call's output; a click on a call or a thought shows it all",
    "Ctrl+C": "copy what is selected in the prompt, or clear it",
    "Ctrl+X": "cut what is selected in the prompt (Ctrl+Z undoes, Ctrl+Y redoes)",
    "Shift+arrows": "select in the prompt; F7 selects all, a double click a word, a triple click the line",
    "Alt+Backspace": "delete the word before the cursor (also Ctrl+W); Ctrl+U and Ctrl+K delete to the line's ends",
    "Ctrl+Q": "quit",
}
"""The keys, listed by /help after the commands."""


def free_refusal(error: RunError) -> str:
    """What the console's refusal for good of a free credit means, from its code and meter."""
    system: System = "system1" if error.meter == "system1" else "agent"
    return SWITCH_REASONS.get(error.code, f"Free {METER_NAMES[system]} credit used up")


def _drive(value: str) -> Path:
    """A typed memory drive path, `~` expanded."""
    return Path(value).expanduser()


def _runner_group(choice: RunnerInfo, current: str) -> str:
    """Where `/runner` lists a runner: the one in use, then those that can run here, then those that cannot."""
    if choice.id == current:
        return "in use"
    return "unavailable" if choice.unavailable else "can run here"


ROLE_NAMES = {"system2": "Frontier model", "system1": "Decision model"}
"""What the transcript calls a picker's role."""
AUTO_OFF = ("off", "none", "no")
"""What `/handover` takes to turn auto mode off; the first is the picker's row."""


def _switched(toggle_id: str, on: bool) -> str:
    """What the transcript says a switch did: `web-read tools on`, `code search off`."""
    state = "on" if on else "off"
    if toggle_id.startswith(capability_toggles.MCP_PREFIX):
        return f"MCP {toggle_id.removeprefix(capability_toggles.MCP_PREFIX)} {state}"
    name = toggle_id.removeprefix(capability_toggles.PREFIX)
    if name in capability_toggles.PACKS:
        return f"{name} tools {state}"
    return f"{name.replace('-', ' ')} {state}"


def thinking_levels(binding: ModelBinding) -> tuple[str, ...]:
    """What /thinking and Shift+Tab set System 2's thinking level to: the efforts the model takes, least to most."""
    return binding.effort_levels if binding.takes_effort else ()


def _guard_state(guard: GuardConfig) -> GuardState:
    """What the entering page says of the guard: off, the fixed rules alone, or System 1 judging too."""
    if not guard.enabled:
        return "off"
    return "full" if guard.system1 else "rules"


CLIPBOARD_COMMANDS = (
    ("pbcopy",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("xsel", "--clipboard", "--input"),
)


@functools.cache
def clipboard_command() -> tuple[str, ...] | None:
    """The machine's own clipboard command, when it has one."""
    return next((command for command in CLIPBOARD_COMMANDS if shutil.which(command[0])), None)


def clipboard_copy(text: str) -> None:
    """Text onto the machine's clipboard, for the terminals that do not take OSC 52: Terminal.app never does,
    iTerm2 not until it is allowed to."""
    if (command := clipboard_command()) is None:
        return
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(command, input=text.encode(), check=False, timeout=2)


def _short(path: Path) -> str:
    """A path to show, the home directory as `~`."""
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home) :] if text.startswith(home + os.sep) else text


ALIASES = ("/frontier", "/efficient", "/handover", "/decision", "/capabilities", "/exit", "/login", "/connect")
"""Names commands answer to that /help lists only in passing: each model's own picker, which a model typed after
it sets directly (`/handover` is `/efficient`), and the rest."""
COMMAND = re.compile(r"/[a-z0-9]+(?:\s|$)", re.IGNORECASE)
"""A prompt that is a command: a slash and a word, then a space or the end; `/var/folders/...` is a path."""


def resolve_command(name: str) -> str:
    """A command by its name or alias, or by a prefix that names only one: `/cap` is `/caps`."""
    name = name.lower()
    if name in COMMANDS or name in ALIASES:
        return name
    matches = [command for command in (*COMMANDS, *ALIASES) if command.startswith(name)]
    return matches[0] if len(matches) == 1 else name


CONSOLE_GROUP = "Console · decision models"
DECISIONS_GROUP = "decisions API · OpenRouter"


def system2_choice(model: ModelInfo) -> Choice:
    """A frontier or efficient model's row: under its provider, the id without the provider prefix, the display name
    as the note when it says more."""
    provider, _, rest = model.id.partition("/")
    name = KNOWN_SYSTEM2.get(model.id) or model.name
    note = "" if name.casefold() in (model.id.casefold(), rest.casefold()) else name
    return Choice(model.id, name, provider, label=rest or model.id, note=note)


def offered(models: Iterable[ModelInfo], role: OfferedRole, keep: str = "") -> list[ModelInfo]:
    """The catalog's models to offer for a role, provider by provider (`offered_models`); on a provider setup does
    not know (an endpoint of your own), all it lists. `keep`, the one in use, comes first, whether or not it is one
    of them."""
    by: dict[str, dict[str, ModelInfo]] = {}
    for model in models:
        provider, _, rest = model.id.partition("/")
        by.setdefault(provider, {})[rest] = model
    if keep:
        provider, _, rest = keep.partition("/")
        by.setdefault(provider, {}).setdefault(rest, ModelInfo(id=keep, name=keep))
    kept: list[ModelInfo] = []
    for provider in sorted(by, key=lambda p: p != keep.partition("/")[0]):
        listed, option = by[provider], PROVIDER_BY_ID.get(provider)
        names = offered_models(option, tuple(listed), role) if option is not None else list(listed)
        kept += [m for m in listed.values() if m.id == keep]
        kept += [listed[name] for name in names if listed[name].id != keep]
    return kept


def system1_choice(model: ModelInfo) -> Choice:
    if model.id.startswith(DECISIONS_PREFIX):
        label = model.id.removeprefix(DECISIONS_PREFIX)
        note = "" if model.name.casefold() in (model.id.casefold(), label.casefold()) else model.name
        return Choice(model.id, model.name, DECISIONS_GROUP, label=label, note=note)
    note = "" if model.name == model.id else model.name
    return Choice(model.id, model.name, CONSOLE_GROUP, note=note)


class HarnessEvent(Message):
    """One engine event, posted from the session's tasks into the app's message loop."""

    def __init__(self, event: Event) -> None:
        super().__init__()
        self.event = event


class StateScreen(ModalScreen[None]):
    """System 1's State in full: the exact text it reads, its JSON laid out. System 2's summary is in each
    compaction's card."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close"), Binding("q", "close", "Close")]

    def __init__(self, cut: CutView | None) -> None:
        super().__init__()
        self.cut = cut

    def compose(self) -> ComposeResult:
        with Vertical(id="state-full"):
            if self.cut is None:
                yield Static(Content.styled("No State yet: it is built from the first prompt.", S_MUTED))
            else:
                tokens = f"{tokens_text(self.cut.tokens)} of {tokens_text(self.cut.budget)} tokens"
                said = f"The session as the decision model reads it, rebuilt for every ask: {tokens}."
                yield Static(Content.styled(said, S_MUTED), classes="state-note")
                text = json.dumps(json.loads(self.cut.text), indent=2, ensure_ascii=False)
                yield TextArea(text, read_only=True, soft_wrap=True, show_line_numbers=False, id="state-cut")

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """/help: the commands and the keys, over the session; Esc or q closes it."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Close"), Binding("q", "close", "Close")]

    def compose(self) -> ComposeResult:
        page = VerticalScroll(id="help-page")
        page.border_title = Content.assemble((" Commands and keys ", S_TAB))
        page.border_subtitle = Content.assemble((" Esc", S_HEAD), (" closes ", S_MUTED))
        here = "/dark" if self.app.current_theme.dark else "/light"  # the look in force needs no line
        with page:
            yield HelpCard(
                {**{group: {k: v for k, v in rows.items() if k != here} for group, rows in HELP.items()}, "Keys": KEYS}
            )

    def action_close(self) -> None:
        self.dismiss(None)


class HarnessApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = names.TITLE
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q,super+q", "quit", "Quit", priority=True),  # Cmd+Q where the terminal passes it on
        Binding("escape", "abort", "Abort", show=False),
        Binding("f2", "toggle_panels", "Panels"),
        Binding("ctrl+o", "toggle_output", "Expand output", show=False),
        # Priority, so the prompt's own paging does not take them: the prompt is a few lines at most.
        Binding("pageup", "scroll_transcript(-1)", "Scroll up", show=False, priority=True),
        Binding("pagedown", "scroll_transcript(1)", "Scroll down", show=False, priority=True),
        Binding("shift+tab", "cycle_thinking", "Thinking", show=False, priority=True),
    ]

    def __init__(
        self,
        open_session: Opener,
        *,
        catalog: CatalogLoader | None = None,
        system1_choices: list[ModelInfo] | None = None,
        first_prompt: str | None = None,
        default_system2: DefaultSystem2 | None = None,
        capabilities: CapabilitySettings | None = None,
        memory: Path | None = None,
        home: Path | None = None,
        copy: Callable[[str], None] | None = None,
        picks: Models | None = None,
        reloaded: bool = False,
        install: Installer | None = None,
        welcome: bool = True,
        setup: SetupHooks | None = None,
    ) -> None:
        super().__init__()
        # Before the stylesheet is read: it names the theme's own variables ($rule, $wash, $muted).
        self.register_theme(LIGHT_THEME)
        self.register_theme(DARK_THEME)
        self.theme = (DARK_THEME if picks is not None and picks.dark else LIGHT_THEME).name
        self._open = open_session
        self._load_catalog = catalog
        self._default_system2 = default_system2
        self._capabilities_at_start = capabilities if capabilities is not None else CapabilitySettings()
        self._memory_at_start = memory
        self._home = home
        self._copy = clipboard_copy if copy is None else copy
        """What else takes the copied text, besides the terminal: the machine's clipboard by default."""
        self._install = install
        """What `/caps` installs a capability's missing extra with before switching it on; None installs nothing, and
        the session that cannot open on the pick says what is missing."""
        self._setup = setup
        """The setup screen's hooks; None runs without it: a session that cannot open says why, as before."""
        self._setting_up = False
        self._installing = False
        self._resuming = False
        self._replaying = False
        self._nudge_text: str | None = None
        self.pending = picks or Models()
        self._reloaded = reloaded
        """Started by /reload: the alternate screen is already up, with the last frame on it."""
        self._width = 0
        """The terminal's width at the last resize."""
        self._handoff_missing = ""
        """Why auto mode's hand-over model could not be opened, as last told."""
        self.reload: Models | None = None
        """Set by /reload: what the restarted process reopens on. `main` restarts it once the app has exited."""
        self.journal_path: Path | None = None
        """The session's journal, kept on quit so `main` can say how to resume it."""
        self._system1_choices = list(system1_choices or [])
        self._first_prompt = first_prompt
        self._entering = welcome and first_prompt is None and (not reloaded or self.pending.entering)
        """The entering page is up, in place of the masthead and the side panels: until the conversation begins."""
        self.session: Session | None = None
        self.catalog: Catalog | None = None
        self.ledger = Ledger()
        self.run_count = 0
        self.run_spent = (0.0, 0.0)
        """System 2's and System 1's cost counted on the masthead as the run went; its end tops them up to its total."""
        self._cut_timer: Timer | None = None
        self._feed_timer: Timer | None = None
        self._opening = True
        self._run_task: asyncio.Task[RunOutcome] | None = None
        self._queued: list[tuple[str, Static]] = []
        """Messages typed during a run that it has not taken yet, each with its `queued` line."""
        self.views = Views(ViewHost(self))
        """The session's extension views (`tui/views.py`): what extensions draw of their own."""

    def compose(self) -> ComposeResult:
        yield StatusBar()
        with Horizontal(id="body"):
            with Vertical(id="main"):  # System 2: its conversation, and under it the prompt, which is to it alone
                yield Transcript()
                yield Prompt(
                    placeholder="Describe the task… (/help for commands)",
                    id="prompt",
                    commands=(*COMMANDS, *ALIASES),
                )
            with Vertical(id="side"):  # System 1 above the State
                yield System1Panel()
                yield StatePanel(id="state")

    def get_driver_class(self) -> type[Driver]:
        """The terminal's driver, made to stay on the alternate screen across /reload: the exiting process leaves
        its last frame up and the restarted one paints over it. Leaving the screen and entering it again would show
        the terminal's own background in between, for as long as the restart takes."""
        app = self

        class ScreenKeeping(super().get_driver_class()):
            def write(self, data: str) -> None:
                if app.reload is not None and data in (ALT_SCREEN_OFF, SHOW_CURSOR):
                    return
                if app._reloaded and data == ALT_SCREEN_ON:
                    return
                super().write(data)

        return ScreenKeeping

    @property
    def transcript(self) -> Transcript:
        return self.query_one(Transcript)

    @property
    def status(self) -> StatusBar:
        return self.query_one(StatusBar)

    @property
    def state_panel(self) -> StatePanel:
        return self.query_one(StatePanel)

    @property
    def _cwd(self) -> Path | None:
        """Where the session works: read paths under it are shown relative to it."""
        return self.session.deps.cwd.resolve() if self.session is not None else None

    @property
    def system1_panel(self) -> System1Panel:
        return self.query_one(System1Panel)

    # -- opening ---------------------------------------------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        """Erase the screen when the terminal narrows, before Textual repaints it: macOS Terminal keeps each row's
        text past the new right edge, and draws the next column of it in the sliver of window left over."""
        if event.size.width < self._width and self._driver is not None:
            self._driver.write(ERASE_SCREEN)
        self._width = event.size.width

    async def on_mount(self) -> None:
        self.query_one("#prompt", Prompt).focus()
        if self._entering:
            await self._enter()
        # A closed terminal window (SIGHUP) or a kill quits as /quit does, so the session closes and its steward starts.
        if os.name == "posix":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGHUP, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: self.run_worker(self.action_quit(), exit_on_error=False))
        self.run_worker(self._start(), name="open-session", exit_on_error=False)
        self.set_interval(1.0, self._jobs_strip)

    async def _enter(self) -> None:
        """The entering page (`welcome.py`): the transcript's first block, with the masthead and the side panels
        put away, so it has the screen."""
        self._entering = True
        for name in ENTERING_HIDES:
            self.query_one(name).display = False
        self.transcript.add_class("entering")
        await self.transcript.mount(Welcome(self._welcome()))

    async def _begin(self) -> None:
        """The conversation begins (a prompt, a resumed journal): the entering page goes, and the masthead and the
        side panels come back."""
        if not self._entering:
            return
        self._entering = False
        for name in ENTERING_HIDES:
            self.query_one(name).display = True
        self.transcript.remove_class("entering")
        await self.query(Welcome).remove()

    def _welcome(self, shown: Opening | None = None) -> Opening:
        """What the entering page says of the session: the masthead's models, and what the session has. While
        the session reopens (a capability was switched), what the page `shown` of it stays, so the page holds
        still."""
        session, status = self.session, self.status
        if session is None and shown is not None:
            return replace(
                shown, system2=status.system2, system1=status.system1, handoff=status.handoff, note=status.note
            )
        free = self._free()
        toggles = self._toggles(self._capabilities_now()) if session is not None else []
        return Opening(
            system2=status.system2,
            system1=status.system1,
            handoff=status.handoff,
            thinking=status.thinking,
            levels=status.levels,
            note=status.note,
            cwd=self._cwd,
            journal=session.deps.journal.path if session is not None else None,
            capabilities=[(t.id, t.name, t.on, DECIDES in t.label) for t in toggles],
            guard=_guard_state(self._capabilities_now().guard) if session is not None else "off",
            free=free is not None and free.on_free("agent"),
            credit=self._credit_line(),
        )

    def _jobs_strip(self) -> None:
        """System 1's row of the status bar: the background jobs it watches, each with its run time, its verdict at
        the latest look and when it looks next."""
        jobs = self.session.jobs_status() if self.session is not None else []
        parts = []
        for job in jobs[:2]:
            verdict = job.verdict.replace("_", " ") if job.verdict else "not looked at yet"
            look = f" · next look {duration_text(round(job.next_look_s))}" if job.next_look_s is not None else ""
            parts.append(f"{JOB} {brief(job.command, 28)} {duration_text(job.elapsed_s)} · {verdict}{look}")
        if len(jobs) > 2:
            parts.append(f"+{len(jobs) - 2} more")
        text = "   ".join(parts)
        if text != self.status.watching:
            self.status.watching = text
            self.status.refresh_line()

    async def _start(self) -> None:
        if self._load_catalog is not None:
            self.run_worker(self._fetch_catalog(), name="catalog", exit_on_error=False)
        if await self._open_with(self.pending) and self._first_prompt:
            await self._submit(self._first_prompt)

    async def _open_with(self, models: Models) -> bool:
        """Open the session on `models` (the picks made so far). Without a configured System 2, start on the
        default the harness can reach (the masthead names it); a session that cannot open leaves the pickers to
        fix it."""
        self._opening = True
        try:
            session = await self._open(self._emit, models)
        except System2Unavailable as e:
            self._opening = False
            default = await self._default_system2() if self._default_system2 is not None else None
            if models.system2 is None and default:
                self.pending.system2 = default  # /frontier changes it, config.toml keeps one
                return await self._open_with(self.pending)
            if self._setup is not None:
                # Nothing to run on yet: setup connects it, rather than an error to read.
                self.status.note = SETUP_NOTE
                self._refresh_status()
                self._open_setup()
                return False
            await self.transcript.notice(f"Cannot open a session: {e}", "error")
            await self.transcript.notice(
                f"No provider key found for a default. /frontier picks a model (a typed provider/model is accepted) "
                f"and opens the session on it; {CONFIG_HINT}.",
                "warn",
            )
            self._refresh_status()
            return False
        except System1Unconfigured as e:
            self._opening = False
            if self._setup is not None:
                self.status.note = SETUP_NOTE
                self._refresh_status()
                self._open_setup("system1", e.system2)
                return False
            await self.transcript.notice(f"Cannot open a session: {e}", "error")
            await self.transcript.notice("Connect the decision model with /setup, or pick one with /decision.", "warn")
            self._refresh_status()
            return False
        except ConfigError as e:
            self._opening = False
            if self._setup is not None:
                await self.transcript.notice(f"The session could not open: {e}", "warn")
                await self.transcript.notice(
                    "/setup reconnects your models, or /frontier and /decision pick others: either reopens it.", "ok"
                )
                self._refresh_status()
                return False
            await self.transcript.notice(f"Cannot open a session: {e}", "error")
            await self.transcript.notice(
                "Fix the configuration (export the key it names), or pick with /frontier or /decision: a pick "
                "reopens the session on it.",
                "warn",
            )
            self._refresh_status()
            return False
        self.session = session
        self._opening = False
        self.status.note = ""
        await self._session_opened(session)
        if self.pending.timers_stopped:
            await self.transcript.notice(f"{self.pending.timers_stopped} timers stopped")
            self.pending.timers_stopped = 0
        return True

    async def _session_opened(self, session: Session) -> None:
        journal = session.deps.journal
        if (system1 := journal.last_models().get("system1")) and system1 != session.system1:
            try:
                await session.set_model(system1, "system1")
            except (ConfigError, ValueError) as e:
                await self.transcript.notice(
                    f"The session's last decision model, {system1}, cannot be set up: {e}", "warn"
                )
        self._keep_thinking(session.deps.system2)
        if self.pending.auto and (missing := session.handoff_missing) and missing != self._handoff_missing:
            self._handoff_missing = missing  # said once, not at every reopen
            await self.transcript.notice(
                f"Runs stay on the frontier model, with no efficient model: {missing}. /efficient <provider/model> "
                "names another.",
                "warn",
            )
        if not self._resuming:  # a reopened session's conversation is already on the screen
            self._replaying = True  # its compactions drawn settled, not landing again
            try:
                await self._replay(journal.active_path())
            finally:
                self._replaying = False
            await self._steward_spend(journal.path)
        self.state_panel.cwd = self.system1_panel.cwd = self._cwd
        self._cut_now()
        await self.views.load(session.deps.hooks.extensions)
        self._refresh_status()

    def _cut_soon(self) -> None:
        """System 1's State rebuilt for the panel, at most every CUT_SOON_S: a long session's cut takes a tenth
        of a second, so it is built off the event loop, from the journal as it stands."""
        if self._cut_timer is None:
            self._cut_timer = self.set_timer(CUT_SOON_S, self._cut_now)

    def _cut_now(self) -> None:
        if self._cut_timer is not None:
            self._cut_timer.stop()
        self._cut_timer = None
        if (session := self.session) is None:
            return
        entries = session.deps.journal.active_path()
        budget = int(session.deps.decisions.max_state_tokens * STATE_SHARE)  # as the loop cuts it for System 1
        self.run_worker(self._show_cut(session, entries, budget, system2_start(entries)), group="cut", exclusive=True)

    async def _show_cut(self, session: Session, entries: list[Entry], budget: int, start: str | None) -> None:
        running = session.deps.processes.for_system1() if session.deps.processes is not None else []
        cut = await asyncio.to_thread(cut_view, entries, budget, running)
        if self.session is session:  # not a session /clear closed meanwhile
            await self.state_panel.show_cut(cut, start)

    async def _steward_spend(self, journal: Path | None) -> None:
        if self._home is None or journal is None:
            return
        spent = read_spend(self._home, journal)
        if not spent.runs:
            return
        writer = spent.writer
        self._spend(writer if writer.input_tokens else None, spent.system1.cost_usd)
        asks = spent.system1.asks
        line = f": {asks} decision model ask{'s' if asks != 1 else ''}"
        if writer.input_tokens:
            sent, got = tokens_text(writer.input_tokens), tokens_text(writer.output_tokens)
            line += f" and {sent} in / {got} out to the writer"
        line += " stewarding this session"
        if spent.cost_usd >= 0.0001:
            line += f", ${spent.cost_usd:.4f}"
        if spent.runs > 1:
            line += f" ({spent.runs} runs)"
        await self.transcript.marker(Content.assemble(("◇ Memory steward", S_INK), line))

    def _emit(self, event: Event) -> None:
        self.post_message(HarnessEvent(event))

    async def _fetch_catalog(self) -> None:
        assert self._load_catalog is not None
        try:
            self.catalog = await self._load_catalog()
        except Exception as e:  # listings are a convenience; the pickers still take typed names
            await self.transcript.notice(f"Could not list models: {type(e).__name__}: {e}", "warn")
            return
        # A model by its listed name, without the maker an aggregator puts first (`Anthropic: Claude Opus 5.5`).
        self.status.labels = {
            m.id: m.name.rpartition(": ")[2] for m in self.catalog.system2 if m.name and m.name != m.id
        }
        self._refresh_status()

    async def _replay(self, entries: list[Entry]) -> None:
        """A resumed journal as it stands, not run again step by step: the counts and the ledger from all of it,
        and on screen only its last `RESUME_TURNS` turns with System 1's side of them (`event` entries, through
        the same handling as live events), a compaction's card with the summary its entry keeps. Drawing the
        whole of a long session one widget at a time took minutes (each mount lays out all the widgets before it);
        the turns left out are in the journal, their work in the State. Journals from before System 1's side was
        kept have no such entries."""
        replies = [
            i for i, e in enumerate(entries) if isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
        ]
        if any(isinstance(e, MessageEntry) for e in entries):
            await self._begin()
        start = replies[-RESUME_TURNS] if len(replies) > RESUME_TURNS else 0
        run_id, nudge, summary, turn = None, None, "", 0
        handed: HandoverJudgement | None = None  # a hand-over before the turns drawn, its own turns among them
        for i, entry in enumerate(entries):
            drawn = i >= start
            if isinstance(entry, MessageEntry) and entry.run_id is not None and entry.run_id != run_id:
                run_id = entry.run_id  # a run began: its own ledger, under the prompt that began it
                await self._hand_back()
                handed, turn = None, 0
                self.run_count += 1
                first = entry.message
                asked = first.content if isinstance(first, UserMessage) and first.source == "user" else ""
                self.ledger.runs.append(RunLedger(run_id=run_id, prompt=asked))
            if drawn and handed is not None and isinstance(entry, MessageEntry):
                name = model_label(handed.target, self.status.labels)
                await self.transcript.hand_over(handoff_card(handed, name))
                self.transcript.working("Efficient model", name)
                self.status.handed, handed = True, None
            match entry:
                case EventEntry(event=event):
                    if isinstance(event, Compaction):  # the summary is its entry's, just before it
                        event = event.model_copy(update={"summary": summary})
                    nudge = event.text if isinstance(event, Nudge) else None
                    if drawn:
                        await self._handle(event)
                    elif isinstance(row := self._account(event), HandoverJudgement) and isinstance(event, Handoff):
                        handed = row
                    continue
                case MessageEntry(message=AssistantMessage(usage=usage) as reply):
                    turn += 1
                    self._spend(usage, efficient=self._by_efficient(reply.model))
                    run = self.ledger.current
                    run.model = run.model or reply.model  # the frontier model: a run starts on it
                    self.status.context = usage.input_tokens + usage.output_tokens  # the next request's size, nearly
                    calls = [
                        ToolCallStart(turn=0, call_id=call.id, name=call.name, arguments=call.arguments)
                        for call in reply.tool_calls
                    ]
                    for start_call in calls:
                        self.ledger.observe(start_call)  # a search's row on System 1's panel, a refusal's call
                    if not drawn:
                        continue
                    await self.transcript.begin_turn(self._turn_head(turn, reply.model))
                    if reply.reasoning:
                        await self.transcript.reasoning(reply.reasoning, live=False)
                    if reply.text:
                        await self.transcript.text(reply.text)
                    for start_call in calls:
                        await self.transcript.tool_start(start_call, live=False)
                    await self.transcript.finish_reply()
                case CompactionEntry(summary=text):
                    summary = text
                case MemoryRecallEntry(cost_usd=cost):
                    self._spend(system1=cost)
                case MessageEntry(message=ToolResultMessage(result=result)) if not drawn:
                    self.ledger.observe(
                        ToolCallEnd(
                            turn=0,
                            call_id=result.call_id,
                            name=result.tool_name,
                            status=result.status,
                            content="",
                            duration_ms=result.duration_ms,
                        )
                    )
                case _ if not drawn:
                    continue
                case MessageEntry(message=UserMessage(content=content, source="user")):
                    await self.transcript.user(content)
                case MessageEntry(message=UserMessage(content=content, source="harness")):
                    if content != nudge:  # a nudge's message: its event above showed it
                        await self.transcript.notice(f"⚑ {content}", "warn")
                case MessageEntry(message=ToolResultMessage(result=result)):
                    end = ToolCallEnd(
                        turn=0,
                        call_id=result.call_id,
                        name=result.tool_name,
                        status=result.status,
                        content=result.content,
                        duration_ms=result.duration_ms,
                    )
                    self.ledger.observe(end)
                    self.transcript.tool_end(end)
        await self._hand_back()  # the session's last run is over, however it ended
        self.call_after_refresh(self.transcript.scroll_end, animate=False)  # at the end, where the session left off
        self._feed()

    def _refresh_status(self) -> None:
        if self.session is not None:
            binding = self.session.deps.system2
            self.status.system2 = binding.ref
            self.status.system1 = self.session.system1
            self.status.window = binding.window.working
            self.status.thinking = (
                (binding.profile.reasoning_effort or THINKING_DEFAULT) if binding.takes_effort else ""
            )
            self.status.levels = binding.effort_levels
            self.status.free = self._agent_meter()
            handoff = self.session.deps.handoff
            runs = handoff is not None and handoff.binding.ref != binding.ref
            self.status.handoff = handoff.binding.ref if handoff is not None and runs else ""
            if not self.status.handed:
                self.transcript.working(self._role(), model_label(binding.ref, self.status.labels))
        else:
            self.status.system2 = self.pending.system2 or "none"
            self.status.system1 = self.pending.system1 or "none"
            self.status.thinking = self.status.free = ""
            self.status.note = (
                "opening the session…" if self._opening else SETUP_NOTE if self._setup is not None else "no session"
            )
        self.status.refresh_line()
        for page in self.query(Welcome):
            page.show(self._welcome(page.opening))

    # -- events ----------------------------------------------------------------------------------------------

    async def on_harness_event(self, message: HarnessEvent) -> None:
        """A live event: the TUI's own handling, then the extensions' views (a replayed one gets only the first)."""
        await self._handle(message.event)
        await self.views.dispatch(message.event)

    def _by_efficient(self, model: str) -> bool:
        """A reply the efficient model wrote: one by the model a run hands over to, when that is not the frontier's."""
        return bool(model) and model == self.status.handoff and model != self.status.system2

    def _role(self) -> str:
        """System 2's name on the transcript's tab: the frontier model only while there is one to hand over to."""
        return "Frontier model" if self.status.handoff else "Model"

    def _turn_head(self, turn: int | None = None, model: str = "") -> Content:
        """Who takes a turn: the frontier model, or the efficient one once the run has handed over; `model`, the
        reply's own, where it is known (a resumed session's turns)."""
        handed = self.ledger.current.handover
        efficient = self.status.handed
        ref = model or (handed.target if efficient and handed is not None and handed.target else self.status.system2)
        return turn_head(efficient, model_label(ref, self.status.labels), turn, handoff=bool(self.status.handoff))

    def _spend(self, system2: Usage | None = None, system1: float = 0.0, *, efficient: bool = False) -> None:
        """Count what System 2's call (a reply, a State write) and System 1's asks cost on the masthead, and how much
        of System 2's prompt was read from the cache. A System 2 call with tokens but no cost has no known price, so
        its total is short."""
        if system2 is not None and efficient:
            self.status.cost_handoff += system2.cost_usd
        elif system2 is not None:
            self.status.cost2 += system2.cost_usd
            self.status.unpriced |= system2.input_tokens > 0 and not system2.cost_usd
            self.status.prompt2 += system2.input_tokens
            self.status.cached2 += system2.cache_read_tokens
            self.status.cache_reported |= bool(system2.cache_read_tokens or system2.cache_write_tokens)
        self.status.cost1 += system1
        spent2, spent1 = self.run_spent
        self.run_spent = (spent2 + (system2.cost_usd if system2 else 0.0), spent1 + system1)

    def _account(self, event: Event) -> Row | None:
        """What an event changes, drawn or not: the ledger, each system's cost (as it is spent, so the masthead's
        move during a run and come back on resume), the context after a compaction."""
        row = self.ledger.observe(event)
        if row is not None or isinstance(event, Decision | ProcessCheck):
            self._feed_soon()
        match event:
            case MessageEvent(message=AssistantMessage(usage=usage, model=model)):
                self._spend(usage, efficient=self._by_efficient(model))
            case State(usage=Usage() as usage):
                self._spend(usage)
            case Decision(cost_usd=cost):
                self._spend(system1=cost)
            case Compaction(tokens_after=after):
                self.status.context = after  # until the next request measures it
            case _:
                pass
        return row

    def _feed_soon(self) -> None:
        """System 1's panel redrawn, at most every FEED_SOON_S."""
        if self._feed_timer is None:
            self._feed_timer = self.set_timer(FEED_SOON_S, self._feed)

    def _feed(self) -> None:
        """System 1's panel on the ledger as it stands."""
        if self._feed_timer is not None:
            self._feed_timer.stop()
        self._feed_timer = None
        self.system1_panel.show(self.ledger)

    async def _handle(self, event: Event) -> None:
        """One event, live or replayed from the journal."""
        row = self._account(event)
        if isinstance(event, MessageEvent | Compaction | ProcessEvent):
            self._cut_soon()
        if isinstance(event, Nudge):
            self._nudge_text = event.text
        transcript = self.transcript
        run = self.run_count  # the run the event's rows and markers belong to
        match event:
            case RunStart():
                await self._begin()
                self.run_count += 1
                self.run_spent = (0.0, 0.0)
                self.status.running = True
                if self.session is not None and any(tool.name == "decision_code" for tool in self.session.deps.tools):
                    self.status.activity = "decision_code indexing the working directory"  # before the first turn
            case MessageEvent(message=UserMessage(content=content, source="user")):
                await self._delivered(content)
            case MessageEvent(message=UserMessage(content=content, source="harness")):
                if content != self._nudge_text:
                    await transcript.notice(f"⚑ {content}")
                self._nudge_text = None
            case TurnStart(turn=turn):
                self.status.activity = ""
                self.status.face("reading")
                await transcript.begin_turn(self._turn_head(turn))
            case TextDelta(text=text):
                await transcript.text(text)
            case ReasoningDelta(text=text):
                await transcript.reasoning(text)
            case ToolCallStart():
                self.status.face("turnstile")
                await transcript.tool_start(event)
            case ToolCallEnd():
                self.status.face("reading")
                transcript.tool_end(event)
            case ContextBudget(tokens=tokens, working_window=window):
                self.status.context, self.status.window = tokens, window
            case Decision(purpose="supervise" | "wrap_up") if isinstance(row, TurnJudgement):
                await self._show_verdict(row)
            case Decision(purpose="guard") if isinstance(row, GuardJudgement):
                await self._show_guard(row)
            case StateWriting():
                self.status.activity = "⟲ writing the summary"
            case State():
                self.status.activity = ""
            case Indexing(
                name=name, root=root, done=done, total=total, finished=finished, duration_ms=duration_ms, read=read
            ):
                key = f"index:{self.run_count}:{name}:{root}"
                tool = "decision_code" if name == "code-search" else name
                # Another folder the agent searched (decision_code's scope) is named; the working directory is not.
                here = self.session is None or Path(root) == self.session.deps.cwd.resolve()
                folder = "the working directory" if here else root
                if finished:
                    self.status.activity = ""
                    secs = (duration_ms or 0) / 1000
                    of = "" if here else f" of {root}"
                    if read is not None and read < total:  # a refresh: the rest were kept as they were
                        files = f"{read:,} changed file{'s' if read != 1 else ''}"
                        line = Content.assemble(("⌁ Refreshed", S_INK), f" {files}{of} for {tool} in {secs:.1f}s")
                    else:
                        line = Content.assemble(("⌁ Indexed", S_INK), f" {total:,} files{of} for {tool} in {secs:.1f}s")
                else:
                    self.status.activity = f"{tool} indexing {done:,} of {total:,} files"
                    share = done / total if total else 0.0
                    line = Content.assemble(
                        ("⌁ Indexing", S_INK),
                        f" {folder} for {tool}  ",
                        *meter(share, S_WARN),
                        f"  {done:,} of {total:,} files",
                    )
                await transcript.marker(line, key=key)
            case ProcessCheck(verdicts=verdicts):
                # System 1's look at what runs, as it happens: the "are you stuck?" a user no longer has to ask. Its
                # judgement, as its other cards read, not the command again: that is named only when several run.
                for v in verdicts:
                    line = look_card(v, named=len(verdicts) > 1)
                    if v.told:
                        await transcript.marker(line, classes="decision told")
                    else:  # a routine look updates its command's line: one line a command, not one a look
                        await transcript.marker(line, key=f"look:{v.id}", classes="decision")
            case Nudge(reason="process_check" | "process_look"):
                pass  # the check above already showed what the agent is told
            case Nudge(reason="process_waiting"):
                await transcript.notice("⚑ Told the agent to answer what does not wait on the job, instead of waiting")
            case ProcessEvent(
                action="ended", process=ProcessInfo(kind="call", state="moved", command=command, note=note)
            ):
                # Why a command the agent ran went on in the background: System 1, the timer, a rule, your message.
                # The note is the agent's too, so it keeps its own words; on screen the decision model is named so.
                said = (note or "moved").replace("System 1", "the decision model")
                await transcript.notice(f"⇢ `{brief(command)}` went on in the background: {said}")
            case Nudge(reason="process_ended", text=text):
                # What the agent is told: each process in a few words and how it ended, not the output it quotes.
                for line in ended_lines(text):
                    await transcript.notice(f"⚑ {line}")
            case Nudge(reason=reason, text=text):
                await transcript.notice(f"⚑ Nudge ({reason}): {text}", "warn")
                if isinstance(row, TurnJudgement):
                    await self._show_verdict(row)
            case Reduced(applied=True, source_chars=source, receipt_chars=receipt, items=items):
                await transcript.marker(
                    Content.assemble(
                        ("⌁ Receipt", S_INK),
                        f": {source:,} chars of output became {receipt:,} ({items} items of evidence)",
                    )
                )
            case ToolGuard(layer="system1") if isinstance(row, GuardJudgement):
                await self._show_guard(row)
            case ToolGuard(call_id=call_id) if isinstance(row, RuleRefusal):
                if (block := transcript.tool(call_id)) is not None:
                    await block.judged(rule_card(row), refused=True)
                else:
                    await transcript.marker(rule_card(row), classes="decision refused")
            case ToolGuard(name=name, reason=reason):
                await transcript.notice(f"⊘ {name} refused by the guard: {reason}", "warn")
            case SecretUnavailable(message=message):
                await transcript.notice(f"Secret not loaded: {message}", "warn")
            case WindowFallback(message=message):
                await transcript.notice(f"Window not known: {message}", "warn")
            case FreeNotice(kind=kind, text=text):
                await transcript.notice(f"◇ {text}", "warn" if kind == "low" else "ok")
            case HookError(hook=hook, extension=extension, error=error):
                await transcript.notice(f"Extension {extension} failed in {hook}: {error}", "error")
            case Handoff(target=target) if isinstance(row, HandoverJudgement):
                name = model_label(target, self.status.labels)
                await transcript.hand_over(handoff_card(row, name))
                transcript.working("Efficient model", name)
                self.status.handed = True
                self.status.refresh_line()
            case SubagentStart(name=name, tools=tools):
                await transcript.notice(f"Subagent {name} started with {', '.join(tools) or 'no tools'}", "info")
            case SubagentEnd(name=name, status=status, turns=turns):
                kind = "ok" if status == "completed" else "warn"
                await transcript.notice(f"Subagent {name}: {status}, {turns} turns", kind)
            case RunEnd(reason=reason, detail=detail, error=error, turns=turns, usage=usage, system1=system1):
                await self._hand_back()
                await transcript.finish_reply()
                transcript.run_ended()
                spent2, spent1 = self.run_spent  # what went uncounted: calls with no event of their own (code search)
                self._spend(Usage(cost_usd=max(usage.cost_usd - spent2, 0.0)), max(system1.cost_usd - spent1, 0.0))
                self.status.running = self.session is not None and self.session.running
                if reason in FAILED:
                    self.status.face("failed", FAILED_FACE_S)
                else:
                    self.status.face("idle")
                if error is not None and error.code in FREE_FINAL:
                    line = free_refusal(error)
                    if error.next:
                        name = model_label(error.next, self.status.labels)
                        line += f"; your next message runs on your keys with {name}"
                else:
                    line = REASONS.get(reason, reason) + (f": {detail}" if detail else "")
                line += (
                    f" · {turns} turns · {tokens_text(usage.input_tokens)} in / {tokens_text(usage.output_tokens)} out"
                )
                if system1.asks or system1.failed:
                    line += f" · decision model {system1.asks} asks"
                    if system1.failed:
                        line += f", {system1.failed} failed"
                if refusals := self.ledger.current.refusals:
                    line += f" · the guard refused {refusals} call{'s' if refusals > 1 else ''}"
                if reason == "waiting":
                    # The agent handed over: you have the session; its job's end, or System 1, brings it back.
                    commands = (detail or "").removeprefix("waiting on ").strip("`").split("`, `")
                    waited = ", ".join(f"`{brief(command)}`" for command in commands)
                    await transcript.marker(
                        Content.assemble(
                            ("⏸ ", S_INK),
                            f"Waiting on {waited}: you get the result when it ends; the decision model looks at it "
                            "meanwhile.",
                        )
                    )
                else:
                    style = S_OK if reason == "completed" else S_WARN
                    await transcript.marker(Content.assemble(("■ ", style), line))
            case _:
                pass
        if isinstance(row, CompactionJudgement):
            # In the transcript: the context it cut and the reads System 1 kept; on System 1's panel, a row.
            key = f"compaction:{run}:{row.turn}"
            await transcript.compaction(row, key, self.status.window, self._cwd, live=not self._replaying)
        self.status.free = self._agent_meter()  # each response's headers move the meter
        self.status.refresh_line()

    async def _hand_back(self) -> None:
        """A run that handed over has ended: the next message is the frontier model's again, and the transcript says
        so."""
        self.status.handed = False
        ref = self.session.deps.system2.ref if self.session is not None else self.status.system2
        back = model_label(ref, self.status.labels)
        if self.transcript.handed:
            line = f"⇠ The frontier model ({back}) takes the next message"
            await self.transcript.hand_back(Content.assemble((line, S_MUTED)))
        self.transcript.working(self._role(), back)
        self.status.refresh_line()

    async def _show_verdict(self, j: TurnJudgement) -> None:
        """The supervisor's card at the turn boundary; redrawn when a nudge follows it."""
        key = f"s1:{self.run_count}:{j.turn}"
        await self.transcript.marker(supervisor_card(j), key=key, classes="decision")

    async def _show_guard(self, g: GuardJudgement) -> None:
        """System 1's judgement of a tool call, under the call it concerns: the latest running call of that tool,
        since the judgement names the tool, not the call."""
        if (block := self.transcript.running_tool(g.tool)) is not None:
            await block.judged(guard_card(g), refused=g.refused)
        else:
            await self.transcript.marker(guard_card(g), classes="decision refused" if g.refused else "decision")

    # -- the prompt line -------------------------------------------------------------------------------------

    @on(TextArea.Changed, "#prompt")
    def _on_prompt_changed(self, message: TextArea.Changed) -> None:
        """Images the prompt names show on its border, so a dropped one reads as attached."""
        sees = self.session is None or self.session.deps.system2.profile.vision
        message.text_area.border_subtitle = attachments(images_in(message.text_area.text), sees)
        for page in self.query(Welcome):
            page.typing(bool(message.text_area.text.strip()))

    @on(Prompt.Submitted, "#prompt")
    async def _on_prompt(self, message: Prompt.Submitted) -> None:
        text = message.value.strip()
        message.prompt.clear()
        if not text:
            return
        if COMMAND.match(text):
            await self._command(text)
        else:
            await self._submit(text)  # a path is not a command: a dropped file's lands at the start

    async def _submit(self, text: str) -> None:
        session = self.session
        if session is None:
            hint = "the session is still opening; try again in a moment" if self._opening else "no session"
            await self.transcript.notice(f"Not sent: {hint}.", "warn")
            return
        if session.running:
            try:
                session.steer(text)
            except SessionBusy as e:  # compacting: nothing to steer
                await self.transcript.notice(f"Not sent: {e}.", "warn")
                return
            self._queued.append((text, await self.transcript.user(text, queued=True)))
            return
        await self._begin()
        await self.transcript.user(text)
        try:
            self._run_task = session.start(text)
        except SessionBusy as e:
            await self.transcript.notice(f"Not sent: {e}", "warn")
            return
        self._run_task.add_done_callback(self._run_finished)
        self.status.running = True
        self.status.refresh_line()

    async def _delivered(self, text: str) -> None:
        """A queued message entered the conversation: at the next turn, or, after Esc or a stop, as the prompt of
        the run the session starts with it. Its line moves to where that happened, without the `queued` tag."""
        index = next((i for i, (queued, _) in enumerate(self._queued) if queued == text), None)
        if index is None:
            return  # a prompt `_submit` drew as it started the run
        _, line = self._queued.pop(index)
        await line.remove()
        await self.transcript.user(text)

    async def _compact(self) -> None:
        """/compact: the compaction a run makes near the window's limit, now. It runs in a worker, so System 2's
        summary streams into the compaction's card as at a run's compaction."""
        session = self.session
        if session is None or session.running:
            why = "a run is in progress (Esc aborts it)" if session is not None else "no session yet"
            await self.transcript.notice(f"Not compacted: {why}.", "warn")
            return
        self.status.running, self.status.activity = True, "compacting"
        self.status.refresh_line()
        self.run_worker(self._compacting(session), group="compact", exit_on_error=False)

    async def _compacting(self, session: Session) -> None:
        outcome, why = None, ""
        try:
            outcome = await session.compact()
        except SessionBusy as e:
            why = str(e)
        finally:
            self.status.running, self.status.activity = session.running, ""
            self.status.refresh_line()
        match outcome:
            case "fits":
                keep = session.deps.system2.profile.thresholds.keep_recent
                window = session.deps.system2.window.working
                await self.transcript.notice(
                    f"Nothing to compact: the whole conversation fits in the most recent {keep:.0%} of the working "
                    f"window ({tokens_text(int(keep * window))} of {tokens_text(window)} tokens), which compaction "
                    "keeps as it is."
                )
            case "stopped":
                self.transcript.compactions_ended()
                await self.transcript.notice("Compaction stopped; nothing was cut.", "warn")
            case None:
                self.transcript.compactions_ended()
                await self.transcript.notice(f"Not compacted: {why}.", "warn")
            case "compacted":
                pass  # the compaction's own line says what it cut

    def _run_finished(self, task: asyncio.Task[RunOutcome]) -> None:
        if not self.is_running:  # the app exited while the run was being closed
            return
        self.status.running = False
        self.status.refresh_line()
        self.call_later(self._queued.clear)  # after the run's last events: what is still queued was never sent
        if task.cancelled():
            return
        if (error := task.exception()) is not None:
            self.call_later(self.transcript.notice, f"The run failed: {type(error).__name__}: {error}", "error")
            return
        if (ended := task.result().error) is not None and ended.code in FREE_FINAL:
            self.call_later(self._free_ended, ended)

    async def _command(self, text: str) -> None:
        name, _, rest = text.partition(" ")
        rest = rest.strip()
        match resolve_command(name):
            case "/models":
                await self._models()
            case "/frontier":
                await self._pick_or_set("system2", rest)
            case "/decision":
                await self._pick_or_set("system1", rest)
            case "/efficient" | "/handover":
                await self._auto(rest)
            case "/state":
                self.push_screen(StateScreen(self.state_panel.cut))
            case "/capabilities" | "/caps":
                await self._capabilities(rest)
            case "/schedule":
                await self._schedule()
            case "/runner":
                await self._runner(rest)
            case "/secrets":
                await self._secrets(rest)
            case "/setup" | "/login" | "/connect":
                await self._setup_command()
            case "/free":
                await self._free_command(rest)
            case "/thinking":
                await self._thinking(rest)
            case "/compact":
                await self._compact()
            case "/abort":
                self.action_abort()
            case "/clear":
                await self._clear()
            case "/reload":
                await self._reload()
            case "/panels":
                self.action_toggle_panels()
            case ("/dark" | "/light") as look:
                self.pending.dark = look == "/dark"
                self.theme = (DARK_THEME if self.pending.dark else LIGHT_THEME).name
                for page in self.query(Welcome):  # its tips offer the other look now
                    page.show(page.opening)
            case "/help":
                self.push_screen(HelpScreen())
            case "/quit" | "/exit":
                await self.action_quit()
            case _:
                await self.transcript.notice(f"Unknown command {name}; /help lists them.", "warn")

    # -- setup -----------------------------------------------------------------------------------------------

    def _open_setup(self, start: str = "welcome", system2: str = "") -> None:
        """The setup screen (`tui/onboarding.py`), once at a time; what it saves opens the session."""
        if self._setup is None or self._setting_up:
            return
        self._setting_up = True
        screen = OnboardingScreen(
            home=self._secrets_home(),
            env=self._setup.env(),
            start="system1" if start == "system1" else "free" if start == "free" else "welcome",
            system2=system2,
            transport=self._setup.transport,
        )
        self.push_screen(screen, callback=self._set_up)

    def _set_up(self, done: Onboarded | StartedFree | None) -> None:
        self._setting_up = False
        self.run_worker(self._after_setup(done), group="set-model", exit_on_error=False)

    async def _after_setup(self, done: Onboarded | StartedFree | None) -> None:
        if done is None:
            if self.session is None:
                await self.transcript.notice(
                    "No models connected yet. Type /setup whenever you're ready: it takes about a minute.", "ok"
                )
            return
        if isinstance(done, StartedFree):
            await self._on_free_credit("on free credit", Models(**vars(self.pending)))
            return
        assert self._setup is not None
        self._setup.configured(done.setup.keys)
        before = Models(**vars(self.pending))
        self.pending.system2, self.pending.system1 = done.setup.system2, done.setup.system1_choice
        self.pending.auto = done.setup.handoff is not None
        if (caps := self.pending.capabilities) is not None:
            handoff = caps.handoff.model_copy(update={"to": done.setup.handoff})
            self.pending.capabilities = caps.model_copy(update={"handoff": handoff})
        if self._load_catalog is not None:
            self.run_worker(self._fetch_catalog(), name="catalog", exit_on_error=False)
        if self.session is None:
            if await self._open_with(self.pending) and self._first_prompt:
                prompt, self._first_prompt = self._first_prompt, None
                await self._submit(prompt)
            return
        note = f"frontier {done.setup.system2} · decision {done.setup.system1_choice}"
        await self._reopen(note, before, shown=bool(self.query(Welcome)))  # the entering page has the models

    async def _setup_command(self) -> None:
        if self._setup is None:
            await self.transcript.notice("Setup is not available here; set the models in config.toml.", "warn")
            return
        if await self._busy():
            return
        self._open_setup()

    # -- free credit -----------------------------------------------------------------------------------------

    def _free(self) -> FreeSession | None:
        """What the session's systems run on, while it uses the free credit."""
        return self.session.free if self.session is not None else None

    def _meter_left(self, system: System) -> str:
        """`$3.42 of $4.00`: what is left of `system`'s free meter, while it runs on it; empty otherwise."""
        free = self._free()
        meters = free.plan.meters() if free is not None and free.on_free(system) else None
        if meters is None:
            return ""
        meter = getattr(meters, system)
        return f"${max(meter.limit_usd - meter.used_usd, 0):.2f} of ${meter.limit_usd:.2f}"

    def _agent_meter(self) -> str:
        """The masthead's free credit: the agent meter, while the frontier model runs on it."""
        left = self._meter_left("agent")
        return f"free {left}" if left else ""

    def _credit_line(self) -> str:
        """The entering page's line under the models: each meter a system runs on."""
        free = self._free()
        parts = [f"agent {left}"] if (left := self._meter_left("agent")) else []
        if (left := self._meter_left("system1")) and free is not None and (bundle := free.bundle) is not None:
            parts.append(f"{free_names(bundle.system2, bundle.handoff, bundle.system1)['decision']} {left}")
        return " · ".join(["Free credit", *parts]) if parts else ""

    async def _said_locked(self, role: str) -> bool:
        """Whether `role`'s model is fixed because its system runs on free credit, saying why and the way out."""
        free = self._free()
        if free is None or free.locked(role) is None or free.bundle is None:
            return False
        named = free_names(free.bundle.system2, free.bundle.handoff, free.bundle.system1)
        models = f"{named['frontier']}, {named['efficient']} and {named['decision']}"
        what = {"system2": "frontier", "handoff": "efficient", "system1": "decision"}[role]
        await self.transcript.notice(
            f"The {what} model is fixed: this session runs on free credit, with {models}. To pick models, use your "
            "own keys: /free pause.",
            "warn",
        )
        return True

    def _free_env(self) -> Mapping[str, str]:
        return self._setup.env() if self._setup is not None else process_env()

    async def _free_command(self, rest: str) -> None:
        """`/free`: the free credit, and what each model runs on; `/free start|pause|resume|sign-out`."""
        match rest.strip().lower():
            case "" | "status":
                self.run_worker(self._show_free(), group="free", exit_on_error=False)
            case "start":
                if self._setup is None:
                    await self.transcript.notice(f"Sign in from a terminal: {names.NAME} free start.", "warn")
                elif not await self._busy():
                    self._open_setup("free")
            case "pause" | "resume" as verb:
                self.run_worker(self._use_free(verb == "resume"), group="free", exit_on_error=False)
            case "sign-out" | "signout":
                self.run_worker(self._free_sign_out(), group="free", exit_on_error=False)
            case _:
                await self.transcript.notice("/free, or /free start, pause, resume or sign-out.", "warn")

    async def _show_free(self) -> None:
        try:
            found = await report(self._secrets_home(), self._free_env())
        except (httpx.HTTPError, ConnectionError, ConfigError) as error:
            await self.transcript.notice(f"The free credit could not be read: {error}", "warn")
            return
        await self.transcript.marker(self._free_card(found), classes="decision")

    def _free_card(self, found: FreeReport) -> Content:
        """The grant, its meters, what each model runs on now, the hand-over, and what can be done."""
        free, grant = self._free(), found.grant
        if not found.signed_in:
            head = "not signed in"
        elif grant is None:
            head = found.error.message or found.error.code if found.error is not None else "could not be read"
        elif found.use is not True:
            head = f"{grant.state} · paused, on your own keys"
        else:
            head = grant.state
        lines: list[list[tuple[str, str] | str]] = [[("◇ Free credit", S_HEAD), (f" · {head}", S_MUTED)]]
        if grant is not None:
            for label, meter, pays in (
                ("Agent", grant.meters.agent, "the frontier and efficient models"),
                ("Decision", grant.meters.system1, "the decision model"),
            ):
                left = f"${max(meter.limit_usd - meter.used_usd, 0):.2f} of ${meter.limit_usd:.2f} left"
                lines.append([(f"  {label:<10}", S_MUTED), (left, S_INK), (f" · {pays}", S_MUTED)])
            lines.append([])
            bundle = grant.bundle
            named = free_names(bundle.system2, bundle.handoff, bundle.system1)
            labels = self.status.labels
            rows: tuple[tuple[str, System, str, str], ...] = (
                ("Frontier", "agent", named["frontier"], model_label(self._current("system2"), labels)),
                ("Efficient", "agent", named["efficient"], model_label(self.status.handoff, labels) or "off"),
                ("Decision", "system1", named["decision"], model_label(self._current("system1"), labels)),
            )
            for label, system, on_free, own in rows:
                if free is not None and free.on_free(system):
                    said = [(on_free, S_HEAD), (" on free credit", S_OK)]
                else:
                    said = [(own or "none", S_HEAD), (" on your keys", S_MUTED)]
                lines.append([(f"  {label:<10}", S_MUTED), *said])
            lines += [[], *([("  " + line, S_INK)] for line in FREE_ABOUT.format(**named).split("\n"))]
        blocked = free_blocked(found)
        offer: list[tuple[str, str] | str]
        if not found.signed_in and blocked:
            offer = [(blocked, S_WARN)]
        elif not found.signed_in:
            offer = [("/free start", S_HEAD), (" signs in with GitHub or Google: no keys, no card", S_MUTED)]
        elif found.use is True:
            offer = [("/free pause", S_HEAD), (" runs on your own keys, keeping the credit · ", S_MUTED)]
        else:
            offer = [("/free resume", S_HEAD), (" runs on the free credit again · ", S_MUTED)]
        if found.signed_in:
            offer.append(("/free sign-out", S_HEAD))
        lines += [[], ["  ", *offer]]
        return Content("\n").join(Content.assemble(*line) for line in lines)

    async def _use_free(self, use: bool) -> None:
        """`/free pause` or `/free resume`: `[free] use` set, and the session reopened on what it now runs on."""
        if await self._busy():
            return
        env, home = self._free_env(), self._secrets_home()
        if use and not FreeStore(home, env).signed_in(console_of(env)):
            await self.transcript.notice("Not signed in to the free credit: /free start signs in.", "warn")
            return
        before = Models(**vars(self.pending))
        await asyncio.to_thread(set_use, home, use)
        await self._on_free_credit("free credit resumed" if use else "free credit paused: on your own keys", before)

    async def _free_sign_out(self) -> None:
        if await self._busy():
            return
        before = Models(**vars(self.pending))
        revoked = await sign_out(self._secrets_home(), self._free_env())
        said = "Signed out of the free credit." if revoked else "Signed out here; the console did not confirm it."
        await self.transcript.notice(said, "ok")
        await self._on_free_credit("signed out of the free credit", before)

    async def _on_free_credit(self, note: str, before: Models) -> None:
        """Open or reopen the session on what the free credit now says each system runs on. The models picked in the
        session go: on free credit the grant's are fixed, and back on your keys your configuration's apply."""
        self.pending.system2 = self.pending.system1 = None
        if self.session is None:
            await self._open_with(self.pending)
            return
        await self._reopen(note, before, shown=bool(self.query(Welcome)))

    async def _free_ended(self, error: RunError) -> None:
        """A run the free credit ended for good with none of your models to go on: a card saying what ran out and how
        to keep going. With one, the run's stop line says which."""
        if error.next:
            return
        gone = "Free credit doesn't come back. " if error.code in ("free_exhausted", "free_expired") else ""
        card = Content.assemble(
            (f"◇ {free_refusal(error)}\n", S_HEAD),
            (gone, S_INK),
            ("Add your own keys with ", S_INK),
            ("/setup", S_HEAD),
            (" to keep going: this conversation stays, and continues on them.", S_INK),
        )
        await self.transcript.marker(card, classes="decision refused")

    # -- secrets ---------------------------------------------------------------------------------------------

    def _secrets_home(self) -> Path:
        return self._home if self._home is not None else home_dir(process_env())

    async def _secrets(self, rest: str) -> None:
        """`/secrets`: each entry with whether it loads now, and a row to add one; `/secrets set|remove <NAME>`."""
        verb, _, name = rest.partition(" ")
        if verb == "set":
            self.push_screen(SecretForm(name.strip()), callback=self._secret_entered)
            return
        if verb == "remove" and name.strip():
            self.run_worker(self._remove_secret(name.strip()), group="secrets", exit_on_error=False)
            return
        if verb:
            await self.transcript.notice("/secrets, /secrets set <NAME> or /secrets remove <NAME>.", "warn")
            return
        self.run_worker(self._show_secrets(), group="secrets", exit_on_error=False)

    async def _show_secrets(self) -> None:
        try:
            statuses = await asyncio.to_thread(secrets_commands.check, self._secrets_home(), process_env())
        except ConfigError as error:
            await self.transcript.notice(str(error), "warn")
            return
        choices = [
            Choice(
                id=status.name,
                name=status.name,
                group="loads" if status.ok else "does not load",
                note=(status.source if status.ok else status.message.removeprefix(f"{status.name}: "))
                + ("" if status.source == "keychain" else " · edit in config.toml")
                + (" · harness only, not the agent's" if status.harness_only else ""),
            )
            for status in statuses
        ]
        choices.sort(key=lambda choice: choice.group != "does not load")
        choices.append(Choice(id=ADD_SECRET, name="Add a secret…", group="new", note="stored in the OS keychain"))
        hint = "Enter replaces a keychain value, or adds one; values are never shown"
        picker = ModelPicker(
            "Secrets · keys the agent's commands get", choices, "", hint=hint, typed=False, stacked=True
        )
        self.push_screen(picker, callback=self._secret_picked)

    def _secret_picked(self, picked: str | None) -> None:
        if picked:
            self.push_screen(SecretForm("" if picked == ADD_SECRET else picked), callback=self._secret_entered)

    def _secret_entered(self, entered: tuple[str, str] | None) -> None:
        if entered:
            self.run_worker(self._store_secret(*entered), group="secrets", exit_on_error=False)

    async def _store_secret(self, name: str, value: str) -> None:
        try:
            await asyncio.to_thread(secrets_commands.store, name, value, self._secrets_home(), process_env())
        except (ConfigError, ValueError) as error:
            await self.transcript.notice(f"Not stored: {error}", "warn")
            return
        await self.transcript.notice(f"Stored {name} in the keychain. New sessions get it; /reload to use it now.")

    async def _remove_secret(self, name: str) -> None:
        try:
            await asyncio.to_thread(secrets_commands.remove, name, self._secrets_home(), process_env())
        except (ConfigError, ValueError) as error:
            await self.transcript.notice(f"Not removed: {error}", "warn")
            return
        await self.transcript.notice(f"Removed {name}. New sessions no longer get it.")

    # -- capabilities ----------------------------------------------------------------------------------------

    def _capabilities_now(self) -> CapabilitySettings:
        return self.pending.capabilities if self.pending.capabilities is not None else self._capabilities_at_start

    def _profile_packs(self) -> list[str]:
        return list(self.session.deps.system2.profile.tool_packs) if self.session is not None else []

    def _toggles(self, caps: CapabilitySettings) -> list[capability_toggles.Toggle]:
        return capability_toggles.toggles(caps, self._capabilities_at_start, self._profile_packs(), base=True)

    def _capability_choices(self) -> list[Choice]:
        """One row per capability, grouped by whether it is on: the tool packs, the browser, code search, the
        workspace, the memory drive, each MCP connection the config names, the executor backends."""
        caps = self._capabilities_now()
        rows = self._toggles(caps)
        drive = caps.memory.drive or self._memory_at_start or "the drive in the config home"
        rows = [
            row.model_copy(update={"note": f"{row.note} · {drive}"}) if row.id == "cap.memory" else row for row in rows
        ]
        rows.sort(key=lambda row: not row.on)  # what is on first, each group in capability order
        return [
            Choice(id=row.id, name=row.label, group="on" if row.on else "off", label=row.label, note=row.note)
            for row in rows
        ]

    def _capability_key(self, word: str) -> str | None:
        """A typed capability name as a row id: `web-read`, `browser`, `workspace`, `memory`, `guard`,
        `tool-result-guard`, `subagents` or an MCP namespace."""
        word = word.lower().replace("_", "-")
        if word in capability_toggles.PACKS:
            return f"cap.{word}"
        if word == "code-search":
            return "cap.code-search"
        if word in ("browser", "workspace", "memory", "guard", "tool-result-guard", "subagents", "schedule"):
            return f"cap.{word}"
        if any(entry.namespace == word.removeprefix("mcp:") for entry in self._capabilities_at_start.mcp):
            return f"{capability_toggles.MCP_PREFIX}{word.removeprefix('mcp:')}"
        return None

    async def _capabilities(self, rest: str) -> None:
        if rest:
            word, _, value = rest.partition(" ")
            if (key := self._capability_key(word.strip())) is None:
                await self.transcript.notice(f"No capability called {word.strip()!r}; /caps lists them.", "warn")
                return
            # Off the message loop: a reopen waits on the providers, and the screen must keep drawing meanwhile.
            self.run_worker(self._toggle(key, value.strip()), group="capabilities", exit_on_error=False)
            return
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        hint = "Enter switches one on or off; the session reopens on its journal with the change"
        picker = ModelPicker(
            "Capabilities · what the agent can use",
            self._capability_choices(),
            "",
            hint=hint,
            typed=False,
            stacked=True,
        )
        self.push_screen(picker, callback=self._capability_picked)

    def _schedule_extension(self) -> Schedule | None:
        if self.session is None:
            return None
        return next((e for e in self.session.deps.hooks.extensions if isinstance(e, Schedule)), None)

    async def _schedule(self) -> None:
        schedule = self._schedule_extension()
        if schedule is None:
            await self.transcript.notice("Timers are off. Turn them on with /caps schedule.")
            return
        timers = schedule.listing().timers
        if not timers:
            await self.transcript.notice("No timers set.")
            return
        choices = [
            Choice(
                id=t.id,
                name=t.prompt,
                group="timers",
                label=f"{t.id} · {brief(t.prompt)}",
                note=f"{'repeat every ' + str(t.every_s) + 's' if t.every_s is not None else 'once'} · {t.next_run_at}",
            )
            for t in timers
        ]

        def picked(id: str | None) -> None:
            if id is not None:
                result = schedule.cancel(id)
                text = f"Timer {id} stopped" if result.status == "ok" else result.content
                self.call_later(self.transcript.notice, text)

        self.push_screen(
            ModelPicker(
                "Timers",
                choices,
                "",
                hint="Enter stops a timer; Esc closes",
                typed=False,
                stacked=True,
            ),
            callback=picked,
        )

    async def _runner(self, rest: str) -> None:
        """`/runner`: a picker of every runner, the one in force marked, each that cannot run here saying why;
        `/runner <name>` sets one directly."""
        if rest.strip():
            self.run_worker(self._set_runner(rest.strip()), group="capabilities", exit_on_error=False)
            return
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        current = self._capabilities_now().runner.name
        choices = [
            Choice(
                id=choice.id,
                name=choice.name,
                group="in use"
                if choice.id == current
                else ("can run here" if not choice.unavailable else "unavailable"),
                label=choice.name,
                note=choice.unavailable or choice.description,
            )
            for choice in runner_choices(self._capabilities_at_start.runner)
        ]
        choices.sort(key=lambda choice: ("in use", "can run here", "unavailable").index(choice.group))
        hint = "Enter runs the session's commands there; the session reopens on its journal"
        picker = ModelPicker("Runner · where commands run", choices, "", hint=hint, typed=False, stacked=True)
        self.push_screen(picker, callback=self._runner_picked)

    def _runner_picked(self, picked: str | None) -> None:
        if picked:
            self.run_worker(self._set_runner(picked), group="capabilities", exit_on_error=False)

    async def _set_runner(self, name: str) -> None:
        """Run the session's commands on runner `name` and reopen the session on it."""
        caps = self._capabilities_now()
        if name == caps.runner.name:
            await self.transcript.notice(f"Commands already run on {name}.")
            return
        if (reason := runner_problem(name, self._capabilities_at_start.runner)) is not None:
            await self.transcript.notice(f"{reason}. The runner stays {caps.runner.name}.", "warn")
            return
        before = Models(**vars(self.pending))
        runner = runner_config(name, self._capabilities_at_start.runner)
        self.pending.capabilities = caps.model_copy(update={"runner": runner})
        await self._reopen(f"runner {name}", before)

    async def _models(self) -> None:
        """`/models`: the frontier, efficient and decision models, a row each, the efficient one gray while hand-over
        is off; Enter on one opens that model's own picker."""
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        on = self.pending.auto
        thinking = self._thinking_now()
        frontier = self._current("system2") + (f"  thinking {thinking}" if thinking else "")
        off = "hand-over is off: the frontier model runs every turn; Enter picks a model to turn it on"
        group = "hand-over on" if on else "hand-over off"
        choices = [
            Choice("system2", "Frontier", group, f"Frontier  {frontier}", "starts each task and does the hard part"),
            Choice(
                "handoff",
                "Efficient",
                group,
                f"Efficient {self._handoff_model()}" + ("" if on else "  off"),
                "finishes a run once the decision model judges it can" if on else off,
                dim=not on,
            ),
            Choice(
                "system1",
                "Decision",
                group,
                f"Decision  {self._current('system1')}",
                "watches the run, guards risky calls and picks the moment to hand over",
            ),
        ]
        picker = ModelPicker("Models", choices, "", hint="Enter opens that model's picker", typed=False, stacked=True)
        self.push_screen(picker, callback=self._model_row_picked)

    def _model_row_picked(self, picked: str | None) -> None:
        match picked:
            case "system2" | "system1":
                self.call_later(self._pick_or_set, picked, "")
            case "handoff":
                self.call_later(self._auto, "")

    def _handoff_model(self) -> str:
        """The model auto mode hands over to: the one named, else the default."""
        return self._capabilities_now().handoff.to or DEFAULT_TARGET

    async def _auto(self, rest: str) -> None:
        """`/efficient` (`/handover`): a picker of the models a run can hand over to, the one in use marked, with a
        row to turn auto mode off; `/efficient on`, `/efficient off` or `/efficient <provider/model>` sets it
        directly."""
        if await self._said_locked("handoff"):
            return
        if rest:
            self.run_worker(self._set_auto(rest), group="capabilities", exit_on_error=False)
            return
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        off = "the frontier model runs every turn"
        choices = [Choice(id=AUTO_OFF[0], name="off", group="auto mode", label="off", note=off)]
        hint = "Enter picks the model a run hands over to, or off; the session reopens on its journal"
        target = self._handoff_model() if self.pending.auto else ""
        models = list(self.catalog.system2) if self.catalog is not None else []
        frontier = self._current("system2")
        choices += [system2_choice(m) for m in offered(models, "efficient", target) if m.id != frontier]
        current = target or AUTO_OFF[0]
        picker = ModelPicker("Efficient model · finishes a run after the hard part", choices, current, hint=hint)
        self.push_screen(picker, callback=self._auto_picked)

    def _auto_picked(self, picked: str | None) -> None:
        if picked:
            self.run_worker(self._set_auto(picked), group="capabilities", exit_on_error=False)

    async def _set_auto(self, value: str) -> None:
        """Auto mode off, on (with the hand-over model as it was), or on with `value` as that model; the session
        reopens on it."""
        caps = self._capabilities_now()
        on = value.lower() not in AUTO_OFF
        named = value if on and value.lower() != "on" else None
        target = named or self._handoff_model()
        if on == self.pending.auto and (not on or target == self._handoff_model()):
            await self.transcript.notice(
                f"The efficient model is already {target}." if on else "The efficient model is already off."
            )
            return
        if named is not None and named == self._current("system2"):
            await self.transcript.notice(f"{named} is the frontier model already: pick a cheaper one.", "warn")
            return
        before = Models(**vars(self.pending))
        if named is not None:
            try:
                handoff = HandoffConfig.model_validate({**caps.handoff.model_dump(), "to": named})
            except ValidationError:
                await self.transcript.notice(f"Not a model: {named!r}; an efficient model is provider/model.", "warn")
                return
            self.pending.capabilities = caps.model_copy(update={"handoff": handoff})
        self.pending.auto = on
        note = f"efficient model {target}" if on else "efficient model off"
        await self._reopen(note, before, shown=bool(self.query(Welcome)))  # the entering page has the hand-over

    def _capability_picked(self, picked: str | None) -> None:
        if picked:
            self.run_worker(self._toggle(picked, ""), group="capabilities", exit_on_error=False)

    async def _toggle(self, key: str, value: str) -> None:
        """Flip one capability in the picks and reopen the session on them. One switched on first gets what it
        needs installed, when this environment lacks it (`_install_needs`)."""
        if self._installing:
            await self.transcript.notice("An install is still running; switch capabilities once it is done.", "warn")
            return
        caps = self._capabilities_now()
        toggle = next((row for row in self._toggles(caps) if row.id == key), None)
        if key == "cap.memory" and value:
            memory = caps.memory.model_copy(update={"enabled": True, "drive": _drive(value)})
            caps, note = caps.model_copy(update={"memory": memory}), f"memory drive {value}"
        elif toggle is not None:
            on = not toggle.on
            if on and not await self._install_needs(key):
                return
            caps = self._capabilities_now()  # as they are now: a /runner pick may have come during the install
            caps = capability_toggles.flip(caps, self._capabilities_at_start, key, on, self._profile_packs())
            note = _switched(key, on)
        else:
            await self.transcript.notice(f"No capability called {key!r}; /caps lists them.", "warn")
            return
        before = Models(**vars(self.pending))
        self.pending.capabilities = caps
        # The entering page lists the capabilities, so a switch shows there: it needs no line of its own.
        await self._reopen(note, before, shown=bool(self.query(Welcome)))

    async def _install_needs(self, key: str) -> bool:
        """Install what the capability behind `key` needs and this environment lacks: its extra's packages, then its
        setup (the browser's Chromium). False when the install failed, said why, or cannot start now."""
        needs = capability_needs(key)
        if needs is None or self._install is None:
            return True
        if await self._busy():
            return False
        self._installing = True
        try:
            await self._install(needs, self.transcript.notice)
        except InstallFailed as error:
            await self.transcript.notice(f"{str(error).rstrip('.')}. The capability stays off.", "error")
            return False
        finally:
            self._installing = False
        return True

    async def _busy(self) -> bool:
        """Whether the session cannot reopen now (a run is going, or it is still opening), saying so."""
        session = self.session
        if session is not None and session.running:
            await self.transcript.notice(
                "Wait for the run to finish (Esc aborts it) before changing capabilities.", "warn"
            )
            return True
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return True
        return False

    async def _reopen(self, note: str, before: Models, *, shown: bool = False) -> None:
        """Open the session again on the picks made so far and on the same journal, keeping the screen. When
        it cannot open on them, the reason is shown and the session comes back on the picks as they were. `shown`:
        the screen shows the change itself, so only a failure is said."""
        session = self.session
        if await self._busy():
            return
        if not shown:
            await self.transcript.notice(f"{note}; reopening the session…", "ok")
        if session is not None:
            before.session_file = self.pending.session_file = session.deps.journal.path
            self.session = None
            self._opening = True
            self._refresh_status()
            await self.views.close()
            schedule = next((e for e in session.deps.hooks.extensions if isinstance(e, Schedule)), None)
            self.pending.timers_stopped += len(schedule.listing().timers) if schedule is not None else 0
            await session.aclose()
        if await self._resume():
            if not shown:
                await self._reopened(note)
            return
        if session is None:
            return
        before.timers_stopped = self.pending.timers_stopped
        self.pending = before
        await self.transcript.notice("Back on the capabilities as they were.", "warn")
        if await self._resume():
            await self._reopened("as before")

    async def _reopened(self, note: str) -> None:
        """Say the session is back, and what the agent has now."""
        session = self.session
        if session is None:
            return
        tools = [tool.name for tool in session.deps.tools]
        line = f"Session reopened · {note} · tools: {', '.join(tools)}"
        if "decision_code" in tools:
            line += " · decision_code indexes the working directory before the first turn"
        await self.transcript.notice(line, "ok")

    async def _resume(self) -> bool:
        """Open on the picks, the conversation already on the screen."""
        self._resuming = True
        try:
            return await self._open_with(self.pending)
        finally:
            self._resuming = False

    # -- clearing --------------------------------------------------------------------------------------------

    async def _reload(self) -> None:
        """Quit, and have `main` start the process again on the same command line, resuming this session on its
        journal with the picks made here: new code, config and keys take effect without losing the
        conversation."""
        session = self.session
        if session is not None and session.running:
            await self.transcript.notice("Wait for the run to finish (Esc aborts it) before reloading.", "warn")
            return
        if session is not None and session.deps.journal.path is None:
            await self.transcript.notice(
                "Not reloaded: with --no-session the journal is in memory only, so the conversation would be lost.",
                "warn",
            )
            return
        picks = Models(**vars(self.pending))
        if session is not None:
            picks.system2, picks.system1 = session.deps.system2.ref, session.system1
            picks.session_file, picks.fresh = session.deps.journal.path, False
        schedule = self._schedule_extension()
        picks.timers_stopped += len(schedule.listing().timers) if schedule is not None else 0
        picks.entering = self._entering  # back on the page it was on
        self.reload = picks
        await self.action_quit()

    async def _clear(self) -> None:
        """Start over: the screen as at the start, the session closed and a new one opened on a fresh journal,
        with the models and capabilities as they are. The journal so far stays on disk, to resume with
        --session."""
        session = self.session
        if session is not None and session.running:
            await self.transcript.notice("Wait for the run to finish (Esc aborts it) before clearing.", "warn")
            return
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        before = Models(**vars(self.pending))
        kept: Path | None = None
        if session is not None:
            # The picks made in place (/frontier, /decision) carry over: a fresh journal has no record of them.
            kept = before.session_file = session.deps.journal.path
            self.pending.system2, self.pending.system1 = session.deps.system2.ref, session.system1
            self.session = None
            self._opening = True
            await self.views.close()
            schedule = next((e for e in session.deps.hooks.extensions if isinstance(e, Schedule)), None)
            self.pending.timers_stopped += len(schedule.listing().timers) if schedule is not None else 0
            await session.aclose()
        self.pending.session_file, self.pending.fresh = None, True
        await self._reset_screen()
        if await self._open_with(self.pending):
            self.pending.fresh = False  # a later reopen takes its journal from the session
            where = f" · the conversation so far is kept in {_short(kept)}" if kept is not None else ""
            await self.transcript.notice(f"Session cleared{where}", "ok")
            return
        if session is None:
            return
        before.timers_stopped = self.pending.timers_stopped
        self.pending = before
        await self.transcript.notice("Back on the session as it was.", "warn")
        await self._open_with(self.pending)

    async def _reset_screen(self) -> None:
        """The screen as at the start: the cube, the State placeholder, an empty System 1 panel, zero counters."""
        self.ledger = Ledger()
        self.run_count = 0
        self.status.context = 0
        self.status.cost2 = self.status.cost1 = 0.0
        self.status.unpriced = False
        self.status.prompt2 = self.status.cached2 = 0
        self.status.cache_reported = False
        self.status.running = False
        self.status.activity = ""
        await self.transcript.clear()
        await self.state_panel.clear()
        self._feed()
        await self._enter()
        self._refresh_status()

    # -- models ----------------------------------------------------------------------------------------------

    def _current(self, role: Role) -> str:
        session = self.session
        if session is None:
            return (self.pending.system2 if role == "system2" else self.pending.system1) or ""
        return session.deps.system2.ref if role == "system2" else session.system1

    def _choices(self, role: Role) -> list[Choice]:
        """The picker's rows: the frontier models of the catalog (`offered`), the current one among them; every
        decision model."""
        current = self._current(role)
        if role == "system2":
            models = list(self.catalog.system2) if self.catalog is not None else []
            choices = [system2_choice(m) for m in offered(models, "frontier", current)]
        else:
            models = list(self.catalog.system1) if self.catalog is not None else self._system1_choices
            choices = [system1_choice(m) for m in models]
            if current and all(c.id != current for c in choices):
                choices.insert(0, system1_choice(ModelInfo(id=current, name=current)))
        return choices

    async def _pick_or_set(self, role: Role, value: str) -> None:
        if await self._said_locked(role):
            return
        if value:
            self.run_worker(self._set_model(value, role), group="set-model", exit_on_error=False)
            return
        if self._opening:
            await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
            return
        current = self._current(role)
        if role == "system2":
            title = "Frontier model · starts each task and does the hard part"
            hint = ""
        else:
            title = "Decision model · picks the moment to hand over and keeps the work on track"
            hint = "decisions:<model> picks a decisions-API model"
        if self.session is None:
            hint = "No session yet: the pick opens one" + (f" · {hint}" if hint else "")
        if self.catalog is None and self._load_catalog is not None:
            hint = "The catalog is still loading; " + hint[0].lower() + hint[1:]
        thinking = self._thinking_now() if role == "system2" else None
        levels: tuple[str, ...] = ()
        if role == "system2":  # the current model's run; the whole ladder when it takes none, for the pick
            binding = self.session.deps.system2 if self.session is not None else None
            levels = (binding.effort_levels if binding is not None else ()) or EFFORT_LEVELS
        picker = ModelPicker(title, self._choices(role), current, hint=hint, levels=levels, thinking=thinking)
        self.push_screen(
            picker,
            callback=lambda picked: self._picked(picked, role, picker.thinking if picker.thinking_touched else None),
        )

    def _picked(self, picked: str | None, role: Role, thinking: str | None = None) -> None:
        if not picked:
            return
        if thinking and self.session is not None and picked == self.session.deps.system2.ref:
            self.run_worker(self._thinking(thinking), group="set-model", exit_on_error=False)  # the level alone
            return
        self.run_worker(self._set_model(picked, role, thinking), group="set-model", exit_on_error=False)

    async def _set_model(self, ref: str, role: Role, thinking: str | None = None) -> None:
        session = self.session
        if thinking:
            self.pending.thinking = thinking
        if session is None:
            if self._opening:
                await self.transcript.notice("The session is still opening; try again in a moment.", "warn")
                return
            # No session yet (nothing configured, or it could not open): the pick is what it opens on.
            if role == "system2":
                self.pending.system2 = ref
            else:
                self.pending.system1 = ref
            await self.transcript.notice(f"{ROLE_NAMES[role]} → {ref}; opening the session…", "ok")
            await self._open_with(self.pending)
            return
        if session.running:
            await self.transcript.notice("Wait for the run to finish (Esc aborts it) before changing models.", "warn")
            return
        try:
            binding = await session.set_model(ref, role)
        except (ConfigError, ValueError, KeyError, SessionBusy) as e:
            await self.transcript.notice(f"Cannot set the {ROLE_NAMES[role].lower()} to {ref}: {e}", "error")
            return
        level = lacking = ""
        if role == "system2" and binding is not None:
            lacking = self._keep_thinking(binding)
            if binding.takes_effort:
                level = f" · thinking {binding.profile.reasoning_effort or THINKING_DEFAULT}{lacking}"
            elif self.pending.thinking:  # a level was chosen, and this model has none: it goes without
                level = " · it takes no thinking level"
        self._refresh_status()
        if self.query(Welcome) and not lacking:
            return  # The entering page has each model on its card: the pick needs no line of its own.
        if role == "system2" and binding is not None:
            window = tokens_text(binding.window.working)
            await self.transcript.notice(
                f"Frontier model → {binding.ref} (working window {window} tokens){level}", "ok"
            )
        else:
            await self.transcript.notice(f"Decision model → {ref}", "ok")

    # -- thinking --------------------------------------------------------------------------------------------

    def _thinking_now(self) -> str | None:
        """The level in force, `default` for the model's own; None without a session."""
        if self.session is None:
            return None
        return self.session.deps.system2.profile.reasoning_effort or THINKING_DEFAULT

    def _apply_thinking(self, binding: ModelBinding, level: str) -> None:
        binding.profile = binding.profile.model_copy(update={"reasoning_effort": level})

    def _keep_thinking(self, binding: ModelBinding) -> str:
        """The level chosen stays across models: apply it to a binding whose model takes it. A word for the notice
        when the model takes levels, but not that one; it keeps its own, and the choice waits for a model that does."""
        kept = self.pending.thinking
        if kept is None or not binding.takes_effort:
            return ""
        if kept in thinking_levels(binding):
            self._apply_thinking(binding, kept)
            return ""
        return f" ({kept} is not a level it takes)"

    async def _thinking(self, level: str) -> None:
        """Set the thinking level, or say what it is. It applies from the next request, during a run too."""
        session = self.session
        if session is None:
            await self.transcript.notice("No session yet; the level is set once one is open.", "warn")
            return
        binding = session.deps.system2
        if not binding.takes_effort:
            why = (
                "its API refuses one for this model"
                if binding.profile.effort_levels == []
                else "its profile names none, and its API is not asked for one"
            )
            await self.transcript.notice(f"{binding.ref} takes no thinking level: {why}.", "warn")
            return
        current, levels = binding.profile.reasoning_effort or THINKING_DEFAULT, thinking_levels(binding)
        if not level:
            await self.transcript.notice(
                f"Thinking is {current}; /thinking {'|'.join(levels)} sets it, Shift+Tab cycles it."
            )
            return
        if level not in levels:
            await self.transcript.notice(f"/thinking takes {', '.join(levels)} (now: {current}).", "warn")
            return
        self._apply_thinking(binding, level)
        self.pending.thinking = level
        self._refresh_status()
        if not self.query(Welcome):  # the entering page has the level on System 2's card
            await self.transcript.notice(f"Thinking → {level}", "ok")

    async def action_cycle_thinking(self) -> None:
        """Shift+Tab: the next level round, from the model's own to low, past the highest to low again."""
        if isinstance(self.screen, ModalScreen):
            raise SkipAction()  # a picker has its own; the State screen none
        session = self.session
        if session is None or not session.deps.system2.takes_effort:
            await self._thinking(EFFORT_LEVELS[0])  # says why it cannot
            return
        levels, current = thinking_levels(session.deps.system2), session.deps.system2.profile.reasoning_effort
        index = levels.index(current) if current in levels else -1
        await self._thinking(levels[(index + 1) % len(levels)])

    # -- actions ---------------------------------------------------------------------------------------------

    def action_abort(self) -> None:
        session = self.session
        if session is not None and session.running:
            session.abort()
            self.call_later(self.transcript.notice, "Aborting the run…", "warn")
        else:
            self.query_one("#prompt", Prompt).clear()

    def action_toggle_output(self) -> None:
        self.transcript.toggle_last_tool()

    def action_scroll_transcript(self, direction: int) -> None:
        """Page the transcript from the keyboard; the prompt keeps the focus."""
        if isinstance(self.screen, ModalScreen):
            raise SkipAction()  # a picker pages its own list
        if direction < 0:
            self.transcript.scroll_page_up()
        else:
            self.transcript.scroll_page_down()

    # -- the clipboard ---------------------------------------------------------------------------------------

    def copy_to_clipboard(self, text: str) -> None:
        """The terminal's clipboard through OSC 52, as Textual does, and the machine's own as well."""
        super().copy_to_clipboard(text)
        self._copy(text)

    def on_paste(self, event: events.Paste) -> None:
        """A paste the prompt did not take: nothing had the focus, as when a screenshot is dragged in from another
        app (the terminal lost the focus, and Textual drops it until a click) or a click moved it. It goes to the
        prompt, so a drop is never lost. A picker's own search box keeps what is pasted into it."""
        if isinstance(self.screen, ModalScreen) or isinstance(self.focused, (Input, TextArea)):
            return
        event.stop()
        self.query_one("#prompt", Prompt).take_paste(event.text)

    def on_key(self, event: events.Key) -> None:
        """A character typed while nothing that writes has the focus, as after a click on the entering page's
        capabilities to scroll them: the prompt takes the focus back, and the character with it."""
        if (
            isinstance(self.screen, ModalScreen)
            or isinstance(self.focused, (Input, TextArea))
            or not event.is_printable
        ):
            return
        if event.character:
            prompt = self.query_one("#prompt", Prompt)
            prompt.focus()
            prompt.insert(event.character)
            event.stop()

    def on_text_selected(self, event: events.TextSelected) -> None:
        """A drag that selected text has ended: it is copied there and then, as a terminal's own selection
        is; ctrl+c (cmd+c) copies it again."""
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.status.flash(f"copied {len(text):,} characters")

    def action_toggle_panels(self) -> None:
        side = self.query_one("#side")
        side.display = not side.display

    async def action_quit(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            self.journal_path = session.deps.journal.path
            with contextlib.suppress(Exception):
                async with asyncio.timeout(10):
                    await session.aclose()
        self.exit()


def main(options: Options, env: Mapping[str, str] | None = None, prompt: str | None = None) -> None:
    """Run the TUI on a session built the way the CLI builds one; a resumed journal's models win over config."""
    reloaded = os.environ.pop(RELOAD_ENV, None)
    environment = process_env() if env is None else dict(env)
    bootstrap = Bootstrap(options, environment)
    picks = PICKS.validate_json(reloaded) if reloaded else Models(auto=bootstrap.config.auto)
    current = [bootstrap]
    """The Bootstrap the catalog and the default are read from, made again once setup adds keys."""

    def configured(keys: Mapping[str, str]) -> None:
        environment.update(keys)
        current[0] = Bootstrap(options, environment)

    async def catalog() -> Catalog:
        return await current[0].catalog()

    async def known_system2() -> str | None:
        return await current[0].known_system2()

    async def open_session(emit: EventSink, picks: Models) -> Session:
        opened = options
        journal = picks.session_file or (None if picks.fresh else options.session_file)
        if journal is not None and journal.is_file() and options.system2 is None:
            system2 = Journal(cwd=str(options.cwd), path=journal).last_models().get("system2")
            opened = options.model_copy(update={"system2": system2, "system2_picked": False}) if system2 else options
        picked: dict[str, object] = {k: v for k, v in (("system2", picks.system2), ("system1", picks.system1)) if v}
        caps = picks.capabilities
        if picks.auto:
            picked["handoff_default"] = DEFAULT_TARGET
        else:  # off: System 2 runs every turn, whatever model the config names for the hand-over
            caps = caps if caps is not None else current[0].capability_settings()
            caps = caps.model_copy(update={"handoff": caps.handoff.model_copy(update={"to": None})})
        if caps is not None:
            # The picked object already holds --memory, --no-memory and --handoff-to, folded in at start.
            picked |= {"capabilities": caps, "memory": None, "memory_off": False, "handoff_to": None}
        if picks.session_file is not None or picks.fresh:
            picked["session_file"] = picks.session_file  # None after /clear: a new journal
        picked["tui"] = True
        delivery = TimerDelivery()

        def stream(event: Event) -> None:
            delivery.observe(event)
            emit(event)

        session = await Bootstrap(opened.model_copy(update=picked), environment).session(stream)
        delivery.session = session
        for extension in session.deps.hooks.extensions:
            if isinstance(extension, Schedule):
                extension.fire = delivery.fire
        # The user watches this session: a job's end starts a run that tells the agent, as it happens.
        session.job_notices = "wake"
        return session

    app = HarnessApp(
        open_session,
        welcome=options.session_file is None,  # a journal to resume goes straight to its conversation
        catalog=catalog,
        system1_choices=system1_choices((), (), bootstrap.config.system1.models),
        first_prompt=None if reloaded else prompt,  # sent before the reload; its journal has it
        default_system2=known_system2,
        capabilities=bootstrap.capability_settings(),
        memory=bootstrap.memory_path(),
        home=bootstrap.home,
        picks=picks,
        reloaded=reloaded is not None,
        install=install,
        setup=SetupHooks(env=lambda: environment, configured=configured),
    )
    # Index builds and OCR run in spawned worker processes. The first spawn starts multiprocessing's resource tracker
    # with sys.stderr's descriptor, which is -1 while the app runs (Textual's capture), and fails ("bad value(s) in
    # fds_to_keep"); started now, it gets the terminal's.
    resource_tracker.ensure_running()
    app.run()
    if app.reload is not None:
        # The same interpreter and command line, so an editable install or a reinstall runs its new code.
        try:
            os.execve(sys.executable, sys.orig_argv, {**os.environ, RELOAD_ENV: PICKS.dump_json(app.reload).decode()})
        except OSError:
            sys.stdout.write(ALT_SCREEN_OFF + SHOW_CURSOR)  # kept for the restart that did not happen
            raise
    if app.journal_path is not None and app.journal_path.is_file():
        print(f"Resume this session: {names.NAME} --session {_short(app.journal_path)}")

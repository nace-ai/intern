"""The setup screen: what `intern` opens on when it has no models to run on, and what `/setup` brings back.

                         ▗▄▄ INTERN ▄▄▖            (the wordmark)
            ✓ Welcome ── ● How to run ── ○ Sign in ── ○ Ready
    ╭────────────────────────────────────────────────────────────╮
    │ How do you want to run Intern?                             │
    │ ● Start free                              recommended      │
    │     Free credit on Nace's models. No keys, no card.        │
    │     Sign in with GitHub or Google to claim it, once.       │
    │   Use my own keys                                          │
    │     Any provider; pick your models. No sign-in.            │
    ╰────────────────────────────────────────────────────────────╯
                          Enter continues · Esc goes back

Intern runs on three models: a frontier model that starts each task and does the hard part, an efficient model that
takes over for the rest, and a decision model that picks the moment and keeps the run on track. (The code calls them
System 2, the hand-over target and System 1.) The screen says what Intern is, then asks how to run it.

**Start free** shows what Nace sees on free credit (`onboarding.CONSENT`), then signs in with the Nace console's
device flow (`free_commands.claim`, as `intern free start --accept` does, with the consent's version): the page opens
in the browser, the screen shows its address and the code, and waits; Esc stops it. Signed in, the last step names
the three models the free credit runs on, explains the hand-over (`onboarding.FREE_ABOUT`) and says the credit, and
the screen dismisses with `StartedFree`. Already signed in, it picks the free credit up again with no sign-in. It is
not offered while the console takes no sign-ins, while free calls are paused, or once the credit is spent, and the
step says why; a refused sign-in says why in plain words and offers the user's own keys on the same screen.

**Use my own keys** asks where the frontier model runs (OpenRouter first, signed in with the browser or a pasted key;
any other provider or a local server too; a key is checked before it is kept) and offers every model it lists, its
frontier models first; then the efficient model on a provider of its own, the frontier's or another, its efficient
models first, or no efficient model (`SKIP`: every turn runs on the frontier model); then the decision model, by
where it runs. Nothing on it is an error to read and give up on: a refused key, a server that does not answer or a
subscription Intern cannot sign in with each says what to do instead, on the step it happened. The last step saves
(`onboarding.save`) and the screen dismisses with what it saved, for the app to open the session on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Literal

import httpx
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from intern import names
from intern.free_commands import FreeReport, Granted, claim, report, set_use
from intern.models.registry import PROVIDERS, ProviderSpec
from intern.nace_free import FreeGrant, Refusal, SignInCode
from intern.onboarding import (
    CONSENT,
    CONSENT_VERSION,
    FREE_ABOUT,
    HANDOFF_OWN_KEYS,
    PROVIDER_BY_ID,
    SYSTEM1_BY_ID,
    SYSTEM1_OPTIONS,
    SYSTEM2_PROVIDERS,
    KeyCheck,
    OpenRouterSignIn,
    ProviderOption,
    Role,
    Saved,
    Setup,
    SignInFailed,
    System1Option,
    check_system1,
    check_system2,
    efficient_models,
    free_names,
    key_found,
    model_name,
    offered_models,
    save,
)
from intern.secret_store import MIN_SECRET_CHARS
from intern.tui.pickers import Choice, fit, ranked
from intern.tui.theme import BRAND, S_HEAD, S_INK, S_MUTED, S_MUTED_ITALIC, S_OK, S_OK_BOLD, S_WARN
from intern.tui.welcome import Mascot, wordmark

Step = Literal[
    "welcome",
    "how",
    "consent",
    "free-sign-in",
    "refused",
    "free-done",
    "provider",
    "unavailable",
    "address",
    "key",
    "frontier",
    "efficient",
    "decision",
    "decision-key",
    "decision-model",
    "done",
]
Way = Literal["free", "own"]
"""How Intern runs: on the free credit, or on the user's own keys."""
Stages = tuple[tuple[str, tuple[Step, ...]], ...]
FREE_STAGES: Stages = (
    ("Welcome", ("welcome",)),
    ("How to run", ("how",)),
    ("Sign in", ("consent", "free-sign-in", "refused")),
    ("Ready", ("free-done",)),
)
OWN_STAGES: Stages = (
    ("Welcome", ("welcome",)),
    ("How to run", ("how",)),
    ("Frontier", ("frontier",)),
    ("Efficient", ("efficient",)),
    ("Decision", ("decision", "decision-key", "decision-model")),
    ("Ready", ("done",)),
)
"""The steps as the progress line shows them, on the free credit's way and on the user's own keys'; on the latter,
the steps that connect a provider (`CONNECTING`) are in their role's stage."""
CONNECTING: tuple[Step, ...] = ("provider", "unavailable", "address", "key")
"""The steps that connect a provider, for the frontier model or the efficient one (`OnboardingScreen.role`)."""
LISTING: tuple[Step, ...] = (
    "how",
    "refused",
    "provider",
    "frontier",
    "efficient",
    "decision",
    "decision-model",
)
UNFILTERED: tuple[Step, ...] = ("how", "refused")
"""Listing steps whose rows run to more lines, too short to filter: the list has the focus."""
FILTERED: tuple[Step, ...] = ("provider", "frontier", "efficient", "decision-model")
"""Listing steps long enough to filter by typing; the rest are a few rows, picked with the arrows."""
TYPED: tuple[Step, ...] = ("frontier", "efficient", "decision-model")
"""Listing steps where a name typed that is not listed is used as is."""
FREE, OWN, RETRY = "free", "own", "retry"
"""The rows of the how-to-run and refused steps."""
REASONS = {
    "account_too_new": "Free credit needs a GitHub account at least 30 days old. Signing in with Google works too.",
    "email_unverified": "Free credit needs a Google account with a verified email.",
    "network_blocked": "Free credit can't be claimed from this network (a VPN or a data centre, for example).",
    "grants_paused": "The free trial isn't taking new sign-ins right now.",
    "invite_only": "The free trial is open to invited accounts only for now.",
    "identity_required": "Free credit needs a GitHub or Google sign-in, not an email code.",
    "identity_used": "That GitHub or Google account has already claimed its free credit, on another Nace account.",
    "account_flagged": "This account can't get free credit.",
    "free_revoked": "The free credit on this account was revoked.",
    "access_denied": "The sign-in was declined in the browser.",
    "expired_token": "The code expired before the sign-in was finished.",
}
"""Why a sign-in was refused, in plain words: by the console's `reason`, else its `code`."""
RETRYABLE = ("access_denied", "expired_token", "unavailable")
"""Refusals a second sign-in may not meet."""
SKIP = "skip"
"""The efficient provider step's last row: no efficient model, so no hand-over."""
OPENROUTER = PROVIDER_BY_ID["openrouter"]
OPENROUTER_NOTE = "every model on one key · sign in with your browser"
SETUP_STEPS = ("Choose how to run: free credit, or your own keys", "Sign in once, or pick your models", "Start a task")
"""What setup asks, on its first screen."""
KAPITSA_START = "kapitsa-systemone --model ~/.cache/kapitsa/models/drex-max-v1 --port 8001 --name drex-max-v1"
"""How to serve Drex Max on this machine where Kapitsa looks for it (docs/local-models.md#system-1)."""
DECISION_JOBS = (
    "Decides when to hand the task to the efficient model",
    "Spots when the work goes in circles and steers it back",
    "Searches your code and files, and ranks what it finds",
    "Picks what to keep in mind when the context fills up",
    "Checks risky commands before they run",
)
"""What the decision model does, on its step."""
LABEL_WIDTH = 34
NONE = ProviderOption("", "", "", "")
ROOMY_ROWS = 56
"""From this height on the wordmark stays over a list; below it the list has its rows."""
CUSTOM_KEY_ENV = "CUSTOM_API_KEY"
LOCAL_SUGGESTED = ("qwen3.8:27b", "qwen3:30b", "gpt-oss:20b")


@dataclass
class Connection:
    """A provider connected for a role: where it is, when it is a server or an endpoint, and the models it lists."""

    provider: ProviderOption
    address: str = ""
    listed: tuple[str, ...] = ()


@dataclass(frozen=True)
class Onboarded:
    """What the screen saved: the picks the session opens on, and the keys to use in this process right away."""

    setup: Setup
    saved: Saved


@dataclass(frozen=True)
class StartedFree:
    """The free credit is signed in and in use (`[free] use = true`): the session opens on its models."""

    grant: FreeGrant | None


def refusal_text(refused: Refusal) -> str:
    """Why the console gave no free credit, in plain words."""
    said = REASONS.get(refused.reason or "") or REASONS.get(refused.code)
    if said:
        return said
    if refused.code == "unavailable":
        return "The Nace console couldn't be reached, or isn't taking sign-ins now."
    return refused.message or f"The Nace console refused the sign-in ({refused.code})."


def credit_text(grant: FreeGrant) -> str:
    """The agent credit left: what the frontier and efficient models run on."""
    agent = grant.meters.agent
    return f"${max(agent.limit_usd - agent.used_usd, 0):.2f} of ${agent.limit_usd:.2f}"


def free_blocked(found: FreeReport | None) -> str:
    """Why Start free is not offered, in plain words; empty while it is. A console that could not be asked does not
    hide it: the sign-in then says what went wrong."""
    if found is None:
        return ""
    status = found.status
    usable = found.grant is not None and found.grant.state == "active"
    if found.grant is not None and not found.grant.meters.agent.runs():
        return "Your free credit is used up, and it doesn't come back."
    if status is not None and status.inference == "paused":
        return status.message or "The free trial is paused right now."
    if status is not None and status.grants == "paused" and not usable:
        return status.message or REASONS["grants_paused"]
    return ""


class OnboardingScreen(Screen[Onboarded | StartedFree | None]):
    """Dismisses with what was saved, with `StartedFree`, or None when the user leaves setup from its first step."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+o", "open_link", "Open the page", show=False),
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "Page up", show=False),
        Binding("pagedown", "page(1)", "Page down", show=False),
    ]

    def __init__(
        self,
        *,
        home: Path,
        env: Mapping[str, str],
        start: Literal["welcome", "system1", "decision", "free"] = "welcome",
        system2: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__()
        self.home = home
        self.env = dict(env)
        self.transport = transport
        """What the screen checks keys and asks the Nace console over; None for the network."""
        self.step: Step = "welcome" if start == "welcome" else "consent" if start == "free" else "decision"
        self.way: Way = "free" if start in ("welcome", "free") else "own"
        self.free: FreeReport | None = None
        """The free credit as the console said when the how-to-run step was first shown; None until it answers."""
        self.code: SignInCode | None = None
        self.refused: Refusal | None = None
        self.granted: Granted | None = None
        self.from_decision = start in ("system1", "decision")
        """Setup started at the decision model, System 2 already working: then the efficient model, after it."""
        self.role: Role = "frontier"
        """Whose provider the connecting steps connect."""
        self.trail: list[tuple[Step, Role]] = []
        """The steps taken, each with its role, for Esc to go back through."""
        self.connections: dict[Role, Connection] = {}
        self.system2 = system2
        """The frontier model, `provider/model`; given when setup starts at the decision model (it already works)."""
        self.option: System1Option | None = None
        self.system1_listed: tuple[str, ...] = ()
        self.system1_model = ""
        self.handoff: str | None = None
        """The efficient model, `provider/model`; None until picked, or skipped."""
        self.skipped = False
        """The efficient model's step was skipped: no hand-over."""
        self.keys: dict[str, str] = {}
        self.choices: list[Choice] = []
        self.saved: Onboarded | None = None
        self.busy = False
        self._sign_in: OpenRouterSignIn | None = None

    # -- layout ------------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            with Center():
                yield Static(Content("\n").join(wordmark()), id="setup-wordmark")
            with Center():
                yield Static(id="setup-stages")
            with Center(), Vertical(id="setup-card"):
                with Horizontal(id="setup-intro"):
                    yield Mascot()
                    yield Static(id="setup-about")
                yield Static(id="setup-title")
                yield Static(id="setup-body")
                yield Input(placeholder="Filter…", id="setup-filter")
                yield OptionList(id="setup-options")
                yield Input(id="setup-input")
                yield Static(id="setup-status")
            with Center():
                yield Static(id="setup-keys")

    def on_mount(self) -> None:
        self._show()

    @property
    def connection(self) -> Connection | None:
        return self.connections.get(self.role)

    @property
    def provider(self) -> ProviderOption | None:
        return self.connection.provider if self.connection is not None else None

    @property
    def address(self) -> str:
        return self.connection.address if self.connection is not None else ""

    @property
    def listed(self) -> tuple[str, ...]:
        return self.connection.listed if self.connection is not None else ()

    def _key_present(self, name: str) -> bool:
        return bool(name) and (name in self.keys or key_found(name, self.env))

    def _show(self, status: Content | str = "") -> None:
        """Draw the current step: its title, what it says, the list or the line it asks with, and the keys."""
        step = self.step
        intro = step in ("welcome", "done", "free-done")
        self.query_one("#setup-intro").display = intro
        bare = step in ("welcome", "done")  # all on the intro: no title or body under it
        self.query_one("#setup-title").display = self.query_one("#setup-body").display = not bare
        self.query_one(Mascot).play("happy" if step in ("done", "free-done") else "idle")
        self.query_one("#setup-stages", Static).update(self._stages())
        filter_line, options, line = (
            self.query_one("#setup-filter", Input),
            self.query_one("#setup-options", OptionList),
            self.query_one("#setup-input", Input),
        )
        listing = step in LISTING
        tall = listing or step == "free-done"
        self.query_one("#setup-wordmark").display = not tall or self.app.size.height >= ROOMY_ROWS
        options.display = listing
        filter_line.display = step in FILTERED
        line.display = step in ("address", "key", "decision-key")
        title, body = getattr(self, f"_{step.replace('-', '_')}")()
        self.query_one("#setup-title", Static).update(Content.styled(title, S_HEAD))
        self.query_one("#setup-body", Static).update(body)
        self.query_one("#setup-status", Static).update(status)
        self.query_one("#setup-keys", Static).update(self._keys())
        if listing:
            filter_line.value = ""
            self._refilter("")
            (filter_line if filter_line.display else options).focus()
        elif line.display:
            line.focus()
        else:
            self.set_focus(None)

    def _stages(self) -> Content:
        """The progress line. On the user's own keys the connecting steps are in their role's stage; setup that
        started at the decision model goes on to the efficient model."""
        stages = FREE_STAGES if self.way == "free" else OWN_STAGES
        if self.way == "own" and self.from_decision:
            stages = (OWN_STAGES[4], OWN_STAGES[3], OWN_STAGES[5])
        step: Step = self.role if self.way == "own" and self.step in CONNECTING else self.step
        at = next(i for i, (_, steps) in enumerate(stages) if step in steps)
        parts: list[tuple[str, str] | str] = []
        for i, (name, _) in enumerate(stages):
            if i:
                parts.append((" ── ", S_MUTED))
            mark = "✓" if i < at else "●" if i == at else "○"
            style = S_OK if i < at else f"bold {BRAND}" if i == at else S_MUTED
            parts += [(f"{mark} ", style), (name, S_HEAD if i == at else S_MUTED)]
        return Content.assemble(*parts)

    def _keys(self) -> Content:
        keys = {
            "welcome": "Enter to set up · Esc to skip",
            "unavailable": "Enter or Esc goes back to the list",
            "consent": "Enter agrees and opens the sign-in page · Esc goes back",
            "free-sign-in": "Ctrl+O opens the page again · Esc cancels",
            "done": "Enter to start",
            "free-done": "Enter to start",
        }.get(self.step, "Enter continues · Esc goes back")
        if self.step in ("key", "decision-key"):
            keys += " · Ctrl+O opens the key page"
        if self.step in TYPED:
            keys += " · or type any model id"
        return Content.styled(keys, S_MUTED)

    # -- each step: (title, body) --------------------------------------------------------------------------------

    def _welcome(self) -> tuple[str, Content]:
        self.query_one("#setup-about", Static).update(
            Content.assemble(
                ("Welcome to Intern\n", f"bold {BRAND}"),
                ("━" * len("Welcome to Intern") + "\n\n", BRAND),
                ("Setup takes a minute:\n\n", S_INK),
                *(
                    part
                    for i, step in enumerate(SETUP_STEPS, 1)
                    for part in ((f"  {i}  ", f"bold {BRAND}"), (step + "\n", S_INK))
                ),
            )
        )
        return "", Content("")

    def _how(self) -> tuple[str, Content]:
        blocked = free_blocked(self.free)
        self.choices = [] if blocked else [Choice(FREE, "Start free", "", note="recommended")]
        self.choices.append(Choice(OWN, "Use my own keys", ""))
        if blocked:
            return "How do you want to run Intern?", Content.assemble(
                (blocked, S_WARN), (" You can run Intern on your own keys.", S_INK)
            )
        return "How do you want to run Intern?", Content("")

    def _how_lines(self, choice: Choice) -> tuple[str, ...]:
        """What a row of the how-to-run step says under its name."""
        if choice.id == OWN:
            return ("Any provider; pick your models. No sign-in.",)
        found = self.free
        if found is not None and found.signed_in and found.grant is not None:
            return (f"Signed in · {credit_text(found.grant)} left.", "Picks up where you left off, with no sign-in.")
        return ("Free credit on Nace's models. No keys, no card.", "Sign in with GitHub or Google to claim it, once.")

    def _consent(self) -> tuple[str, Content]:
        return "Before you sign in", Content.assemble(("What Nace sees. ", S_HEAD), (CONSENT, S_INK))

    def _free_sign_in(self) -> tuple[str, Content]:
        code = self.code
        if code is None:
            return "Sign in", Content.styled("Asking the Nace console for a code…", S_MUTED_ITALIC)
        return "Sign in", Content.assemble(
            ("Confirm this code in your browser, then sign in with GitHub or Google:\n\n", S_INK),
            (f"    {code.user_code}\n\n", f"bold {BRAND}"),
            ("The page opened in your browser. If it didn't, open ", S_MUTED),
            (code.url, f"underline {BRAND}"),
            (" (Ctrl+O opens it).", S_MUTED),
        )

    def _refused(self) -> tuple[str, Content]:
        refused = self.refused
        assert refused is not None
        self.choices = [Choice(OWN, "Use my own keys", "", note="any provider; no sign-in")]
        if refused.code in RETRYABLE or refused.reason in RETRYABLE:
            self.choices.append(Choice(RETRY, "Try again", "", note="sign in again in the browser"))
        return "No free credit this time", Content.styled(refusal_text(refused), S_WARN)

    def _free_done(self) -> tuple[str, Content]:
        grant = self.granted.grant if self.granted is not None else None
        if grant is None:
            self.query_one("#setup-about", Static).update(
                Content.assemble(
                    ("You're on free credit\n\n", f"bold {BRAND}"),
                    ("Describe a task to start. ", S_INK),
                    ("/free", S_HEAD),
                    (" shows what it runs on and what's left.", S_MUTED),
                )
            )
            return "", Content("")
        bundle = grant.bundle
        named = free_names(bundle.system2, bundle.handoff, bundle.system1)
        rows = [
            ("Frontier", named["frontier"], "does the hard part"),
            ("Efficient", named["efficient"], "finishes it, for much less"),
            ("Decision", named["decision"], "watches every step"),
        ]
        self.query_one("#setup-about", Static).update(
            Content.assemble(
                ("You're on free credit\n\n", f"bold {BRAND}"),
                *(
                    part
                    for label, name, does in rows
                    for part in ((f"{label:<11}", S_MUTED), (f"{name:<21}", S_HEAD), (f"{does}\n", S_MUTED))
                ),
                (f"{'Credit':<11}", S_MUTED),
                (credit_text(grant), S_INK),
                (" left for the models above", S_MUTED),
            )
        )
        body = Content.assemble(
            (FREE_ABOUT.format(**named), S_INK),
            ("\n\nDescribe a task to start. ", S_INK),
            ("/free", S_HEAD),
            (" shows what's left; ", S_MUTED),
            ("/setup", S_HEAD),
            (" adds your own keys for when it runs out.", S_MUTED),
        )
        return "", body

    def _provider(self) -> tuple[str, Content]:
        self.choices = self._provider_choices()
        if self.role == "efficient":
            said = "Where your efficient model runs: your frontier model's provider, or another one."
            return "Connect your efficient model", Content.styled(said, S_INK)
        said = "Where your frontier model runs. OpenRouter has every model on one key."
        return "Connect your frontier model", Content.styled(said, S_INK)

    def _provider_choices(self) -> list[Choice]:
        ready = [p for p in SYSTEM2_PROVIDERS if p.kind == "key" and self._key_present(p.key_env)]
        notes = {p.id: f"key found in ${p.key_env}" for p in ready}
        frontier = PROVIDER_BY_ID.get(self.system2.partition("/")[0])
        if self.role == "efficient" and frontier is not None:  # the frontier model's provider first
            ready = [frontier, *(p for p in ready if p is not frontier)]
            notes[frontier.id] = "your frontier model's provider"
        choices = [Choice(p.id, p.name, "Ready to use", note=notes[p.id]) for p in ready]
        if self.role == "efficient":
            choices.append(Choice(SKIP, "Skip this step", "Or", note=f"every turn runs on {model_name(self.system2)}"))
        if OPENROUTER not in ready:
            choices.append(Choice(OPENROUTER.id, OPENROUTER.name, "Recommended", note="★ " + OPENROUTER_NOTE))
        for p in SYSTEM2_PROVIDERS:
            if p in ready or p is OPENROUTER or p.kind == "subscription":  # none can be signed in with yet
                continue
            note = p.blurb + (f" · {p.badge}" if p.badge else "")
            choices.append(Choice(p.id, p.name, p.group, note=note))
        return choices

    def _unavailable(self) -> tuple[str, Content]:
        assert self.provider is not None
        return self.provider.name, Content.assemble((self.provider.unavailable, S_INK))

    def _address(self) -> tuple[str, Content]:
        assert self.provider is not None
        line = self.query_one("#setup-input", Input)
        line.password = False
        line.placeholder = "http://localhost:8000/v1"
        line.value = self.address or self.provider.base_url
        if self.provider.kind == "custom":
            said = "Its address, the part before /chat/completions."
        else:
            said = f"Where {self.provider.name} serves its OpenAI-compatible API. Start it first if it isn't running."
        return f"Connect {self.provider.name}", Content.styled(said, S_INK)

    def _key_body(self, name: str, env_name: str, url: str, *, optional: bool = False) -> Content:
        line = self.query_one("#setup-input", Input)
        line.password, line.value = True, ""
        browser = env_name == "OPENROUTER_API_KEY"
        if self._key_present(env_name):
            line.placeholder = f"Enter uses the key already in ${env_name}, or paste another"
        elif browser:
            line.placeholder = "Press Enter to sign in with your browser, or paste a key"
        elif optional:
            line.placeholder = "Paste the key, or press Enter if it takes none"
        else:
            line.placeholder = "Paste your key (it stays hidden)"
        parts: list[tuple[str, str] | str] = [(f"Paste your {name} API key.", S_INK)]
        if browser:
            parts = [
                ("One OpenRouter key runs every model Intern uses. ", S_INK),
                ("Press Enter to sign in with your browser", S_HEAD),
                (", or paste a key you already have.", S_INK),
            ]
        if url and not browser:
            parts += [(" Make one at ", S_INK), (url, f"underline {BRAND}"), (" (Ctrl+O opens it).", S_MUTED)]
        parts.append(("\nThe key stays on this machine, never in your config file.", S_MUTED))
        return Content.assemble(*parts)

    def _key(self) -> tuple[str, Content]:
        assert self.provider is not None
        p = self.provider
        if p.kind == "custom":
            return f"Connect {p.name}", self._key_body("endpoint's", CUSTOM_KEY_ENV, "", optional=True)
        return f"Connect {p.name}", self._key_body(p.name, p.key_env, p.key_url)

    def _frontier(self) -> tuple[str, Content]:
        assert self.provider is not None and self.role == "frontier"
        suggested = list(self.provider.suggested or (LOCAL_SUGGESTED if self.provider.kind == "local" else ()))
        models = offered_models(replace(self.provider, suggested=tuple(suggested)), self.listed, "frontier")
        more = f"More on {self.provider.name}"
        self.choices = [
            Choice(m, model_name(f"{self.provider.id}/{m}"), "Frontier models" if m in suggested else more)
            for m in models
        ]
        said = "Starts each task and does the hard part. Pick a strong model."
        return "Frontier model", Content.styled(said, S_INK)

    def _efficient(self) -> tuple[str, Content]:
        assert self.provider is not None and self.role == "efficient"
        option = self.provider
        refs = efficient_models(option.id, self.system2, self.listed)
        first = {f"{option.id}/{m}" for m in (option.handoff_suggested, *option.efficient)}
        more = f"More on {option.name}"
        self.choices = [Choice(ref, model_name(ref), "Efficient models" if ref in first else more) for ref in refs]
        return "Efficient model", Content.styled(HANDOFF_OWN_KEYS, S_INK)

    def _decision(self) -> tuple[str, Content]:
        self.choices = []
        for option in SYSTEM1_OPTIONS:
            note = option.blurb + (" · your key is set" if option.key_env and self._key_present(option.key_env) else "")
            group = {"decisions-api": "On OpenRouter", "kapitsa": "On this machine"}.get(option.backend, "On Nace AI")
            self.choices.append(Choice(option.id, option.name, group, note=note))
        body = Content.assemble(
            ("A small, fast model that makes Intern's calls while your models work:\n", S_INK),
            *(("  •  " + job + "\n", S_INK) for job in DECISION_JOBS),
            ("Search and command checks are off until you turn them on with ", S_MUTED),
            ("/caps", S_HEAD),
            (".", S_MUTED),
        )
        return "Decision model", body

    def _decision_key(self) -> tuple[str, Content]:
        assert self.option is not None
        where = "OpenRouter" if self.option.backend == "decisions-api" else "Nace AI"
        return f"Connect {where}", self._key_body(where, self.option.key_env, self.option.key_url)

    def _decision_model(self) -> tuple[str, Content]:
        assert self.option is not None
        where = "OpenRouter" if self.option.backend == "decisions-api" else "Nace AI"
        offered = [m for m in self.system1_listed if m.startswith(self.option.only)]
        drex_first = sorted(offered, key=lambda m: "drex" not in m)
        self.choices = [Choice(m, model_name(m), f"Decision models on {where}") for m in drex_first]
        return "Decision model", Content.styled("Pick one.", S_INK)

    def _done(self) -> tuple[str, Content]:
        assert self.saved is not None
        setup, saved = self.saved.setup, self.saved.saved
        rows: list[tuple[str, str]] = [
            ("Frontier" if setup.handoff else "Model", model_name(setup.system2)),
            ("Efficient", model_name(setup.handoff) if setup.handoff else "skipped, no hand-over"),
            ("Decision", model_name(setup.system1_choice.removeprefix("decisions:"))),
        ]
        where = {"keychain": "your keychain", "env-file": f"{names.HOME}/.env"}
        for place in dict.fromkeys(saved.keys.values()):
            kept = ", ".join(name for name, stored in saved.keys.items() if stored == place)
            rows.append(("Key", f"{kept}, saved to {where[place]}"))
        self.query_one("#setup-about", Static).update(
            Content.assemble(
                ("You're all set\n\n", f"bold {BRAND}"),
                *(part for label, value in rows for part in ((f"{label:<11}", S_MUTED), (f"{value}\n", S_INK))),
                ("\nDescribe a task to start. ", S_INK),
                ("/setup", S_HEAD),
                (" brings this screen back.", S_MUTED),
            )
        )
        return "", Content("")

    # -- lists ---------------------------------------------------------------------------------------------------

    def _refilter(self, query: str) -> None:
        options = self.query_one("#setup-options", OptionList)
        was = options.highlighted_option.id if options.highlighted_option is not None else None
        options.clear_options()
        if self.step in UNFILTERED:
            for choice in self.choices:
                lines = self._how_lines(choice) if self.step == "how" else (choice.note,)
                head = [(fit(choice.name, LABEL_WIDTH), S_HEAD)]
                if self.step == "how" and choice.note:
                    head.append((choice.note, S_OK))
                said = Content.assemble(*(part for line in lines for part in (("\n    ", ""), (line, S_MUTED))))
                options.add_option(Option(Content.assemble("  ", *head, said, "\n"), id=choice.id))
            ids = [choice.id for choice in self.choices]
            options.highlighted = ids.index(was) if was in ids else 0  # the console's answer redraws the step
            return
        shown = ranked(self.choices, query)
        group: str | None = None
        first: int | None = None
        for choice in shown:
            if choice.group != group:
                group = choice.group
                options.add_option(Option(Content.styled(group.upper(), "bold"), disabled=True))
            unavailable = self.step == "provider" and bool(PROVIDER_BY_ID.get(choice.id, NONE).unavailable)
            parts: list[tuple[str, str] | str] = [
                "  ",
                (fit(choice.name, LABEL_WIDTH), S_MUTED if unavailable else S_HEAD),
                ("  " + choice.note, S_MUTED_ITALIC if unavailable else S_MUTED),
            ]
            options.add_option(Option(Content.assemble(*parts), id=choice.id))
            first = options.option_count - 1 if first is None else first
        if not shown:
            typed = self.step in TYPED
            none = "nothing listed matches; Enter uses what you typed" if typed else "nothing listed matches"
            options.add_option(Option(Content.styled(none, S_MUTED), disabled=True))
        options.highlighted = first

    @on(Input.Changed, "#setup-filter")
    def _on_filter(self, message: Input.Changed) -> None:
        self._refilter(message.value)

    @on(Input.Submitted, "#setup-filter")
    def _on_filter_submit(self, message: Input.Submitted) -> None:
        highlighted = self.query_one("#setup-options", OptionList).highlighted_option
        if highlighted is not None and highlighted.id is not None:
            self._picked(highlighted.id)
        elif message.value.strip() and self.step in TYPED:
            typed = message.value.strip()
            provider = self.provider if self.step == "efficient" else None  # its provider's, as listed ones are
            self._picked(f"{provider.id}/{typed}" if provider is not None else typed)

    @on(OptionList.OptionSelected, "#setup-options")
    def _on_pick(self, message: OptionList.OptionSelected) -> None:
        if message.option.id is not None:
            self._picked(message.option.id)

    def action_move(self, delta: int) -> None:
        options = self.query_one("#setup-options", OptionList)
        if options.display:
            options.action_cursor_down() if delta > 0 else options.action_cursor_up()

    def action_page(self, delta: int) -> None:
        options = self.query_one("#setup-options", OptionList)
        if options.display:
            options.action_page_down() if delta > 0 else options.action_page_up()

    # -- moving through it ---------------------------------------------------------------------------------------

    def _go(self, step: Step, status: Content | str = "", *, role: Role | None = None) -> None:
        self.trail.append((self.step, self.role))
        self.step, self.role = step, role or self.role
        self._show(status)

    def action_back(self) -> None:
        if self.step in ("free-sign-in", "refused"):
            stopped = self.step == "free-sign-in"
            self.workers.cancel_group(self, "setup-free")
            while self.trail and self.trail[-1][0] in ("consent", "free-sign-in"):
                self.trail.pop()
            self.step, self.role = self.trail.pop() if self.trail else ("how", "frontier")
            self._show(Content.styled("Sign-in stopped. Start free tries again.", S_WARN) if stopped else "")
            return
        if self._sign_in is not None:
            self._sign_in.close()
            self._sign_in = None
            self.workers.cancel_group(self, "setup-check")
            self.busy = False
            self._status("Sign-in stopped. Enter tries again, or paste a key.", warn=True)
            return
        if self.busy:
            return
        if self.step == "welcome" or not self.trail:
            self.dismiss(None)
            return
        if self.step in ("done", "free-done"):
            return  # already saved, or signed in: Enter goes on
        self.step, self.role = self.trail.pop()
        if self.step == "how":
            self.way = "free" if not free_blocked(self.free) else "own"
        self._show()

    def action_open_link(self) -> None:
        url = ""
        if self.step == "free-sign-in" and self.code is not None:
            url = self.code.url
        elif self.step == "key" and self.provider is not None:
            url = self.provider.key_url
        elif self.step == "decision-key" and self.option is not None:
            url = self.option.key_url
        if url:
            self.app.open_url(url)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter" and self.step in ("welcome", "done", "unavailable", "consent", "free-done"):
            event.stop()
            if self.step == "welcome":
                self._go("how")
                if self.free is None:
                    self._read_free()
            elif self.step == "unavailable":
                self.action_back()
            elif self.step == "consent":
                self._go("free-sign-in")
                self._claim()
            elif self.step == "free-done":
                self.dismiss(StartedFree(self.granted.grant if self.granted is not None else None))
            else:
                self.dismiss(self.saved)

    @work(exclusive=True, group="setup-read")
    async def _read_free(self) -> None:
        """Ask the console whether the free credit can be offered, and what is left of one signed in to; the step
        is redrawn on the answer. A console that does not answer leaves Start free offered."""
        try:
            self.free = await report(self.home, self.env, self.transport)
        except (httpx.HTTPError, OSError, ValueError):
            return
        if self.step == "how":
            self.way = "own" if free_blocked(self.free) else "free"
            self._show()

    def _start_free(self) -> None:
        """Start free: picked up again with no sign-in where this machine is signed in to a grant with credit left;
        else what Nace sees, then the sign-in."""
        self.way = "free"
        found = self.free
        if found is not None and found.signed_in and found.grant is not None and found.grant.state == "active":
            self._resume_free(found.grant)
        else:
            self._go("consent")

    @work(exclusive=True, group="setup-free")
    async def _resume_free(self, grant: FreeGrant) -> None:
        await asyncio.to_thread(set_use, self.home, True)
        self.granted = Granted(grant=grant)
        self._go("free-done")

    @work(exclusive=True, group="setup-free")
    async def _claim(self) -> None:
        """The console's device sign-in, the consent's version with it: the code and the page as it gives them,
        then Ready, or why not."""
        self.code, self.refused = None, None

        def code_shown(code: SignInCode) -> None:
            self.code = code
            self.app.open_url(code.url)
            self._show(Content.styled("Waiting for you to finish in the browser… Esc cancels", S_MUTED_ITALIC))

        try:
            done = await claim(self.home, self.env, CONSENT_VERSION, code_shown, self.transport)
        except (httpx.HTTPError, ConnectionError, OSError, KeyError, ValueError) as error:
            done = Refusal(code="unavailable", message=f"The console could not be reached ({type(error).__name__}).")
        if self.step != "free-sign-in":
            return  # cancelled meanwhile
        if isinstance(done, Refusal):
            self.refused = done
            self.step = "refused"
            self._show()
            return
        self.granted = done
        self.trail.clear()
        self._go("free-done")

    def _picked(self, picked: str) -> None:
        if self.busy:
            return
        match self.step:
            case "how":
                if picked == FREE:
                    self._start_free()
                else:
                    self.way = "own"
                    self._go("provider", role="frontier")
            case "refused":
                if picked == RETRY:
                    self.step = "free-sign-in"
                    self._show()
                    self._claim()
                else:
                    self.way = "own"
                    self._go("provider", role="frontier")
            case "provider":
                if picked == SKIP:  # no efficient model: every turn runs on the frontier model
                    self.handoff, self.skipped = None, True
                    self._go("decision") if self.option is None else self._finish()
                    return
                option = next(p for p in SYSTEM2_PROVIDERS if p.id == picked)
                frontier = self.connections.get("frontier")
                if self.role == "efficient" and frontier is not None and frontier.provider is option:
                    # shortcut: one endpoint per provider id, so both models on `custom` share one address.
                    self.connections["efficient"] = frontier  # its key, address and models are the frontier's
                    self._go("efficient")
                    return
                self.connections[self.role] = Connection(option)
                if option.unavailable:
                    self._go("unavailable")
                elif option.kind in ("local", "custom"):
                    self._go("address")
                else:
                    self._go("key")
            case "frontier":
                assert self.provider is not None
                self.system2 = f"{self.provider.id}/{picked}"
                self._go("provider", role="efficient")
            case "efficient":
                self.handoff, self.skipped = picked, False
                if self.option is None:
                    self._go("decision")
                else:
                    self._after_decision()
            case "decision":
                self.option = SYSTEM1_BY_ID[picked]
                if self.option.backend == "kapitsa":
                    self._check_kapitsa()
                elif self.option.key_env in self.keys or self._key_present(self.option.key_env):
                    key = self.keys.get(self.option.key_env) or self.env[self.option.key_env]
                    self._after_decision_key(key, checked=None)
                else:
                    self._go("decision-key")
            case "decision-model":
                self.system1_model = picked
                self._after_decision()

    def _after_decision(self, status: Content | str = "") -> None:
        """The decision model is set: the efficient model if setup started past it, else save."""
        if self.handoff is None and not self.skipped:
            self._go("provider", status, role="efficient")
        else:
            self._finish()

    @on(Input.Submitted, "#setup-input")
    def _on_line(self, message: Input.Submitted) -> None:
        if self.busy:
            return
        value = message.value.strip()
        if self.step == "address":
            if not value.startswith(("http://", "https://")):
                self._status("An address starts with http:// or https://, e.g. http://localhost:11434/v1", warn=True)
                return
            assert self.connection is not None
            self.connection.address = value.rstrip("/")
            if self.connection.provider.kind == "custom":
                self._go("key")
            else:
                self._check_system2(None)
            return
        env_name = self._key_env()
        if not value and self._key_present(env_name):
            value = self.keys.get(env_name) or self.env[env_name]
        elif not value:
            if self.step == "key" and self.provider is not None and self.provider.kind == "custom":
                self._check_system2(None)
                return
            if env_name == "OPENROUTER_API_KEY":
                self._browser_sign_in()
                return
            self._status("Paste the key to continue (it stays hidden), or Esc to pick another way.", warn=True)
            return
        elif len(value) < MIN_SECRET_CHARS or not (value.isascii() and value.isprintable()) or " " in value:
            self._status("That doesn't look like a whole key; copy it again from the key page (Ctrl+O).", warn=True)
            return
        if self.step == "key":
            self._check_system2(value)
        else:
            self._check_system1(value)

    @work(exclusive=True, group="setup-check")
    async def _browser_sign_in(self) -> None:
        """OpenRouter's browser sign-in; the key it gives is checked and kept like a pasted one."""
        sign_in = self._sign_in = OpenRouterSignIn(self.transport)
        self.busy = True
        try:
            url = await sign_in.start()
            self.app.open_url(url)
            self._status(
                "Finish signing in in your browser; this continues on its own. Esc stops it.\n"
                f"If no browser opened, open this link: {url}"
            )
            key = await sign_in.key()
        except SignInFailed as failed:
            self._status(f"{str(failed)[0].upper()}{str(failed)[1:]}.", warn=True)
            return
        finally:
            sign_in.close()
            self._sign_in = None
            self.busy = False
        if self.step == "key":
            self._check_system2(key)
        else:
            self._check_system1(key)

    def _key_env(self) -> str:
        if self.step == "decision-key":
            return self.option.key_env if self.option is not None else ""
        if self.provider is None:
            return ""
        return CUSTOM_KEY_ENV if self.provider.kind == "custom" else self.provider.key_env

    def _status(self, text: str, *, warn: bool = False, ok: bool = False) -> None:
        style = S_WARN if warn else S_OK_BOLD if ok else S_MUTED_ITALIC
        self.query_one("#setup-status", Static).update(Content.styled(text, style))

    @work(exclusive=True, group="setup-check")
    async def _check_system2(self, key: str | None) -> None:
        assert self.provider is not None
        self.busy = True
        self._status("Checking with the provider…")
        try:
            found = await check_system2(self.provider, key, self.address or None, self.transport)
        finally:
            self.busy = False
        if not found.ok:
            self._status(self._fix(found, local=key is None), warn=True)
            return
        if key:
            self.keys[self._key_env()] = key
        assert self.connection is not None
        self.connection.listed = found.models
        self._go("efficient" if self.role == "efficient" else "frontier", self._kept(found))

    def _fix(self, found: KeyCheck, *, local: bool) -> str:
        if local:
            return f"{found.message[0].upper()}{found.message[1:]}. Check the address, or Esc to pick another way."
        return "The provider didn't accept that key. Copy it again from the key page (Ctrl+O), or make a new one."

    def _kept(self, found: KeyCheck) -> Content:
        if found.message:
            return Content.styled(f"Key kept, but {found.message.removesuffix('; kept unchecked')}.", S_WARN)
        return Content.styled("✓ Connected", S_OK_BOLD)

    @work(exclusive=True, group="setup-check")
    async def _check_system1(self, key: str) -> None:
        assert self.option is not None
        self.busy = True
        self._status("Checking with the provider…")
        try:
            found = await check_system1(self.option, key, self.transport)
        finally:
            self.busy = False
        if not found.ok:
            self._status(self._fix(found, local=False), warn=True)
            return
        self._after_decision_key(key, checked=found)

    @work(exclusive=True, group="setup-check")
    async def _check_kapitsa(self) -> None:
        """kapitsa-systemone takes no key: whether it runs, and the model it serves is the pick."""
        assert self.option is not None
        self.busy = True
        self._status("Looking for kapitsa-systemone…")
        try:
            found = await check_system1(self.option, "", self.transport)
        finally:
            self.busy = False
        if not found.ok:
            why = f"{found.message[0].upper()}{found.message[1:]}"
            self._status(f"{why}. Start it, then pick Kapitsa again:\n{KAPITSA_START}", warn=True)
            return
        self.system1_model = found.models[0]
        self._after_decision(Content.styled(f"✓ Connected to {self.system1_model} on this machine", S_OK_BOLD))

    def _after_decision_key(self, key: str, checked: KeyCheck | None) -> None:
        assert self.option is not None
        self.keys[self.option.key_env] = key
        if self.option.model:
            self._after_decision(self._kept(checked) if checked is not None else "")
            return
        if checked is not None:
            self.system1_listed = checked.models
            self._go("decision-model", self._kept(checked))
        else:
            self._list_decision_models(key)

    @work(exclusive=True, group="setup-check")
    async def _list_decision_models(self, key: str) -> None:
        assert self.option is not None
        self.busy = True
        self._status("Listing decision models…")
        try:
            found = await check_system1(self.option, key, self.transport)
        finally:
            self.busy = False
        self.system1_listed = found.models
        self._go("decision-model")

    @work(exclusive=True, group="setup-save")
    async def _finish(self) -> None:
        """Save, and show what was saved; a save that fails says why and stays on its step to try again."""
        assert self.option is not None
        providers: dict[str, ProviderSpec] = {}
        used = {ref.partition("/")[0] for ref in (self.system2, self.handoff) if ref}
        for connection in self.connections.values():
            p, address = connection.provider, connection.address
            if p.id not in used:
                continue  # connected, then left by going back
            if p.kind == "custom":
                key_env = CUSTOM_KEY_ENV if CUSTOM_KEY_ENV in self.keys else None
                providers[p.id] = ProviderSpec(base_url=address, api_key_env=key_env)
            elif p.kind == "local" and address and address != PROVIDERS[p.id].base_url:
                providers[p.id] = PROVIDERS[p.id].model_copy(update={"base_url": address})
        # A key typed in that is already exported with the same value needs no keeping.
        keys = {name: value for name, value in self.keys.items() if self.env.get(name) != value}
        # Own keys picked over the free credit pause it; a setup only for the decision model leaves it be.
        pause_free = not self.from_decision
        setup = Setup(self.system2, self.option, self.system1_model, keys, providers, self.handoff, pause_free)
        self.busy = True
        self._status("Saving…")
        try:
            saved = await asyncio.to_thread(save, self.home, setup, self.env)
        except (OSError, ValueError) as error:
            self._status(f"Could not save the settings ({error}). Enter tries again.", warn=True)
            return
        finally:
            self.busy = False
        setup.keys = dict(self.keys)  # every key this process should use now, kept or already exported
        self.saved = Onboarded(setup, saved)
        self._go("done")

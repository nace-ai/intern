"""The setup screen: what `intern` opens on when it has no models to run on, and what `/setup` brings back.

                         ▗▄▄ INTERN ▄▄▖            (the wordmark)
          ✓ Welcome ── ✓ Connect ── ● Models ── ○ Decision model ── ○ Ready
    ╭────────────────────────────────────────────────────────────╮
    │ Pick your models                                           │
    │ The frontier model starts each task. Once the hard part is │
    │ done, the efficient model takes over.                      │
    │ RECOMMENDED                                                │
    │ ● Claude Opus 5.5 → Hy4 Flash      best results            │
    │ MORE PAIRS                                                 │
    │   Hy4 → Hy4 Flash                  lowest cost             │
    │   ...                                                      │
    ╰────────────────────────────────────────────────────────────╯
                          Enter continues · Esc goes back

Intern runs on three models: a frontier model that starts each task and does the hard part, an efficient model that
takes over for the rest, and a decision model that picks the moment and keeps the run on track. (The code calls them
System 2, the hand-over target and System 1.) The screen says what Intern is, asks where the models run (OpenRouter
first, signed in with the browser or a pasted key; any other provider or a local server too; a key is checked before
it is kept), then offers Nace's frontier and efficient pairs on that provider (`onboarding.PAIRS`), or a frontier and
an efficient model of the user's own; then the decision model, Drex Max first. It always sets two models: there is no
"no efficient model" here. Nothing on it is an error to read and give up on: a refused key, a
server that does not answer or a subscription Intern cannot sign in with each says what to do instead, on the step it
happened. The last step saves (`onboarding.save`) and the screen dismisses with what it saved, for the app to open
the session on.
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
from intern.models.registry import PROVIDERS, ProviderSpec
from intern.onboarding import (
    HANDOFF_OWN_KEYS,
    PROVIDER_BY_ID,
    SYSTEM1_BY_ID,
    SYSTEM1_OPTIONS,
    SYSTEM2_PROVIDERS,
    KeyCheck,
    OpenRouterSignIn,
    ProviderOption,
    Recommendation,
    Saved,
    Setup,
    SignInFailed,
    System1Option,
    check_system1,
    check_system2,
    efficient_models,
    key_found,
    model_name,
    offered_models,
    pairs_for,
    save,
)
from intern.secret_store import MIN_SECRET_CHARS
from intern.tui.pickers import Choice, fit, ranked
from intern.tui.theme import BRAND, S_HEAD, S_INK, S_MUTED, S_MUTED_ITALIC, S_OK, S_OK_BOLD, S_WARN
from intern.tui.welcome import Mascot, wordmark

Step = Literal[
    "welcome",
    "provider",
    "unavailable",
    "address",
    "key",
    "pair",
    "frontier",
    "efficient",
    "decision",
    "decision-key",
    "decision-model",
    "done",
]
STAGES: tuple[tuple[str, tuple[Step, ...]], ...] = (
    ("Welcome", ("welcome",)),
    ("Connect", ("provider", "unavailable", "address", "key")),
    ("Models", ("pair", "frontier", "efficient")),
    ("Decision model", ("decision", "decision-key", "decision-model")),
    ("Ready", ("done",)),
)
"""The steps as the progress line shows them."""
LISTING: tuple[Step, ...] = ("provider", "pair", "frontier", "efficient", "decision", "decision-model")
TYPED: tuple[Step, ...] = ("frontier", "efficient", "decision-model")
"""Listing steps where a name typed that is not listed is used as is."""
CHOOSE = "choose"
OPENROUTER = PROVIDER_BY_ID["openrouter"]
OPENROUTER_NOTE = "every model on one key · sign in with your browser"
WELCOME = (
    "Frontier models are smart but expensive. Efficient models are cheap but struggle with hard problems.\n\n"
    "Intern uses both. The frontier model cracks the hard part, then the efficient model finishes the job. "
    "A small decision model picks the moment to switch."
)
"""What Intern is, on the first screen."""
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


@dataclass(frozen=True)
class Onboarded:
    """What the screen saved: the picks the session opens on, and the keys to use in this process right away."""

    setup: Setup
    saved: Saved


class OnboardingScreen(Screen[Onboarded | None]):
    """Dismisses with what was saved, or None when the user leaves setup from its first step."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+o", "open_link", "Open the key page", show=False),
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
        start: Literal["welcome", "system1", "decision"] = "welcome",
        system2: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__()
        self.home = home
        self.env = dict(env)
        self.transport = transport
        self.step: Step = "welcome" if start == "welcome" else "decision"
        self.trail: list[Step] = []
        """The steps taken, for Esc to go back through."""
        self.provider: ProviderOption | None = None
        self.address = ""
        self.listed: tuple[str, ...] = ()
        self.system2 = system2
        """The frontier model, `provider/model`; given when setup starts at the decision model (it already works)."""
        self.option: System1Option | None = None
        self.system1_listed: tuple[str, ...] = ()
        self.system1_model = ""
        self.handoff: str | None = None
        """The efficient model, `provider/model`; None until picked."""
        self.pairs: tuple[Recommendation, ...] = ()
        """The frontier and efficient pairs on the connected provider."""
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

    def _key_present(self, name: str) -> bool:
        return bool(name) and (name in self.keys or key_found(name, self.env))

    def _show(self, status: Content | str = "") -> None:
        """Draw the current step: its title, what it says, the list or the line it asks with, and the keys."""
        step = self.step
        self.query_one("#setup-intro").display = step in ("welcome", "done")
        self.query_one(Mascot).play("happy" if step == "done" else "idle")
        self.query_one("#setup-stages", Static).update(self._stages())
        filter_line, options, line = (
            self.query_one("#setup-filter", Input),
            self.query_one("#setup-options", OptionList),
            self.query_one("#setup-input", Input),
        )
        listing = step in LISTING
        self.query_one("#setup-wordmark").display = not listing or self.app.size.height >= ROOMY_ROWS
        filter_line.display = options.display = listing
        line.display = step in ("address", "key", "decision-key")
        title, body = getattr(self, f"_{step.replace('-', '_')}")()
        self.query_one("#setup-title", Static).update(Content.styled(title, S_HEAD))
        self.query_one("#setup-body", Static).update(body)
        self.query_one("#setup-status", Static).update(status)
        self.query_one("#setup-keys", Static).update(self._keys())
        if listing:
            filter_line.value = ""
            self._refilter("")
            filter_line.focus()
        elif line.display:
            line.focus()
        else:
            self.set_focus(None)

    def _stages(self) -> Content:
        at = next(i for i, (_, steps) in enumerate(STAGES) if self.step in steps)
        parts: list[tuple[str, str] | str] = []
        for i, (name, _) in enumerate(STAGES):
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
            "done": "Enter to start",
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
                ("Welcome to Intern\n\n", f"bold {BRAND}"),
                (WELCOME, S_INK),
                ("\n\nSetup takes a minute. You'll need an API key, or a model running on your machine.", S_MUTED),
            )
        )
        return "", Content("")

    def _pair(self) -> tuple[str, Content]:
        self.choices = [
            Choice(r.id, r.title, "Recommended" if i == 0 else "More pairs", note=r.tag)
            for i, r in enumerate(self.pairs)
        ]
        self.choices.append(Choice(CHOOSE, "Choose your own", "Or", note="any frontier and efficient model"))
        body = Content.styled(
            "The frontier model starts each task. Once the hard part is done, the efficient model takes over.", S_INK
        )
        return "Pick your models", body

    def _provider(self) -> tuple[str, Content]:
        self.choices = self._provider_choices()
        body = Content.styled("Where your models run. OpenRouter has every model on one key.", S_INK)
        return "Connect your models", body

    def _provider_choices(self) -> list[Choice]:
        ready = [p for p in SYSTEM2_PROVIDERS if p.kind == "key" and self._key_present(p.key_env)]
        choices = [Choice(p.id, p.name, "Ready to use", note=f"key found in ${p.key_env}") for p in ready]
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
        assert self.provider is not None
        suggested = list(self.provider.suggested or (LOCAL_SUGGESTED if self.provider.kind == "local" else ()))
        models = offered_models(replace(self.provider, suggested=tuple(suggested)), self.listed, "frontier")
        top = {m for m in suggested if m in models}
        self.choices = [
            Choice(
                m,
                model_name(f"{self.provider.id}/{m}"),
                "Suggested" if m in top else f"On {self.provider.name}",
                note="★ recommended" if i == 0 and m in top else "",
            )
            for i, m in enumerate(models)
        ]
        said = "Starts each task and does the hard part. Pick a strong model."
        return "Frontier model", Content.styled(said, S_INK)

    def _efficient(self) -> tuple[str, Content]:
        provider_id = self.system2.partition("/")[0]
        option = PROVIDER_BY_ID.get(provider_id)
        listed = self.listed if self.provider is not None else ()
        refs = efficient_models(self.system2, listed)
        first = {f"{provider_id}/{m}" for m in (option.handoff_suggested, *option.efficient)} if option else set()
        where = option.name if option else provider_id
        self.choices = [
            Choice(
                ref,
                model_name(ref),
                "Suggested" if ref in first else f"On {where}",
                note="★ recommended" if i == 0 and ref in first else "",
            )
            for i, ref in enumerate(refs)
        ]
        return "Efficient model", Content.styled(HANDOFF_OWN_KEYS, S_INK)

    def _decision(self) -> tuple[str, Content]:
        self.choices = []
        for option in SYSTEM1_OPTIONS:
            note = option.blurb
            if option.recommended and self._key_present(option.key_env):
                note += " · uses your OpenRouter key"
            group = "Recommended" if option.recommended else "Other decision models"
            self.choices.append(Choice(option.id, option.name, group, note=("★ " if option.recommended else "") + note))
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
        self.choices = [Choice(m, model_name(m), "Decision models on OpenRouter") for m in self.system1_listed]
        return "Decision model", Content.styled("Pick one.", S_INK)

    def _done(self) -> tuple[str, Content]:
        assert self.saved is not None
        setup, saved = self.saved.setup, self.saved.saved
        rows: list[tuple[str, str]] = [
            ("Frontier", model_name(setup.system2)),
            ("Efficient", model_name(setup.handoff) if setup.handoff else "none"),
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
        options.clear_options()
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
            self._picked(f"{self.system2.partition('/')[0]}/{typed}" if self.step == "efficient" else typed)

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

    def _go(self, step: Step, status: Content | str = "") -> None:
        self.trail.append(self.step)
        self.step = step
        self._show(status)

    def action_back(self) -> None:
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
        if self.step == "done":
            return  # already saved: Enter goes on
        self.step = self.trail.pop()
        self._show()

    def action_open_link(self) -> None:
        url = ""
        if self.step == "key" and self.provider is not None:
            url = self.provider.key_url
        elif self.step == "decision-key" and self.option is not None:
            url = self.option.key_url
        if url:
            self.app.open_url(url)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter" and self.step in ("welcome", "done", "unavailable"):
            event.stop()
            if self.step == "welcome":
                self._go("provider")
            elif self.step == "unavailable":
                self.action_back()
            else:
                self.dismiss(self.saved)

    def _picked(self, picked: str) -> None:
        if self.busy:
            return
        match self.step:
            case "pair":
                pair = next((r for r in self.pairs if r.id == picked), None)
                if pair is None:
                    self._go("frontier")
                    return
                self.system2, self.handoff = pair.system2, pair.handoff
                self._go("decision")
            case "provider":
                option = next(p for p in SYSTEM2_PROVIDERS if p.id == picked)
                self.provider, self.listed, self.address = option, (), ""
                if option.unavailable:
                    self._go("unavailable")
                elif option.kind in ("local", "custom"):
                    self._go("address")
                else:
                    self._go("key")
            case "frontier":
                assert self.provider is not None
                self.system2 = f"{self.provider.id}/{picked}"
                self._go("efficient")
            case "efficient":
                self.handoff = picked
                if self.option is None:
                    self._go("decision")
                else:
                    self._after_decision()
            case "decision":
                self.option = SYSTEM1_BY_ID[picked]
                if self.option.key_env in self.keys or self._key_present(self.option.key_env):
                    key = self.keys.get(self.option.key_env) or self.env[self.option.key_env]
                    self._after_decision_key(key, checked=None)
                else:
                    self._go("decision-key")
            case "decision-model":
                self.system1_model = picked
                self._after_decision()

    def _after_decision(self, status: Content | str = "") -> None:
        """The decision model is set: pick the efficient model if setup started past it, else save."""
        if self.handoff is None:
            self._go("efficient", status)
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
            self.address = value.rstrip("/")
            if self.provider is not None and self.provider.kind == "custom":
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
        self.listed = found.models
        assert self.provider is not None
        self.pairs = pairs_for(self.provider.id, self.listed)
        self._go("pair" if self.pairs else "frontier", self._kept(found))

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
        provider: ProviderSpec | None = None
        if self.provider is not None and self.provider.kind == "custom":
            key_env = CUSTOM_KEY_ENV if CUSTOM_KEY_ENV in self.keys else None
            provider = ProviderSpec(base_url=self.address, api_key_env=key_env)
        elif self.provider is not None and self.provider.kind == "local":
            default = PROVIDERS[self.provider.id].base_url
            if self.address and self.address != default:
                provider = PROVIDERS[self.provider.id].model_copy(update={"base_url": self.address})
        # A key typed in that is already exported with the same value needs no keeping.
        keys = {name: value for name, value in self.keys.items() if self.env.get(name) != value}
        setup = Setup(self.system2, self.option, self.system1_model, keys, provider, self.handoff)
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

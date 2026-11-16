"""The setup screen: what `intern` opens on when it has no models to run on, and what `/setup` brings back.

                         ▗▄▄ INTERN ▄▄▖            (the wordmark)
            ● Welcome ── ○ System 2 ── ○ System 1 ── ○ Ready
    ╭────────────────────────────────────────────────────────────╮
    │ Step 1 of 2 · System 2, the agent model                    │
    │ System 2 does the work: it reads, reasons and calls tools. │
    │ ┌ Filter… ───────────────────────────────────────────────┐ │
    │ POPULAR                                                    │
    │ ● Anthropic        Claude Sonnet, Opus and Haiku   ✓ key   │
    │   OpenRouter       hundreds of models on one key           │
    │   ...                                                      │
    ╰────────────────────────────────────────────────────────────╯
             Enter continues · Esc goes back · Ctrl+O opens the key page

It explains what Intern is, then walks through the two models it needs: System 2's provider, its key (checked with
the provider before it is kept) and its model; then System 1's decision model and its key, Drex Max on OpenRouter
first, reusing an OpenRouter key already given. Nothing on it is an error to read and give up on: a refused key, a
server that does not answer or a subscription Intern cannot sign in with each says what to do instead, on the step
it happened. The last step saves (`onboarding.save`) and the screen dismisses with what it saved, for the app to
open the session on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
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
    PROVIDER_BY_ID,
    SYSTEM1_BY_ID,
    SYSTEM1_OPTIONS,
    SYSTEM2_PROVIDERS,
    KeyCheck,
    OpenRouterSignIn,
    ProviderOption,
    Saved,
    Setup,
    SignInFailed,
    System1Option,
    check_system1,
    check_system2,
    key_found,
    save,
    suggested_models,
)
from intern.secret_store import MIN_SECRET_CHARS
from intern.tui.pickers import Choice, fit, ranked
from intern.tui.theme import BRAND, S_HEAD, S_INK, S_MUTED, S_MUTED_ITALIC, S_OK, S_OK_BOLD, S_WARN
from intern.tui.welcome import ABOUT, Mascot, wordmark

Step = Literal[
    "welcome", "provider", "unavailable", "address", "key", "model", "system1", "system1-key", "system1-model", "done"
]
STAGES: tuple[tuple[str, tuple[Step, ...]], ...] = (
    ("Welcome", ("welcome",)),
    ("System 2 · the agent", ("provider", "unavailable", "address", "key", "model")),
    ("System 1 · the judge", ("system1", "system1-key", "system1-model")),
    ("Ready", ("done",)),
)
"""The steps as the progress line shows them."""
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
        start: Literal["welcome", "system1"] = "welcome",
        system2: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__()
        self.home = home
        self.env = dict(env)
        self.transport = transport
        self.step: Step = start
        self.trail: list[Step] = []
        """The steps taken, for Esc to go back through."""
        self.provider: ProviderOption | None = None
        self.address = ""
        self.listed: tuple[str, ...] = ()
        self.system2 = system2
        """`provider/model`; given when setup starts at System 1 (System 2 already works)."""
        self.option: System1Option | None = None
        self.system1_listed: tuple[str, ...] = ()
        self.system1_model = ""
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
        self.query_one("#setup-stages", Static).update(self._stages())
        filter_line, options, line = (
            self.query_one("#setup-filter", Input),
            self.query_one("#setup-options", OptionList),
            self.query_one("#setup-input", Input),
        )
        listing = step in ("provider", "model", "system1", "system1-model")
        self.query_one("#setup-wordmark").display = not listing or self.app.size.height >= ROOMY_ROWS
        filter_line.display = options.display = listing
        line.display = step in ("address", "key", "system1-key")
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
            "welcome": "Enter starts setup · Esc skips it",
            "unavailable": "Enter or Esc goes back to the list",
            "done": "Enter starts your first session",
        }.get(self.step, "Enter continues · Esc goes back")
        if self.step in ("key", "system1-key"):
            keys += " · Ctrl+O opens the key page"
        if self.step in ("model", "system1-model"):
            keys += " · a typed name that is not listed is used as is"
        return Content.styled(keys, S_MUTED)

    # -- each step: (title, body) --------------------------------------------------------------------------------

    def _welcome(self) -> tuple[str, Content]:
        self.query_one("#setup-about", Static).update(
            Content.assemble(
                ("Welcome to Intern\n\n", f"bold {BRAND}"),
                (ABOUT, S_INK),
                ("\n\nIt needs two models to run. Let's connect them: it takes about a minute.", S_MUTED),
            )
        )
        return "", Content("")

    def _provider(self) -> tuple[str, Content]:
        self.choices = self._provider_choices()
        body = Content.assemble(
            ("System 2 does the work: it reads, reasons and calls tools. Pick where it runs; ", S_INK),
            ("/system2", S_HEAD),
            (" changes it any time.", S_INK),
        )
        return "Step 1 of 2 · System 2, the agent model", body

    def _provider_choices(self) -> list[Choice]:
        ready = [p for p in SYSTEM2_PROVIDERS if p.kind == "key" and self._key_present(p.key_env)]
        choices = [Choice(p.id, p.name, "Ready to use", note=f"key found in ${p.key_env}") for p in ready]
        for p in SYSTEM2_PROVIDERS:
            if p in ready:
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
                ("Press Enter to sign in to OpenRouter in your browser", S_HEAD),
                (": it makes a key for Intern, nothing to copy. Or paste a key you have.", S_INK),
            ]
        if url and not browser:
            parts += [(" Make one at ", S_INK), (url, f"underline {BRAND}"), (" (Ctrl+O opens it).", S_MUTED)]
        parts.append(("\nIt is kept in your system keychain, and checked with the provider first.", S_MUTED))
        return Content.assemble(*parts)

    def _key(self) -> tuple[str, Content]:
        assert self.provider is not None
        p = self.provider
        if p.kind == "custom":
            return f"Connect {p.name}", self._key_body("endpoint's", CUSTOM_KEY_ENV, "", optional=True)
        return f"Connect {p.name}", self._key_body(p.name, p.key_env, p.key_url)

    def _model(self) -> tuple[str, Content]:
        assert self.provider is not None
        suggested = list(self.provider.suggested or (LOCAL_SUGGESTED if self.provider.kind == "local" else ()))
        option = ProviderOption(self.provider.id, self.provider.name, "", "", suggested=tuple(suggested))
        models = suggested_models(option, self.listed)
        top = {m for m in suggested if m in models}
        self.choices = [
            Choice(
                m,
                m,
                "Suggested" if m in top else f"Listed by {self.provider.name}",
                note="★ recommended" if i == 0 and m in top else "",
            )
            for i, m in enumerate(models)
        ]
        said = "Pick the model System 2 runs on." if models else "Type the model's name, as the provider names it."
        return f"{self.provider.name} · System 2's model", Content.styled(said, S_INK)

    def _system1(self) -> tuple[str, Content]:
        self.choices = []
        for option in SYSTEM1_OPTIONS:
            has_key = self._key_present(option.key_env)
            note = option.blurb + (" · uses your OpenRouter key" if has_key and "OPENROUTER" in option.key_env else "")
            if has_key and "OPENROUTER" not in option.key_env:
                note += f" · key found in ${option.key_env}"
            group = "Recommended" if option.recommended else "Other decision models"
            self.choices.append(Choice(option.id, option.name, group, note=("★ " if option.recommended else "") + note))
        body = Content.assemble(
            (
                "System 1 is a small, fast model that watches every turn: it nudges your intern out of circles, checks "
                "risky commands before they run and decides what it keeps in mind. ",
                S_INK,
            ),
            ("Drex Max", S_HEAD),
            (" is #1 on Artificial Analysis and the one we recommend.", S_INK),
        )
        return "Step 2 of 2 · System 1, the decision model", body

    def _system1_key(self) -> tuple[str, Content]:
        assert self.option is not None
        where = "OpenRouter" if self.option.backend == "decisions-api" else "Nace AI"
        return f"Connect {where} for {self.option.name.split(' · ')[0]}", self._key_body(
            where, self.option.key_env, self.option.key_url
        )

    def _system1_model(self) -> tuple[str, Content]:
        self.choices = [Choice(m, m, "Decision models on OpenRouter") for m in self.system1_listed]
        said = "Pick a decision model." if self.choices else "Type the decision model's id, e.g. nace-ai/drex-max."
        return "System 1's decision model", Content.styled(said, S_INK)

    def _done(self) -> tuple[str, Content]:
        assert self.saved is not None
        setup, saved = self.saved.setup, self.saved.saved
        rows: list[tuple[str, str]] = [
            ("System 2", setup.system2),
            ("System 1", f"{setup.system1.name.split(' · ')[0]} ({setup.system1_choice.removeprefix('decisions:')})"),
        ]
        where = {"keychain": "system keychain", "env-file": f"{names.HOME}/.env (yours only)"}
        for place in dict.fromkeys(saved.keys.values()):
            kept = ", ".join(name for name, stored in saved.keys.items() if stored == place)
            rows.append(("Keys", f"{kept} → {where[place]}"))
        rows.append(("Settings", str(saved.config).replace(str(Path.home()), "~")))
        self.query_one("#setup-about", Static).update(
            Content.assemble(
                ("You're all set\n\n", f"bold {BRAND}"),
                *(part for label, value in rows for part in ((f"{label:<10}", S_MUTED), (f"{value}\n", S_INK))),
                ("\nDescribe a task and your intern gets to work. ", S_INK),
                ("/setup", S_HEAD),
                (" brings this back, ", S_MUTED),
                ("/help", S_HEAD),
                (" lists the rest.", S_MUTED),
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
            typed = self.step in ("model", "system1-model")
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
        elif message.value.strip() and self.step in ("model", "system1-model"):
            self._picked(message.value.strip())

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
        elif self.step == "system1-key" and self.option is not None:
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
            case "provider":
                option = next(p for p in SYSTEM2_PROVIDERS if p.id == picked)
                self.provider, self.listed, self.address = option, (), ""
                if option.unavailable:
                    self._go("unavailable")
                elif option.kind in ("local", "custom"):
                    self._go("address")
                else:
                    self._go("key")
            case "model":
                assert self.provider is not None
                self.system2 = f"{self.provider.id}/{picked}"
                self._go("system1")
            case "system1":
                self.option = SYSTEM1_BY_ID[picked]
                if self.option.key_env in self.keys:
                    self._after_system1_key(self.keys[self.option.key_env], checked=None)
                else:
                    self._go("system1-key")
            case "system1-model":
                self.system1_model = picked
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
        if self.step == "system1-key":
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
        self._go("model", self._kept(found))

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
        self._after_system1_key(key, checked=found)

    def _after_system1_key(self, key: str, checked: KeyCheck | None) -> None:
        assert self.option is not None
        self.keys[self.option.key_env] = key
        if self.option.model:
            self._finish()
            return
        if checked is not None:
            self.system1_listed = checked.models
            self._go("system1-model", self._kept(checked))
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
        self._go("system1-model")

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
        setup = Setup(self.system2, self.option, self.system1_model, keys, provider)
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

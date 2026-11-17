"""The model pickers behind /system2 and /system1: a filter line over the catalog, in groups; Enter picks.

Rows are two aligned columns: how the model reads inside its group (its id without the group's prefix) and, dim,
what else is known about it (its display name, its provider). Known models come first. A name that is not
listed is accepted as typed, so a custom `provider/model` or a decision model the catalog missed can still be
chosen; the harness refuses it with its own reason if it cannot be resolved.

A picker whose notes are sentences (capabilities, runners, secrets) stacks each row instead: the label on a line of
its own, the note under it, wrapped at the label's indent rather than back under the label column, and a blank line
between rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.css.styles import RulesMap
from textual.geometry import Spacing
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.style import Style
from textual.visual import Padding, RenderOptions, Visual
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from intern.tui.theme import S_HEAD, S_MUTED, S_MUTED_ITALIC, S_OK_BOLD, S_TAB

LABEL_WIDTH = 56
KEYS = (("↑↓", "move"), ("Enter", "picks"), ("Esc", "closes"))
TYPED = "a name not listed is used as typed"
THINKING_DEFAULT = "default"
"""Shown for a model whose profile sets no effort: the model's own."""
MARK = 2
"""Cells before a label: the current row's `●`, or blank."""


@dataclass(frozen=True)
class Choice:
    id: str
    name: str
    group: str
    label: str = ""
    """How the id reads inside its group (without the group's prefix); the id itself when empty."""
    note: str = ""
    """Shown dim after the label, or under it in a stacked picker: the display name when it says more than the
    label, the provider for a known model, what a capability does."""

    @property
    def shown(self) -> str:
        return self.label or self.id


def match_score(choice: Choice, query: str) -> int:
    """3 for a prefix of the id or name, 2 for a substring, 1 for a subsequence (or no query), 0 for no match."""
    q = query.casefold()
    if not q:
        return 1
    for text in (choice.id.casefold(), choice.name.casefold(), choice.note.casefold()):
        if text.startswith(q):
            return 3
        if q in text:
            return 2
    letters = iter(f"{choice.id} {choice.name}".casefold())
    return 1 if all(ch in letters for ch in q) else 0


def ranked(choices: list[Choice], query: str) -> list[Choice]:
    """The choices that match, best first; ties keep the catalog's order (so groups stay together)."""
    scored = [(match_score(c, query), i, c) for i, c in enumerate(choices)]
    return [c for score, _, c in sorted(scored, key=lambda t: (-t[0], t[1])) if score]


def fit(text: str, width: int) -> str:
    """`text` in exactly `width` cells: padded, or cut in the middle with an ellipsis."""
    if len(text) <= width:
        return text.ljust(width)
    head = max(1, (width - 1) * 3 // 5)
    tail = width - 1 - head
    return text[:head] + "…" + text[-tail:] if tail > 0 else text[: width - 1] + "…"


class Stacked(Visual):
    """One option over several lines: its head, then its note wrapped under an indent, so a long note never runs
    back to the option's left edge."""

    def __init__(self, head: Content, note: Content, indent: int) -> None:
        self.head = head
        self.note = Padding(note, Spacing(0, 0, 0, indent))

    def get_optimal_width(self, rules: RulesMap, container_width: int) -> int:
        return max(
            self.head.get_optimal_width(rules, container_width), self.note.get_optimal_width(rules, container_width)
        )

    def get_height(self, rules: RulesMap, width: int) -> int:
        return self.head.get_height(rules, width) + self.note.get_height(rules, width)

    def render_strips(self, width: int, height: int | None, style: Style, options: RenderOptions) -> list[Strip]:
        return [
            *self.head.render_strips(width, None, style, options),
            *self.note.render_strips(width, None, style, options),
        ]


def label_parts(label: str, style: str) -> list[tuple[str, str]]:
    """A label in `style`, what follows its first ` · ` (`code search · System 1`) as a violet tab, so what System 1
    drives stands apart; filled, it still reads on the highlighted row, which is violet too."""
    name, dot, tag = label.partition(" · ")
    return [(name, style), ("  ", ""), (f" {tag} ", S_TAB)] if dot else [(name, style)]


class ModelPicker(ModalScreen[str | None]):
    """Dismisses with the picked model id, the typed name when nothing listed matches, or None."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "Page up", show=False),
        Binding("pagedown", "page(1)", "Page down", show=False),
        # Priority, so the filter line does not take the arrows; a picker without levels lets them through.
        Binding("left", "level(-1)", "Thinking", show=False, priority=True),
        Binding("right", "level(1)", "Thinking", show=False, priority=True),
        Binding("shift+tab", "level(1)", "Thinking", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        choices: list[Choice],
        current: str,
        *,
        hint: str = "",
        typed: bool = True,
        stacked: bool = False,
        levels: tuple[str, ...] = (),
        thinking: str | None = None,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.current = current
        self.hint = hint
        self.typed = typed
        """Whether a typed name that matches nothing listed is a valid pick (a model is; a capability is not)."""
        self.stacked = stacked
        """Whether each note goes under its label (sentences: capabilities, runners, secrets) rather than beside it
        (models: a name and a provider)."""
        self.levels = levels
        """The thinking levels ←/→ go through, set with the pick; none for a picker that has no say in it."""
        self.thinking = thinking
        self.thinking_touched = False
        """Whether ←/→ changed the level: only then does the pick carry one."""

    def compose(self) -> ComposeResult:
        count = len(self.choices)
        with Vertical(id="picker"):
            yield Static(
                Content.assemble((self.title_text, "bold"), (f"   {count} listed", S_MUTED)), id="picker-title"
            )
            yield Input(placeholder="Filter…", id="picker-filter")
            yield OptionList(id="picker-options")
            if self.levels:
                yield Static(self._thinking_line(), id="picker-thinking")
            yield Static(self._hint_lines(), id="picker-hint")

    def _hint_lines(self) -> Content:
        """What Enter does here on a line of its own, then the keys, so neither wraps into the other."""
        keys: list[tuple[str, str]] = []
        for key, does in KEYS:
            keys += [("   " if keys else "", S_MUTED), (key, S_HEAD), (f" {does}", S_MUTED)]
        if self.typed:
            keys.append((f"   {TYPED}", S_MUTED))
        return Content.assemble(*([(f"{self.hint}\n", S_MUTED)] if self.hint else []), *keys)

    def on_mount(self) -> None:
        self._refilter("")
        self.query_one(Input).focus()

    def _refilter(self, query: str) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        shown = ranked(self.choices, query)
        counts = Counter(c.group for c in shown)
        width = min(max((len(c.shown) for c in shown), default=0), LABEL_WIDTH)
        group: str | None = None
        first: int | None = None
        current: int | None = None
        for choice in shown:
            if choice.group != group:
                header = Content.assemble((choice.group.upper(), "bold"), (f"  {counts[choice.group]}", S_MUTED))
                # Blank lines above every group but the first set the groups apart: more than between stacked rows.
                above = (2 if self.stacked else 1) if group is not None else 0
                gap = Padding(header, Spacing(above, 0, 0, 0)) if above else header
                options.add_option(Option(gap, disabled=True))
                group = choice.group
            elif self.stacked:
                # A blank line between stacked rows, outside either, so the highlight covers just the one row.
                options.add_option(Option("", disabled=True))
            is_current = choice.id == self.current
            mark = ("● ", S_OK_BOLD) if is_current else " " * MARK
            current_tag = [("  current", S_MUTED_ITALIC)] if is_current else []
            if self.stacked:
                head = Content.assemble(mark, *label_parts(choice.shown, "bold"), *current_tag)
                prompt = Stacked(head, Content.styled(choice.note, S_MUTED), MARK) if choice.note else head
            else:
                parts: list[str | tuple[str, str]] = [mark, (fit(choice.shown, width), "bold" if is_current else "")]
                if choice.note:
                    parts.append(("  " + choice.note, S_MUTED))
                prompt = Content.assemble(*parts, *current_tag)
            options.add_option(Option(prompt, id=choice.id))
            index = options.option_count - 1
            first = index if first is None else first
            current = index if is_current else current
        if not shown:
            none = "nothing listed matches; Enter uses what you typed" if self.typed else "nothing listed matches"
            options.add_option(Option(Content.assemble((none, S_MUTED)), disabled=True))
        options.highlighted = first if query else (current if current is not None else first)

    @on(Input.Changed, "#picker-filter")
    def _on_filter(self, message: Input.Changed) -> None:
        self._refilter(message.value)

    @on(Input.Submitted, "#picker-filter")
    def _on_submit(self, message: Input.Submitted) -> None:
        options = self.query_one(OptionList)
        highlighted = options.highlighted_option
        if highlighted is not None and highlighted.id is not None:
            self.dismiss(highlighted.id)
        elif message.value.strip() and self.typed:
            self.dismiss(message.value.strip())

    @on(OptionList.OptionSelected, "#picker-options")
    def _on_pick(self, message: OptionList.OptionSelected) -> None:
        if message.option.id is not None:
            self.dismiss(message.option.id)

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        if delta > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_page(self, delta: int) -> None:
        options = self.query_one(OptionList)
        if delta > 0:
            options.action_page_down()
        else:
            options.action_page_up()

    def _thinking_line(self) -> Content:
        return Content.assemble(
            ("Thinking  ◀ ", S_MUTED),
            (self.thinking or THINKING_DEFAULT, "bold"),
            (" ▶   ←→ changes it; set with the pick", S_MUTED),
        )

    def action_level(self, delta: int) -> None:
        if not self.levels:
            raise SkipAction()  # the arrows are the filter line's
        if self.thinking in self.levels:
            self.thinking = self.levels[(self.levels.index(self.thinking) + delta) % len(self.levels)]
        else:
            self.thinking = self.levels[0] if delta > 0 else self.levels[-1]
        self.thinking_touched = True
        self.query_one("#picker-thinking", Static).update(self._thinking_line())

    def action_cancel(self) -> None:
        self.dismiss(None)


class SecretForm(ModalScreen[tuple[str, str] | None]):
    """/secrets set: a name and a hidden value; dismisses with both, or None. The value is never shown or kept
    after the screen closes."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, name: str = "") -> None:
        super().__init__()
        self.secret_name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(Content.assemble(("Store a secret in the keychain", "bold")), id="picker-title")
            yield Input(value=self.secret_name, placeholder="Name, e.g. GITHUB_TOKEN", id="secret-name")
            yield Input(placeholder="Value (hidden)", password=True, id="secret-value")
            yield Static(
                Content.assemble(
                    ("Commands see it as that environment variable; output shows [REDACTED].  ", S_MUTED),
                    ("Enter stores · Esc closes", S_MUTED),
                ),
                id="picker-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#secret-value" if self.secret_name else "#secret-name", Input).focus()

    @on(Input.Submitted, "#secret-name")
    def _to_value(self) -> None:
        self.query_one("#secret-value", Input).focus()

    @on(Input.Submitted, "#secret-value")
    def _submit(self) -> None:
        name = self.query_one("#secret-name", Input).value.strip()
        value = self.query_one("#secret-value", Input).value
        if name and value:
            self.dismiss((name, value))

    def action_cancel(self) -> None:
        self.dismiss(None)

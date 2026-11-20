"""The /theme picker: the themes, each with a swatch, its name and a few words; the screen behind takes each one as
the highlight reaches it, Enter keeps it and Esc goes back to the theme it opened on."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from intern.tui.theme import (
    S_HEAD,
    S_MUTED,
    S_MUTED_ITALIC,
    S_OK_BOLD,
    THEMES,
    ThemeInfo,
    active_theme,
    apply_theme,
    palette,
)

NAME_WIDTH = max(len(info.name) for info in THEMES)


def swatch(theme_id: str) -> Content:
    """The theme in six cells: its ink on its page, then its accent, so each row shows its own look whatever the
    theme in force."""
    c = palette(theme_id)
    return Content.assemble((" Aa ", f"bold {c['ink']} on {c['paper']}"), ("  ", f"on {c['brand']}"))


def theme_row(info: ThemeInfo, *, current: bool) -> Content:
    mark = ("● ", S_OK_BOLD) if current else "  "
    tag = [("  current", S_MUTED_ITALIC)] if current else []
    return Content.assemble(
        mark, swatch(info.id), "  ", (info.name.ljust(NAME_WIDTH), "bold"), "  ", (info.blurb, S_MUTED), *tag
    )


class ThemePicker(ModalScreen[str | None]):
    """Dismisses with the theme id Enter kept, or None after Esc, the theme it opened on back in force. Nothing is
    saved here: the caller keeps the pick."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self) -> None:
        super().__init__()
        self.original: str | None = None
        """The theme in force when the picker opened, which Esc goes back to."""

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-picker"):
            yield Static(
                Content.assemble(("Theme", "bold"), ("   each one shows as you move to it", S_MUTED)), id="theme-title"
            )
            yield OptionList(id="theme-options")
            keys = (("↑↓", "preview"), ("Enter", "keeps it"), ("Esc", "goes back"))
            parts: list[tuple[str, str]] = []
            for key, does in keys:
                parts += [("   " if parts else "", S_MUTED), (key, S_HEAD), (f" {does}", S_MUTED)]
            yield Static(Content.assemble(*parts), id="theme-hint")

    def on_mount(self) -> None:
        self.original = active_theme(self.app)
        options = self.query_one(OptionList)
        for info in THEMES:
            options.add_option(Option(theme_row(info, current=info.id == self.original), id=info.id))
        options.highlighted = next(i for i, info in enumerate(THEMES) if info.id == self.original)
        options.focus()

    @on(OptionList.OptionHighlighted, "#theme-options")
    def _preview(self, message: OptionList.OptionHighlighted) -> None:
        if message.option.id is not None:
            apply_theme(self.app, message.option.id)

    @on(OptionList.OptionSelected, "#theme-options")
    def _keep(self, message: OptionList.OptionSelected) -> None:
        if message.option.id is not None:
            self.dismiss(apply_theme(self.app, message.option.id))

    def action_cancel(self) -> None:
        apply_theme(self.app, self.original)
        self.dismiss(None)

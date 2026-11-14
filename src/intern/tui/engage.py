"""The engagement dashboard: a full screen over the store's sections (findings, hosts, endpoints,
specialists, activity, coverage, credentials, footholds), with ←→ or 1-8 picking a section, Enter
scrolling its list, Esc going back a level and q closing."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from intern.engagement import dash
from intern.engagement.models import State
from intern.engagement.store import EngagementStore

SECTION_TITLES = dash.SECTIONS


class SectionList(Static):
    """One section's rows, scrollable."""

    def on_mount(self) -> None:
        self.can_focus = True


class EngagementScreen(ModalScreen[None]):
    """The whole engagement, one section at a time."""

    CSS = """
    EngagementScreen { align: center middle; }
    #engage-box { width: 90%; height: 85%; border: round $accent; background: $surface; padding: 1 2; }
    #engage-title { text-style: bold; color: $accent; }
    #engage-hint { color: $text-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "prev_section", "←", show=False),
        Binding("right", "next_section", "→", show=False),
        Binding("escape,q", "close", "Close", show=False),
        *[Binding(str(i + 1), f"pick({i})", SECTION_TITLES[i], show=False) for i in range(len(SECTION_TITLES))],
    ]

    def __init__(self, store: EngagementStore, agents: list[dict[str, str]]) -> None:
        super().__init__()
        self.store = store
        self.agents = agents
        self.index = 0

    def compose(self) -> ComposeResult:
        state: State = self.store.state
        sections = dash.dash_sections(state)
        lines = sections[SECTION_TITLES[self.index]] or ["(nothing)"]
        if SECTION_TITLES[self.index] == "agents":
            lines = [f"{a.get('id', '?')} {a.get('role', '?')}: {a.get('status', '?')}" for a in self.agents] or [
                "no specialists dispatched"
            ]
        with Vertical(id="engage-box"):
            yield Static(f"engage {state.target} · {self.store.dir}", id="engage-title", classes="engage-header")
            yield Static(self._tabs(), id="engage-hint", classes="engage-header")
            yield SectionList("\n".join(lines), id="engage-section")

    def _tabs(self) -> str:
        return "  ".join(
            (f"[{i + 1}]" if i == self.index else str(i + 1)) + "·" + name for i, name in enumerate(SECTION_TITLES)
        )

    def _refresh(self) -> None:
        box = self.query_one("#engage-box", Vertical)
        box.remove_children()
        state: State = self.store.state
        sections = dash.dash_sections(state)
        lines = sections[SECTION_TITLES[self.index]] or ["(nothing)"]
        if SECTION_TITLES[self.index] == "agents":
            lines = [f"{a.get('id', '?')} {a.get('role', '?')}: {a.get('status', '?')}" for a in self.agents] or [
                "no specialists dispatched"
            ]
        box.mount(Static(f"engage {state.target} · {self.store.dir}", id="engage-title", classes="engage-header"))
        box.mount(Static(self._tabs(), id="engage-hint", classes="engage-header"))
        box.mount(SectionList("\n".join(lines), id="engage-section"))

    def action_prev_section(self) -> None:
        self.index = (self.index - 1) % len(SECTION_TITLES)
        self._refresh()

    def action_next_section(self) -> None:
        self.index = (self.index + 1) % len(SECTION_TITLES)
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self, index: int) -> None:
        self.index = max(0, min(index, len(SECTION_TITLES) - 1))
        self._refresh()

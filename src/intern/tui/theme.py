"""The look: paper, ink and one accent, in four themes: Light, Dark, Sand and Midnight.

An off-white page, white cards, hairline rules, near-black ink, a warm gray for what is secondary. Intern itself is
drawn in ink, as the Nace mark is: the mark (the cube with eyes, `widgets.mark`) and the wordmark's block letters.
The accent (`brand`) is for what is alive in the session: the panels' tabs and borders, the prompt's bar and cursor,
System 1's picks, its searches and compactions as they run, and the wordmark's shadow; green only for what went right,
amber for what needs a look, red for what went wrong. The dark look swaps paper and ink, each a little grayed down,
and takes the mint as the brand has it; the light look takes a deeper shade of it, which reads on paper. Sand is the
light look on the brand's sand neutrals, with the same deep mint; Midnight is a dark look on deep blue, its accent
a moonlit periwinkle.

Widgets name colours by role (`INK`, `S_MUTED`, ...), which are the theme's variables: `Content` reads them when
it draws, so a switch of theme redraws everything in the other look.

The public surface, for `/theme` and the setup screen: `THEMES` (each theme's id, name and blurb), `DEFAULT_THEME`,
`apply_theme(app, theme_id)` to switch a running app, `save_theme(home, theme_id)` to keep the choice in
`config.toml`, `palette(theme_id)` for its colours and `active_theme(app)` for the one in force.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from textual.app import App
from textual.theme import Theme

from intern import names
from intern.config import CONFIG_FILE

CONFIG_KEY = "theme"
"""The top-level key in `config.toml` that names the theme."""

LIGHT = {
    "paper": "#fbfbf9",
    "card": "#ffffff",
    "wash": "#f4f4f0",
    "rule": "#e8e8e3",
    "ink": "#0c0c09",
    "ink-soft": "#2b2b22",
    "muted": "#7c7c67",
    "green": "#3fb55a",
    "amber": "#b7791f",
    "red": "#b23a3a",
    "select": "#dedcd3",
    "brand": "#00795f",
}
DARK = {
    "paper": "#141411",
    "card": "#1b1b17",
    "wash": "#20201b",
    "rule": "#2d2d26",
    "ink": "#e6e5de",
    "ink-soft": "#c4c3b8",
    "muted": "#8d8c78",
    "green": "#4cbf66",
    "amber": "#d4973a",
    "red": "#d9625f",
    "select": "#3a3a31",
    "brand": "#0fdbb4",
}
"""`select`: text the mouse selected, a shade of the page, not ink at half strength. `brand`: the Nace accent, a mint
(OKLCH 0.7953 0.1501 173.8) as the brand has it on the dark page and a deeper shade of the same hue on the light one,
each strong enough against its page to be read as text."""
SAND = {
    "paper": "#f3ede1",
    "card": "#faf7f0",
    "wash": "#ebe3d3",
    "rule": "#dccfb7",
    "ink": "#241e15",
    "ink-soft": "#463b2b",
    "muted": "#6e6250",
    "green": "#2c7a3a",
    "amber": "#94570c",
    "red": "#a63a2b",
    "select": "#e2d4b8",
    "brand": "#00715a",
}
"""The brand's sand neutrals: a warm page, cards a shade lighter, brown ink; the light look's deep mint."""
MIDNIGHT = {
    "paper": "#0b1020",
    "card": "#10182c",
    "wash": "#162038",
    "rule": "#25314f",
    "ink": "#e1e7f4",
    "ink-soft": "#bac5db",
    "muted": "#8391b2",
    "green": "#5ccf8e",
    "amber": "#e8b25a",
    "red": "#f08680",
    "select": "#2b3a5f",
    "brand": "#97a9ff",
}
"""A deep blue night: cards a shade up from the page, ink a cool white, a periwinkle accent that is never a status
colour."""


@dataclass(frozen=True)
class ThemeInfo:
    """A theme as a picker lists it."""

    id: str
    """What `config.toml` and `/theme <id>` name it by."""
    name: str
    blurb: str
    """What it looks like, in a few words."""


@dataclass(frozen=True)
class _Look:
    info: ThemeInfo
    colours: dict[str, str]
    dark: bool


_LOOKS = (
    _Look(ThemeInfo("light", "Light", "paper, ink and deep mint"), LIGHT, dark=False),
    _Look(ThemeInfo("dark", "Dark", "charcoal page, bright mint accent"), DARK, dark=True),
    _Look(ThemeInfo("sand", "Sand", "warm sand page, brown ink"), SAND, dark=False),
    _Look(ThemeInfo("midnight", "Midnight", "deep blue night, periwinkle accent"), MIDNIGHT, dark=True),
)
_BY_ID = {look.info.id: look for look in _LOOKS}

THEMES: tuple[ThemeInfo, ...] = tuple(look.info for look in _LOOKS)
"""Every theme, in the order a picker lists them."""
DEFAULT_THEME = "light"

PAPER = "$background"
CARD = "$surface"
WASH = "$wash"
RULE = "$rule"
INK = "$foreground"
INK_SOFT = "$primary"
MUTED = "$muted"
GREEN = "$success"
AMBER = "$warning"
RED = "$error"
SELECT = "$select"
BRAND = "$brand"
# Content styles, by role, so widgets never name a colour.
S_INK = INK
S_RULE = RULE
S_HEAD = f"bold {INK}"
S_MUTED = MUTED
S_HEAD_MUTED = f"bold {MUTED}"
S_MUTED_ITALIC = f"italic {MUTED}"
S_OK = GREEN
S_OK_BOLD = f"bold {GREEN}"
S_WARN = AMBER
S_WARN_BOLD = f"bold {AMBER}"
S_BAD = RED
S_BAD_BOLD = f"bold {RED}"


S_BOX = f"bold {INK} on {SELECT}"
"""The thinking level's box on the masthead, filled so a change of level is seen at once; `S_BOX_MUTED` its label."""
S_BOX_MUTED = f"{MUTED} on {SELECT}"

S_BRAND_BOLD = f"bold {BRAND}"
"""A hand-over, wherever it shows: its sign `⇢`, the latest P(ready) column and the model that took over."""

S_TAB = f"bold {PAPER} on {BRAND}"
"""A panel's name on its header, a tab in the accent; also the `System 1` tag on what System 1 drives in
`/caps`."""

S_WORDMARK = f"bold {BRAND}"
WORDMARK = names.TITLE


def make_theme(name: str, c: Mapping[str, str], *, dark: bool) -> Theme:
    return Theme(
        name=name,
        primary=c["ink-soft"],
        secondary=c["muted"],
        accent=c["ink"],
        foreground=c["ink"],
        background=c["paper"],
        surface=c["card"],
        panel=c["wash"],
        success=c["green"],
        warning=c["amber"],
        error=c["red"],
        dark=dark,
        variables={
            "rule": c["rule"],
            "wash": c["wash"],
            "muted": c["muted"],
            "select": c["select"],
            "brand": c["brand"],
            "block-cursor-background": c["brand"],
            "block-cursor-foreground": c["paper"],
            "block-cursor-text-style": "none",
            "block-cursor-blurred-background": c["wash"],
            "block-cursor-blurred-foreground": c["ink"],
            "block-cursor-blurred-text-style": "none",
            "block-hover-background": c["wash"],
            "input-cursor-background": c["brand"],
            "input-cursor-foreground": c["paper"],
            "input-selection-background": c["select"],
            "screen-selection-background": c["select"],
            "screen-selection-foreground": c["ink"],
            "border": c["rule"],
            "border-blurred": c["rule"],
            "scrollbar": c["wash"],
            "scrollbar-hover": c["rule"],
            "scrollbar-active": c["rule"],
            "scrollbar-background": c["paper"],
            "scrollbar-background-hover": c["paper"],
            "scrollbar-background-active": c["paper"],
            "link-color": c["ink"],
        },
    )


def textual_name(theme_id: str) -> str:
    """The name the theme is registered under in Textual, apart from Textual's own themes."""
    return f"intern-{theme_id}"


TEXTUAL_THEMES = {look.info.id: make_theme(textual_name(look.info.id), look.colours, dark=look.dark) for look in _LOOKS}
LIGHT_THEME = TEXTUAL_THEMES["light"]
DARK_THEME = TEXTUAL_THEMES["dark"]


def resolve_theme(theme_id: str | None) -> str:
    """`theme_id` when it names a theme (its id or its name, in any case), else `DEFAULT_THEME`."""
    wanted = (theme_id or "").strip().casefold()
    for info in THEMES:
        if wanted in (info.id, info.name.casefold()):
            return info.id
    return DEFAULT_THEME


def theme_info(theme_id: str | None) -> ThemeInfo:
    """The theme `theme_id` names, or the default's."""
    return _BY_ID[resolve_theme(theme_id)].info


def palette(theme_id: str | None) -> dict[str, str]:
    """The theme's colours by role (`paper`, `card`, `ink`, `muted`, `brand`, ...), for what is drawn in raw colours:
    the mark, the entering page's Intern, a swatch."""
    return _BY_ID[resolve_theme(theme_id)].colours


def register_themes(app: App[Any]) -> None:
    """Make every theme known to `app`; done before its stylesheet is read, which names their variables."""
    for theme in TEXTUAL_THEMES.values():
        if app.get_theme(theme.name) is None:
            app.register_theme(theme)


def apply_theme(app: App[Any], theme_id: str | None) -> str:
    """Switch `app` to the theme `theme_id` names, live: every widget redraws in it. An unknown or missing id gives the
    default. Returns the id applied. Nothing is saved: `save_theme` keeps a choice."""
    resolved = resolve_theme(theme_id)
    register_themes(app)
    app.theme = textual_name(resolved)
    return resolved


def active_theme(app: App[Any]) -> str:
    """The id of the theme `app` is in; for a theme not Intern's own, Light or Dark as it is light or dark."""
    for theme_id, theme in TEXTUAL_THEMES.items():
        if app.theme == theme.name:
            return theme_id
    return "dark" if app.current_theme.dark else "light"


def app_palette(app: App[Any]) -> dict[str, str]:
    """The colours of the theme `app` is in."""
    return palette(active_theme(app))


def save_theme(home: Path, theme_id: str) -> Path:
    """Keep `theme = "<id>"` at the top of `config.toml` in `home`, every other line, comments included, as it was.
    Returns the file. Raises `OSError` when it cannot be written, and tomlkit's `ParseError` when the file is not
    TOML."""
    path = home / CONFIG_FILE
    document = tomlkit.parse(path.read_text()) if path.is_file() else tomlkit.document()
    document[CONFIG_KEY] = resolve_theme(theme_id)  # tomlkit puts a new top-level key before the tables
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(document))
    return path

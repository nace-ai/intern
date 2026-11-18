"""The look: paper, ink and one accent, the Nace mint, in light and dark.

An off-white page, white cards, hairline rules, near-black ink, a warm gray for what is secondary. Intern itself is
drawn in ink, as the Nace mark is: the mark (the cube with eyes, `widgets.mark`) and the wordmark's block letters.
The accent (`brand`) is for what is alive in the session: the panels' tabs and borders, the prompt's bar and cursor,
System 1's picks and the wordmark's shadow; green only for what went right, amber for what needs a look, red for what
went wrong. The dark look swaps paper and ink, each a little grayed down, and takes the mint as the brand has it; the
light look takes a deeper shade of it, which reads on paper (`/dark`, `/light`).

Widgets name colours by role (`INK`, `S_MUTED`, ...), which are the theme's variables: `Content` reads them when
it draws, so a switch of theme redraws everything in the other look.
"""

from __future__ import annotations

from textual.theme import Theme

from intern import names

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
    "glint": "#22c7a4",
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
    "glint": "#c2fbe9",
}
"""`select`: text the mouse selected, a shade of the page, not ink at half strength. `brand`: the Nace accent, a mint
(OKLCH 0.7953 0.1501 173.8) as the brand has it on the dark page and a deeper shade of the same hue on the light one,
each strong enough against its page to be read as text. `glint`: the light that sweeps across the accent where
System 1 is at work (the `auto` thinking level), lighter than it in both looks."""

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
GLINT = "$glint"
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
S_BOX_AUTO = f"bold {BRAND} on {SELECT}"
"""The `auto` level in the box, and the pips of the level System 1 picked, in the accent: System 1's choice;
`S_BOX_GLINT` the light that sweeps across it."""
S_BOX_GLINT = f"bold {GLINT} on {SELECT}"

S_BRAND_BOLD = f"bold {BRAND}"
"""System 1's pick of the thinking level, wherever it shows: its sign `✦`, the level picked and its column."""

S_TAB = f"bold {PAPER} on {BRAND}"
"""A panel's name on its header, a tab in the accent; also the `System 1` tag on what System 1 drives in
`/caps`."""

S_WORDMARK = f"bold {BRAND}"
WORDMARK = names.TITLE


def make_theme(name: str, c: dict[str, str], *, dark: bool) -> Theme:
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
            "glint": c["glint"],
            "block-cursor-background": c["brand"],
            "block-cursor-foreground": c["paper"],
            "block-cursor-text-style": "none",
            "block-cursor-blurred-background": c["wash"],
            "block-cursor-blurred-foreground": c["ink"],
            "block-cursor-blurred-text-style": "none",
            "block-hover-background": c["wash"],
            "input-cursor-background": c["brand"],
            "input-cursor-foreground": c["paper"],
            "input-selection-background": c["rule"],
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


LIGHT_THEME = make_theme("intern-light", LIGHT, dark=False)
DARK_THEME = make_theme("intern-dark", DARK, dark=True)

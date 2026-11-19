"""The themes: each reads well, a pick is shown live and kept in config.toml, and the app opens in the saved one."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("textual", reason="needs the tui extra")

from textual.app import App

from intern.config import load_user_config
from intern.providers.fake import Turn
from intern.tui import widgets
from intern.tui.app import HarnessApp, Models
from intern.tui.theme import (
    DEFAULT_THEME,
    THEMES,
    active_theme,
    apply_theme,
    palette,
    resolve_theme,
    save_theme,
)
from intern.tui.theme_picker import ThemePicker
from intern.tui.widgets import Prompt

from .test_tui import SIZE, make_opener, send, transcript_text, wait_for


@pytest.fixture(autouse=True)
def no_shift_on_this_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enter reads the Mac's own Shift key (`widgets.shift_held`): a Shift held on the machine running the tests
    would make an Enter a new line."""
    monkeypatch.setattr(widgets, "shift_held", lambda: False)


async def say(app: HarnessApp, pilot, text: str) -> None:
    """`send`, once the prompt has the focus back: after the session opens, or after a picker closes."""
    await wait_for(lambda: isinstance(app.focused, Prompt))
    await send(app, pilot, text)


def luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


READS = [
    # (text, ground, least WCAG ratio): body text at AA's 4.5, what is secondary or boxed a little under.
    ("ink", "paper", 7.0),
    ("ink", "card", 7.0),
    ("ink", "wash", 7.0),
    ("ink", "select", 7.0),
    ("ink-soft", "card", 7.0),
    ("muted", "paper", 4.5),
    ("muted", "card", 4.5),
    ("muted", "wash", 4.0),
    ("muted", "select", 3.5),
    ("green", "card", 4.5),
    ("green", "paper", 4.5),
    ("amber", "card", 4.5),
    ("amber", "paper", 4.5),
    ("red", "card", 4.5),
    ("red", "paper", 4.5),
    ("brand", "card", 4.5),
    ("brand", "paper", 4.5),
    ("paper", "brand", 4.5),  # a panel's tab, the picker's cursor
]


BRAND_LOOKS = {
    "light": {"muted": "#7c7c67", "green": "#3fb55a", "amber": "#b7791f"},
    "dark": {"muted": "#8d8c78", "red": "#d9625f"},
}
"""Light and Dark are the Nace brand's looks (#144), kept as the brand drew them: these are the colours that would
fail the contrast floor below, pinned so a theme change does not move them by accident."""


@pytest.mark.parametrize("theme_id", [info.id for info in THEMES if info.id not in BRAND_LOOKS])
@pytest.mark.parametrize(("text", "ground", "least"), READS)
def test_every_new_themes_text_reads_on_the_grounds_it_is_drawn_on(theme_id: str, text: str, ground: str, least: float):
    colours = palette(theme_id)
    assert contrast(colours[text], colours[ground]) >= least, f"{theme_id}: {text} on {ground}"


@pytest.mark.parametrize("theme_id", BRAND_LOOKS)
def test_light_and_dark_keep_the_brand_colours(theme_id: str):
    colours = palette(theme_id)
    assert {role: colours[role] for role in BRAND_LOOKS[theme_id]} == BRAND_LOOKS[theme_id]


def test_the_themes_have_distinct_ids_every_colour_role_and_a_name_and_blurb_to_list():
    assert len({info.id for info in THEMES}) == len(THEMES) >= 4
    assert DEFAULT_THEME in {info.id for info in THEMES}
    roles = set(palette(DEFAULT_THEME))
    for info in THEMES:
        assert set(palette(info.id)) == roles
        assert info.name and 3 <= len(info.blurb.split()) <= 6


def test_a_theme_is_named_by_its_id_or_name_and_anything_else_is_the_default():
    assert resolve_theme("midnight") == resolve_theme("Midnight") == "midnight"
    assert resolve_theme(None) == resolve_theme("") == resolve_theme("solarized") == DEFAULT_THEME


def test_the_theme_is_saved_at_the_top_of_config_toml_keeping_the_rest(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('# my models\nsystem2 = "fake/model"  # the agent\n\n[system1]\nmodel = "drex-latest"\n')
    save_theme(tmp_path, "sand")
    save_theme(tmp_path, "midnight")
    text = config.read_text()
    assert "# my models" in text and "# the agent" in text and text.count("theme =") == 1
    loaded = load_user_config(tmp_path)
    assert loaded.theme == "midnight" and loaded.system2 == "fake/model" and loaded.system1.model == "drex-latest"


def test_a_theme_that_is_not_a_name_does_not_stop_the_config_loading(tmp_path: Path):
    (tmp_path / "config.toml").write_text("theme = 3\n")
    assert load_user_config(tmp_path).theme is None


async def test_any_app_can_be_switched_to_a_theme_live():
    """What the setup screen does on its own app: the themes are registered on first use."""
    app = App[None]()
    async with app.run_test() as pilot:
        assert apply_theme(app, "midnight") == "midnight"
        await pilot.pause()
        assert app.current_theme.background == palette("midnight")["paper"]
        assert active_theme(app) == "midnight"
        assert apply_theme(app, "nonsense") == DEFAULT_THEME


@pytest.mark.parametrize("theme_id", [info.id for info in THEMES])
async def test_the_app_opens_in_the_theme_it_is_given(tmp_path: Path, theme_id: str):
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), picks=Models(theme=theme_id), home=tmp_path)
    async with app.run_test(size=SIZE):
        await wait_for(lambda: app.session is not None)
        assert active_theme(app) == theme_id
        assert app.screen.styles.background.hex.lower() == palette(theme_id)["paper"]
        await app.action_quit()


async def test_an_unknown_saved_theme_opens_in_the_default(tmp_path: Path):
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), picks=Models(theme="neon"), home=tmp_path)
    async with app.run_test(size=SIZE):
        assert active_theme(app) == DEFAULT_THEME
        await app.action_quit()


async def test_the_theme_picker_previews_each_theme_esc_goes_back_and_enter_keeps_one(tmp_path: Path):
    config = tmp_path / "config.toml"
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), picks=Models(theme="dark"), home=tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await say(app, pilot, "/theme")
        await wait_for(lambda: isinstance(app.screen, ThemePicker))
        await pilot.press("down")  # Dark → Sand, shown behind the picker at once
        await pilot.pause()
        assert active_theme(app) == "sand"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ThemePicker)
        assert active_theme(app) == "dark" and not config.exists()

        await say(app, pilot, "/theme")
        await wait_for(lambda: isinstance(app.screen, ThemePicker))
        await pilot.press("down", "down", "enter")  # Dark → Sand → Midnight, kept
        await wait_for(lambda: config.is_file())
        assert active_theme(app) == "midnight" and load_user_config(tmp_path).theme == "midnight"
        assert app.pending.theme == "midnight"  # what /reload reopens on
        await app.action_quit()


async def test_theme_sets_one_by_name_and_dark_and_light_are_shortcuts_that_keep_it_too(tmp_path: Path):
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), home=tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await say(app, pilot, "/theme Sand")
        await wait_for(lambda: load_user_config(tmp_path).theme == "sand")
        assert active_theme(app) == "sand"

        await say(app, pilot, "/theme neon")
        await wait_for(lambda: "No theme neon" in transcript_text(app))
        assert active_theme(app) == "sand" and load_user_config(tmp_path).theme == "sand"

        await say(app, pilot, "/dark")
        await wait_for(lambda: load_user_config(tmp_path).theme == "dark")
        assert active_theme(app) == "dark"
        await say(app, pilot, "/light")
        await wait_for(lambda: load_user_config(tmp_path).theme == "light")
        assert active_theme(app) == "light"
        await app.action_quit()


async def test_a_theme_that_cannot_be_saved_still_shows_and_says_why(tmp_path: Path):
    (tmp_path / "config.toml").write_text("this is [not toml\n")
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), home=tmp_path)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await say(app, pilot, "/theme midnight")
        await wait_for(lambda: "Midnight is on for this session but not saved" in transcript_text(app))
        assert active_theme(app) == "midnight"
        assert (tmp_path / "config.toml").read_text() == "this is [not toml\n"
        await app.action_quit()

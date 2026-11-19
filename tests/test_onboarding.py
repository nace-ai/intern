"""First-run setup: the screen a new user meets, what it checks, and what it saves."""

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

pytest.importorskip("textual", reason="needs the tui extra")

from intern.config import Bootstrap, Options, read_env_file
from intern.onboarding import (
    PROVIDER_BY_ID,
    SYSTEM1_BY_ID,
    OpenRouterSignIn,
    Setup,
    check_system2,
    save,
)
from intern.providers.fake import Turn
from intern.tui.app import HarnessApp, Models, SetupHooks
from intern.tui.onboarding import OnboardingScreen
from tests.test_tui import SIZE, make_opener, transcript_text, wait_for

NO_KEYCHAIN = {"PATH": "/nonexistent"}
"""No `security` or `secret-tool` to find: keys go to the config home's .env."""


def provider(requests: list[httpx.Request], *, refuse: str = "") -> httpx.MockTransport:
    """Anthropic and OpenRouter as the checks see them; a key equal to `refuse` gets a 401."""

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if refuse and key == refuse:
            return httpx.Response(401, json={"error": "invalid key"})
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"label": "intern"}})
        if request.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}]})
        return httpx.Response(200, json={"data": [{"id": "nace-ai/drex-max"}]})

    return httpx.MockTransport(answer)


async def test_a_first_launch_walks_through_both_models_and_opens_the_session_on_them(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN}
    opened: list[Models] = []
    fake = make_opener(tmp_path, [Turn(text="ok")], configured_system2=None)

    async def open_session(emit, picks: Models):
        opened.append(replace(picks))
        # The real provider is not reached here: the session runs on the scripted one.
        return await fake(emit, replace(picks, system2="fake/model" if picks.system2 else None, system1=None))

    async def default() -> str | None:
        return None

    requests: list[httpx.Request] = []
    hooks = SetupHooks(env=lambda: env, configured=env.update, transport=provider(requests))
    app = HarnessApp(open_session, default_system2=default, home=home, setup=hooks)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        assert "Cannot open" not in transcript_text(app)  # nothing to fix is said as an error
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter")  # welcome
        await pilot.press(*"choose", "enter")  # each model yourself, not a recommended combination
        await pilot.press(*"anthropic", "enter")
        assert screen.step == "key"
        await pilot.press(*"sk-ant-test-key-123", "enter")
        await wait_for(lambda: screen.step == "model")
        await pilot.press("enter")  # the suggested model, first
        assert screen.system2 == "anthropic/claude-sonnet-5-5"
        assert screen.step == "system1"
        await pilot.press("enter")  # Drex Max, recommended and first
        assert screen.step == "system1-key"
        await pilot.press(*"sk-or-v1-test-key", "enter")
        await wait_for(lambda: screen.step == "handoff")
        await pilot.press("enter")  # Haiku, the cheaper sibling, preselected
        await wait_for(lambda: screen.step == "done")
        await pilot.press("enter")
        await wait_for(lambda: app.session is not None)
        await app.action_quit()

    assert opened[-1].system2 == "anthropic/claude-sonnet-5-5"
    assert opened[-1].system1 == "decisions:nace-ai/drex-max"
    assert opened[-1].auto
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "anthropic/claude-sonnet-5-5"
    assert config["system1"] == {
        "backend": "decisions-api",
        "model": "nace-ai/drex-max",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    }
    assert config["capabilities"] == {"handoff": {"to": "anthropic/claude-haiku-5-5"}}
    # Keys are kept out of config.toml, readable by the user only, and in use in this process at once.
    assert "sk-" not in (home / "config.toml").read_text()
    kept = read_env_file(home / ".env")
    assert kept == {"ANTHROPIC_API_KEY": "sk-ant-test-key-123", "OPENROUTER_API_KEY": "sk-or-v1-test-key"}
    assert (home / ".env").stat().st_mode & 0o077 == 0
    assert env["OPENROUTER_API_KEY"] == "sk-or-v1-test-key"
    # What it saved is what a session started later from the config reads.
    later = Bootstrap(Options(cwd=tmp_path), {**NO_KEYCHAIN, "INTERN_HOME": str(home), **kept})
    assert later.default_system2() == "anthropic/claude-sonnet-5-5"
    assert later.system1_choice() == "decisions:nace-ai/drex-max"
    assert later.capability_settings().handoff.to == "anthropic/claude-haiku-5-5"


async def test_a_recommended_combination_asks_only_for_the_key_it_lacks_and_saves_all_three_models(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN}
    requests: list[httpx.Request] = []
    screen = OnboardingScreen(home=home, env=env, transport=provider(requests))
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter")  # welcome
        assert screen.step == "recommended"
        await pilot.press("enter")  # One key for everything, first
        assert screen.step == "key"
        await pilot.press(*"sk-or-v1-test-key", "enter")
        # The same OpenRouter key runs System 1: no second key step, and no model steps.
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    assert screen.trail == ["welcome", "recommended", "key"]
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "openrouter/anthropic/claude-sonnet-5.5"
    assert config["system1"] == {
        "backend": "decisions-api",
        "model": "nace-ai/drex-max",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    }
    assert config["capabilities"] == {"handoff": {"to": "openrouter/deepseek/deepseek-v4.1-flash"}}
    assert "auto" not in config
    assert read_env_file(home / ".env") == {"OPENROUTER_API_KEY": "sk-or-v1-test-key"}


async def test_choosing_no_hand_over_writes_none_and_turns_auto_mode_off(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('[capabilities.handoff]\nto = "anthropic/claude-haiku-5-5"\nthreshold = 0.8\n')
    env = {**NO_KEYCHAIN, "ANTHROPIC_API_KEY": "sk-ant-in-env", "OPENROUTER_API_KEY": "sk-or-v1-in-env"}

    def listing(request: httpx.Request) -> httpx.Response:
        # Anthropic lists no Haiku here: the cheaper sibling is not offered, and off is what Enter picks.
        if request.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-opus-5-5"}]})
        return provider([]).handle_request(request)

    screen = OnboardingScreen(home=home, env=env, transport=httpx.MockTransport(listing))
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter", *"choose", "enter", *"anthropic", "enter", "enter")  # the key in the environment
        await wait_for(lambda: screen.step == "model")
        await pilot.press("enter", "enter", "enter")  # Sonnet, Drex Max, the OpenRouter key in the environment
        await wait_for(lambda: screen.step == "handoff")
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    config = tomllib.loads((home / "config.toml").read_text())
    assert config["auto"] is False
    assert config["capabilities"] == {"handoff": {"threshold": 0.8}}
    later = Bootstrap(Options(cwd=tmp_path), {**env, "INTERN_HOME": str(home)})
    assert later.capability_settings().handoff.to is None
    assert later.config.auto is False


async def test_a_refused_key_keeps_the_step_and_says_how_to_fix_it(tmp_path: Path):
    requests: list[httpx.Request] = []
    screen = OnboardingScreen(home=tmp_path, env=NO_KEYCHAIN, transport=provider(requests, refuse="sk-bad-key-000"))

    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter", *"choose", "enter", *"anthropic", "enter", *"sk-bad-key-000", "enter")
        await wait_for(lambda: bool(requests) and not screen.busy)
        await pilot.pause()
        assert screen.step == "key"
        status = str(screen.query_one("#setup-status").render())
        assert "didn't accept that key" in status
        await app.action_quit()


async def test_a_subscription_intern_cannot_sign_in_with_says_what_works_instead(tmp_path: Path):
    screen = OnboardingScreen(home=tmp_path, env=NO_KEYCHAIN)
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter", *"choose", "enter", *"claude pro", "enter")
        assert screen.step == "unavailable"
        assert "API key" in str(screen.query_one("#setup-body").render())
        await pilot.press("enter")
        assert screen.step == "provider"
        await app.action_quit()


def test_saving_keeps_the_rest_of_the_config(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        '# my settings\nmodel = "openrouter/old"\n\n[decider]\nbackend = "console"\nfallback_models = ["x"]\n\n'
        "[capabilities.memory]\nenabled = false\n"
    )
    setup = Setup("ollama/qwen3:30b", SYSTEM1_BY_ID["drex-console"], keys={"NACE_API_KEY": "nace-test-key"})
    saved = save(tmp_path, setup, NO_KEYCHAIN)
    text = saved.config.read_text()
    config = tomllib.loads(text)
    assert text.startswith("# my settings")
    assert "model" not in config and config["system2"] == "ollama/qwen3:30b"
    assert config["decider"] == {"backend": "console", "model": "drex-latest", "console_key_env": "NACE_API_KEY"}
    assert config["capabilities"] == {"memory": {"enabled": False}}
    assert saved.keys == {"NACE_API_KEY": "env-file"}


async def test_a_local_server_that_does_not_answer_is_said_with_what_to_check():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    found = await check_system2(PROVIDER_BY_ID["ollama"], None, transport=httpx.MockTransport(down))
    assert not found.ok and "is the server running" in found.message


async def test_signing_in_with_openrouter_trades_the_browser_code_for_a_key():
    exchanged: list[dict[str, str]] = []

    def keys(request: httpx.Request) -> httpx.Response:
        exchanged.append(json.loads(request.content))
        return httpx.Response(200, json={"key": "sk-or-v1-from-browser"})

    sign_in = OpenRouterSignIn(httpx.MockTransport(keys))
    url = await sign_in.start()
    callback = httpx.URL(url).params["callback_url"]
    assert httpx.URL(callback).host == "127.0.0.1"
    async with httpx.AsyncClient() as browser:  # the browser, sent back by OpenRouter
        await browser.get(f"{callback.replace('/callback/', '/elsewhere/')}?code=stray")
        page = await browser.get(f"{callback}?code=the-code")
    assert "Signed in" in page.text
    assert await sign_in.key() == "sk-or-v1-from-browser"
    assert exchanged == [{"code": "the-code", "code_verifier": sign_in.verifier, "code_challenge_method": "S256"}]

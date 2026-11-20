"""First-run setup: the screen a new user meets, what it checks, and what it saves."""

from __future__ import annotations

import json
import stat
import threading
import tomllib
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

pytest.importorskip("textual", reason="needs the tui extra")

from intern.config import Bootstrap, Options, read_env_file
from intern.free_commands import set_use
from intern.models.registry import ProviderSpec
from intern.onboarding import (
    PROVIDER_BY_ID,
    RECOMMENDED,
    SYSTEM1_BY_ID,
    OpenRouterSignIn,
    Setup,
    check_system1,
    check_system2,
    save,
    write_config,
    write_env_file,
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
        if request.url.params.get("output_modalities") == "decisions":
            return httpx.Response(200, json={"data": [{"id": "nace-ai/drex-max"}]})
        chat = ["anthropic/claude-opus-5.5", "tencent/hy4", "tencent/hy4-flash", "openai/gpt-6-luna"]
        return httpx.Response(200, json={"data": [{"id": model} for model in chat]})

    return httpx.MockTransport(answer)


def setup_app(tmp_path: Path, env: dict[str, str], requests: list[httpx.Request], opened: list[Models]) -> HarnessApp:
    """The app as `intern` opens it with no models configured, its provider checks answered by `provider`."""
    fake = make_opener(tmp_path, [Turn(text="ok")], configured_system2=None)

    async def open_session(emit, picks: Models):
        opened.append(replace(picks))
        # The real provider is not reached here: the session runs on the scripted one.
        return await fake(emit, replace(picks, system2="fake/model" if picks.system2 else None, system1=None))

    async def default() -> str | None:
        return None

    hooks = SetupHooks(env=lambda: env, configured=env.update, transport=provider(requests))
    return HarnessApp(open_session, default_system2=default, home=tmp_path / "home", setup=hooks)


async def test_a_first_launch_connects_openrouter_then_takes_its_models_and_drex_and_opens_the_session(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN}
    opened: list[Models] = []
    app = setup_app(tmp_path, env, [], opened)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        assert "Cannot open" not in transcript_text(app)  # nothing to fix is said as an error
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        about = str(screen.query_one("#setup-about").render())
        assert "Welcome to Intern" in about and "System" not in about
        await pilot.press("enter", "down", "enter")  # welcome; how to run: my own keys
        assert screen.step == "provider"
        assert screen.choices[0].id == "openrouter"  # recommended, first, among every other provider
        assert any(choice.id == "anthropic" for choice in screen.choices)
        await pilot.press("enter")
        assert screen.step == "key" and screen.provider is not None and screen.provider.id == "openrouter"
        await pilot.press(*"sk-or-v1-test-key", "enter")
        await wait_for(lambda: screen.step == "frontier")  # its frontier models, Claude Opus 5.5 first
        await pilot.press("enter", "enter")  # Opus; OpenRouter again for the efficient model, connected already
        assert screen.step == "efficient"
        await pilot.press("enter")  # Hy4 Flash, first
        assert screen.step == "decision"
        await pilot.press("enter")  # Drex Latest, recommended and first, on a Nace key
        assert screen.step == "decision-key"
        await pilot.press(*"nace-test-key-123", "enter")
        await wait_for(lambda: screen.step == "done")
        steps = [
            "welcome",
            "how",
            "provider",
            "key",
            "frontier",
            "provider",
            "efficient",
            "decision",
            "decision-key",
        ]
        assert [step for step, _ in screen.trail] == steps
        await pilot.press("enter")
        await wait_for(lambda: app.session is not None)
        await app.action_quit()

    assert opened[-1].system2 == "openrouter/anthropic/claude-opus-5.5"
    assert opened[-1].system1 == "drex-latest"
    assert opened[-1].auto
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "openrouter/anthropic/claude-opus-5.5"
    assert config["system1"] == {"backend": "console", "model": "drex-latest", "console_key_env": "NACE_API_KEY"}
    assert config["capabilities"] == {"handoff": {"to": "openrouter/tencent/hy4-flash"}}
    # Keys are kept out of config.toml, readable by the user only, and in use in this process at once.
    assert "sk-" not in (home / "config.toml").read_text()
    assert read_env_file(home / ".env") == {
        "OPENROUTER_API_KEY": "sk-or-v1-test-key",
        "NACE_API_KEY": "nace-test-key-123",
    }
    assert (home / ".env").stat().st_mode & 0o077 == 0
    assert env["OPENROUTER_API_KEY"] == "sk-or-v1-test-key"
    # What it saved is what a session started later from the config reads.
    later = Bootstrap(Options(cwd=tmp_path), {**NO_KEYCHAIN, "INTERN_HOME": str(home), **read_env_file(home / ".env")})
    assert later.default_system2() == "openrouter/anthropic/claude-opus-5.5"
    assert later.system1_choice() == "drex-latest"
    assert later.capability_settings().handoff.to == "openrouter/tencent/hy4-flash"


async def test_every_pair_is_a_frontier_and_an_efficient_model_on_the_one_openrouter_key():
    assert [r.title for r in RECOMMENDED] == [
        "Claude Opus 5.5 → Hy4 Flash",
        "Hy4 → Hy4 Flash",
        "GPT-6.2 Astra → GPT-6 Luna",
    ]
    for r in RECOMMENDED:
        assert r.system2 != r.handoff
        assert [k.env for k in r.keys] == ["OPENROUTER_API_KEY"]


async def test_choosing_your_own_asks_for_a_frontier_model_then_an_efficient_one(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN, "OPENROUTER_API_KEY": "sk-or-v1-in-env"}
    opened: list[Models] = []
    app = setup_app(tmp_path, env, [], opened)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter", "down", "enter")  # welcome; my own keys
        assert screen.choices[0].id == "openrouter" and screen.choices[0].group == "Ready to use"
        await pilot.press("enter", "enter")  # OpenRouter; the key already in the environment
        await wait_for(lambda: screen.step == "frontier")
        # Everything OpenRouter lists, its frontier models first.
        assert [(c.id, c.group) for c in screen.choices] == [
            ("anthropic/claude-opus-5.5", "Frontier models"),
            ("tencent/hy4", "Frontier models"),
            ("tencent/hy4-flash", "More on OpenRouter"),
            ("openai/gpt-6-luna", "More on OpenRouter"),
        ]
        await pilot.press(*"hy4", "enter")
        assert screen.system2 == "openrouter/tencent/hy4"
        # The efficient model's provider, the frontier's first: picked again, it is connected already.
        assert screen.step == "provider" and screen.role == "efficient" and screen.choices[0].id == "openrouter"
        await pilot.press("enter")
        assert screen.step == "efficient"
        assert screen.choices[0].id == "openrouter/tencent/hy4-flash"  # suggested first
        assert all(choice.id != screen.system2 for choice in screen.choices)  # never the frontier model again
        await pilot.press("enter", "down", "down", "enter")  # Hy4 Flash; Drex Max, on the OpenRouter key
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "openrouter/tencent/hy4"
    assert config["capabilities"] == {"handoff": {"to": "openrouter/tencent/hy4-flash"}}


async def test_another_provider_walks_through_frontier_efficient_and_the_decision_model_key(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[free]\nuse = true\n")  # on the free credit until now
    env = {**NO_KEYCHAIN}
    opened: list[Models] = []
    app = setup_app(tmp_path, env, [], opened)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter", "down", "enter")  # welcome; my own keys
        assert screen.step == "provider"
        await pilot.press(*"anthropic", "enter")
        assert screen.step == "key"
        await pilot.press(*"sk-ant-test-key-123", "enter")
        # Straight to Anthropic's frontier models.
        await wait_for(lambda: screen.step == "frontier")
        await pilot.press("enter")  # the suggested model, first
        assert screen.system2 == "anthropic/claude-sonnet-5-5"
        await pilot.press("enter")  # the efficient model on Anthropic too, first
        assert screen.step == "efficient"
        await pilot.press("enter")  # Haiku, the cheaper sibling, first
        assert screen.step == "decision"
        await pilot.press("down", "down", "enter")  # Drex Max
        assert screen.step == "decision-key"
        await pilot.press(*"sk-or-v1-test-key", "enter")
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "anthropic/claude-sonnet-5-5"
    assert config["capabilities"] == {"handoff": {"to": "anthropic/claude-haiku-5-5"}}
    # Own keys picked over the free credit pause it, or the session would open on the grant's models again.
    assert config["free"] == {"use": False}
    kept = read_env_file(home / ".env")
    assert kept == {"ANTHROPIC_API_KEY": "sk-ant-test-key-123", "OPENROUTER_API_KEY": "sk-or-v1-test-key"}


async def test_the_efficient_model_can_be_on_another_provider_than_the_frontier_model(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN}
    app = setup_app(tmp_path, env, [], [])
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter", "down", "enter", *"anthropic", "enter", *"sk-ant-test-key-123", "enter")
        await wait_for(lambda: screen.step == "frontier")
        await pilot.press("enter")  # Claude Sonnet 5.5
        await pilot.press(*"openrouter", "enter")
        assert screen.step == "key" and screen.provider is not None and screen.provider.id == "openrouter"
        await pilot.press(*"sk-or-v1-test-key", "enter")
        await wait_for(lambda: screen.step == "efficient")
        assert screen.choices[0].id == "openrouter/tencent/hy4-flash"
        await pilot.press("enter", "down", "down", "enter")  # Hy4 Flash; Drex Max, on the OpenRouter key just given
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "anthropic/claude-sonnet-5-5"
    assert config["capabilities"] == {"handoff": {"to": "openrouter/tencent/hy4-flash"}}
    kept = read_env_file(home / ".env")
    assert kept == {"ANTHROPIC_API_KEY": "sk-ant-test-key-123", "OPENROUTER_API_KEY": "sk-or-v1-test-key"}


async def test_skipping_the_efficient_model_runs_every_turn_on_the_frontier_model(tmp_path: Path):
    home = tmp_path / "home"
    env = {**NO_KEYCHAIN, "OPENROUTER_API_KEY": "sk-or-v1-in-env"}
    opened: list[Models] = []
    app = setup_app(tmp_path, env, [], opened)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter", "down", "enter", "enter", "enter")  # welcome; my own keys; OpenRouter; its key
        await wait_for(lambda: screen.step == "frontier")
        await pilot.press(*"hy4", "enter")
        assert screen.step == "provider" and screen.role == "efficient"
        skip = next(choice for choice in screen.choices if choice.id == "skip")
        assert skip.name == "Skip this step" and skip.note == "every turn runs on Hy4"
        await pilot.press(*"skip", "enter")
        assert screen.step == "decision"
        await pilot.press("down", "down", "enter")  # Drex Max, on the OpenRouter key
        await wait_for(lambda: screen.step == "done")
        await pilot.press("enter")
        await wait_for(lambda: app.session is not None)
        await app.action_quit()

    assert not opened[-1].auto
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["system2"] == "openrouter/tencent/hy4" and config["auto"] is False
    assert "capabilities" not in config


async def test_starting_at_the_decision_model_asks_for_the_efficient_model_after_it(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("[free]\nuse = true\n")
    env = {**NO_KEYCHAIN, "ANTHROPIC_API_KEY": "sk-ant-in-env", "OPENROUTER_API_KEY": "sk-or-v1-in-env"}
    screen = OnboardingScreen(
        home=home, env=env, start="system1", system2="anthropic/claude-sonnet-5-5", transport=provider([])
    )
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        assert screen.step == "decision"
        await pilot.press("down", "down", "enter")  # Drex Max, on the OpenRouter key in the environment
        await wait_for(lambda: screen.step == "provider")
        await pilot.press("enter")  # the frontier model's provider, first
        assert screen.step == "key"
        await pilot.press("enter")  # the Anthropic key in the environment
        await wait_for(lambda: screen.step == "efficient")
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    config = tomllib.loads((home / "config.toml").read_text())
    assert config["capabilities"] == {"handoff": {"to": "anthropic/claude-haiku-5-5"}}
    assert config["free"] == {"use": True}  # connecting only a decision model leaves the free credit on


async def test_kapitsa_is_picked_without_a_key_once_it_runs_and_the_session_opens_on_it(tmp_path: Path):
    home = tmp_path / "home"
    running = False

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            if not running:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"status": "ok", "model": "drex-max-v1"})
        return httpx.Response(200, json={"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}]})

    env = {**NO_KEYCHAIN, "ANTHROPIC_API_KEY": "sk-ant-in-env"}
    screen = OnboardingScreen(
        home=home,
        env=env,
        start="system1",
        system2="anthropic/claude-sonnet-5-5",
        transport=httpx.MockTransport(answer),
    )
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.pause()
        assert screen.step == "decision"
        await pilot.press(*["down"] * [c.id for c in screen.choices].index("kapitsa"), "enter")
        # Not running yet: the step stays, and says how to start it.
        await wait_for(lambda: "kapitsa-systemone --model" in str(screen.query_one("#setup-status").render()))
        assert screen.step == "decision"
        running = True
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "provider")
        await pilot.press("enter")  # the efficient model's provider: Anthropic, on the key in the environment
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "efficient")
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "done")
        await app.action_quit()

    # A session opens on it with no decision key anywhere.
    bootstrap = Bootstrap(Options(cwd=tmp_path), {"INTERN_HOME": str(home)})
    assert bootstrap.system1_choice() == "kapitsa:drex-max-v1"
    backend = bootstrap.system1_backend(bootstrap.system1_choice())
    assert backend._url == "http://127.0.0.1:8001/v1/systemone"  # pyright: ignore


async def test_a_refused_key_keeps_the_step_and_says_how_to_fix_it(tmp_path: Path):
    requests: list[httpx.Request] = []
    screen = OnboardingScreen(home=tmp_path, env=NO_KEYCHAIN, transport=provider(requests, refuse="sk-bad-key-000"))

    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter", "down", "enter", "enter", *"sk-bad-key-000", "enter")
        await wait_for(lambda: bool(requests) and not screen.busy)
        await pilot.pause()
        assert screen.step == "key"
        status = str(screen.query_one("#setup-status").render())
        assert "didn't accept that key" in status
        await app.action_quit()


async def test_subscriptions_intern_cannot_sign_in_with_are_not_offered(tmp_path: Path):
    screen = OnboardingScreen(home=tmp_path, env=NO_KEYCHAIN, transport=provider([]))
    app = HarnessApp(make_opener(tmp_path, [Turn(text="ok")]), welcome=False)
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(screen)
        await pilot.press("enter", "down", "enter")
        assert screen.step == "provider"
        assert "anthropic" in {choice.id for choice in screen.choices}
        assert not {"claude-subscription", "chatgpt", "copilot"} & {choice.id for choice in screen.choices}
        assert all(choice.group != "Subscriptions" for choice in screen.choices)
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


def test_saving_keeps_capability_settings_written_inline(tmp_path: Path):
    """`capabilities = {...}` on one line is the same settings as `[capabilities]`; setup changes only the hand-over."""
    inline = (
        "capabilities = {guard = {enabled = true}, memory = {enabled = false}, "
        'handoff = {to = "openrouter/old", at = 0.5}}\n'
    )
    (tmp_path / "config.toml").write_text(inline)
    setup = Setup("ollama/qwen3:30b", SYSTEM1_BY_ID["drex-console"], handoff="anthropic/claude-haiku-5-5")
    config = tomllib.loads(save(tmp_path, setup, NO_KEYCHAIN).config.read_text())
    assert config["capabilities"] == {
        "guard": {"enabled": True},
        "memory": {"enabled": False},
        "handoff": {"to": "anthropic/claude-haiku-5-5", "at": 0.5},
    }

    (tmp_path / "config.toml").write_text(inline)
    config = tomllib.loads(save(tmp_path, replace(setup, handoff=None), NO_KEYCHAIN).config.read_text())
    assert config["capabilities"] == {"guard": {"enabled": True}, "memory": {"enabled": False}, "handoff": {"at": 0.5}}

    (tmp_path / "config.toml").write_text("capabilities = {guard = {enabled = true}}\n")
    config = tomllib.loads(save(tmp_path, setup, NO_KEYCHAIN).config.read_text())
    assert config["capabilities"] == {"guard": {"enabled": True}, "handoff": {"to": "anthropic/claude-haiku-5-5"}}


def test_writers_at_the_same_time_lose_no_change_and_leave_a_file_that_reads(tmp_path: Path):
    """Setup, `intern free` and a key going to `.env` all change the home's files; each change must still be there."""
    (tmp_path / "config.toml").write_text("# mine\n[capabilities.guard]\nenabled = true\n")
    writers, rounds = 6, 8
    failures: list[BaseException] = []

    def write(writer: int) -> None:
        try:
            for round_ in range(rounds):
                name = f"w{writer}r{round_}"
                spec = ProviderSpec(base_url=f"http://127.0.0.1:1/{name}")
                write_config(tmp_path, Setup("ollama/qwen3:30b", SYSTEM1_BY_ID["drex-console"], providers={name: spec}))
                set_use(tmp_path, True)
                write_env_file(tmp_path / ".env", name.upper(), "fake-value")
        except BaseException as error:  # a half-written file fails the next writer's read
            failures.append(error)

    threads = [threading.Thread(target=write, args=(writer,)) for writer in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
    text = (tmp_path / "config.toml").read_text()
    config = tomllib.loads(text)
    every = {f"w{writer}r{round_}" for writer in range(writers) for round_ in range(rounds)}
    assert set(config["providers"]) == every
    assert config["free"] == {"use": True} and config["capabilities"] == {"guard": {"enabled": True}}
    assert text.startswith("# mine")
    assert set(read_env_file(tmp_path / ".env")) == {name.upper() for name in every}
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_a_config_file_that_is_a_link_stays_a_link(tmp_path: Path):
    """Dotfile managers link `config.toml` into the home; saving changes the file the link points to."""
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text("# mine\n")
    real.chmod(0o640)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").symlink_to(real)
    set_use(home, False)
    assert (home / "config.toml").is_symlink()
    assert tomllib.loads(real.read_text()) == {"free": {"use": False}} and real.read_text().startswith("# mine")
    assert stat.S_IMODE(real.stat().st_mode) == 0o640


async def test_a_local_server_that_does_not_answer_is_said_with_what_to_check():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    found = await check_system2(PROVIDER_BY_ID["ollama"], None, transport=httpx.MockTransport(down))
    assert not found.ok and "is the server running" in found.message


async def test_setup_offers_only_models_that_chat_never_an_embedding_or_reranker():
    def listing(request: httpx.Request) -> httpx.Response:
        served = [
            {"id": "accounts/fireworks/models/kimi-k3", "kind": "HF_BASE_MODEL", "supports_chat": True},
            {"id": "accounts/fireworks/models/qwen3-embedding-8b", "kind": "EMBEDDING_MODEL", "supports_chat": True},
            {"id": "accounts/fireworks/models/qwen3-reranker-8b", "kind": "EMBEDDING_MODEL", "supports_chat": True},
        ]
        return httpx.Response(200, json={"data": served})

    found = await check_system2(PROVIDER_BY_ID["fireworks"], "fw-key", transport=httpx.MockTransport(listing))
    assert found.models == ("accounts/fireworks/models/kimi-k3",)


async def test_more_drex_models_on_a_nace_key_are_the_ones_the_console_serves_not_its_retired_ones():
    def console(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "console.nace.ai"
        served = [{"name": "drex-v2.0"}, {"name": "drex-v1.0", "description": "Retired. Use drex-latest."}]
        return httpx.Response(200, json={"models": [*served, {"name": "drex-latest"}]})

    found = await check_system1(SYSTEM1_BY_ID["drex-more"], "nace-key", transport=httpx.MockTransport(console))
    assert found.ok and found.models == ("drex-v2.0", "drex-latest")


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

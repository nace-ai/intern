"""The terminal UI on the free credit, against a fake console: Start free in setup through the sign-in to Ready and a
session on the grant's models, a refused sign-in going on to the user's own keys, Start free hidden while the trial
takes no sign-ins, the model commands locked while the free credit runs them, `/free` pausing it, and a spent
credit's card."""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("textual", reason="needs the tui extra")

from intern.config import Bootstrap, Options
from intern.free_commands import console_of
from intern.nace_free import FreeStore
from intern.onboarding import CONSENT_VERSION
from intern.tui.app import HarnessApp, Models, SetupHooks
from intern.tui.onboarding import OnboardingScreen
from intern.tui.pickers import ModelPicker
from intern.tui.welcome import Welcome
from tests import test_free_sessions, test_nace_free
from tests.fake_console import GRANT
from tests.fake_openai import MODEL
from tests.test_free_sessions import OWN, READ, with_own_keys
from tests.test_nace_free import start
from tests.test_tui import SIZE, plain, send, transcript_text, wait_for

console = test_nace_free.console
env = test_nace_free.env
own = test_free_sessions.own
work = test_free_sessions.work
BUNDLE = GRANT["bundle"]


@pytest.fixture
def browsed(monkeypatch) -> list[str]:
    """The pages the app opened in the browser, none of them for real."""
    opened: list[str] = []
    monkeypatch.setattr(HarnessApp, "open_url", lambda self, url, new_tab=True: opened.append(url))
    return opened


def free_app(env: dict[str, str], work: Path, opened: list[Models] | None = None) -> HarnessApp:
    """The app as `intern` opens it, its sessions real ones on this machine's config home and the fake console."""

    async def open_session(emit, picks: Models):
        if opened is not None:
            opened.append(replace(picks))
        options = Options(cwd=work, persist=False, tui=True, system2=picks.system2, system1=picks.system1)
        return await Bootstrap(options, env).session(emit)

    async def no_default() -> str | None:
        return None

    hooks = SetupHooks(env=lambda: env, configured=env.update)
    return HarnessApp(open_session, default_system2=no_default, home=Path(env["INTERN_HOME"]), setup=hooks)


async def to_consent(app: HarnessApp, pilot) -> OnboardingScreen:
    """From a first launch with nothing to run on: setup, past its welcome, Start free picked."""
    await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
    screen = app.screen
    assert isinstance(screen, OnboardingScreen)
    await pilot.press("enter")  # welcome
    assert screen.step == "how"
    await wait_for(lambda: screen.free is not None)
    assert screen.choices[0].id == "free"  # first, recommended
    await pilot.press("enter")
    assert screen.step == "consent"
    return screen


def signed_in(env: dict[str, str]) -> bool:
    return FreeStore(Path(env["INTERN_HOME"]), env).signed_in(console_of(env))


async def test_start_free_signs_in_in_the_browser_and_opens_the_session_on_the_grants_three_models(
    env, console, work, browsed
):
    console.pending = 10**6  # the user has not confirmed in the browser yet
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_consent(app, pilot)
        await pilot.press("enter")  # agrees to what Nace sees
        await wait_for(lambda: screen.code is not None)
        await pilot.pause()
        page = f"{console.origin}/oauth/device?user_code=ABCD-EFGH"
        assert browsed == [page]
        waiting = plain(screen.query_one("#setup-body"))
        assert "ABCD-EFGH" in waiting and page in waiting
        console.pending = 0  # confirmed
        await wait_for(lambda: screen.step == "free-done")
        await pilot.pause()
        ready = plain(screen.query_one("#setup-about"))
        assert all(name in ready for name in ("GLM 5.3", "DeepSeek V4.1 Flash", "Drex", "$3.50 of $4.00"))
        await pilot.press("enter")
        await wait_for(lambda: app.session is not None)
        session = app.session
        assert session is not None and session.deps.system2.ref == BUNDLE["system2"]
        assert session.deps.handoff is not None and session.deps.handoff.binding.ref == BUNDLE["handoff"]
        assert session.system1 == BUNDLE["system1"]
        await pilot.pause()
        opening = app.query_one(Welcome).opening
        assert opening.free and "agent $3.50 of $4.00" in opening.credit and "Drex $0.90 of $1.00" in opening.credit
        assert "free $3.50 of $4.00" in plain(app.status)
        await app.action_quit()
    assert console.authorizations[-1]["consent_version"] == CONSENT_VERSION
    assert tomllib.loads((Path(env["INTERN_HOME"]) / "config.toml").read_text())["free"] == {"use": True}


async def test_a_refused_sign_in_says_why_and_goes_on_to_your_own_keys(env, console, work, browsed):
    console.outcome = "not_eligible:account_too_new"
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_consent(app, pilot)
        await pilot.press("enter")
        await wait_for(lambda: screen.step == "refused")
        await pilot.pause()
        assert "30 days" in plain(screen.query_one("#setup-body"))
        assert [choice.id for choice in screen.choices] == ["own"]  # signing in again would be refused again
        await pilot.press("enter")
        assert screen.step == "provider"
        await app.action_quit()
    assert not signed_in(env)


async def test_esc_stops_the_sign_in_and_nothing_is_kept(env, console, work, browsed):
    console.pending = 10**6
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_consent(app, pilot)
        await pilot.press("enter")
        await wait_for(lambda: screen.code is not None)
        await pilot.press("escape")
        assert screen.step == "how"
        console.pending = 0  # confirmed in the browser after all: nobody is waiting for it now
        await asyncio.sleep(0.2)
        await app.action_quit()
    assert not signed_in(env)


@pytest.mark.parametrize("paused", ["grants", "inference"])
async def test_start_free_is_not_offered_while_the_trial_takes_no_sign_ins_or_serves_no_calls(
    env, console, work, paused
):
    console.status[paused] = "paused"
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: isinstance(app.screen, OnboardingScreen))
        screen = app.screen
        assert isinstance(screen, OnboardingScreen)
        await pilot.press("enter")
        await wait_for(lambda: screen.free is not None)
        await pilot.pause()
        assert [choice.id for choice in screen.choices] == ["own"]
        assert plain(screen.query_one("#setup-body")).strip()  # why, on the step
        await pilot.press("enter")
        assert screen.step == "provider"
        await app.action_quit()


@pytest.mark.parametrize(
    "command",
    ["/frontier openai/gpt-5", "/frontier", "/decision decisions:other", "/decision", "/handover off", "/efficient"],
)
async def test_a_model_command_on_free_credit_says_the_models_are_fixed_and_offers_free_pause(
    env, console, work, capsys, command
):
    await asyncio.to_thread(start, env, capsys)
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await send(app, pilot, command)
        await wait_for(lambda: "/free pause" in transcript_text(app))
        assert not isinstance(app.screen, ModelPicker)
        session = app.session
        assert session is not None and session.deps.system2.ref == BUNDLE["system2"]
        assert session.system1 == BUNDLE["system1"]
        assert session.deps.handoff is not None and session.deps.handoff.binding.ref == BUNDLE["handoff"]
        await app.action_quit()


async def test_free_shows_the_credit_and_pause_and_resume_move_the_session_between_your_keys_and_it(
    env, console, own, work, capsys
):
    await asyncio.to_thread(start, env, capsys)
    env = with_own_keys(env, own)
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await send(app, pilot, "/free")
        await wait_for(lambda: "◇ Free credit" in transcript_text(app))
        card = transcript_text(app)
        assert "$3.50 of $4.00 left" in card and "$0.90 of $1.00 left" in card and "/free pause" in card
        await send(app, pilot, "/free pause")
        await wait_for(lambda: app.session is not None and app.session.deps.system2.ref == OWN)
        assert app.session is not None and app.session.free is None
        await send(app, pilot, "/frontier")
        await wait_for(lambda: isinstance(app.screen, ModelPicker))  # your own keys: the models are yours to pick
        await pilot.press("escape")
        await send(app, pilot, "/free resume")
        await wait_for(lambda: app.session is not None and app.session.deps.system2.ref == BUNDLE["system2"])
        await app.action_quit()


async def test_a_spent_agent_credit_with_no_key_of_your_own_ends_on_a_card_pointing_at_setup(
    env, console, work, capsys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = [READ]
    console.charge["system2"] = 3.6  # the run's first reply spends the agent meter
    await asyncio.to_thread(start, env, capsys)
    app = free_app(env, work)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await send(app, pilot, "read my notes")
        await wait_for(lambda: "doesn't come back" in transcript_text(app))
        text = transcript_text(app)
        assert "Free agent credit used up" in text and "/setup" in text
        assert "HTTP 402" not in text and '"error"' not in text  # in words, not the console's body
        await app.action_quit()


async def test_a_spent_agent_credit_with_a_model_of_your_own_ends_saying_the_next_message_runs_on_it(
    env, console, own, work, capsys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = [READ]
    console.charge["system2"] = 3.6
    await asyncio.to_thread(start, env, capsys)
    app = free_app(with_own_keys(env, own), work)
    async with app.run_test(size=SIZE) as pilot:
        await wait_for(lambda: app.session is not None)
        await send(app, pilot, "read my notes")
        await wait_for(lambda: "Free agent credit used up" in transcript_text(app))
        await pilot.pause()
        text = transcript_text(app)
        assert MODEL in text and "HTTP 402" not in text and '"error"' not in text
        await app.action_quit()

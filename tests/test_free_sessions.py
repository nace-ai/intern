"""Sessions on the free credit, against a fake console: each system on the bundle while its meter lasts and on the
user's own configuration otherwise, the pickers locked to the bundle (CLI and ACP), the caps in every request, an
unbound session that says the credit is spent, and the meters the console reports on the way to the client. A meter
that runs out during a run moves the system to the user's own key for the same model, or ends the run at once saying
what the next one will use; each switch and low meter is told once."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acp import RequestError, text_block
from acp.schema import UsageUpdate

from intern import cli, config, decisions, nace_free
from intern.config import Bootstrap, Options, System2Unavailable
from intern.events import Decision, Event, FreeNotice
from intern.protocol.acp import PROVIDER_ERROR, AcpAgent, bootstrap_opener
from tests import test_nace_free
from tests.fake_console import GRANT
from tests.fake_openai import MODEL, FakeOpenAI, Reply
from tests.test_acp import FakeClient
from tests.test_nace_free import free, start

console = test_nace_free.console
env = test_nace_free.env
BUNDLE = GRANT["bundle"]
OWN = f"mine/{MODEL}"
OWN_SYSTEM1 = "decisions:own-judge"
OWN_CONFIG = """
system2 = "mine/{model}"

[providers.mine]
base_url = "{url}"
api_key_env = "MINE_KEY"

[system1]
backend = "decisions-api"
model = "own-judge"
models = ["own-console-judge"]
base_url = "{url}"
api_key_env = "MINE_KEY"
"""


@pytest.fixture
def own():
    """The user's own provider and decisions API, on a fake server of their own."""
    with FakeOpenAI([]) as server:
        yield server


@pytest.fixture
def work(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def home_of(env: dict[str, str]) -> Path:
    return Path(env["INTERN_HOME"])


def with_own_keys(env: dict[str, str], own: FakeOpenAI) -> dict[str, str]:
    config = home_of(env) / "config.toml"
    config.write_text(OWN_CONFIG.format(model=MODEL, url=own.url) + config.read_text())
    return {**env, "MINE_KEY": "test-key"}


def signed_in(env: dict[str, str], capsys) -> None:
    assert start(env, capsys)[0] == 0


async def open_session(env: dict[str, str], work: Path, **options: Any):
    events: list[Event] = []
    return await Bootstrap(Options(cwd=work, persist=False, **options), env).session(events.append)


async def test_a_session_with_a_grant_runs_every_system_on_the_bundle_within_its_caps(env, console, own, work, capsys):
    await asyncio.to_thread(signed_in, env, capsys)
    env = with_own_keys(env, own)  # own keys too: the free credit comes first
    async with await open_session(env, work) as session:
        assert session.deps.system2.ref == BUNDLE["system2"]
        handoff = session.deps.handoff
        assert handoff is not None and handoff.binding.ref == BUNDLE["handoff"]
        assert (handoff.config.threshold, handoff.config.streak, handoff.config.min_turn) == (0.7, 2, 8)
        assert session.system1 == BUNDLE["system1"]
        outcome = await session.run("hi")
        assert outcome.reason == "completed"
        window = session.deps.system2.window.working
        profile = session.deps.system2.profile
        # The output cap holds after the doubling that follows replies cut off at it.
        assert window == BUNDLE["window_tokens"] and profile.output_cap(window, 3) == BUNDLE["max_output_tokens"]
        assert handoff.binding.window.working <= BUNDLE["window_tokens"]
    [request] = console.requests
    assert request["model"] == BUNDLE["system2"] and request["max_tokens"] == BUNDLE["max_output_tokens"]
    assert console.system1 and {ask["model"] for ask in console.system1} == {"drex-latest"}
    assert console.bearers and all(bearer.startswith("nace_at_test_") for bearer in console.bearers)
    assert own.requests == [] and own.system1 == []


async def test_a_free_sessions_steward_writes_with_the_hand_over_model_on_free_credit(
    env, console, work, capsys, monkeypatch
):
    await asyncio.to_thread(signed_in, env, capsys)
    started: list[list[str]] = []
    popen = SimpleNamespace(Popen=lambda argv, **kwargs: started.append(argv), DEVNULL=subprocess.DEVNULL)
    monkeypatch.setattr(config, "subprocess", popen)
    journal = work.parent / "session.jsonl"
    session = await Bootstrap(Options(cwd=work, session_file=journal), env).session(lambda event: None)
    async with session:
        assert (await session.run("hi")).reason == "completed"
    [argv] = started
    assert argv[argv.index("--writer") + 1] == BUNDLE["handoff"] and "--free-writer" in argv
    assert argv[argv.index("--system1") + 1] == BUNDLE["system1"] and "--free-system1" in argv


@pytest.mark.parametrize(
    "picked",
    [["--system2", OWN], ["--system1", OWN_SYSTEM1], ["--handoff-to", OWN]],
    ids=["system2", "system1", "handoff-to"],
)
def test_the_command_line_refuses_another_model_for_a_system_on_free_credit(
    env, console, own, work, capsys, monkeypatch, picked
):
    signed_in(env, capsys)
    for name, value in with_own_keys(env, own).items():
        monkeypatch.setenv(name, value)
    code = cli.main(["-p", "--no-session", "--cwd", str(work), *picked, "--", "hi"])
    assert code == 2 and "runs on free credit" in capsys.readouterr().err
    assert console.requests == [] and own.requests == []


def acp_agent(env: dict[str, str], work: Path) -> tuple[AcpAgent, FakeClient]:
    agent = AcpAgent(bootstrap_opener(Options(cwd=work), env), home_of(env))
    client = FakeClient()
    agent.on_connect(client)  # pyright: ignore[reportArgumentType]
    return agent, client


def free_locked(error: pytest.ExceptionInfo[RequestError]) -> bool:
    return error.value.code == -32602 and error.value.data == {"code": "free_locked"}


def values(options: Any, config_id: str) -> list[str]:
    option = next(o for o in options if o.id == config_id)
    choices = option.options
    if choices and hasattr(choices[0], "group"):
        return [choice.value for group in choices for choice in group.options]
    return [choice.value for choice in choices]


@pytest.mark.parametrize("meta", [{"system2": OWN}, {"system1": OWN_SYSTEM1}], ids=["system2", "system1"])
async def test_acp_refuses_another_model_in_meta_for_a_system_on_free_credit(env, console, own, work, capsys, meta):
    await asyncio.to_thread(signed_in, env, capsys)
    agent, _ = acp_agent(with_own_keys(env, own), work)
    with pytest.raises(RequestError) as refused:
        await agent.new_session(cwd=str(work), nace=meta)
    assert free_locked(refused) and agent.sessions == {} and agent.unbound == {}
    await agent.aclose()


async def test_acp_pickers_offer_only_the_bundle_and_refuse_any_other_model(env, console, own, work, capsys):
    await asyncio.to_thread(signed_in, env, capsys)
    agent, _ = acp_agent(with_own_keys(env, own), work)
    opened = await agent.new_session(cwd=str(work))
    options = opened.config_options
    assert values(options, "model") == [BUNDLE["system2"]]
    assert values(options, "system1") == [BUNDLE["system1"]]
    assert values(options, "handoff") == [BUNDLE["handoff"]]  # no `off`
    for config_id, value in (("model", OWN), ("system1", OWN_SYSTEM1), ("handoff", OWN), ("handoff", "off")):
        with pytest.raises(RequestError) as refused:
            await agent.set_config_option(config_id=config_id, session_id=opened.session_id, value=value)
        assert free_locked(refused), (config_id, value)
    # The bundle's own model is no change.
    await agent.set_config_option(config_id="model", session_id=opened.session_id, value=BUNDLE["system2"])
    live = agent.sessions[opened.session_id].session
    assert (live.deps.system2.ref, live.system1) == (BUNDLE["system2"], BUNDLE["system1"])
    await agent.aclose()


async def test_a_paused_free_credit_runs_on_the_users_own_configuration(env, console, own, work, capsys):
    await asyncio.to_thread(signed_in, env, capsys)
    assert (await asyncio.to_thread(free, ["pause"], env, capsys))[0] == 0
    env = with_own_keys(env, own)
    before = len(console.bearers)
    async with await open_session(env, work) as session:
        assert (session.deps.system2.ref, session.system1) == (OWN, OWN_SYSTEM1)
        assert session.free is None
        assert (await session.run("hi")).reason == "completed"
    assert own.requests and own.system1
    assert len(console.bearers) == before and console.requests == []


async def test_a_spent_agent_meter_runs_system2_on_own_keys_while_system1_stays_free(env, console, own, work, capsys):
    console.spend("agent", 4.0)
    await asyncio.to_thread(signed_in, env, capsys)
    async with await open_session(with_own_keys(env, own), work) as session:
        assert session.deps.system2.ref == OWN and session.system1 == BUNDLE["system1"]
        assert (await session.run("hi")).reason == "completed"
    assert own.requests and own.system1 == [] and console.requests == [] and console.system1


async def test_a_meter_spent_during_a_run_moves_the_next_run_to_own_keys(env, console, own, work, capsys):
    console.charge["system2"] = 3.6  # one reply leaves less than nothing on the agent meter
    await asyncio.to_thread(signed_in, env, capsys)
    async with await open_session(with_own_keys(env, own), work) as session:
        assert (await session.run("hi")).reason == "completed"
        assert session.deps.system2.ref == BUNDLE["system2"]
        assert (await session.run("again")).reason == "completed"
        assert session.deps.system2.ref == OWN
        assert session.free is not None and session.free.source.agent == "own"
    assert len(console.requests) == 1 and len(own.requests) == 1


async def test_a_spent_grant_with_no_own_keys_opens_unbound_saying_so(env, console, work, capsys):
    console.grant["state"] = "exhausted"
    console.spend("agent", 4.0)
    console.spend("system1", 1.0)
    await asyncio.to_thread(signed_in, env, capsys)
    with pytest.raises(System2Unavailable, match="free agent credit is used up"):
        await open_session(env, work)

    agent, _ = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    assert opened.session_id in agent.unbound
    with pytest.raises(RequestError, match="free agent credit is used up"):
        await agent.prompt(prompt=[text_block("hi")], session_id=opened.session_id)
    assert console.requests == []
    await agent.aclose()


@pytest.mark.parametrize(
    ("spent", "data"),
    [
        ("agent_meter", {"code": "free_exhausted", "meter": "agent"}),
        ("paused", {"code": "free_paused", "meter": None}),
    ],
)
async def test_an_unbound_session_tells_the_client_the_free_credit_is_why_by_code(
    env, console, work, capsys, spent, data
):
    """A client tells a spent or paused free credit from a model that cannot bind by `data.code`, not the wording."""
    if spent == "paused":
        console.status["inference"] = "paused"
    else:
        console.spend("agent", 4.0)
    await asyncio.to_thread(signed_in, env, capsys)
    agent, _ = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    assert opened.session_id in agent.unbound
    with pytest.raises(RequestError) as prompted:
        await agent.prompt(prompt=[text_block("hi")], session_id=opened.session_id)
    with pytest.raises(RequestError) as picked:
        await agent.set_config_option(config_id="model", session_id=opened.session_id, value=BUNDLE["system2"])
    for refused in (prompted, picked):
        assert (refused.value.code, refused.value.data) == (-32602, data)
    assert opened.session_id in agent.unbound and console.requests == []
    await agent.aclose()


async def test_usage_updates_carry_the_meters_the_console_reports(env, console, work, capsys):
    console.charge["system2"] = 0.25
    await asyncio.to_thread(signed_in, env, capsys)
    agent, client = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    response = await agent.prompt(prompt=[text_block("hi")], session_id=opened.session_id)
    assert response.stop_reason == "end_turn"
    last = client.of(UsageUpdate)[-1].field_meta["nace"]["free"]
    assert last["source"] == {"system1": "free", "agent": "free"}
    assert last["meters"]["agent"] == {"usedUsd": console.grant["meters"]["agent"]["used_usd"], "limitUsd": 4.0}
    assert last["meters"]["agent"]["usedUsd"] == pytest.approx(0.75)  # the grant's 0.5 and one reply's 0.25
    await agent.aclose()


def test_models_json_shows_the_free_credit_and_only_the_bundle_for_a_system_on_it(
    env, console, own, work, capsys, monkeypatch
):
    console.spend("system1", 1.0)  # System 1's credit is spent: it lists the user's own models
    signed_in(env, capsys)
    for name, value in with_own_keys(env, own).items():
        monkeypatch.setenv(name, value)
    assert cli.main(["models", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["free"]["state"] == "active" and listed["free"]["use"] is True
    assert listed["free"]["source"] == {"system1": "own", "agent": "free"}
    assert listed["free"]["bundle"] == BUNDLE
    assert listed["free"]["meters"]["system1"] == {"used_usd": 1.0, "limit_usd": 1.0}
    assert [m["id"] for m in listed["system2"]] == [BUNDLE["system2"]]
    assert listed["system2"][0]["window"] == BUNDLE["window_tokens"]
    assert listed["handoff"] == {"models": [BUNDLE["handoff"]], "default": BUNDLE["handoff"]}
    assert [m["id"] for m in listed["system1"]] == ["own-console-judge"]  # the user's own


OWN_FIREWORKS = """
[providers.fireworks]
base_url = "{url}"
api_key_env = "FIREWORKS_API_KEY"
"""
OWN_CONSOLE = """
[system1]
console_url = "{url}"
"""
READ = Reply(tool_calls=[("read", {"path": "notes.txt"})])
JUDGED = [READ] * 4
"""Turns enough for System 1's first look at the run (`supervise.EVERY`)."""


def add_config(env: dict[str, str], text: str) -> None:
    path = home_of(env) / "config.toml"
    path.write_text(path.read_text() + text)


async def open_watched(env: dict[str, str], work: Path):
    events: list[Event] = []
    session = await Bootstrap(Options(cwd=work, persist=False), env).session(events.append)
    return session, events


def notices(events: list[Event]) -> list[FreeNotice]:
    return [event for event in events if isinstance(event, FreeNotice)]


async def test_a_system1_meter_spent_mid_run_moves_the_ask_to_the_own_console_key_without_failing_it(
    env, console, own, work, capsys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = list(JUDGED)
    await asyncio.to_thread(signed_in, env, capsys)
    add_config(env, OWN_CONSOLE.format(url=own.url))
    session, events = await open_watched({**env, "NACE_API_KEY": "test-key"}, work)
    async with session:
        console.spend("system1", 1.0)  # spent since the session read its meters
        assert (await session.run("read my notes")).reason == "completed"
        assert session.free is not None and session.free.source.system1 == "own"
        assert (await session.run("again")).reason == "completed"
    assert console.refused and set(console.refused) == {"system1"}
    assert own.system1 and {ask["model"] for ask in own.system1} >= {BUNDLE["system1"]}
    decisions = [event for event in events if isinstance(event, Decision)]
    assert decisions and all(decision.ok for decision in decisions)
    [switch] = notices(events)
    assert (switch.kind, switch.meter) == ("switch", "system1")
    # System 2 stayed on its free credit throughout.
    assert len(console.requests) == len(JUDGED) + 2 and own.requests == []


async def test_an_agent_meter_spent_mid_run_goes_on_with_the_same_model_on_the_own_fireworks_key(
    env, console, own, work, capsys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = [READ]
    console.charge["system2"] = 3.6  # the run's first reply spends the agent meter
    await asyncio.to_thread(signed_in, env, capsys)
    env = with_own_keys(env, own)
    add_config(env, OWN_FIREWORKS.format(url=own.url))
    session, events = await open_watched({**env, "FIREWORKS_API_KEY": "test-key"}, work)
    async with session:
        outcome = await session.run("read my notes")
        assert outcome.reason == "completed" and outcome.error is None
        assert [r["model"] for r in own.requests] == ["accounts/fireworks/models/glm-5p3"]
        # The next run is on the user's own System 2.
        assert (await session.run("again")).reason == "completed"
        assert session.deps.system2.ref == OWN
    assert len(console.requests) == 1 and console.refused == ["agent"]
    assert [r["model"] for r in own.requests] == ["accounts/fireworks/models/glm-5p3", MODEL]
    [switch] = notices(events)
    assert (switch.kind, switch.meter) == ("switch", "agent") and "glm-5p3" in switch.text


async def test_a_system_moved_to_own_keys_goes_back_to_free_credit_at_the_run_after_its_meter_has_room_again(
    env, console, own, work, capsys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = [READ]
    console.charge["system2"] = 3.6  # the run's first reply spends the agent meter
    await asyncio.to_thread(signed_in, env, capsys)
    env = with_own_keys(env, own)
    add_config(env, OWN_FIREWORKS.format(url=own.url))
    session, events = await open_watched({**env, "FIREWORKS_API_KEY": "test-key"}, work)
    async with session:
        reads = console.grant_reads
        assert (await session.run("read my notes")).reason == "completed"
        assert console.refused == ["agent"] and len(own.requests) == 1  # went on with GLM on the Fireworks key
        console.grant["meters"]["agent"]["limit_usd"] = 20.0  # an admin raises the limit
        # A run long enough for System 1 to look, which stays on free credit: its answers carry the meters.
        own.replies = list(JUDGED)
        assert (await session.run("again")).reason == "completed"
        assert session.deps.system2.ref == OWN
        assert (await session.run("and again")).reason == "completed"
        assert session.deps.system2.ref == BUNDLE["system2"]
        assert session.free is not None and session.free.source.agent == "free"
        assert console.grant_reads == reads  # no call to the console to learn it
    assert len(console.requests) == 2 and console.refused == ["agent"]
    assert [(notice.kind, notice.meter) for notice in notices(events)] == [("switch", "agent")] * 2


@pytest.mark.parametrize("paused", ["mid_run", "between_runs"])
async def test_a_session_that_fell_back_while_free_inference_was_paused_goes_back_to_free_credit_once_it_reopens(
    env, console, own, work, capsys, monkeypatch, paused
):
    await asyncio.to_thread(signed_in, env, capsys)
    env = with_own_keys(env, own)
    add_config(env, OWN_FIREWORKS.format(url=own.url))
    session, _ = await open_watched({**env, "FIREWORKS_API_KEY": "test-key"}, work)
    async with session:
        assert (await session.run("hi")).reason == "completed"
        console.status["inference"] = "paused"
        if paused == "between_runs":
            monkeypatch.setattr(nace_free, "GRANT_TTL_S", 0.0)  # the grant read before has gone stale
        assert (await session.run("go on")).reason == "completed"
        assert (await session.run("and on")).reason == "completed"
        assert (session.deps.system2.ref, session.system1) == (OWN, OWN_SYSTEM1)
        console.status["inference"] = "open"
        monkeypatch.setattr(nace_free, "GRANT_TTL_S", 0.0)
        before = len(console.requests)
        assert (await session.run("now")).reason == "completed"
        assert (session.deps.system2.ref, session.system1) == (BUNDLE["system2"], BUNDLE["system1"])
        assert session.free is not None and session.free.source.model_dump() == {"agent": "free", "system1": "free"}
    assert len(console.requests) == before + 1


@pytest.mark.parametrize("keys", ["own", "none"])
async def test_an_agent_meter_spent_mid_run_with_no_key_for_its_model_ends_the_turn_saying_what_runs_next(
    env, console, own, work, capsys, keys
):
    (work / "notes.txt").write_text("notes\n")
    console.replies = [READ]
    console.charge["system2"] = 3.6
    await asyncio.to_thread(signed_in, env, capsys)
    env = with_own_keys(env, own) if keys == "own" else env
    agent, client = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    with pytest.raises(RequestError) as ended:
        await asyncio.wait_for(agent.prompt(prompt=[text_block("read my notes")], session_id=opened.session_id), 30)
    assert ended.value.code == PROVIDER_ERROR
    data = ended.value.data
    assert isinstance(data, dict)
    assert (data["kind"], data["status"], data["code"], data["meter"]) == ("quota", 402, "free_exhausted", "agent")
    assert data["next"] == (OWN if keys == "own" else None)
    assert console.refused == ["agent"]  # not retried

    if keys == "own":
        response = await agent.prompt(prompt=[text_block("go on")], session_id=opened.session_id)
        assert response.stop_reason == "end_turn" and own.requests
        told = [u.field_meta["nace"]["free"].get("notice") for u in client.of(UsageUpdate)]
        assert len([notice for notice in told if notice]) == 1
    await agent.aclose()


async def test_a_system1_meter_spent_mid_run_with_no_own_console_key_stops_the_run_at_once(
    env, console, work, capsys, monkeypatch
):
    monkeypatch.setattr(decisions, "RETRY_WINDOW_S", 1800.0)  # the real window, which tests otherwise shorten
    (work / "notes.txt").write_text("notes\n")
    console.replies = list(JUDGED)
    await asyncio.to_thread(signed_in, env, capsys)
    agent, _ = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    console.spend("system1", 1.0)
    with pytest.raises(RequestError) as stopped:
        # A refusal that fell into the retry window would not answer in time.
        await asyncio.wait_for(agent.prompt(prompt=[text_block("read my notes")], session_id=opened.session_id), 30)
    assert stopped.value.code == -32603
    assert stopped.value.data == {"reason": "system1_unavailable", "code": "free_exhausted", "meter": "system1"}
    assert len(console.requests) == len(JUDGED)
    await agent.aclose()


async def test_a_low_agent_meter_is_told_once_at_20_and_once_at_5_percent_left(env, console, work, capsys):
    await asyncio.to_thread(signed_in, env, capsys)
    agent, client = acp_agent(env, work)
    opened = await agent.new_session(cwd=str(work))
    for used in (3.3, 3.4, 3.85, 3.9):  # 17.5%, 15%, 3.75% and 2.5% of $4 left
        console.spend("agent", used)
        response = await agent.prompt(prompt=[text_block("hi")], session_id=opened.session_id)
        assert response.stop_reason == "end_turn"
    told = [u.field_meta["nace"]["free"].get("notice") for u in client.of(UsageUpdate)]
    low = [notice for notice in told if notice]
    assert len(low) == 2 and "20%" in low[0] and "5%" in low[1]
    await agent.aclose()

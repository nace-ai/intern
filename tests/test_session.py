"""Session commands: what clients can do to a session while it runs and between runs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from intern.capability_settings import HandoffConfig
from intern.context import requests_section
from intern.decisions import Answers, ChoiceAnswer, DecisionsApiBackend, NoulQuestion, Question
from intern.events import Compaction, Handoff, State
from intern.handoff import TAKEOVER_NOTE, HandoffTarget
from intern.journal import CompactionEntry, EventEntry, MessageEntry, ModelChangeEntry
from intern.messages import AssistantMessage, UserMessage
from intern.providers.base import ProviderRequest
from intern.providers.fake import FakeProvider, Turn
from intern.session import Session, SessionBusy, SessionIdle

from .support import Quiet, WithTools, binding, is_state_write


def slow_then_done(seconds: float):
    calls: list[int] = []

    def script(_request) -> Turn:
        calls.append(1)
        if len(calls) == 1:
            return Turn(tool_calls=[("bash", {"command": f"sleep {seconds}"})])
        return Turn(text=f"reply {len(calls)}")

    return script


async def test_follow_ups_run_after_the_current_run(make_harness):
    harness = make_harness(slow_then_done(0.3))
    session = Session(harness.deps)
    task = session.start("first")
    await asyncio.sleep(0.1)
    assert session.follow_up("second") is None
    with pytest.raises(SessionBusy):
        session.start("third")
    await task
    prompts = [
        e.message.content
        for e in harness.deps.journal.active_path()
        if isinstance(e, MessageEntry) and isinstance(e.message, UserMessage)
    ]
    assert prompts == ["first", "second"]
    assert not session.running


STATE = "## Plan\n- [x] print the outputs\n## Now\nreport them"


def six_outputs():
    """Says hi to a greeting; prints six 2,000-character outputs, then stops, to a prompt that asks it to print."""
    printed: list[int] = []

    def script(request: ProviderRequest) -> Turn:
        if is_state_write(request):
            return Turn(text=STATE)
        if "print" not in str(request.messages[-1].model_dump()) and not printed:
            return Turn(text="hi")
        if len(printed) == 6:
            return Turn(text="printed")
        printed.append(1)
        return Turn(tool_calls=[("bash", {"command": f"printf 'OUT{len(printed)} {'x' * 2_000}'"})])

    return script


class SlowStateWrites(FakeProvider):
    async def stream(self, request: ProviderRequest):
        if is_state_write(request):
            await asyncio.sleep(30)
        async for item in super().stream(request):
            yield item


async def test_compact_cuts_now_what_a_run_cuts_past_its_threshold_and_holds_the_session_meanwhile(make_harness):
    script = six_outputs()
    harness = make_harness(script, window=12_000)
    session = Session(harness.deps)
    await session.run("say hi")
    assert await session.compact() == "fits"  # every turn fits in the recent share kept as it is: nothing to cut
    await session.run("print six outputs")
    assert not harness.recorder.of(Compaction)  # under compact_at: the run itself did not compact

    compacting = asyncio.create_task(session.compact())
    await asyncio.sleep(0)
    assert session.running
    with pytest.raises(SessionBusy):
        session.start("not now")
    with pytest.raises(SessionBusy):
        session.steer("not now")
    assert await compacting == "compacted" and not session.running

    # As at the threshold: the State written over the turns it did not cover, then the older turns cut to it.
    (written,) = harness.recorder.of(State)
    (compaction,) = harness.recorder.of(Compaction)
    assert written.landed == compaction.turn == 7  # the last run's last turn
    entry = next(e for e in harness.deps.journal.active_path() if isinstance(e, CompactionEntry))
    # The writer's State, and after it the user's requests as they asked them, kept by code.
    assert (
        entry.method == "state"
        and entry.summary == f"{STATE}\n\n{requests_section(['say hi', 'print six outputs'], 10_000)}"
    )
    await session.run("go on")
    sent = str([m.model_dump() for m in harness.provider.requests[-1].messages])
    assert "print the outputs" in sent and "OUT1 " not in sent and "OUT6 " in sent  # the recent turns stay as they were
    # The first request was cut with its turns; the State still has it, as written.
    assert sent.count("say hi") == 1 and "'content': 'say hi'" not in sent


async def test_a_compact_stopped_while_the_state_is_written_cuts_nothing(make_harness):
    harness = make_harness(six_outputs(), window=12_000)
    session = Session(harness.deps)
    await session.run("print six outputs")
    harness.deps.system2.provider = SlowStateWrites(six_outputs())
    compacting = asyncio.create_task(session.compact())
    await asyncio.sleep(0.2)
    session.abort()
    assert await asyncio.wait_for(compacting, 10) == "stopped"
    assert not harness.recorder.of(Compaction)
    assert not any(isinstance(e, CompactionEntry) for e in harness.deps.journal.active_path())


async def test_abort_ends_the_run_and_drops_queued_follow_ups(make_harness):
    harness = make_harness(slow_then_done(30))
    session = Session(harness.deps)
    task = session.start("first")
    await asyncio.sleep(0.3)
    session.follow_up("never")
    session.abort()
    outcome = await asyncio.wait_for(task, 10)
    assert outcome.reason == "aborted"
    assert session.state().last_reason == "aborted"
    with pytest.raises(SessionIdle):
        session.abort()


async def test_branching_rewinds_the_conversation(make_harness):
    harness = make_harness([Turn(text="a"), Turn(text="b"), Turn(text="c")])
    session = Session(harness.deps)
    await session.run("one")
    fork_point = harness.deps.journal.leaf_id
    await session.run("two")
    assert fork_point is not None
    session.branch(fork_point)
    await session.run("three")
    last = harness.provider.requests[-1].messages
    texts = [getattr(m, "content", getattr(m, "text", "")) for m in last]
    assert "two" not in texts and "three" in texts[-1]


async def test_set_model_switches_the_next_request_and_is_journaled(make_harness):
    harness = make_harness([Turn(text="from the first model")])
    other = FakeProvider([Turn(text="from the second model")], model="fake/other")

    async def resolve(ref: str):
        return binding(other)

    session = Session(harness.deps, resolve_model=resolve)
    await session.run("one")
    await session.set_model("fake/other")
    outcome = await session.run("two")
    assert outcome.final_text == "from the second model"
    assert any(isinstance(e, ModelChangeEntry) for e in harness.deps.journal.entries())


class Decider(Quiet):
    name = "decider"


async def test_a_system1_with_a_small_window_is_sent_a_state_that_fits_it(make_harness):
    # A decision model refuses (422) a state longer than its window, and a refused ask stops the run.
    states: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            listed = [{"id": "small", "context_length": 16_384}, {"id": "large", "context_length": 262_144}]
            return httpx.Response(200, json={"data": listed})
        states.append(json.loads(request.content)["state"])
        return httpx.Response(200, json={"answers": {"ok": {"type": "noul", "noul": 0.5}}})

    def resolve(ref: str) -> DecisionsApiBackend:
        model = ref.removeprefix("decisions:")
        return DecisionsApiBackend(
            "https://windows.test/v1", SecretStr("k"), [model], transport=httpx.MockTransport(api)
        )

    harness = make_harness([Turn(text="a")])
    session = Session(harness.deps, resolve_system1=resolve)
    await session.set_model("decisions:small", "system1")
    await harness.deps.decisions.ask("test", "x" * 200_000, {"ok": NoulQuestion(instructions="Is this ok?")})
    assert len(states[-1]) < 37_000  # three quarters of 16,384 tokens at 3 characters each, and the cut's marker
    await session.set_model("decisions:large", "system1")
    assert harness.deps.decisions.max_state_tokens == 32_768  # the configured state, back on a larger window


async def test_system1_switches_while_idle_keeping_its_spend_and_is_journaled(make_harness):
    harness = make_harness([Turn(text="a"), Turn(tool_calls=[("bash", {"command": "sleep 0.2"})]), Turn(text="b")])
    decider, quiet = Decider(), harness.deps.decisions.backend
    session = Session(harness.deps, resolve_system1=lambda ref: quiet if ref == "quiet" else decider)
    assert session.system1 == "quiet"
    await session.run("one")
    harness.deps.decisions.usage.cost_usd = 0.2
    await session.set_model("drex-latest", "system1")
    assert harness.deps.decisions.backend is decider and harness.deps.decisions.usage.cost_usd == 0.2
    assert session.system1 == "drex-latest"
    change = [e for e in harness.deps.journal.entries() if isinstance(e, ModelChangeEntry)]
    assert [(e.role, e.model) for e in change] == [("system1", "drex-latest")]
    task = session.start("two")
    await asyncio.sleep(0.05)
    with pytest.raises(SessionBusy):
        await session.set_model("quiet", "system1")
    await task
    await session.set_model("quiet", "system1")
    assert harness.deps.decisions.backend is quiet and session.system1 == "quiet"


async def test_state_reports_usage_across_runs(make_harness):
    harness = make_harness([Turn(text="a", output_tokens=7), Turn(text="b", output_tokens=5)])
    session = Session(harness.deps)
    await session.run("one")
    await session.run("two")
    state = session.state()
    assert state.usage.output_tokens == 12
    assert state.running is False and state.entries > 4


async def test_branching_is_refused_between_a_call_and_its_result(make_harness):
    harness = make_harness([Turn(tool_calls=[("bash", {"command": "echo a"}), ("bash", {"command": "echo b"})])])
    session = Session(harness.deps)
    await session.run("go")
    entries = [e for e in harness.deps.journal.active_path() if isinstance(e, MessageEntry)]
    first_result = next(e for e in entries if e.message.role == "tool")
    with pytest.raises(ValueError, match="turn boundary"):
        session.branch(first_result.id)
    session.branch(entries[0].id)


async def test_models_cannot_change_mid_run(make_harness):
    harness = make_harness(slow_then_done(0.5))

    async def resolve(ref: str):
        return binding(FakeProvider([]))

    session = Session(harness.deps, resolve_model=resolve)
    task = session.start("go")
    await asyncio.sleep(0.1)
    with pytest.raises(SessionBusy):
        await session.set_model("fake/other")
    await task


class Ready:
    """A System 1 that judges every turn ready for the hand-over."""

    name = "ready"

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        ready = ChoiceAnswer(choice="ready", probabilities={"ready": 1.0})
        return Answers(answers={name: ready for name in questions if name == "ready"})


async def test_a_hand_over_stays_in_the_session_and_the_next_run_is_system_2s_again(make_harness):
    strong = [Turn(text="built", reasoning="plan", tool_calls=[("greet", {"who": "a"})]), Turn(text="second answer")]
    harness = make_harness(strong, extensions=[WithTools()], system1=Ready())
    cheap = FakeProvider(
        [Turn(text="fixing", tool_calls=[("greet", {"who": "b"})]), Turn(text="all done")], "cheap/small"
    )
    harness.deps.handoff = HandoffTarget(
        binding=replace(binding(cheap), ref="cheap/small"), config=HandoffConfig(min_turn=1, streak=1)
    )
    session = Session(harness.deps)

    first = await session.run("do the task")

    assert first.final_text == "all done"
    path = harness.deps.journal.active_path()
    replies = [e.message for e in path if isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)]
    assert [(m.model, m.text) for m in replies] == [
        ("fake/model", "built"),
        ("cheap/small", "fixing"),
        ("cheap/small", "all done"),
    ]
    assert any(isinstance(e, EventEntry) and isinstance(e.event, Handoff) for e in path)  # a resumed session shows it

    second = await session.run("next")

    assert second.final_text == "second answer" and len(cheap.requests) == 2  # System 2 answered, not the cheaper model
    sent = harness.provider.requests[-1].messages
    # It was sent what the cheaper model did, as its own conversation going on: the take-over note was that model's.
    assert [m.text for m in sent if isinstance(m, AssistantMessage)] == ["built", "fixing", "all done"]
    told = [[TAKEOVER_NOTE in m.content for m in r.messages if isinstance(m, UserMessage)] for r in cheap.requests]
    assert all(any(each) for each in told)
    assert not any(TAKEOVER_NOTE in m.content for m in sent if isinstance(m, UserMessage))

from __future__ import annotations

import json
import re

import pytest

from intern.decisions import Answers, ChoiceAnswer
from intern.events import Decision, Nudge, State
from intern.messages import UserMessage
from intern.models.profile import Profile
from intern.providers.base import ProviderRequest
from intern.providers.fake import Turn
from intern.supervise import EVERY, WRAP_NUDGE, WRAP_STRIKES

from .support import is_state_write


class Review:
    """System 1 answering a look: a scripted mode per look from the first (the last one repeating), each a bare choice
    or a (choice, probabilities) pair, and the same for the wrap-up question beside it (`wraps`, "working" if none).
    It reads the turn off the session's last call."""

    name = "review"

    def __init__(self, *modes: str | tuple[str, dict[str, float]], wraps: tuple[str, ...] = ("working",)) -> None:
        self.modes = list(modes) or ["advancing"]
        self.wraps = list(wraps)
        self.asks: list[tuple[int, str]] = []

    async def decide(self, state: str, questions: dict) -> Answers:
        if "mode" not in questions:  # the tool-call guard's ask: no opinion
            return Answers(answers={})
        assert set(questions) == {"mode", "wrap"}  # one request for both: the State is paid for once
        self.asks.append((int(re.findall(r"turn (\d+)", state)[-1]), state))
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        wrap = self.wraps.pop(0) if len(self.wraps) > 1 else self.wraps[0]
        choice, probabilities = (mode, {}) if isinstance(mode, str) else mode
        return Answers(
            answers={
                "mode": ChoiceAnswer(choice=choice, probabilities=probabilities),
                "wrap": ChoiceAnswer(choice=wrap, probabilities={}),
            }
        )


def churn():
    turns: list[int] = []

    def script(request: ProviderRequest) -> Turn:
        if is_state_write(request):
            return Turn(text="## Plan\n- steps\n## Now\nthe next step")
        turns.append(1)
        return Turn(tool_calls=[("bash", {"command": f"python repro.py --turn {len(turns)}"})])

    return script


async def test_a_stuck_look_is_looked_at_again_next_turn_and_nudges_when_it_holds(make_harness):
    system1 = Review("re_investigating")
    harness = make_harness(churn(), window=200_000, profile=Profile(max_turns=18), system1=system1)
    await harness.run("fix the bug")
    # A look every fourth turn; a stuck one is looked at again at the next turn and nudges when it holds. The next
    # look is four turns after a nudge, and after the third nudge none is left to send, so System 1 is not asked.
    assert [t for t, _ in system1.asks] == [4, 5, 9, 10, 14, 15]
    assert all(e.ok for e in harness.recorder.of(Decision) if e.purpose == "supervise")
    assert [n.turn for n in harness.recorder.of(Nudge) if n.reason == "stuck_gate"] == [5, 10, 15]
    nudged = harness.provider.requests[5].messages[-1]
    assert isinstance(nudged, UserMessage) and "Your next tool call must be the change" in nudged.content
    assert '"goal":"fix the bug"' in system1.asks[0][1] and "repro.py --turn 1" in system1.asks[0][1]


@pytest.mark.parametrize("break_mode", ["advancing", "unclear"])
async def test_a_second_look_that_is_not_stuck_resets_the_strikes(make_harness, break_mode):
    system1 = Review("retrying", break_mode, "re_investigating", break_mode, "advancing")
    harness = make_harness(churn(), window=200_000, profile=Profile(max_turns=12), system1=system1)
    await harness.run("fix the bug")
    # The breaking verdict is taken as System 1 gave it, not replaced by the no-opinion fallback.
    reviews = [e.answers for e in harness.recorder.of(Decision) if e.purpose == "supervise" and e.answers]
    assert break_mode in [answers["mode"]["choice"] for answers in reviews]
    assert [t for t, _ in system1.asks] == [4, 5, 9, 10]
    assert [n.reason for n in harness.recorder.of(Nudge)] == ["turn_budget"]


async def test_a_strike_needs_the_two_stuck_modes_together_at_the_threshold(make_harness):
    unsure = ("re_investigating", {"re_investigating": 0.45, "retrying": 0.2, "advancing": 0.35})  # 0.65: no strike
    split = ("advancing", {"advancing": 0.15, "re_investigating": 0.45, "retrying": 0.4})  # 0.85: a strike
    system1 = Review(unsure, split, split, "advancing")
    harness = make_harness(churn(), window=200_000, profile=Profile(max_turns=10), system1=system1)
    await harness.run("fix the bug")
    assert [t for t, _ in system1.asks] == [4, 8, 9]
    assert [n.turn for n in harness.recorder.of(Nudge) if n.reason == "stuck_gate"] == [9]


async def test_a_done_look_that_holds_at_the_next_turn_sends_one_wrap_up_nudge(make_harness):
    # Done at turn 4, working at 5: no nudge. Done at 9 and still at 10: the wrap-up, once, though every later look
    # says done too; the stuck nudge's count is untouched by it.
    done = "done_polishing"
    system1 = Review(wraps=(done, "working", done))
    harness = make_harness(churn(), window=200_000, profile=Profile(max_turns=16), system1=system1)
    await harness.run("fix the bug")
    assert [(n.turn, n.reason) for n in harness.recorder.of(Nudge) if n.reason != "turn_budget"] == [(10, "wrap_up")]
    assert [t for t, _ in system1.asks] == [4, 5, 9, 10, 14]
    nudged = harness.provider.requests[10].messages[-1]
    assert isinstance(nudged, UserMessage) and nudged.content == WRAP_NUDGE.format(turns=EVERY + WRAP_STRIKES - 1)


async def test_no_state_is_written_between_compactions_and_system_1_reads_the_whole_session(make_harness):
    system1 = Review()
    harness = make_harness(churn(), window=200_000, profile=Profile(max_turns=16), system1=system1)
    await harness.run("do the steps")
    # System 2 writes the State only at a compaction; System 1 reads the session itself, its first turn included.
    assert harness.recorder.of(State) == []
    assert not any(is_state_write(r) for r in harness.provider.requests)
    turn, state = system1.asks[-1]
    calls = [c["input"] for entry in json.loads(state)["history"] for c in entry.get("tool_calls", [])]
    assert turn == 16 and len(calls) == 16 and '--turn 1"' in calls[0]

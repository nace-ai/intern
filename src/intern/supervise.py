"""System 1 supervises the run: every few turns it says what the latest turns are doing and whether the request is
met, and being stuck, or done and still changing things, earns a nudge.

Long capped runs spend their turn budget investigating what the session already established, with every call new and
no change ever made. One System 1 ask every EVERY turns, over System 1's State (a cut of the session built by code,
`session_cut`, its newest turns kept whole), carries two questions. The mode of the latest turns: advancing,
re-investigating, retrying, off plan, finished, or unclear. Unclear is the way out for turns that fit no single mode,
so they are not forced into the nearest one; it is not a stuck verdict (drex-max never picked it over 1,485 reviews
of 132 stored runs). And whether the request is done (WRAP_UP).

A verdict that would act is looked at again at the next turn, and the nudge goes out when it holds (STRIKES looks in
a row); otherwise the next look is EVERY turns on. Asked at every turn in two requests, the questions were a fifth
of a benchmark's spend and led to a nudge at 2.6% and 0.4% of the asks; a look covers the turns since the one
before. The loop waits for the answer at the turn's end (about
a second) and the nudge goes into the next request.

Stuck (STUCK: re_investigating and retrying together): a look is a strike when System 1 puts P(re_investigating) +
P(retrying) at STUCK_THRESHOLD or more. The nudge asks for the change as the next call, and the next look is EVERY
turns on, so the agent has that long to act on it.

Done: the request is met and checked, and the latest turns change or re-check what it did not ask for. One wrap-up
nudge per run. The mode's `finished` does not see it: a done agent that keeps tuning makes changes, which reads as
advancing. The nudge never asks the agent to stop: the same verdict comes mid-run on work a grader fails, and
stopping early is how that work fails. It asks to keep what works working instead.

The two questions share a request, so the State is paid for once. Sharing moves the answers: the done question's
little, the mode's stuck probabilities down, which is why STUCK_THRESHOLD is 0.7 where the mode asked alone had 0.8.
Replayed through System 1 over 42 stored runs (1,935 turns), this rule sends 23 stuck nudges where every turn's
verdicts, three in a row, sent 27 (16 of them within a few turns of one of those), and the wrap-up in 22 runs where
that sent it in 21 (17 the same runs), with System 1 asked at 27% of the turns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from intern.decisions import ChoiceAnswer, ChoiceQuestion, DecisionClient

EVERY = 4  # turns between looks; MODE's question names as many, and the State keeps as many whole (PINNED)
STRIKES = 2  # stuck looks in a row before a nudge: the one that found it, and the next turn's
STUCK_THRESHOLD = 0.7  # P(re_investigating) + P(retrying) that makes a look a strike
MAX_NUDGES = 3
WRAP_STRIKES = 2  # done looks in a row before the wrap-up nudge, once per run
WRAP_THRESHOLD = 0.8  # P(done_polishing) that makes a look count toward it

MODE = ChoiceQuestion(
    instructions="The state is the agent's session so far, oldest first; its last few entries are the latest turns. "
    "Compare the latest turns (about the last four replies and their tool calls) with what the session established "
    "before them. What are these turns doing?",
    criteria={
        "advancing": "they produce results the session did not have, and the work moves: a step completes, a change "
        "is made, a test confirms or refutes something",
        "re_investigating": "they read, grep or debug things earlier turns already covered, without a change to the "
        "code or the plan",
        "retrying": "they repeat variants of the same failing attempt",
        "off_plan": "they work on something the user did not ask for and the agent's plan does not need",
        "finished": "the user's request is met; the agent should stop and answer",
        "unclear": "the turns fit two of these equally well, or the session shows too little of them to tell",
    },
)
STUCK = frozenset({"re_investigating", "retrying"})

# It never tells the agent to stop: it fires because the turns are stuck, not because the work is done. "If no change is
# possible, give your answer now instead" and "Stop investigating" ended 8 h tasks at 6-10 minutes.
STUCK_NUDGE = """\
A review of your last {turns} turns found no progress: they re-read, re-ran or retried what your own notes \
already establish, and changed nothing. Do not repeat them. Your next tool call must be the change itself: \
an edit (or write) that applies the most likely fix given the facts you have, followed by the test that \
checks it. A tested wrong change tells you more than another read."""


WRAP_UP = ChoiceQuestion(
    instructions="The state is the agent's session so far, oldest first; its last few entries are the latest turns. "
    "Compare what the user asked with what the session's tool results already show. Which is true now?",
    criteria={
        "working": "a part of the request is not done yet, or not yet shown to work; the latest turns work toward it",
        "done_polishing": "every part of the request is done and shown to work on the real output, with each file the "
        "request names in place, and the latest turns change or re-check things the request did not ask for (speed, "
        "style, extra cases, refactoring, more tests)",
        "done_unsaved": "a working solution has been shown, but an output the request names (a file, a value in a "
        "file) is not yet where and how the request asks for it",
        "unclear": "the session shows too little to tell",
    },
)

WRAP_NUDGE = """\
A review of your last {turns} turns found the request done and checked: each part it asks for is in place and shown \
to work, and these turns change or re-check things it did not ask for. Keep what works: from here, make any further \
change on a copy, check it, and replace the original only once the check passes, so the deliverable works whenever \
the run ends. If a part of the request is not met after all, fix that first."""


def stuck_probability(choice: str, probabilities: Mapping[str, float]) -> float:
    """P(re_investigating) + P(retrying); a choice given without probabilities counts as certain."""
    if not probabilities:
        return 1.0 if choice in STUCK else 0.0
    return sum(probabilities.get(mode, 0.0) for mode in STUCK)


def is_strike(choice: str, probabilities: Mapping[str, float]) -> bool:
    """Whether a turn's mode verdict counts toward a stuck nudge (the TUI's ledger counts with this too)."""
    return stuck_probability(choice, probabilities) >= STUCK_THRESHOLD


def done_probability(answer: object) -> float:
    """P(done_polishing); a choice given without probabilities counts as certain."""
    if not isinstance(answer, ChoiceAnswer):
        return 0.0
    if not answer.probabilities:
        return 1.0 if answer.choice == "done_polishing" else 0.0
    return answer.probabilities.get("done_polishing", 0.0)


class Supervisor:
    """Per-run counters for the stuck and wrap-up nudges; `after_turn` asks System 1 when a look is due and returns
    the nudge and its reason, if one is due."""

    def __init__(self) -> None:
        self.wait = EVERY  # turns until the next look
        self.strikes = 0
        self.nudges = 0
        self.done_turns = 0
        self.wrapped = False

    async def after_turn(
        self, turn: int, decisions: DecisionClient, state: Callable[[], str]
    ) -> tuple[str, str] | None:
        self.wait -= 1
        if self.wait > 0 or (self.wrapped and self.nudges >= MAX_NUDGES):  # not due, or no nudge left to send
            return None
        answers = await decisions.ask("supervise", state(), {"mode": MODE, "wrap": WRAP_UP}, turn=turn)
        mode = answers["mode"]
        strike = (
            self.nudges < MAX_NUDGES and isinstance(mode, ChoiceAnswer) and is_strike(mode.choice, mode.probabilities)
        )
        done = not self.wrapped and done_probability(answers.get("wrap")) >= WRAP_THRESHOLD
        self.strikes = self.strikes + 1 if strike else 0
        self.done_turns = self.done_turns + 1 if done else 0
        self.wait = 1 if strike or done else EVERY  # a verdict that would act is looked at again at the next turn
        if self.done_turns >= WRAP_STRIKES:
            self.wrapped = True
            self.strikes = self.done_turns = 0
            self.wait = EVERY
            return WRAP_NUDGE.format(turns=EVERY + WRAP_STRIKES - 1), "wrap_up"
        if self.strikes >= STRIKES:
            self.strikes = 0
            self.nudges += 1
            self.wait = EVERY
            return STUCK_NUDGE.format(turns=EVERY + STRIKES - 1), "stuck_gate"
        return None

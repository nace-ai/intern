"""One hand-over per run from System 2 to a cheaper model, at the turn System 1 judges the rest within its reach.

It hands over once, from the stronger model to the cheaper one, never the other way round or back and forth. The stage
it hands over at is the earliest turn at which a substantive artifact exists, a check relevant to the task has
exercised it, and what remains is localized repair, validation or finishing. System 1 judges it after every turn
until the hand-over, from the first turn whose streak could reach `min_turn`, in its own ask: the supervisor looks
only every few turns, and the hand-over's timing is what it is for.

The model taken over by inherits the workspace and the conversation, without the reasoning of the model before it,
which only goes back to the model that wrote it, and goes on until the run ends. It never hands back within the run.

It works in the session's own journal, with the run's tools, System 1, control and turn count, so its turns are part
of the conversation as System 2's are. The note that tells it it is taking over (`TAKEOVER_NOTE`) is the run's and
that model's alone: it is sent, where it took over, with each of its requests until the run ends or a compaction
cuts its first turn, and the journal does not keep it. So System 1's State and a compaction read the session as any
single agent's, and so does System 2 at the next user message, its cached prefix up to the hand-over intact; it may
hand over anew.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from intern.capability_settings import HandoffConfig
from intern.decisions import ChoiceAnswer, ChoiceQuestion, DecisionClient

if TYPE_CHECKING:
    from intern.loop import ModelBinding

DEFAULT_TARGET = "anthropic/claude-haiku-5-5"
"""What the terminal UI's auto mode hands a run over to while `[capabilities.handoff]` names no model."""

READY = ChoiceQuestion(
    instructions="The state is an agent's session so far, oldest first; its last entries are the latest turn. A "
    "strong model has been doing the task. A weaker, cheaper model will take over from here and finish it, in the "
    "same workspace and with the same session. Judge from what the tool results show, not from the agent's claims, "
    "whether the work has reached the point where that hand-over is safe.",
    criteria={
        "ready": "all three hold: (1) a substantive implementation, runnable prototype or file the task requires "
        "exists; investigation, a plan or an unexecuted sketch is not enough; (2) it has been run, tested or "
        "inspected by a check relevant to the task, and the result is shown; a failed check counts when it points at "
        "a localized problem, an environment failure that kept it from running does not; (3) what remains is "
        "localized diagnosis, repair, hardening, validation or preparing the final output, not choosing the approach "
        "or building the main solution",
        "not_ready": "one of the three does not hold yet: there is no substantive artifact, or no relevant check has "
        "exercised it, or the core approach or the main implementation is still open",
        "unclear": "the session shows too little to tell",
    },
)

TAKEOVER_NOTE = """\
You are taking over this task from another agent, whose turns are above; its work is in the workspace. The main \
solution exists and has been checked, so finish it rather than redo it: keep its approach and files, and change only \
what a check shows is wrong. Run the relevant check before and after each change, and undo a change that makes it \
worse. Once the checks pass and the required output is in place, stop."""


@dataclass
class HandoffTarget:
    """The model a run hands over to, bound when the session opens, and when to."""

    binding: ModelBinding
    config: HandoffConfig


def ready_probability(answer: object) -> float:
    """P(ready); a choice given without probabilities counts as certain."""
    if not isinstance(answer, ChoiceAnswer):
        return 0.0
    if not answer.probabilities:
        return 1.0 if answer.choice == "ready" else 0.0
    return answer.probabilities.get("ready", 0.0)


class Readiness:
    """Per-run count of turns judged ready; `after_turn` asks System 1 and says whether to hand over now."""

    def __init__(self, target: HandoffTarget) -> None:
        self.target = target
        self.streak = 0
        self.probability = 0.0

    async def after_turn(self, turn: int, decisions: DecisionClient, state: Callable[[], str]) -> bool:
        config = self.target.config
        if turn < config.min_turn - config.streak + 1:  # no streak that starts here can end before min_turn
            return False
        answer = (await decisions.ask("handoff", state(), {"ready": READY}, turn=turn))["ready"]
        self.probability = ready_probability(answer)
        self.streak = self.streak + 1 if self.probability >= config.threshold else 0
        return self.streak >= config.streak and turn >= config.min_turn

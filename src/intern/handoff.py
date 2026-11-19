"""One hand-over per run from System 2 to a cheaper model, at the turn System 1 judges the rest within its reach.

It hands over once, from the stronger model to the cheaper one, never the other way round or back and forth. The stage
it hands over at is the earliest turn at which a substantive artifact exists, a check relevant to the task has
exercised it, and what remains is localized repair, validation or finishing. System 1 judges it after every turn
until the hand-over, from the first turn whose streak could reach `min_turn`, in its own ask: the supervisor looks
only every few turns, and the hand-over's timing is what it is for.

The model taken over by inherits the workspace and the conversation, without the reasoning of the model before it,
which only goes back to the model that wrote it, and goes on until the run ends. It never hands back within the run.

It runs as a child run: the conversation is copied into a journal of its own, and it keeps the run's tools, System 1,
control and turn count, so it sees what it would in the run itself. Only its final reply joins the session's
conversation, so the next user message is System 2's again, its cached prefix intact, and may hand over anew.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from intern.capability_settings import HandoffConfig
from intern.decisions import ChoiceAnswer, ChoiceQuestion, DecisionClient
from intern.journal import Entry, Journal, MessageEntry, SessionEntry
from intern.messages import AssistantMessage, UserMessage

if TYPE_CHECKING:
    from pathlib import Path

    from intern.loop import ModelBinding

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


def seed_journal(parent: Journal, path: Path | None) -> Journal:
    """The child run's journal, holding the conversation so far: the parent's active path under the same ids, so a
    compaction's cut still names its first kept entry."""
    session = parent.active_path()[0]
    assert isinstance(session, SessionEntry)
    journal = Journal(cwd=session.cwd, path=path)
    for entry in parent.active_path()[1:]:
        journal.append(entry.model_copy())
    return journal


def kept_messages(entries: Sequence[Entry]) -> list[UserMessage | AssistantMessage]:
    """What the parent's conversation keeps of the child run's own entries: the user's messages to it (steering), then
    its last reply with text, the text alone, since its tool calls and reasoning belong to turns that conversation
    does not hold."""
    messages = [e.message for e in entries if isinstance(e, MessageEntry)]
    kept: list[UserMessage | AssistantMessage] = [
        m for m in messages if isinstance(m, UserMessage) and m.source == "user"
    ]
    reply = next((m for m in reversed(messages) if isinstance(m, AssistantMessage) and m.text.strip()), None)
    if reply is not None:
        kept.append(
            reply.model_copy(
                update={"tool_calls": [], "invalid_tool_calls": [], "reasoning": None, "reasoning_blocks": []}
            )
        )
    return kept

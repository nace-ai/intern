"""One hand-over per run from System 2 to a cheaper model, at the turn System 1 judges the rest within its reach.

AgentBaton (arXiv 2610.35959): a strong model that hands over once, at the right stage, to a cheaper one keeps most of
its accuracy at a fraction of the cost; the other way round, or switching back and forth, does not pay. The right stage
is the earliest turn at which a substantive artifact exists, a check relevant to the task has exercised it, and what
remains is localized repair, validation or finishing. System 1 judges it after every turn until the hand-over, from
the first turn whose streak could reach `min_turn`, in its own ask: the supervisor looks only every few turns, and
the hand-over's timing is what it is for. Over the 62 tasks of a stored DeepSeek V4.1 Flash run it first fired at a
median 53% of the trajectory with P(ready) >= 0.7 on two turns in a row from turn 5, 62% on runs that failed and 45%
on those that passed.

The model taken over by inherits the workspace and either the conversation (`context`: without the reasoning of the
model before it, which only goes back to the model that wrote it) or a fresh start from the user's requests and a
JSON-lines file of the turns so far (`logfile`), and goes on until the run ends. It never hands back.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from intern.capability_settings import HandoffConfig
from intern.decisions import ChoiceAnswer, ChoiceQuestion, DecisionClient
from intern.journal import Entry, MessageEntry
from intern.messages import AssistantMessage, ToolResultMessage

if TYPE_CHECKING:
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

LOGFILE_NOTE = """\
You are taking over this task from another agent, which worked on it for {turns} turns in this same workspace: every \
file it created or changed is still there. Its turns are in {path}, one JSON object per line, oldest first: `turn` \
(its number), `response` (what it wrote), and `actions`, the tool calls it made, each with `tool`, `arguments`, \
`status` and `output` (what the tool returned, possibly shortened). Read the last records first (`tail -n 5`) to see \
where the work stands, and earlier ones only as you need them. Then carry the task through to the end."""


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


def trajectory(entries: Sequence[Entry]) -> list[dict[str, object]]:
    """The session's turns as the logfile holds them: each reply's text and its tool calls with their results."""
    records: list[dict[str, object]] = []
    actions: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        message = entry.message
        if isinstance(message, AssistantMessage):
            calls = [
                {"tool": c.name, "arguments": c.arguments, "status": None, "output": None} for c in message.tool_calls
            ]
            actions.update((c.id, action) for c, action in zip(message.tool_calls, calls, strict=True))
            records.append({"turn": len(records) + 1, "response": message.text, "actions": calls})
        elif isinstance(message, ToolResultMessage) and (action := actions.get(message.result.call_id)) is not None:
            action["status"], action["output"] = message.result.status, message.result.content
    return records


def logfile_text(records: list[dict[str, object]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)

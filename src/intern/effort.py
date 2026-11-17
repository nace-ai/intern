"""Auto thinking: System 1 picks System 2's reasoning effort once per run, from the request that starts it.

At the `auto` thought level (`AUTO_EFFORT`), offered only on a provider that keeps its prompt cache when the level
changes (`effort_keeps_cache`: Anthropic's API), the first request of a run asks System 1 one choice over the session's
State, its newest user message being the request the run works on: which of the model's levels that work needs. Every
request of the run goes with that level; the next run asks again. A State write goes with it as with a fixed level
(`loop.state_effort`): the run's level when it is low or medium, so the write reads the agent's prompt from the cache.

Once a run, not once a turn. A turn's need is no clearer than the run's from where the loop stands (the next turn's
work is known only once its reply is in), so a pick a turn would cost an ask on every turn for little more.

Anthropic's only, because a level that changes costs the prompt cache everywhere else: the first request at a level not
used lately read none of it on OpenAI (directly or through OpenRouter), Fireworks, DeepInfra and OpenRouter's upstreams,
where the provider writes the level into the prompt's head (gpt-oss: `Reasoning: high` in its system message). Each
level keeps its own cache after that. Anthropic's `output_config.effort` kept every token across low -> high -> low
(Claude Haiku and Sonnet 5.5; docs/architecture.md has the measurements).
"""

from __future__ import annotations

from collections.abc import Sequence

from intern.decisions import ChoiceAnswer, ChoiceQuestion, DecisionClient
from intern.models.profile import EFFORT_LEVELS

PURPOSE = "effort"
"""The ask's purpose on its `decision` event; the terminal UI shows the pick from it."""

NEEDS = {
    "low": "little: a reply, a question the session already answers, a lookup, or a small mechanical change with "
    "nothing to work out",
    "medium": "some: routine work of a few steps on familiar ground, with an obvious way to check it",
    "high": "a lot: work over several steps or files, a bug to find, a choice of approach, or a result that must be "
    "checked",
    "xhigh": "very much: a subtle bug, a change across much of the code, or a plan that needs careful thought before "
    "its first step",
    "max": "the most there is: the hardest problems, where an error is costly, such as a deep proof, a tricky "
    "algorithm or a security flaw",
}
"""What each level is for, as System 1 reads it."""

INSTRUCTIONS = (
    "The state is an agent's session so far, oldest first. Its newest user message starts the agent's next run: the "
    "work from that message until the agent answers it. How much reasoning does that run need from the agent? Judge "
    "the work the message asks for, given what the session already established, not the length of the message."
)


def question(levels: Sequence[str]) -> ChoiceQuestion:
    """The choice over `levels` (two or more, least to most); the top one also takes what is harder still when the
    model goes no higher."""
    criteria = {level: NEEDS[level] for level in levels}
    top = levels[-1]
    if top != EFFORT_LEVELS[-1]:
        criteria[top] += ", or anything harder"
    return ChoiceQuestion(instructions=INSTRUCTIONS, criteria=criteria)


async def pick(decisions: DecisionClient, state: str, levels: Sequence[str], turn: int) -> str | None:
    """The level for a run: System 1's choice among `levels`; the one level without an ask when there is only one,
    None when there is none."""
    if len(levels) < 2:
        return levels[0] if levels else None
    answer = (await decisions.ask(PURPOSE, state, {"level": question(levels)}, turn=turn))["level"]
    assert isinstance(answer, ChoiceAnswer)  # ask() returns only answers that fit their question
    return answer.choice

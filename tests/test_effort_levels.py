"""The reasoning efforts a binding can be set to: its provider's run when the API takes one for every model, the
common three on the profile's word, none otherwise. What the ACP and the TUI offer as thought levels."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intern.decisions import Answer, Answers, ChoiceAnswer, ChoiceQuestion, Question
from intern.models.profile import COMMON_EFFORTS, EFFORT_LEVELS, Profile, effort_choices, load_profile
from intern.providers.anthropic import AnthropicProvider
from intern.providers.fake import FakeProvider, Turn
from intern.providers.openai_compat import OpenAICompatProvider
from intern.providers.openai_responses import OpenAIResponsesProvider

from .support import binding


class Ladder(FakeProvider):
    """A provider whose API takes the whole run for every model, as the Anthropic provider does."""

    effort_levels = EFFORT_LEVELS
    effort_keeps_cache = True


def test_a_binding_takes_an_effort_on_its_provider_s_word_or_its_profile_s():
    plain = binding(FakeProvider([]))
    assert plain.effort_levels == () and not plain.takes_effort
    named = binding(FakeProvider([]), profile=Profile(reasoning_effort="medium"))
    assert named.effort_levels == COMMON_EFFORTS == ("low", "medium", "high") and named.takes_effort
    assert binding(Ladder([])).effort_levels == EFFORT_LEVELS  # the provider's run, profile or not
    assert binding(Ladder([]), profile=Profile(reasoning_effort="low")).effort_levels == EFFORT_LEVELS
    # Levels a profile declares hold without an effort, so `default` (none sent) comes first and stays offered.
    declared = binding(FakeProvider([]), profile=Profile(effort_levels=["max", "low"]))
    assert declared.effort_levels == ("low", "max") and declared.effort_choices == ("default", "low", "max")
    assert named.effort_choices == COMMON_EFFORTS  # a named effort alone is always sent
    # `auto` only where a change of level keeps the provider's prompt cache (Anthropic's API), and then last.
    assert binding(Ladder([])).effort_choices == ("default", *EFFORT_LEVELS, "auto")
    assert plain.effort_choices == ()  # nothing to pick among, so no auto either
    with pytest.raises(ValidationError, match="not off"):
        Profile(effort_levels=["low", "off"])


def test_deepseek_v4_flash_on_fireworks_offers_every_level_after_default():
    for model in (
        "models/deepseek-v4p1-flash",
        "routers/deepseek-v4p1-flash-1017-ultrafast",
        "routers/spot-deepseek-v4-flash",
    ):
        profile = load_profile(f"fireworks/accounts/fireworks/{model}")
        assert effort_choices((), profile) == ("default", *EFFORT_LEVELS) and profile.reasoning_effort is None
    assert effort_choices((), load_profile("openrouter/deepseek/deepseek-v4-flash")) == ()


def test_the_native_apis_take_a_run_for_every_model_and_compatible_endpoints_go_by_the_profile():
    assert AnthropicProvider.effort_levels == ("low", "medium", "high", "xhigh", "max")
    assert OpenAIResponsesProvider.effort_levels == ("low", "medium", "high", "xhigh")
    assert not hasattr(OpenAICompatProvider, "effort_levels")  # some reject the field; a profile says when it is taken
    assert EFFORT_LEVELS[:3] == COMMON_EFFORTS and len(set(EFFORT_LEVELS)) == 5


def test_a_model_is_offered_only_the_levels_its_api_takes_not_all_its_provider_s():
    """A level the API refuses fails every request with a 400, so the profile's stated levels replace the provider's
    run: none for models that do not reason or refuse the field, the few a Pro model takes (measured 2026-11-17)."""
    anthropic, openai = AnthropicProvider.effort_levels, OpenAIResponsesProvider.effort_levels
    offered = {
        ref: effort_choices(levels, load_profile(ref))
        for ref, levels in (
            ("anthropic/claude-sonnet-4-5-20250929", anthropic),
            ("anthropic/claude-haiku-4-5-20251001", anthropic),
            ("anthropic/claude-opus-4-5-20251101", anthropic),
            ("openai/gpt-4o", openai),
            ("openai/gpt-4.1-mini", openai),
            ("openai/gpt-live-1", openai),
            ("openai/chat-latest", openai),
            ("openai/gpt-5-pro", openai),
            ("openai/gpt-5.5-pro", openai),
            ("openai/gpt-5.5", openai),
        )
    }
    assert offered == {
        "anthropic/claude-sonnet-4-5-20250929": (),
        "anthropic/claude-haiku-4-5-20251001": (),
        "anthropic/claude-opus-4-5-20251101": ("default", *EFFORT_LEVELS),
        "openai/gpt-4o": (),
        "openai/gpt-4.1-mini": (),
        "openai/gpt-live-1": (),
        "openai/chat-latest": ("default", "medium"),
        "openai/gpt-5-pro": ("default", "high"),
        "openai/gpt-5.5-pro": ("default", "medium", "high", "xhigh"),
        "openai/gpt-5.5": ("default", "low", "medium", "high", "xhigh"),
    }
    # The pro models keep their windows: a profile of their own does not stack on the family's.
    assert load_profile("openai/gpt-5.5-pro").window == load_profile("openai/gpt-5.5").window == 1_050_000
    assert not binding(Ladder([]), profile=Profile(effort_levels=[])).takes_effort
    with pytest.raises(ValidationError, match="not one of the effort_levels"):
        Profile(effort_levels=["high"], reasoning_effort="low")
    assert Profile(effort_levels=["medium", "high"], reasoning_effort="auto").reasoning_effort == "auto"  # picks among
    with pytest.raises(ValidationError, match="not one of the effort_levels"):
        Profile(effort_levels=[], reasoning_effort="auto")  # nothing to pick among


class Picks:
    """A System 1 that answers the effort question with the levels it is given, one a run, and keeps the state each
    was asked over; `Filled` answers every other question quietly."""

    name = "picks"

    def __init__(self, *levels: str) -> None:
        self.levels = list(levels)
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        answers: dict[str, Answer] = {}
        for name, q in questions.items():
            if isinstance(q, ChoiceQuestion) and q.criteria.keys() >= {"low", "high"}:
                self.asked.append((state, tuple(q.criteria)))
                level = self.levels.pop(0)
                answers[name] = ChoiceAnswer(choice=level, probabilities={c: float(c == level) for c in q.criteria})
        return Answers(answers=answers)


async def test_at_auto_system1_picks_the_effort_once_a_run_from_its_request_and_every_request_of_the_run_takes_it(
    make_harness,
):
    replies = iter([Turn(tool_calls=[("bash", {"command": "echo hi"})]), Turn(text="done"), Turn(text="hello")])
    system1 = Picks("high", "low")
    auto = Profile(reasoning_effort="auto")
    harness = make_harness(lambda request: next(replies), profile=auto, system1=system1, provider_class=Ladder)
    assert (await harness.run("refactor the parser")).reason == "completed"
    assert (await harness.run("say hello")).reason == "completed"
    assert [r.reasoning_effort for r in harness.provider.requests] == ["high", "high", "low"]
    # One ask a run, not a turn, over the levels the model takes, with the run's own request in what System 1 reads.
    assert [levels for _, levels in system1.asked] == [EFFORT_LEVELS, EFFORT_LEVELS]
    assert "refactor the parser" in system1.asked[0][0] and "say hello" in system1.asked[1][0]
    assert harness.deps.system2.profile.reasoning_effort == "auto"  # the level stays auto; the pick is the run's


async def test_auto_on_a_provider_whose_cache_a_change_of_level_breaks_sends_no_effort_and_asks_nothing(make_harness):
    """A profile may name `auto` for any model; off Anthropic's API a change of level has the provider read the whole
    prompt again, so the run goes as at `default` instead, and System 1 is not asked."""
    system1 = Picks("high")
    harness = make_harness(lambda request: Turn(text="ok"), profile=Profile(reasoning_effort="auto"), system1=system1)
    assert (await harness.run("refactor the parser")).reason == "completed"
    assert [r.reasoning_effort for r in harness.provider.requests] == [None] and not system1.asked

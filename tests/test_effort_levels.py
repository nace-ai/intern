"""The reasoning efforts a binding can be set to: its provider's run when the API takes one for every model, the
common three on the profile's word, none otherwise. What the ACP and the TUI offer as thought levels."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intern.models.profile import COMMON_EFFORTS, EFFORT_LEVELS, Profile, effort_choices, load_profile
from intern.providers.anthropic import AnthropicProvider
from intern.providers.fake import FakeProvider, Turn
from intern.providers.openai_compat import OpenAICompatProvider
from intern.providers.openai_responses import OpenAIResponsesProvider

from .support import binding


class Ladder(FakeProvider):
    """A provider whose API takes the whole run for every model, as the Anthropic provider does."""

    effort_levels = EFFORT_LEVELS


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
    assert binding(Ladder([])).effort_choices == ("default", *EFFORT_LEVELS)
    assert plain.effort_choices == ()
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


async def test_a_profile_that_still_names_the_retired_auto_level_loads_and_sends_the_models_own(make_harness):
    """`auto`, where System 1 picked the effort for each run, went in 0.48.0: a profile that names it is not an
    error, and its runs send no effort, as at `default`."""
    profile = Profile(reasoning_effort="auto")
    assert profile.reasoning_effort is None
    harness = make_harness(lambda request: Turn(text="ok"), profile=profile, provider_class=Ladder)
    assert (await harness.run("refactor the parser")).reason == "completed"
    assert [r.reasoning_effort for r in harness.provider.requests] == [None]

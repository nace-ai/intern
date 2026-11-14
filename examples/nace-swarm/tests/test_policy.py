"""Tests for the fixed policy formulas (no network involved)."""

from __future__ import annotations

from swarm.models import Agent, Candidate, CountryState, Policy
from swarm.policy import GRID_SIZE, apply_policies, update_agent


def _candidate(party: str, foreign: float, economic: float, social: float) -> Candidate:
    return Candidate(
        id=f"{party[0].lower()}1",
        party=party,  # type: ignore[arg-type]
        name="Test",
        slogan="test",
        policies={
            "foreign": Policy(value=foreign, text="f"),
            "economic": Policy(value=economic, text="e"),
            "social_welfare": Policy(value=social, text="s"),
        },
    )


def _agent(**kw) -> Agent:
    base = dict(id="a1", wealth=30_000.0, age=40.0, gender="female", health=0.6, x=20.0, y=20.0, lean=-0.2)
    base.update(kw)
    return Agent(**base)


def test_right_economic_policy_raises_market_lowers_mobility() -> None:
    country = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    after = apply_policies(country, _candidate("Right", 0.0, 0.6, 0.0))
    assert after.stock_market > country.stock_market
    assert after.class_mobility < country.class_mobility


def test_left_social_policy_raises_healthcare_lowers_unemployment() -> None:
    country = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    after = apply_policies(country, _candidate("Left", 0.0, 0.0, -0.6))
    assert after.healthcare_access > country.healthcare_access
    assert after.unemployment < country.unemployment


def test_foreign_policy_far_from_zero_lowers_relations() -> None:
    country = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    after = apply_policies(country, _candidate("Right", 0.8, 0.0, 0.0))
    assert after.foreign_relations < country.foreign_relations


def test_country_values_stay_in_range() -> None:
    country = CountryState(
        stock_market=0.99, class_mobility=0.01, foreign_relations=0.01,
        unemployment=0.99, healthcare_access=0.01,
    )
    after = apply_policies(country, _candidate("Left", -1.0, -1.0, -1.0))
    for value in after.model_dump().values():
        assert 0.0 <= value <= 1.0


def test_update_agent_fields_and_history() -> None:
    old = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    new = apply_policies(old, _candidate("Left", -0.2, -0.6, -0.4))
    agent = _agent()
    updated = update_agent(agent, old, new, "l1", {"l1": 0.8, "r1": 0.1, "abstain": 0.1}, round=1)

    assert len(updated.history) == len(agent.history) + 1
    snap = updated.history[-1]
    assert snap.round == 1
    assert snap.vote == "l1"
    assert snap.vote_probs == {"l1": 0.8, "r1": 0.1, "abstain": 0.1}
    # Left winner expected: lean should drift left.
    assert updated.lean < agent.lean
    # Wealth can change, but never below 0.
    assert updated.wealth >= 0.0
    # Healthcare rose: health should not fall.
    assert updated.health >= agent.health
    # Grid bounds hold and x moves by lean * 2.
    assert 0.0 <= updated.x <= GRID_SIZE
    assert 0.0 <= updated.health <= 1.0
    assert -1.0 <= updated.lean <= 1.0


def test_lean_shifts_toward_expected_party() -> None:
    old = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    left_leaner = _agent(lean=-0.9)
    updated = update_agent(left_leaner, old, old, "l1", {"l1": 1.0}, round=1)
    assert updated.lean < left_leaner.lean  # stays more left

    right_leaner = _agent(lean=0.9)
    updated = update_agent(right_leaner, old, old, "r1", {"r1": 1.0}, round=1)
    assert updated.lean > right_leaner.lean  # stays more right


def test_abstain_pulls_lean_to_center() -> None:
    old = CountryState(
        stock_market=0.5, class_mobility=0.5, foreign_relations=0.5,
        unemployment=0.5, healthcare_access=0.5,
    )
    agent = _agent(lean=-0.5)
    updated = update_agent(agent, old, old, "abstain", {"abstain": 1.0}, round=1)
    assert abs(updated.lean) < abs(agent.lean)

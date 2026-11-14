"""Fixed formulas: a winner's policies change the country, and the country
change updates each agent's wealth, health, lean and position."""

from __future__ import annotations

from .models import Agent, Candidate, CountryState, Snapshot

GRID_SIZE = 40.0
K_MARKET = 0.1
K_MOBILITY = 0.1
K_WELFARE = 0.1
K_FOREIGN = 0.1
K_HEALTH = 0.2
K_WEALTH_MARKET = 0.05
K_MOBILITY_BONUS = 5_000.0
WEALTH_REF = 50_000.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def apply_policies(country: CountryState, winner: Candidate) -> CountryState:
    """Return the CountryState after the winner's policies take effect."""
    economic = winner.policies["economic"].value
    social = winner.policies["social_welfare"].value
    foreign = winner.policies["foreign"].value

    return CountryState(
        stock_market=_clamp01(country.stock_market + K_MARKET * economic),
        class_mobility=_clamp01(country.class_mobility - K_MOBILITY * economic),
        foreign_relations=_clamp01(country.foreign_relations - K_FOREIGN * abs(foreign)),
        unemployment=_clamp01(country.unemployment + K_WELFARE * social),
        healthcare_access=_clamp01(country.healthcare_access - K_WELFARE * social),
    )


def _party_probability(probs: dict[str, float], prefix: str) -> float:
    """Sum the probabilities of all choices for candidates of one party.

    Candidate ids are prefixed with "l" (Left) or "r" (Right).
    """
    return sum(p for choice, p in probs.items() if choice.startswith(prefix))


def update_agent(
    agent: Agent,
    old_country: CountryState,
    new_country: CountryState,
    vote: str | None,
    probs: dict[str, float],
    round: int,
) -> Agent:
    """Return the agent after the round: new wealth, health, lean, position
    and a snapshot appended to its history."""
    d_market = new_country.stock_market - old_country.stock_market
    d_mobility = new_country.class_mobility - old_country.class_mobility
    d_healthcare = new_country.healthcare_access - old_country.healthcare_access

    # Poorer agents (wealth below the reference) get more of the mobility bonus.
    poverty = max(0.0, WEALTH_REF - agent.wealth) / WEALTH_REF
    wealth = agent.wealth + agent.wealth * K_WEALTH_MARKET * d_market + K_MOBILITY_BONUS * d_mobility * poverty
    wealth = max(0.0, wealth)

    health = max(0.0, min(1.0, agent.health + K_HEALTH * d_healthcare))

    p_right = _party_probability(probs, "r")
    p_left = _party_probability(probs, "l")
    lean = max(-1.0, min(1.0, 0.7 * agent.lean + 0.3 * (p_right - p_left)))

    x = max(0.0, min(GRID_SIZE, agent.x + lean * 2.0))

    snapshot = Snapshot(
        round=round,
        wealth=wealth,
        health=health,
        lean=lean,
        x=x,
        y=agent.y,
        vote=vote,
        vote_probs=dict(probs),
    )

    return agent.model_copy(update={"wealth": wealth, "health": health, "lean": lean, "x": x, "history": [*agent.history, snapshot]})

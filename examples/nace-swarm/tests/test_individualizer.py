"""Tests for the individualizer."""

from swarm.individualizer import make_agents


def test_same_seed_gives_same_agents() -> None:
    a = make_agents(50, seed=42)
    b = make_agents(50, seed=42)
    assert [ag.model_dump() for ag in a] == [ag.model_dump() for ag in b]


def test_lean_within_bounds() -> None:
    for agent in make_agents(100, seed=7):
        assert -1.0 <= agent.lean <= 1.0


def test_mean_wealth_near_50k() -> None:
    agents = make_agents(1000, seed=1)
    mean_wealth = sum(ag.wealth for ag in agents) / len(agents)
    assert abs(mean_wealth - 50_000) < 5_000

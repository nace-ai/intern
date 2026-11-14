"""Tests for the Drex client, fake mode only (no network)."""

from __future__ import annotations

import asyncio

import pytest
from swarm.drex import ABSTAIN, build_requests, vote
from swarm.individualizer import make_agents
from swarm.models import Candidate, CountryState, Policy

COUNTRY = CountryState(
    stock_market=0.5,
    class_mobility=0.5,
    foreign_relations=0.5,
    unemployment=0.4,
    healthcare_access=0.6,
)


def make_candidate(cid: str, party: str, bias: float) -> Candidate:
    return Candidate(
        id=cid,
        party=party,
        name=f"Candidate {cid}",
        slogan=f"a {bias:+.0f} stance on everything",
        policies={
            area: Policy(value=bias, text=f"{area} at {bias:+.2f}")
            for area in ("foreign", "economic", "social_welfare")
        },
    )


def some_candidates() -> list[Candidate]:
    return [
        make_candidate("l1", "Left", -0.6),
        make_candidate("l2", "Left", -0.2),
        make_candidate("r1", "Right", 0.3),
        make_candidate("r2", "Right", 0.7),
    ]


def test_fake_mode_every_agent_gets_a_vote() -> None:
    agents = make_agents(41, seed=7)
    candidates = some_candidates()

    for stage in ("primary", "general"):
        votes = asyncio.run(vote(agents, COUNTRY, candidates, stage, fake=True))
        assert set(votes) == {a.id for a in agents}
        for agent_id, (choice, probs) in votes.items():
            assert choice in {c.id for c in candidates}
            assert choice in probs
            assert 0.0 <= probs[choice] <= 1.0
            assert sum(probs.values()) == pytest.approx(1.0)


def test_fake_mode_picks_closest_average_policy() -> None:
    agents = make_agents(20, seed=3)
    candidates = some_candidates()
    votes = asyncio.run(vote(agents, COUNTRY, candidates, "general", fake=True))
    for agent in agents:
        choice, _ = votes[agent.id]
        expected = min(
            candidates,
            key=lambda c: abs(sum(p.value for p in c.policies.values()) / 3 - agent.lean),
        )
        assert choice == expected.id


def test_requests_batched_at_sixteen() -> None:
    agents = make_agents(40, seed=11)
    requests = build_requests(agents, COUNTRY, some_candidates(), "general")
    assert len(requests) == 3
    for request in requests:
        assert request["model"] == "drex-latest"
        assert len(request["questions"]) <= 16
        assert len(request["state"]["agents"]) == len(request["questions"])


def test_general_questions_include_abstain_only() -> None:
    agents = make_agents(5, seed=1)
    candidates = some_candidates()
    for request in build_requests(agents, COUNTRY, candidates, "general"):
        for question in request["questions"].values():
            assert ABSTAIN in question["criteria"]
    for request in build_requests(agents, COUNTRY, candidates, "primary"):
        for question in request["questions"].values():
            assert ABSTAIN not in question["criteria"]

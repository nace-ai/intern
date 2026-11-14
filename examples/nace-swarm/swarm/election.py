"""One election round: candidates, primaries, general election, policies,
and the agents' updates. Following steps 1-5 of "Each round" in AGENTS.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .drex import vote
from .llm import Party, generate_candidates
from .models import Agent, Candidate, CountryState
from .policy import apply_policies, update_agent

PARTIES: tuple[Party, ...] = ("Left", "Right")

Vote = tuple[str, dict[str, float]]


@dataclass
class World:
    """Everything a round needs: the agents, the country, the last winner."""

    agents: list[Agent]
    country: CountryState
    prev_winner: Candidate | None = None


def _tally(votes: dict[str, Vote]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for choice, _ in votes.values():
        counts[choice] = counts.get(choice, 0) + 1
    return counts


def _top_candidate(candidates: list[Candidate], tally: dict[str, int]) -> Candidate:
    """The candidate with the most votes; ties go to the earlier candidate."""
    best = max((tally.get(c.id, 0) for c in candidates), default=0)
    return next(c for c in candidates if tally.get(c.id, 0) == best)


async def run_round(
    world: World, k: int, fake: bool = False, usage: dict[str, int] | None = None
) -> dict[str, Any]:
    """Run one election round and update ``world`` in place.

    Returns a JSON-serializable record: candidates, primary tallies, general
    tally, winner, the country before and after, and every agent's
    (id, x, y, prev_x, prev_y, lean, vote, probs).

    If ``usage`` is given, the Drex token counts of every request in the round
    are added to it.
    """
    # 1. Each party writes 5 candidates.
    candidates = {
        party: generate_candidates(party, world.country, world.prev_winner, fake=fake)
        for party in PARTIES
    }

    # 2. Primaries: left-leaning agents vote Left, right-leaning agents vote Right.
    primary_votes: dict[Party, dict[str, Vote]] = {}
    for party in PARTIES:
        voters = [a for a in world.agents if a.lean < 0.0] if party == "Left" else [
            a for a in world.agents if a.lean >= 0.0
        ]
        primary_votes[party] = await vote(
            voters, world.country, candidates[party], "primary", fake=fake, usage=usage
        )
    primary_tallies = {party: _tally(votes) for party, votes in primary_votes.items()}
    left_winner = _top_candidate(candidates["Left"], primary_tallies["Left"])
    right_winner = _top_candidate(candidates["Right"], primary_tallies["Right"])

    # 3. General election: every agent votes between the two winners, or abstains.
    general_votes = await vote(
        world.agents, world.country, [left_winner, right_winner], "general", fake=fake, usage=usage
    )
    general_tally = _tally(general_votes)

    # 4. The winner (most general votes among the two) changes the country.
    winner = max(
        (left_winner, right_winner), key=lambda c: general_tally.get(c.id, 0)
    )
    old_country = world.country
    new_country = apply_policies(old_country, winner)

    # 5. The country change updates every agent, with a snapshot in its memory.
    prev = {a.id: (a.x, a.y) for a in world.agents}
    updated: list[Agent] = []
    for agent in world.agents:
        choice, probs = general_votes[agent.id]
        updated.append(update_agent(agent, old_country, new_country, choice, probs, k))

    world.agents = updated
    world.country = new_country
    world.prev_winner = winner

    return {
        "round": k,
        "candidates": {party: [c.model_dump() for c in cands] for party, cands in candidates.items()},
        "primary_tallies": primary_tallies,
        "general_tally": general_tally,
        "winner": winner.model_dump(),
        "country_before": old_country.model_dump(),
        "country_after": new_country.model_dump(),
        "agents": [
            {
                "id": a.id,
                "x": a.x,
                "y": a.y,
                "prev_x": prev[a.id][0],
                "prev_y": prev[a.id][1],
                "lean": a.lean,
                "vote": general_votes[a.id][0],
                "probs": general_votes[a.id][1],
                "history": [s.model_dump() for s in a.history],
            }
            for a in updated
        ],
    }

"""Drex (Nace) decision-model client: voter agents vote.

Live mode calls POST https://console.nace.ai/v1/systemone with the agents'
evidence in ``state`` and one choice question per agent. Questions cannot see
each other's answers, so up to 16 agents are batched per request and at most 8
requests are in flight at once.

Run one real request for 2 agents (needs NACE_API_KEY):
    uv run python -m swarm.drex --live-check
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
from typing import Any, Literal

import httpx

from .models import Agent, Candidate, CountryState

URL = "https://console.nace.ai/v1/systemone"
MODEL = "drex-latest"
TIMEOUT_S = 60.0
BATCH_SIZE = 16
MAX_IN_FLIGHT = 8
MAX_RETRIES = 3
RETRIABLE_STATUS = (429, 529)
FATAL_STATUS = (401, 402, 422)
ABSTAIN = "abstain"
ABSTAIN_TEXT = "Vote for neither candidate."

Stage = Literal["primary", "general"]
VoteResult = dict[str, tuple[str, dict[str, float]]]


class DrexError(RuntimeError):
    """A clear Drex error that should not be retried."""


def _agent_state(agent: Agent) -> dict[str, Any]:
    """Evidence about one agent; the format is identical on every call."""
    return {
        "wealth": agent.wealth,
        "age": agent.age,
        "gender": agent.gender,
        "health": agent.health,
        "x": agent.x,
        "y": agent.y,
        "lean": agent.lean,
        "memory": [s.model_dump() for s in agent.history],
    }


def _candidate_state(candidate: Candidate) -> dict[str, Any]:
    return {
        "party": candidate.party,
        "name": candidate.name,
        "slogan": candidate.slogan,
        "policies": {area: p.value for area, p in candidate.policies.items()},
    }


def _candidate_option(candidate: Candidate) -> str:
    return f"{candidate.name} ({candidate.party}): {candidate.slogan}"


def _question(agent_id: str, candidates: list[Candidate], stage: Stage) -> dict[str, Any]:
    options = {c.id: _candidate_option(c) for c in candidates}
    if stage == "general":
        options[ABSTAIN] = ABSTAIN_TEXT
    instructions = (
        f"You are agent `agents.{agent_id}`. Given your wealth, health, age, "
        "memory and the `country` state, which candidate in `candidates` "
        "would you vote for?"
    )
    return {"type": "choice", "instructions": instructions, "criteria": options}


def build_request(
    batch: list[Agent], country: CountryState, candidates: list[Candidate], stage: Stage
) -> dict[str, Any]:
    """Build one Drex request holding up to 16 agents."""
    state = {
        "agents": {a.id: _agent_state(a) for a in batch},
        "country": country.model_dump(),
        "candidates": {c.id: _candidate_state(c) for c in candidates},
    }
    questions = {f"vote_{a.id}": _question(a.id, candidates, stage) for a in batch}
    return {"model": MODEL, "state": state, "questions": questions}


def build_requests(
    agents: list[Agent], country: CountryState, candidates: list[Candidate], stage: Stage
) -> list[dict[str, Any]]:
    """Split the agents into batches of at most 16, one request per batch."""
    return [
        build_request(agents[i : i + BATCH_SIZE], country, candidates, stage)
        for i in range(0, len(agents), BATCH_SIZE)
    ]


def _avg_policy(candidate: Candidate) -> float:
    values = [p.value for p in candidate.policies.values()]
    return sum(values) / len(values)


def _fake_probs(distances: dict[str, float], temperature: float = 0.25) -> dict[str, float]:
    weights = {key: math.exp(-dist / temperature) for key, dist in distances.items()}
    total = sum(weights.values())
    return {key: w / total for key, w in weights.items()}


def fake_vote(
    agents: list[Agent], candidates: list[Candidate], stage: Stage
) -> VoteResult:
    """Fake mode: each agent votes for the candidate whose average policy is
    closest to its lean."""
    out: VoteResult = {}
    for agent in agents:
        distances = {c.id: abs(_avg_policy(c) - agent.lean) for c in candidates}
        best = min(distances, key=distances.get)
        out[agent.id] = (best, _fake_probs(distances))
    return out


def _backoff(attempt: int, retry_after_ms: str | None) -> float:
    if retry_after_ms:
        try:
            return float(retry_after_ms) / 1000.0
        except ValueError:
            pass
    return float(2**attempt)


async def _post(
    client: httpx.AsyncClient, request: dict[str, Any]
) -> dict[str, Any]:
    """POST one request, retrying 429/529/5xx up to MAX_RETRIES times."""
    for attempt in range(MAX_RETRIES + 1):
        response = await client.post(URL, json=request)
        if response.status_code in FATAL_STATUS:
            raise DrexError(
                f"Drex refused the request (HTTP {response.status_code}); not retrying."
            )
        if response.status_code in RETRIABLE_STATUS or 500 <= response.status_code < 600:
            if attempt >= MAX_RETRIES:
                raise DrexError(
                    f"Drex kept failing (HTTP {response.status_code}) "
                    f"after {MAX_RETRIES} retries."
                )
            await asyncio.sleep(
                _backoff(attempt, response.headers.get("retry-after-ms"))
            )
            continue
        response.raise_for_status()
        return response.json()
    raise AssertionError("unreachable")


def _collect(agents: list[Agent], responses: list[dict[str, Any]]) -> VoteResult:
    out: VoteResult = {}
    wanted = {a.id for a in agents}
    for response in responses:
        for name, answer in response["answers"].items():
            agent_id = name[len("vote_") :]
            if agent_id not in wanted:
                raise DrexError(f"Drex returned an answer for an unknown agent: {agent_id}")
            probabilities = dict(answer.get("probabilities", {}))
            choice = answer.get("choice")
            if choice is None and probabilities:
                choice = max(probabilities, key=probabilities.get)
            if choice is None:
                raise DrexError(f"Drex answer for {agent_id} has no choice.")
            out[agent_id] = (choice, probabilities)
    missing = wanted - set(out)
    if missing:
        raise DrexError(f"Drex did not answer for agents: {sorted(missing)}")
    return out


def _headers() -> dict[str, str]:
    key = os.environ.get("NACE_API_KEY")
    if not key:
        raise DrexError("NACE_API_KEY is not set in the environment.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _add_usage(usage: dict[str, int] | None, response: dict[str, Any]) -> None:
    if usage is None:
        return
    for key, value in response.get("usage", {}).items():
        usage[key] = usage.get(key, 0) + int(value)


async def vote(
    agents: list[Agent],
    country: CountryState,
    candidates: list[Candidate],
    stage: Stage,
    fake: bool = False,
    usage: dict[str, int] | None = None,
) -> VoteResult:
    """Ask every agent which candidate it would vote for.

    Returns {agent_id: (choice, probabilities)}.

    If ``usage`` is given, the input/output token counts of every request are
    added to it.
    """
    if not agents:
        return {}
    if fake:
        return fake_vote(agents, candidates, stage)
    requests = build_requests(agents, country, candidates, stage)
    sem = asyncio.Semaphore(MAX_IN_FLIGHT)
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_headers()) as client:
        async def one(request: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                response = await _post(client, request)
                _add_usage(usage, response)
                return response

        responses = await asyncio.gather(*(one(r) for r in requests))
    return _collect(agents, responses)


async def _live_check() -> None:
    """Send one real request for 2 agents and print the choices, model, usage."""
    from .individualizer import make_agents

    agents = make_agents(2, seed=1)
    country = CountryState(
        stock_market=0.5,
        class_mobility=0.5,
        foreign_relations=0.5,
        unemployment=0.4,
        healthcare_access=0.6,
    )
    candidates = [
        Candidate(
            id="l1",
            party="Left",
            name="Ada Marlowe",
            slogan="Climb down together",
            policies={
                "foreign": {"value": -0.4, "text": "multilateral trade deals"},
                "economic": {"value": -0.6, "text": "progressive taxes"},
                "social_welfare": {"value": -0.7, "text": "universal healthcare"},
            },
        ),
        Candidate(
            id="r1",
            party="Right",
            name="Bram Holt",
            slogan="Work pays",
            policies={
                "foreign": {"value": 0.5, "text": "home first on trade"},
                "economic": {"value": 0.6, "text": "deregulation"},
                "social_welfare": {"value": 0.4, "text": "work-first benefits"},
            },
        ),
    ]
    request = build_request(agents, country, candidates, "general")
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_headers()) as client:
        data = await _post(client, request)
    print("model:", data["model"])
    print("usage:", data["usage"])
    for name, answer in data["answers"].items():
        print(name, "->", answer.get("choice"), "confidence:", answer.get("confidence"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-check",
        action="store_true",
        help="send one real request for 2 agents (needs NACE_API_KEY)",
    )
    args = parser.parse_args()
    if args.live_check:
        try:
            asyncio.run(_live_check())
        except DrexError as exc:
            raise SystemExit(str(exc))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

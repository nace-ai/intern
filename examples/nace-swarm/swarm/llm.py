"""Candidate writer: one LLM writes each party's 5 candidates.

Live mode uses Qwen (DeepInfra) through the openai package (model
Qwen/Qwen3.8-27B). It asks for JSON only, validates with pydantic, retries
once if the response does not validate, then falls back to the fake
candidates.

Fake mode needs no network and returns fixed candidates.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from .models import POLICY_AREAS, Candidate, CountryState, Policy

BASE_URL = "https://api.deepinfra.com/v1/openai"
MODEL = "Qwen/Qwen3.8-27B"
NUM_CANDIDATES = 5
PARTY_ID_PREFIX = {"Left": "l", "Right": "r"}

Party = Literal["Left", "Right"]


class LLMError(RuntimeError):
    """The candidate writer could not get a valid response."""


class _RawPolicy(BaseModel):
    value: float = Field(ge=-1, le=1)
    text: str


class _RawCandidate(BaseModel):
    name: str
    slogan: str
    policies: dict[str, _RawPolicy]


# ---------------------------------------------------------------------------
# Fake mode: fixed candidates, no network
# ---------------------------------------------------------------------------

# (name, slogan, foreign, economic, social_welfare) — Left leans negative,
# Right leans positive, with variety between candidates.
_FAKE_SPECS: dict[Party, list[tuple[str, str, float, float, float]]] = {
    "Left": [
        ("Ada Marlowe", "Climb down together", -0.4, -0.6, -0.7),
        ("Cora Ellery", "Health is a right, not a perk", -0.2, -0.4, -0.9),
        ("Dane Okafor", "Wealth for the many", -0.5, -0.8, -0.3),
        ("Freya Lindqvist", "Fair rules, fair pay", -0.3, -0.5, -0.4),
        ("Gus Whitfield", "A stronger state, a stronger you", -0.6, -0.3, -0.6),
    ],
    "Right": [
        ("Bram Holt", "Work pays", 0.5, 0.6, 0.4),
        ("Carla Reyes", "Prosperity through freedom", 0.3, 0.8, 0.2),
        ("Dmitri Volkov", "Strong nation first", 0.7, 0.4, 0.5),
        ("Elise Chen", "Growth without excuses", 0.4, 0.7, 0.1),
        ("Farid Nassar", "Order enables opportunity", 0.6, 0.5, 0.3),
    ],
}

_FAKE_TEXT: dict[Party, dict[str, str]] = {
    "Left": {
        "foreign": "calm, cooperative multilateral diplomacy",
        "economic": "progressive taxation and public investment",
        "social_welfare": "expansive public welfare",
    },
    "Right": {
        "foreign": "home-first diplomacy and defense",
        "economic": "deregulation and tax cuts",
        "social_welfare": "work-first welfare",
    },
}


def fake_candidates(party: Party) -> list[Candidate]:
    """Return the fixed candidates for a party (fake mode)."""
    if party not in PARTY_ID_PREFIX:
        raise LLMError(f"unknown party: {party!r}")
    prefix = PARTY_ID_PREFIX[party]
    out: list[Candidate] = []
    for i, (name, slogan, foreign, economic, social) in enumerate(_FAKE_SPECS[party], 1):
        out.append(
            Candidate(
                id=f"{prefix}{i}",
                party=party,
                name=name,
                slogan=slogan,
                policies={
                    area: Policy(value=value, text=_FAKE_TEXT[party][area])
                    for area, value in zip(POLICY_AREAS, (foreign, economic, social))
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# Live mode: Qwen writes JSON, validated with pydantic
# ---------------------------------------------------------------------------


def _prompt(party: Party, country: CountryState, prev_winner: Candidate | None) -> str:
    lean_word = "negative (left-leaning)" if party == "Left" else "positive (right-leaning)"
    if prev_winner is None:
        prev = "none (this is the first round)"
    else:
        prev = f"{prev_winner.name} of the {prev_winner.party} party: {prev_winner.slogan}"
    return (
        f"You are writing the {NUM_CANDIDATES} candidates for the {party} party in an "
        "election simulation. Current country state: "
        f"{json.dumps(country.model_dump())}. Previous winner: {prev}.\n"
        "Return JSON only: an array of exactly 5 objects, each shaped as\n"
        '{"name": "...", "slogan": "one line", '
        '"policies": {"foreign": {"value": <number>, "text": "one line"}, '
        '"economic": {"value": <number>, "text": "one line"}, '
        '"social_welfare": {"value": <number>, "text": "one line"}}}.\n'
        f"Policy values range from -1 (left) to +1 (right); the {party} party's values "
        f"should lean {lean_word}, and the 5 candidates must differ from each other. "
        "Give each candidate distinct names, slogans and policy texts. "
        "Return nothing except the JSON array."
    )


def _ask_llm(party: Party, country: CountryState, prev_winner: Candidate | None) -> str:
    api_key = os.environ.get("DEEPINFRA_API_KEY")
    if not api_key:
        raise LLMError("DEEPINFRA_API_KEY is not set in the environment.")
    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": _prompt(party, country, prev_winner)}],
        temperature=0.9,
    )
    return response.choices[0].message.content or ""


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array in the response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("the JSON response is not an array")
    return data


def _build_candidates(raw: list[Any], party: Party) -> list[Candidate]:
    parsed = _RawCandidate.model_validate(raw)
    if len(parsed) != NUM_CANDIDATES:
        raise ValueError(f"expected {NUM_CANDIDATES} candidates, got {len(parsed)}")
    prefix = PARTY_ID_PREFIX[party]
    out: list[Candidate] = []
    for i, cand in enumerate(parsed, 1):
        out.append(
            Candidate(
                id=f"{prefix}{i}",
                party=party,
                name=cand.name,
                slogan=cand.slogan,
                policies={
                    area: Policy(value=cand.policies[area].value, text=cand.policies[area].text)
                    for area in POLICY_AREAS
                },
            )
        )
    return out


def _live_candidates(party: Party, country: CountryState, prev_winner: Candidate | None) -> list[Candidate] | None:
    """Ask the LLM; retry once on any failure, otherwise return None."""
    for _ in range(2):
        try:
            return _build_candidates(_parse_json_array(_ask_llm(party, country, prev_winner)), party)
        except (LLMError, ValueError, json.JSONDecodeError, ValidationError, KeyError, IndexError):
            continue
    return None


def generate_candidates(
    party: Party,
    country: CountryState,
    prev_winner: Candidate | None = None,
    fake: bool = False,
) -> list[Candidate]:
    """Generate the party's 5 candidates.

    Live mode asks Qwen for JSON only, validates it with pydantic, retries
    once, then falls back to the fake candidates.
    """
    if party not in PARTY_ID_PREFIX:
        raise LLMError(f"unknown party: {party!r}")
    if fake:
        return fake_candidates(party)
    return _live_candidates(party, country, prev_winner) or fake_candidates(party)

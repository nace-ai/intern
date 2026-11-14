"""Pydantic models for the Nace swarm simulation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

POLICY_AREAS: tuple[str, ...] = ("foreign", "economic", "social_welfare")


class Policy(BaseModel):
    """A single policy: a position from -1 (left) to +1 (right) plus a one-line description."""

    value: float = Field(ge=-1, le=1)
    text: str


class Snapshot(BaseModel):
    """A record of an agent's own state and vote at one round."""

    round: int
    wealth: float
    health: float
    lean: float = Field(ge=-1, le=1)
    x: float
    y: float
    vote: str | None = None
    vote_probs: dict[str, float] = Field(default_factory=dict)


class Agent(BaseModel):
    """A voter agent living on the 40x40 grid."""

    id: str
    wealth: float = Field(ge=0)
    age: float
    gender: str
    health: float
    x: float
    y: float
    lean: float = Field(ge=-1, le=1)
    history: list[Snapshot] = Field(default_factory=list)


class CountryState(BaseModel):
    """The country's state; every field is a float from 0 to 1."""

    stock_market: float = Field(ge=0, le=1)
    class_mobility: float = Field(ge=0, le=1)
    foreign_relations: float = Field(ge=0, le=1)
    unemployment: float = Field(ge=0, le=1)
    healthcare_access: float = Field(ge=0, le=1)


class Candidate(BaseModel):
    """A party candidate with policies on the three policy areas."""

    id: str
    party: Literal["Left", "Right"]
    name: str
    slogan: str
    policies: dict[str, Policy]

    @field_validator("policies")
    @classmethod
    def _policies_must_cover_all_areas(cls, value: dict[str, Policy]) -> dict[str, Policy]:
        keys = set(value)
        if keys != set(POLICY_AREAS):
            missing = sorted(set(POLICY_AREAS) - keys)
            extra = sorted(keys - set(POLICY_AREAS))
            raise ValueError(
                f"policies must have exactly the keys {list(POLICY_AREAS)} "
                f"(missing={missing}, extra={extra})"
            )
        return value

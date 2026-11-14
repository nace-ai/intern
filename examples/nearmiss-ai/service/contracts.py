"""Pydantic models for the shared interface contract (contracts.md, v1.0).

These mirror contracts.md field-for-field. Builder B never imports Builder A's
code; the only coupling is this JSON contract.

Extra optional output fields (allowed by contracts.md: "Unknown extra fields may
be ignored by readers") are marked "extension" below. They carry provenance that
helps the dashboard but nothing in them is required by the contract.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "1.0"

AgentName = Literal["pattern", "evidence", "investigation", "coordinator"]
UncertaintyStatus = Literal["supported", "conflicted", "insufficient", "unresolved"]
NextAction = Literal["investigate_cluster", "request_context", "specialist_review", "no_pattern_identified"]
ReviewPriority = Literal["routine", "elevated", "urgent"]


# --------------------------------------------------------------------------- A -> B


class CandidateIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    retrieval_score: float

    @field_validator("retrieval_score")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("retrieval_score must be a finite number")
        return value


class DataSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    url: str | None = None
    sample: str = Field(min_length=1)


class CaseContext(BaseModel):
    """Contract A: the bounded case package Builder A sends to POST /analyze."""

    model_config = ConfigDict(extra="ignore")

    contract_version: Literal["1.0"]
    case_id: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    candidate_incidents: list[CandidateIncident] = Field(default_factory=list)
    retrieval_method: str = Field(min_length=1)
    data_source: DataSource

    @field_validator("narrative")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("narrative must not be blank")
        return value

    @model_validator(mode="after")
    def _candidates_differ_from_case(self) -> "CaseContext":
        for candidate in self.candidate_incidents:
            if candidate.case_id == self.case_id:
                raise ValueError(
                    f"candidate_incidents must not contain the selected case itself ({self.case_id})"
                )
        return self


# --------------------------------------------------------------------------- B -> A


class EvidenceRef(BaseModel):
    """Contract B. Only built from real NDI output (see evidence/store.py)."""

    document_id: str
    title: str
    source: str
    page_or_region: str | None
    passage_text: str
    status: Literal["parsed", "grounded"]
    relevant_claim: str


class ErrorItem(BaseModel):
    code: str
    message: str
    agent: AgentName | None = None


class AuxiliaryDecision(BaseModel):
    """Extension: a secondary Drex question asked in the same call as the primary one."""

    question_id: str
    question: str
    type: Literal["choice", "noul"]
    selected_option: str | None = None
    probabilities: dict[str, float] | None = None
    yes_probability: float | None = None


class AgentMessage(BaseModel):
    """Contract C: one chronological entry of the agent_trace."""

    agent: AgentName
    round_number: int = Field(ge=1, le=2)
    input_summary: str
    decision_question: str
    allowed_options: list[str] = Field(min_length=1)
    selected_option: str
    probabilities: dict[str, float]
    supporting_incident_ids: list[str]
    evidence_refs: list[EvidenceRef]
    uncertainty_status: UncertaintyStatus
    duration_seconds: float = Field(ge=0)
    action_summary: str
    # extensions (optional, ignorable by readers)
    auxiliary_decisions: list[AuxiliaryDecision] = Field(default_factory=list)
    drex_request_id: str | None = None
    drex_model: str | None = None

    @model_validator(mode="after")
    def _probabilities_cover_options(self) -> "AgentMessage":
        if self.selected_option not in self.allowed_options:
            raise ValueError("selected_option must be one of allowed_options")
        if set(self.probabilities) != set(self.allowed_options):
            raise ValueError("probabilities must contain exactly the allowed options")
        for value in self.probabilities.values():
            if not (0.0 <= value <= 1.0):
                raise ValueError("probabilities must be in [0, 1]")
        return self


class PatternClaim(BaseModel):
    pattern_family: str
    supporting_incident_ids: list[str]


class InvestigationResult(BaseModel):
    """Contract D."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    case_id: str
    run_id: str
    status: Literal["complete", "incomplete"]
    agent_trace: list[AgentMessage]
    initial_claim: PatternClaim | None
    revised_claim: PatternClaim | None
    final_recommended_next_action: NextAction | None
    review_priority: ReviewPriority | None
    references: list[EvidenceRef]
    total_duration_seconds: float = Field(ge=0)
    provider_model_details: dict[str, Any]
    errors: list[ErrorItem]
    # extension: explicit policy rules that changed the outcome, in plain language
    policy_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _status_consistency(self) -> "InvestigationResult":
        if self.status == "incomplete":
            if self.final_recommended_next_action is not None or self.review_priority is not None:
                raise ValueError("an incomplete result must not carry a final action or priority")
            if not self.errors:
                raise ValueError("an incomplete result must carry at least one error")
        else:
            if self.final_recommended_next_action is None or self.review_priority is None:
                raise ValueError("a complete result needs a final action and a review priority")
        return self


# --------------------------------------------------------------------------- API


class HealthResponse(BaseModel):
    status: Literal["ready", "degraded"]
    nace_configured: bool


class EvidenceStatusResponse(BaseModel):
    status: Literal["ready", "not_ready", "error"]
    document_count: int
    detail: str

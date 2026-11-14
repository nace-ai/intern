"""Typed contracts for facts, cases, and provider results."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Verdict = Literal["CLEAR", "HOLD", "REVIEW"]
HazardClass = Literal["other", "class4_acoustic"]
CertStatus = Literal["valid", "expired", "missing"]
WaiverStatus = Literal["valid", "expired", "missing"]
SealStatus = Literal["intact", "damaged"]
ProviderName = Literal["drex", "openrouter", "oracle_baseline", "regex_baseline"]
DecisionStatus = Literal["ok", "invalid", "error", "timeout"]
RunMode = Literal["mock", "live", "reference"]
EvidenceMode = Literal["raw_text", "extracted"]


def _parse_decimal(value: Any, *, positive: bool, nonnegative: bool) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("numeric facts must be decimal strings or integers, not floats or booleans")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid numeric value") from exc
    if not number.is_finite():
        raise ValueError("invalid numeric value")
    if nonnegative and number < 0:
        raise ValueError("measurement cannot be negative")
    if positive and number <= 0:
        raise ValueError("measurement must be positive")
    return number


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_singing_or_telepathic: bool | None
    hazard_class: HazardClass | None
    cert_status: CertStatus | None
    waiver_status: WaiverStatus | None
    seal_status: SealStatus | None
    seal_pressure_kpa: Decimal | None
    manifest_gross_weight_kg: Decimal | None
    certificate_gross_weight_kg: Decimal | None
    conflicted_fields: list[str] = Field(default_factory=list)

    @field_validator("seal_pressure_kpa", mode="before")
    @classmethod
    def _pressure(cls, value: Any) -> Decimal | None:
        return _parse_decimal(value, positive=False, nonnegative=True)

    @field_validator("manifest_gross_weight_kg", "certificate_gross_weight_kg", mode="before")
    @classmethod
    def _weight(cls, value: Any) -> Decimal | None:
        return _parse_decimal(value, positive=True, nonnegative=False)

    def dump_public(self) -> dict[str, Any]:
        raw = self.model_dump(mode="json")
        for key in ("seal_pressure_kpa", "manifest_gross_weight_kg", "certificate_gross_weight_kg"):
            value = getattr(self, key)
            raw[key] = None if value is None else format(value, "f")
        return raw


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    kind: Literal["cargo_manifest", "containment_certificate", "inspector_note"]
    text: str


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    quote: str


class FieldAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected: Any = None
    status: Literal["stated", "explicitly_absent", "unknown", "conflicted"]
    quotes: list[Quote] = Field(default_factory=list)


class SourceAnnotations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, FieldAnnotation]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    split: Literal["dev", "test", "hard"]
    template_family: str
    rule_signature: str
    facts: Facts
    documents: list[Document]
    source_annotations: SourceAnnotations
    expected_verdict: Verdict


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    shared_input: str
    input_hash: str
    evidence_mode: EvidenceMode


class DecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    provider: ProviderName
    requested_model: str | None = None
    actual_model: str | None = None
    verdict: Verdict | None = None
    probabilities: dict[str, float] | None = None
    status: DecisionStatus
    latency_ms: float | None = None
    server_latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None
    cost_source: str | None = None
    request_id: str | None = None
    input_hash: str
    timestamp: str
    mode: RunMode
    cached: bool | None = None
    error: str | None = None

    def dump_public(self) -> dict[str, Any]:
        raw = self.model_dump(mode="json")
        if self.cost_usd is None:
            raw["cost_usd"] = None
        else:
            raw["cost_usd"] = format(self.cost_usd, "f")
        return raw

"""Shared data shapes for Quorum.

These live here as pydantic models. The file is the agreement between
the checks/policy modules (Himanshu) and the intake/panel modules (Dhruv).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FieldCitation(BaseModel):
    page: int
    polygons: list[list[list[float]]] = []
    source_text: str = ""


class Field(BaseModel):
    value: str | int | None = None
    status: str = "not_found"  # found | not_found | ambiguous
    citations: list[FieldCitation] = []


class ParsedInvoice(BaseModel):
    """Output of NDI intake."""

    doc_id: str = ""
    source_file: str = ""
    content_hash: str = ""
    fields: dict[str, Field] = {}
    warnings: list[str] = []
    parse_ms: int = 0
    stub: bool = False


class Verification(BaseModel):
    iban: str = ""
    method: str = ""
    verified_on: str = ""


class TrustedSupplier(BaseModel):
    supplier_id: str = ""
    names: list[str] = []
    iban: str = ""
    bic: str = ""
    median_amount_minor: int | None = None
    paid_invoice_numbers: list[str] = []
    verifications: list[Verification] = []


class Facts(BaseModel):
    """Facts produced by checks.facts()."""

    supplier_known: bool = False
    supplier_id: str | None = None
    iban_status: str = "not_found"  # found | not_found | ambiguous
    iban_matches_trusted: bool = False
    trusted_verification_for_this_iban: bool = False
    duplicate_invoice_number: bool = False
    amount_ratio_to_supplier_median: float | None = None


class PanelAnswer(BaseModel):
    id: str
    p_yes: float


class GuardResult(BaseModel):
    raised: bool = False
    findings: list[str] = []


class DecisionUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class Decision(BaseModel):
    """Output of the Drex panel and of the baseline."""

    decision_id: str = ""
    doc_id: str = ""
    system: str = "drex"  # drex | baseline
    model: str = ""
    raw_action: str | None = None
    final_action: str | None = None
    policy_action: str | None = None
    p: dict[str, float] = {}
    risk_score: float | None = None
    panel: list[PanelAnswer] = []
    guard: GuardResult = GuardResult()
    policy: dict[str, float] = {"L": 100, "V": 5, "D": 30}
    request_id: str | None = None
    decision_ms: float | None = None
    wall_ms: int = 0
    usage: DecisionUsage = DecisionUsage()
    # Cost of this decision's API call in USD as reported by the provider, when it reports one
    # (the LLM guard on DeepInfra). None for Drex: its cost is derived from input tokens.
    cost_usd: float | None = None
    error: str | None = None
    stub: bool = False

"""Shared data contract. Every module speaks these types; adapters translate wire formats into them.

Money is ALWAYS integer cents (`*_cents: int`). Dates are ISO strings "YYYY-MM-DD".
Polygons are normalized [0, 1] page coordinates, origin top-left, one polygon = list of (x, y) points.
Changing a field here affects every agent: add optional fields only; never rename or remove.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- enums


class ErrorType(StrEnum):
    DUPLICATE = "DUPLICATE"
    MUE_UNITS_EXCEEDED = "MUE_UNITS_EXCEEDED"
    NCCI_UNBUNDLED = "NCCI_UNBUNDLED"
    OVER_FEE_SCHEDULE = "OVER_FEE_SCHEDULE"
    EOB_BILL_MISMATCH = "EOB_BILL_MISMATCH"
    SERVICE_AFTER_DEATH = "SERVICE_AFTER_DEATH"
    MISSING_DATA = "MISSING_DATA"
    NO_SURPRISES_ACT = "NO_SURPRISES_ACT"


class Action(StrEnum):
    OK = "OK"
    DISPUTE = "DISPUTE"
    APPEAL = "APPEAL"
    ASK_PATIENT = "ASK_PATIENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DenialCategory(StrEnum):
    """CARC families (see research/raw/MED-D-rules-design.md)."""

    MEDICAL_NECESSITY = "MEDICAL_NECESSITY"
    PRIOR_AUTH = "PRIOR_AUTH"
    NOT_COVERED = "NOT_COVERED"
    OUT_OF_NETWORK = "OUT_OF_NETWORK"
    CODING_BUNDLING = "CODING_BUNDLING"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"
    TIMELY_FILING = "TIMELY_FILING"
    ELIGIBILITY = "ELIGIBILITY"
    MISSING_INFO = "MISSING_INFO"


Lane = Literal["frontier", "drex", "hybrid"]
Route = Literal["drex_kept", "frontier_verified", "frontier_only", "human", "hard_cap"]
DocKind = Literal["bill", "eob", "denial", "plan"]
QuestionType = Literal["noul", "choice", "score"]

# ---------------------------------------------------------------- documents / evidence


class Citation(BaseModel):
    doc: DocKind
    page: int = 1  # 1-based
    polygons: list[list[tuple[float, float]]] = Field(default_factory=list)
    text: str | None = None  # the exact text printed in that region


class Patient(BaseModel):
    patient_id: str  # pseudonymous, e.g. "PT-0042". No real names anywhere (Nace terms §5.6)
    display_name: str = "Patient"  # synthetic placeholder only
    dob: str | None = None
    date_of_death: str | None = None  # set only for SERVICE_AFTER_DEATH cases
    member_id: str | None = None


class Provider(BaseModel):
    name: str  # synthetic facility name
    npi: str | None = None
    in_network: bool = True
    address: str | None = None


class BillLine(BaseModel):
    line_no: int  # 1-based, unique within the bill
    dos: str | None  # date of service; None = missing (MISSING_DATA)
    code: str  # CPT/HCPCS code
    modifiers: list[str] = Field(default_factory=list)
    label: str  # OUR plain-language description (never AMA descriptor text)
    units: int = 1
    charge_cents: int
    pos: str | None = None  # place of service code, e.g. "11", "22", "23"
    rendering_provider: str | None = None


class Bill(BaseModel):
    account_no: str
    statement_date: str
    lines: list[BillLine]
    total_charges_cents: int
    insurance_paid_cents: int = 0
    adjustments_cents: int = 0
    patient_balance_cents: int  # what the bill asks the patient to pay


class EobLine(BaseModel):
    line_no: int  # refers to BillLine.line_no
    code: str
    units: int = 1
    billed_cents: int
    allowed_cents: int
    plan_paid_cents: int
    patient_resp_cents: int  # deductible + copay + coinsurance + non-covered
    carc: list[str] = Field(default_factory=list)  # e.g. ["CO-97"]
    remark: str | None = None


class Eob(BaseModel):
    payer: str  # synthetic payer name
    claim_id: str
    processed_date: str
    lines: list[EobLine]
    patient_resp_total_cents: int


class Denial(BaseModel):
    letter_date: str
    carc: str  # e.g. "CO-50"
    category: DenialCategory  # GOLD lives in labels; this is what the letter claims
    reason_text: str  # synthetic plan wording
    service_code: str | None = None
    appeal_deadline: str | None = None
    plan_section: str | None = None


class PlanSummary(BaseModel):
    plan_name: str
    plan_type: Literal["PPO", "HMO", "EPO", "HDHP"] = "PPO"
    deductible_cents: int
    deductible_met_cents: int = 0
    coinsurance_pct: int = 20
    copay_cents: int = 0
    oop_max_cents: int
    emergency_nsa_protected: bool = True


class DocRef(BaseModel):
    kind: DocKind
    pdf: str  # path relative to billproof/, e.g. "data/synthetic/pdf/SYN-0001_bill.pdf"
    pages: int = 1


class Case(BaseModel):
    """Everything a lane may see. NEVER put gold labels or planted-error info in here."""

    case_id: str  # "SYN-0001" synthetic · "IMR-..." real
    split: Literal["dev", "test", "demo"]
    title: str  # short neutral title for the UI, e.g. "ER visit · knee MRI"
    patient: Patient
    provider: Provider
    bill: Bill
    eob: Eob | None = None
    denial: Denial | None = None
    plan: PlanSummary
    service_setting: Literal["office", "outpatient", "inpatient", "emergency", "lab", "imaging"] = "office"
    docs: list[DocRef] = Field(default_factory=list)
    citations: dict[str, Citation] = Field(default_factory=dict)  # field path -> where it is printed
    # field path examples: "bill.lines.3.charge_cents", "bill.lines.3.code", "eob.lines.3.patient_resp_cents",
    # "bill.patient_balance_cents", "eob.patient_resp_total_cents", "denial.carc", "patient.date_of_death"


# ---------------------------------------------------------------- deterministic rules


class RuleFlag(BaseModel):
    """Output of rules/checks.py. Deterministic, code-computed."""

    check: ErrorType
    line_no: int | None = None  # None = case-level
    fired: bool  # True = rule says there's a problem
    detail: str  # human-readable, e.g. "units 4 > MUE 1 (MAI 2)"
    rule_cite: str  # e.g. "CMS MUE (practitioner) 70553: 1/day"
    amount_cents: int = 0  # dollars at stake on this flag (code-computed)
    evidence: list[str] = Field(default_factory=list)  # Case.citations keys


# ---------------------------------------------------------------- decisions


class Question(BaseModel):
    """One typed Drex question.  noul: yes/no · choice: criteria={label: description} · score: criteria=[levels]."""

    name: str  # see docs/CONTRACT.md §Question names
    type: QuestionType
    instructions: str
    criteria: dict[str, str | None] | list[str] | None = None
    family: str  # e.g. "duplicate", "mue", "ncci", "fee", "eob_mismatch", "after_death", "missing", "nsa",
    #              "denial_category", "action", "appeal_strength", "imr_overturn", "letter_*"
    line_no: int | None = None
    evidence: list[str] = Field(default_factory=list)  # Case.citations keys relevant to this question

    def options(self) -> list[str]:
        if self.type == "noul":
            return ["yes", "no"]
        if self.type == "choice":
            assert isinstance(self.criteria, dict)
            return list(self.criteria)
        assert isinstance(self.criteria, list)
        return list(self.criteria)


class Answer(BaseModel):
    name: str
    choice: str  # one of question.options()
    probabilities: dict[str, float] | None = None  # native probs (Drex). None for LLM lanes
    confidence: float  # P(choice): native for Drex, SELF-REPORTED for frontier (label it so in UI)
    confidence_kind: Literal["native", "self_reported", "rule"] = "native"
    route: Route | None = None  # set by lanes (hybrid)
    rationale: str | None = None  # frontier only, short
    raw: dict[str, Any] = Field(default_factory=dict)


class DecisionResult(BaseModel):
    """One adapter call (or one lane's combined result for a case)."""

    lane: str  # "drex" | "frontier" | "hybrid" (| "naive")
    model: str  # as REPORTED by the provider (Drex: assert == DREX_MODEL)
    answers: dict[str, Answer]
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # billed (from usage); simulated cost for mocks
    simulated: bool = False
    replayed: bool = False
    request_id: str | None = None
    error: str | None = None
    calls: int = 1  # hybrid may make 1 drex + 1 frontier call


class GoldItem(BaseModel):
    """Answer key for one question. Only simulators, the runner and metrics may read gold."""

    answer: str
    difficulty: float = 0.3  # 0 easy .. 1 hard; drives simulators (never shown to real models)
    error_type: ErrorType | None = None
    rule_id: str | None = None


class LineVerdict(BaseModel):
    line_no: int
    action: Action
    reasons: list[str] = Field(default_factory=list)  # ErrorType values / short reasons
    disputed_cents: int = 0
    rule_cites: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """Code-computed. Models never set dollar figures."""

    case_action: Action  # worst-of line actions + case-level checks
    lines: list[LineVerdict]
    billed_balance_cents: int  # what the bill asks for
    owed_cents: int  # what code says is actually owed
    disputed_cents: int
    needs_human: bool
    human_reasons: list[str] = Field(default_factory=list)
    deadline: str | None = None


class LetterClause(BaseModel):
    clause_id: str  # file stem in letters/clauses/
    title: str
    text: str  # rendered (slots filled by code)
    picked_by: str  # question name that selected it
    probability: float | None = None
    evidence: list[Citation] = Field(default_factory=list)


class Letter(BaseModel):
    letter_type: Literal["dispute", "appeal", "itemized_request", "none"]
    recipient: Literal["provider_billing", "insurer"]
    subject: str
    clauses: list[LetterClause]
    amount_cents: int
    deadline: str | None = None
    generated_words: int = 0  # words written by an LLM: 0 in v1 ("without writing a single word")


class CaseAnalysis(BaseModel):
    """What pipeline.analyze_case returns and what the UI renders."""

    case_id: str
    flags: list[RuleFlag]
    questions: list[Question]
    lanes: dict[str, DecisionResult]  # keyed by lane
    verdicts: dict[str, Verdict]  # keyed by lane
    letter: Letter | None = None  # built from the hybrid lane
    gold: dict[str, GoldItem] | None = None  # only when the caller allows (UI scoring, bench)

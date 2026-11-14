"""Real benchmark: California DMHC Independent Medical Review (IMR) outcomes.

Each item is one real external-review case, reduced to PRE-DECISION fields
only (docs/CONTRACT.md §2): year, diagnosis/treatment categories, the
requested treatment, the plan's denial basis, age range, and a neutral
plan_reason string. The raw CSV's `Findings` column is the reviewer's
post-decision narrative — it literally names the outcome — so it is NEVER
shipped in an item. It is used at build time for ONE thing: the phrase
"requested ... for <X>" is a pre-decision fact (what the enrollee asked the
plan to cover). `extract_requested_treatment` pulls that span, then
`strip_decision_language` + the outcome-word guard sanitize it; on any doubt
the build falls back to the structured TreatmentSubCategory column.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache

from billproof.config import REAL_DIR
from billproof.schema import GoldItem, Question

ITEMS_PATH = REAL_DIR / "imr_items.jsonl"
LABELS_PATH = REAL_DIR / "imr_labels.jsonl"
SHA_PATH = REAL_DIR / "imr_labels.sha256"

# ---------------------------------------------------------------- leakage guard

# Verdict phrases / reviewer-narrative markers. Any of these in a
# model-visible field leaks the outcome. ("medical necessity" the noun is the
# DMHC denial-basis category and is allowed; "medically necessary" is the
# reviewer's verdict wording and is banned.)
OUTCOME_PATTERNS = [
    r"overturn",
    r"uphold",
    r"revers",
    r"rescind",
    r"final\s*result",
    r"reviewer",
    r"determin",
    r"findings",
    r"medically\s+(?:un)?necessary",
    r"beneficial\s+(?:for|to)",
    r"credential",
    r"board[-\s]?certif",
    r"denial\s+should",
    r"plan'?s\s+denial",
    r"standard\s+therapy",
    r"emergency\s+medical\s+services",
    r"independent\s+medical",
]
OUTCOME_RE = re.compile("|".join(OUTCOME_PATTERNS), re.IGNORECASE)


def strip_decision_language(text: str) -> str:
    """Remove outcome-revealing spans from text; defensive pre-ship pass.

    Cuts the "Final Result"/"Credentials" tail and scrubs verdict phrases to a
    single space. The leakage test bans OUTCOME_RE from every shipped field.
    """
    t = re.sub(r"(?:final\s*result|credentials?/qualifications?)\s*:.*", "", text, flags=re.I | re.S)
    t = OUTCOME_RE.sub(" ", t)
    return re.sub(r"\s{2,}", " ", t).strip(" .,;:- ")


def has_outcome_language(text: str) -> bool:
    return bool(OUTCOME_RE.search(text))


# ------------------------------------------------------- pre-decision extraction

_REQ_RE = re.compile(
    r"requested\s+(?:continued\s+|additional\s+)?(?:prior\s+)?"
    r"(?:authorization\s+(?:and|for)\s+)?(?:authorization|coverage|reimbursement|payment)"
    r"\s+(?:and\s+coverage\s+)?for\s+(.+?)(?:\.|,|;|\n|$)",
    re.IGNORECASE,
)
# Drop trailing circumstance clauses ("provided from X through Y", "to treat ...").
_TRIM_RE = re.compile(
    r"\s+(?:provided|performed|received|rendered|from|during|on|between|covering|"
    r"for\s+the\s+period|to\s+treat|as\s+treatment|because|due|that|which)\b.*$",
    re.IGNORECASE,
)


def extract_requested_treatment(findings: str) -> str | None:
    """Pull 'what the enrollee asked for' — a pre-decision fact — from the
    Findings opening sentence. Returns a short sanitized span, or None."""
    m = _REQ_RE.search(findings[:500])
    if not m:
        return None
    t = _TRIM_RE.sub("", m.group(1)).strip(" .,;:-")
    t = re.sub(r"\s{2,}", " ", t)
    if not (3 <= len(t) <= 90) or has_outcome_language(t):
        return None
    return t


# --------------------------------------------------------------- item shaping

DENIAL_TYPES = {  # raw `Type` column -> (item code, plan_reason label)
    "Medical Necessity": ("medical_necessity", "medical necessity"),
    "Experimental/Investigational": ("experimental_investigational", "experimental/investigational"),
    "Urgent Care": ("urgent_care", "urgent-care eligibility"),
}

ITEM_FIELDS = (
    "case_id", "split", "year", "diagnosis_category", "treatment_category",
    "treatment_requested", "denial_type", "age_range", "plan_reason",
)


def plan_reason(denial_type: str, treatment: str) -> str:
    """Neutral restatement of the plan's stated basis (pre-decision)."""
    label = next((v[1] for v in DENIAL_TYPES.values() if v[0] == denial_type), denial_type)
    return f"Health plan denied coverage for {treatment}; stated basis: {label}."


# ------------------------------------------------------------------ interface


@lru_cache(maxsize=1)
def _items() -> list[dict]:
    if not ITEMS_PATH.exists():
        return []
    return [json.loads(l) for l in ITEMS_PATH.read_text().splitlines() if l.strip()]


@lru_cache(maxsize=1)
def _labels() -> dict[str, dict]:
    if not LABELS_PATH.exists():
        return {}
    return {r["case_id"]: r["gold"] for r in map(json.loads, LABELS_PATH.read_text().splitlines()) if r}


def list_items(split: str | None = None) -> list[dict]:
    items = _items()
    return items if split is None else [i for i in items if i["split"] == split]


def build_questions(item: dict) -> list[Question]:
    return [
        Question(
            name="imr_overturn",
            type="noul",
            instructions=(
                "Appeal triage, not medical advice: a health plan denied the "
                "requested treatment and the enrollee appealed to independent "
                "medical review. Using only the pre-decision details in the "
                "state (diagnosis and treatment categories, requested "
                "treatment, age range, and the plan's stated basis), would an "
                "independent reviewer likely OVERTURN this denial? Answer "
                "'yes' if the denial would likely be reversed, 'no' if it "
                "would likely stand."
            ),
            family="imr_overturn",
        )
    ]


def build_state(item: dict) -> dict:
    """The exact JSON every lane sees. Items contain only pre-decision fields
    (enforced by tests/test_imr_leakage.py), so the state IS the item."""
    return {k: item[k] for k in ITEM_FIELDS}


def load_gold(case_id: str) -> dict[str, GoldItem]:
    return {q: GoldItem(**g) for q, g in _labels().get(case_id, {}).items()}


def label_sha256() -> str:
    """sha256 of imr_labels.jsonl (shown on stage before a run)."""
    if SHA_PATH.exists():
        return SHA_PATH.read_text().split()[0]
    if LABELS_PATH.exists():
        return hashlib.sha256(LABELS_PATH.read_bytes()).hexdigest()
    return ""

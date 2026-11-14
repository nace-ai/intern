"""Deterministic fictional customs oracle. Labels never come from a model."""

from __future__ import annotations

from decimal import Decimal

from schemas import Facts, Verdict

PRESSURE_HOLD_BELOW = Decimal("90")
WEIGHT_REVIEW_ABOVE = Decimal("0.03")

ALWAYS_REQUIRED = (
    "is_singing_or_telepathic",
    "hazard_class",
    "seal_status",
    "seal_pressure_kpa",
    "manifest_gross_weight_kg",
    "cert_status",
)


def missing_required(facts: Facts) -> list[str]:
    """Names of required facts that are unknown. Explicit 'missing' is known."""
    missing: list[str] = []
    for name in ALWAYS_REQUIRED:
        if getattr(facts, name) is None:
            missing.append(name)
    bio_applies = facts.is_singing_or_telepathic is True
    certificate_not_valid = facts.cert_status != "valid"
    if bio_applies and certificate_not_valid and facts.waiver_status is None:
        missing.append("waiver_status")
    certificate_exists = facts.cert_status in ("valid", "expired")
    if certificate_exists and facts.certificate_gross_weight_kg is None:
        missing.append("certificate_gross_weight_kg")
    return missing


def weight_ratio(facts: Facts) -> Decimal | None:
    if facts.cert_status not in ("valid", "expired"):
        return None
    if facts.certificate_gross_weight_kg is None or facts.manifest_gross_weight_kg is None:
        return None
    return abs(facts.certificate_gross_weight_kg - facts.manifest_gross_weight_kg) / facts.manifest_gross_weight_kg


def decide(facts: Facts) -> Verdict:
    """Apply the published precedence. Boundary comparisons use Decimal."""
    if facts.seal_status == "damaged":
        return "HOLD"
    if facts.seal_pressure_kpa is not None and facts.seal_pressure_kpa < PRESSURE_HOLD_BELOW:
        return "HOLD"
    if facts.conflicted_fields or missing_required(facts):
        return "REVIEW"
    ratio = weight_ratio(facts)
    if ratio is not None and ratio > WEIGHT_REVIEW_ABOVE:
        return "REVIEW"
    if facts.is_singing_or_telepathic is True and facts.cert_status != "valid":
        waiver_clears = facts.waiver_status == "valid" and facts.hazard_class != "class4_acoustic"
        if not waiver_clears:
            return "REVIEW"
    return "CLEAR"

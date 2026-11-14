"""Independently specified outcomes for precedence and numeric boundaries."""

from decimal import Decimal

import pytest

from rules import decide, missing_required, weight_ratio
from schemas import Facts


def facts(**overrides) -> Facts:
    base = {
        "is_singing_or_telepathic": False,
        "hazard_class": "other",
        "cert_status": "valid",
        "waiver_status": None,
        "seal_status": "intact",
        "seal_pressure_kpa": "101.325",
        "manifest_gross_weight_kg": "1000",
        "certificate_gross_weight_kg": "1000",
        "conflicted_fields": [],
    }
    base.update(overrides)
    return Facts.model_validate(base)


def test_exact_90_kpa_is_not_hold():
    assert decide(facts(seal_pressure_kpa="90")) == "CLEAR"
    assert decide(facts(seal_pressure_kpa="90.0")) == "CLEAR"
    assert decide(facts(seal_pressure_kpa="90.000")) == "CLEAR"


def test_pressure_just_below_90_is_hold():
    assert decide(facts(seal_pressure_kpa="89.999")) == "HOLD"
    assert decide(facts(seal_pressure_kpa="0")) == "HOLD"


def test_exact_three_percent_is_not_review():
    sample = facts(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030")
    assert weight_ratio(sample) == Decimal("0.03")
    assert decide(sample) == "CLEAR"
    low = facts(manifest_gross_weight_kg="200", certificate_gross_weight_kg="194")
    assert weight_ratio(low) == Decimal("0.03")
    assert decide(low) == "CLEAR"


def test_weight_just_over_three_percent_is_review():
    sample = facts(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030.001")
    assert weight_ratio(sample) > Decimal("0.03")
    assert decide(sample) == "REVIEW"


def test_damaged_seal_overrides_missing_and_conflict():
    sample = facts(
        seal_status="damaged",
        is_singing_or_telepathic=None,
        hazard_class=None,
        cert_status=None,
        seal_pressure_kpa=None,
        manifest_gross_weight_kg=None,
        certificate_gross_weight_kg=None,
        conflicted_fields=["hazard_class", "cert_status"],
    )
    assert decide(sample) == "HOLD"


def test_low_pressure_overrides_missing_information():
    sample = facts(
        seal_pressure_kpa="42.5",
        is_singing_or_telepathic=None,
        hazard_class=None,
        cert_status=None,
        seal_status=None,
        manifest_gross_weight_kg=None,
        certificate_gross_weight_kg=None,
    )
    assert decide(sample) == "HOLD"


def test_class4_alone_is_not_hold():
    assert decide(facts(hazard_class="class4_acoustic")) == "CLEAR"


def test_waiver_cannot_bypass_weight_mismatch():
    sample = facts(
        is_singing_or_telepathic=True,
        cert_status="expired",
        waiver_status="valid",
        hazard_class="other",
        manifest_gross_weight_kg="800",
        certificate_gross_weight_kg="900",
    )
    assert decide(sample) == "REVIEW"


def test_waiver_exception_and_class4_exclusion():
    cleared = facts(
        is_singing_or_telepathic=True,
        cert_status="missing",
        waiver_status="valid",
        hazard_class="other",
        certificate_gross_weight_kg=None,
    )
    assert decide(cleared) == "CLEAR"
    excluded = facts(
        is_singing_or_telepathic=True,
        cert_status="expired",
        waiver_status="valid",
        hazard_class="class4_acoustic",
        manifest_gross_weight_kg="500",
        certificate_gross_weight_kg="500",
    )
    assert decide(excluded) == "REVIEW"


def test_missing_certificate_is_known_and_unknown_is_not():
    known_absent = facts(cert_status="missing", certificate_gross_weight_kg=None)
    assert "cert_status" not in missing_required(known_absent)
    assert decide(known_absent) == "CLEAR"
    unknown = facts(cert_status=None, certificate_gross_weight_kg=None)
    assert "cert_status" in missing_required(unknown)
    assert decide(unknown) == "REVIEW"


def test_expired_certificate_without_weight_is_review():
    sample = facts(cert_status="expired", certificate_gross_weight_kg=None)
    assert "certificate_gross_weight_kg" in missing_required(sample)
    assert decide(sample) == "REVIEW"


def test_singing_with_valid_certificate_does_not_require_waiver():
    sample = facts(is_singing_or_telepathic=True, cert_status="valid", waiver_status=None)
    assert "waiver_status" not in missing_required(sample)
    assert decide(sample) == "CLEAR"


def test_singing_without_valid_certificate_requires_known_waiver():
    unknown_waiver = facts(
        is_singing_or_telepathic=True,
        cert_status="expired",
        waiver_status=None,
        certificate_gross_weight_kg="1000",
    )
    assert decide(unknown_waiver) == "REVIEW"
    explicit_absent = facts(
        is_singing_or_telepathic=True,
        cert_status="missing",
        waiver_status="missing",
        certificate_gross_weight_kg=None,
    )
    assert decide(explicit_absent) == "REVIEW"


def test_conflict_is_review_unless_physical_hold():
    assert decide(facts(conflicted_fields=["manifest_gross_weight_kg"])) == "REVIEW"
    assert decide(facts(conflicted_fields=["seal_status"], seal_pressure_kpa="10")) == "HOLD"


def _changed_fields(left: Facts, right: Facts) -> list[str]:
    public_left = left.dump_public()
    public_right = right.dump_public()
    return [key for key in public_left if public_left[key] != public_right[key]]


def test_counterfactual_pairs_change_one_fact_and_the_spec_outcome():
    """Expected labels come from the published precedence, one changed fact at a time."""
    pairs = [
        ("seal_pressure_kpa", facts(seal_pressure_kpa="90"), "CLEAR", facts(seal_pressure_kpa="89.999"), "HOLD"),
        (
            "certificate_gross_weight_kg",
            facts(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030"),
            "CLEAR",
            facts(manifest_gross_weight_kg="1000", certificate_gross_weight_kg="1030.001"),
            "REVIEW",
        ),
        (
            "seal_status",
            facts(conflicted_fields=["hazard_class"]),
            "REVIEW",
            facts(conflicted_fields=["hazard_class"], seal_status="damaged"),
            "HOLD",
        ),
        (
            "cert_status",
            facts(cert_status="missing", certificate_gross_weight_kg=None),
            "CLEAR",
            facts(cert_status=None, certificate_gross_weight_kg=None),
            "REVIEW",
        ),
        (
            "conflicted_fields",
            facts(),
            "CLEAR",
            facts(conflicted_fields=["hazard_class"]),
            "REVIEW",
        ),
        (
            "waiver_status",
            facts(is_singing_or_telepathic=True, cert_status="missing", waiver_status="valid", certificate_gross_weight_kg=None),
            "CLEAR",
            facts(is_singing_or_telepathic=True, cert_status="missing", waiver_status="expired", certificate_gross_weight_kg=None),
            "REVIEW",
        ),
        (
            "hazard_class",
            facts(is_singing_or_telepathic=True, cert_status="missing", waiver_status="valid", certificate_gross_weight_kg=None),
            "CLEAR",
            facts(
                is_singing_or_telepathic=True,
                cert_status="missing",
                waiver_status="valid",
                hazard_class="class4_acoustic",
                certificate_gross_weight_kg=None,
            ),
            "REVIEW",
        ),
        (
            "seal_status",
            facts(is_singing_or_telepathic=True, cert_status="missing", waiver_status="valid", certificate_gross_weight_kg=None),
            "CLEAR",
            facts(
                is_singing_or_telepathic=True,
                cert_status="missing",
                waiver_status="valid",
                seal_status="damaged",
                certificate_gross_weight_kg=None,
            ),
            "HOLD",
        ),
        (
            "certificate_gross_weight_kg",
            facts(
                is_singing_or_telepathic=True,
                cert_status="expired",
                waiver_status="valid",
                manifest_gross_weight_kg="800",
                certificate_gross_weight_kg="800",
            ),
            "CLEAR",
            facts(
                is_singing_or_telepathic=True,
                cert_status="expired",
                waiver_status="valid",
                manifest_gross_weight_kg="800",
                certificate_gross_weight_kg="860",
            ),
            "REVIEW",
        ),
        (
            "seal_pressure_kpa",
            facts(is_singing_or_telepathic=None, seal_pressure_kpa="90"),
            "REVIEW",
            facts(is_singing_or_telepathic=None, seal_pressure_kpa="89.99"),
            "HOLD",
        ),
        (
            "conflicted_fields",
            facts(is_singing_or_telepathic=True, cert_status="missing", waiver_status="valid", certificate_gross_weight_kg=None),
            "CLEAR",
            facts(
                is_singing_or_telepathic=True,
                cert_status="missing",
                waiver_status="valid",
                certificate_gross_weight_kg=None,
                conflicted_fields=["manifest_gross_weight_kg"],
            ),
            "REVIEW",
        ),
        (
            "hazard_class",
            facts(),
            "CLEAR",
            facts(hazard_class=None),
            "REVIEW",
        ),
        (
            "is_singing_or_telepathic",
            facts(cert_status="expired"),
            "CLEAR",
            facts(cert_status="expired", is_singing_or_telepathic=True),
            "REVIEW",
        ),
    ]
    assert len(pairs) >= 10
    for field, left, left_label, right, right_label in pairs:
        assert decide(left) == left_label
        assert decide(right) == right_label
        assert _changed_fields(left, right) == [field]


def test_invalid_numbers_are_rejected():
    with pytest.raises(ValueError):
        facts(seal_pressure_kpa="-0.1")
    with pytest.raises(ValueError):
        facts(manifest_gross_weight_kg="0")
    with pytest.raises(ValueError):
        facts(certificate_gross_weight_kg="-5")
    with pytest.raises(ValueError):
        facts(seal_pressure_kpa=89.999)

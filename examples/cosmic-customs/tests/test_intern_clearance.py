"""Clearance metrics and rule precedence, specified independently.

Covers evaluation.summarize false-clearance and failure handling, and
rules.decide precedence through schemas.Facts. No network, no .env,
no prompts.py, no data/, results/, or live artifacts.
"""

from evaluation import summarize
from rules import decide
from schemas import DecisionResult, Facts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def record(case_id: str, verdict: str | None, status: str) -> DecisionResult:
    return DecisionResult.model_validate(
        {
            "case_id": case_id,
            "provider": "drex",
            "status": status,
            "verdict": verdict,
            "input_hash": "abc",
            "timestamp": "2026-11-14T20:00:00+00:00",
            "mode": "mock",
        }
    )


def facts(**overrides) -> Facts:
    """Ordinary intact-looking cargo facts."""
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


def waiver_case(**overrides) -> Facts:
    """Singing organism, missing certificate, valid waiver, hazard other."""
    base = {
        "is_singing_or_telepathic": True,
        "hazard_class": "other",
        "cert_status": "missing",
        "waiver_status": "valid",
        "seal_status": "intact",
        "seal_pressure_kpa": "101.325",
        "manifest_gross_weight_kg": "1000",
        "certificate_gross_weight_kg": None,
        "conflicted_fields": [],
    }
    base.update(overrides)
    return Facts.model_validate(base)


# ---------------------------------------------------------------------------
# 1. false clearance through evaluation.summarize
# ---------------------------------------------------------------------------


def test_gold_hold_predicted_clear_is_false_clearance():
    summary = summarize([record("A", "CLEAR", "ok")], {"A": "HOLD"}, planned=1)
    assert summary["false_clearance"] == {"numerator": 1, "denominator": 1}
    assert summary["confusion_matrix"]["HOLD"]["CLEAR"] == 1


def test_gold_review_predicted_clear_is_false_clearance():
    summary = summarize([record("A", "CLEAR", "ok")], {"A": "REVIEW"}, planned=1)
    assert summary["false_clearance"] == {"numerator": 1, "denominator": 1}
    assert summary["confusion_matrix"]["REVIEW"]["CLEAR"] == 1


def test_gold_clear_predicted_clear_is_not_false_clearance():
    summary = summarize([record("A", "CLEAR", "ok")], {"A": "CLEAR"}, planned=1)
    assert summary["false_clearance"] == {"numerator": 0, "denominator": 0}
    assert summary["confusion_matrix"]["CLEAR"]["CLEAR"] == 1


def test_failed_risky_gold_stays_in_denominator_not_prediction():
    gold = {"A": "HOLD", "B": "REVIEW", "C": "HOLD", "D": "REVIEW", "E": "REVIEW"}
    rows = [
        record("A", None, "error"),       # gold HOLD, failed
        record("B", None, "invalid"),     # gold REVIEW, failed
        record("C", None, "timeout"),     # gold HOLD, failed
        record("D", "CLEAR", "ok"),       # gold REVIEW, predicted CLEAR
        record("E", "CLEAR", "invalid"),  # gold REVIEW, CLEAR verdict but failed
    ]
    summary = summarize(rows, gold, planned=5)
    # Failed attempts stay in the false-clearance denominator.
    assert summary["false_clearance"]["denominator"] == 5
    # Only the successful CLEAR on risky gold counts.
    assert summary["false_clearance"]["numerator"] == 1
    matrix = summary["confusion_matrix"]
    # Failed attempts are FAILURE, not REVIEW or CLEAR predictions.
    assert matrix["HOLD"]["FAILURE"] == 2
    assert matrix["HOLD"]["CLEAR"] == 0
    assert matrix["HOLD"]["HOLD"] == 0
    assert matrix["REVIEW"]["FAILURE"] == 2
    assert matrix["REVIEW"]["REVIEW"] == 0
    assert matrix["REVIEW"]["CLEAR"] == 1


# ---------------------------------------------------------------------------
# 2. failed requests
# ---------------------------------------------------------------------------


def test_failed_statuses_in_accuracy_denominator_not_numerator():
    gold = {"A": "REVIEW", "B": "REVIEW", "C": "REVIEW", "D": "REVIEW"}
    rows = [
        record("A", None, "error"),
        record("B", None, "invalid"),
        record("C", None, "timeout"),
        record("D", "REVIEW", "ok"),
    ]
    summary = summarize(rows, gold, planned=4)
    # Failures count in the denominator, not the numerator.
    assert summary["accuracy"] == {"numerator": 1, "denominator": 4}
    # Failures are not successful REVIEW predictions; only the ok one is.
    assert summary["confusion_matrix"]["REVIEW"]["REVIEW"] == 1
    assert summary["confusion_matrix"]["REVIEW"]["FAILURE"] == 3
    assert summary["failure_rate"] == {"numerator": 3, "denominator": 4}


# ---------------------------------------------------------------------------
# 3. rule precedence through rules.decide and schemas.Facts
# ---------------------------------------------------------------------------


def test_damaged_seal_overrides_ordinary_intact_facts():
    assert decide(facts(seal_status="damaged")) == "HOLD"


def test_pressure_below_90_with_missing_organism_flag_is_hold():
    assert decide(facts(seal_pressure_kpa="89.99", is_singing_or_telepathic=None)) == "HOLD"


def test_pressure_exact_90_with_missing_organism_flag_is_review():
    assert decide(facts(seal_pressure_kpa="90", is_singing_or_telepathic=None)) == "REVIEW"


def test_pressure_exact_90_ordinary_missing_certificate_is_clear():
    assert (
        decide(
            facts(
                seal_pressure_kpa="90",
                seal_status="intact",
                is_singing_or_telepathic=False,
                cert_status="missing",
                certificate_gross_weight_kg=None,
            )
        )
        == "CLEAR"
    )


def test_singing_valid_cert_weight_within_3_percent_is_clear():
    sample = facts(is_singing_or_telepathic=True, manifest_gross_weight_kg="100", certificate_gross_weight_kg="103")
    assert decide(sample) == "CLEAR"


def test_singing_valid_cert_weight_over_3_percent_is_review():
    sample = facts(is_singing_or_telepathic=True, manifest_gross_weight_kg="100", certificate_gross_weight_kg="103.01")
    assert decide(sample) == "REVIEW"


def test_waiver_clears_singing_missing_certificate_other_hazard():
    assert decide(waiver_case()) == "CLEAR"


def test_waiver_does_not_clear_class4_acoustic():
    assert decide(waiver_case(hazard_class="class4_acoustic")) == "REVIEW"


def test_waiver_case_damaged_seal_is_hold():
    assert decide(waiver_case(seal_status="damaged")) == "HOLD"


def test_expired_certificate_valid_waiver_weight_over_3_percent_is_review():
    sample = facts(
        is_singing_or_telepathic=True,
        cert_status="expired",
        waiver_status="valid",
        manifest_gross_weight_kg="100",
        certificate_gross_weight_kg="110",
    )
    assert decide(sample) == "REVIEW"


def test_corrected_historical_suspicion_intact_seal_is_clear():
    assert decide(facts(seal_status="intact", seal_pressure_kpa="101")) == "CLEAR"

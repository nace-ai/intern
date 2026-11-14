"""Metric denominators, invalid responses, and baseline honesty."""

from decimal import Decimal

from evaluation import oracle_baseline, paired_speedup, project_cost, regex_baseline, savings_ratio, summarize
from rules import decide
from schemas import DecisionResult, Facts


def record(case_id: str, verdict: str | None, status: str, gold_cost: str | None = None, latency: float | None = 10) -> DecisionResult:
    return DecisionResult.model_validate(
        {
            "case_id": case_id,
            "provider": "drex",
            "status": status,
            "verdict": verdict,
            "latency_ms": latency,
            "cost_usd": gold_cost,
            "input_hash": "abc",
            "timestamp": "2026-11-14T20:00:00+00:00",
            "mode": "live",
        }
    )


def test_failure_is_not_a_review_prediction():
    gold = {"A": "REVIEW", "B": "HOLD"}
    rows = [
        record("A", None, "error"),
        record("B", "CLEAR", "ok"),
    ]
    summary = summarize(rows, gold, planned=2)
    assert summary["accuracy"] == {"numerator": 0, "denominator": 2}
    assert summary["false_clearance"] == {"numerator": 1, "denominator": 2}
    assert summary["confusion_matrix"]["REVIEW"]["FAILURE"] == 1
    assert summary["confusion_matrix"]["REVIEW"]["REVIEW"] == 0
    assert summary["failure_rate"] == {"numerator": 1, "denominator": 2}


def test_missing_cost_is_unknown_not_zero():
    summary = summarize([record("A", "CLEAR", "ok", None)], {"A": "CLEAR"}, planned=1)
    assert summary["cost_usd"]["known_total"] is None
    assert summary["cost_usd"]["unknown_count"] == 1
    assert project_cost(None, 1, 100, unknown=1)["projected_usd"] is None


def test_known_cost_projects_without_treating_unknown_as_zero():
    projected = project_cost(Decimal("0.50"), 5, 100, unknown=0)
    assert Decimal(projected["projected_usd"]) == Decimal("10")
    assert savings_ratio(Decimal("4"), Decimal("1"), complete=False) is None
    assert savings_ratio(Decimal("0"), Decimal("1"), complete=True) is None
    assert savings_ratio(Decimal("4"), Decimal("1"), complete=True) == Decimal("0.75")


def test_speedup_uses_only_matched_valid_pairs():
    left = [record("A", "CLEAR", "ok", latency=10), record("B", None, "timeout", latency=50)]
    right = [
        record("A", "CLEAR", "ok", latency=30),
        record("B", "CLEAR", "ok", latency=10),
    ]
    right[0].provider = "openrouter"
    right[1].provider = "openrouter"
    summary = paired_speedup(left, right)
    assert summary["matched_valid_pairs"] == 1
    assert summary["median_right_over_left"] == 3


def test_regex_baseline_is_fooled_by_a_corrected_suspicion():
    facts = Facts.model_validate(
        {
            "is_singing_or_telepathic": False,
            "hazard_class": "other",
            "cert_status": "valid",
            "waiver_status": None,
            "seal_status": "intact",
            "seal_pressure_kpa": "101",
            "manifest_gross_weight_kg": "1000",
            "certificate_gross_weight_kg": "1000",
            "conflicted_fields": [],
        }
    )
    text = "An earlier watch suspected a ruptured seal. That suspicion was corrected. The seal is intact. Seal pressure gauge reads 101 kPa."
    assert oracle_baseline(facts) == decide(facts) == "CLEAR"
    assert regex_baseline(text) == "HOLD"

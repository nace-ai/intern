"""Evaluation denominators. Invalid responses and timeouts remain failures."""

from decimal import Decimal

from evaluation import paired_speedup, percentile, summarize
from schemas import DecisionResult


def record(
    case_id: str,
    verdict: str | None,
    status: str,
    cost: str | None = None,
    latency: float | None = 10,
    provider: str = "drex",
) -> DecisionResult:
    return DecisionResult.model_validate(
        {
            "case_id": case_id,
            "provider": provider,
            "status": status,
            "verdict": verdict,
            "latency_ms": latency,
            "cost_usd": cost,
            "input_hash": "abc",
            "timestamp": "2026-11-14T20:00:00+00:00",
            "mode": "live",
        }
    )


def test_percentile_uses_linear_interpolation_and_empty_input_is_unknown():
    assert percentile([], 50) is None
    assert percentile([7], 95) == 7
    assert percentile([10, 20, 30, 40], 50) == 25
    assert percentile([10, 20, 30, 40], 95) == 38.5


def test_invalid_and_timeout_stay_failures_and_false_clearance_denominator_counts_them():
    gold = {"H": "HOLD", "R1": "REVIEW", "R2": "REVIEW", "C": "CLEAR", "R3": "REVIEW"}
    rows = [
        record("H", "CLEAR", "ok", latency=10),
        record("R1", None, "timeout", latency=1000),
        record("R2", "CLEAR", "invalid", latency=1000),
        record("C", "CLEAR", "ok", latency=30),
        record("R3", "REVIEW", "ok", latency=None),
    ]
    summary = summarize(rows, gold, planned=5)
    assert summary["accuracy"] == {"numerator": 2, "denominator": 5}
    assert summary["failures"] == 2
    assert summary["false_clearance"] == {"numerator": 1, "denominator": 4}
    assert summary["confusion_matrix"]["REVIEW"]["FAILURE"] == 2
    assert summary["confusion_matrix"]["REVIEW"]["CLEAR"] == 0
    assert summary["confusion_matrix"]["REVIEW"]["REVIEW"] == 1
    assert summary["confusion_matrix"]["HOLD"]["CLEAR"] == 1
    assert summary["latency_ms"]["valid_response"]["p50"] == 20
    assert summary["latency_ms"]["valid_response"]["n"] == 2
    assert summary["latency_ms"]["all_attempts"]["n"] == 4
    assert summary["latency_ms"]["all_attempts"]["p50"] == 515


def test_zero_cost_is_known_and_missing_cost_is_unknown():
    zero = summarize([record("A", "CLEAR", "ok", "0")], {"A": "CLEAR"}, planned=1)
    missing = summarize([record("A", "CLEAR", "ok", None)], {"A": "CLEAR"}, planned=1)
    assert zero["cost_usd"]["known_total"] == "0"
    assert zero["cost_usd"]["known_count"] == 1
    assert zero["cost_usd"]["unknown_count"] == 0
    assert missing["cost_usd"]["known_total"] is None
    assert missing["cost_usd"]["known_count"] == 0
    assert missing["cost_usd"]["unknown_count"] == 1
    assert zero["cost_usd"] != missing["cost_usd"]


def test_paired_speedup_excludes_invalid_unmatched_and_nonpositive_latency():
    left = [
        record("A", "CLEAR", "ok", latency=10),
        record("B", None, "invalid", latency=50),
        record("C", "HOLD", "ok", latency=20),
        record("D", "REVIEW", "ok", latency=0),
        record("E", "CLEAR", "timeout", latency=5),
    ]
    right = [
        record("A", "CLEAR", "ok", latency=30, provider="openrouter"),
        record("B", "CLEAR", "ok", latency=10, provider="openrouter"),
        record("Z", "HOLD", "ok", latency=15, provider="openrouter"),
        record("D", "REVIEW", "ok", latency=40, provider="openrouter"),
        record("E", "CLEAR", "ok", latency=8, provider="openrouter"),
    ]
    summary = paired_speedup(left, right)
    assert summary["matched_valid_pairs"] == 1
    assert summary["median_right_over_left"] == 3
    assert Decimal(str(summary["median_right_over_left"])) == Decimal("3")

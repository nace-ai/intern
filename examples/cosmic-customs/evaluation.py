"""Score attempts. Failures are not successful predictions."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Any

from rules import decide
from schemas import DecisionResult, Facts

VERDICTS = ("CLEAR", "HOLD", "REVIEW")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def _gold_for(record: DecisionResult, gold: dict[str, str]) -> str:
    return gold[record.case_id]


def summarize(records: list[DecisionResult], gold: dict[str, str], planned: int) -> dict[str, Any]:
    attempted = len(records)
    completed = [record for record in records if record.status == "ok" and record.verdict in VERDICTS]
    failures = [record for record in records if record.status in ("invalid", "error", "timeout")]
    correct = [
        record
        for record in completed
        if record.verdict == _gold_for(record, gold)
    ]
    risky_gold = [record for record in records if _gold_for(record, gold) in ("HOLD", "REVIEW")]
    false_clear = [
        record
        for record in risky_gold
        if record.status == "ok" and record.verdict == "CLEAR"
    ]
    matrix = {gold_label: {pred: 0 for pred in (*VERDICTS, "FAILURE")} for gold_label in VERDICTS}
    for record in records:
        label = _gold_for(record, gold)
        if record.status == "ok" and record.verdict in VERDICTS:
            matrix[label][record.verdict] += 1
        else:
            matrix[label]["FAILURE"] += 1
    known_costs = [record.cost_usd for record in records if record.cost_usd is not None]
    unknown_cost = sum(1 for record in records if record.cost_usd is None)
    valid_latency = [record.latency_ms for record in completed if record.latency_ms is not None]
    all_latency = [record.latency_ms for record in records if record.latency_ms is not None]
    return {
        "planned": planned,
        "attempted": attempted,
        "completed": len(completed),
        "failures": len(failures),
        "accuracy": {
            "numerator": len(correct),
            "denominator": attempted,
        },
        "false_clearance": {
            "numerator": len(false_clear),
            "denominator": len(risky_gold),
        },
        "failure_rate": {
            "numerator": len(failures),
            "denominator": attempted,
        },
        "confusion_matrix": matrix,
        "latency_ms": {
            "valid_response": {
                "p50": percentile([float(item) for item in valid_latency], 50),
                "p95": percentile([float(item) for item in valid_latency], 95),
                "n": len(valid_latency),
            },
            "all_attempts": {
                "p50": percentile([float(item) for item in all_latency], 50),
                "p95": percentile([float(item) for item in all_latency], 95),
                "n": len(all_latency),
            },
        },
        "cost_usd": {
            "known_total": format(sum(known_costs, Decimal("0")), "f") if known_costs else None,
            "known_count": len(known_costs),
            "unknown_count": unknown_cost,
        },
    }


def paired_speedup(left: list[DecisionResult], right: list[DecisionResult]) -> dict[str, Any]:
    """Speedup is right latency / left latency on matched valid cases. >1 means left is faster."""
    right_by_case = {record.case_id: record for record in right if record.status == "ok" and record.latency_ms is not None}
    ratios: list[float] = []
    for record in left:
        match = right_by_case.get(record.case_id)
        if record.status != "ok" or record.latency_ms is None or match is None:
            continue
        if record.latency_ms <= 0 or match.latency_ms <= 0:
            continue
        ratios.append(float(match.latency_ms) / float(record.latency_ms))
    return {
        "matched_valid_pairs": len(ratios),
        "median_right_over_left": median(ratios) if ratios else None,
        "definition": "median(openrouter_latency_ms / drex_latency_ms) when left is Drex and right is OpenRouter",
    }


def savings_ratio(baseline_cost: Decimal | None, comparison_cost: Decimal | None, complete: bool) -> Decimal | None:
    if not complete or baseline_cost is None or comparison_cost is None:
        return None
    if baseline_cost <= 0:
        return None
    return (baseline_cost - comparison_cost) / baseline_cost


def project_cost(pilot_known_total: Decimal | None, pilot_cases: int, full_cases: int, unknown: int) -> dict[str, Any]:
    if pilot_known_total is None or unknown or pilot_cases <= 0:
        return {"projected_usd": None, "reason": "pilot cost is incomplete"}
    per_case = pilot_known_total / Decimal(pilot_cases)
    return {
        "projected_usd": format(per_case * Decimal(full_cases), "f"),
        "per_case_usd": format(per_case, "f"),
        "reason": "linear projection from known pilot charges; not a measurement of the full run",
    }


def oracle_baseline(facts: Facts) -> str:
    """Privileged reference. It reads gold facts, not the packet text."""
    return decide(facts)


def regex_baseline(text: str) -> str:
    """Deliberately simple keyword scan. It is not evidence of general reasoning."""
    import re

    if re.search(
        r"(damaged|ruptured|compromised).{0,48}seal|seal.{0,48}(damaged|ruptured|compromised)",
        text,
        re.IGNORECASE,
    ):
        return "HOLD"
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*kPa", text, re.IGNORECASE):
        if Decimal(match.group(1)) < Decimal("90"):
            return "HOLD"
    if re.search(r"not on file", text, re.IGNORECASE) and re.search(r"singing|telepath", text, re.IGNORECASE):
        return "REVIEW"
    if re.search(r"contradict|two readings|do not agree", text, re.IGNORECASE):
        return "REVIEW"
    return "CLEAR"

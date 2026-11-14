"""Intern integrity checks that do not depend on existing tests or network access."""

import json
from pathlib import Path

from evaluation import summarize
from prompts import assert_model_input_allowlist, shared_input_for_case
from schemas import DecisionResult

ROOT = Path(__file__).resolve().parents[1]


def _error_result() -> DecisionResult:
    return DecisionResult.model_validate(
        {
            "case_id": "A",
            "provider": "drex",
            "requested_model": None,
            "actual_model": None,
            "verdict": None,
            "probabilities": None,
            "status": "error",
            "latency_ms": None,
            "server_latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "cost_source": None,
            "request_id": None,
            "input_hash": "abc",
            "timestamp": "2026-11-14T20:00:00+00:00",
            "mode": "live",
            "cached": None,
            "error": "boom",
        }
    )


def test_model_input_excludes_hidden_fields():
    dev = json.loads((ROOT / "data" / "dev_20.json").read_text(encoding="utf-8"))
    case = dev[0]
    rendered = shared_input_for_case(case)
    assert_model_input_allowlist(case, rendered)
    for hidden in ("expected_verdict", "source_annotations", "rule_signature", "template_family"):
        assert hidden not in rendered


def test_failed_attempt_is_not_counted_as_review():
    result = _error_result()
    summary = summarize([result], {"A": "REVIEW"}, planned=1)
    assert summary["accuracy"]["numerator"] == 0
    assert summary["accuracy"]["denominator"] == 1
    assert summary["confusion_matrix"]["REVIEW"]["FAILURE"] == 1
    assert summary["confusion_matrix"]["REVIEW"]["REVIEW"] == 0


def test_missing_cost_stays_unknown():
    result = _error_result()
    summary = summarize([result], {"A": "REVIEW"}, planned=1)
    assert summary["cost_usd"]["unknown_count"] == 1
    assert summary["cost_usd"]["known_total"] is None

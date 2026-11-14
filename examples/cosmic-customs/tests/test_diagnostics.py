"""Read-only diagnostics over saved runs and small fixtures."""

import json
from pathlib import Path

from diagnostics import diagnose, format_report
from schemas import DecisionResult

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "results" / "live" / "decisions" / "latest.json"


def _row(case_id: str, provider: str, verdict: str | None, status: str, latency: float | None, cost: str | None) -> dict:
    return DecisionResult.model_validate(
        {
            "case_id": case_id,
            "provider": provider,
            "status": status,
            "verdict": verdict,
            "latency_ms": latency,
            "cost_usd": cost,
            "input_hash": "abc",
            "timestamp": "2026-11-14T00:00:00+00:00",
            "mode": "mock",
        }
    ).dump_public()


def test_diagnostic_counts_failures_false_clearances_and_known_zero(tmp_path):
    held_out = json.loads((ROOT / "data" / "test_100.json").read_text(encoding="utf-8"))
    review = next(case for case in held_out if case["case_id"] == "TST-021")
    hold = next(case for case in held_out if case["expected_verdict"] == "HOLD")
    clear = next(case for case in held_out if case["expected_verdict"] == "CLEAR")
    records = [
        _row(review["case_id"], "drex", "CLEAR", "ok", 10, None),
        _row(hold["case_id"], "drex", None, "timeout", 1000, None),
        _row(clear["case_id"], "drex", "CLEAR", "ok", 30, "0"),
        _row(review["case_id"], "openrouter", "REVIEW", "ok", 40, "1.5"),
        _row(hold["case_id"], "openrouter", "HOLD", "ok", 40, "1.5"),
        _row(clear["case_id"], "openrouter", None, "invalid", 5, None),
    ]
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": "fixture",
                "mode": "mock",
                "measurements": "simulated",
                "split": "test",
                "planned": 3,
            }
        ),
        encoding="utf-8",
    )
    (run / "records.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    before = (run / "records.jsonl").read_bytes()
    report = diagnose(run)
    assert (run / "records.jsonl").read_bytes() == before
    assert report["simulated"] is True
    assert report["banner"][0].startswith("SIMULATED")
    drex = report["providers"]["drex"]
    router = report["providers"]["openrouter"]
    assert drex["accuracy"] == {"numerator": 1, "denominator": 3}
    assert drex["false_clearance"] == {"numerator": 1, "denominator": 2}
    assert drex["false_clearance_cases"][0]["case_id"] == "TST-021"
    assert drex["false_clearance_cases"][0]["rule_signature"] == review["rule_signature"]
    assert drex["by_signature"][review["rule_signature"]]["false_clearance"] == 1
    assert drex["cost"]["state"] == "known_zero"
    assert drex["cost"]["known_total"] == "0"
    assert drex["cost"]["unknown_count"] == 2
    assert router["false_clearance"]["numerator"] == 0
    assert router["cost"]["state"] == "known"
    assert router["cost"]["unknown_count"] == 1
    assert report["speedup"]["matched_valid_pairs"] == 1
    assert report["speedup"]["median_right_over_left"] == 4
    assert report["latency_pairs_excluded"] == 2
    verdicts = [item for item in report["disagreements"] if item["kind"] == "verdict"]
    assert [item["case_id"] for item in verdicts] == [review["case_id"]]
    text = format_report(report)
    assert "SIMULATED" in text
    assert "known_zero" in text
    assert "unknown" in text
    assert "not measured model performance" in text


def test_hard_case_fixture_is_labeled_simulated(tmp_path):
    hard = json.loads((ROOT / "data" / "hard_cases.json").read_text(encoding="utf-8"))
    first, second = hard[0], hard[1]
    records = [
        _row(first["case_id"], "drex", "HOLD", "ok", None, None),
        _row(second["case_id"], "drex", "CLEAR", "ok", None, None),
        _row(first["case_id"], "openrouter", first["expected_verdict"], "ok", None, None),
        _row(second["case_id"], "openrouter", "CLEAR", "ok", None, None),
    ]
    run = tmp_path / "hard"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps({"run_id": "hard-fixture", "mode": "mock", "measurements": "simulated", "split": "hard", "dataset": "hard_cases", "planned": 2}),
        encoding="utf-8",
    )
    (run / "records.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    report = diagnose(run)
    text = format_report(report)
    assert report["simulated"] is True
    assert any("hard-case" in line.lower() for line in report["banner"])
    assert "not" in text.lower() and "model performance" in text.lower()
    assert report["speedup"]["matched_valid_pairs"] == 0
    assert report["latency_pairs_excluded"] == 2


def test_saved_held_out_diagnostic_matches_published_figures():
    if not LIVE.is_file():
        return
    before = LIVE.read_bytes()
    report = diagnose(LIVE)
    assert LIVE.read_bytes() == before
    assert report["simulated"] is False
    assert report["banner"][0].startswith("MEASURED")
    assert report["split"] == "test"
    assert report["providers"]["drex"]["accuracy"] == {"numerator": 89, "denominator": 100}
    assert report["providers"]["drex"]["false_clearance"] == {"numerator": 8, "denominator": 67}
    assert report["providers"]["openrouter"]["accuracy"] == {"numerator": 95, "denominator": 100}
    assert report["providers"]["openrouter"]["false_clearance"] == {"numerator": 3, "denominator": 67}
    assert report["providers"]["drex"]["by_signature"]["review_conflict_overrides_waiver"]["correct"] == 0
    assert report["providers"]["drex"]["by_signature"]["review_conflict_overrides_waiver"]["false_clearance"] == 8
    assert report["providers"]["openrouter"]["by_signature"]["review_missing_required"]["false_clearance"] == 3
    assert report["speedup"]["matched_valid_pairs"] == 100
    assert round(report["speedup"]["median_right_over_left"], 2) == 3.78
    assert report["providers"]["drex"]["cost"]["state"] == "known"
    assert report["providers"]["drex"]["cost"]["unknown_count"] == 0
    assert report["providers"]["openrouter"]["cost"]["unknown_count"] == 0
    assert report["saved_summary_agrees"] is True
    assert report["latency_pairs_excluded"] == 0

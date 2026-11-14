"""Deterministic checks for the report math that is easy to get wrong."""

import json

import pytest

from billproof.bench.metrics import brier, build_report, ece, mcnemar, threshold_sweep, wilson_ci


def _row(case, lane, correct, confs, kinds, routes, latency, cost):
    names = ["dup_L1", "eob_mismatch"]
    return {
        "case_id": case,
        "lane": lane,
        "latency_ms": latency,
        "cost_usd": cost,
        "case_correct": all(correct),
        "gold": {name: "yes" for name in names},
        "correct": {name: flag for name, flag in zip(names, correct)},
        "answers": {
            name: {
                "choice": "yes" if flag else "no",
                "confidence": conf,
                "confidence_kind": kind,
                "probabilities": None,
                "route": route,
            }
            for name, flag, conf, kind, route in zip(names, correct, confs, kinds, routes)
        },
    }


def _six_rows():
    native = ("native", "native")
    reported = ("self_reported", "self_reported")
    kept = ("drex_kept", "drex_kept")
    return [
        _row("C1", "drex", [True, False], [0.96, 0.40], native, kept, 100, 1.0),
        _row("C2", "drex", [True, True], [0.92, 0.91], native, kept, 300, 1.0),
        _row("C1", "frontier", [True, True], [0.80, 0.80], reported, ("frontier_only",) * 2, 400, 4.0),
        _row("C2", "frontier", [True, True], [0.60, 0.60], reported, ("frontier_only",) * 2, 400, 4.0),
        _row("C1", "hybrid", [True, True], [0.96, 0.70], ("native", "self_reported"), ("drex_kept", "frontier_verified"), 100, 1.0),
        _row("C2", "hybrid", [True, True], [0.92, 0.91], native, kept, 100, 1.0),
    ]


def test_wilson_known_interval():
    lo, hi = wilson_ci(81, 100)
    assert lo == pytest.approx(0.72213, abs=1e-4)
    assert hi == pytest.approx(0.87485, abs=1e-4)
    assert wilson_ci(0, 0) == (None, None)
    zlo, _zhi = wilson_ci(0, 10)
    assert zlo == 0.0


def test_ece_equal_width_and_brier():
    score, bins = ece([0.9, 0.9], [0, 0])
    assert score == pytest.approx(0.9)
    assert len(bins) == 1
    assert bins[0]["lo"] == pytest.approx(0.9)
    assert bins[0]["hi"] == pytest.approx(1.0)
    assert bins[0]["conf"] == pytest.approx(0.9)
    assert bins[0]["acc"] == pytest.approx(0.0)
    assert bins[0]["n"] == 2
    assert brier([1, 0, 0.5], [1, 0, 1]) == pytest.approx(0.25 / 3)
    assert brier([{"yes": 0.8, "no": 0.2}], ["yes"]) == pytest.approx(0.08)


def test_mcnemar_exact_then_chi2():
    exact = mcnemar([True] * 6 + [False], [False] * 6 + [True])
    assert exact["b"] == 6
    assert exact["c"] == 1
    assert exact["p_value"] == pytest.approx(0.125)
    large = mcnemar([True] * 20 + [False] * 5, [False] * 20 + [True] * 5)
    assert (large["b"], large["c"]) == (20, 5)
    assert large["stat"] == pytest.approx(14**2 / 25)
    assert large["p_value"] == pytest.approx(0.005110, abs=1e-4)


def test_build_report_six_rows():
    report = build_report(
        _six_rows(),
        {"bench": "synthetic", "mode": "SIMULATED", "ni_margin_pts": 3},
    )
    drex = report["lanes"]["drex"]
    hybrid = report["lanes"]["hybrid"]
    frontier = report["lanes"]["frontier"]

    assert drex["n"] == 2
    assert drex["n_questions"] == 4
    assert drex["accuracy"] == pytest.approx(0.75)
    assert drex["ci"] == [pytest.approx(wilson_ci(3, 4)[0]), pytest.approx(wilson_ci(3, 4)[1])]
    assert drex["case_pass"] == pytest.approx(0.5)
    assert drex["by_family"]["dup"] == {"acc": pytest.approx(1.0), "n": 2}
    assert drex["by_family"]["eob_mismatch"]["acc"] == pytest.approx(0.5)
    assert drex["p50_ms"] == pytest.approx(200)
    assert drex["p95_ms"] == pytest.approx(290)
    assert drex["ece"] == pytest.approx(0.1525)
    assert drex["brier"] == pytest.approx(0.044025)
    assert drex["escalation_rate"] == 0.0

    # Escalated hybrid answer is self-reported and stays out of native ECE.
    assert hybrid["accuracy"] == pytest.approx(1.0)
    assert hybrid["case_pass"] == pytest.approx(1.0)
    assert hybrid["escalation_rate"] == pytest.approx(0.25)
    assert hybrid["ece"] == pytest.approx(0.07)
    assert hybrid["brier"] == pytest.approx(0.0161 / 3)
    assert hybrid["self_reported_ece"] is None

    assert frontier["accuracy"] == pytest.approx(1.0)
    assert frontier["ece"] is None
    assert frontier["brier"] is None
    assert frontier["self_reported_ece"] == pytest.approx(0.3)
    assert report["reliability"]["frontier_self_reported"]
    assert report["reliability"]["frontier"][0]["n"] == 2

    low = report["sweep"][0]
    high = report["sweep"][-1]
    assert len(report["sweep"]) == 50
    assert low["threshold"] == pytest.approx(0.50)
    assert high["threshold"] == pytest.approx(0.99)
    assert low["automation_rate"] == pytest.approx(0.75)
    assert low["error_automated"] == pytest.approx(0.0)
    assert low["accuracy"] == pytest.approx(1.0)
    assert low["cost_per_case"] == pytest.approx(3.0)
    assert low["assumed_human"] is False
    assert high["automation_rate"] == pytest.approx(0.0)
    assert high["error_automated"] is None
    assert high["accuracy"] == pytest.approx(1.0)
    assert high["cost_per_case"] == pytest.approx(5.0)

    assert report["mcnemar"]["b"] == 0
    assert report["mcnemar"]["c"] == 0
    assert report["mcnemar"]["p_value"] == pytest.approx(1.0)
    assert report["noninferior"] is True
    assert report["ni_margin_pts"] == 3
    assert report["headline"] == (
        "Drex+Frontier within 0.0 pts of Frontier "
        "(95% CI hybrid-frontier +0.0 to +0.0 pts) at 25.0% of the cost, 4.0× faster"
    )
    assert "N = 2 cases, 4 questions." in report["honesty"]
    assert "synthetic" in report["honesty"]
    assert "SIMULATED" in report["honesty"]
    assert any("upper bound" in line.lower() for line in report["honesty"])
    json.dumps(report)

    flagged = threshold_sweep([row for row in _six_rows() if row["lane"] == "drex"])
    assert flagged[0]["assumed_human"] is True
    assert flagged[0]["accuracy"] == pytest.approx(1.0)

    sparse = build_report(
        [{"nope": True}, {"case_id": "X", "lane": "drex", "latency_ms": "fast"}],
        {"mode": "SIMULATED", "bench": "synthetic"},
    )
    assert sparse["lanes"]["drex"]["accuracy"] is None
    assert sparse["lanes"]["drex"]["n"] == 1
    assert sparse["noninferior"] is False

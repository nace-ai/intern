"""Trust-dial simulation from recorded answers. No model calls.

Rule. For a threshold t, Drex keeps every question whose recorded native
confidence is >= t. Every other question takes GPT-6.1 Sol's recorded answer
to that same question. The chart metric is rescored on the mixed answers.

Cost per 1,000 cases (estimate, list price):
    per case = Drex recorded cost_usd
             + if r > 0 routed questions out of n:
                 (gpt_input_tokens * $2 + gpt_output_tokens * (r / n) * $10) / 1e6
    cost_per_1000 = mean(per case) * 1000
GPT input tokens are the recorded packed call's input, charged once per routed
case. Output tokens are the recorded packed output scaled by the routed share.

Median latency (estimate):
    per case = Drex recorded latency_ms + (if r > 0) GPT recorded latency_ms * (r / n)
GPT runs after Drex. GPT time is scaled by the routed share, because a packed
call's time grows with the answers it writes. Prompt reading time is not
modelled, so short GPT calls are under-estimated (see the measured gap on hard).

Every point except `measured` is labelled "simulated from recorded answers".
"""

from __future__ import annotations

import json
import statistics
from functools import lru_cache
from typing import Callable

from billproof.config import DATA_DIR
from billproof.dial import loaders

METRICS = DATA_DIR / "metrics"
GPT_IN_PER_MTOK = 2.0
GPT_OUT_PER_MTOK = 10.0
SIMULATED = "simulated from recorded answers"
MEASURED = "measured"
THRESHOLDS = [round(0.50 + step / 100, 2) for step in range(51)]
LINE_FAMILIES = ("dup", "mue", "ncci", "fee", "eob", "pr")

BENCHES = {
    "hard": {
        "title": "BillProof-Hard (frozen 100)",
        "chart": "line_verdict",
        "metrics": ["line_verdict", "question_accuracy"],
    },
    "pa_criteria_gym": {
        "title": "Prior authorization, criteria-gym (370)",
        "chart": "whole_case",
        "metrics": ["whole_case", "decision"],
    },
    "persius": {
        "title": "Persius real appeals (400)",
        "chart": "accuracy",
        "metrics": ["accuracy"],
    },
    "synthetic_v1": {
        "title": "Synthetic v1 bills (115)",
        "chart": "question_accuracy",
        "metrics": ["question_accuracy"],
    },
}

FORMULAS = {
    "rule": "Drex keeps every question with recorded confidence >= threshold. The rest take GPT-6.1 Sol's recorded answer to the same question.",
    "cost_per_1000_usd": (
        "mean over cases of [Drex recorded cost_usd + (if any question routed) "
        "(GPT recorded input_tokens * $2 + GPT recorded output_tokens * routed/asked * $10) / 1e6] * 1000. "
        "GPT list price $2 in / $10 out per 1M tokens."
    ),
    "latency_p50_ms": (
        "median over cases of [Drex recorded latency_ms + (if any question routed) "
        "GPT recorded latency_ms * routed/asked]. GPT runs after Drex."
    ),
    "gpt_only": "GPT-6.1 Sol recorded answers on every question. Cost is GPT list price from recorded tokens. No Drex cost.",
    "drex_only": "drex-max-v1.1 recorded answers on every question. Cost is Drex recorded cost_usd.",
}


def _rate(k: int, n: int) -> dict:
    return {"value": (k / n) if n else None, "k": k, "n": n}


@lru_cache(maxsize=8)
def cases_for(bench: str) -> list[dict]:
    if bench not in loaders.LOADERS:
        raise KeyError(bench)
    return loaders.LOADERS[bench]()


# ------------------------------------------------------------------ scoring


def _line_verdict(choices: dict[str, str | None]) -> str | None:
    from billproof.bench.hard.outcome_score import line_verdict

    return line_verdict(choices)


def _hard_lines(case: dict) -> list[str]:
    numbers = []
    for question in case["questions"]:
        head, sep, tail = question["id"].partition("_L")
        if sep and head == "dup" and tail.isdigit():
            numbers.append(tail)
    return numbers


def _score(bench: str, cases: list[dict], picks: list[list[bool]]) -> dict:
    """picks[i][j] is True when question j of case i goes to GPT."""
    q_k = q_n = 0
    out: dict = {}
    if bench == "hard":
        line_k = line_n = 0
        for case, routed in zip(cases, picks):
            chosen = {}
            gold = {}
            for question, to_gpt in zip(case["questions"], routed):
                chosen[question["id"]] = question["gpt_choice"] if to_gpt else question["drex_choice"]
                gold[question["id"]] = question["gold"]
                q_n += 1
                q_k += int((question["gpt_ok"] if to_gpt else question["drex_ok"]))
            for number in _hard_lines(case):
                model = _line_verdict({f: chosen.get(f"{f}_L{number}") for f in LINE_FAMILIES})
                truth = _line_verdict({f: gold.get(f"{f}_L{number}") for f in LINE_FAMILIES})
                line_n += 1
                line_k += int(model is not None and model == truth)
        out["line_verdict"] = _rate(line_k, line_n)
        out["question_accuracy"] = _rate(q_k, q_n)
    elif bench == "pa_criteria_gym":
        whole_k = dec_k = 0
        for case, routed in zip(cases, picks):
            any_routed = any(routed)
            parts_ok = all(
                (q["gpt_ok"] if to_gpt else q["drex_ok"]) for q, to_gpt in zip(case["questions"], routed)
            )
            # A case with any routed question takes GPT's case-level fields (decision,
            # action, criteria, evidence, missing info, policy id). Otherwise Drex's.
            rest = case["case"]["gpt_rest"] if any_routed else case["case"]["drex_rest"]
            decision = case["case"]["gpt_decision"] if any_routed else case["case"]["drex_decision"]
            whole_k += int(parts_ok and rest and decision)
            dec_k += int(decision)
        out["whole_case"] = _rate(whole_k, len(cases))
        out["decision"] = _rate(dec_k, len(cases))
    else:
        for case, routed in zip(cases, picks):
            for question, to_gpt in zip(case["questions"], routed):
                q_n += 1
                q_k += int(question["gpt_ok"] if to_gpt else question["drex_ok"])
        out["accuracy" if bench == "persius" else "question_accuracy"] = _rate(q_k, q_n)
    return out


# ------------------------------------------------------------------ routing


def global_router(threshold: float) -> Callable[[dict], bool]:
    return lambda question: question["conf"] < threshold


def family_router(threshold: float, taus: dict) -> Callable[[dict], bool]:
    """The frozen map: families with tau 1.0 always go to GPT; the rest use the dial."""
    always = {family for family, tau in (taus.get("taus") or {}).items() if float(tau) >= 1.0}
    return lambda question: question["family"] in always or question["conf"] < threshold


def _gpt_case_cost(case: dict, routed: int, asked: int) -> float:
    if routed <= 0 or asked <= 0:
        return 0.0
    return (
        case["gpt_input_tokens"] * GPT_IN_PER_MTOK
        + case["gpt_output_tokens"] * (routed / asked) * GPT_OUT_PER_MTOK
    ) / 1_000_000


def simulate(bench: str, router: Callable[[dict], bool], *, threshold: float | None, with_ids: bool = False) -> dict:
    cases = cases_for(bench)
    picks = [[bool(router(q)) for q in case["questions"]] for case in cases]
    q_routed = sum(sum(row) for row in picks)
    q_total = sum(len(row) for row in picks)
    c_routed = sum(1 for row in picks if any(row))
    drex_costs, gpt_costs, latencies = [], [], []
    for case, row in zip(cases, picks):
        routed, asked = sum(row), len(row)
        drex_costs.append(case["drex_cost_usd"])
        gpt_costs.append(_gpt_case_cost(case, routed, asked))
        latencies.append(case["drex_ms"] + (case["gpt_ms"] * routed / asked if routed and asked else 0.0))
    metrics = _score(bench, cases, picks)
    n = len(cases)
    point = {
        "threshold": threshold,
        "label": SIMULATED,
        "value": metrics[BENCHES[bench]["chart"]]["value"],
        "metrics": metrics,
        "questions_routed": {"k": q_routed, "n": q_total, "share": (q_routed / q_total) if q_total else None},
        "cases_routed": {"k": c_routed, "n": n, "share": (c_routed / n) if n else None},
        "cost_per_1000_usd": (sum(drex_costs) + sum(gpt_costs)) / n * 1000 if n else None,
        "cost_parts_per_1000_usd": {
            "drex": sum(drex_costs) / n * 1000 if n else None,
            "gpt": sum(gpt_costs) / n * 1000 if n else None,
        },
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
    }
    if with_ids:
        point["routed_by_case"] = [
            {
                "case_id": case["case_id"],
                "asked": len(row),
                "routed": [q["id"] for q, to_gpt in zip(case["questions"], row) if to_gpt],
            }
            for case, row in zip(cases, picks)
        ]
    return point


def gpt_only(bench: str) -> dict:
    cases = cases_for(bench)
    picks = [[True] * len(case["questions"]) for case in cases]
    n = len(cases)
    metrics = _score(bench, cases, picks)
    costs = [
        (c["gpt_input_tokens"] * GPT_IN_PER_MTOK + c["gpt_output_tokens"] * GPT_OUT_PER_MTOK) / 1_000_000
        for c in cases
    ]
    return {
        "label": SIMULATED,
        "lane": "GPT-6.1 Sol",
        "model": loaders.GPT_MODEL,
        "value": metrics[BENCHES[bench]["chart"]]["value"],
        "metrics": metrics,
        "cost_per_1000_usd": sum(costs) / n * 1000,
        "latency_p50_ms": statistics.median(c["gpt_ms"] for c in cases),
        "note": FORMULAS["gpt_only"],
    }


def drex_only(bench: str) -> dict:
    cases = cases_for(bench)
    picks = [[False] * len(case["questions"]) for case in cases]
    n = len(cases)
    metrics = _score(bench, cases, picks)
    return {
        "label": SIMULATED,
        "lane": "Drex v1.1",
        "model": loaders.DREX_MODEL,
        "value": metrics[BENCHES[bench]["chart"]]["value"],
        "metrics": metrics,
        "cost_per_1000_usd": sum(c["drex_cost_usd"] for c in cases) / n * 1000,
        "latency_p50_ms": statistics.median(c["drex_ms"] for c in cases),
        "note": FORMULAS["drex_only"],
    }


# ------------------------------------------------------------------ hard measured


def _read_metric(name: str) -> dict | None:
    path = METRICS / name
    return json.loads(path.read_text()) if path.exists() else None


def hard_measured() -> dict | None:
    """The recorded per-family hybrid run (fresh GPT call on escalated questions)."""
    rows = loaders.load_hard_measured()
    if not rows:
        return None
    line = _read_metric("hard_outcome_hybrid_line_verdict.json") or {}
    question = _read_metric("hard_outcome_hybrid_question_accuracy.json") or {}
    escalated = sum(int(row.get("n_escalated") or 0) for row in rows)
    gpt_wrong = sum(
        1
        for row in rows
        for name, answer in (row.get("answers") or {}).items()
        if (answer or {}).get("route") == "frontier" and not (row.get("correct") or {}).get(name)
    )
    asked = sum(int(row.get("n_questions") or 0) for row in rows)
    gpt_cost = sum(float(row.get("list_cost_usd", row.get("cost_usd")) or 0.0) for row in rows) / len(rows) * 1000
    drex = {c["case_id"]: c["drex_cost_usd"] for c in cases_for("hard")}
    drex_cost = sum(drex.get(row["case_id"], 0.0) for row in rows) / len(rows) * 1000
    taus = loaders.hard_family_taus()
    return {
        "label": MEASURED,
        "lane": "Drex-first hybrid (per-family map), measured",
        "threshold": float(taus.get("else_tau", 0.95)),
        "router": "family",
        "value": line.get("value"),
        "metrics": {
            "line_verdict": {"value": line.get("value"), "k": line.get("k"), "n": line.get("n")},
            "question_accuracy": {"value": question.get("value"), "k": question.get("k"), "n": question.get("n")},
        },
        "questions_routed": {"k": escalated, "n": asked, "share": escalated / asked if asked else None},
        "cases_routed": {
            "k": sum(1 for row in rows if row.get("n_escalated")),
            "n": len(rows),
            "share": sum(1 for row in rows if row.get("n_escalated")) / len(rows),
        },
        "gpt_wrong_on_escalated": gpt_wrong,
        "cost_per_1000_usd": gpt_cost,
        "cost_basis": "recorded list_cost_usd of the GPT escalation call only (as published, $62.26). Drex is not included.",
        "cost_parts_per_1000_usd": {"drex": drex_cost, "gpt": gpt_cost},
        "cost_per_1000_usd_with_drex": gpt_cost + drex_cost,
        "latency_p50_ms": statistics.median(float(row["latency_ms"]) for row in rows),
        "escalated_ids": {row["case_id"]: list(row.get("escalated") or []) for row in rows},
        "sources": [
            "data/runs/hard_test_family_hybrid.jsonl",
            "data/metrics/hard_outcome_hybrid_line_verdict.json",
            "data/metrics/hard_outcome_hybrid_question_accuracy.json",
        ],
    }


def _hard_compare(measured: dict, simulated: dict) -> dict:
    """Simulated family map at the same cut-off versus the measured run."""
    sim_ids = {row["case_id"]: set(row["routed"]) for row in simulated["routed_by_case"]}
    packed_wrong = {
        (case["case_id"], q["id"]) for case in cases_for("hard") for q in case["questions"] if not q["gpt_ok"]
    }
    same = diff = packed_wrong_routed = 0
    for case_id, listed in measured["escalated_ids"].items():
        ids = set(listed)
        packed_wrong_routed += sum(1 for name in ids if (case_id, name) in packed_wrong)
        got = sim_ids.get(case_id, set())
        same += len(ids & got)
        diff += len(ids ^ got)
    gap = {}
    for key in ("line_verdict", "question_accuracy"):
        m, s = measured["metrics"][key]["value"], simulated["metrics"][key]["value"]
        gap[key] = {"measured": m, "simulated": s, "measured_minus_simulated": (m - s) if m is not None and s is not None else None}
    gap["cost_per_1000_usd"] = {
        "measured_gpt_only": measured["cost_per_1000_usd"],
        "simulated_gpt_part": simulated["cost_parts_per_1000_usd"]["gpt"],
        "measured_minus_simulated": measured["cost_per_1000_usd"] - simulated["cost_parts_per_1000_usd"]["gpt"],
    }
    gap["latency_p50_ms"] = {
        "measured": measured["latency_p50_ms"],
        "simulated": simulated["latency_p50_ms"],
        "measured_minus_simulated": measured["latency_p50_ms"] - simulated["latency_p50_ms"],
    }
    gap["routing"] = {
        "same_question_ids": same,
        "ids_in_only_one": diff,
        "measured_share": measured["questions_routed"]["share"],
        "simulated_share": simulated["questions_routed"]["share"],
    }
    metric_gap = gap["line_verdict"]["measured_minus_simulated"]
    gap["why"] = [
        (
            f"Routing: the simulated per-family map picks the same {same} question ids E escalated "
            f"({diff} ids differ)."
        ),
        (
            f"Accuracy: measured minus simulated line verdict is {metric_gap:+.4f}. The simulation takes the packed "
            "GPT call's answers on routed questions. The fresh escalation call got "
            f"{measured['gpt_wrong_on_escalated']} of {measured['questions_routed']['k']} escalated questions wrong; "
            f"the packed call got {packed_wrong_routed} of them wrong. Any accuracy gap comes from those calls, "
            "since the questions Drex kept are the same recorded answers in both."
        ),
        (
            "Cost: the simulation charges the full packed GPT input once per routed case plus scaled output. "
            "The escalation call sent a shorter prompt, so the simulation over-states GPT cost by "
            f"${-gap['cost_per_1000_usd']['measured_minus_simulated']:.2f} per 1,000 cases."
        ),
        (
            "Latency: the simulation scales packed GPT time by the routed share and ignores prompt reading time, "
            f"so it under-states the measured median by {gap['latency_p50_ms']['measured_minus_simulated'] / 1000:.1f} s."
        ),
    ]
    return gap


# ------------------------------------------------------------------ documents


def _cheapest_match(gpt: dict, points: list[dict], chart: str, tolerance: float = 0.01) -> dict | None:
    target = gpt["metrics"][chart]["value"]
    ok = [p for p in points if p["metrics"][chart]["value"] is not None and p["metrics"][chart]["value"] >= target - tolerance - 1e-12]
    if not ok:
        return None
    best = min(ok, key=lambda p: (p["cost_per_1000_usd"], p["threshold"]))
    return {
        "threshold": best["threshold"],
        "router": best.get("router", "global"),
        "value": best["value"],
        "gpt_value": target,
        "cost_per_1000_usd": best["cost_per_1000_usd"],
        "gpt_cost_per_1000_usd": gpt["cost_per_1000_usd"],
        "questions_routed_share": best["questions_routed"]["share"],
        "post_hoc": "Threshold picked on the same recorded set it is scored on. Not a held-out choice.",
        "rule": f"lowest simulated cost among points whose chart metric is >= GPT-6.1 Sol minus {tolerance:g}",
        "label": SIMULATED,
    }


def curve_document(bench: str) -> dict:
    if bench not in BENCHES:
        raise KeyError(bench)
    spec = BENCHES[bench]
    cases = cases_for(bench)
    curve = []
    for t in THRESHOLDS:
        point = simulate(bench, global_router(t), threshold=t)
        point["router"] = "global"
        curve.append(point)
    gpt, drex = gpt_only(bench), drex_only(bench)
    document = {
        "bench": bench,
        "title": spec["title"],
        "chart": spec["chart"],
        "metrics": spec["metrics"],
        "n_cases": len(cases),
        "n_questions": sum(len(c["questions"]) for c in cases),
        "drex_model": loaders.DREX_MODEL,
        "gpt_model": loaders.GPT_MODEL,
        "label": SIMULATED,
        "labels": {
            "curve": SIMULATED,
            "gpt_only": SIMULATED,
            "drex_only": SIMULATED,
            "measured": MEASURED,
        },
        "formulas": FORMULAS,
        "thresholds": THRESHOLDS,
        "curve": curve,
        "gpt_only": gpt,
        "drex_only": drex,
        "measured": [],
        "match_gpt_within_1pt": _cheapest_match(gpt, curve, spec["chart"]),
        "match_gpt_exact": _cheapest_match(gpt, curve, spec["chart"], 0.0),
    }
    if bench == "hard":
        taus = loaders.hard_family_taus()
        family_curve = []
        for t in THRESHOLDS:
            point = simulate(bench, family_router(t, taus), threshold=t)
            point["router"] = "family"
            family_curve.append(point)
        document["family_curve"] = family_curve
        document["family_map"] = {
            "source": "data/runs/hard_family_tau.json",
            "rule": "Families with tau 1.0 (fee, pr, bill_balance; dev accuracy < 0.90) always go to GPT. The rest use the dial. The frozen map is the point at 0.95.",
            "taus": taus.get("taus"),
        }
        document["match_gpt_within_1pt_family"] = _cheapest_match(gpt, family_curve, spec["chart"])
        document["match_gpt_exact_family"] = _cheapest_match(gpt, family_curve, spec["chart"], 0.0)
        measured = hard_measured()
        if measured is not None:
            frozen = simulate(bench, family_router(measured["threshold"], taus), threshold=measured["threshold"], with_ids=True)
            compare = _hard_compare(measured, frozen)
            frozen.pop("routed_by_case", None)
            frozen["router"] = "family"
            measured.pop("escalated_ids", None)
            document["measured"] = [measured]
            document["family_frozen_simulated"] = frozen
            document["measured_vs_simulated"] = compare
    if bench == "pa_criteria_gym":
        document["notes"] = [
            "Questions are Drex's recorded fact:: and scope:: answers. GPT's scope answer is one field and scores both scope questions.",
            "A case with any routed question takes GPT's recorded case-level fields (decision, action, criteria, evidence). Otherwise Drex's.",
            "Both lanes are 370/370 on this set, so every threshold scores the same. Only cost and latency move.",
        ]
    return document


def point_at(bench: str, threshold: float, router: str = "global") -> dict:
    if bench not in BENCHES:
        raise KeyError(bench)
    if router == "family":
        if bench != "hard":
            raise KeyError("family")
        route = family_router(threshold, loaders.hard_family_taus())
    else:
        route = global_router(threshold)
    point = simulate(bench, route, threshold=threshold, with_ids=True)
    point["router"] = router
    point["bench"] = bench
    point["chart"] = BENCHES[bench]["chart"]
    point["formulas"] = FORMULAS
    return point

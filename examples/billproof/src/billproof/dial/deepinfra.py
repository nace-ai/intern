"""Trust dial with Qwen3.8-Max via DeepInfra as the escalation lane.

Recorded answers only. Questions below the threshold take that model's recorded
choice instead of GPT-6.1 Sol. No model calls.

Cost, when any question on a case is routed:
    scale = provider estimated_cost / list-price of the recorded packed call
    routed = scale * (input list dollars + output list dollars * routed/asked)
When the provider did not send estimated_cost, scale is 1 and the published
DeepInfra list rate is used. A fully routed case costs the recorded call.
"""

from __future__ import annotations

import copy
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from billproof.adapters.frontier import _deepinfra_list
from billproof.config import DATA_DIR
from billproof.dial.simulate import (
    BENCHES,
    SIMULATED,
    THRESHOLDS,
    _cheapest_match,
    _score,
    cases_for,
    drex_only,
    family_router,
    global_router,
    gpt_only,
)
from billproof.dial.loaders import hard_family_taus

METRICS = DATA_DIR / "metrics"
RUNS = DATA_DIR / "runs"
ESCALATION_MODEL = "Qwen/Qwen3.8-Max"
ACTIVE = ("hard", "pa_criteria_gym", "persius")


def _slug(model: str) -> str:
    return model.replace("/", "__").replace(" ", "_")


def source_path(bench: str, model: str = ESCALATION_MODEL) -> Path:
    name = {"hard": "hard", "pa_criteria_gym": "pa", "persius": "persius"}[bench]
    return RUNS / f"di_{name}_{_slug(model)}.jsonl"


def path_for(bench: str) -> Path:
    return METRICS / f"dial_{bench}_deepinfra.json"


def _last_ok(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not path.exists():
        return found
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("case_id") and not row.get("error") and not row.get("simulated"):
                found[row["case_id"]] = row
    return found


def _stamp(case: dict, row: dict) -> None:
    case["di_model"] = row.get("model") or row.get("model_requested")
    case["di_input_tokens"] = int(row.get("input_tokens") or 0)
    case["di_output_tokens"] = int(row.get("output_tokens") or 0)
    case["di_ms"] = float(row.get("latency_ms") or 0.0)
    case["di_cost_usd"] = float(row.get("cost_usd") or 0.0)
    case["di_cost_basis"] = row.get("cost_basis")


def _apply(bench: str, case: dict, row: dict) -> None:
    _stamp(case, row)
    if bench == "hard":
        answers = row.get("answers") or {}
        correct = row.get("correct") or {}
        invalid = row.get("invalid") or {}
        for question in case["questions"]:
            name = question["id"]
            payload = answers.get(name) or {}
            bad = bool(invalid.get(name)) or bool(row.get("error"))
            question["gpt_choice"] = None if bad else payload.get("choice")
            question["gpt_ok"] = (not bad) and bool(correct.get(name))
        return
    if bench == "pa_criteria_gym":
        score = row.get("score") or {}
        pred = row.get("prediction") or {}
        parts = score.get("parts") or {}
        err = row.get("error")
        for question in case["questions"]:
            kind, _, field = question["id"].partition("::")
            if kind == "fact":
                question["gpt_ok"] = bool((score.get("per_fact") or {}).get(field)) and not err
                question["gpt_choice"] = ((pred.get("facts") or {}).get(field))
            else:
                question["gpt_ok"] = bool(score.get("scope")) and not err
                question["gpt_choice"] = pred.get("scope")
        flags = dict(case.get("case") or {})
        flags["gpt_rest"] = all(v for k, v in parts.items() if k not in ("facts_all", "scope")) and not err
        flags["gpt_decision"] = bool(score.get("decision")) and not err
        case["case"] = flags
        return
    question = case["questions"][0]
    question["gpt_choice"] = row.get("choice")
    question["gpt_ok"] = bool(row.get("correct")) and not row.get("error")


def cases_for_deepinfra(bench: str, model: str = ESCALATION_MODEL) -> list[dict]:
    if bench not in ACTIVE:
        raise KeyError(bench)
    base = copy.deepcopy(cases_for(bench))
    rows = _last_ok(source_path(bench, model))
    missing = [case["case_id"] for case in base if case["case_id"] not in rows]
    if missing:
        raise FileNotFoundError(
            f"{source_path(bench, model).name} covers {len(base) - len(missing)}/{len(base)} "
            f"(first missing {missing[0]})"
        )
    for case in base:
        _apply(bench, case, rows[case["case_id"]])
    return base


def _routed_cost(case: dict, routed: int, asked: int) -> float:
    if routed <= 0 or asked <= 0:
        return 0.0
    listed = _deepinfra_list(case.get("di_model") or ESCALATION_MODEL)
    inn = int(case.get("di_input_tokens") or 0)
    out = int(case.get("di_output_tokens") or 0)
    if listed:
        in_d = inn * float(listed["input_usd_per_mtok"]) / 1_000_000
        out_d = out * float(listed["output_usd_per_mtok"]) / 1_000_000
    else:
        in_d = out_d = 0.0
    list_total = in_d + out_d
    full = float(case.get("di_cost_usd") or 0.0)
    if case.get("di_cost_basis") == "provider_estimated_cost" and full > 0 and list_total > 0:
        return (in_d + out_d * (routed / asked)) * (full / list_total)
    if listed:
        return in_d + out_d * (routed / asked)
    return full * (routed / asked)


def _point(bench: str, cases: list[dict], router, threshold: float | None, *, with_ids: bool = False) -> dict:
    picks = [[bool(router(q)) for q in case["questions"]] for case in cases]
    metrics = _score(bench, cases, picks)
    n = len(cases)
    q_routed = sum(sum(row) for row in picks)
    q_total = sum(len(row) for row in picks)
    c_routed = sum(1 for row in picks if any(row))
    drex_costs, di_costs, latencies = [], [], []
    for case, row in zip(cases, picks):
        routed, asked = sum(row), len(row)
        drex_costs.append(case["drex_cost_usd"])
        di_costs.append(_routed_cost(case, routed, asked))
        latencies.append(case["drex_ms"] + (case["di_ms"] * routed / asked if routed and asked else 0.0))
    point = {
        "threshold": threshold,
        "router": "global",
        "label": SIMULATED,
        "escalate": "deepinfra",
        "escalate_model": ESCALATION_MODEL,
        "via": "DeepInfra",
        "value": metrics[BENCHES[bench]["chart"]]["value"],
        "metrics": metrics,
        "questions_routed": {"k": q_routed, "n": q_total, "share": (q_routed / q_total) if q_total else None},
        "cases_routed": {"k": c_routed, "n": n, "share": (c_routed / n) if n else None},
        "cost_per_1000_usd": (sum(drex_costs) + sum(di_costs)) / n * 1000 if n else None,
        "cost_parts_per_1000_usd": {
            "drex": sum(drex_costs) / n * 1000 if n else None,
            "deepinfra": sum(di_costs) / n * 1000 if n else None,
        },
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
    }
    if with_ids:
        point["routed_by_case"] = [
            {
                "case_id": case["case_id"],
                "asked": len(row),
                "routed": [q["id"] for q, flag in zip(case["questions"], row) if flag],
            }
            for case, row in zip(cases, picks)
        ]
    return point


def _only(bench: str, cases: list[dict]) -> dict:
    picks = [[True] * len(case["questions"]) for case in cases]
    metrics = _score(bench, cases, picks)
    n = len(cases)
    costs = [case["di_cost_usd"] for case in cases]
    return {
        "label": SIMULATED,
        "escalate_model": ESCALATION_MODEL,
        "via": "DeepInfra",
        "value": metrics[BENCHES[bench]["chart"]]["value"],
        "metrics": metrics,
        "cost_per_1000_usd": (sum(costs) / n * 1000) if n else None,
        "latency_p50_ms": statistics.median(case["di_ms"] for case in cases) if cases else None,
        "note": "DeepSeek V4.1 Flash recorded answers on every question. Cost is the recorded provider cost. No Drex cost.",
    }


def point_at(bench: str, threshold: float, router: str = "global") -> dict:
    if bench not in ACTIVE:
        raise KeyError(bench)
    cases = cases_for_deepinfra(bench)
    if router == "family":
        if bench != "hard":
            raise KeyError("family")
        route = family_router(threshold, hard_family_taus())
    else:
        route = global_router(threshold)
    point = _point(bench, cases, route, threshold, with_ids=True)
    point["router"] = router
    point["bench"] = bench
    point["chart"] = BENCHES[bench]["chart"]
    return point


def curve_document(bench: str) -> dict:
    if bench not in ACTIVE:
        raise KeyError(bench)
    spec = BENCHES[bench]
    cases = cases_for_deepinfra(bench)
    curve = []
    for threshold in THRESHOLDS:
        point = _point(bench, cases, global_router(threshold), threshold)
        point["router"] = "global"
        curve.append(point)
    gpt, drex = gpt_only(bench), drex_only(bench)
    alone = _only(bench, cases)
    document = {
        "bench": bench,
        "title": spec["title"],
        "chart": spec["chart"],
        "metrics": spec["metrics"],
        "n_cases": len(cases),
        "n_questions": sum(len(case["questions"]) for case in cases),
        "escalate": "deepinfra",
        "escalate_model": ESCALATION_MODEL,
        "via": "DeepInfra",
        "provider": "deepinfra",
        "source_run": str(source_path(bench).relative_to(DATA_DIR.parent)),
        "label": SIMULATED,
        "formulas": {
            "rule": (
                "Drex keeps every question with recorded confidence >= threshold. "
                "The rest take DeepSeek V4.1 Flash on DeepInfra, the recorded answer to the same question."
            ),
            "cost_per_1000_usd": (
                "mean of [Drex recorded cost_usd + routed DeepInfra cost]. "
                "Routed cost scales the recorded packed call: full input plus output * routed/asked, "
                "in list-price weights, multiplied by recorded estimated_cost/list when estimated_cost is present. "
                "List rates are the published DeepInfra cents_per_token figures, not the GPT $2/$10 rate."
            ),
            "latency_p50_ms": (
                "median of [Drex recorded latency_ms + DeepInfra recorded latency_ms * routed/asked]."
            ),
        },
        "thresholds": THRESHOLDS,
        "curve": curve,
        "deepinfra_only": alone,
        "gpt_only": gpt,
        "drex_only": drex,
        "match_gpt_within_1pt": _cheapest_match(gpt, curve, spec["chart"]),
        "match_gpt_exact": _cheapest_match(gpt, curve, spec["chart"], 0.0),
    }
    if bench == "hard":
        taus = hard_family_taus()
        family_curve = []
        for threshold in THRESHOLDS:
            point = _point(bench, cases, family_router(threshold, taus), threshold)
            point["router"] = "family"
            family_curve.append(point)
        document["family_curve"] = family_curve
        document["match_gpt_within_1pt_family"] = _cheapest_match(gpt, family_curve, spec["chart"])
    return document


def build(bench: str) -> dict:
    document = curve_document(bench)
    document["computed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document["generator"] = "billproof.dial.deepinfra"
    path_for(bench).write_text(json.dumps(document, indent=1) + "\n")
    return document


def main(argv: list[str]) -> None:
    for bench in argv or list(ACTIVE):
        try:
            document = build(bench)
        except FileNotFoundError as exc:
            print(f"{bench}: skipped ({exc})")
            continue
        chart = document["chart"]
        alone = document["deepinfra_only"]
        print(
            f"{bench}: deepinfra {alone['value']:.4f} ${alone['cost_per_1000_usd']:.2f}/1k | "
            f"match {chart}: {document['match_gpt_within_1pt']}"
        )


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])

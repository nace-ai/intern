"""Build the results hub from recorded metric files and run rows.

This module does not call a model. A rate, interval, latency, or dollar figure
is copied from a metric file or reduced from fields already stored on run rows
(median of latency_ms, mean of a recorded cost, Wilson interval from recorded k
and n). Recovering k as round(rate * n) is used only when that product is an
integer within 1e-4.
"""

from __future__ import annotations

import gzip
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from billproof.bench.metrics import wilson_ci
from billproof.config import DATA_DIR

METRICS = DATA_DIR / "metrics"
RUNS = DATA_DIR / "runs"
SUMMARY_PATH = METRICS / "all_benchmarks.json"
RESULTS_MD = DATA_DIR.parent / "docs" / "RESULTS.md"
GPT_LIST_IN = 2.0
GPT_LIST_OUT = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(DATA_DIR.parent))
    except ValueError:
        return str(path)


def _iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _ci_from(block: dict) -> list[float] | None:
    if isinstance(block.get("ci95"), list) and len(block["ci95"]) == 2:
        return [block["ci95"][0], block["ci95"][1]]
    if isinstance(block.get("wilson_95"), list) and len(block["wilson_95"]) == 2:
        return [block["wilson_95"][0], block["wilson_95"][1]]
    if isinstance(block.get("ci"), list) and len(block["ci"]) == 2:
        return [block["ci"][0], block["ci"][1]]
    if block.get("ci_low") is not None and block.get("ci_high") is not None:
        return [block["ci_low"], block["ci_high"]]
    return None


def _recover_k(value: Any, n: Any) -> int | None:
    if isinstance(value, bool) or value is None or not n:
        return None
    try:
        product = float(value) * int(n)
    except (TypeError, ValueError):
        return None
    nearest = round(product)
    if abs(product - nearest) < 1e-4:
        return int(nearest)
    return None


def pack_metric(block: dict | None, *, source: str) -> dict | None:
    if not isinstance(block, dict) or "value" not in block:
        return None
    n = block.get("n")
    k = block.get("k")
    recovered = False
    if k is None:
        k = _recover_k(block.get("value"), n)
        recovered = k is not None
    ci = _ci_from(block)
    method = block.get("ci_method")
    if ci is None and k is not None and n:
        lo, hi = wilson_ci(int(k), int(n))
        ci = [lo, hi]
        method = "wilson"
    note = block.get("note")
    if recovered and not block.get("k"):
        extra = "k is round(value*n) from the recorded rate. Wilson CI computed by the results hub."
        note = f"{note} {extra}".strip() if note else extra
    return {
        "value": block.get("value"),
        "k": k,
        "n": n,
        "ci95": ci,
        "ci_method": method,
        "source": source,
        "source_run": block.get("source_run"),
        "simulated": block.get("simulated"),
        "projected": block.get("projected"),
        "added_after_first_results": block.get("added_after_first_results"),
        "partial": block.get("partial"),
        "note": note,
    }


def _lane(
    lane_id: str,
    label: str,
    metric: dict | None,
    *,
    latency_p50_ms: float | None = None,
    latency_source: str | None = None,
    cost_per_1000_usd: float | None = None,
    cost_basis: str | None = None,
    cost_source: str | None = None,
    extra: dict | None = None,
    metrics: dict | None = None,
    primary_metric: str | None = None,
) -> dict:
    body = {
        "id": lane_id,
        "label": label,
        "value": None if metric is None else metric.get("value"),
        "k": None if metric is None else metric.get("k"),
        "n": None if metric is None else metric.get("n"),
        "ci95": None if metric is None else metric.get("ci95"),
        "ci_method": None if metric is None else metric.get("ci_method"),
        "latency_p50_ms": latency_p50_ms,
        "latency_source": latency_source,
        "cost_per_1000_usd": cost_per_1000_usd,
        "cost_basis": cost_basis,
        "cost_source": cost_source,
        "source": None if metric is None else metric.get("source"),
    }
    if metric and metric.get("note"):
        body["note"] = metric["note"]
    if metric and metric.get("added_after_first_results"):
        body["added_after_first_results"] = True
    if metric and metric.get("simulated"):
        body["simulated"] = True
    if metric and metric.get("projected"):
        body["projected"] = True
    if metric and metric.get("partial"):
        body["partial"] = True
    if extra:
        body["extra"] = extra
    if metrics:
        body["metrics"] = metrics
    if primary_metric:
        body["primary_metric"] = primary_metric
    return body


def _median(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def _mean(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.fmean(clean))


def _case_cost_latency(path: Path, *, cost_key: str = "list_cost_usd") -> list[dict]:
    """One row per case. Question-level files repeat the call cost; keep one copy."""
    by: dict[str, dict] = {}
    order: list[str] = []
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        if not case_id:
            continue
        if case_id not in by:
            order.append(case_id)
            by[case_id] = {"case_id": case_id, "latency_ms": None, "cost": None, "question_level": False}
        slot = by[case_id]
        if row.get("question_id"):
            slot["question_level"] = True
            if slot["latency_ms"] is None and row.get("latency_ms") is not None:
                slot["latency_ms"] = row.get("latency_ms")
            if slot["cost"] is None and row.get("cost_usd") is not None:
                slot["cost"] = row.get("cost_usd")
            continue
        slot["latency_ms"] = row.get("latency_ms")
        if row.get(cost_key) is not None:
            slot["cost"] = row.get(cost_key)
        elif row.get("cost_usd") is not None:
            slot["cost"] = row.get("cost_usd")
    return [by[case_id] for case_id in order]


def _p50_cost(rows: list[dict], ids: set[str] | None = None) -> tuple[float | None, float | None, int]:
    chosen = [row for row in rows if ids is None or row["case_id"] in ids]
    return (
        _median([row["latency_ms"] for row in chosen]),
        (None if _mean([row["cost"] for row in chosen]) is None else _mean([row["cost"] for row in chosen]) * 1000.0),
        len(chosen),
    )


def _hard_ids() -> set[str]:
    document = read_json(RUNS / "ladder_hard_ids.json")
    return set(document["run_order"])


def _ladder_index() -> dict[tuple[str, str], dict]:
    summary = read_json(METRICS / "ladder_summary.json")
    return {(point["benchmark"], point["model"]): point for point in summary["points"]}


def _class_counts(rows: list[dict], lane: str) -> dict[str, int]:
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "n": 0, "k": 0}
    for row in rows:
        if row.get("lane") != lane:
            continue
        gold = row.get("gold")
        choice = row.get("choice") if not row.get("error") else None
        if gold not in ("yes", "no"):
            continue
        counts["n"] += 1
        if row.get("correct"):
            counts["k"] += 1
        if gold == "yes":
            counts["tp" if choice == "yes" else "fn"] += 1
        else:
            counts["tn" if choice == "no" else "fp"] += 1
    positives = counts["tp"] + counts["fn"]
    negatives = counts["tn"] + counts["fp"]
    counts["sensitivity"] = (counts["tp"] / positives) if positives else None
    counts["specificity"] = (counts["tn"] / negatives) if negatives else None
    return counts


def _bench(**kwargs: Any) -> dict:
    return kwargs


def _pa() -> dict:
    path = METRICS / "pa_development_summary.json"
    document = read_json(path)
    source = _rel(path)
    labels = {
        "gpt": "GPT-6.1 Sol",
        "drex": "Drex v1.1",
        "drexfin": "Drex finance",
        "hybrid": "Drex then GPT @0.95",
        "checked": "GPT checked by Drex @0.95",
        "drex_supp": "Drex v1.1 supplemental set",
    }
    lanes = []
    breakdowns: dict[str, Any] = {}
    for key, label in labels.items():
        column = document["columns"][key]
        metric = pack_metric(
            {
                "value": column["whole_case"],
                "k": column["whole_k"],
                "n": column["n"],
                "ci": column["whole_ci"],
                "ci_method": "wilson",
                "source_run": source,
            },
            source=f"{source}#columns.{key}.whole_case",
        )
        decision = pack_metric(
            {
                "value": column["decision"],
                "k": column["decision_k"],
                "n": column["n"],
                "ci": column["decision_ci"],
                "ci_method": "wilson",
            },
            source=f"{source}#columns.{key}.decision",
        )
        list_cost = column.get("cost_per_1k_list")
        recorded = column.get("cost_per_1000")
        if list_cost is not None:
            cost, basis, cost_source = list_cost, "list", f"{source}#columns.{key}.cost_per_1k_list"
        else:
            cost, basis, cost_source = recorded, "recorded", f"{source}#columns.{key}.cost_per_1000"
        lanes.append(
            _lane(
                key,
                label,
                metric,
                latency_p50_ms=column.get("p50_ms"),
                latency_source=f"{source}#columns.{key}.p50_ms",
                cost_per_1000_usd=cost,
                cost_basis=basis,
                cost_source=cost_source,
                metrics={"whole_case": metric, "decision": decision},
                primary_metric="whole_case",
                extra={
                    "decision": decision,
                    "per_fact": column.get("per_fact"),
                    "n_facts": column.get("n_facts"),
                    "adapter_cost_per_1000": recorded if list_cost is not None else None,
                    "errors": column.get("errors"),
                },
            )
        )
        breakdowns[key] = {"by_decision": column.get("by_decision")}
    n_main = document["columns"]["gpt"]["n"]
    return _bench(
        id="pa_criteria_gym",
        title="Prior authorization, criteria-gym",
        tests="Whole prior-auth request on published Medicaid criteria, synthetic patient facts.",
        n=n_main,
        split=document.get("split"),
        metric="whole_case",
        metric_definition="Share of requests where the decision and every criterion fact match gold.",
        lanes=lanes,
        breakdowns=breakdowns,
        chart={
            "metric": "whole_case",
            "value_field": "value",
            "note": "Plot lanes[].metrics.whole_case. decision is the label only. whole_case also requires every criterion fact.",
        },
        notes=[
            f"Source split field is {document.get('split')!r}. The 370-row columns share one held-out set. "
            f"drex_supp n={document['columns']['drex_supp']['n']} is a different supplemental set.",
            "GPT, hybrid, and checked costs are the file's cost_per_1k_list. Drex costs are the recorded Nace cost_per_1000. "
            "The adapter-rate cost_per_1000 for GPT is kept on the lane extra field.",
            "Drex then GPT did not escalate on this set, so its cost and latency match Drex alone.",
        ],
        sources=[source],
        computed_at=document.get("computed_at"),
    )


def _synthetic() -> dict:
    path = RUNS / "real_synthetic_test.report.json"
    document = read_json(path)
    source = _rel(path)
    run_path = RUNS / "real_synthetic_test.jsonl"
    token_cost: dict[str, float] = {}
    token_n: dict[str, int] = {}
    for row in _iter_jsonl(run_path):
        lane = row.get("lane")
        if lane not in ("frontier", "drex", "hybrid"):
            continue
        token_n[lane] = token_n.get(lane, 0) + 1
        tokens_in = int(row.get("input_tokens") or 0)
        tokens_out = int(row.get("output_tokens") or 0)
        if lane == "frontier":
            token_cost[lane] = token_cost.get(lane, 0.0) + (tokens_in * GPT_LIST_IN + tokens_out * GPT_LIST_OUT) / 1_000_000
        else:
            token_cost[lane] = token_cost.get(lane, 0.0) + float(row.get("cost_usd") or 0.0)
    labels = {"frontier": "GPT-6.1 Sol", "drex": "Drex v1.1", "hybrid": "Hybrid"}
    lanes = []
    breakdowns = {}
    for key, label in labels.items():
        lane = document["lanes"][key]
        k = lane.get("n_automated_correct")
        n_q = lane.get("n_questions")
        metric = pack_metric(
            {
                "value": lane["accuracy"],
                "k": k,
                "n": n_q,
                "ci": lane["ci"],
                "ci_method": "wilson",
                "source_run": "real_synthetic_test",
            },
            source=f"{source}#lanes.{key}.accuracy",
        )
        cost = (token_cost.get(key, 0.0) / token_n[key] * 1000.0) if token_n.get(key) else None
        if key == "frontier":
            basis = "list"
        else:
            basis = "recorded"
        lanes.append(
            _lane(
                key,
                label,
                metric,
                latency_p50_ms=lane.get("p50_ms"),
                latency_source=f"{source}#lanes.{key}.p50_ms",
                cost_per_1000_usd=cost,
                cost_basis=basis,
                cost_source=f"{_rel(run_path)} tokens at $2/$10 per 1M" if basis == "list" else f"{_rel(run_path)} cost_usd",
                extra={
                    "case_pass": lane.get("case_pass"),
                    "n_cases": lane.get("n"),
                    "n_questions": n_q,
                    "report_cost_per_case_usd": lane.get("cost_per_case_usd"),
                    "report_cost_basis": "adapter rate stored on the report" if key != "drex" else "recorded",
                },
            )
        )
        breakdowns[key] = {"by_family": lane.get("by_family")}
    meta = document["meta"]
    return _bench(
        id="synthetic_v1",
        title="Synthetic v1 bills",
        tests="Planted errors on synthetic bills: duplicate, fee, NCCI, EOB, and the case questions.",
        n=meta.get("n_cases"),
        split=meta.get("split"),
        metric="question_accuracy",
        metric_definition="Share of questions correct. The Wilson interval uses the question count. case_pass is the share of bills with every question correct.",
        lanes=lanes,
        breakdowns=breakdowns,
        notes=[
            f"Live run mode={meta.get('mode')}. Drex model in the report meta is {meta.get('drex_model')}.",
            "No drex-max-finance lane is in real_synthetic_test.report.json.",
            "GPT $/1,000 is recomputed from recorded frontier tokens at $2/$10 per 1M. Drex and hybrid $/1,000 are the mean of recorded cost_usd. Hybrid tokens are not repriced at the GPT list rate. The report's cost_per_case_usd stays on extra.",
            "Question rows inside one bill are not independent, so the Wilson interval is narrower than a case-clustered interval.",
        ],
        sources=[source, _rel(run_path)],
        computed_at=meta.get("created"),
    )


def _persius() -> dict:
    path = METRICS / "c_persius_test.json"
    document = read_json(path)
    source = _rel(path)
    rows = list(_iter_jsonl(RUNS / "c_persius_test.jsonl"))
    labels = {"drex": "Drex v1.1", "frontier": "GPT-6.1 Sol"}
    lanes = []
    breakdowns: dict[str, Any] = {}
    ids = read_json(RUNS / "c_persius_test_ids.json")
    appeal = {item["case_id"]: item.get("appeal_type") for item in ids.get("items") or []}
    for key, label in labels.items():
        lane = document["lanes"][key]
        metric = pack_metric(
            {
                "value": lane["accuracy"],
                "k": lane["n_correct"],
                "n": lane["n"],
                "wilson_95": lane["wilson_95"],
                "ci_method": "wilson",
                "source_run": document.get("source_run"),
            },
            source=f"{source}#lanes.{key}.accuracy",
        )
        if key == "frontier" and lane.get("cost_per_1000_usd_list") is not None:
            cost, basis, cost_source = lane["cost_per_1000_usd_list"], "list", f"{source}#lanes.frontier.cost_per_1000_usd_list"
        else:
            cost, basis, cost_source = lane.get("cost_per_1000_usd"), "recorded", f"{source}#lanes.{key}.cost_per_1000_usd"
        counts = _class_counts(rows, key)
        by_type: dict[str, dict] = {}
        for row in rows:
            if row.get("lane") != key:
                continue
            kind = appeal.get(row.get("case_id")) or "unknown"
            slot = by_type.setdefault(kind, {"n": 0, "k": 0})
            slot["n"] += 1
            slot["k"] += 1 if row.get("correct") else 0
        for slot in by_type.values():
            lo, hi = wilson_ci(slot["k"], slot["n"])
            slot["accuracy"] = slot["k"] / slot["n"] if slot["n"] else None
            slot["ci95"] = [lo, hi]
        lanes.append(
            _lane(
                key,
                label,
                metric,
                latency_p50_ms=lane.get("latency_p50_ms"),
                latency_source=f"{source}#lanes.{key}.latency_p50_ms",
                cost_per_1000_usd=cost,
                cost_basis=basis,
                cost_source=cost_source,
                extra={
                    "brier": lane.get("brier"),
                    "ece": lane.get("ece"),
                    "model": lane.get("model"),
                    "confidence_kind": lane.get("confidence_kind"),
                    "adapter_cost_per_1000": lane.get("cost_per_1000_usd") if key == "frontier" else None,
                    "class_counts": counts,
                    "risk_coverage": lane.get("risk_coverage"),
                },
            )
        )
        breakdowns[key] = {"by_appeal_type": by_type}
    return _bench(
        id="persius",
        title="Persius real appeals",
        tests="Held-out independent medical review appeals: does the review overturn the denial?",
        n=document.get("n_items_frozen"),
        split=document.get("split"),
        metric="accuracy",
        metric_definition="Share of appeals whose yes/no overturn choice matches the recorded decision. Invalid answers count wrong.",
        lanes=lanes,
        breakdowns=breakdowns,
        mcnemar=document.get("mcnemar"),
        notes=[
            "Neutral GPT prompt, not the bill-audit prompt. Same appeal text and question for both lanes.",
            "GPT $/1,000 is cost_per_1000_usd_list ($2/$10 per 1M from stored tokens). cost_per_1000_usd on that lane is the adapter rate.",
            "Sensitivity and specificity are counted from data/runs/c_persius_test.jsonl. A wrong or blank choice is a false negative when gold is yes and a false positive when gold is no.",
            "by_appeal_type joins c_persius_test_ids.json to the run rows. It is a reduction of recorded correct flags, not a new score.",
        ],
        sources=[source, document.get("source_run"), "data/runs/c_persius_test_ids.json"],
        computed_at=document.get("computed_at"),
        population=document.get("population"),
    )


def _score_block(lane_id: str, label: str, block: dict, *, source: str, cost_basis: str) -> dict:
    metric = pack_metric(
        {
            "value": block.get("accuracy"),
            "k": block.get("k"),
            "n": block.get("n"),
            "ci95": block.get("ci95"),
            "ci_method": block.get("ci_method") or "wilson",
            "source_run": source,
        },
        source=source,
    )
    extra = {
        key: block.get(key)
        for key in ("counts", "sensitivity", "specificity", "gpt_call_rate", "mcnemar_vs_gpt", "mcnemar_vs_drex", "cost_note")
        if key in block
    }
    return _lane(
        lane_id,
        label,
        metric,
        latency_p50_ms=block.get("latency_p50_ms"),
        latency_source=source,
        cost_per_1000_usd=block.get("cost_per_1000_usd_list"),
        cost_basis=cost_basis,
        cost_source=source,
        extra=extra,
        metrics={"accuracy": metric} if metric else None,
        primary_metric="accuracy",
    )


def _persius_combo() -> dict | None:
    path = METRICS / "g_persius_combo.json"
    if not path.exists():
        return None
    document = read_json(path)
    if document.get("applied") is not True:
        return None
    source = _rel(path)
    test = document["test"]
    baselines = document.get("baselines") or {}
    cost_basis = "policy: Drex recorded cost_usd plus GPT list $2/$10"
    lanes = [
        _score_block("combo", document.get("description") or document.get("rule_id"), test, source=f"{source}#test", cost_basis=cost_basis),
    ]
    if "frontier" in baselines:
        lanes.append(_score_block("frontier", "GPT-6.1 Sol alone", baselines["frontier"], source=f"{source}#baselines.frontier", cost_basis="list"))
    if "drex" in baselines:
        lanes.append(_score_block("drex", "Drex v1.1 alone", baselines["drex"], source=f"{source}#baselines.drex", cost_basis="recorded"))
    dev_lanes = []
    rule_path = RUNS / "g_persius_rule.json"
    rule = read_json(rule_path) if rule_path.exists() else {}
    if rule.get("dev"):
        dev_lanes.append(_score_block("combo", rule.get("description") or rule.get("rule_id"), rule["dev"], source=f"{_rel(rule_path)}#dev", cost_basis=cost_basis))
    for key, label in (("frontier", "GPT-6.1 Sol alone"), ("drex", "Drex v1.1 alone")):
        if (rule.get("baselines") or {}).get(key):
            dev_lanes.append(
                _score_block(key, label, rule["baselines"][key], source=f"{_rel(rule_path)}#baselines.{key}", cost_basis="list" if key == "frontier" else "recorded")
            )
    tied = []
    top = (rule.get("grid") or [None])[0]
    if top and top.get("dev"):
        top_acc = top["dev"].get("accuracy")
        top_cost = top["dev"].get("cost_per_1000_usd_list")
        tied = [
            entry.get("rule_id")
            for entry in rule.get("grid") or []
            if (entry.get("dev") or {}).get("accuracy") == top_acc
            and (entry.get("dev") or {}).get("cost_per_1000_usd_list") == top_cost
        ]
    dev_spend = {"drex": 0.0, "frontier": 0.0, "rows": 0}
    dev_jsonl = RUNS / "g_persius_dev.jsonl"
    if dev_jsonl.exists():
        for row in _iter_jsonl(dev_jsonl):
            lane = row.get("lane")
            if lane in dev_spend:
                dev_spend[lane] += float(row.get("cost_usd") or 0.0)
                dev_spend["rows"] += 1
    gpt_k = (baselines.get("frontier") or {}).get("k")
    combo_k = test.get("k")
    beat = None
    if combo_k is not None and gpt_k is not None:
        beat = "beats GPT on the point count" if combo_k > gpt_k else "does not beat GPT on the point count"
    notes = [
        document.get("note"),
        document.get("description"),
        f"Selected rule_id {document.get('rule_id')}. Dev accuracy {document.get('dev_accuracy')} on n={document.get('dev_n')}, from {document.get('dev_source')}.",
        beat,
        "McNemar b is the combination correct and the other lane wrong. c is the reverse. The test p-value is not corrected for the dev grid.",
    ]
    if len(tied) > 1:
        notes.append(
            "These dev rules tied on accuracy and list $/1,000, so rule_id order selected "
            + ", ".join(str(item) for item in tied)
            + f". The frozen rule is {document.get('rule_id')}."
        )
    if dev_spend["rows"]:
        notes.append(
            "Dev call spend, sum of recorded cost_usd on data/runs/g_persius_dev.jsonl: "
            f"Drex {dev_spend['drex']:.6f}, GPT adapter {dev_spend['frontier']:.6f}, rows {dev_spend['rows']}. "
            "That is the invoice on this script, not the policy $/1,000."
        )
    return _bench(
        id="persius_combo",
        title="Persius combination on real appeals",
        tests="One rule, frozen on a 300-row Persius train sample, scored once on the existing 400 test rows.",
        n=test.get("n"),
        split="test",
        metric="accuracy",
        metric_definition="Share of the 400 recorded test appeals the frozen rule gets right. No new test model call.",
        chart={
            "metric": "accuracy",
            "value_field": "value",
            "note": "Plot lanes[].metrics.accuracy on the test split. The combination is the first lane. It is not a win unless its k is higher than frontier.",
        },
        lanes=lanes,
        secondary=[
            {
                "id": "train_dev_300",
                "metric": "accuracy",
                "metric_definition": "Where the rule was chosen. Not the test score.",
                "lanes": dev_lanes,
            }
        ] if dev_lanes else [],
        mcnemar={
            "vs_gpt": test.get("mcnemar_vs_gpt"),
            "vs_drex": test.get("mcnemar_vs_drex"),
            "b_meaning": "combination correct and the named lane wrong",
        },
        notes=[note for note in notes if note],
        sources=[source, document.get("dev_source"), document.get("source_run"), "data/runs/g_persius_dev.jsonl"],
        computed_at=document.get("computed_at"),
        extra={"rule_id": document.get("rule_id"), "new_test_calls": document.get("new_test_calls"), "dev_spend_cost_usd": dev_spend},
    )


def _hard_latency_cost() -> dict[str, dict]:
    """Median latency and $/1,000 for the frozen 100, from the run rows those scores cite."""
    ids = _hard_ids()
    ladder = _ladder_index()
    specs = {
        "drex-v1": [RUNS / "hard_test_drex-v1.answers.jsonl.gz"],
        "drex-finance": [RUNS / "hard_test_drex-finance.answers.jsonl.gz"],
        "gpt": [RUNS / "hard_test_gpt_subset.jsonl", RUNS / "hard_test_gpt.jsonl"],
        "billproof-system": [RUNS / "hard_test_billproof-system.jsonl"],
        "hybrid": [RUNS / "hard_test_family_hybrid.jsonl"],
        "anthropic__claude-haiku-5.5": [RUNS / "ladder_hard_anthropic__claude-haiku-5.5.jsonl"],
        "deepseek__deepseek-v4.1-flash": [RUNS / "ladder_hard_deepseek__deepseek-v4.1-flash.jsonl"],
        "google__gemini-3.5-flash-lite": [RUNS / "ladder_hard_google__gemini-3.5-flash-lite.jsonl"],
        "openai__gpt-5.4-mini": [RUNS / "ladder_hard_openai__gpt-5.4-mini.jsonl"],
    }
    model_key = {
        "drex-v1": "drex-max-v1.1",
        "drex-finance": "drex-max-finance",
        "gpt": "openai/gpt-6.1-sol",
        "anthropic__claude-haiku-5.5": "anthropic/claude-haiku-5.5",
        "deepseek__deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
        "google__gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
        "openai__gpt-5.4-mini": "openai/gpt-5.4-mini",
    }
    found: dict[str, dict] = {}
    for lane, paths in specs.items():
        rows: list[dict] = []
        seen: set[str] = set()
        for path in paths:
            for row in _case_cost_latency(path):
                if row["case_id"] in seen:
                    continue
                if lane in ("drex-v1", "drex-finance", "gpt") and row["case_id"] not in ids:
                    continue
                seen.add(row["case_id"])
                rows.append(row)
        latency, cost, n = _p50_cost(rows)
        point = ladder.get(("hard", model_key[lane])) if lane in model_key else None
        if point and point.get("cost_per_1000_list_usd") is not None:
            cost = point["cost_per_1000_list_usd"]
            basis = "list"
            cost_source = "data/metrics/ladder_summary.json"
        else:
            basis = "recorded list_cost_usd or cost_usd, mean times 1000"
            cost_source = ", ".join(_rel(path) for path in paths)
        found[lane] = {
            "latency_p50_ms": latency,
            "latency_source": ", ".join(_rel(path) for path in paths),
            "cost_per_1000_usd": cost,
            "cost_basis": basis,
            "cost_source": cost_source,
            "n_cases_cost": n if point is None else point.get("n"),
            "partial": bool(point and point.get("partial")),
        }
    return found


def _hard() -> dict:
    path = METRICS / "hard_outcome_test.json"
    document = read_json(path)
    source = _rel(path)
    timing = _hard_latency_cost()
    labels = {
        "gpt": "GPT-6.1 Sol",
        "drex-v1": "Drex v1.1",
        "drex-finance": "Drex finance",
        "hybrid": "Drex-first hybrid",
        "billproof-system": "BillProof system",
        "anthropic__claude-haiku-5.5": "Claude Haiku 5.5",
        "deepseek__deepseek-v4.1-flash": "DeepSeek v4.1 Flash",
        "google__gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
        "openai__gpt-5.4-mini": "GPT-5.4 mini",
    }
    lanes = []
    secondary = []
    breakdowns: dict[str, Any] = {}
    for key, label in labels.items():
        block = document["lanes"][key]
        metric = pack_metric(block["line_verdict"], source=f"{source}#lanes.{key}.line_verdict")
        clock = timing.get(key) or {}
        extra = {
            "n_cases": block.get("n_cases"),
            "bill_verdict": pack_metric(block.get("bill_verdict"), source=f"{source}#lanes.{key}.bill_verdict"),
            "strict_case": pack_metric(block.get("strict_case"), source=f"{source}#lanes.{key}.strict_case"),
            "overcharge_caught": pack_metric(block.get("overcharge_caught"), source=f"{source}#lanes.{key}.overcharge_caught"),
            "false_alarm_dollars": pack_metric(
                block.get("overcharge_wrongly_disputed_usd") or block.get("false_alarm_dollars"),
                source=f"{source}#lanes.{key}.false_alarm",
            ),
        }
        if block.get("question_accuracy"):
            extra["question_accuracy"] = pack_metric(block["question_accuracy"], source=f"{source}#lanes.{key}.question_accuracy")
        all_six_files = {
            "gpt": "hard_test_gpt-subset_line_verdict.json",
            "drex-v1": "hard_test_drex-v1-subset_line_verdict.json",
            "drex-finance": "hard_test_drex-finance-subset_line_verdict.json",
            "hybrid": "hard_test_family_hybrid_line_verdict.json",
            "billproof-system": "hard_test_billproof-system_line_verdict.json",
        }
        metrics = {
            "line_verdict": metric,
            "question_accuracy": extra.get("question_accuracy"),
            "strict_case": extra.get("strict_case"),
        }
        all_six_name = all_six_files.get(key)
        if all_six_name and (METRICS / all_six_name).exists():
            packed = pack_metric(read_json(METRICS / all_six_name), source=f"data/metrics/{all_six_name}")
            if packed is not None:
                packed["definition"] = (
                    "Earlier all-six line rule from this file: a line counts only when all six line questions match. "
                    "Not the OK-versus-dispute line_verdict."
                )
            metrics["line_all_checks"] = packed
        else:
            metrics["line_all_checks"] = {
                "value": None,
                "k": None,
                "n": None,
                "ci95": None,
                "source": None,
                "note": "No recorded all-six line file for this lane on the frozen 100.",
            }
        lanes.append(
            _lane(
                key,
                label,
                metric,
                latency_p50_ms=clock.get("latency_p50_ms"),
                latency_source=clock.get("latency_source"),
                cost_per_1000_usd=clock.get("cost_per_1000_usd"),
                cost_basis=clock.get("cost_basis"),
                cost_source=clock.get("cost_source"),
                extra=extra,
                metrics=metrics,
                primary_metric="line_verdict",
            )
        )
        if clock.get("partial"):
            lanes[-1]["partial"] = True
        strict = extra["strict_case"]
        if strict:
            secondary.append(_lane(key, label, strict, extra={"role": "strict_all_six_questions"}))
        families = {}
        for family, rates in (block.get("families") or {}).items():
            families[family] = {
                name: pack_metric(rates.get(name), source=f"{source}#lanes.{key}.families.{family}.{name}")
                for name in ("precision", "recall")
                if isinstance(rates, dict) and name in rates
            }
        breakdowns[key] = {"by_family": families}
    summary_path = METRICS / "hard_test_summary.json"
    summary = read_json(summary_path)
    slice_300 = []
    for key in ("drex-v1", "drex-finance", "gpt", "gpt-one"):
        lane = summary["lanes"][key]
        metric = pack_metric(lane["question_accuracy"], source=f"{_rel(summary_path)}#lanes.{key}.question_accuracy")
        cost_block = lane.get("cost_per_1000_usd") or {}
        basis = "list" if "list" in str(cost_block.get("note") or "").lower() or cost_block.get("stored_value") is not None else "recorded"
        slice_300.append(
            _lane(
                key,
                {"drex-v1": "Drex v1.1", "drex-finance": "Drex finance", "gpt": "GPT-6.1 Sol", "gpt-one": "GPT one-question packing"}[key],
                metric,
                latency_p50_ms=(lane.get("latency_p50_ms") or {}).get("value"),
                latency_source=f"{_rel(summary_path)}#lanes.{key}.latency_p50_ms",
                cost_per_1000_usd=cost_block.get("value"),
                cost_basis=basis,
                cost_source=f"{_rel(summary_path)}#lanes.{key}.cost_per_1000_usd",
                extra={
                    "case_accuracy": pack_metric(lane.get("case_accuracy"), source=f"{_rel(summary_path)}#lanes.{key}.case_accuracy"),
                    "cost_note": cost_block.get("note"),
                },
            )
        )
    return _bench(
        id="hard",
        title="BillProof-Hard",
        tests="Synthetic bills built so a model can miss a line. Primary score is the shared 100-case OK-versus-dispute line verdict.",
        n=document.get("n_ids"),
        split=document.get("split"),
        metric="line_verdict",
        metric_definition=document.get("rule"),
        chart={
            "metric": "line_verdict",
            "value_field": "value",
            "note": (
                "Plot lanes[].metrics.line_verdict. That is the OK-versus-dispute score from "
                "hard_outcome_<lane>_line_verdict.json. Do not plot question_accuracy or line_all_checks."
            ),
        },
        lanes=lanes,
        secondary=[{"id": "strict_case_100", "metric": "strict_case", "metric_definition": "Every question on the bill matches. Secondary to the line verdict.", "lanes": secondary},
                   {"id": "question_accuracy_300", "metric": "question_accuracy", "metric_definition": "Question accuracy on the runs in hard_test_summary.json. GPT here is the early 4-case file, not the shared 100.", "lanes": slice_300}],
        breakdowns=breakdowns,
        notes=[
            document.get("note"),
            document.get("dollars"),
            "Primary rows are the frozen 100 (ladder_hard_ids.json) except DeepSeek, whose run file stopped early and is marked partial.",
            "The OK-versus-dispute files were added after the first results. line_all_checks is the earlier all-six line score from hard_test_*_line_verdict.json on the same 100, where that file exists. Cheaper models have no recorded all-six file, so that metric is empty. strict_case stays beside both. The 300-case question-accuracy slice is a different denominator.",
            "Drex $/1,000 is the stored Drex token cost in ladder_summary.json ($0.03 per million input tokens). It is not a fresh Nace invoice.",
            "Latency is the median of recorded per-case latency_ms on the cited run. Drex answer logs repeat that call latency on each question; one copy is kept.",
            "Where ladder_summary.json has a list $/1,000 for that model, that recorded figure is used. Hybrid and the BillProof system use the mean of recorded list_cost_usd, or cost_usd when list_cost_usd is absent.",
        ],
        sources=[source, _rel(summary_path), "data/metrics/ladder_summary.json", "data/runs/ladder_hard_ids.json"],
        computed_at=None,
        added_after_first_results=True,
    )


_HARD_QUESTION = {
    "openai/gpt-6.1-sol": "hard_outcome_gpt_question_accuracy.json",
    "drex-max-v1.1": "hard_outcome_drex-v1_question_accuracy.json",
    "drex-max-finance": "hard_outcome_drex-finance_question_accuracy.json",
}
_HARD_LATENCY_KEY = {
    "openai/gpt-6.1-sol": "gpt",
    "drex-max-v1.1": "drex-v1",
    "drex-max-finance": "drex-finance",
}


def _ladder() -> dict:
    summary_path = METRICS / "ladder_summary.json"
    summary = read_json(summary_path)
    timing = _hard_latency_cost()
    hard_lanes = []
    pa_lanes = []
    for point in summary["points"]:
        bench = point["benchmark"]
        model = point["model"]
        slug = model.replace("/", "__")
        if bench == "hard":
            metric_path = METRICS / f"ladder_hard_{slug}_question_accuracy.json"
            if not metric_path.exists() and model in _HARD_QUESTION:
                metric_path = METRICS / _HARD_QUESTION[model]
            latency_path = METRICS / f"ladder_hard_{slug}_p50_latency_ms.json"
            if metric_path.exists():
                metric = pack_metric(read_json(metric_path), source=_rel(metric_path))
            else:
                metric = pack_metric(
                    {
                        "value": point["accuracy"],
                        "n": point["n"],
                        "source_run": point.get("source_run"),
                        "partial": point.get("partial"),
                        "note": "ladder_summary n is cases. No question-level k was stored for this point, so no Wilson interval is invented.",
                    },
                    source=f"{_rel(summary_path)}#points",
                )
                if metric is not None:
                    metric["k"] = None
                    metric["ci95"] = None
                    metric["ci_method"] = None
            if latency_path.exists():
                latency = read_json(latency_path)["value"]
                latency_source = _rel(latency_path)
            else:
                clock = timing.get(_HARD_LATENCY_KEY.get(model, "")) or {}
                latency = clock.get("latency_p50_ms")
                latency_source = clock.get("latency_source")
        else:
            metric_path = METRICS / f"ladder_pa_{slug}_whole_case.json"
            latency_path = METRICS / f"ladder_pa_{slug}_p50_latency_ms.json"
            if metric_path.exists():
                metric = pack_metric(read_json(metric_path), source=_rel(metric_path))
            else:
                metric = pack_metric(
                    {"value": point["accuracy"], "n": point["n"], "source_run": point.get("source_run")},
                    source=f"{_rel(summary_path)}#points",
                )
            latency = read_json(latency_path)["value"] if latency_path.exists() else None
            latency_source = _rel(latency_path) if latency_path.exists() else None
        lane = _lane(
            model,
            model,
            metric,
            latency_p50_ms=latency,
            latency_source=latency_source,
            cost_per_1000_usd=point.get("cost_per_1000_list_usd"),
            cost_basis="list",
            cost_source=f"{_rel(summary_path)}",
            extra={"ladder_summary_accuracy": point.get("accuracy"), "partial": point.get("partial"), "source_run": point.get("source_run")},
        )
        if point.get("partial"):
            lane["partial"] = True
        (hard_lanes if bench == "hard" else pa_lanes).append(lane)
    return _bench(
        id="ladder",
        title="Cheaper-LLM ladder",
        tests="Same frozen prompts as the hard bill set and a 100-case prior-auth slice, on cheaper models.",
        n=100,
        metric="question_accuracy_on_hard__whole_case_on_pa",
        metric_definition="Hard points are question accuracy. Prior-auth points are whole-case accuracy. Costs are list price from tokens, as stored in ladder_summary.json.",
        lanes=hard_lanes,
        secondary=[{"id": "pa_100", "metric": "whole_case", "metric_definition": "Whole-case accuracy on the frozen 100 prior-auth rows.", "lanes": pa_lanes}],
        breakdowns={},
        notes=[
            "This is the ladder's own metric. The hard benchmark's primary number is the OK-versus-dispute line verdict, not this question accuracy.",
            "DeepSeek is a prefix of the frozen order, marked partial. It is not a sample chosen by score.",
            "Cheaper models use ladder_hard_*_question_accuracy.json. GPT-6.1 Sol, Drex v1.1, and Drex finance use hard_outcome_*_question_accuracy.json on the same 100 cases. Their p50 latency is the per-case median from the hard answer files.",
            "If a point has no question-level file, k and the Wilson interval stay empty. ladder_summary n is a case count and is not used to recover k.",
        ],
        sources=[_rel(summary_path)],
        computed_at=summary.get("computed_at"),
    )


def _cc_queue() -> dict:
    path = DATA_DIR / "command_center" / "queue_summary.json"
    document = read_json(path)
    source = _rel(path)
    drex = document["drex"]
    gpt = document["gpt"]
    projected = gpt.get("projected") or {}
    list_path = METRICS / "cc_gpt_projected_cost_per_1000_list.json"
    list_cost = read_json(list_path) if list_path.exists() else None
    drex_metric = pack_metric(
        {"value": drex["accuracy"], "k": drex["n_correct"], "n": drex["n"], "source_run": source, "simulated": drex.get("simulated")},
        source=f"{source}#drex.accuracy",
    )
    gpt_metric = pack_metric(
        {
            "value": gpt["accuracy"],
            "k": gpt["n_correct"],
            "n": gpt["n_measured"],
            "source_run": "data/command_center/queue_gpt.jsonl",
            "simulated": gpt.get("simulated"),
        },
        source=f"{source}#gpt.accuracy",
    )
    replay = list(_iter_jsonl(DATA_DIR / "command_center" / "queue_replay.jsonl"))
    gpt_rows = list(_iter_jsonl(DATA_DIR / "command_center" / "queue_gpt.jsonl"))
    drex_latency = _median([row.get("latency_ms") for row in replay if isinstance(row, dict)])
    gpt_latency = _median([row.get("latency_ms") for row in gpt_rows if isinstance(row, dict)])
    lanes = [
        _lane(
            "drex",
            "Drex v1.1",
            drex_metric,
            latency_p50_ms=drex_latency,
            latency_source="data/command_center/queue_replay.jsonl latency_ms median" if drex_latency is not None else None,
            cost_per_1000_usd=drex.get("cost_per_1000"),
            cost_basis="recorded",
            cost_source=f"{source}#drex.cost_per_1000",
            extra={
                "cases_per_minute": drex.get("cases_per_minute"),
                "criteria_accuracy": drex.get("criteria_accuracy"),
                "model": drex.get("model"),
            },
        ),
        _lane(
            "gpt",
            "GPT-6.1 Sol measured sample",
            gpt_metric,
            latency_p50_ms=gpt_latency,
            latency_source="data/command_center/queue_gpt.jsonl latency_ms median" if gpt_latency is not None else None,
            cost_per_1000_usd=(list_cost or {}).get("value", projected.get("cost_per_1000")),
            cost_basis="list" if list_cost else "recorded",
            cost_source=_rel(list_path) if list_cost else f"{source}#gpt.projected.cost_per_1000",
            extra={
                "projected": True,
                "projected_full_queue_cost_usd": projected.get("cost_usd"),
                "projected_cases_per_minute": projected.get("cases_per_minute"),
                "mean_latency_ms": gpt.get("mean_latency_ms"),
                "adapter_projected_cost_per_1000": projected.get("cost_per_1000"),
                "note": projected.get("note"),
            },
        ),
    ]
    return _bench(
        id="cc_queue",
        title="Command Center queue",
        tests="Replay of the prior-auth queue: Drex decides every request; GPT is a measured sample scaled to the queue for time and cost.",
        n=document.get("n_planned"),
        metric="decision_accuracy",
        metric_definition="Share of queue decisions matching gold. GPT accuracy is the measured sample only and is not projected.",
        lanes=lanes,
        breakdowns={"drex": {"by_label": drex.get("by_label"), "by_colour": drex.get("by_colour")}},
        notes=[
            "GPT time and cost on the full queue are projected from the measured sample. The projection is labelled.",
            "GPT $/1,000 uses cc_gpt_projected_cost_per_1000_list.json when that file exists.",
            "Drex latency is the median of recorded queue_replay.jsonl latency_ms when that field is present.",
        ],
        sources=[source, _rel(list_path) if list_path.exists() else ""],
        computed_at=document.get("computed_at"),
    )


def _cc_rule() -> dict:
    path = DATA_DIR / "command_center" / "rule_change_summary.json"
    document = read_json(path)
    source = _rel(path)
    n_done = int(document.get("n_done") or 0)
    n_flips = int(document.get("n_flips") or 0)
    metric = pack_metric(
        {
            "value": (n_flips / n_done) if n_done else None,
            "k": n_flips,
            "n": n_done,
            "source_run": source,
            "simulated": True,
            "note": document.get("description"),
        },
        source=f"{source}#n_flips",
    )
    lane = _lane(
        "simulated_and",
        "Simulated OR to AND",
        metric,
        cost_basis=None,
        extra={"n_flips": n_flips, "flip_counts": document.get("flip_counts"), "pair_kind": document.get("pair_kind")},
    )
    return _bench(
        id="cc_rule_change",
        title="Command Center rule change",
        tests="What flips if every published OR list is tightened to AND, using the same recorded Drex facts.",
        n=document.get("n_done"),
        metric="flip_rate",
        metric_definition="Share of affected requests whose decision changes when the combining rule changes. k is the flip count. Not an accuracy.",
        lanes=[lane],
        breakdowns={"flip_counts": document.get("flip_counts"), "search": document.get("search")},
        notes=[
            "Simulated. There was no real before/after policy pair.",
            document.get("description"),
        ],
        sources=[source],
        computed_at=document.get("computed_at"),
        simulated=True,
    )


def _cc_decides() -> dict:
    path = DATA_DIR / "command_center" / "what_decides_summary.json"
    document = read_json(path)
    source = _rel(path)
    jsonl = DATA_DIR / "command_center" / "what_decides.jsonl"
    latencies = [row.get("latency_ms") for row in _iter_jsonl(jsonl) if isinstance(row, dict)]
    latency = _median(latencies)
    metric = pack_metric(
        {
            "value": document.get("agreement"),
            "k": document.get("n_agree"),
            "n": document.get("n_scored"),
            "source_run": source,
            "simulated": document.get("simulated"),
        },
        source=f"{source}#agreement",
    )
    lane = _lane(
        "drex",
        "Drex what-decides",
        metric,
        latency_p50_ms=latency,
        latency_source=f"{_rel(jsonl)} latency_ms median" if latency is not None else None,
        cost_per_1000_usd=(document["total_cost_usd"] / document["n_scored"] * 1000.0) if document.get("n_scored") else None,
        cost_basis="recorded",
        cost_source=f"{source}#total_cost_usd",
    )
    return _bench(
        id="cc_what_decides",
        title="Command Center what-decides",
        tests="Does the record Drex names as deciding match the gold deciding record?",
        n=document.get("n_scored"),
        metric="agreement",
        metric_definition="Share of scored requests where the named deciding record agrees with gold.",
        lanes=[lane],
        breakdowns={"by_kind": document.get("agreement_by_kind")},
        notes=[
            "Not a simulated run. Cost is total_cost_usd divided by n_scored, times 1000.",
            "p50 latency is the median of latency_ms on what_decides.jsonl. The summary file does not store a median.",
        ],
        sources=[source],
        computed_at=document.get("computed_at"),
    )


def _full_chain() -> dict:
    rows = []
    costs = []
    latencies = []
    matches = []
    gpt_matches = []
    gpt_costs = []
    for slug in ("PA-D1", "PA-D2", "PA-D3", "PA-D4", "PA-D5"):
        path = DATA_DIR / "pa_demo" / slug / "evidence.json"
        document = read_json(path)
        gold = document.get("gold_decision")
        decision = document.get("decision")
        matches.append(decision == gold)
        costs.append(document.get("total_cost_usd"))
        timings = document.get("timings") or {}
        if timings.get("drex_ms") is not None:
            latencies.append(timings.get("drex_ms"))
        gpt = (document.get("gpt_check") or {}).get("gpt_decision")
        gpt_matches.append(gpt == gold)
        gpt_costs.append((document.get("costs") or {}).get("gpt"))
        rows.append({"case_id": slug, "gold": gold, "decision": decision, "gpt_decision": gpt, "total_cost_usd": document.get("total_cost_usd")})
    k = sum(1 for flag in matches if flag)
    n = len(rows)
    lo, hi = wilson_ci(k, n)
    gpt_k = sum(1 for flag in gpt_matches if flag)
    glo, ghi = wilson_ci(gpt_k, n)
    total = sum(float(c) for c in costs if c is not None)
    gpt_total = sum(float(c) for c in gpt_costs if c is not None)
    chain = pack_metric(
        {"value": k / n if n else None, "k": k, "n": n, "ci_low": lo, "ci_high": hi, "ci_method": "wilson", "source_run": "data/pa_demo/PA-D1..PA-D5/evidence.json"},
        source="data/pa_demo/*/evidence.json decision==gold_decision",
    )
    gpt = pack_metric(
        {"value": gpt_k / n if n else None, "k": gpt_k, "n": n, "ci_low": glo, "ci_high": ghi, "ci_method": "wilson", "source_run": "data/pa_demo/*/evidence.json#gpt_check"},
        source="data/pa_demo/*/evidence.json gpt_check.gpt_decision==gold_decision",
    )
    return _bench(
        id="full_chain",
        title="Full-chain demo",
        tests="Five scripted prior-auth packets: public policy page, NDI boxes, search, Drex, letter.",
        n=n,
        metric="decision_matches_gold",
        metric_definition="Share of the five evidence files whose top-level decision equals gold_decision.",
        lanes=[
            _lane(
                "chain",
                "BillProof chain",
                chain,
                latency_p50_ms=_median(latencies),
                latency_source="data/pa_demo/*/evidence.json timings.drex_ms median",
                cost_per_1000_usd=None,
                cost_basis="five-case total",
                cost_source="data/pa_demo/*/evidence.json total_cost_usd",
                extra={"total_cost_usd": total, "n_cases": n},
            ),
            _lane(
                "gpt_check",
                "GPT check",
                gpt,
                cost_per_1000_usd=None,
                cost_basis="five-case total",
                cost_source="data/pa_demo/*/evidence.json costs.gpt",
                extra={"total_cost_usd": gpt_total, "n_cases": n},
            ),
        ],
        breakdowns={"cases": rows},
        notes=[
            "Five hand-built demo packets, not a random sample. The Wilson interval is wide because n=5.",
            "The table shows the five-case recorded total, not a scaled $/1,000. Scaling five demo packets would read as about $100 per thousand.",
            "Drex latency is timings.drex_ms only. Browser, NDI, and search seconds are in the same timings object and are not folded into this median.",
        ],
        sources=["data/pa_demo/PA-D1/evidence.json through PA-D5/evidence.json"],
        computed_at=None,
    )


def _attach_qcombo(bench: dict) -> None:
    """Add recorded DeepInfra alone lanes and Drex-then-that-model lanes from qcombo_*.json."""
    loaded = []
    for path in METRICS.glob(f"qcombo_{bench['id']}_*.json"):
        try:
            document = read_json(path)
        except json.JSONDecodeError:
            continue
        if document.get("bench") != bench["id"] or not document.get("n_cases"):
            continue
        loaded.append((99 if document.get("order") is None else int(document["order"]), path, document))
    if not loaded:
        return
    for _, path, document in sorted(loaded, key=lambda item: item[0]):
        source = _rel(path)
        chart = document.get("chart") or bench.get("metric")
        for role in ("model", "combo"):
            lane_doc = (document.get("lanes") or {}).get(role)
            if not isinstance(lane_doc, dict) or not lane_doc.get("id"):
                continue
            blocks = {}
            for name, block in (lane_doc.get("metrics") or {}).items():
                packed = pack_metric(block, source=f"{source}#lanes.{role}.metrics.{name}")
                if packed is not None:
                    blocks[name] = packed
            primary = blocks.get(chart) if chart else None
            if primary is None:
                primary = pack_metric(lane_doc, source=source)
            lane = _lane(
                lane_doc["id"],
                lane_doc.get("label") or lane_doc["id"],
                primary,
                latency_p50_ms=lane_doc.get("latency_p50_ms"),
                latency_source=source,
                cost_per_1000_usd=lane_doc.get("cost_per_1000_usd"),
                cost_basis=lane_doc.get("cost_basis"),
                cost_source=source,
                extra={
                    "provider": "deepinfra",
                    "model": document.get("model"),
                    "escalate_model": lane_doc.get("escalate_model"),
                    "threshold": lane_doc.get("threshold"),
                    "n_cases": document.get("n_cases"),
                    "n_expected": document.get("n_expected"),
                    "questions_routed": lane_doc.get("questions_routed"),
                    "questions_routed_share": lane_doc.get("questions_routed_share"),
                    "cases_routed": lane_doc.get("cases_routed"),
                    "paired_drex": lane_doc.get("paired_drex"),
                    "first_missing": document.get("first_missing"),
                    "cost_parts_per_1000_usd": lane_doc.get("cost_parts_per_1000_usd"),
                },
                metrics=blocks or None,
                primary_metric=chart,
            )
            lane["provider"] = "deepinfra"
            if lane_doc.get("escalate_model"):
                lane["escalate_model"] = lane_doc["escalate_model"]
            bench["lanes"].append(lane)
        if source not in (bench.get("sources") or []):
            bench.setdefault("sources", []).append(source)
    bench.setdefault("notes", []).append(
        "Qwen and DeepSeek lanes are recorded DeepInfra answers on the longest prefix of the frozen order "
        "where Drex also has an answer. drex_then_* keeps Drex when recorded confidence is at least 0.90 and "
        "otherwise uses that model's recorded answer. t=0.90 was not tuned: these rows are the reported set, "
        "and no DeepInfra run exists on a separate dev or practice split. partial means the prefix is short of the frozen n. "
        "Combo $/1,000 is Drex's recorded cost plus the recorded DeepInfra cost scaled to the routed share of the packed call."
    )


def build_summary() -> dict:
    combo = _persius_combo()
    benchmarks = [
        _synthetic(),
        _pa(),
        _persius(),
        *([combo] if combo else []),
        _hard(),
        _ladder(),
        _cc_queue(),
        _cc_rule(),
        _cc_decides(),
        _full_chain(),
    ]
    for bench in benchmarks:
        if bench.get("id") in ("hard", "pa_criteria_gym", "persius"):
            _attach_qcombo(bench)
    for bench in benchmarks:
        chart = bench.get("chart") or {
            "metric": bench.get("metric"),
            "value_field": "value",
            "note": "Plot lanes[].metrics[chart.metric]. lanes[].value is that same number.",
        }
        bench["chart"] = chart
        metric_name = chart.get("metric")
        for group in [bench.get("lanes") or []]:
            for lane in group:
                if lane.get("metrics"):
                    continue
                lane["metrics"] = {
                    metric_name: {
                        "value": lane.get("value"),
                        "k": lane.get("k"),
                        "n": lane.get("n"),
                        "ci95": lane.get("ci95"),
                        "ci_method": lane.get("ci_method"),
                        "source": lane.get("source"),
                    }
                }
                lane["primary_metric"] = metric_name
    return {
        "generated_by": "billproof.results.catalog",
        "generated_at": _now(),
        "price_basis": "GPT-6.1 Sol list price is $2 input and $10 output per 1M tokens when a source file stores that figure or stores token counts. Other model costs are the list figure in ladder_summary.json when present. Drex $/1,000 on the hard and ladder tables is the stored Drex token cost in ladder_summary.json. A cost_basis other than list is labelled on the lane.",
        "benchmarks": benchmarks,
    }


def load_summary() -> dict:
    if not SUMMARY_PATH.exists():
        raise FileNotFoundError(SUMMARY_PATH)
    return read_json(SUMMARY_PATH)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _fmt_ci(ci: Any) -> str:
    if not isinstance(ci, list) or len(ci) != 2 or ci[0] is None:
        return "—"
    return f"{float(ci[0]) * 100:.1f}–{float(ci[1]) * 100:.1f}%"


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.0f} ms"


def _lane_row(lane: dict) -> str:
    unit = lane.get("value_unit")
    if unit == "flips":
        shown = f"{lane.get('k')} / {lane.get('n')}"
        ci = "—"
    else:
        shown = _fmt_pct(lane.get("value"))
        if lane.get("k") is not None and lane.get("n") is not None:
            shown += f" ({lane.get('k')}/{lane.get('n')})"
        ci = _fmt_ci(lane.get("ci95"))
    flags = []
    if lane.get("partial"):
        flags.append("partial")
    if lane.get("projected"):
        flags.append("projected")
    if lane.get("simulated"):
        flags.append("simulated")
    if lane.get("added_after_first_results"):
        flags.append("added after first results")
    flag = ", ".join(flags)
    extra = lane.get("extra") or {}
    if lane.get("cost_per_1000_usd") is None and extra.get("total_cost_usd") is not None:
        cost_cell = f"{float(extra['total_cost_usd']):.5f} total"
    else:
        cost_cell = _fmt_num(lane.get("cost_per_1000_usd"))
    return (
        f"| {lane.get('label')} | {shown} | {ci} | {_fmt_ms(lane.get('latency_p50_ms'))} | "
        f"{cost_cell} | {lane.get('cost_basis') or '—'} | {flag or '—'} |"
    )


def render_markdown(document: dict) -> str:
    lines = [
        "# BillProof results",
        "",
        "Every number below is copied from a recorded metric file or reduced from recorded run rows "
        "(median latency, mean of a stored cost, Wilson interval from a stored k and n). "
        "Nothing here was typed in by hand.",
        "",
        f"Generated {document.get('generated_at')} by `{document.get('generated_by')}`.",
        "",
        document.get("price_basis") or "",
        "",
    ]
    for bench in document["benchmarks"]:
        lines.append(f"## {bench['title']}")
        lines.append("")
        lines.append(bench["tests"])
        lines.append("")
        lines.append(f"- id: `{bench['id']}`")
        lines.append(f"- n: {bench.get('n')}")
        lines.append(f"- metric: `{bench.get('metric')}` — {bench.get('metric_definition')}")
        chart = bench.get("chart") or {}
        if chart.get("metric"):
            lines.append(f"- chart: `{chart.get('metric')}` — {chart.get('note')}")
        if bench.get("split"):
            lines.append(f"- split: {bench.get('split')}")
        lines.append("")
        lines.append("| Lane | Value | 95% CI | p50 latency | $/1,000 | Cost basis | Flags |")
        lines.append("|---|---|---|---|---|---|---|")
        for lane in bench["lanes"]:
            lines.append(_lane_row(lane))
        lines.append("")
        primary = (bench.get("chart") or {}).get("metric") or bench.get("metric")
        extra_keys: list[str] = []
        for lane in bench["lanes"]:
            for key in (lane.get("metrics") or {}):
                if key != primary and key not in extra_keys:
                    extra_keys.append(key)
        for key in extra_keys:
            lines.append(f"### {key}")
            lines.append("")
            lines.append("Recorded on the same lanes. Not the chart metric.")
            lines.append("")
            lines.append("| Lane | Value | 95% CI | Source |")
            lines.append("|---|---|---|---|")
            for lane in bench["lanes"]:
                block = (lane.get("metrics") or {}).get(key) or {}
                shown = _fmt_pct(block.get("value"))
                if block.get("k") is not None and block.get("n") is not None:
                    shown += f" ({block.get('k')}/{block.get('n')})"
                lines.append(
                    f"| {lane.get('label')} | {shown} | {_fmt_ci(block.get('ci95'))} | {block.get('source') or '—'} |"
                )
            lines.append("")
        for extra in bench.get("secondary") or []:
            lines.append(f"### {extra['id']}")
            lines.append("")
            lines.append(extra.get("metric_definition") or "")
            lines.append("")
            lines.append("| Lane | Value | 95% CI | p50 latency | $/1,000 | Cost basis | Flags |")
            lines.append("|---|---|---|---|---|---|---|")
            for lane in extra["lanes"]:
                lines.append(_lane_row(lane))
            lines.append("")
        if bench.get("mcnemar"):
            lines.append(f"McNemar: `{json.dumps(bench['mcnemar'])}`")
            lines.append("")
        lines.append("Notes:")
        lines.append("")
        for note in bench.get("notes") or []:
            if note:
                lines.append(f"- {note}")
        lines.append("")
        lines.append("Sources: " + ", ".join(f"`{item}`" for item in bench.get("sources") or [] if item))
        lines.append("")
    lines.append("## Case explorer")
    lines.append("")
    lines.append("`GET /api/results/summary` returns this document.")
    lines.append("`GET /api/results/{bench}` returns one benchmark, including breakdowns.")
    lines.append("`GET /api/results/{bench}/cases?lane=&only_wrong=&offset=&limit=` pages cases.")
    lines.append("`GET /api/results/{bench}/case/{id}` returns one case.")
    lines.append("")
    return "\n".join(lines)

"""Drex-first, DeepInfra-if-unsure, from recorded answers. No model calls.

Drex keeps a question when its recorded confidence is >= t. Every other question
on that case takes the DeepInfra model's recorded answer. The case set is the
longest prefix of the frozen order where both Drex and that model have an
answer. A shorter prefix is marked partial.

t is 0.90. These files are the reported benches (Persius test 400, the shared
prior-auth 370, Hard frozen 100). No DeepInfra run sits on a separate dev or
practice split, so t is not chosen on these rows. The dial file is the curve.
"""

from __future__ import annotations

import copy
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from billproof.adapters.frontier import _deepinfra_list
from billproof.bench.metrics import wilson_ci
from billproof.config import DATA_DIR
from billproof.dial.deepinfra import _apply, _routed_cost
from billproof.dial.simulate import (
    BENCHES,
    SIMULATED,
    THRESHOLDS,
    _cheapest_match,
    _score,
    cases_for,
    global_router,
)

METRICS = DATA_DIR / "metrics"
RUNS = DATA_DIR / "runs"
DEFAULT_T = 0.90
ACTIVE = ("hard", "pa_criteria_gym", "persius")
SHORT = {"hard": "hard", "pa_criteria_gym": "pa", "persius": "persius"}
EXPECTED = {"hard": 100, "pa_criteria_gym": 370, "persius": 400}

# Stage order: Qwen3.8-Max, then 27B, then DeepSeek V4.1 Flash.
MODELS = (
    {
        "model": "Qwen/Qwen3.8-Max",
        "order": 0,
        "alone_id": "qwen38max",
        "combo_id": "drex_then_qwen38max",
        "alone_label": "Qwen3.8-Max via DeepInfra",
        "combo_label": "Drex then Qwen3.8-Max",
    },
    {
        "model": "Qwen/Qwen3.8-27B",
        "order": 1,
        "alone_id": "qwen3827b",
        "combo_id": "drex_then_qwen3827b",
        "alone_label": "Qwen3.8-27B via DeepInfra",
        "combo_label": "Drex then Qwen3.8-27B",
    },
    {
        "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "order": 2,
        "alone_id": "deepseekv41flash",
        "combo_id": "drex_then_deepseekv41flash",
        "alone_label": "DeepSeek V4.1 Flash via DeepInfra",
        "combo_label": "Drex then DeepSeek V4.1 Flash",
    },
)

THRESHOLD_NOTE = (
    "t=0.90 was not tuned. Persius rows are the frozen 400 test, Hard rows are the frozen 100, "
    "and the 370 prior-auth rows are the shared held-out set this bench reports. "
    "No DeepInfra answers exist on a separate dev or practice split. "
    "The dial file holds the curve from 0.50 to 1.00. "
    "post_hoc_on_reported_rows is the cheapest point within 1 point of the escalation model "
    "on these same rows. It is not the operating point."
)

COST_FORMULA = (
    "cost per 1,000 cases = mean(Drex recorded cost_usd + routed DeepInfra cost) * 1000. "
    "Routed cost is 0 when no question is sent. Otherwise it is the recorded packed call, "
    "scaled as (list input dollars + list output dollars * routed/asked) * "
    "(recorded cost_usd / list-price of that call) when cost_basis is provider_estimated_cost "
    "and both the recorded cost and the list price are positive. "
    "List rates are DeepInfra's published cents_per_token figures from _deepinfra_list. "
    "If the provider did not send estimated_cost, the scale is 1 and the list rate is used. "
    "If no list rate is known, the recorded cost_usd is scaled by routed/asked. "
    "The escalation model alone uses mean(recorded cost_usd) * 1000 and no Drex cost. "
    "Drex alone uses mean(recorded Drex cost_usd) * 1000 and no DeepInfra cost. "
    "On prior auth, a case with any routed question takes the escalation model's recorded "
    "decision and the other case-level parts; Drex keeps those when nothing is routed."
)

LATENCY_FORMULA = (
    "p50 latency is the median over paired cases of "
    "[Drex recorded latency_ms + DeepInfra recorded latency_ms * routed/asked when any question is routed]. "
    "Drex alone is the median Drex latency. The escalation model alone is the median of its recorded latency."
)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(model: str) -> str:
    return model.replace("/", "__").replace(" ", "_")


def spec_for(model: str) -> dict:
    for spec in MODELS:
        if spec["model"] == model:
            return spec
    raise KeyError(model)


def source_path(bench: str, model: str) -> Path:
    return RUNS / f"di_{SHORT[bench]}_{slug(model)}.jsonl"


def qcombo_path(bench: str, model: str) -> Path:
    return METRICS / f"qcombo_{bench}_{slug(model)}.json"


def dial_path(bench: str, model: str) -> Path:
    return METRICS / f"dial_{bench}_{slug(model)}.json"


def prefix_ids(order: list[str], have: set[str]) -> list[str]:
    """Longest leading run of `order` whose ids are all in `have`."""
    kept: list[str] = []
    for case_id in order:
        if case_id not in have:
            break
        kept.append(case_id)
    return kept


def frozen_order(bench: str) -> list[str]:
    if bench == "hard":
        payload = json.loads((RUNS / "ladder_hard_ids.json").read_text())
        order = list(payload["run_order"])
        if len(order) != EXPECTED["hard"]:
            raise RuntimeError(f"expected {EXPECTED['hard']} hard ids, found {len(order)}")
        return order
    if bench == "pa_criteria_gym":
        order: list[str] = []
        seen: set[str] = set()
        path = RUNS / "pa_hybrid_development.jsonl"
        for row in _iter_jsonl(path):
            case_id = row.get("case_id")
            if case_id and case_id not in seen:
                seen.add(case_id)
                order.append(case_id)
        if len(order) != EXPECTED["pa_criteria_gym"]:
            raise RuntimeError(f"expected {EXPECTED['pa_criteria_gym']} PA ids, found {len(order)}")
        return order
    if bench == "persius":
        payload = json.loads((RUNS / "c_persius_test_ids.json").read_text())
        order = [item["case_id"] for item in payload["items"]]
        if len(order) != EXPECTED["persius"]:
            raise RuntimeError(f"expected {EXPECTED['persius']} Persius ids, found {len(order)}")
        return order
    raise KeyError(bench)


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def last_ok(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        if not case_id or row.get("error") or row.get("simulated"):
            continue
        found[case_id] = row
    return found


def _with_ci(metrics: dict, source: str, *, partial: bool, simulated: bool) -> dict:
    out = {}
    for name, block in metrics.items():
        k, n = int(block["k"]), int(block["n"])
        lo, hi = wilson_ci(k, n) if n else (None, None)
        out[name] = {
            "value": block["value"],
            "k": k,
            "n": n,
            "ci95": [lo, hi] if n else None,
            "ci_method": "wilson" if n else None,
            "source_run": source,
            "partial": partial,
            "simulated": simulated,
        }
    return out


def _overlay(bench: str, case: dict, row: dict) -> dict:
    copied = copy.deepcopy(case)
    _apply(bench, copied, row)
    return copied


def paired_cases(bench: str, model: str) -> tuple[list[dict], dict]:
    """Prefix of the frozen order where Drex and this model both have an answer."""
    if bench not in ACTIVE:
        raise KeyError(bench)
    order = frozen_order(bench)
    by_id = {case["case_id"]: case for case in cases_for(bench)}
    rows = last_ok(source_path(bench, model))
    both = set(by_id) & set(rows)
    ids = prefix_ids(order, both)
    first_missing = next((case_id for case_id in order if case_id not in both), None)
    meta = {
        "n_frozen": len(order),
        "n_qwen_ok": sum(1 for case_id in order if case_id in rows),
        "n_drex": sum(1 for case_id in order if case_id in by_id),
        "n_both_anywhere": sum(1 for case_id in order if case_id in both),
        "n_prefix": len(ids),
        "first_missing": first_missing,
        "partial": len(ids) < len(order),
    }
    cases = [_overlay(bench, by_id[case_id], rows[case_id]) for case_id in ids]
    return cases, meta


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _mean_per_1000(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values)) * 1000.0


def summarize(bench: str, cases: list[dict], threshold: float | None, *, routed: str) -> dict:
    """routed is 'none' (Drex), 'all' (escalation model), or 'threshold'."""
    if routed == "none":
        picks = [[False] * len(case["questions"]) for case in cases]
    elif routed == "all":
        picks = [[True] * len(case["questions"]) for case in cases]
    else:
        route = global_router(threshold if threshold is not None else DEFAULT_T)
        picks = [[bool(route(question)) for question in case["questions"]] for case in cases]
    metrics = _score(bench, cases, picks) if cases else {name: {"value": None, "k": 0, "n": 0} for name in BENCHES[bench]["metrics"]}
    drex_costs: list[float] = []
    di_costs: list[float] = []
    latencies: list[float] = []
    for case, row in zip(cases, picks):
        n_routed, asked = sum(row), len(row)
        if routed == "all":
            drex_costs.append(0.0)
            di_costs.append(float(case.get("di_cost_usd") or 0.0))
            latencies.append(float(case.get("di_ms") or 0.0))
        elif routed == "none":
            drex_costs.append(float(case["drex_cost_usd"]))
            di_costs.append(0.0)
            latencies.append(float(case["drex_ms"]))
        else:
            drex_costs.append(float(case["drex_cost_usd"]))
            di_costs.append(_routed_cost(case, n_routed, asked))
            extra = float(case.get("di_ms") or 0.0) * n_routed / asked if n_routed and asked else 0.0
            latencies.append(float(case["drex_ms"]) + extra)
    q_routed = sum(sum(row) for row in picks)
    q_total = sum(len(row) for row in picks)
    c_routed = sum(1 for row in picks if any(row))
    n = len(cases)
    chart = BENCHES[bench]["chart"]
    return {
        "threshold": None if routed != "threshold" else threshold,
        "router": "global" if routed == "threshold" else None,
        "label": SIMULATED if routed == "threshold" else "recorded",
        "value": metrics.get(chart, {}).get("value"),
        "metrics": metrics,
        "questions_routed": {
            "k": q_routed,
            "n": q_total,
            "share": (q_routed / q_total) if q_total else None,
        },
        "cases_routed": {"k": c_routed, "n": n, "share": (c_routed / n) if n else None},
        "cost_per_1000_usd": _mean_per_1000([a + b for a, b in zip(drex_costs, di_costs)]),
        "cost_parts_per_1000_usd": {
            "drex": _mean_per_1000(drex_costs),
            "deepinfra": _mean_per_1000(di_costs),
        },
        "latency_p50_ms": _median(latencies),
        "n_cases": n,
    }


def _lane_body(
    spec: dict,
    role: str,
    point: dict,
    *,
    bench: str,
    source: str,
    partial: bool,
    n_expected: int,
    model: str,
) -> dict:
    simulated = role == "combo"
    metrics = _with_ci(point["metrics"], source, partial=partial, simulated=simulated)
    identity = spec["combo_id"] if role == "combo" else (spec["alone_id"] if role == "model" else "drex")
    label = spec["combo_label"] if role == "combo" else (spec["alone_label"] if role == "model" else "Drex v1.1")
    chart_name = BENCHES[bench]["chart"]
    primary = metrics.get(chart_name) or {}
    body = {
        "id": identity,
        "label": label,
        "role": role,
        "model": model if role != "drex" else "drex-max-v1.1",
        "provider": "deepinfra" if role != "drex" else "nace",
        "escalate_model": model if role == "combo" else None,
        "threshold": point.get("threshold"),
        "partial": partial,
        "simulated": simulated,
        "value": primary.get("value"),
        "k": primary.get("k"),
        "n": primary.get("n"),
        "ci95": primary.get("ci95"),
        "ci_method": primary.get("ci_method"),
        "metrics": metrics,
        "n_cases": point["n_cases"],
        "n_expected": n_expected,
        "questions_routed": point["questions_routed"],
        "questions_routed_share": point["questions_routed"]["share"],
        "cases_routed": point["cases_routed"],
        "cost_per_1000_usd": point["cost_per_1000_usd"],
        "cost_parts_per_1000_usd": point["cost_parts_per_1000_usd"],
        "cost_basis": _cost_basis(role),
        "latency_p50_ms": point["latency_p50_ms"],
        "source_run": source,
    }
    return body


def _cost_basis(role: str) -> str:
    if role == "drex":
        return "recorded"
    if role == "model":
        return "provider_estimated_cost or list"
    return "recorded drex + routed deepinfra"


def curve_point(bench: str, cases: list[dict], threshold: float, model: str, *, with_ids: bool = False) -> dict:
    point = summarize(bench, cases, threshold, routed="threshold")
    point["escalate"] = "qwen"
    point["escalate_model"] = model
    point["via"] = "DeepInfra"
    point["provider"] = "deepinfra"
    point["bench"] = bench
    point["chart"] = BENCHES[bench]["chart"]
    if with_ids:
        route = global_router(threshold)
        point["routed_by_case"] = [
            {
                "case_id": case["case_id"],
                "asked": len(case["questions"]),
                "routed": [question["id"] for question in case["questions"] if route(question)],
            }
            for case in cases
        ]
    return point


def _only_point(bench: str, cases: list[dict], model: str, *, role: str) -> dict:
    point = summarize(bench, cases, None, routed="all" if role == "model" else "none")
    point["escalate_model"] = model if role == "model" else None
    point["via"] = "DeepInfra" if role == "model" else None
    point["provider"] = "deepinfra" if role == "model" else "nace"
    point["chart"] = BENCHES[bench]["chart"]
    return point


def documents(bench: str, model: str) -> tuple[dict, dict]:
    spec = spec_for(model)
    if bench not in ACTIVE:
        raise KeyError(bench)
    cases, meta = paired_cases(bench, model)
    source = f"data/runs/{source_path(bench, model).name}"
    partial = bool(meta["partial"])
    chart = BENCHES[bench]["chart"]
    drex = _lane_body(spec, "drex", _only_point(bench, cases, model, role="drex"), bench=bench, source=source, partial=partial, n_expected=meta["n_frozen"], model=model)
    alone = _lane_body(spec, "model", _only_point(bench, cases, model, role="model"), bench=bench, source=source, partial=partial, n_expected=meta["n_frozen"], model=model)
    combo_point = summarize(bench, cases, DEFAULT_T, routed="threshold")
    combo = _lane_body(spec, "combo", combo_point, bench=bench, source=source, partial=partial, n_expected=meta["n_frozen"], model=model)
    combo["paired_drex"] = {
        "value": drex["value"],
        "k": drex["k"],
        "n": drex["n"],
        "ci95": drex["ci95"],
        "n_cases": drex["n_cases"],
        "cost_per_1000_usd": drex["cost_per_1000_usd"],
        "latency_p50_ms": drex["latency_p50_ms"],
    }
    listed = _deepinfra_list(model) or {}
    qcombo = {
        "bench": bench,
        "di_bench": SHORT[bench],
        "title": BENCHES[bench]["title"],
        "chart": chart,
        "metrics": BENCHES[bench]["metrics"],
        "model": model,
        "provider": "deepinfra",
        "via": "DeepInfra",
        "order": spec["order"],
        "alone_id": spec["alone_id"],
        "combo_id": spec["combo_id"],
        "threshold": DEFAULT_T,
        "threshold_selection": {
            "method": "fixed",
            "threshold": DEFAULT_T,
            "tuned_on": None,
            "note": THRESHOLD_NOTE,
        },
        "partial": partial,
        "n_cases": meta["n_prefix"],
        "n_expected": meta["n_frozen"],
        "n_qwen_ok": meta["n_qwen_ok"],
        "n_both_anywhere": meta["n_both_anywhere"],
        "first_missing": meta["first_missing"],
        "source_run": source,
        "list_rate_usd_per_mtok": {
            "input": listed.get("input_usd_per_mtok"),
            "output": listed.get("output_usd_per_mtok"),
        },
        "formulas": {
            "rule": (
                "Drex keeps every question with recorded confidence >= 0.90. "
                "The rest take this model's recorded answer to the same question."
            ),
            "cases": (
                "Only cases in the longest prefix of the frozen order where both Drex and this model "
                "have a non-error recorded answer. partial is true when that prefix is shorter than the frozen set."
            ),
            "cost_per_1000_usd": COST_FORMULA,
            "latency_p50_ms": LATENCY_FORMULA,
        },
        "lanes": {"drex": drex, "model": alone, "combo": combo},
        "computed_at": now(),
        "generator": "billproof.dial.qcombo",
    }
    curve = []
    for threshold in THRESHOLDS:
        point = curve_point(bench, cases, threshold, model)
        metrics = _with_ci(point["metrics"], source, partial=partial, simulated=True)
        point["metrics"] = metrics
        point["value"] = (metrics.get(chart) or {}).get("value")
        point["partial"] = partial
        point["n_cases"] = meta["n_prefix"]
        curve.append(point)
    qwen_only = _only_point(bench, cases, model, role="model")
    qwen_only["metrics"] = _with_ci(qwen_only["metrics"], source, partial=partial, simulated=False)
    qwen_only["value"] = (qwen_only["metrics"].get(chart) or {}).get("value")
    drex_only = _only_point(bench, cases, model, role="drex")
    drex_only["metrics"] = _with_ci(drex_only["metrics"], source, partial=partial, simulated=False)
    drex_only["value"] = (drex_only["metrics"].get(chart) or {}).get("value")
    operating = next(point for point in curve if point["threshold"] == DEFAULT_T)
    observed = _cheapest_match(qwen_only, curve, chart) if cases else None
    if observed is not None:
        observed["note"] = "Scored on the same rows. Not used as the operating point."
        observed["rule"] = (
            "lowest simulated cost among points whose chart metric is within 1 point of this model alone. "
            "Reported only as a post-hoc description of these rows."
        )
    dial = {
        "bench": bench,
        "title": BENCHES[bench]["title"],
        "chart": chart,
        "metrics": BENCHES[bench]["metrics"],
        "n_cases": meta["n_prefix"],
        "n_expected": meta["n_frozen"],
        "n_questions": sum(len(case["questions"]) for case in cases),
        "partial": partial,
        "first_missing": meta["first_missing"],
        "escalate": "qwen",
        "escalate_model": model,
        "via": "DeepInfra",
        "provider": "deepinfra",
        "source_run": source,
        "label": SIMULATED,
        "threshold": DEFAULT_T,
        "threshold_selection": qcombo["threshold_selection"],
        "formulas": qcombo["formulas"],
        "thresholds": THRESHOLDS,
        "curve": curve,
        "qwen_only": qwen_only,
        "drex_only": drex_only,
        "match_qwen_within_1pt": {
            "threshold": DEFAULT_T,
            "router": "global",
            "value": operating["value"],
            "cost_per_1000_usd": operating["cost_per_1000_usd"],
            "questions_routed_share": operating["questions_routed"]["share"],
            "post_hoc": (
                "Operating point t=0.90. Not tuned: no DeepInfra answers on a dev or practice split. "
                "The star is this fixed point, not a search over the reported rows."
            ),
            "label": SIMULATED,
        },
        "post_hoc_on_reported_rows": observed,
        "computed_at": qcombo["computed_at"],
        "generator": "billproof.dial.qcombo",
    }
    return qcombo, dial


def write_pair(bench: str, model: str) -> dict | None:
    path = source_path(bench, model)
    if not path.exists():
        return None
    qcombo, dial = documents(bench, model)
    if qcombo["n_cases"] == 0:
        qcombo_path(bench, model).unlink(missing_ok=True)
        dial_path(bench, model).unlink(missing_ok=True)
        return qcombo
    _write(qcombo_path(bench, model), qcombo)
    _write(dial_path(bench, model), dial)
    return qcombo


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, indent=2) + "\n")
    tmp.replace(path)


def point_at(bench: str, threshold: float, model: str = MODELS[0]["model"]) -> dict:
    spec_for(model)
    if bench not in ACTIVE:
        raise KeyError(bench)
    cases, meta = paired_cases(bench, model)
    if not cases:
        raise FileNotFoundError(
            f"{source_path(bench, model).name} has no paired prefix yet "
            f"(qwen ok on frozen order: {meta['n_qwen_ok']}, first missing {meta['first_missing']})"
        )
    point = curve_point(bench, cases, round(threshold, 2), model, with_ids=True)
    source = f"data/runs/{source_path(bench, model).name}"
    point["metrics"] = _with_ci(point["metrics"], source, partial=bool(meta["partial"]), simulated=True)
    chart = BENCHES[bench]["chart"]
    point["value"] = (point["metrics"].get(chart) or {}).get("value")
    point["partial"] = bool(meta["partial"])
    point["n_cases"] = meta["n_prefix"]
    point["n_expected"] = meta["n_frozen"]
    return point


def build_all() -> list[dict]:
    written = []
    for spec in MODELS:
        for bench in ACTIVE:
            document = write_pair(bench, spec["model"])
            if document is not None:
                written.append(document)
    return written


def format_table(documents: list[dict]) -> str:
    lines = [
        "| Bench | Model | Lane | Metric | k/n | Wilson 95% | Routed | $/1,000 | p50 ms | Flags |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    order = {spec["model"]: spec["order"] for spec in MODELS}
    bench_order = {name: index for index, name in enumerate(ACTIVE)}
    role_order = {"drex": 0, "model": 1, "combo": 2}
    rows = []
    for document in documents:
        if not document.get("n_cases"):
            continue
        for role in ("drex", "model", "combo"):
            lane = document["lanes"][role]
            rows.append((bench_order[document["bench"]], order[document["model"]], role_order[role], document, lane))
    for _, _, _, document, lane in sorted(rows):
        ci = lane.get("ci95") or [None, None]
        ci_text = "—" if ci[0] is None else f"{ci[0] * 100:.1f}–{ci[1] * 100:.1f}%"
        value = lane.get("value")
        shown = "—" if value is None else f"{value * 100:.1f}% ({lane.get('k')}/{lane.get('n')})"
        share = lane.get("questions_routed_share")
        share_text = "—" if share is None else f"{share * 100:.1f}%"
        cost = lane.get("cost_per_1000_usd")
        latency = lane.get("latency_p50_ms")
        flags = []
        if lane.get("partial"):
            flags.append(f"partial {document['n_cases']}/{document['n_expected']}")
        if lane.get("simulated"):
            flags.append("simulated")
        if role_threshold(lane):
            flags.append("t=0.90")
        lines.append(
            f"| {document['bench']} | {document['model']} | {lane['label']} | {document['chart']} | {shown} | {ci_text} | "
            f"{share_text} | {cost if cost is None else f'{cost:.2f}'} | "
            f"{'—' if latency is None else f'{latency:.0f}'} | {', '.join(flags) or '—'} |"
        )
    return "\n".join(lines)


def role_threshold(lane: dict) -> bool:
    return lane.get("role") == "combo"

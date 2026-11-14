"""Reproducible metrics envelopes for the `real_` and `realfin_` bench runs.

    cd billproof && .venv/bin/python -m billproof.bench.export_metrics
    .venv/bin/python -m billproof.bench.export_metrics --runs real_imr_test
    .venv/bin/python -m billproof.bench.export_metrics --split dev --out-dir /tmp/metrics
    .venv/bin/python -m billproof.bench.export_metrics --check

Writes `data/metrics/bench_<model>_<bench>.json`. Every number is an envelope
`{value, n, source_run, simulated, computed_at}`; `n` is that number's own
denominator (questions for accuracy, cases for cost, pairs for ratios).

Rules this module keeps:
- The 1% error automation cutoff is selected on the dev split only. Test rows
  are passed to `evaluate_error_cap` after the cutoff is fixed.
- A question routed to a human or hard-capped is never a model answer and is
  never assumed right for the human.
- Frontier self-reported confidence and Drex native probabilities are scored
  and binned separately, never pooled.
- Costs are estimates (recorded tokens x list prices), not invoices.
- Partial or failed populations are labelled and are never a headline: the CLI
  skips them unless `--allow-partial` is given, and even then `headline` is null.
- No network calls; reads only run JSONL/meta under `data/runs/`.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from billproof.bench.metrics import (
    brier,
    ece,
    evaluate_error_cap,
    question_units,
    select_error_cap_threshold,
    strict_case_correct,
    wilson_ci,
)
from billproof.config import DATA_DIR, RUNS_DIR

SCHEMA_VERSION = 1
LANE_ORDER = ("frontier", "drex", "hybrid")
HUMAN_ROUTES = ("human", "hard_cap")
_RUN_RE = re.compile(r"^(real|realfin)_(synthetic|imr)_(dev|test)$")
_FAMILY_MODEL = {"real": "drex-max-v1.1", "realfin": "drex-max-finance"}
_COST_NOTE = (
    "All costs are estimates: recorded token counts times list prices "
    "(frontier $2.50/$15 per 1M; Drex list price), not invoices. Simulated runs "
    "use the adapter's token estimator."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue  # a live run may be mid-append; never fail on its tail
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse_run_id(run_id: str) -> tuple[str | None, str | None, str | None]:
    match = _RUN_RE.match(run_id)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, None, None


def _dedupe_rows(rows: Sequence[dict]) -> list[dict]:
    """One row per (case, lane): a later success replaces an earlier failed call."""
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row.get("case_id")), str(row.get("lane")))
        if key not in best:
            order.append(key)
            best[key] = row
        elif best[key].get("error") and not row.get("error"):
            best[key] = row
        elif not (row.get("error") and not best[key].get("error")):
            best[key] = row
    return [best[key] for key in order]


def load_run(run_id: str, runs_dir: Path = RUNS_DIR) -> dict[str, Any]:
    """Read one run; meta wins over the run-id naming when both exist."""
    rows_path = runs_dir / f"{run_id}.jsonl"
    if not rows_path.is_file():
        raise FileNotFoundError(f"no run rows at {rows_path}")
    raw_rows = _read_jsonl(rows_path)
    rows = _dedupe_rows(raw_rows)
    meta = _read_json(runs_dir / f"{run_id}.meta.json")
    family, bench, split = _parse_run_id(run_id)
    family = str(meta.get("family") or family or run_id.split("_", 1)[0])
    bench = str(meta.get("bench") or bench or "")
    split = str(meta.get("split") or split or "")
    if not bench or not split:
        raise ValueError(f"cannot infer bench/split for run {run_id!r}")
    simulated = str(meta.get("mode") or "").upper() == "SIMULATED" or any(
        bool(row.get("simulated")) for row in rows
    )
    cases = {str(row.get("case_id")) for row in rows if row.get("case_id")}
    return {
        "run_id": run_id,
        "family": family,
        "bench": bench,
        "split": split,
        "rows": rows,
        "meta": meta,
        "simulated": simulated,
        "partial": bool(meta.get("partial")),
        "planned_cases": meta.get("planned_cases"),
        "n_cases": len(cases),
        "n_error_rows": sum(1 for row in rows if row.get("error")),
        "n_duplicate_rows": len(raw_rows) - len(rows),
    }


def discover_runs(runs_dir: Path = RUNS_DIR) -> dict[tuple[str, str, str], str]:
    """(family, bench, split) -> run id, for canonical real_/realfin_ run names."""
    found: dict[tuple[str, str, str], str] = {}
    for path in sorted(runs_dir.glob("*.jsonl")):
        run_id = path.stem
        family, bench, split = _parse_run_id(run_id)
        if not (family and bench and split):
            continue
        try:
            info = load_run(run_id, runs_dir)
        except (FileNotFoundError, ValueError):
            continue
        found[(info["family"], info["bench"], info["split"])] = run_id
    return found


def _model_slug(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(model).lower()).strip("_")
    return slug or "unknown"


def _drex_model(rows: Sequence[dict], family: str) -> str:
    for row in rows:
        if row.get("lane") == "drex" and row.get("model") and row.get("model") != "unknown":
            return str(row["model"])
    return _FAMILY_MODEL.get(family, "unknown")


def _env(
    value: Any,
    n: int,
    source_run: str | None,
    simulated: bool,
    computed_at: str,
    **extra: Any,
) -> dict[str, Any]:
    envelope = {
        "value": value,
        "n": int(n),
        "source_run": source_run,
        "simulated": bool(simulated),
        "computed_at": computed_at,
    }
    envelope.update(extra)
    return envelope


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _mean(values: Iterable[float]) -> float | None:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(arr.mean()) if arr.size else None


def _percentile(values: Iterable[float], q: float) -> float | None:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.percentile(arr, q)) if arr.size else None


def _scored(units: Sequence[dict]) -> list[dict]:
    return [unit for unit in units if not unit["failed"] and unit["correct"] is not None]


def _answered(units: Sequence[dict]) -> list[dict]:
    return [unit for unit in units if unit["has_answer"] and not unit["human_routed"]]


def _calibration(
    units: Sequence[dict], kind: str
) -> tuple[float | None, float | None, list[dict], int]:
    def matches(unit: dict) -> bool:
        if kind == "native":
            return bool(unit["native"])
        return unit["confidence_kind"] == "self_reported"

    chosen = [
        unit
        for unit in units
        if not unit["failed"]
        and not unit["human_routed"]
        and unit["correct"] is not None
        and unit["confidence"] is not None
        and matches(unit)
    ]
    if not chosen:
        return None, None, [], 0
    confs = [unit["confidence"] for unit in chosen]
    ys = [1.0 if unit["correct"] else 0.0 for unit in chosen]
    ece_value, bins = ece(confs, ys, bins=10)
    full_probs = all(
        isinstance(unit["probabilities"], dict) and unit["probabilities"] and unit["gold"] is not None
        for unit in chosen
    )
    if full_probs:
        score = brier([unit["probabilities"] for unit in chosen], [unit["gold"] for unit in chosen])
    else:
        score = brier(confs, ys)
    if not math.isfinite(score):
        score = None
    return score, ece_value, bins, len(chosen)


def _family_table(units: Sequence[dict]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[bool]] = {}
    for unit in _answered(units):
        if unit["correct"] is None:
            continue
        buckets.setdefault(unit["family"], []).append(bool(unit["correct"]))
    return {
        family: {"acc": float(np.mean(np.asarray(flags, dtype=float))), "n": len(flags)}
        for family, flags in sorted(buckets.items())
    }


def _paired(rows_a: Sequence[dict], rows_b: Sequence[dict], field: str) -> tuple[list[float], list[float]]:
    a = {
        str(row["case_id"]): _num(row.get(field))
        for row in rows_a
        if row.get("case_id") and not row.get("error")
    }
    b = {
        str(row["case_id"]): _num(row.get(field))
        for row in rows_b
        if row.get("case_id") and not row.get("error")
    }
    pairs = [
        (a[case_id], b[case_id])
        for case_id in sorted(set(a) & set(b))
        if a[case_id] is not None and b[case_id] is not None
    ]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _ratio(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None or baseline == 0:
        return None
    return value / baseline


def _lane_metrics(
    metrics: dict[str, dict],
    lane: str,
    lane_rows: Sequence[dict],
    *,
    source_run: str,
    simulated: bool,
    computed_at: str,
    cost_basis: str,
) -> None:
    units = question_units(lane_rows, lane)
    scored = _scored(units)
    answered = [unit for unit in _answered(scored)]
    hits = sum(1 for unit in answered if unit["correct"])
    accuracy = hits / len(answered) if answered else None
    lo, hi = wilson_ci(hits, len(answered)) if answered else (None, None)
    all_hits = sum(1 for unit in scored if unit["correct"])
    metrics[f"question_accuracy.{lane}"] = _env(
        accuracy,
        len(answered),
        source_run,
        simulated,
        computed_at,
        ci95=[lo, hi],
        denominator="questions answered by the model",
        excluded_human_routed=len(scored) - len(answered),
    )
    metrics[f"question_accuracy_all_scored.{lane}"] = _env(
        all_hits / len(scored) if scored else None,
        len(scored),
        source_run,
        simulated,
        computed_at,
        note="human/hard-cap routed questions count as not correct, never as human success",
    )
    metrics[f"coverage.{lane}"] = _env(
        len(answered) / len(scored) if scored else None,
        len(scored),
        source_run,
        simulated,
        computed_at,
        denominator="questions with a gold answer",
    )

    flags = [strict_case_correct(row) for row in lane_rows]
    flags = [flag for flag in flags if flag is not None]
    metrics[f"case_pass.{lane}"] = _env(
        _mean([1.0 if flag else 0.0 for flag in flags]),
        len(flags),
        source_run,
        simulated,
        computed_at,
        note="strict: a case with a human handoff does not pass",
    )

    costs = [_num(row.get("cost_usd")) for row in lane_rows if not row.get("error")]
    costs = [cost for cost in costs if cost is not None]
    mean_cost = _mean(costs)
    metrics[f"cost_per_case_usd.{lane}"] = _env(
        mean_cost,
        len(costs),
        source_run,
        simulated,
        computed_at,
        estimated=True,
        basis=cost_basis,
    )
    metrics[f"cost_per_1000_cases_usd.{lane}"] = _env(
        mean_cost * 1000.0 if mean_cost is not None else None,
        len(costs),
        source_run,
        simulated,
        computed_at,
        estimated=True,
        basis=cost_basis,
    )

    latencies = [_num(row.get("latency_ms")) for row in lane_rows if not row.get("error")]
    latencies = [latency for latency in latencies if latency is not None]
    metrics[f"latency_p50_ms.{lane}"] = _env(
        _percentile(latencies, 50), len(latencies), source_run, simulated, computed_at
    )
    metrics[f"latency_p95_ms.{lane}"] = _env(
        _percentile(latencies, 95), len(latencies), source_run, simulated, computed_at
    )

    if lane != "frontier":
        brier_native, ece_native, bins_native, n_native = _calibration(units, "native")
        metrics[f"brier_native.{lane}"] = _env(
            brier_native,
            n_native,
            source_run,
            simulated,
            computed_at,
            confidence_kind="native_probability",
        )
        metrics[f"ece_native.{lane}"] = _env(
            ece_native,
            n_native,
            source_run,
            simulated,
            computed_at,
            confidence_kind="native_probability",
            bins=10,
        )
        metrics[f"reliability_native.{lane}"] = _env(
            bins_native,
            n_native,
            source_run,
            simulated,
            computed_at,
            confidence_kind="native_probability",
        )

    brier_self, ece_self, bins_self, n_self = _calibration(units, "self_reported")
    if lane in ("frontier", "hybrid"):
        label = "frontier" if lane == "frontier" else "hybrid_frontier_verified"
        metrics[f"brier_self_reported.{label}"] = _env(
            brier_self,
            n_self,
            source_run,
            simulated,
            computed_at,
            confidence_kind="self_reported",
        )
        metrics[f"ece_self_reported.{label}"] = _env(
            ece_self,
            n_self,
            source_run,
            simulated,
            computed_at,
            confidence_kind="self_reported",
            bins=10,
        )
        metrics[f"reliability_self_reported.{label}"] = _env(
            bins_self,
            n_self,
            source_run,
            simulated,
            computed_at,
            confidence_kind="self_reported",
        )

    for family, block in _family_table(units).items():
        metrics[f"by_family.{lane}.{family}"] = _env(
            block["acc"], int(block["n"]), source_run, simulated, computed_at
        )


def _run_status(run: dict[str, Any]) -> str:
    if run["partial"]:
        return "partial"
    if run["n_error_rows"] or run.get("n_duplicate_rows"):
        return "incomplete"
    return "complete"


def _population(run: dict[str, Any], lanes: dict[str, list[dict]], expected_lanes: Sequence[str]) -> dict[str, Any]:
    n_cases = len({str(row.get("case_id")) for row in run["rows"] if row.get("case_id")})
    error_rows = int(run["n_error_rows"])
    declared = run["meta"].get("lanes")
    expected = [str(lane) for lane in declared] if isinstance(declared, (list, tuple)) else list(expected_lanes)
    missing_lanes = [lane for lane in expected if not lanes.get(lane)]
    duplicates = int(run.get("n_duplicate_rows") or 0)
    # Check every lane against the actual split, including borrowed frontier.
    from billproof.bench.runner import _items, _case_id

    expected_ids = {_case_id(run["bench"], item) for item in _items(run["bench"], run["split"])}
    lane_population_ok = bool(expected_ids) and all(
        {str(row.get("case_id")) for row in lanes.get(lane, []) if not row.get("error")} == expected_ids
        for lane in expected_lanes
    )
    complete = (not run["partial"] and error_rows == 0 and duplicates == 0
                and not missing_lanes and lane_population_ok and not run["simulated"])
    if run["partial"]:
        status = "partial"
    elif not complete:
        status = "incomplete"
    else:
        status = "complete"
    return {
        "run_id": run["run_id"],
        "split": run["split"],
        "n_cases": n_cases,
        "planned_cases": run["planned_cases"],
        "partial": bool(run["partial"]),
        "n_error_rows": error_rows,
        "n_duplicate_rows": duplicates,
        "lanes_present": [lane for lane in LANE_ORDER if lanes.get(lane)],
        "lanes_expected": expected,
        "lanes_missing": missing_lanes,
        "status": status,
        "complete": complete,
        "lane_population_ok": lane_population_ok,
        "expected_cases": len(expected_ids),
        "headline_eligible": complete and run["split"] == "test",
    }


def _threshold_block(
    run: dict[str, Any],
    selection: dict[str, Any] | None,
    dev_run: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    computed_at: str,
    dev_status: str | None = None,
) -> dict[str, Any]:
    simulated = bool(run["simulated"])
    dev_id = dev_run["run_id"] if dev_run else None
    dev_simulated = bool(dev_run["simulated"]) if dev_run else False
    block: dict[str, Any] = {
        "target_error_rate": selection["target_error_rate"] if selection else None,
        "selection_split": "dev",
        "selection_run": dev_id,
        "selection_run_status": dev_status,
        "selection_run_partial": bool(dev_status and dev_status != "complete"),
        "confidence_source": "drex native probability of the chosen answer",
        "lane": "drex",
        "min_automated": selection["min_automated"] if selection else None,
        "achieved_on_dev": bool(selection["achieved"]) if selection else False,
        "reason": selection["reason"] if selection else "no_dev_run",
        "value": None,
        "dev": None,
        "test": None,
        "note": "Selected on dev only; test rows never choose the threshold.",
    }
    if selection is None:
        block["note"] += " No dev run for this bench, so no threshold was selected."
        return block
    if dev_status and dev_status != "complete":
        block["note"] += " Dev selection population is not complete, so this cutoff is provisional."
    block["value"] = _env(
        selection["threshold"],
        selection["n_questions"],
        dev_id,
        dev_simulated,
        computed_at,
        achieved=selection["achieved"],
        n_automated=selection["n_automated"],
        error_automated=selection["error_automated"],
        automation_rate=selection["automation_rate"],
        best_available=selection["best_available"],
        selection_split="dev",
    )
    block["dev"] = {
        "automation_rate": _env(
            selection["automation_rate"], selection["n_questions"], dev_id, dev_simulated, computed_at
        ),
        "error_automated": _env(
            selection["error_automated"], selection["n_automated"], dev_id, dev_simulated, computed_at
        ),
        "accuracy_automated": _env(
            selection["accuracy_automated"], selection["n_automated"], dev_id, dev_simulated, computed_at
        ),
    }
    if evaluation is not None:
        block["test"] = {
            "automation_rate": _env(
                evaluation["automation_rate"], evaluation["n_questions"], run["run_id"], simulated, computed_at
            ),
            "error_automated": _env(
                evaluation["error_automated"], evaluation["n_automated"], run["run_id"], simulated, computed_at
            ),
            "accuracy_automated": _env(
                evaluation["accuracy_automated"], evaluation["n_automated"], run["run_id"], simulated, computed_at
            ),
            "n_automated": _env(
                evaluation["n_automated"], evaluation["n_questions"], run["run_id"], simulated, computed_at
            ),
            "n_wrong": _env(
                evaluation["n_wrong"], evaluation["n_automated"], run["run_id"], simulated, computed_at
            ),
            "fixed_policy": True,
        }
    return block


def build_bench_metrics(
    run_id: str,
    *,
    runs_dir: Path = RUNS_DIR,
    target_error: float = 0.01,
    min_automated: int = 30,
    computed_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate one run into labelled metric envelopes (no network, no writes)."""
    computed_at = computed_at or _utc_now()
    run = load_run(run_id, runs_dir)
    family, bench, split = run["family"], run["bench"], run["split"]
    rows = run["rows"]
    lanes: dict[str, list[dict]] = {
        lane: [row for row in rows if row.get("lane") == lane] for lane in LANE_ORDER
    }
    lane_sources = {lane: run_id for lane in LANE_ORDER}
    lane_simulated = {lane: bool(run["simulated"]) for lane in LANE_ORDER}
    frontier_borrowed = False
    frontier_note = None
    if family == "realfin":
        real_id = f"real_{bench}_{split}"
        try:
            real = load_run(real_id, runs_dir)
            real_frontier = [row for row in real["rows"] if row.get("lane") == "frontier"]
        except (FileNotFoundError, ValueError):
            real = None
            real_frontier = []
        if real_frontier:
            lanes["frontier"] = real_frontier
            lane_sources["frontier"] = real_id
            lane_simulated["frontier"] = bool(real["simulated"])
            frontier_borrowed = True
            frontier_note = f"frontier rows borrowed from {real_id} (same bench and split)"
        elif lanes["frontier"]:
            frontier_note = "real_ frontier run unavailable; used frontier rows in the realfin run"
        else:
            frontier_note = "no frontier rows: real_ run for this bench/split is missing"
    lanes = {lane: lane_rows for lane, lane_rows in lanes.items() if lane_rows}

    model = _drex_model(rows, family)
    dev_run_id = f"{family}_{bench}_dev"
    try:
        dev_run = load_run(dev_run_id, runs_dir)
    except (FileNotFoundError, ValueError):
        dev_run = None
    if dev_run is not None and dev_run["split"] == "dev" and dev_run["rows"]:
        selection = select_error_cap_threshold(
            dev_run["rows"], target_error=target_error, lane="drex", min_automated=min_automated
        )
    else:
        selection = None
    dev_status = _run_status(dev_run) if dev_run is not None else None
    evaluation = (
        evaluate_error_cap(rows, selection["threshold"], lane="drex")
        if selection is not None and selection["threshold"] is not None and split == "test"
        else None
    )

    population = _population(run, lanes, LANE_ORDER)
    cost_basis = (
        "simulated_token_estimator"
        if run["simulated"]
        else "recorded_cost_usd_estimate_from_list_prices"
    )
    metrics: dict[str, dict] = {}
    for lane in LANE_ORDER:
        if lane not in lanes:
            continue
        _lane_metrics(
            metrics,
            lane,
            lanes[lane],
            source_run=lane_sources[lane],
            simulated=lane_simulated[lane],
            computed_at=computed_at,
            cost_basis=cost_basis,
        )

    frontier_rows = lanes.get("frontier")
    if frontier_rows:
        for lane in ("drex", "hybrid"):
            if lane not in lanes:
                continue
            lane_costs, frontier_costs = _paired(lanes[lane], frontier_rows, "cost_usd")
            cost_ratio = _ratio(_mean(lane_costs), _mean(frontier_costs))
            metrics[f"cost_ratio_vs_frontier.{lane}"] = _env(
                cost_ratio,
                len(lane_costs),
                run_id,
                bool(run["simulated"] or lane_simulated["frontier"]),
                computed_at,
                frontier_source_run=lane_sources["frontier"],
                basis="mean cost on paired cases / frontier mean cost on the same cases",
            )
            lane_lat, frontier_lat = _paired(lanes[lane], frontier_rows, "latency_ms")
            speed_ratio = _ratio(_percentile(frontier_lat, 50), _percentile(lane_lat, 50))
            metrics[f"speed_ratio_vs_frontier.{lane}"] = _env(
                speed_ratio,
                len(lane_lat),
                run_id,
                bool(run["simulated"] or lane_simulated["frontier"]),
                computed_at,
                frontier_source_run=lane_sources["frontier"],
                basis="frontier p50 latency / lane p50 latency on paired cases; >1 is faster",
            )

    seen: set[tuple[str, str]] = set()
    for lane, lane_rows in lanes.items():
        for unit in question_units(lane_rows, lane):
            if not unit["failed"] and unit["correct"] is not None:
                seen.add((unit["case_id"], unit["name"]))
    n_questions = len(seen)
    honesty = [
        "Costs are estimates (recorded tokens x list prices), not invoices.",
        "Old IMR dropped: structured fields only, near chance, superseded by Persius. Recorded IMR rows are retained for audit.",
        "Latency is observed client wall time including router pacing and retries; speed ratios are deployment-specific.",
        "Native calibration and the automation cutoff use the selected-class probability; live routing uses the API confidence scalar, which can differ.",
        "Frontier confidence is self-reported by the model; Drex confidence is a native probability. "
        "ECE, Brier and reliability bins are never pooled across the two.",
        "Human/hard-cap routed questions are not model answers: they are excluded from model accuracy "
        "and counted as not correct in the all-scored view, never as human success.",
        "The 1% automation threshold is selected on the dev split only; test rows are used only to "
        "evaluate the fixed cutoff.",
    ]
    if population["status"] != "complete":
        honesty.insert(
            0,
            f"NOT A HEADLINE: {population['status']} population "
            f"({population['n_cases']} cases"
            + (f" of {population['planned_cases']}" if population["planned_cases"] else "")
            + f", {population['n_error_rows']} error rows, "
            f"{population['n_duplicate_rows']} duplicate rows, "
            f"missing lanes {population['lanes_missing'] or 'none'}).",
        )
    if population["status"] == "complete" and split != "test":
        honesty.append("Dev split: tuning population, never a headline.")
    if run["simulated"]:
        honesty.append("SIMULATED")
    if bench == "synthetic":
        honesty.append("Synthetic upper bound: the same team wrote the documents and the labels.")
    else:
        honesty.append("Real IMR outcomes (pre-decision fields only).")

    headline = None
    if population["headline_eligible"] and split == "test":
        hybrid = metrics.get("question_accuracy_all_scored.hybrid", {}).get("value")
        drex = metrics.get("question_accuracy.drex", {}).get("value")
        frontier = metrics.get("question_accuracy.frontier", {}).get("value")
        if hybrid is not None and frontier is not None:
            headline = (
                f"Drex+Frontier strict question accuracy {hybrid:.1%} vs Frontier {frontier:.1%} "
                f"on {population['n_cases']} test cases."
            )
        elif drex is not None and frontier is not None:
            headline = (
                f"Drex question accuracy {drex:.1%} vs Frontier {frontier:.1%} "
                f"on {population['n_cases']} test cases."
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "model_slug": _model_slug(model),
        "bench": bench,
        "split": split,
        "run_id": run_id,
        "simulated": bool(run["simulated"]),
        "computed_at": computed_at,
        "n_cases": population["n_cases"],
        "n_questions": n_questions,
        "population": population,
        "headline": headline,
        "cost_note": _COST_NOTE,
        "sources": {
            "run": run_id,
            "dev_run": dev_run["run_id"] if dev_run else None,
            "frontier_run": lane_sources.get("frontier"),
            "frontier_simulated": lane_simulated.get("frontier"),
        },
        "frontier_borrowed": frontier_borrowed,
        "frontier_note": frontier_note,
        "threshold": _threshold_block(run, selection, dev_run, evaluation, computed_at, dev_status),
        "metrics": metrics,
        "honesty": honesty,
    }


def output_path(out_dir: Path, model: str, bench: str, split: str) -> Path:
    name = f"bench_{_model_slug(model)}_{bench}.json"
    if split != "test":
        name = f"bench_{_model_slug(model)}_{bench}_{split}.json"
    return out_dir / name


def _print_summary(payload: dict, path: Path | None) -> None:
    pop = payload["population"]
    flag = "HEADLINE" if pop["headline_eligible"] else pop["status"].upper()
    print(
        f"{payload['run_id']:<28} {payload['model']:<20} {payload['bench']:<9} "
        f"{pop['n_cases']:>4} cases  {flag}"
    )
    for lane in LANE_ORDER:
        key = f"question_accuracy.{lane}"
        if key not in payload["metrics"]:
            continue
        block = payload["metrics"][key]
        ci = block.get("ci95") or [None, None]
        ci_text = (
            f"[{ci[0]:.3f}, {ci[1]:.3f}]"
            if ci[0] is not None and ci[1] is not None
            else "[n/a]"
        )
        cost = payload["metrics"].get(f"cost_per_1000_cases_usd.{lane}", {}).get("value")
        cost_text = f"${cost:.2f}" if cost is not None else "n/a"
        print(
            f"  {lane:<9} acc {block['value'] if block['value'] is not None else float('nan'):.3f} "
            f"{ci_text:<18} n={block['n']:<5} $/1k {cost_text}"
        )
    threshold = payload["threshold"]
    if threshold.get("value") is not None:
        test = threshold.get("test") or {}
        error = (test.get("error_automated") or {}).get("value")
        automation = (test.get("automation_rate") or {}).get("value")
        print(
            f"  1% cap: dev threshold {threshold['value']['value']} "
            f"(achieved={threshold['achieved_on_dev']}); "
            f"test automation {automation if automation is not None else 'n/a'} "
            f"error {error if error is not None else 'n/a'}"
        )
    if path is not None:
        print(f"  wrote {path}")


def self_check() -> None:
    """Tiny deterministic checks for the leakage and routing rules (no files)."""

    def row(case_id: str, split: str, confidence: float, correct: bool, route: str | None = None) -> dict:
        return {
            "case_id": case_id,
            "split": split,
            "lane": "drex",
            "answers": {
                "q_L1": {
                    "choice": "yes" if correct else "no",
                    "confidence": confidence,
                    "confidence_kind": "native",
                    "probabilities": {"yes": confidence if correct else 1.0 - confidence,
                                      "no": 1.0 - confidence if correct else confidence},
                    "route": route,
                }
            },
            "gold": {"q_L1": "yes"},
            "correct_strict": {"q_L1": correct},
        }

    dev = [
        row("D1", "dev", 0.99, True),
        row("D2", "dev", 0.99, True),
        row("D3", "dev", 0.80, True),
        row("D4", "dev", 0.70, False),
    ]
    selection = select_error_cap_threshold(dev, target_error=0.01, min_automated=1)
    assert selection["achieved"] and selection["threshold"] == 0.80, selection
    assert selection["error_automated"] == 0.0

    test_rows = [row("T1", "test", 0.99, True), row("T2", "test", 0.80, False)]
    try:
        select_error_cap_threshold(test_rows, target_error=0.01, min_automated=1)
    except ValueError:
        pass
    else:
        raise AssertionError("threshold selection must refuse test rows")

    evaluation = evaluate_error_cap(test_rows, 0.80)
    assert evaluation["n_automated"] == 2 and evaluation["n_wrong"] == 1
    assert evaluation["error_automated"] == 0.5

    human = row("H1", "test", 0.99, True, route="human")
    units = question_units([human], "drex")
    assert units[0]["human_routed"] and units[0]["correct"] is False
    assert strict_case_correct(human) is False

    envelope = _env(1.0, 2, "run", False, "now")
    for key in ("value", "n", "source_run", "simulated", "computed_at"):
        assert key in envelope
    print("self-check ok")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", nargs="*", help="explicit run ids (default: discovered real_/realfin_ runs)")
    parser.add_argument("--bench", choices=["synthetic", "imr", "all"], default="all")
    parser.add_argument("--split", choices=["test", "dev"], default="test")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "metrics")
    parser.add_argument("--target-error", type=float, default=0.01)
    parser.add_argument("--min-automated", type=int, default=30)
    parser.add_argument("--allow-partial", action="store_true", help="write labelled non-headline files")
    parser.add_argument("--check", action="store_true", help="run the built-in self-checks and exit")
    args = parser.parse_args(argv)

    if args.check:
        self_check()
        return 0

    discovered = discover_runs(args.runs_dir)
    if args.runs:
        run_ids = list(dict.fromkeys(args.runs))
        missing = [
            run_id for run_id in run_ids if not (args.runs_dir / f"{run_id}.jsonl").is_file()
        ]
        if missing:
            print(f"missing run rows: {', '.join(missing)}")
            return 2
    else:
        run_ids = [
            run_id
            for (family, bench, split), run_id in sorted(discovered.items())
            if split == args.split and (args.bench == "all" or bench == args.bench)
        ]

    if not run_ids:
        print(f"no real_/realfin_ runs for split={args.split} bench={args.bench}; nothing to export")
        return 0

    written = 0
    skipped = 0
    for run_id in run_ids:
        try:
            payload = build_bench_metrics(
                run_id,
                runs_dir=args.runs_dir,
                target_error=args.target_error,
                min_automated=args.min_automated,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"{run_id}: {exc}")
            skipped += 1
            continue
        if not payload["population"]["complete"] and not args.allow_partial:
            print(
                f"{run_id}: {payload['population']['status']} population "
                f"({payload['population']['n_cases']} cases, "
                f"{payload['population']['n_error_rows']} error rows); "
                "not written as a headline (use --allow-partial for a labelled file)"
            )
            skipped += 1
            continue
        args.out_dir.mkdir(parents=True, exist_ok=True)
        path = output_path(args.out_dir, payload["model"], payload["bench"], payload["split"])
        path.write_text(json.dumps(payload, indent=2) + "\n")
        _print_summary(payload, path)
        written += 1
    print(f"wrote {written} file(s), skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

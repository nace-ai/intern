"""Record and replay benchmark decisions in the shared JSONL wire format."""

from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from billproof.config import RUNS_DIR, settings
from billproof.schema import GoldItem


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _items(bench: str, split: str):
    if bench == "synthetic":
        from billproof.data import list_cases

        return list_cases(split)
    if bench == "imr":
        from billproof.bench.real_imr import list_items

        return list_items(split)
    raise ValueError(f"unknown benchmark: {bench}")


def _questions_state(bench: str, item):
    if bench == "synthetic":
        from billproof.decisions.questions import build_questions, build_state
        from billproof.rules.checks import run_checks

        flags = run_checks(item)
        return build_questions(item, flags), build_state(item, flags), flags
    from billproof.bench.real_imr import build_questions, build_state

    return build_questions(item), build_state(item), []


def _gold(bench: str, item) -> dict[str, GoldItem]:
    if bench == "synthetic":
        from billproof.data import load_gold

        return load_gold(item.case_id)
    from billproof.bench.real_imr import load_gold

    return load_gold(item["case_id"])


def _case_id(bench: str, item) -> str:
    return item.case_id if bench == "synthetic" else item["case_id"]


def _split(bench: str, item) -> str:
    return item.split if bench == "synthetic" else item["split"]


def _item(bench: str, case_id: str):
    """One bench item by id (dev-tune context rebuild; never exposes gold)."""
    if bench == "synthetic":
        from billproof.data import load_case

        try:
            return load_case(case_id)
        except Exception:
            return None
    for it in _items(bench, None):
        if str(it.get("case_id")) == case_id:
            return it
    return None


def _lane_coverage(rows: list[dict], lanes: list[str], planned_ids: set[str]) -> dict:
    """Per-lane completeness vs the full split identity set (incl. shared lanes)."""
    coverage = {}
    for lane in lanes:
        have = {str(r.get("case_id")) for r in rows
                if str(r.get("lane")) == lane and not r.get("error")}
        coverage[lane] = {"done": len(have & planned_ids), "planned": len(planned_ids),
                          "complete": planned_ids <= have}
    return coverage


def _service_mode(service: str) -> str:
    cfg = settings()
    try:
        return cfg.mode_label(service)
    except RuntimeError:
        # MOCK_MODE=0 still intends live calls; unavailable credentials become
        # explicit per-row failures during the normal run.
        return "LIVE" if cfg.mock_mode == "0" else "SIMULATED"


def _lane_modes(lanes: list[str]) -> dict[str, str]:
    modes = {}
    for lane in lanes:
        services = ("frontier", "drex") if lane == "hybrid" else (lane,)
        modes[lane] = "LIVE" if all(_service_mode(s) == "LIVE" for s in services) else "SIMULATED"
    return modes


def _mode(bench: str, lanes: list[str]) -> str:
    del bench
    modes = _lane_modes(lanes).values()
    return "LIVE" if modes and all(mode == "LIVE" for mode in modes) else "SIMULATED"


def _meta_hash(bench: str) -> str:
    from billproof.data import label_sha256

    return label_sha256(bench)


def run_bench(
    bench: str,
    split: str,
    lanes,
    *,
    record: bool = True,
    run_id: str | None = None,
    on_event=None,
    limit: int | None = None,
    seed: int = 0,
    assume_human_correct: bool = False,
) -> str:
    """Run each selected lane on each item; exceptions are retained as failed rows."""
    lane_names = [x.strip() for x in lanes.split(",") if x.strip()] if isinstance(lanes, str) else list(lanes)
    if not lane_names or any(lane not in {"frontier", "drex", "hybrid"} for lane in lane_names):
        raise ValueError("lanes must contain frontier, drex, and/or hybrid")
    items = _items(bench, split)
    if limit is not None:
        items = items[:max(0, limit)]
    run_id = run_id or f"{bench}_{split}_{uuid.uuid4().hex[:10]}"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    # One real Drex warm-up only. Simulated calls have no useful cold-start
    # effect, and warming every lane would add hidden calls and stage delay.
    try:
        warm_live_drex = bool(items) and settings().use_real("drex")
    except RuntimeError:
        warm_live_drex = False
    if warm_live_drex:
        first = items[0]
        qs, state, _ = _questions_state(bench, first)
        from billproof.decisions.lanes import run_lane

        warm_gold = _gold(bench, first)
        try:
            run_lane("drex", first, qs, state, gold=warm_gold)
        except Exception:
            pass

    rng = random.Random(seed)
    cum = {lane: {"done": 0, "correct_q": 0, "total_q": 0, "case_correct": 0,
                  "cases": 0, "accuracy": 0.0, "time_ms": 0.0, "cost_usd": 0.0}
           for lane in lane_names}
    from billproof.decisions.escalation import active_bands
    from billproof.decisions.lanes import run_lane

    try:
        bands_used = active_bands(bench)
    except Exception:
        bands_used = None
    planned_ids = {str(_case_id(bench, it)) for it in items}

    for item in items:
        case_id = _case_id(bench, item)
        item_split = _split(bench, item)
        questions, state, flags = _questions_state(bench, item)
        gold_items = _gold(bench, item)
        gold = {name: value.answer for name, value in gold_items.items()}
        order = lane_names.copy()
        rng.shuffle(order)
        for lane in order:
            result = None
            error = None
            try:
                result = run_lane(lane, item, questions, state, gold=gold_items)
                answers = {
                    name: {key: getattr(answer, key) for key in
                           ("choice", "confidence", "confidence_kind", "probabilities", "route")}
                    for name, answer in result.answers.items()
                }
                # Score every evaluated question. Missing answers count as failures.
                evaluated = [q.name for q in questions if q.name in gold]
                strict = {}
                generous = {}
                for name in evaluated:
                    answer = result.answers.get(name)
                    is_human = bool(answer and answer.route in ("human", "hard_cap"))
                    got_it = bool(answer and answer.choice == gold[name])
                    strict[name] = got_it and not is_human
                    generous[name] = got_it or is_human
                correct = generous if assume_human_correct else strict
                case_correct = bool(correct) and all(correct.values())
                strict_case = bool(strict) and all(strict.values())
                generous_case = bool(generous) and all(generous.values())
                verdict = None
                if bench == "synthetic":
                    from billproof.decisions.verdicts import compute_verdict

                    verdict_obj = compute_verdict(item, flags, result.answers)
                    verdict = verdict_obj.model_dump(mode="json")
                else:
                    verdict = {"case_action": None, "owed_cents": None}
                row = {
                    "run_id": run_id, "bench": bench, "case_id": case_id, "split": item_split,
                    "lane": lane, "model": result.model, "simulated": result.simulated,
                    "replayed": result.replayed, "latency_ms": result.latency_ms,
                    "cost_usd": result.cost_usd, "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens, "answers": answers, "gold": gold,
                    "correct": correct, "correct_strict": strict,
                    "correct_if_humans_correct": generous, "case_correct": case_correct,
                    "case_correct_strict": strict_case,
                    "case_correct_if_humans_correct": generous_case, "verdict": verdict,
                    "ts": _utc_now(),
                }
                if result.error:
                    row["error"] = result.error
                    for key in ("correct", "correct_strict", "correct_if_humans_correct"):
                        row[key] = {name: None for name in row[key]}
                    row["case_correct"] = None
                    row["case_correct_strict"] = None
                    row["case_correct_if_humans_correct"] = None
                    correct = row["correct"]
                    case_correct = False
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                row = {
                    "run_id": run_id, "bench": bench, "case_id": case_id, "split": item_split,
                    "lane": lane, "model": "unknown", "simulated": _mode(bench, [lane]) == "SIMULATED",
                    "replayed": False, "latency_ms": 0.0, "cost_usd": 0.0,
                    "input_tokens": 0, "output_tokens": 0, "answers": {}, "gold": gold,
                    "correct": {q: False for q in gold},
                    "correct_strict": {q: False for q in gold},
                    "correct_if_humans_correct": {q: False for q in gold},
                    "case_correct": False, "case_correct_strict": False,
                    "case_correct_if_humans_correct": False,
                    "verdict": ({"case_action": None, "owed_cents": None} if bench == "imr" else None),
                    "error": error, "ts": _utc_now(),
                }
                correct = row["correct"]
                case_correct = False
            rows.append(row)
            stats = cum[lane]
            stats["done"] += 1
            if not row.get("error"):
                stats["correct_q"] += sum(v is True for v in correct.values())
                stats["total_q"] += len(correct)
                stats["case_correct"] += int(bool(case_correct))
            stats["cases"] += 1
            stats["time_ms"] += float(row["latency_ms"])
            stats["cost_usd"] += float(row["cost_usd"])
            stats["accuracy"] = stats["correct_q"] / stats["total_q"] if stats["total_q"] else 0.0
            if on_event:
                route_counts = {"drex_kept": 0, "frontier_verified": 0, "human": 0}
                for answer in row["answers"].values():
                    route = answer.get("route")
                    if route in ("human", "hard_cap"):
                        route_counts["human"] += 1
                    elif route in ("frontier_verified", "frontier_only") or lane == "frontier":
                        route_counts["frontier_verified"] += 1
                    else:
                        route_counts["drex_kept"] += 1
                snapshot = {key: (round(value, 6) if key == "cost_usd" else
                                  round(value, 1) if key == "time_ms" else value)
                            for key, value in stats.items()}
                snapshot["accuracy"] = round(stats["accuracy"], 4) if stats["total_q"] else None
                on_event({"lane": lane, "case_id": case_id, "i": stats["cases"],
                          "correct": correct, "case_correct": bool(case_correct),
                          "latency_ms": row["latency_ms"], "cost_usd": row["cost_usd"],
                          "routes": route_counts, "cum": json.loads(json.dumps(cum)),
                          **({"error": error} if error else {})})

    created = _utc_now()
    lane_modes = _lane_modes(lane_names)
    coverage = _lane_coverage(rows, lane_names, planned_ids)
    meta = {"run_id": run_id, "bench": bench, "split": split, "mode": _mode(bench, lane_names),
            "lanes": lane_names, "n_cases": len(items), "label_sha256": _meta_hash(bench),
            "created": created, "assume_human_correct": assume_human_correct,
            "lane_modes": lane_modes, "drex_model": settings().drex_model,
            "frontier_model": settings().frontier_model, "bands": bands_used,
            "coverage": coverage,
            "partial": any(not c["complete"] for c in coverage.values())}
    score_summary = {}
    for lane in lane_names:
        lane_rows = [row for row in rows if row["lane"] == lane and not row.get("error")]
        strict_total = sum(len(row["correct_strict"]) for row in lane_rows)
        strict_hits = sum(sum(row["correct_strict"].values()) for row in lane_rows)
        generous_total = sum(len(row["correct_if_humans_correct"]) for row in lane_rows)
        generous_hits = sum(sum(row["correct_if_humans_correct"].values()) for row in lane_rows)
        answer_total = sum(len(row["correct"]) for row in lane_rows)
        escalated = sum(
            1 for row in lane_rows for answer in row["answers"].values()
            if answer.get("route") in ("frontier_verified", "human", "hard_cap")
        )
        score_summary[lane] = {
            "accuracy_strict": strict_hits / strict_total if strict_total else 0.0,
            "accuracy_if_humans_correct": generous_hits / generous_total if generous_total else 0.0,
            "escalation_rate": escalated / answer_total if answer_total else 0.0,
        }
    if "hybrid" in score_summary:
        meta.update({f"hybrid_{key}": value for key, value in score_summary["hybrid"].items()})
    errors_by_lane = {lane: sum(1 for row in rows if row["lane"] == lane and row.get("error"))
                      for lane in lane_names}
    meta["n_errors"] = sum(errors_by_lane.values())
    meta["errors_by_lane"] = errors_by_lane
    if record:
        from billproof.bench.metrics import build_report

        metric_rows = []
        for row in rows:
            if not row.get("error"):
                metric_rows.append(row)
                continue
            # Keep failed calls in JSONL and case counts, but exclude them from
            # accuracy, calibration, and case-pass denominators.
            failed = dict(row)
            failed["answers"] = {}
            failed.pop("case_correct", None)
            failed.pop("error", None)
            metric_rows.append(failed)
        report = build_report(metric_rows, meta)
        for lane, scores in score_summary.items():
            lane_report = report.setdefault("lanes", {}).setdefault(lane, {})
            lane_report.update(scores)
            lane_report["n_errors"] = errors_by_lane[lane]
            lane_report["accuracy"] = (scores["accuracy_if_humans_correct"] if assume_human_correct
                                        else scores["accuracy_strict"])
        (RUNS_DIR / f"{run_id}.report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        path = RUNS_DIR / f"{run_id}.jsonl"
        path.write_text("".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rows))
        (RUNS_DIR / f"{run_id}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return run_id


def iter_replay(run_id: str) -> Iterator[dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    path = RUNS_DIR / f"{run_id}.jsonl"
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def tune_bands(
    dev_rows: list[dict],
    *,
    bench: str = "synthetic",
    model: str | None = None,
    margin: float = 0.01,
    frontier_rows: list[dict] | None = None,
    source_run: str | None = None,
    frontier_source_run: str | None = None,
    out_path: str | Path | None = None,
    write: bool = True,
) -> dict:
    """Choose the least costly confidence bands within margin of frontier dev accuracy.

    Bands are selected per (bench, drex_model) on DEV SPLIT ROWS ONLY and stored
    under bands.json "scopes" ("<bench>|<model>"); a synthetic tune also refreshes
    the flat {keep, verify} global default for backwards compatibility.

    For a middle-band question the verifier outcome is proxied by the PAIRED
    OBSERVED frontier dev answer, and its cost by a proportional share of the
    observed frontier call cost (an upper bound using the full call cost is also
    reported). This replaces the previous gold-informed `_sim_verify`/`_sim_usage`
    estimate, which leaked labels into band selection. The proxy is NOT measured
    verifier performance: dev noninferiority is not guaranteed — only the real
    hybrid test run establishes the outcome.

    `frontier_rows` (read-only, e.g. real_<bench>_dev for realfin_* dev runs that
    record no frontier lane) must cover the same case/question identities.
    Confidence is the recorded API scalar (runtime routing semantics), not the
    chosen-class probability used for the standalone automation cutoff.
    Raises ValueError on test rows, error rows, or incomplete/mismatched pairs.
    """
    from billproof.decisions import escalation
    from billproof.schema import Answer

    model = model or settings().drex_model

    # ------------------------------------------------- validate dev-only input
    problems: list[str] = []
    by_case: dict[str, dict[str, dict]] = {}
    n_err = 0
    for row in dev_rows:
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id"))
        if row.get("bench") not in (None, bench):
            problems.append(f"{case_id}: bench={row.get('bench')!r} != {bench!r}")
        if row.get("split") != "dev":
            problems.append(f"{case_id}: split={row.get('split')!r} — tuning never uses non-dev rows")
        if row.get("error"):
            n_err += 1
        by_case.setdefault(case_id, {})[str(row.get("lane"))] = row
    if n_err:
        problems.append(f"{n_err} error rows present — tune only once every call succeeded")
    shared_frontier: dict[str, dict] = {}
    for row in frontier_rows or []:
        if str(row.get("lane")) != "frontier":
            continue
        if row.get("error"):
            problems.append(f"{row.get('case_id')}: error row in frontier share source")
            continue
        if row.get("split") != "dev" or row.get("bench") not in (None, bench):
            problems.append(f"{row.get('case_id')}: frontier share must be same-bench dev")
            continue
        shared_frontier[str(row.get("case_id"))] = row
    if frontier_rows and not shared_frontier:
        problems.append("frontier share source has no usable rows")

    for case_id, lanes in sorted(by_case.items()):
        if "frontier" not in lanes and case_id in shared_frontier:
            lanes["frontier"] = shared_frontier[case_id]
        if "drex" not in lanes:
            problems.append(f"{case_id}: no drex row")
            continue
        if "frontier" not in lanes:
            problems.append(f"{case_id}: no frontier row (own or shared) — need the verifier proxy")
            continue
        gold_names = set((lanes["drex"].get("gold") or {}).keys())
        if not gold_names:
            problems.append(f"{case_id}: drex row has no gold questions")
            continue
        if set((lanes["frontier"].get("gold") or {}).keys()) != gold_names:
            problems.append(f"{case_id}: frontier gold questions differ from drex — wrong share source?")
    cases = [lanes for lanes in by_case.values() if "drex" in lanes and "frontier" in lanes]
    if not cases:
        problems.append("no cases with paired drex+frontier dev rows")
    if problems:
        raise ValueError("dev band tuning refused: " + "; ".join(problems[:12])
                         + (f" … (+{len(problems) - 12} more)" if len(problems) > 12 else ""))

    contexts = {}
    for pair in cases:
        case_id = str(pair["drex"]["case_id"])
        item = _item(bench, case_id)
        if item is None:
            raise ValueError(f"dev band tuning refused: no {bench} item for {case_id}")
        questions, _state, flags = _questions_state(bench, item)
        qmap = {q.name: q for q in questions}
        drex_answers = pair["drex"].get("answers") or {}
        frontier_answers = pair["frontier"].get("answers") or {}
        # Scored questions = gold ∩ asked, exactly like the runtime scorer.
        evaluated = [n for n in (pair["drex"].get("gold") or {}) if n in qmap]
        missing = [n for n in evaluated
                   if n not in drex_answers or drex_answers[n].get("choice") is None]
        if missing:
            problems.append(f"{case_id}: drex answers missing for {missing[:4]}"
                            + ("…" if len(missing) > 4 else ""))
        missing = [n for n in evaluated
                   if n not in frontier_answers or frontier_answers[n].get("choice") is None]
        if missing:
            problems.append(f"{case_id}: frontier answers missing for {missing[:4]}"
                            + ("…" if len(missing) > 4 else ""))
        if not evaluated:
            problems.append(f"{case_id}: no scored questions (gold ∩ asked is empty)")
        contexts[case_id] = (qmap, flags, evaluated)
    if problems:
        raise ValueError("dev band tuning refused: " + "; ".join(problems[:12])
                         + (f" … (+{len(problems) - 12} more)" if len(problems) > 12 else ""))

    def _hits(row: dict, key: str) -> tuple[int, int]:
        corr = row.get(key) or row.get("correct") or {}
        return sum(1 for v in corr.values() if v is True), len(corr)

    frontier_hits = sum(_hits(p["frontier"], "correct_strict")[0] for p in cases)
    question_count = sum(_hits(p["frontier"], "correct_strict")[1] for p in cases)
    frontier_accuracy = frontier_hits / question_count if question_count else 0.0
    thresholds = [round(i / 20, 2) for i in range(21)]
    candidates = []
    for keep in thresholds:
        for verify in thresholds:
            if verify > keep:
                continue
            hits = total = ghits = 0
            cost = cost_upper = 0.0
            for pair in cases:
                drex, frontier, hybrid = pair["drex"], pair["frontier"], pair.get("hybrid") or {}
                qmap, flags, evaluated = contexts[str(drex["case_id"])]
                gold_map = drex.get("gold") or {}
                n_q = len(evaluated)
                n_verify = 0
                drex_cost = float(drex.get("cost_usd") or 0.0)
                frontier_cost = float(frontier.get("cost_usd") or 0.0)
                cost += drex_cost
                cost_upper += drex_cost
                for name in evaluated:
                    expected = gold_map[name]
                    da = (drex.get("answers") or {}).get(name) or {}
                    # API-reported scalar: exactly what route() compares at runtime.
                    ans = Answer(name=name, choice=str(da.get("choice") or ""),
                                 confidence=float(da.get("confidence") or 0.0),
                                 confidence_kind=da.get("confidence_kind", "native"), raw={})
                    band = escalation.decide_band(qmap[name], ans, flags, keep, verify)
                    # Historical human routing reflects the OLD thresholds.
                    # Re-evaluate it; immutable hard caps were applied above.
                    total += 1
                    if band == "keep":
                        predicted = da.get("choice")
                    elif band == "verify":
                        n_verify += 1
                        predicted = ((frontier.get("answers") or {}).get(name) or {}).get("choice")
                    else:  # human / hard_cap — strict score cannot assume correctness
                        predicted = None
                    hits += int(predicted is not None and predicted == expected)
                    ghits += int(predicted == expected or band in ("human", "hard_cap"))
                if n_verify:
                    # Verify reads a scoped subset; proportional share of the
                    # observed frontier call cost is the proxy, the full call
                    # cost the reported upper bound.
                    cost += frontier_cost * (n_verify / n_q)
                    cost_upper += frontier_cost
            candidates.append({"keep": keep, "verify": verify,
                               "accuracy": hits / total if total else 0.0,
                               "accuracy_if_humans_correct": ghits / total if total else 0.0,
                               "cost_per_case": cost / len(cases),
                               "cost_per_case_upper": cost_upper / len(cases),
                               "frontier_accuracy": frontier_accuracy})
    eligible = [c for c in candidates if c["accuracy"] >= frontier_accuracy - margin]
    if eligible:
        selected = min(eligible, key=lambda c: (c["cost_per_case"], -c["accuracy"], -c["keep"], -c["verify"]))
        selection = "minimum_cost_within_margin"
    else:
        # Choose the Pareto knee: greatest vertical distance above the chord
        # from the cheapest point to the highest-accuracy point.
        front = [c for c in candidates if not any(
            other["cost_per_case"] <= c["cost_per_case"]
            and other["accuracy"] >= c["accuracy"]
            and (other["cost_per_case"] < c["cost_per_case"] or other["accuracy"] > c["accuracy"])
            for other in candidates
        )]
        low = min(front, key=lambda c: (c["cost_per_case"], -c["accuracy"]))
        high = max(front, key=lambda c: (c["accuracy"], -c["cost_per_case"]))
        cost_span = high["cost_per_case"] - low["cost_per_case"]
        acc_span = high["accuracy"] - low["accuracy"]
        if cost_span <= 0 or acc_span <= 0:
            selected = high
        else:
            selected = max(front, key=lambda c: (
                (c["accuracy"] - low["accuracy"])
                - acc_span * (c["cost_per_case"] - low["cost_per_case"]) / cost_span,
                c["accuracy"], -c["cost_per_case"],
            ))
        selection = "pareto_knee_no_candidate_within_margin"
    output = {**selected, "selection": selection, "margin": margin,
              "bench": bench, "drex_model": model,
              "n_cases": len(cases), "n_questions": question_count,
              "tuned_at": _utc_now(), "tuned_split": "dev",
              **({"source_run": source_run} if source_run else {}),
              **({"frontier_source_run": frontier_source_run} if frontier_source_run else {}),
              "selection_basis": (
                  "paired observed frontier dev answers proxy the verifier outcome; "
                  "verify cost = proportional share of the observed frontier call "
                  "(cost_per_case_upper = full-call upper bound). No gold-informed "
                  "simulation: the real hybrid test run alone establishes outcome."),
              "confidence_basis": "api_scalar_not_chosen_class_probability",
              **({"knee_method": "maximum_distance_above_pareto_endpoint_chord"}
                 if selection.startswith("pareto_knee") else {})}
    if write:
        path = Path(out_path) if out_path is not None else RUNS_DIR / "bands.json"
        merged: dict = {}
        try:
            if path.exists():
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    merged = loaded
        except json.JSONDecodeError:
            merged = {}
        scopes = dict(merged.get("scopes") or {})
        scopes[f"{bench}|{model}"] = output
        if bench == "synthetic":
            # Flat {keep, verify} stays the global default = latest synthetic tune.
            merged.update({k: v for k, v in output.items() if k != "scopes"})
        merged["scopes"] = scopes
        merged["format"] = 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=2) + "\n")
        if out_path is None:
            escalation.reload_bands()  # only the canonical file affects routing
    return output

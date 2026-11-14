#!/usr/bin/env python3
"""Persius combination on a new frozen train-split dev sample.

The leakage filter, question text, and GPT system prompt are imported from
scripts/c_persius.py and scripts/c_persius_fair.py. They are not redefined.

The candidate rules below are fixed before any dev outcome is read. `tune`
picks one rule on the dev sample only. `apply` scores that frozen rule once
on the recorded 400-row test file and does not call a model.

From billproof/:
  PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py run
  PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py tune
  PYTHONPATH=src .venv/bin/python scripts/g_persius_combo.py apply

Exit 0 when the requested step is done. Exit 3 on a transient failure so a
supervisor can resume. Exit 2 when the run must not continue.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import c_persius_fair as fair  # noqa: E402

from billproof.bench.metrics import mcnemar, wilson_ci  # noqa: E402

RUNS = ROOT / "data" / "runs"
METRICS = ROOT / "data" / "metrics"
LOG_DIR = ROOT / "out" / "logs"
IDS_PATH = RUNS / "g_persius_dev_ids.json"
JSONL_PATH = RUNS / "g_persius_dev.jsonl"
RULE_PATH = RUNS / "g_persius_rule.json"
METRICS_PATH = METRICS / "g_persius_combo.json"
PILOT_IDS = RUNS / "c_persius_ids.json"
TEST_JSONL = RUNS / "c_persius_test.jsonl"

N_PER_CLASS = 150
N_DEV = N_PER_CLASS * 2
RANK_SALT = "g-persius-dev|20261114"
ROUTER_CAP_USD = 8.0
NACE_CAP_USD = 0.3
GPT_LIST_IN = 2.0
GPT_LIST_OUT = 10.0
GPT_ERROR_ABORT = 8
TRANSIENT_RETRIES = 5

# Pre-registered before any dev label is scored. Do not add a rule after looking.
WEIGHTED_W = (0.0, 0.25, 0.5, 0.75, 1.0)
CONF_TAUS = (0.55, 0.65, 0.75, 0.85, 0.95)
BANDS = ((0.15, 0.85), (0.2, 0.8), (0.25, 0.75), (0.3, 0.7), (0.4, 0.6))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(message: str) -> None:
    print(f"[g_persius_combo] {message}", flush=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, indent=2) + "\n"
    with temporary.open("w") as handle:
        handle.write(text)
        handle.flush()
    temporary.replace(path)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()


def _rank(row_index: int) -> str:
    return hashlib.md5(f"{RANK_SALT}|{row_index}".encode()).hexdigest()


def _pilot_indexes() -> set[int]:
    if not PILOT_IDS.exists():
        raise SystemExit("ABORT: missing data/runs/c_persius_ids.json")
    document = json.loads(PILOT_IDS.read_text())
    indexes = {int(item["row_index"]) for item in document.get("items") or []}
    if len(indexes) != 20:
        raise SystemExit(f"ABORT: pilot id file has {len(indexes)} row indexes, want 20")
    return indexes


def _draw_document() -> dict:
    if IDS_PATH.exists():
        document = json.loads(IDS_PATH.read_text())
        items = document.get("items") or []
        if len(items) != N_DEV:
            raise SystemExit(f"ABORT: {IDS_PATH.name} has {len(items)} items; refusing to redraw")
        yes = sum(item.get("gold") == "yes" for item in items)
        no = sum(item.get("gold") == "no" for item in items)
        if yes != N_PER_CLASS or no != N_PER_CLASS:
            raise SystemExit(f"ABORT: existing dev ids are yes={yes} no={no}; refusing to redraw")
        if document.get("rank_salt") != RANK_SALT:
            raise SystemExit("ABORT: existing dev ids use a different rank salt; refusing to redraw")
        _log(f"using existing {IDS_PATH.name} n={len(items)}")
        return document

    excluded = _pilot_indexes()
    texts, decisions, appeal_types, sufficiency = fair.FROZEN._load_train()
    n_rows = len(texts)
    n_suf = 0
    n_dropped = 0
    n_excluded = 0
    n_overturn_before = 0
    n_overturn_after = 0
    pools: dict[str, list[dict]] = {"yes": [], "no": []}
    for index, (text, decision, appeal_type, suf) in enumerate(
        zip(texts, decisions, appeal_types, sufficiency)
    ):
        if suf != 1:
            continue
        n_suf += 1
        if decision == "Overturned":
            n_overturn_before += 1
        body = text or ""
        if fair.FROZEN.leaks(body):
            n_dropped += 1
            continue
        if index in excluded:
            n_excluded += 1
            continue
        if decision == "Overturned":
            gold = "yes"
            n_overturn_after += 1
        elif decision == "Upheld":
            gold = "no"
        else:
            continue
        pools[gold].append(
            {
                "row_index": index,
                "case_id": f"persius-train-{index}",
                "gold": gold,
                "decision": decision,
                "text_sha256": fair._sha256(body),
                "appeal_type": appeal_type,
                "rank_md5": _rank(index),
            }
        )
    if len(pools["yes"]) < N_PER_CLASS or len(pools["no"]) < N_PER_CLASS:
        raise SystemExit(
            f"ABORT: kept classes yes={len(pools['yes'])} no={len(pools['no'])} "
            f"cannot fill {N_PER_CLASS} each"
        )
    picked: list[dict] = []
    for pool in (pools["yes"], pools["no"]):
        pool.sort(key=lambda row: row["rank_md5"])
        picked.extend(pool[:N_PER_CLASS])
    picked.sort(key=lambda row: row["rank_md5"])
    if len(picked) != N_DEV or sum(row["gold"] == "yes" for row in picked) != N_PER_CLASS:
        raise SystemExit("ABORT: dev draw is not 150/150")
    overlap = excluded & {row["row_index"] for row in picked}
    if overlap:
        raise SystemExit(f"ABORT: dev draw overlaps the 20-row pilot: {sorted(overlap)[:5]}")
    kept = len(pools["yes"]) + len(pools["no"])
    document = {
        "pilot": "g_persius_dev",
        "split": "train",
        "n": N_DEV,
        "per_class": N_PER_CLASS,
        "stratified": True,
        "rank_salt": RANK_SALT,
        "rank": "md5('g-persius-dev|20261114|{row_index}') ascending, first 150 within each class",
        "excluded_pilot_ids": "data/runs/c_persius_ids.json",
        "gold_map": {"Overturned": "yes", "Upheld": "no"},
        "source": "train sufficiency_id==1 after frozen c_persius.leaks, excluding the 20-row pilot",
        "leakage": "imported c_persius.leaks; regexes not copied",
        "question_instructions_sha256": fair._sha256(fair.QUESTION.instructions),
        "gpt_system_sha256": fair.GPT_SYSTEM_SHA,
        "population": {
            "train_rows": n_rows,
            "sufficiency_1": n_suf,
            "dropped_by_leaks": n_dropped,
            "excluded_pilot": n_excluded,
            "kept": kept,
            "base_rate_before": (n_overturn_before / n_suf) if n_suf else None,
            "base_rate_after_exclude": (n_overturn_after / kept) if kept else None,
            "n_yes_kept": len(pools["yes"]),
            "n_no_kept": len(pools["no"]),
        },
        "items": picked,
        "computed_at": _now(),
    }
    _write_json(IDS_PATH, document)
    _log(
        f"wrote {IDS_PATH.name} n={N_DEV} kept={kept} "
        f"dropped={n_dropped} excluded_pilot={n_excluded}"
    )
    return document


def _spend(rows: list[dict]) -> dict[str, float]:
    spent = {"drex": 0.0, "frontier": 0.0}
    for row in rows:
        lane = row.get("lane")
        if lane in spent:
            spent[lane] += float(row.get("cost_usd") or 0.0)
    return spent


def _would_exceed(lane: str, text: str, rows: list[dict]) -> str | None:
    spent = _spend(rows)
    estimate = fair._estimate_cost(lane, text)
    cap = NACE_CAP_USD if lane == "drex" else ROUTER_CAP_USD
    used = spent["drex" if lane == "drex" else "frontier"]
    if used >= cap or used + estimate > cap:
        return f"{lane} spend {used:.6f} + est {estimate:.6f} would exceed {cap:.2f}"
    return None


def _call(adapter, state: dict, item: dict, lane: str):
    last_error: Exception | None = None
    for attempt in range(TRANSIENT_RETRIES):
        try:
            result = adapter.decide(state, [fair.QUESTION], case_id=item["case_id"])
        except SystemExit:
            raise
        except Exception as exc:
            last_error = exc
            wait = 15 * (attempt + 1)
            _log(f"transport {lane} {item['case_id']} attempt {attempt + 1}: {type(exc).__name__}; sleep {wait}s")
            time.sleep(wait)
            continue
        if fair._transient(result.error):
            safe = fair._redact_error(result.error, "")
            wait = 15 * (attempt + 1)
            _log(f"transient {lane} {item['case_id']} attempt {attempt + 1}: {safe}; sleep {wait}s")
            time.sleep(wait)
            continue
        return result
    raise SystemExit(3) if last_error is None else SystemExit(3)


def _run_calls(document: dict) -> None:
    items = document["items"]
    rows = fair._dedupe(_load_jsonl(JSONL_PATH))
    done = {(row.get("case_id"), row.get("lane")) for row in rows if not row.get("simulated")}
    expected = {(item["case_id"], lane) for item in items for lane in ("drex", "frontier")}
    if expected <= done:
        _log(f"dev calls already complete n_pairs={len(items)}")
        return
    texts = fair._texts(fair.FROZEN.TRAIN_PATH, {item["row_index"] for item in items})
    drex, frontier = fair._require_live_adapters()
    adapters = {"drex": drex, "frontier": frontier}
    gpt_errors = sum(1 for row in rows if row.get("lane") == "frontier" and fair._is_error_row(row))
    _log(f"resume done={len(done)} remaining={len(expected - done)} gpt_errors={gpt_errors}")
    for item in items:
        body = texts[item["row_index"]]
        fair._verify_body(item, body)
        if fair.FROZEN.leaks(body):
            raise SystemExit(f"ABORT: leakage filter fired on frozen dev id {item['case_id']}")
        state = fair.FROZEN._state(body)
        for lane in ("drex", "frontier"):
            if (item["case_id"], lane) in done:
                continue
            reason = _would_exceed(lane, body, rows)
            if reason:
                _log(f"stop before call: {reason}")
                raise SystemExit(2)
            result = _call(adapters[lane], state, item, lane)
            try:
                fair._check_live(lane, result)
            except SystemExit:
                raise SystemExit(2)
            row = fair._row_from_result(item, lane, result, body)
            row["pilot"] = "g_persius_dev"
            row["split"] = "train"
            if row.get("simulated"):
                raise SystemExit(2)
            _append_jsonl(JSONL_PATH, row)
            rows.append(row)
            done.add((item["case_id"], lane))
            if lane == "frontier" and fair._is_error_row(row):
                gpt_errors += 1
            _log(
                f"{item['case_id']} {lane} choice={row['choice']} correct={row['correct']} "
                f"p_yes={row['p_yes']} cost={row['cost_usd']:.6f} "
                f"latency_ms={row['latency_ms']:.0f} error={row['error']}"
            )
            if gpt_errors > GPT_ERROR_ABORT:
                raise SystemExit(2)
    _log("dev calls finished")


def _clip(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return min(0.999, max(0.001, number))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _logit(value: float) -> float:
    clipped = _clip(value)
    assert clipped is not None
    return math.log(clipped / (1.0 - clipped))


def _p_yes(row: dict | None) -> float | None:
    if row is None or fair._is_error_row(row):
        return None
    value = row.get("p_yes")
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _choice_of(row: dict | None) -> str | None:
    if row is None or fair._is_error_row(row):
        return None
    choice = row.get("choice")
    return choice if choice in ("yes", "no") else None


def _confidence(row: dict | None) -> float | None:
    if row is None or fair._is_error_row(row):
        return None
    value = row.get("confidence")
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _yes_if(score: float | None) -> str | None:
    if score is None:
        return None
    return "yes" if score >= 0.5 else "no"


RuleFn = Callable[[dict | None, dict | None], tuple[str | None, tuple[str, ...]]]


def _rules() -> list[tuple[str, str, RuleFn]]:
    """(id, description, function). Order is the tie-break order after accuracy and cost."""
    found: list[tuple[str, str, RuleFn]] = []

    def weighted(weight: float) -> RuleFn:
        def predict(drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
            p_d, p_g = _p_yes(drex), _p_yes(gpt)
            if weight == 0.0:
                return _yes_if(p_g), ("frontier",)
            if weight == 1.0:
                return _yes_if(p_d), ("drex",)
            if p_d is None or p_g is None:
                return None, ("drex", "frontier")
            return _yes_if(weight * p_d + (1.0 - weight) * p_g), ("drex", "frontier")

        return predict

    def log_odds(weight: float) -> RuleFn:
        def predict(drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
            p_d, p_g = _p_yes(drex), _p_yes(gpt)
            if weight == 0.0:
                return _yes_if(p_g), ("frontier",)
            if weight == 1.0:
                return _yes_if(p_d), ("drex",)
            if p_d is None or p_g is None:
                return None, ("drex", "frontier")
            score = _sigmoid(weight * _logit(p_d) + (1.0 - weight) * _logit(p_g))
            return _yes_if(score), ("drex", "frontier")

        return predict

    for weight in WEIGHTED_W:
        found.append((f"weighted_{weight:.2f}", f"w*drex_p+(1-w)*gpt_p, w={weight}, yes if score>=0.5", weighted(weight)))
        if weight not in (0.0, 1.0):
            found.append(
                (
                    f"logodds_{weight:.2f}",
                    f"sigmoid(w*logit drex + (1-w)*logit gpt), w={weight}, yes if score>=0.5",
                    log_odds(weight),
                )
            )

    def confidence_route(tau: float) -> RuleFn:
        def predict(drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
            conf = _confidence(drex)
            if conf is not None and conf >= tau:
                return _choice_of(drex), ("drex",)
            return _choice_of(gpt), ("drex", "frontier")

        return predict

    for tau in CONF_TAUS:
        found.append(
            (
                f"drex_conf_{tau:.2f}",
                f"use Drex when Drex confidence>={tau}, else GPT",
                confidence_route(tau),
            )
        )

    def band(lo: float, hi: float) -> RuleFn:
        def predict(drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
            p_d = _p_yes(drex)
            if p_d is not None and (p_d <= lo or p_d >= hi):
                return _choice_of(drex), ("drex",)
            return _choice_of(gpt), ("drex", "frontier")

        return predict

    for lo, hi in BANDS:
        found.append((f"band_{lo:.2f}_{hi:.2f}", f"Drex when p_yes<={lo} or >={hi}, else GPT", band(lo, hi)))

    def either(_drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
        d_yes = _choice_of(_drex) == "yes"
        g_choice = _choice_of(gpt)
        if _choice_of(_drex) is None and g_choice is None:
            return None, ("drex", "frontier")
        if d_yes or g_choice == "yes":
            return "yes", ("drex", "frontier")
        return "no", ("drex", "frontier")

    def both(_drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
        if _choice_of(_drex) is None or _choice_of(gpt) is None:
            return None, ("drex", "frontier")
        if _choice_of(_drex) == "yes" and _choice_of(gpt) == "yes":
            return "yes", ("drex", "frontier")
        return "no", ("drex", "frontier")

    def disagree(drex: dict | None, gpt: dict | None) -> tuple[str | None, tuple[str, ...]]:
        d_choice, g_choice = _choice_of(drex), _choice_of(gpt)
        if d_choice is None and g_choice is None:
            return None, ("drex", "frontier")
        if d_choice is not None and d_choice == g_choice:
            return d_choice, ("drex", "frontier")
        d_conf, g_conf = _confidence(drex), _confidence(gpt)
        if d_conf is None and g_conf is None:
            return g_choice, ("drex", "frontier")
        if g_conf is None or (d_conf is not None and d_conf > g_conf):
            return d_choice, ("drex", "frontier")
        return g_choice, ("drex", "frontier")

    found.append(("or_yes", "yes if either lane says yes", either))
    found.append(("and_yes", "yes only if both lanes say yes", both))
    found.append(("disagree_higher_conf", "agree on the shared choice; on a split, take the higher confidence (GPT wins a tie)", disagree))
    return found


def _list_cost(row: dict | None) -> float:
    if row is None:
        return 0.0
    if row.get("lane") == "drex":
        return float(row.get("cost_usd") or 0.0)
    tokens_in = int(row.get("input_tokens") or 0)
    tokens_out = int(row.get("output_tokens") or 0)
    return (tokens_in * GPT_LIST_IN + tokens_out * GPT_LIST_OUT) / 1_000_000


def _policy_cost(drex: dict | None, gpt: dict | None, used: tuple[str, ...]) -> float:
    total = 0.0
    if "drex" in used:
        total += _list_cost(drex)
    if "frontier" in used:
        total += _list_cost(gpt)
    return total


def _policy_latency_ms(drex: dict | None, gpt: dict | None, used: tuple[str, ...]) -> float:
    total = 0.0
    if "drex" in used and drex is not None:
        total += float(drex.get("latency_ms") or 0.0)
    if "frontier" in used and gpt is not None:
        total += float(gpt.get("latency_ms") or 0.0)
    return total


def _pairs(rows: list[dict]) -> list[dict]:
    by: dict[str, dict[str, dict]] = {}
    order: list[str] = []
    for row in fair._dedupe(rows):
        case_id = row.get("case_id")
        lane = row.get("lane")
        if not case_id or lane not in ("drex", "frontier"):
            continue
        if case_id not in by:
            order.append(case_id)
            by[case_id] = {}
        by[case_id][lane] = row
    paired = []
    for case_id in order:
        lanes = by[case_id]
        if "drex" not in lanes or "frontier" not in lanes:
            continue
        gold = lanes["drex"].get("gold")
        if gold not in ("yes", "no") or lanes["frontier"].get("gold") != gold:
            raise SystemExit(f"ABORT: gold mismatch on {case_id}")
        paired.append({"case_id": case_id, "gold": gold, "drex": lanes["drex"], "frontier": lanes["frontier"]})
    return paired


def _score_rule(rule: RuleFn, paired: list[dict]) -> dict[str, Any]:
    combo_correct: list[bool] = []
    gpt_correct: list[bool] = []
    drex_correct: list[bool] = []
    costs: list[float] = []
    latencies: list[float] = []
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    n_gpt_calls = 0
    for item in paired:
        choice, used = rule(item["drex"], item["frontier"])
        gold = item["gold"]
        combo_correct.append(choice == gold)
        gpt_correct.append(bool(item["frontier"].get("correct")))
        drex_correct.append(bool(item["drex"].get("correct")))
        costs.append(_policy_cost(item["drex"], item["frontier"], used))
        latencies.append(_policy_latency_ms(item["drex"], item["frontier"], used))
        if "frontier" in used:
            n_gpt_calls += 1
        # A missing choice is wrong: false negative when gold is yes, false positive when gold is no.
        if gold == "yes":
            counts["tp" if choice == "yes" else "fn"] += 1
        else:
            counts["tn" if choice == "no" else "fp"] += 1
    n = len(paired)
    k = sum(1 for flag in combo_correct if flag)
    lo, hi = wilson_ci(k, n)
    positives = counts["tp"] + counts["fn"]
    negatives = counts["tn"] + counts["fp"]
    return {
        "n": n,
        "k": k,
        "accuracy": (k / n) if n else None,
        "ci95": [lo, hi],
        "ci_method": "wilson",
        "mcnemar_vs_gpt": mcnemar(combo_correct, gpt_correct),
        "mcnemar_vs_drex": mcnemar(combo_correct, drex_correct),
        "counts": counts,
        "sensitivity": (counts["tp"] / positives) if positives else None,
        "specificity": (counts["tn"] / negatives) if negatives else None,
        "cost_per_1000_usd_list": (statistics.fmean(costs) * 1000.0) if costs else None,
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
        "gpt_call_rate": (n_gpt_calls / n) if n else None,
        "cost_note": "Drex at recorded cost_usd. GPT at list $2/$10 per 1M from recorded tokens. Latency is the sum of the lanes the rule uses, sequential.",
    }


def _lane_alone(paired: list[dict], lane: str) -> dict[str, Any]:
    flags = [bool(item[lane].get("correct")) for item in paired]
    n = len(flags)
    k = sum(1 for flag in flags if flag)
    lo, hi = wilson_ci(k, n)
    costs = [_list_cost(item[lane]) for item in paired]
    latencies = [float(item[lane].get("latency_ms") or 0.0) for item in paired]
    return {
        "n": n,
        "k": k,
        "accuracy": (k / n) if n else None,
        "ci95": [lo, hi],
        "ci_method": "wilson",
        "cost_per_1000_usd_list": (statistics.fmean(costs) * 1000.0) if costs else None,
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
    }


def cmd_tune() -> dict:
    if RULE_PATH.exists():
        document = json.loads(RULE_PATH.read_text())
        _log(f"rule already frozen: {document.get('rule_id')}")
        return document
    rows = _load_jsonl(JSONL_PATH)
    paired = _pairs(rows)
    if len(paired) != N_DEV:
        raise SystemExit(f"ABORT: tune saw {len(paired)} complete dev pairs, want {N_DEV}")
    if any(row.get("simulated") for item in paired for row in (item["drex"], item["frontier"])):
        raise SystemExit("ABORT: simulated dev row")
    grid = []
    for rule_id, description, function in _rules():
        scored = _score_rule(function, paired)
        grid.append({"rule_id": rule_id, "description": description, "dev": scored})
        _log(
            f"dev {rule_id} acc={scored['accuracy']:.4f} "
            f"cost_per_1000={scored['cost_per_1000_usd_list']:.4f} "
            f"gpt_call_rate={scored['gpt_call_rate']:.3f}"
        )
    def sort_key(entry: dict) -> tuple:
        dev = entry["dev"]
        return (
            -float(dev["accuracy"]),
            float(dev["cost_per_1000_usd_list"]),
            entry["rule_id"],
        )

    ordered = sorted(grid, key=sort_key)
    winner = ordered[0]
    document = {
        "pilot": "g_persius_dev",
        "split": "train",
        "n": N_DEV,
        "selection": "max dev accuracy, then lower list cost per 1,000, then rule_id",
        "selection_note": "Grid fixed in scripts/g_persius_combo.py before this file was written. Test rows were not read.",
        "rule_id": winner["rule_id"],
        "description": winner["description"],
        "dev": winner["dev"],
        "baselines": {"drex": _lane_alone(paired, "drex"), "frontier": _lane_alone(paired, "frontier")},
        "grid": [{k: entry[k] for k in ("rule_id", "description", "dev")} for entry in ordered],
        "source_run": "data/runs/g_persius_dev.jsonl",
        "computed_at": _now(),
    }
    _write_json(RULE_PATH, document)
    _log(f"froze {winner['rule_id']} dev_acc={winner['dev']['accuracy']:.4f}")
    return document


def _rule_function(rule_id: str) -> RuleFn:
    for candidate, _description, function in _rules():
        if candidate == rule_id:
            return function
    raise SystemExit(f"ABORT: frozen rule {rule_id} is not in the pre-registered grid")


def cmd_apply() -> dict:
    if METRICS_PATH.exists():
        document = json.loads(METRICS_PATH.read_text())
        if document.get("applied") is True:
            _log(f"test already scored: {document.get('rule_id')} acc={document.get('test', {}).get('accuracy')}")
            return document
    if not RULE_PATH.exists():
        raise SystemExit("ABORT: no frozen rule; run tune first")
    rule = json.loads(RULE_PATH.read_text())
    rule_id = rule["rule_id"]
    function = _rule_function(rule_id)
    if not TEST_JSONL.exists():
        raise SystemExit("ABORT: missing data/runs/c_persius_test.jsonl")
    paired = _pairs(_load_jsonl(TEST_JSONL))
    if len(paired) != 400:
        raise SystemExit(f"ABORT: test pairs {len(paired)}, want 400")
    scored = _score_rule(function, paired)
    baselines = {"drex": _lane_alone(paired, "drex"), "frontier": _lane_alone(paired, "frontier")}
    document = {
        "pilot": "g_persius_combo",
        "applied": True,
        "status": "complete",
        "rule_id": rule_id,
        "description": rule["description"],
        "selection": rule["selection"],
        "dev_n": rule.get("n"),
        "dev_accuracy": (rule.get("dev") or {}).get("accuracy"),
        "dev_source": "data/runs/g_persius_rule.json",
        "test": scored,
        "baselines": baselines,
        "source_run": "data/runs/c_persius_test.jsonl",
        "new_test_calls": 0,
        "note": "Rule chosen on the train dev sample only. Applied once to the recorded 400 test rows. No new test model call. The test p-value is not corrected for the dev grid search.",
        "computed_at": _now(),
    }
    _write_json(METRICS_PATH, document)
    _log(
        f"test {rule_id} acc={scored['accuracy']:.4f} "
        f"ci={scored['ci95']} mcnemar_vs_gpt_p={scored['mcnemar_vs_gpt'].get('p_value')} "
        f"cost_per_1000={scored['cost_per_1000_usd_list']:.4f}"
    )
    return document


def cmd_run() -> None:
    document = _draw_document()
    try:
        _run_calls(document)
    except SystemExit as exc:
        code = exc.code
        if code in (2, 3):
            raise
        if isinstance(code, int):
            raise
        raise SystemExit(2) from exc
    rows = fair._dedupe(_load_jsonl(JSONL_PATH))
    paired = _pairs(rows)
    if len(paired) != N_DEV:
        raise SystemExit(3)
    cmd_tune()
    cmd_apply()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        cmd_run()
    elif command == "tune":
        cmd_tune()
    elif command == "apply":
        cmd_apply()
    elif command == "draw":
        _draw_document()
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()

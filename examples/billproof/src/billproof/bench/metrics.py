"""Accuracy, calibration, and the /api/report payload.

Brier and ECE use native probabilities only (Drex, and the Drex answers
inside a hybrid run). Frontier self-reported confidence is returned separately
and is never folded into those two scores.

`lanes.*.n` is the case count. DEMO_SCRIPT reads it as N cases; the Wilson
interval is on the question denominator `n_questions`.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

import numpy as np

# Exact McNemar below this many discordant pairs; chi-square with Yates after.
_EXACT_DISCORDANT = 25
_FAMILY_RE = re.compile(r"^(.*?)(?:_L\d+|_\d+)$")
_LANE_ORDER = ("frontier", "drex", "hybrid")


def wilson_ci(k: float, n: float, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson score interval for k successes in n trials. (None, None) if n is 0."""
    n = int(n)
    if n <= 0:
        return (None, None)
    z = float(z)
    phat = min(1.0, max(0.0, float(k) / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def bootstrap_ci(
    xs: Iterable[float], n_boot: int = 2000, seed: int = 0
) -> tuple[float | None, float | None]:
    """Percentile 95% CI of the mean. Seeded; empty input returns (None, None)."""
    arr = np.asarray(list(xs), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return (None, None)
    if arr.size == 1 or n_boot <= 0:
        mean = float(arr.mean())
        return (mean, mean)
    rng = np.random.default_rng(seed)
    draws = rng.choice(arr, size=(int(n_boot), int(arr.size)), replace=True)
    lo, hi = np.percentile(draws.mean(axis=1), [2.5, 97.5])
    return (float(lo), float(hi))


def _multiclass_brier(probs: dict, label: Any) -> float:
    text = str(label)
    total = 0.0
    hit = False
    for key, prob in probs.items():
        target = 1.0 if str(key) == text else 0.0
        hit = hit or target == 1.0
        total += (float(prob) - target) ** 2
    if not hit:
        total += 1.0
    return total


def brier(probs: Sequence, ys: Sequence) -> float:
    """Mean Brier score.

    Scalar probabilities pair with 0/1 labels: mean((p - y)^2).
    A dict or a vector of class probabilities is the multiclass score
    mean_i sum_k (p_ik - 1[y_i = k])^2. Class indices address a vector.
    """
    ps = list(probs)
    labels = list(ys)
    if len(ps) != len(labels):
        raise ValueError(f"brier length mismatch: {len(ps)} probs vs {len(labels)} labels")
    if not ps:
        return float("nan")
    first = ps[0]
    if isinstance(first, dict):
        scores = [_multiclass_brier(p, y) for p, y in zip(ps, labels)]
        return float(np.mean(np.asarray(scores, dtype=float)))
    if isinstance(first, (list, tuple, np.ndarray)):
        mat = np.asarray(ps, dtype=float)
        idx = np.asarray(labels, dtype=int)
        onehot = np.zeros_like(mat)
        onehot[np.arange(len(idx)), idx] = 1.0
        return float(np.mean(np.sum((mat - onehot) ** 2, axis=1)))
    pred = np.asarray(ps, dtype=float)
    obs = np.asarray(labels, dtype=float)
    return float(np.mean((pred - obs) ** 2))


def ece(
    confs: Sequence, ys: Sequence, bins: int = 10
) -> tuple[float | None, list[dict[str, float | int | None]]]:
    """Expected calibration error on equal-width bins of [0, 1].

    Returns (ece, occupied bins). Each bin is {lo, hi, conf, acc, n}.
    Empty bins are omitted so a reliability chart does not plot them at 0.
    Confidence outside [0, 1] is dropped. No usable pairs → (None, []).
    """
    if bins < 1:
        raise ValueError("bins must be >= 1")
    cs = list(confs)
    labels = list(ys)
    if len(cs) != len(labels):
        raise ValueError(f"ece length mismatch: {len(cs)} confs vs {len(labels)} labels")
    conf = []
    obs = []
    for raw_c, raw_y in zip(cs, labels):
        try:
            c = float(raw_c)
            y = float(raw_y)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(c) or not math.isfinite(y) or c < 0.0 or c > 1.0:
            continue
        if abs(y - 1.0) < 1e-9:
            y = 1.0
        elif abs(y) < 1e-9:
            y = 0.0
        else:
            continue
        conf.append(c)
        obs.append(y)
    if not conf:
        return (None, [])
    c_arr = np.asarray(conf, dtype=float)
    y_arr = np.asarray(obs, dtype=float)
    # 1e-9 keeps exact tenths (0.9 * 10 == 8.999999999999998) in the upper bin.
    idx = np.clip(np.floor(c_arr * bins + 1e-9).astype(int), 0, bins - 1)
    total = float(c_arr.size)
    ece_sum = 0.0
    out: list[dict[str, float | int | None]] = []
    for b in range(bins):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            continue
        mean_conf = float(c_arr[mask].mean())
        acc = float(y_arr[mask].mean())
        ece_sum += (n / total) * abs(acc - mean_conf)
        out.append(
            {
                "lo": b / bins,
                "hi": (b + 1) / bins,
                "conf": mean_conf,
                "acc": acc,
                "n": n,
            }
        )
    return (float(ece_sum), out)


def _chi2_sf_df1(stat: float) -> float:
    if stat <= 0.0:
        return 1.0
    return float(math.erfc(math.sqrt(stat / 2.0)))


def _binom_twosided(k: int, n: int) -> float:
    """Two-sided exact binomial test of k ~ Bin(n, 1/2). Matches scipy binomtest."""
    if n <= 0:
        return 1.0
    threshold = math.comb(n, k)
    total = sum(math.comb(n, i) for i in range(n + 1) if math.comb(n, i) <= threshold)
    return min(1.0, total / (2**n))


def _as_bool_list(values: Sequence, name: str) -> np.ndarray:
    out = []
    for item in values:
        if isinstance(item, (bool, np.bool_)):
            out.append(bool(item))
        elif isinstance(item, (int, float)) and float(item) in (0.0, 1.0):
            out.append(bool(item))
        else:
            raise ValueError(f"mcnemar {name} must be boolean, got {item!r}")
    return np.asarray(out, dtype=bool)


def mcnemar(a_correct: Sequence, b_correct: Sequence) -> dict[str, float]:
    """Paired McNemar test.

    b = first correct and second wrong. c = first wrong and second correct.
    Exact binomial when b + c < 25, otherwise chi-square with Yates continuity
    correction. `stat` is that continuity-corrected chi-square either way;
    `p_value` comes from the exact test on small samples.
    """
    a = _as_bool_list(a_correct, "a_correct")
    b = _as_bool_list(b_correct, "b_correct")
    if a.shape != b.shape:
        raise ValueError(f"mcnemar length mismatch: {a.size} vs {b.size}")
    b_count = int(np.sum(a & ~b))
    c_count = int(np.sum(~a & b))
    discordant = b_count + c_count
    if discordant == 0:
        stat = 0.0
        p_value = 1.0
    else:
        stat = (max(abs(b_count - c_count) - 1, 0) ** 2) / discordant
        if discordant < _EXACT_DISCORDANT:
            p_value = _binom_twosided(b_count, discordant)
        else:
            p_value = _chi2_sf_df1(stat)
    return {"b": float(b_count), "c": float(c_count), "p_value": float(p_value), "stat": float(stat)}


def family_of(name: str) -> str:
    """Question family: the prefix before `_L<digits>` or `_<digits>`."""
    match = _FAMILY_RE.match(str(name))
    if not match or not match.group(1):
        return str(name)
    return match.group(1)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _mean(values: Iterable[float]) -> float | None:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(arr.mean())


def _percentile(values: Iterable[float], q: float) -> float | None:
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.percentile(arr, q))


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
    return None


def _gold_label(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("answer")
    return item


def _mapping(row: dict, key: str) -> dict:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def _question_names(row: dict) -> list[str]:
    correct = _mapping(row, "correct")
    gold = _mapping(row, "gold")
    answers = _mapping(row, "answers")
    if correct:
        return [str(name) for name in correct]
    if gold:
        return [str(name) for name in gold]
    return [str(name) for name in answers]


def _question_correct(row: dict, name: str) -> bool | None:
    correct = _mapping(row, "correct")
    if name in correct and correct[name] is not None:
        parsed = _parse_bool(correct[name])
        if parsed is not None:
            return parsed
        return None
    answers = _mapping(row, "answers")
    gold = _mapping(row, "gold")
    answer = answers.get(name)
    if name not in gold or not isinstance(answer, dict):
        return None
    label = _gold_label(gold[name])
    choice = answer.get("choice")
    if label is None or choice is None:
        return None
    return str(choice) == str(label)


def _case_correct(row: dict, flags: list[bool | None]) -> bool | None:
    if "case_correct" in row and row.get("case_correct") is not None:
        return _parse_bool(row.get("case_correct"))
    known = [flag for flag in flags if flag is not None]
    if flags and len(known) == len(flags):
        return all(known)
    if row.get("error") and not known:
        return False
    return None


def _confidence(answer: dict | None) -> float | None:
    if not isinstance(answer, dict):
        return None
    conf = _num(answer.get("confidence"))
    if conf is None:
        probs = answer.get("probabilities")
        choice = answer.get("choice")
        if isinstance(probs, dict) and choice in probs:
            conf = _num(probs.get(choice))
        elif isinstance(probs, dict) and probs:
            conf = _num(max(probs.values()))
    if conf is None or conf < 0.0 or conf > 1.0:
        return None
    return conf


def _native(lane: str, answer: dict | None) -> bool:
    if not isinstance(answer, dict):
        return False
    kind = answer.get("confidence_kind")
    if kind == "native":
        return True
    if kind in ("self_reported", "rule"):
        return False
    if lane == "drex":
        return _confidence(answer) is not None
    if lane == "hybrid":
        # Kind omitted: a probability dict is Drex's; a bare confidence is not.
        return isinstance(answer.get("probabilities"), dict) and bool(answer.get("probabilities"))
    return isinstance(answer.get("probabilities"), dict) and bool(answer.get("probabilities"))


def _self_reported(lane: str, answer: dict | None) -> bool:
    if lane != "frontier" or not isinstance(answer, dict):
        return False
    kind = answer.get("confidence_kind")
    if kind in ("native", "rule"):
        return False
    return _confidence(answer) is not None


def _group_rows(rows: Sequence[dict]) -> tuple[dict[tuple[str, str], dict], list[str]]:
    by: dict[tuple[str, str], dict] = {}
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane = row.get("lane")
        case_id = row.get("case_id")
        if lane is None or case_id is None:
            continue
        cid = str(case_id)
        if cid not in order:
            order.append(cid)
        by[(cid, str(lane))] = row
    return by, order


def _thresholds() -> list[float]:
    return [round(i / 100, 2) for i in range(50, 100)]


def threshold_sweep(rows: Sequence[dict]) -> list[dict[str, Any]]:
    """Retune a single keep-threshold over logged Drex confidences.

    A question stays on Drex when native confidence >= threshold. Otherwise the
    recorded frontier answer is used. If that answer was not logged, the
    question is counted correct (a human caught it) and the point is flagged
    `assumed_human`. Escalating a case adds the frontier case cost once, on
    top of the Drex case cost.
    """
    by, order = _group_rows(rows)
    units: list[dict[str, Any]] = []
    for cid in order:
        drex = by.get((cid, "drex"))
        frontier = by.get((cid, "frontier"))
        source = drex if drex is not None else by.get((cid, "hybrid"))
        if source is None:
            continue
        source_lane = "drex" if drex is not None else "hybrid"
        questions = []
        for name in _question_names(source):
            answer = _mapping(source, "answers").get(name)
            if not isinstance(answer, dict) or not _native(source_lane, answer):
                continue
            conf = _confidence(answer)
            correct = _question_correct(source, name)
            if conf is None or correct is None:
                continue
            frontier_correct = _question_correct(frontier, name) if frontier is not None else None
            questions.append(
                {"conf": conf, "drex_correct": correct, "frontier_correct": frontier_correct}
            )
        if not questions:
            continue
        units.append(
            {
                "questions": questions,
                "drex_cost": _num(source.get("cost_usd")) if drex is not None else None,
                "frontier_cost": _num(frontier.get("cost_usd")) if frontier is not None else None,
            }
        )

    points = []
    for threshold in _thresholds():
        if not units:
            points.append(
                {
                    "threshold": threshold,
                    "automation_rate": None,
                    "error_automated": None,
                    "cost_per_case": None,
                    "accuracy": None,
                    "assumed_human": False,
                }
            )
            continue
        kept = kept_wrong = total = policy_correct = 0
        assumed = False
        costs: list[float] = []
        cost_known = True
        for unit in units:
            called_frontier = False
            for question in unit["questions"]:
                total += 1
                if question["conf"] + 1e-12 >= threshold:
                    kept += 1
                    ok = bool(question["drex_correct"])
                    kept_wrong += int(not ok)
                else:
                    frontier_correct = question["frontier_correct"]
                    if frontier_correct is None:
                        ok = True
                        assumed = True
                    else:
                        ok = bool(frontier_correct)
                        called_frontier = True
                policy_correct += int(ok)
            drex_cost = unit["drex_cost"]
            if drex_cost is None:
                cost_known = False
                continue
            extra = 0.0
            if called_frontier:
                if unit["frontier_cost"] is None:
                    cost_known = False
                    continue
                extra = unit["frontier_cost"]
            costs.append(drex_cost + extra)
        points.append(
            {
                "threshold": threshold,
                "automation_rate": (kept / total) if total else None,
                "error_automated": (kept_wrong / kept) if kept else None,
                "cost_per_case": _mean(costs) if cost_known and costs else None,
                "accuracy": (policy_correct / total) if total else None,
                "assumed_human": assumed,
            }
        )
    return points


def _prediction_correct(row: dict, name: str) -> bool | None:
    """Prediction quality is separate from resolving a case without human review."""
    if row.get("error"):
        return None
    answer = _mapping(row, "answers").get(name)
    gold = _mapping(row, "gold")
    if isinstance(answer, dict):
        label = _gold_label(gold.get(name))
        if label is not None and answer.get("choice") is not None:
            return str(answer["choice"]) == str(label)
        if answer.get("route") in ("human", "hard_cap"):
            return None  # No label: strict or assumed-human scores cannot reveal correctness.
    return _question_correct(row, name)


def _records(row: dict, lane: str) -> list[dict[str, Any]]:
    gold = _mapping(row, "gold")
    answers = _mapping(row, "answers")
    records = []
    for name in _question_names(row):
        answer = answers.get(name) if isinstance(answers.get(name), dict) else None
        label = _gold_label(gold.get(name)) if name in gold else None
        if label is None and isinstance(answer, dict) and _question_correct(row, name) is True:
            label = answer.get("choice")
        records.append(
            {
                "name": name,
                "family": family_of(name),
                "correct": _question_correct(row, name),
                "prediction_correct": _prediction_correct(row, name),
                "failed": bool(row.get("error")),
                "confidence": _confidence(answer),
                "probabilities": answer.get("probabilities") if isinstance(answer, dict) else None,
                "route": answer.get("route") if isinstance(answer, dict) else None,
                "gold": label,
                "native": _native(lane, answer),
                "self_reported": _self_reported(lane, answer),
                "has_answer": isinstance(answer, dict),
            }
        )
    return records


def _score_brier_ece(records: list[dict], native: bool) -> tuple[float | None, float | None, list]:
    chosen = [r for r in records if (r["native"] if native else r["self_reported"])]
    usable = [r for r in chosen if r["prediction_correct"] is not None and r["confidence"] is not None]
    if not usable:
        return None, None, []
    confs = [r["confidence"] for r in usable]
    ys = [1.0 if r["prediction_correct"] else 0.0 for r in usable]
    ece_value, bins = ece(confs, ys, bins=10)
    full_probs = all(isinstance(r["probabilities"], dict) and r["probabilities"] and r["gold"] is not None for r in usable)
    if full_probs:
        score = brier([r["probabilities"] for r in usable], [r["gold"] for r in usable])
    else:
        score = brier(confs, ys)
    if not math.isfinite(score):
        score = None
    return score, ece_value, bins


def _family_table(records: list[dict]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[bool]] = {}
    for record in records:
        if record["correct"] is None:
            continue
        buckets.setdefault(record["family"], []).append(bool(record["correct"]))
    table = {}
    for family in sorted(buckets):
        flags = buckets[family]
        table[family] = {"acc": float(np.mean(np.asarray(flags, dtype=float))), "n": len(flags)}
    return table


def _selection_stats(records: list[dict], lane: str, threshold: float | None = None) -> dict:
    """Question-level coverage; handoffs never enter automated-accuracy scoring.

    Threshold points only add abstentions to the recorded policy. They cannot
    recover human/hard-cap answers, replay verification, or invent human success.
    """
    answers = [r for r in records if r["has_answer"] and not r["failed"]]
    automated = [r for r in answers
                 if (lane != "hybrid" or r["route"] not in ("human", "hard_cap"))
                 and (threshold is None or threshold == 0 or
                      (r["confidence"] is not None and r["confidence"] + 1e-12 >= threshold))]
    scored = [r for r in automated if r["prediction_correct"] is not None]
    hits = sum(bool(r["prediction_correct"]) for r in scored)
    total, covered = len(answers), len(automated)
    coverage = covered / total if total else None
    if threshold is None and lane in ("frontier", "drex"):
        coverage = 1.0  # These standalone policies do not abstain.
    return {
        "coverage": coverage,
        "accuracy_on_automated": hits / len(scored) if scored else None,
        "accuracy_on_automated_ci": list(wilson_ci(hits, len(scored))),
        "human_rate": 1.0 - coverage if coverage is not None else None,
        "verify_rate": sum(r["route"] == "frontier_verified" for r in automated) / total if total else 0.0,
        "n_answers": total, "n_automated": covered,
        "n_automated_scored": len(scored), "n_automated_correct": hits,
    }


def _selective_points(records: list[dict], lane: str) -> list[dict]:
    return [{"threshold": threshold, **_selection_stats(records, lane, threshold)}
            for threshold in [0.0, *_thresholds(), 1.0]]


def _lane_report(case_rows: list[dict], lane: str) -> dict[str, Any]:
    per_case: list[list[dict]] = []
    case_flags: list[bool] = []
    latencies: list[float] = []
    costs: list[float] = []
    for row in case_rows:
        records = _records(row, lane)
        per_case.append(records)
        flag = _case_correct(row, [r["correct"] for r in records])
        if flag is not None:
            case_flags.append(flag)
        latency = _num(row.get("latency_ms"))
        if latency is not None:
            latencies.append(latency)
        cost = _num(row.get("cost_usd"))
        if cost is not None:
            costs.append(cost)
    flat = [record for records in per_case for record in records]
    scored = [record for record in flat if record["correct"] is not None]
    n_questions = len(scored)
    k = sum(1 for record in scored if record["correct"])
    accuracy = (k / n_questions) if n_questions else None
    lo, hi = wilson_ci(k, n_questions) if n_questions else (None, None)
    # Native rows only. A frontier lane of self-reports yields None here.
    brier_score, ece_value, native_bins = _score_brier_ece(flat, native=True)
    _, self_ece, self_bins = _score_brier_ece(flat, native=False)
    if lane != "frontier":
        self_ece, self_bins = None, []
    answers = [record for record in flat if record["has_answer"]]
    if lane == "hybrid":
        escalation = (
            sum(1 for record in answers if record["route"] != "drex_kept") / len(answers)
            if answers
            else None
        )
    elif lane == "drex":
        escalation = 0.0
    else:
        escalation = None
    return {
        "lane_block": {
            "accuracy": accuracy,
            "ci": [lo, hi],
            **_selection_stats(flat, lane),
            "mean_confidence": _mean(r["confidence"] for r in answers if r["confidence"] is not None),
            "mean_self_reported_confidence": _mean(r["confidence"] for r in answers if r["self_reported"] and r["confidence"] is not None),
            "case_pass": _mean([1.0 if flag else 0.0 for flag in case_flags]),
            "by_family": _family_table(flat),
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "cost_per_case_usd": _mean(costs),
            "brier": brier_score,
            "ece": ece_value,
            "self_reported_ece": self_ece,
            "escalation_rate": escalation,
            "n": len(case_rows),
            "n_questions": n_questions,
        },
        "native_bins": native_bins,
        "self_bins": self_bins,
        "selective": _selective_points(flat, lane),
    }


def _paired_diff_ci(
    pairs: list[tuple[float, float, float, float]], n_boot: int = 2000, seed: int = 0
) -> tuple[float, float, float] | None:
    """95% CI of pooled question accuracy(hybrid) - accuracy(frontier), cases resampled."""
    if not pairs:
        return None
    hk = np.asarray([p[0] for p in pairs], dtype=float)
    hn = np.asarray([p[1] for p in pairs], dtype=float)
    fk = np.asarray([p[2] for p in pairs], dtype=float)
    fn = np.asarray([p[3] for p in pairs], dtype=float)
    if float(hn.sum()) <= 0.0 or float(fn.sum()) <= 0.0:
        return None
    observed = float(hk.sum() / hn.sum() - fk.sum() / fn.sum())
    m = len(pairs)
    if m == 1 or n_boot <= 0:
        return (observed, observed, observed)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, m, size=(n_boot, m))
    hns = hn[idx].sum(axis=1)
    fns = fn[idx].sum(axis=1)
    ok = (hns > 0) & (fns > 0)
    diffs = np.full(n_boot, np.nan)
    diffs[ok] = hk[idx].sum(axis=1)[ok] / hns[ok] - fk[idx].sum(axis=1)[ok] / fns[ok]
    diffs = diffs[np.isfinite(diffs)]
    if diffs.size == 0:
        return None
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return (observed, float(lo), float(hi))


def _fmt_pts(value: float) -> str:
    if abs(value) < 5e-8:
        value = 0.0
    return f"{value:.1f}"


def _fmt_signed_pts(value: float) -> str:
    if abs(value) < 5e-8:
        value = 0.0
    return f"{value:+.1f}"


def _headline(
    hybrid: dict | None, frontier: dict | None, diff: tuple[float, float, float] | None, margin_pts: float
) -> tuple[str, bool]:
    if not hybrid or not frontier or hybrid.get("accuracy") is None or frontier.get("accuracy") is None or diff is None:
        return (
            "Drex+Frontier within n/a pts of Frontier (95% CI n/a) at n/a of the cost, n/a× faster",
            False,
        )
    _observed, lo, hi = diff
    behind = max(0.0, (float(frontier["accuracy"]) - float(hybrid["accuracy"])) * 100.0)
    hc, fc = hybrid.get("cost_per_case_usd"), frontier.get("cost_per_case_usd")
    if hc is not None and fc not in (None, 0, 0.0):
        cost = f"{100.0 * float(hc) / float(fc):.1f}%"
    else:
        cost = "n/a"
    hp, fp = hybrid.get("p50_ms"), frontier.get("p50_ms")
    if hp not in (None, 0, 0.0) and fp is not None:
        speed = f"{float(fp) / float(hp):.1f}"
    else:
        speed = "n/a"
    text = (
        f"Drex+Frontier within {_fmt_pts(behind)} pts of Frontier "
        f"(95% CI hybrid-frontier {_fmt_signed_pts(lo * 100.0)} to {_fmt_signed_pts(hi * 100.0)} pts) "
        f"at {cost} of the cost, {speed}× faster"
    )
    return text, bool(lo >= -(margin_pts / 100.0) - 1e-12)


def _coverage_headline(meta: dict, lanes: dict, legacy: str) -> str:
    """Pick a framing that cannot call abstention an incorrect medical prediction."""
    frontier, hybrid = lanes.get("frontier", {}), lanes.get("hybrid", {})

    def pct(value):
        return f"{value:.1%}" if value is not None else "unscored"

    prefix = "SIMULATED: " if meta.get("mode") == "SIMULATED" else ""
    if meta.get("bench") == "imr":
        accuracy = frontier.get("accuracy_on_automated")
        if accuracy is None:
            return prefix + "IMR: no scored Frontier predictions; compare coverage and accuracy on automated answers."
        confidence = frontier.get("mean_self_reported_confidence")
        confidence_text = f" with {pct(confidence)} mean self-reported confidence" if confidence is not None else ""
        first = f"Frontier answers {pct(frontier.get('coverage'))} at {pct(accuracy)} accuracy{confidence_text}."
        if hybrid.get("human_rate") is None:
            return prefix + first
        return prefix + first + (
            f" Drex+Frontier flags {pct(hybrid['human_rate'])} for human review; "
            f"{pct(hybrid.get('coverage'))} automated at {pct(hybrid.get('accuracy_on_automated'))} accuracy "
            f"({hybrid.get('n_automated_scored', 0)} scored automated answers)."
        )
    if hybrid.get("coverage") is not None and hybrid["coverage"] < 1:
        text = (f"Drex+Frontier automates {pct(hybrid['coverage'])} at "
                f"{pct(hybrid.get('accuracy_on_automated'))} accuracy on automated answers; "
                f"{pct(hybrid.get('human_rate'))} requires human review.")
        if frontier.get("accuracy_on_automated") is not None:
            text += f" Frontier answers {pct(frontier.get('coverage'))} at {pct(frontier['accuracy_on_automated'])} accuracy."
        return prefix + text
    if not hybrid or not frontier or hybrid.get("accuracy_on_automated") is None:
        return prefix + "Insufficient comparable lane results; inspect coverage and scored automated answers."
    return legacy


def _honesty(meta: dict, n_cases: int, n_questions: int) -> list[str]:
    lines = [f"N = {n_cases} cases, {n_questions} questions."]
    bench = str(meta.get("bench") or "synthetic")
    if bench == "imr":
        lines.append("Real IMR outcomes (pre-decision fields only).")
    else:
        lines.append("synthetic")
        lines.append("Upper bound: the same team wrote the documents and the labels.")
    if str(meta.get("mode") or "") == "SIMULATED":
        lines.append("SIMULATED")
    return lines


def _margin_pts(meta: dict) -> float:
    for key in ("ni_margin_pts", "noninferiority_margin_pts"):
        if meta.get(key) is not None:
            parsed = _num(meta.get(key))
            if parsed is not None:
                return parsed
    return 3.0


def _strict_question_correct(row: dict, name: str) -> bool | None:
    """Strict score: a handoff is never a model answer, so it is never correct."""
    answer = _mapping(row, "answers").get(name)
    if isinstance(answer, dict) and answer.get("route") in ("human", "hard_cap"):
        return False
    strict = _mapping(row, "correct_strict")
    if name in strict and strict[name] is not None:
        parsed = _parse_bool(strict[name])
        if parsed is not None:
            return parsed
    return _question_correct(row, name)


def strict_case_correct(row: dict) -> bool | None:
    """Case passes only if no question was handed to a human and every answer is right."""
    if row.get("error"):
        return None
    answers = _mapping(row, "answers")
    if any(
        isinstance(answer, dict) and answer.get("route") in ("human", "hard_cap")
        for answer in answers.values()
    ):
        return False
    if row.get("case_correct_strict") is not None:
        return _parse_bool(row.get("case_correct_strict"))
    return _case_correct(row, [_strict_question_correct(row, name) for name in _question_names(row)])


def question_units(rows: Sequence[dict], lane: str, *, strict: bool = True) -> list[dict[str, Any]]:
    """One record per scored question, with routing and confidence provenance kept apart.

    `human_routed` questions still carry Drex's draft, but they are not model
    answers: callers must exclude them from model accuracy and never assume the
    human got them right.
    """
    units: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_lane = row.get("lane")
        if row_lane is not None and str(row_lane) != lane:
            continue
        answers = _mapping(row, "answers")
        gold = _mapping(row, "gold")
        failed = bool(row.get("error"))
        for name in _question_names(row):
            answer = answers.get(name) if isinstance(answers.get(name), dict) else None
            route = answer.get("route") if answer is not None else None
            human_routed = route in ("human", "hard_cap")
            correct = (
                _strict_question_correct(row, name)
                if strict
                else _question_correct(row, name)
            )
            units.append(
                {
                    "case_id": str(row.get("case_id")),
                    "name": name,
                    "family": family_of(name),
                    "failed": failed,
                    "has_answer": bool(answer is not None and answer.get("choice") is not None),
                    "human_routed": human_routed,
                    "route": route,
                    # Routing uses the API scalar; probability calibration uses
                    # the probability assigned to the actual selected class.
                    "confidence": (
                        _num((answer.get("probabilities") or {}).get(answer.get("choice")))
                        if answer and _native(lane, answer) and isinstance(answer.get("probabilities"), dict)
                        else _confidence(answer)
                    ),
                    "routing_confidence": _confidence(answer),
                    "confidence_kind": answer.get("confidence_kind") if answer is not None else None,
                    "probabilities": answer.get("probabilities") if answer is not None else None,
                    "gold": _gold_label(gold.get(name)) if name in gold else None,
                    "native": _native(lane, answer),
                    "self_reported": _self_reported(lane, answer),
                    "correct": correct,
                }
            )
    return units


def select_error_cap_threshold(
    dev_rows: Sequence[dict],
    *,
    target_error: float = 0.01,
    lane: str = "drex",
    min_automated: int = 30,
) -> dict[str, Any]:
    """Lowest native-confidence cutoff whose automated error stays at or under the cap.

    This is the only tuning step: it raises unless every row is from the dev
    split, so test rows can never choose the threshold. The lowest qualifying
    cutoff automates the most questions.
    """
    splits = {str(row.get("split")) for row in dev_rows if isinstance(row, dict) and row.get("split")}
    if not splits:
        raise ValueError("threshold selection requires rows labelled split='dev'")
    if splits != {"dev"}:
        raise ValueError(f"threshold selection is dev-only, got split(s) {sorted(splits)}")

    units = [
        unit
        for unit in question_units(dev_rows, lane)
        if unit["has_answer"]
        and not unit["human_routed"]
        and unit["native"]
        and unit["confidence"] is not None
        and unit["correct"] is not None
    ]
    base = {
        "selection_split": "dev",
        "target_error_rate": float(target_error),
        "confidence_source": "native_probability_of_chosen_answer",
        "lane": lane,
        "min_automated": int(min_automated),
        "n_questions": len(units),
        "threshold": None,
        "achieved": False,
        "n_automated": 0,
        "n_wrong": 0,
        "error_automated": None,
        "accuracy_automated": None,
        "automation_rate": None,
        "reason": None,
        "best_available": None,
    }
    if not units:
        return {**base, "reason": "no_native_confidence"}
    if len(units) < min_automated:
        return {**base, "reason": "insufficient_sample"}

    candidates = sorted({unit["confidence"] for unit in units})
    best: dict[str, Any] | None = None
    for threshold in candidates:
        automated = [unit for unit in units if unit["confidence"] + 1e-12 >= threshold]
        if len(automated) < min_automated:
            continue
        wrong = sum(1 for unit in automated if not unit["correct"])
        error = wrong / len(automated)
        point = {
            "threshold": float(threshold),
            "n_automated": len(automated),
            "n_wrong": wrong,
            "error_automated": error,
            "accuracy_automated": 1.0 - error,
            "automation_rate": len(automated) / len(units),
        }
        if best is None or (error, -point["threshold"]) < (best["error_automated"], -best["threshold"]):
            best = point
        if error <= target_error + 1e-12:
            return {
                **base,
                **point,
                "threshold": float(threshold),
                "achieved": True,
                "best_available": point,
            }
    return {**base, "reason": "target_not_achievable", "best_available": best}


def evaluate_error_cap(
    rows: Sequence[dict], threshold: float | None, *, lane: str = "drex"
) -> dict[str, Any]:
    """Apply a fixed dev-selected cutoff; test rows are only measured, never tuned."""
    units = [
        unit
        for unit in question_units(rows, lane)
        if unit["correct"] is not None
    ]
    if threshold is None:
        return {
            "threshold": None,
            "n_questions": len(units),
            "n_automated": 0,
            "n_wrong": 0,
            "automation_rate": None,
            "error_automated": None,
            "accuracy_automated": None,
        }
    automated = [
        unit
        for unit in units
        if unit["has_answer"]
        and not unit["human_routed"]
        and unit["native"]
        and unit["confidence"] is not None
        and unit["confidence"] + 1e-12 >= float(threshold)
    ]
    wrong = sum(1 for unit in automated if not unit["correct"])
    return {
        "threshold": float(threshold),
        "n_questions": len(units),
        "n_automated": len(automated),
        "n_wrong": wrong,
        "automation_rate": len(automated) / len(units) if units else None,
        "error_automated": wrong / len(automated) if automated else None,
        "accuracy_automated": 1.0 - wrong / len(automated) if automated else None,
    }


def _lane_names(meta: dict, by: dict[tuple[str, str], dict]) -> list[str]:
    names: list[str] = []
    declared = meta.get("lanes")
    if isinstance(declared, (list, tuple)):
        names.extend(str(lane) for lane in declared if lane)
    for lane in _LANE_ORDER:
        if lane not in names and any(key[1] == lane for key in by):
            names.append(lane)
    for _cid, lane in by:
        if lane not in names:
            names.append(lane)
    return names


def build_report(run_rows: Sequence[dict], meta: dict | None) -> dict[str, Any]:
    """Assemble the GET /api/report object from one JSONL run."""
    meta = dict(meta or {})
    rows = [row for row in (run_rows or []) if isinstance(row, dict)]
    by, order = _group_rows(rows)
    margin = _margin_pts(meta)
    lanes: dict[str, dict] = {}
    reliability: dict[str, list] = {}
    packed: dict[str, dict] = {}
    for lane in _lane_names(meta, by):
        case_rows = [by[(cid, lane)] for cid in order if (cid, lane) in by]
        packed[lane] = _lane_report(case_rows, lane)
        block = packed[lane]["lane_block"]
        lanes[lane] = block
        reliability[lane] = packed[lane]["native_bins"]
    frontier_bins = packed.get("frontier", {}).get("self_bins") or []
    reliability["frontier_self_reported"] = frontier_bins
    # calibration.js reads reliability.frontier and labels it self-reported.
    if frontier_bins and not reliability.get("frontier"):
        reliability["frontier"] = [dict(bin_) for bin_ in frontier_bins]

    case_ids = {cid for cid, _lane in by}
    question_names = {
        (cid, record["name"])
        for (cid, lane), row in by.items()
        for record in _records(row, lane)
        if record["correct"] is not None
    }
    hybrid_rows = {cid: by[(cid, "hybrid")] for cid in order if (cid, "hybrid") in by}
    frontier_rows = {cid: by[(cid, "frontier")] for cid in order if (cid, "frontier") in by}
    paired_cases = [cid for cid in order if cid in hybrid_rows and cid in frontier_rows]
    hybrid_flags = []
    frontier_flags = []
    diff_pairs = []
    for cid in paired_cases:
        h_recs = _records(hybrid_rows[cid], "hybrid")
        f_recs = _records(frontier_rows[cid], "frontier")
        h_flag = _case_correct(hybrid_rows[cid], [r["correct"] for r in h_recs])
        f_flag = _case_correct(frontier_rows[cid], [r["correct"] for r in f_recs])
        if h_flag is None or f_flag is None:
            continue
        hybrid_flags.append(h_flag)
        frontier_flags.append(f_flag)
        diff_pairs.append(
            (
                float(sum(1 for r in h_recs if r["correct"])),
                float(sum(1 for r in h_recs if r["correct"] is not None)),
                float(sum(1 for r in f_recs if r["correct"])),
                float(sum(1 for r in f_recs if r["correct"] is not None)),
            )
        )
    if hybrid_flags:
        mcnemar_result = mcnemar(hybrid_flags, frontier_flags)
    else:
        mcnemar_result = {"b": 0.0, "c": 0.0, "p_value": None, "stat": None}
    headline, noninferior = _headline(
        lanes.get("hybrid"), lanes.get("frontier"), _paired_diff_ci(diff_pairs), margin
    )
    return {
        "meta": meta,
        "lanes": lanes,
        "reliability": reliability,
        "sweep": threshold_sweep(rows),
        "selective": {lane: result["selective"] for lane, result in packed.items()},
        "selective_basis": "Additional confidence-based abstention on recorded final answers; existing human/hard-cap routes remain excluded. Descriptive replay, not threshold tuning.",
        "mcnemar": mcnemar_result,
        "headline": _coverage_headline(meta, lanes, headline),
        "honesty": _honesty(meta, len(case_ids), len(question_names)) + [
            "Display accuracy_on_automated together with coverage and its Wilson interval. Legacy accuracy retains the recorded all-question score, including abstentions or configured human-success assumptions.",
            "Calibration uses prediction correctness, independently of whether an answer was sent to a human.",
        ],
        "noninferior": noninferior,
        "ni_margin_pts": margin,
    }

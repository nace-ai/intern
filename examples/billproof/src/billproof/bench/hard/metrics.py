"""Score hard-bench JSONL into data/metrics/hard_*.json."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from billproof.bench.hard.policy import HYBRID_TAU_GRID
from billproof.bench.hard.runner import jsonl_path, load_cases
from billproof.bench.metrics import brier, ece, family_of, mcnemar, wilson_ci
from billproof.config import DATA_DIR


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def metric(value, n, source, *, simulated: bool = False, **extra) -> dict:
    row = {
        "value": None if value is None or (isinstance(value, float) and not math.isfinite(value)) else value,
        "n": n, "source_run": source, "simulated": simulated, "computed_at": now(),
    }
    row.update(extra)
    return row


def read_jsonl(path: Path) -> list[dict]:
    """Last row per case_id. Earlier unpaid retries stay in the file and do not score twice.

    The two Drex test logs are larger than the commit limit. When the rich
    file is absent, the committed gzip is expanded into the same case rows.
    """
    if not path.exists():
        from billproof.bench.hard.drex_archive import rows_for_missing

        archived = rows_for_missing(path)
        if archived is not None:
            return archived
        return []
    order: list[str] = []
    last: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row.get("case_id")
        if not case_id:
            continue
        if case_id not in last:
            order.append(case_id)
        last[case_id] = row
    return [last[case_id] for case_id in order]


def _questions(row: dict) -> list[str]:
    return list((row.get("gold") or {}).keys())


def accuracy(rows: list[dict]) -> tuple[int, int, int]:
    correct = total = invalid = 0
    for row in rows:
        for name in _questions(row):
            total += 1
            if (row.get("invalid") or {}).get(name):
                invalid += 1
            elif (row.get("correct") or {}).get(name):
                correct += 1
    return correct, total, invalid


def case_accuracy(rows: list[dict]) -> tuple[int, int]:
    return sum(1 for row in rows if row.get("case_correct")), len(rows)


def family_accuracy(rows: list[dict]) -> dict[str, tuple[int, int]]:
    buckets: dict[str, list[int]] = {}
    for row in rows:
        for name, flag in (row.get("correct") or {}).items():
            if (row.get("invalid") or {}).get(name):
                flag = False
            buckets.setdefault(family_of(name), [0, 0])
            buckets[family_of(name)][1] += 1
            buckets[family_of(name)][0] += int(bool(flag))
    return {k: (v[0], v[1]) for k, v in buckets.items()}


def _pairs(rows: list[dict], native: bool) -> tuple[list, list, list]:
    """Brier inputs, ECE confidence, ECE correctness."""
    probs, labels, confs, ys = [], [], [], []
    for row in rows:
        for name, gold in (row.get("gold") or {}).items():
            payload = (row.get("answers") or {}).get(name) or {}
            if payload.get("invalid"):
                continue
            conf = payload.get("confidence")
            if conf is None:
                continue
            hit = bool((row.get("correct") or {}).get(name))
            kind = payload.get("confidence_kind")
            if native and kind != "native":
                continue
            if not native and kind == "native":
                continue
            confs.append(float(conf))
            ys.append(1.0 if hit else 0.0)
            if native and isinstance(payload.get("probabilities"), dict) and gold is not None:
                probs.append(payload["probabilities"])
                labels.append(gold)
    return probs, labels, confs, ys


def calibration(rows: list[dict], native: bool) -> tuple[float | None, float | None, int]:
    """Score, ECE, and the number of answers that entered the score."""
    probs, labels, confs, ys = _pairs(rows, native)
    if not confs:
        return None, None, 0
    ece_value, _bins = ece(confs, ys)
    if probs and len(probs) == len(confs):
        score = brier(probs, labels)
    else:
        score = brier(confs, ys)
    if isinstance(score, float) and not math.isfinite(score):
        score = None
    return score, ece_value, len(confs)


def latency_p50(rows: list[dict]) -> float | None:
    vals = sorted(float(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None and not r.get("error"))
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def cost_per_1000(rows: list[dict]) -> float | None:
    costs = [float(r.get("cost_usd") or 0) for r in rows if not r.get("error")]
    if not costs:
        return None
    return 1000.0 * sum(costs) / len(costs)


def risk_coverage(rows: list[dict], coverage: float) -> tuple[float | None, int]:
    items = []
    for row in rows:
        for name in _questions(row):
            payload = (row.get("answers") or {}).get(name) or {}
            conf = payload.get("confidence")
            if payload.get("invalid") or conf is None:
                conf = -1.0
            else:
                conf = float(conf)
            items.append((conf, bool((row.get("correct") or {}).get(name)) and not payload.get("invalid")))
    if not items:
        return None, 0
    items.sort(key=lambda item: -item[0])
    k = int(math.floor(coverage * len(items)))
    if k <= 0:
        return None, 0
    chosen = items[:k]
    return sum(1 for _, hit in chosen if hit) / k, k


def paired_flags(a_rows: list[dict], b_rows: list[dict]) -> tuple[list[bool], list[bool]]:
    b_map = {row["case_id"]: row for row in b_rows}
    left, right = [], []
    for row in a_rows:
        other = b_map.get(row["case_id"])
        if other is None:
            continue
        names = [n for n in _questions(row) if n in (other.get("gold") or {})]
        for name in names:
            left.append(bool((row.get("correct") or {}).get(name)) and not (row.get("invalid") or {}).get(name))
            right.append(bool((other.get("correct") or {}).get(name)) and not (other.get("invalid") or {}).get(name))
    return left, right


def rescore_hybrid(split: str, tau: float) -> list[dict]:
    """Rebuild hybrid answers at tau from stored Drex rows plus the dev GPT subset."""
    drex_rows = {row["case_id"]: row for row in read_jsonl(jsonl_path(split, "drex-v1"))}
    hybrid_rows = read_jsonl(jsonl_path(split, "hybrid"))
    rebuilt = []
    for row in hybrid_rows:
        drex = drex_rows.get(row["case_id"])
        if drex is None:
            continue
        gpt = row.get("gpt_answers") or {}
        ceiling = float(row.get("tau_ceiling") or tau)
        if tau > ceiling + 1e-9:
            continue
        correct, invalid, answers = {}, {}, {}
        for name, gold in (drex.get("gold") or {}).items():
            payload = (drex.get("answers") or {}).get(name) or {}
            conf = payload.get("confidence")
            use_gpt = conf is not None and float(conf) < tau and name in gpt
            source = gpt[name] if use_gpt else payload
            bad = bool(source.get("invalid")) or source.get("choice") not in ("yes", "no")
            choice = source.get("choice")
            answers[name] = {
                "choice": None if bad else choice,
                "confidence": source.get("confidence"),
                "confidence_kind": source.get("confidence_kind"),
                "probabilities": source.get("probabilities"),
                "invalid": bad,
                "route": "frontier" if use_gpt else "drex",
            }
            invalid[name] = bad
            correct[name] = (not bad) and choice == gold
        copied = dict(row)
        copied.update({
            "answers": answers, "correct": correct, "invalid": invalid, "gold": drex.get("gold"),
            "tau": tau, "case_correct": bool(correct) and all(correct.values()),
            "n_correct": sum(1 for v in correct.values() if v),
            "n_invalid": sum(1 for v in invalid.values() if v),
            "n_questions": len(correct),
        })
        rebuilt.append(copied)
    return rebuilt


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2) + "\n")


def emit_lane(split: str, lane: str, rows: list[dict], out_dir: Path) -> dict:
    source = f"hard_{split}_{lane}"
    simulated = any(bool(row.get("simulated")) for row in rows)
    correct, total, invalid = accuracy(rows)
    cases_ok, n_cases = case_accuracy(rows)
    low, high = wilson_ci(correct, total)
    case_low, case_high = wilson_ci(cases_ok, n_cases)
    prefix = out_dir / f"hard_{split}_{lane}"
    summary = {
        "question_accuracy": metric(correct / total if total else None, total, source, simulated=simulated, k=correct, ci_low=low, ci_high=high),
        "case_accuracy": metric(cases_ok / n_cases if n_cases else None, n_cases, source, simulated=simulated, k=cases_ok, ci_low=case_low, ci_high=case_high),
        "invalid": metric(invalid / total if total else None, total, source, simulated=simulated, k=invalid),
        "latency_p50_ms": metric(latency_p50(rows), n_cases, source, simulated=simulated),
        "cost_per_1000_usd": metric(cost_per_1000(rows), n_cases, source, simulated=simulated),
    }
    native = lane.startswith("drex")
    # Hybrid mixes native Drex probs and self-reported GPT probs. Score them separately.
    if lane == "hybrid":
        brier_n, ece_n, n_native = calibration(rows, native=True)
        brier_s, ece_s, n_self = calibration(rows, native=False)
        summary["brier_native"] = metric(brier_n, n_native, source, simulated=simulated)
        summary["ece_native"] = metric(ece_n, n_native, source, simulated=simulated)
        summary["brier_self_reported"] = metric(brier_s, n_self, source, simulated=simulated)
        summary["ece_self_reported"] = metric(ece_s, n_self, source, simulated=simulated)
    else:
        score, ece_value, n_cal = calibration(rows, native=native)
        key = "native" if native else "self_reported"
        summary[f"brier_{key}"] = metric(score, n_cal, source, simulated=simulated)
        summary[f"ece_{key}"] = metric(ece_value, n_cal, source, simulated=simulated)
    for coverage, name in ((0.80, "risk80"), (0.90, "risk90")):
        acc, k = risk_coverage(rows, coverage)
        low_r, high_r = wilson_ci(round((acc or 0) * k), k) if k else (None, None)
        summary[name] = metric(acc, k, source, simulated=simulated, coverage=coverage, ci_low=low_r, ci_high=high_r)
    families = {}
    for family, (k, n) in sorted(family_accuracy(rows).items()):
        lo, hi = wilson_ci(k, n)
        families[family] = metric(k / n if n else None, n, source, simulated=simulated, k=k, ci_low=lo, ci_high=hi)
        _write(Path(str(prefix) + f"_family_{family}.json"), families[family])
    summary["families"] = families
    for key, row in summary.items():
        if key == "families":
            continue
        _write(Path(str(prefix) + f"_{key}.json"), row)
    return summary


def emit_mcnemar(split: str, left_name: str, right_name: str, left: list[dict], right: list[dict], out_dir: Path) -> dict:
    a, b = paired_flags(left, right)
    result = mcnemar(a, b) if a else {"b": 0, "c": 0, "p_value": None, "stat": None}
    simulated = any(bool(row.get("simulated")) for row in left + right)
    row = metric(result.get("p_value"), len(a), f"hard_{split}_{left_name}_vs_{right_name}",
                 simulated=simulated, b=result.get("b"), c=result.get("c"), stat=result.get("stat"),
                 note="b = first lane correct and second wrong; c = first wrong and second correct")
    _write(out_dir / f"hard_{split}_mcnemar_{left_name}_vs_{right_name}.json", row)
    return row


def choose_tau(split: str) -> dict | None:
    ranked = []
    for tau in HYBRID_TAU_GRID:
        rows = rescore_hybrid(split, tau)
        if not rows:
            continue
        correct, total, _invalid = accuracy(rows)
        ranked.append((correct / total if total else 0, tau, total, correct))
    if not ranked:
        return None
    # Higher accuracy wins. Ties go to the higher tau (less GPT).
    ranked.sort(key=lambda item: (item[0], item[1]))
    best = ranked[-1]
    return {"tau": best[1], "question_accuracy": best[0], "n": best[2], "k": best[3],
            "grid": [{"tau": tau, "accuracy": acc, "n": n, "k": k} for acc, tau, n, k in ranked]}


def packing_agreement(split: str, packed_rows: list[dict], one_rows: list[dict], out_dir: Path) -> dict:
    """Same item, packed call versus one question per call. Agreement is not accuracy."""
    packed = {row["case_id"]: row for row in packed_rows}
    n = both_valid = agree = 0
    both_correct = one_only = packed_only = both_wrong = 0
    packed_invalid = one_invalid = 0
    for row in one_rows:
        other = packed.get(row["case_id"])
        if other is None:
            continue
        name = row.get("question") or next(iter(row.get("gold") or {}), None)
        if not name or name not in (other.get("gold") or {}):
            continue
        n += 1
        single = (row.get("answers") or {}).get(name) or {}
        batch = (other.get("answers") or {}).get(name) or {}
        single_bad = bool(single.get("invalid")) or single.get("choice") not in ("yes", "no")
        batch_bad = bool(batch.get("invalid")) or batch.get("choice") not in ("yes", "no")
        one_invalid += int(single_bad)
        packed_invalid += int(batch_bad)
        gold = (row.get("gold") or {}).get(name)
        single_ok = (not single_bad) and single.get("choice") == gold
        batch_ok = (not batch_bad) and batch.get("choice") == gold
        if single_ok and batch_ok:
            both_correct += 1
        elif single_ok:
            one_only += 1
        elif batch_ok:
            packed_only += 1
        else:
            both_wrong += 1
        if not single_bad and not batch_bad:
            both_valid += 1
            agree += int(single["choice"] == batch["choice"])
    low, high = wilson_ci(agree, both_valid) if both_valid else (None, None)
    simulated = any(bool(row.get("simulated")) for row in list(packed_rows) + list(one_rows))
    result = metric(
        agree / both_valid if both_valid else None, both_valid, f"hard_{split}_gpt_one_vs_gpt",
        simulated=simulated, k=agree, ci_low=low, ci_high=high, n_paired=n,
        packed_invalid=packed_invalid, one_invalid=one_invalid,
        both_correct=both_correct, one_only_correct=one_only,
        packed_only_correct=packed_only, both_wrong=both_wrong,
        note="Share of items where the packed GPT choice matches the one-question call. gpt-one is not a primary lane.",
    )
    _write(out_dir / f"hard_{split}_packing_agreement.json", result)
    return result


def report(split: str) -> dict:
    out_dir = DATA_DIR / "metrics"
    lanes = {}
    loaded = {}
    chosen = choose_tau(split) if split == "dev" else None
    for lane in ("drex-v1", "drex-finance", "gpt", "hybrid", "gpt-one"):
        rows = read_jsonl(jsonl_path(split, lane))
        if lane == "hybrid" and split == "dev" and chosen and rows:
            rows = rescore_hybrid(split, float(chosen["tau"]))
        if not rows:
            continue
        loaded[lane] = rows
        lanes[lane] = emit_lane(split, lane, rows, out_dir)
    tests = []
    order = [name for name in ("drex-v1", "drex-finance", "gpt", "hybrid", "gpt-one") if name in loaded]
    for i, left in enumerate(order):
        for right in order[i + 1:]:
            tests.append({
                "pair": [left, right],
                "mcnemar": emit_mcnemar(split, left, right, loaded[left], loaded[right], out_dir),
            })
    tau = chosen if split == "dev" else None
    packing = None
    if "gpt" in loaded and "gpt-one" in loaded:
        packing = packing_agreement(split, loaded["gpt"], loaded["gpt-one"], out_dir)
    payload = {"split": split, "lanes": lanes, "mcnemar": tests, "hybrid_tau": tau,
               "packing_agreement": packing, "computed_at": now()}
    _write(out_dir / f"hard_{split}_summary.json", payload)
    return payload


def cases_path(split: str) -> Path:
    return DATA_DIR / "synthetic" / "hard" / f"{split}.jsonl"


def load_split(split: str) -> list[dict]:
    return load_cases(cases_path(split))

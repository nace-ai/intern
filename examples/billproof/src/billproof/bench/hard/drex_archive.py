"""Compact Drex answer logs for the two test files that exceed 8MB.

 commits a small replay and leaves the full queue gitignored.
These two Hard logs follow that split. The committed file is one gzip JSON
line per question: case id, question id, answer, confidence, correct,
latency, and cost. The scorer expands that file into the case rows it
already scores. The original JSONL stays in `data/runs/full/`, which is
gitignored. `scripts/hard_drex_archive.py expand` rebuilds a scoring JSONL
there when the original is absent.

Brier in the report is the two-class score. The archive stores confidence,
which is the probability of the chosen answer. The expander rebuilds
`{yes, no}` from that, and the published 0.187 / 0.277 figures come out
the same.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from billproof.config import DATA_DIR, RUNS_DIR

LANES = ("drex-v1", "drex-finance")
MODELS = {"drex-v1": "drex-max-v1.1", "drex-finance": "drex-max-finance"}
FULL_DIR = RUNS_DIR / "full"


def filename(lane: str) -> str:
    return f"hard_test_{lane}.jsonl"


def compact_path(lane: str) -> Path:
    return RUNS_DIR / f"hard_test_{lane}.answers.jsonl.gz"


def full_path(lane: str) -> Path:
    return FULL_DIR / filename(lane)


def legacy_path(lane: str) -> Path:
    return RUNS_DIR / filename(lane)


def lane_of(path: Path) -> str | None:
    """Return the lane when `path` is one of the two archived test logs."""
    name = path.name
    for lane in LANES:
        if name == filename(lane):
            return lane
    return None


def source_path(lane: str) -> Path | None:
    """Rich log, wherever it currently sits. None when only the gzip exists."""
    for path in (full_path(lane), legacy_path(lane)):
        if path.exists():
            return path
    return None


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _gold_by_case() -> dict[str, dict[str, str]]:
    path = DATA_DIR / "synthetic" / "hard" / "test.jsonl"
    out = {}
    if not path.exists():
        return out
    for row in _read_jsonl(path):
        gold = row.get("gold")
        if gold:
            out[row["case_id"]] = gold
    return out


def _other(choice: str) -> str:
    return "no" if choice == "yes" else "yes"


def write_compact(lane: str) -> Path:
    """Write the gzip from the rich log. Refuses a row that disagrees with stored gold."""
    if lane not in LANES:
        raise ValueError(lane)
    source = source_path(lane)
    if source is None:
        raise FileNotFoundError(f"no full log for {lane}")
    gold_cases = _gold_by_case()
    last: dict[str, dict] = {}
    order: list[str] = []
    for row in _read_jsonl(source):
        case_id = row.get("case_id")
        if not case_id:
            continue
        if case_id not in last:
            order.append(case_id)
        last[case_id] = row
    lines: list[str] = []
    for case_id in order:
        row = last[case_id]
        stored = gold_cases.get(case_id) or {}
        for name in row.get("gold") or {}:
            answer = (row.get("answers") or {}).get(name) or {}
            invalid = bool(answer.get("invalid")) or bool((row.get("invalid") or {}).get(name))
            choice = answer.get("choice")
            if invalid or choice not in ("yes", "no"):
                choice = None
            correct = bool((row.get("correct") or {}).get(name)) and choice in ("yes", "no")
            if stored and choice in ("yes", "no"):
                if correct != (choice == stored[name]):
                    raise RuntimeError(f"{lane} {case_id} {name} disagrees with stored gold")
            lines.append(json.dumps({
                "case_id": case_id,
                "question_id": name,
                "answer": choice,
                "confidence": answer.get("confidence"),
                "correct": correct,
                "latency_ms": row.get("latency_ms"),
                "cost_usd": row.get("cost_usd"),
            }, separators=(",", ":")))
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode()
    path = compact_path(lane)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Empty filename keeps the absolute path out of the gzip header.
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
        handle.write(payload)
    return path


def read_compact(lane: str) -> list[dict]:
    path = compact_path(lane)
    if not path.exists():
        return []
    with gzip.open(path, "rt") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _probabilities(choice: str | None, confidence) -> dict | None:
    if choice not in ("yes", "no") or confidence is None:
        return None
    chosen = float(confidence)
    other = 1.0 - chosen
    if choice == "yes":
        return {"yes": chosen, "no": other}
    return {"yes": other, "no": chosen}


def expand_rows(lane: str, questions: list[dict] | None = None, gold_by_case: dict | None = None) -> list[dict]:
    """Case rows in file order. Cost and latency are the case values, not a sum."""
    if questions is None:
        questions = read_compact(lane)
    if gold_by_case is None:
        gold_by_case = _gold_by_case()
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for item in questions:
        case_id = item["case_id"]
        if case_id not in grouped:
            order.append(case_id)
            grouped[case_id] = []
        grouped[case_id].append(item)
    rows = []
    for case_id in order:
        items = grouped[case_id]
        stored = dict(gold_by_case.get(case_id) or {})
        answers, correct, invalid, gold = {}, {}, {}, {}
        for item in items:
            name = item["question_id"]
            choice = item.get("answer")
            bad = choice not in ("yes", "no")
            flag = bool(item.get("correct")) and not bad
            answers[name] = {
                "choice": None if bad else choice,
                "confidence": None if bad else item.get("confidence"),
                "confidence_kind": None if bad else "native",
                "probabilities": None if bad else _probabilities(choice, item.get("confidence")),
                "invalid": bad,
            }
            correct[name] = flag
            invalid[name] = bad
            if name in stored:
                gold[name] = stored[name]
            elif choice in ("yes", "no"):
                gold[name] = choice if flag else _other(choice)
        first = items[0]
        rows.append({
            "case_id": case_id,
            "split": "test",
            "lane": lane,
            "model": MODELS[lane],
            "simulated": False,
            "error": None,
            "latency_ms": first.get("latency_ms"),
            "cost_usd": first.get("cost_usd"),
            "answers": answers,
            "correct": correct,
            "invalid": invalid,
            "gold": gold,
            "case_correct": bool(correct) and all(correct.values()) and not any(invalid.values()),
            "n_questions": len(correct),
            "n_correct": sum(1 for value in correct.values() if value),
            "n_invalid": sum(1 for value in invalid.values() if value),
        })
    return rows


def rows_for_missing(path: Path) -> list[dict] | None:
    """Expanded rows when `path` is an archived log that is not on disk. None for any other path."""
    lane = lane_of(path)
    if lane is None:
        return None
    if path.exists() or not compact_path(lane).exists():
        return None
    return expand_rows(lane)


def case_ids_for(path: Path) -> set[str]:
    """Case ids already answered, so a resume does not buy the 300 again."""
    rows = rows_for_missing(path)
    if not rows:
        return set()
    return {row["case_id"] for row in rows if row.get("cost_usd") or row.get("n_questions")}


def missing_case_rows(seen_names: set[str]) -> list[dict]:
    """One case row per archived lane whose rich log was not already yielded."""
    out = []
    for lane in LANES:
        if filename(lane) in seen_names:
            continue
        out.extend(expand_rows(lane))
    return out


def expand_file(lane: str) -> Path:
    """Rebuild the scoring JSONL in the gitignored directory. Does not replace a rich log."""
    if lane not in LANES:
        raise ValueError(lane)
    target = full_path(lane)
    if target.exists():
        return target
    rows = expand_rows(lane)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    target.write_text(text)
    return target


def _near(name: str, got, expect, tol: float = 1e-6) -> None:
    if got is None or abs(float(got) - float(expect)) > tol:
        raise AssertionError(f"{name}: {got} != {expect}")


def _running(rows: list[dict]) -> tuple[tuple[float, int], float]:
    from billproof.bench.hard.metrics import accuracy

    correct = total = 0
    best = (-1.0, 0)
    for index, row in enumerate(rows, start=1):
        got, count, _invalid = accuracy([row])
        correct += got
        total += count
        acc = correct / total if total else 0.0
        if acc > best[0]:
            best = (acc, index)
    return best, (correct / total if total else 0.0)


def assert_published() -> None:
    """Raise unless the gzip reproduces the published Hard Drex test figures."""
    from billproof.bench.hard.metrics import (
        accuracy, calibration, case_accuracy, cost_per_1000, latency_p50, paired_flags, risk_coverage,
    )
    from billproof.bench.hard.outcome_score import score_lane as new_score
    from billproof.bench.hard.outcomes import score_lane as old_score
    from billproof.bench.hard.runner import load_cases
    from billproof.config import DATA_DIR, RUNS_DIR

    expect = {
        "drex-v1": {
            "k": 48118, "n": 56136, "brier": 0.18727935666693463, "ece": 0.041586123161852374,
            "p50": 893.8113005133346, "per_thousand": 0.5267767, "risk": 0.935267658323684, "risk_n": 44908,
            "line": (3402, 9306), "bill": (164, 300), "caught": 1645718, "wrong": 4710377, "gold_over": 2026976,
            "fee_tp": 1243, "fee_pos": 4522,
            "sub_q": (16083, 18706), "sub_line": (1151, 3101), "sub_bill": (54, 100),
            "sub_caught": 541954, "sub_wrong": 1547090,
            "new_line": (2673, 3101), "new_all": (0, 100), "new_caught": 639408, "new_false": 175090,
            "peak": 0.8576, "peak_case": 280, "end": 0.8572,
        },
        "drex-finance": {
            "k": 45749, "n": 56136, "brier": 0.27687034246034087, "ece": 0.09860531776693979,
            "p50": 806.8815449951217, "per_thousand": 0.5267767, "risk": 0.879197470383896, "risk_n": 44908,
            "line": (2493, 9306), "bill": (162, 300), "caught": 1591535, "wrong": 4402545, "gold_over": 2026976,
            "sub_q": (15270, 18706), "sub_line": (808, 3101), "sub_bill": (55, 100),
            "sub_caught": 512869, "sub_wrong": 1482299,
            "new_line": (2375, 3101), "new_all": (1, 100), "new_caught": 597269, "new_false": 150716,
            "peak": 0.8157, "peak_case": 280, "end": 0.8150,
        },
    }
    records = {
        row["case_id"]: row
        for row in load_cases(DATA_DIR / "synthetic" / "hard" / "test.jsonl")
    }
    order = list(json.loads((RUNS_DIR / "ladder_hard_ids.json").read_text())["run_order"])
    loaded = {}
    for lane, want in expect.items():
        if not compact_path(lane).exists():
            raise FileNotFoundError(compact_path(lane))
        rows = expand_rows(lane)
        loaded[lane] = rows
        correct, total, invalid = accuracy(rows)
        cases_ok, n_cases = case_accuracy(rows)
        brier, ece, _n = calibration(rows, native=True)
        p50 = latency_p50(rows)
        per_thousand = cost_per_1000(rows)
        risk, risk_n = risk_coverage(rows, 0.80)
        if (correct, total, invalid, cases_ok, n_cases) != (want["k"], want["n"], 0, 0, 300):
            raise AssertionError(f"{lane} questions {(correct, total, invalid, cases_ok, n_cases)}")
        _near(f"{lane} brier", brier, want["brier"], 1e-6)
        _near(f"{lane} ece", ece, want["ece"], 1e-9)
        _near(f"{lane} p50", p50, want["p50"], 1e-6)
        _near(f"{lane} per_thousand", per_thousand, want["per_thousand"], 1e-6)
        _near(f"{lane} risk", risk, want["risk"], 1e-9)
        if risk_n != want["risk_n"]:
            raise AssertionError(f"{lane} risk n {risk_n}")
        if round(brier, 3) != round(want["brier"], 3) or round(p50) != round(want["p50"]):
            raise AssertionError(f"{lane} rounded calibration")
        peak, end = _running(rows)
        if peak[1] != want["peak_case"] or round(peak[0], 4) != want["peak"] or round(end, 4) != want["end"]:
            raise AssertionError(f"{lane} running accuracy peak {peak} end {end}")
        old = old_score(rows, records)
        if (old["line_k"], old["line_n"]) != want["line"] or (old["bill_k"], old["bill_n"]) != want["bill"]:
            raise AssertionError(f"{lane} old line/bill {old['line_k']}/{old['line_n']} {old['bill_k']}/{old['bill_n']}")
        if old["strict_k"] != 0 or old["strict_n"] != 300:
            raise AssertionError(f"{lane} strict {old['strict_k']}/{old['strict_n']}")
        if (old["caught_cents"], old["wrong_cents"], old["gold_overcharge_cents"]) != (
            want["caught"], want["wrong"], want["gold_over"],
        ):
            raise AssertionError(
                f"{lane} dollars {old['caught_cents']} {old['wrong_cents']} {old['gold_overcharge_cents']}"
            )
        if "fee_tp" in want:
            fee = old["families"]["fee"]
            if fee[0] != want["fee_tp"] or fee[0] + fee[2] != want["fee_pos"]:
                raise AssertionError(f"{lane} fee recall {fee}")
        by_id = {row["case_id"]: row for row in rows}
        if any(case_id not in by_id for case_id in order):
            raise AssertionError(f"{lane} is missing a frozen id")
        subset = [by_id[case_id] for case_id in order]
        sub_k, sub_n, sub_invalid = accuracy(subset)
        if (sub_k, sub_n, sub_invalid) != (*want["sub_q"], 0):
            raise AssertionError(f"{lane} subset questions {sub_k}/{sub_n} invalid {sub_invalid}")
        sub_old = old_score(subset, records)
        if (sub_old["line_k"], sub_old["line_n"]) != want["sub_line"] or (sub_old["bill_k"], sub_old["bill_n"]) != want["sub_bill"]:
            raise AssertionError(f"{lane} subset line/bill {sub_old['line_k']} {sub_old['bill_k']}")
        if sub_old["strict_k"] != 0 or (sub_old["caught_cents"], sub_old["wrong_cents"]) != (want["sub_caught"], want["sub_wrong"]):
            raise AssertionError(f"{lane} subset dollars {sub_old['caught_cents']} {sub_old['wrong_cents']}")
        fresh = new_score(subset, records)
        if (fresh["line_k"], fresh["line_n"]) != want["new_line"] or (fresh["all_k"], fresh["all_n"]) != want["new_all"]:
            raise AssertionError(f"{lane} dispute line {fresh['line_k']} all {fresh['all_k']}")
        if (fresh["caught_cents"], fresh["gold_disputed_cents"], fresh["false_alarm_cents"]) != (
            want["new_caught"], 673677, want["new_false"],
        ):
            raise AssertionError(
                f"{lane} dispute dollars {fresh['caught_cents']} {fresh['gold_disputed_cents']} {fresh['false_alarm_cents']}"
            )
    left, right = paired_flags(loaded["drex-v1"], loaded["drex-finance"])
    b = sum(1 for a, c in zip(left, right) if a and not c)
    c = sum(1 for a, c in zip(left, right) if c and not a)
    if (b, c, len(left)) != (4074, 1705, 56136):
        raise AssertionError(f"mcnemar b={b} c={c} n={len(left)}")

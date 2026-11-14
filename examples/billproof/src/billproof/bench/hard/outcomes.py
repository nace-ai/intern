"""Outcome scores from recorded answers. No model calls.

Added after the first question-accuracy results. The same function scores
every lane. Question accuracy and the strict whole-case flag are unchanged.

Line verdict, the primary number: a line passes when the row contains all six
line questions and every one is valid and matches gold.

Bill verdict: bill_balance is present, valid, and matches gold.

Strict whole-case: the row was asked the full question set and case_correct
is true. A one-question row is left out of that denominator.

Precision and recall use gold "yes" as the positive class, which is what the
question text asks. An invalid choice is not a predicted yes.

Overcharge dollars are the patient-responsibility gap on lines the row was
asked about. Caught cents are max(0, printed - correct) where the model
answers pr "no" and that gap is positive. Wrongly disputed cents are the
printed patient responsibility where that printed amount equals the correct
amount and the model still answers pr "no". An undercharge is neither.
Fee-schedule excess stays in the fee precision and recall. The model did not
state a dollar amount.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from billproof.bench.hard.metrics import metric, read_jsonl
from billproof.bench.hard.runner import jsonl_path, load_cases
from billproof.bench.metrics import family_of, wilson_ci
from billproof.config import DATA_DIR, RUNS_DIR
from billproof.synth.hard.oracle import correct_pr

LINE_FAMILIES = ("dup", "mue", "ncci", "fee", "eob", "pr")
_LINE = re.compile(r"_L(\d+)$")
NOTE = "Added after the first results. Scored from recorded answers. No new model call."


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _line_no(name: str) -> int | None:
    match = _LINE.search(name)
    if not match:
        return None
    return int(match.group(1))


def _choice(row: dict, name: str) -> str | None:
    if (row.get("invalid") or {}).get(name):
        return None
    payload = (row.get("answers") or {}).get(name) or {}
    if payload.get("invalid"):
        return None
    choice = payload.get("choice")
    if choice not in ("yes", "no"):
        return None
    return choice


def _full_question_set(row: dict, record: dict) -> bool:
    return set(row.get("gold") or {}) == set(record.get("gold") or {})


def score_lane(rows: list[dict], records: dict[str, dict]) -> dict:
    """One scorer. `records` is case_id to the frozen case, gold included."""
    line_k = line_n = 0
    bill_k = bill_n = 0
    strict_k = strict_n = 0
    families: dict[str, list[int]] = {}
    caught = wrong = gold_over = priced = 0
    simulated = False
    for row in rows:
        simulated = simulated or bool(row.get("simulated"))
        record = records.get(row.get("case_id"))
        if record is None:
            continue
        if _full_question_set(row, record):
            strict_n += 1
            strict_k += int(bool(row.get("case_correct")))
        groups: dict[int, list[str]] = {}
        for name in row.get("gold") or {}:
            number = _line_no(name)
            if number is None:
                continue
            groups.setdefault(number, []).append(name)
        for names in groups.values():
            if {family_of(name) for name in names} != set(LINE_FAMILIES):
                continue
            line_n += 1
            if all(_choice(row, name) == (row.get("gold") or {}).get(name) for name in names):
                line_k += 1
        if "bill_balance" in (row.get("gold") or {}):
            bill_n += 1
            if _choice(row, "bill_balance") == row["gold"]["bill_balance"]:
                bill_k += 1
        for name, gold in (row.get("gold") or {}).items():
            family = family_of(name)
            bucket = families.setdefault(family, [0, 0, 0])
            pred_yes = _choice(row, name) == "yes"
            gold_yes = gold == "yes"
            if gold_yes and pred_yes:
                bucket[0] += 1
            elif pred_yes and not gold_yes:
                bucket[1] += 1
            elif gold_yes and not pred_yes:
                bucket[2] += 1
        caught_i, wrong_i, gold_i, priced_i = _dollars(row, record)
        caught += caught_i
        wrong += wrong_i
        gold_over += gold_i
        priced += priced_i
    return {
        "line_k": line_k, "line_n": line_n,
        "bill_k": bill_k, "bill_n": bill_n,
        "strict_k": strict_k, "strict_n": strict_n,
        "families": families,
        "caught_cents": caught, "wrong_cents": wrong,
        "gold_overcharge_cents": gold_over, "priced_lines": priced,
        "n_cases": len(rows), "simulated": simulated,
    }


def _dollars(row: dict, record: dict) -> tuple[int, int, int, int]:
    computed = correct_pr(record)
    eobs = {int(item["line_no"]): item for item in record.get("eobs") or []}
    caught = wrong = gold_over = priced = 0
    for line in record.get("lines") or []:
        number = int(line["line_no"])
        name = f"pr_L{number}"
        if name not in (row.get("gold") or {}):
            continue
        eob = eobs.get(number)
        correct = computed.get(number)
        if eob is None or correct is None:
            continue
        printed = int(eob["patient_resp_cents"])
        correct = int(correct)
        gap = max(0, printed - correct)
        priced += 1
        gold_over += gap
        if _choice(row, name) != "no":
            continue
        if gap > 0:
            caught += gap
        elif printed == correct:
            wrong += printed
    return caught, wrong, gold_over, priced


def _frozen_order() -> list[str]:
    return list(json.loads((RUNS_DIR / "ladder_hard_ids.json").read_text())["run_order"])


def gpt_subset_rows() -> list[dict]:
    """Frozen 100, in run order. Two ids reuse the earlier packed calls."""
    order = _frozen_order()
    subset = {row["case_id"]: row for row in read_jsonl(jsonl_path("test", "gpt", "hard_test_gpt_subset"))}
    prior = {row["case_id"]: row for row in read_jsonl(jsonl_path("test", "gpt"))}
    rows = []
    for case_id in order:
        row = subset.get(case_id) or prior.get(case_id)
        if row is not None:
            rows.append(row)
    return rows


def frozen_subset_rows(lane: str) -> list[dict]:
    """Recorded full-test rows restricted to the frozen 100 ids. No new call."""
    order = _frozen_order()
    found = {row["case_id"]: row for row in read_jsonl(jsonl_path("test", lane))}
    rows = [found[case_id] for case_id in order if case_id in found]
    if len(rows) != 100:
        raise RuntimeError(f"{lane} subset has {len(rows)} rows, expected 100")
    return rows


def _rate(k: int, n: int) -> float | None:
    if n <= 0:
        return None
    return k / n


def question_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Per-family question accuracy. Invalid answers stay in the denominator."""
    counts: dict[str, list[int]] = {}
    for row in rows:
        names = list((row.get("gold") or row.get("correct") or {}).keys())
        correct = row.get("correct") or {}
        for name in names:
            bucket = counts.setdefault(family_of(name), [0, 0])
            bucket[1] += 1
            bucket[0] += int(bool(correct.get(name)))
    return {family: {"k": pair[0], "n": pair[1]} for family, pair in sorted(counts.items())}


def write_outcomes(split: str = "test", lanes: tuple[str, ...] | None = None, merge: bool = False) -> dict:
    """Write hard_{split}_* outcome files for each lane that has a JSONL."""
    lanes = lanes or ("drex-v1", "drex-finance", "gpt", "gpt-one")
    records = {
        row["case_id"]: row
        for row in load_cases(DATA_DIR / "synthetic" / "hard" / f"{split}.jsonl")
    }
    out_dir = DATA_DIR / "metrics"
    out_file = out_dir / f"hard_{split}_outcomes.json"
    payload = {
        "split": split,
        "added_after_first_results": True,
        "note": NOTE,
        "lanes": {},
    }
    if merge and out_file.exists():
        payload = json.loads(out_file.read_text())
        payload["added_after_first_results"] = True
    for lane in lanes:
        if lane == "gpt-subset":
            rows = gpt_subset_rows()
        elif lane == "drex-v1-subset":
            rows = frozen_subset_rows("drex-v1")
        elif lane == "drex-finance-subset":
            rows = frozen_subset_rows("drex-finance")
        else:
            rows = read_jsonl(jsonl_path(split, lane))
        if not rows:
            continue
        scored = score_lane(rows, records)
        source = f"hard_{split}_{lane}"
        simulated = scored["simulated"]
        note = NOTE
        if lane == "system":
            note = (
                "Superseded by billproof-system. " + NOTE
                + " Fee, patient responsibility, and bill_balance are the rules engine"
                " on this lane, so those families match the stored gold by construction."
                " Drex answers duplicate, MUE, NCCI, and EOB."
            )
        if lane == "gpt-subset":
            note = (
                NOTE + " Frozen 100-id subset. HARD-TEST-0002 and HARD-TEST-0004 are the"
                " earlier packed calls, reused rather than bought again."
            )
        if lane in ("drex-v1-subset", "drex-finance-subset"):
            note = (
                NOTE + " Frozen 100 ids from the recorded 300-case answers."
                " No new model call."
            )
        if lane == "billproof-system":
            note = (
                NOTE + " Code computed the fee threshold, patient responsibility, and the"
                " balance sum from the case inputs and passed those numbers to Drex as facts."
                " Drex answered every yes/no question. The label function was not imported."
                " Arithmetic families are near-deterministic by design: money is computed by"
                " code, judgment by Drex."
            )
        if lane == "family_hybrid":
            note = (
                NOTE + " Drex first. Per-family cutoffs were frozen from dev before these"
                " calls. Escalated questions are a fresh GPT call with no Drex draft."
            )
        line = _rate(scored["line_k"], scored["line_n"])
        low, high = wilson_ci(scored["line_k"], scored["line_n"])
        bill = _rate(scored["bill_k"], scored["bill_n"])
        blow, bhigh = wilson_ci(scored["bill_k"], scored["bill_n"])
        strict = _rate(scored["strict_k"], scored["strict_n"])
        slow, shigh = wilson_ci(scored["strict_k"], scored["strict_n"])
        files = {
            "line_verdict": metric(
                line, scored["line_n"], source, simulated=simulated,
                k=scored["line_k"], ci_low=low, ci_high=high,
                added_after_first_results=True, note=note,
            ),
            "bill_verdict": metric(
                bill, scored["bill_n"], source, simulated=simulated,
                k=scored["bill_k"], ci_low=blow, ci_high=bhigh,
                added_after_first_results=True, note=note,
            ),
            "strict_case": metric(
                strict, scored["strict_n"], source, simulated=simulated,
                k=scored["strict_k"], ci_low=slow, ci_high=shigh,
                added_after_first_results=True,
                note="Same whole-case rule as the first results. Kept beside the line verdict.",
            ),
            "overcharge_caught_usd": metric(
                scored["caught_cents"] / 100.0, scored["n_cases"], source, simulated=simulated,
                caught_cents=scored["caught_cents"],
                gold_overcharge_cents=scored["gold_overcharge_cents"],
                priced_lines=scored["priced_lines"],
                catch_rate=_rate(scored["caught_cents"], scored["gold_overcharge_cents"]),
                added_after_first_results=True, note=note,
            ),
            "overcharge_wrongly_disputed_usd": metric(
                scored["wrong_cents"] / 100.0, scored["n_cases"], source, simulated=simulated,
                wrongly_disputed_cents=scored["wrong_cents"],
                priced_lines=scored["priced_lines"],
                added_after_first_results=True, note=note,
            ),
        }
        family_out = {}
        for family, (tp, fp, fn) in sorted(scored["families"].items()):
            prec_n = tp + fp
            rec_n = tp + fn
            plow, phigh = wilson_ci(tp, prec_n)
            rlow, rhigh = wilson_ci(tp, rec_n)
            family_out[family] = {
                "precision": metric(
                    _rate(tp, prec_n), prec_n, source, simulated=simulated,
                    k=tp, fp=fp, fn=fn, ci_low=plow, ci_high=phigh,
                    added_after_first_results=True, note=note,
                ),
                "recall": metric(
                    _rate(tp, rec_n), rec_n, source, simulated=simulated,
                    k=tp, fp=fp, fn=fn, ci_low=rlow, ci_high=rhigh,
                    added_after_first_results=True, note=note,
                ),
            }
            _write(out_dir / f"hard_{split}_{lane}_family_{family}_precision.json", family_out[family]["precision"])
            _write(out_dir / f"hard_{split}_{lane}_family_{family}_recall.json", family_out[family]["recall"])
        for key, body in files.items():
            _write(out_dir / f"hard_{split}_{lane}_{key}.json", body)
        q_counts = question_counts(rows)
        q_out = {}
        for family, item in q_counts.items():
            low, high = wilson_ci(item["k"], item["n"])
            q_out[family] = metric(
                _rate(item["k"], item["n"]), item["n"], source, simulated=simulated,
                k=item["k"], ci_low=low, ci_high=high,
                added_after_first_results=True, note=note,
            )
        payload["lanes"][lane] = {
            **files, "families": family_out, "question_accuracy": q_out,
            "n_cases": scored["n_cases"],
        }
    if "family_hybrid" in payload["lanes"]:
        alias = copy.deepcopy(payload["lanes"]["family_hybrid"])
        alias["note"] = "Same run as family_hybrid."
        payload["lanes"]["hybrid-subset"] = alias
    payload["unreported"] = {
        "hard_test_hybrid_subset": {
            "file": "data/runs/hard_test_hybrid_subset.jsonl",
            "n_rows": 12,
            "label": "stopped, rule changed before completion, not reported",
        }
    }
    _write(out_dir / f"hard_{split}_outcomes.json", payload)
    return payload

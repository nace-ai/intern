"""OK versus dispute, from recorded answers. No model calls.

Added after the first results. Whole-case accuracy over about 187 questions
is 0 for every lane, so it does not rank them. This module is the one scorer
for the shared 100. It does not change an answer, a cutoff, or the subset.

Line verdict, the primary number. On each bill line, dispute if any of
duplicate, MUE, NCCI, fee, or EOB is yes, or patient responsibility is no
(the printed amount is wrong). Otherwise the line is OK, which means
pay that line. The same function is applied to the model's choices and to
the oracle labels. Accuracy is how often those two verdicts match.

A line with any of the six choices missing or invalid has no verdict. It
stays in the denominator and is not a match. It adds neither caught dollars
nor false-alarm dollars. Its gold overcharge still counts, so a missing
verdict does not receive credit for those dollars.

Bills with every line verdict correct are a separate rate. One wrong or
undefined line fails the bill.

Bill verdict. Pay-as-billed when every line verdict is OK. Dispute when any
line verdict is dispute. A bill with no dispute and at least one undefined
line has no bill verdict, stays in the denominator, and is not a match.
The bill-total question is not a second trigger. It stays in the family
rates and in the strict whole-case count.

Dollars use the oracle's per-line patient amounts. The gold disputed amount
on a line is max(0, printed patient responsibility − correct patient
responsibility) when the gold verdict is dispute, and only when both amounts
exist. An undercharge, a duplicate, an MUE, an NCCI edit, a fee-schedule
excess, or a missing EOB can make the verdict dispute while this amount is
zero. Those lines stay in the verdict and in the family rates. They do not
move the dollar ratio. Caught dollars are that amount on lines the model
also disputes. The recall is caught dollars divided by gold disputed dollars.
False-alarm dollars are the printed patient responsibility on lines the
model disputes and the oracle calls OK. On those lines the printed amount
equals the correct amount. No threshold is fit. Wilson intervals are for
the counts. Dollar intervals are a case bootstrap, 2000 draws, seeds 0 and 1.

Strict whole-case is the old rule: every oracle question was asked, every
choice is valid, and every choice matches. Invalid answers stay in that
denominator.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from billproof.bench.hard.metrics import metric
from billproof.bench.hard.outcomes import frozen_subset_rows, gpt_subset_rows
from billproof.bench.hard.runner import load_cases
from billproof.bench.metrics import family_of, wilson_ci
from billproof.config import DATA_DIR, RUNS_DIR
from billproof.synth.hard.oracle import answers, correct_pr

LINE_FAMILIES = ("dup", "mue", "ncci", "fee", "eob", "pr")
DISPUTE_IF_YES = ("dup", "mue", "ncci", "fee", "eob")
N_BOOT = 2000
NOTE = (
    "Added after the first results because all-or-nothing over about 187 questions "
    "saturates at 0 for every lane. One deterministic OK-versus-dispute rule, no tuned "
    "threshold. Scored from recorded answers on the frozen 100. No new model call."
)


def line_verdict(choices: dict[str, str | None]) -> str | None:
    """Return 'dispute', 'ok', or None when any of the six choices is unusable."""
    for family in LINE_FAMILIES:
        if choices.get(family) not in ("yes", "no"):
            return None
    if any(choices[family] == "yes" for family in DISPUTE_IF_YES):
        return "dispute"
    if choices["pr"] == "no":
        return "dispute"
    return "ok"


def bill_verdict(line_verdicts: list[str | None]) -> str | None:
    """Pay-as-billed ('ok') only when every line verdict is OK."""
    if not line_verdicts:
        return None
    if any(item == "dispute" for item in line_verdicts):
        return "dispute"
    if any(item is None for item in line_verdicts):
        return None
    return "ok"


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


def _rate(k: int, n: int) -> float | None:
    if n <= 0:
        return None
    return k / n


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _percentile(stats: list[float], p: float) -> float:
    stats.sort()
    index = int(p * (len(stats) - 1))
    return stats[index]


def _bootstrap_ratio(pairs: list[tuple[int, int]], seed: int) -> tuple[float | None, float | None]:
    if not pairs or sum(den for _, den in pairs) <= 0:
        return None, None
    rng = random.Random(seed)
    n = len(pairs)
    stats: list[float] = []
    for _ in range(N_BOOT):
        num = den = 0
        for _draw in range(n):
            part_num, part_den = pairs[rng.randrange(n)]
            num += part_num
            den += part_den
        if den > 0:
            stats.append(num / den)
    if not stats:
        return None, None
    return _percentile(stats, 0.025), _percentile(stats, 0.975)


def _bootstrap_sum(values: list[int], seed: int) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    stats: list[float] = []
    for _ in range(N_BOOT):
        total = 0
        for _draw in range(n):
            total += values[rng.randrange(n)]
        stats.append(total)
    return _percentile(stats, 0.025), _percentile(stats, 0.975)


def _gold(record: dict) -> dict[str, str]:
    stored = record.get("gold") or {}
    fresh = answers(record)
    if stored and stored != fresh:
        raise RuntimeError(f"stored gold differs from the oracle for {record.get('case_id')}")
    return fresh


def _prepare(records: dict[str, dict]) -> dict[str, dict]:
    prepared = {}
    for case_id, record in records.items():
        prepared[case_id] = {
            "gold": _gold(record),
            "correct_pr": correct_pr(record),
            "lines": record.get("lines") or [],
            "eobs": {int(item["line_no"]): item for item in record.get("eobs") or []},
        }
    return prepared


def _line_amounts(spec: dict, line_no: int) -> tuple[int | None, int | None]:
    """Printed patient cents, and the overcharge gap. None when the line has no EOB amount."""
    eob = spec["eobs"].get(line_no)
    correct = spec["correct_pr"].get(line_no)
    if eob is None or correct is None:
        return None, None
    printed = int(eob["patient_resp_cents"])
    return printed, max(0, printed - int(correct))


def score_lane(rows: list[dict], records: dict[str, dict]) -> dict:
    """Score every row with the same rule. `records` maps case_id to the frozen case."""
    prepared = _prepare(records)
    line_k = line_n = 0
    all_k = all_n = 0
    bill_k = bill_n = 0
    strict_k = strict_n = 0
    undefined_lines = 0
    gold_dispute_lines = model_dispute_lines = 0
    gold_dispute_bills = model_dispute_bills = undefined_bills = 0
    families: dict[str, list[int]] = {}
    question_k = question_n = 0
    caught = gold_cents = false_alarm = ok_patient = 0
    catch_pairs: list[tuple[int, int]] = []
    false_values: list[int] = []
    simulated = False
    for row in rows:
        simulated = simulated or bool(row.get("simulated"))
        spec = prepared.get(row.get("case_id"))
        if spec is None:
            continue
        gold = spec["gold"]
        case_line_k = case_line_n = 0
        case_caught = case_gold = case_false = 0
        verdicts: list[str | None] = []
        gold_verdicts: list[str | None] = []
        for line in spec["lines"]:
            number = int(line["line_no"])
            model_choices = {family: _choice(row, f"{family}_L{number}") for family in LINE_FAMILIES}
            gold_choices = {family: gold[f"{family}_L{number}"] for family in LINE_FAMILIES}
            model = line_verdict(model_choices)
            gold_verdict = line_verdict(gold_choices)
            printed, gap = _line_amounts(spec, number)
            case_line_n += 1
            verdicts.append(model)
            gold_verdicts.append(gold_verdict)
            if gold_verdict == "dispute":
                gold_dispute_lines += 1
            if model == "dispute":
                model_dispute_lines += 1
            if model is None:
                undefined_lines += 1
            elif model == gold_verdict:
                case_line_k += 1
            if gold_verdict == "dispute" and gap is not None:
                case_gold += gap
                if model == "dispute":
                    case_caught += gap
            elif gold_verdict == "ok" and model == "dispute" and printed is not None:
                case_false += printed
                ok_patient += printed
            elif gold_verdict == "ok" and printed is not None:
                ok_patient += printed
        line_k += case_line_k
        line_n += case_line_n
        if case_line_n:
            all_n += 1
            all_k += int(case_line_k == case_line_n)
        model_bill = bill_verdict(verdicts)
        gold_bill = bill_verdict(gold_verdicts)
        if gold_bill is not None:
            bill_n += 1
            bill_k += int(model_bill is not None and model_bill == gold_bill)
            if gold_bill == "dispute":
                gold_dispute_bills += 1
            if model_bill == "dispute":
                model_dispute_bills += 1
            if model_bill is None:
                undefined_bills += 1
        asked = set((row.get("answers") or {}))
        if set(gold) <= asked:
            strict_n += 1
            strict_k += int(all(_choice(row, name) == choice for name, choice in gold.items()))
        for name, choice in gold.items():
            question_n += 1
            question_k += int(_choice(row, name) == choice)
            family = family_of(name)
            bucket = families.setdefault(family, [0, 0, 0])
            pred_yes = _choice(row, name) == "yes"
            gold_yes = choice == "yes"
            if gold_yes and pred_yes:
                bucket[0] += 1
            elif pred_yes and not gold_yes:
                bucket[1] += 1
            elif gold_yes and not pred_yes:
                bucket[2] += 1
        caught += case_caught
        gold_cents += case_gold
        false_alarm += case_false
        catch_pairs.append((case_caught, case_gold))
        false_values.append(case_false)
    catch_low, catch_high = _bootstrap_ratio(catch_pairs, seed=0)
    false_low, false_high = _bootstrap_sum(false_values, seed=1)
    return {
        "line_k": line_k, "line_n": line_n, "undefined_lines": undefined_lines,
        "gold_dispute_lines": gold_dispute_lines, "model_dispute_lines": model_dispute_lines,
        "all_k": all_k, "all_n": all_n,
        "bill_k": bill_k, "bill_n": bill_n,
        "gold_dispute_bills": gold_dispute_bills, "model_dispute_bills": model_dispute_bills,
        "undefined_bills": undefined_bills,
        "strict_k": strict_k, "strict_n": strict_n,
        "question_k": question_k, "question_n": question_n,
        "families": families,
        "caught_cents": caught, "gold_disputed_cents": gold_cents,
        "false_alarm_cents": false_alarm, "ok_patient_cents": ok_patient,
        "catch_ci": (catch_low, catch_high),
        "false_alarm_ci_cents": (false_low, false_high),
        "n_cases": len(rows), "simulated": simulated,
    }


def _frozen_order() -> list[str]:
    payload = json.loads((RUNS_DIR / "ladder_hard_ids.json").read_text())
    return list(payload["run_order"])


def _snapshot(path: Path) -> list[dict]:
    """Last row per case. A trailing partial line, from a live writer, is skipped."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    order: list[str] = []
    last: dict[str, dict] = {}
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise
        case_id = row.get("case_id")
        if not case_id:
            continue
        if case_id not in last:
            order.append(case_id)
        last[case_id] = row
    return [last[case_id] for case_id in order]


def _rows_in_order(path: Path, order: list[str]) -> list[dict]:
    found = {row["case_id"]: row for row in _snapshot(path)}
    return [found[case_id] for case_id in order if case_id in found]


def _lane_rows(order: list[str]) -> list[tuple[str, str, list[dict]]]:
    """(lane id, source file stem, rows) in report order. Ladder files are read only."""
    lanes: list[tuple[str, str, list[dict]]] = [
        ("drex-v1", "hard_test_drex-v1", frozen_subset_rows("drex-v1")),
        ("drex-finance", "hard_test_drex-finance", frozen_subset_rows("drex-finance")),
        ("billproof-system", "hard_test_billproof-system", frozen_subset_rows("billproof-system")),
        ("hybrid", "hard_test_family_hybrid", frozen_subset_rows("family_hybrid")),
        ("gpt", "hard_test_gpt_subset", gpt_subset_rows()),
    ]
    for path in sorted(RUNS_DIR.glob("ladder_hard_*.jsonl")):
        slug = path.name[len("ladder_hard_"):-len(".jsonl")]
        lanes.append((slug, path.stem, _rows_in_order(path, order)))
    return lanes


def _family_metrics(scored: dict, source: str, simulated: bool) -> dict[str, dict]:
    out = {}
    for family, (tp, fp, fn) in sorted(scored["families"].items()):
        prec_n = tp + fp
        rec_n = tp + fn
        plow, phigh = wilson_ci(tp, prec_n)
        rlow, rhigh = wilson_ci(tp, rec_n)
        out[family] = {
            "precision": metric(
                _rate(tp, prec_n), prec_n, source, simulated=simulated,
                k=tp, fp=fp, fn=fn, ci_low=plow, ci_high=phigh, ci_method="wilson",
                added_after_first_results=True, note=NOTE,
            ),
            "recall": metric(
                _rate(tp, rec_n), rec_n, source, simulated=simulated,
                k=tp, fp=fp, fn=fn, ci_low=rlow, ci_high=rhigh, ci_method="wilson",
                added_after_first_results=True, note=NOTE,
            ),
        }
    return out


def _lane_payload(lane: str, source: str, scored: dict) -> dict:
    simulated = scored["simulated"]
    line_low, line_high = wilson_ci(scored["line_k"], scored["line_n"])
    all_low, all_high = wilson_ci(scored["all_k"], scored["all_n"])
    bill_low, bill_high = wilson_ci(scored["bill_k"], scored["bill_n"])
    strict_low, strict_high = wilson_ci(scored["strict_k"], scored["strict_n"])
    q_low, q_high = wilson_ci(scored["question_k"], scored["question_n"])
    catch_low, catch_high = scored["catch_ci"]
    false_low, false_high = scored["false_alarm_ci_cents"]
    files = {
        "line_verdict": metric(
            _rate(scored["line_k"], scored["line_n"]), scored["line_n"], source,
            simulated=simulated, k=scored["line_k"], ci_low=line_low, ci_high=line_high,
            ci_method="wilson", undefined_lines=scored["undefined_lines"],
            gold_dispute_lines=scored["gold_dispute_lines"],
            model_dispute_lines=scored["model_dispute_lines"],
            added_after_first_results=True, note=NOTE,
        ),
        "bills_all_lines_correct": metric(
            _rate(scored["all_k"], scored["all_n"]), scored["all_n"], source,
            simulated=simulated, k=scored["all_k"], ci_low=all_low, ci_high=all_high,
            ci_method="wilson", added_after_first_results=True, note=NOTE,
        ),
        "bill_verdict": metric(
            _rate(scored["bill_k"], scored["bill_n"]), scored["bill_n"], source,
            simulated=simulated, k=scored["bill_k"], ci_low=bill_low, ci_high=bill_high,
            ci_method="wilson", gold_dispute_bills=scored["gold_dispute_bills"],
            model_dispute_bills=scored["model_dispute_bills"],
            undefined_bills=scored["undefined_bills"],
            added_after_first_results=True, note=NOTE,
        ),
        "strict_case": metric(
            _rate(scored["strict_k"], scored["strict_n"]), scored["strict_n"], source,
            simulated=simulated, k=scored["strict_k"], ci_low=strict_low, ci_high=strict_high,
            ci_method="wilson", added_after_first_results=True,
            note="Same whole-case rule as the first results. Kept beside the line verdict.",
        ),
        "overcharge_caught": metric(
            _rate(scored["caught_cents"], scored["gold_disputed_cents"]), scored["n_cases"], source,
            simulated=simulated, caught_cents=scored["caught_cents"],
            gold_disputed_cents=scored["gold_disputed_cents"],
            caught_usd=scored["caught_cents"] / 100.0,
            gold_disputed_usd=scored["gold_disputed_cents"] / 100.0,
            ci_low=catch_low, ci_high=catch_high, ci_method="case_bootstrap_2000_seed0",
            added_after_first_results=True, note=NOTE,
        ),
        "false_alarm_dollars": metric(
            scored["false_alarm_cents"] / 100.0, scored["n_cases"], source,
            simulated=simulated, false_alarm_cents=scored["false_alarm_cents"],
            ok_patient_cents=scored["ok_patient_cents"],
            false_alarm_rate=_rate(scored["false_alarm_cents"], scored["ok_patient_cents"]),
            ci_low=None if false_low is None else false_low / 100.0,
            ci_high=None if false_high is None else false_high / 100.0,
            ci_method="case_bootstrap_sum_2000_seed1",
            added_after_first_results=True, note=NOTE,
        ),
        "question_accuracy": metric(
            _rate(scored["question_k"], scored["question_n"]), scored["question_n"], source,
            simulated=simulated, k=scored["question_k"], ci_low=q_low, ci_high=q_high,
            ci_method="wilson", added_after_first_results=True, note=NOTE,
        ),
    }
    return {
        "lane": lane,
        "source_run": source,
        "n_cases": scored["n_cases"],
        "simulated": simulated,
        **files,
        "families": _family_metrics(scored, source, simulated),
    }


def write_hard_outcomes() -> dict:
    """Write data/metrics/hard_outcome_*.json for every recorded lane on the frozen 100."""
    order = _frozen_order()
    records = {
        row["case_id"]: row
        for row in load_cases(DATA_DIR / "synthetic" / "hard" / "test.jsonl")
        if row["case_id"] in set(order)
    }
    if len(records) != len(order):
        raise RuntimeError(f"frozen cases {len(records)} != ids {len(order)}")
    out_dir = DATA_DIR / "metrics"
    payload = {
        "split": "test",
        "subset": "ladder_hard_ids.json",
        "n_ids": len(order),
        "added_after_first_results": True,
        "primary": "line_verdict",
        "note": NOTE,
        "rule": (
            "Dispute a line if any of dup, mue, ncci, fee, or eob is yes, or pr is no. "
            "Otherwise the line is OK. Pay-as-billed only when every line is OK. "
            "Gold uses the same rule on the oracle labels."
        ),
        "dollars": (
            "Gold disputed amount is max(0, printed patient responsibility - correct "
            "patient responsibility) on a gold-dispute line. Caught dollars are that "
            "amount where the model verdict is also dispute. False-alarm dollars are "
            "the printed patient responsibility where the model disputes a gold-OK line. "
            "No threshold."
        ),
        "lanes": {},
    }
    for lane, source, rows in _lane_rows(order):
        if not rows:
            continue
        body = _lane_payload(lane, source, score_lane(rows, records))
        body["n_ids"] = len(order)
        body["prefix_of_run_order"] = [row["case_id"] for row in rows] == order[: len(rows)]
        payload["lanes"][lane] = body
        for key in (
            "line_verdict", "bills_all_lines_correct", "bill_verdict", "strict_case",
            "overcharge_caught", "false_alarm_dollars", "question_accuracy",
        ):
            _write(out_dir / f"hard_outcome_{lane}_{key}.json", body[key])
        for family, rates in body["families"].items():
            _write(out_dir / f"hard_outcome_{lane}_family_{family}_precision.json", rates["precision"])
            _write(out_dir / f"hard_outcome_{lane}_family_{family}_recall.json", rates["recall"])
    _write(out_dir / "hard_outcome_test.json", payload)
    return payload


def _main() -> None:
    payload = write_hard_outcomes()
    for lane, body in payload["lanes"].items():
        line = body["line_verdict"]
        bill = body["bill_verdict"]
        whole = body["bills_all_lines_correct"]
        strict = body["strict_case"]
        caught = body["overcharge_caught"]
        false_alarm = body["false_alarm_dollars"]
        print(
            f"{lane} n={body['n_cases']} "
            f"line={line['k']}/{line['n']} "
            f"all={whole['k']}/{whole['n']} "
            f"bill={bill['k']}/{bill['n']} "
            f"strict={strict['k']}/{strict['n']} "
            f"caught={caught['caught_cents']}/{caught['gold_disputed_cents']} "
            f"false={false_alarm['false_alarm_cents']} "
            f"undef_lines={line['undefined_lines']}"
        )


if __name__ == "__main__":
    _main()

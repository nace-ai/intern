"""Paged case lists from recorded run files. No model calls."""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from billproof.config import DATA_DIR

RUNS = DATA_DIR / "runs"
CC = DATA_DIR / "command_center"
PA_DEMO = DATA_DIR / "pa_demo"

_HARD_IDS = None


def _iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _hard_ids() -> set[str]:
    global _HARD_IDS
    if _HARD_IDS is None:
        document = json.loads((RUNS / "ladder_hard_ids.json").read_text())
        _HARD_IDS = set(document["run_order"])
    return _HARD_IDS


def _blank(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "gold_summary": None,
        "inputs_summary": {},
        "gold": None,
        "lanes": {},
    }


def _lane_box(**kwargs: Any) -> dict:
    box = {
        "correct": None,
        "choice": None,
        "confidence": None,
        "k": None,
        "n": None,
        "wrong": [],
        "answers": None,
    }
    box.update(kwargs)
    return box


def _finish_question_lane(bucket: dict) -> dict:
    n = bucket["n"]
    k = bucket["k"]
    wrong = bucket["wrong"]
    return _lane_box(
        correct=(n > 0 and k == n and not bucket.get("invalid")),
        k=k,
        n=n,
        wrong=wrong,
        confidence=None,
        choice=None,
        answers=bucket.get("answers"),
    )


def _add_pa(cases: dict[str, dict], path: Path, lane: str) -> None:
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        if not case_id:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        prediction = row.get("prediction") or {}
        score = row.get("score") or {}
        case["gold"] = row.get("gold_decision")
        case["gold_summary"] = row.get("gold_decision")
        case["inputs_summary"] = {
            "scenario": row.get("scenario"),
            "list_id": row.get("list_id"),
            "split": row.get("split"),
        }
        wrong = [name for name, ok in (score.get("per_fact") or {}).items() if not ok]
        case["lanes"][lane] = _lane_box(
            correct=bool(score.get("whole_case")),
            choice=prediction.get("decision"),
            confidence=None,
            k=score.get("n_facts_right"),
            n=score.get("n_facts"),
            wrong=wrong,
            answers={
                "decision": prediction.get("decision"),
                "action": prediction.get("action"),
                "scope": prediction.get("scope"),
                "facts": prediction.get("facts"),
                "criteria": prediction.get("criteria"),
                "score": {key: score.get(key) for key in ("whole_case", "decision", "facts_all", "criteria_all")},
            },
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
            model=row.get("model"),
            error=row.get("error"),
        )


def _add_binary(cases: dict[str, dict], path: Path, *, lane_field: str = "lane") -> None:
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        lane = row.get(lane_field)
        if not case_id or not lane:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        if case["gold"] is None:
            case["gold"] = row.get("gold")
            case["gold_summary"] = row.get("gold")
        if not case["inputs_summary"]:
            case["inputs_summary"] = {
                key: row.get(key)
                for key in ("row_index", "text_sha256", "variant", "state_sha256", "appeal_type")
                if key in row
            }
        case["lanes"][lane] = _lane_box(
            correct=bool(row.get("correct")) if row.get("error") is None else False,
            choice=row.get("choice"),
            confidence=row.get("confidence"),
            k=1 if row.get("correct") else 0,
            n=1,
            wrong=[] if row.get("correct") else [row.get("variant") or "choice"],
            answers={
                "choice": row.get("choice"),
                "confidence": row.get("confidence"),
                "confidence_kind": row.get("confidence_kind"),
                "p_yes": row.get("p_yes"),
                "probabilities": row.get("probabilities"),
                "correct": row.get("correct"),
            },
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
            model=row.get("model"),
            error=row.get("error"),
        )


def _consume_hard_row(bucket: dict, row: dict) -> None:
    if row.get("question_id"):
        bucket["n"] += 1
        ok = bool(row.get("correct"))
        if ok:
            bucket["k"] += 1
        else:
            if len(bucket["wrong"]) < 40:
                bucket["wrong"].append(row["question_id"])
        if bucket.get("latency_ms") is None:
            bucket["latency_ms"] = row.get("latency_ms")
        if bucket.get("cost_usd") is None:
            bucket["cost_usd"] = row.get("cost_usd")
        if bucket.get("answers") is not None:
            bucket["answers"][row["question_id"]] = {
                "choice": row.get("answer") or row.get("choice"),
                "confidence": row.get("confidence"),
                "correct": ok,
            }
        return
    correct = row.get("correct") or {}
    if isinstance(correct, dict):
        bucket["n"] = len(correct)
        bucket["k"] = sum(1 for flag in correct.values() if flag)
        bucket["wrong"] = [name for name, flag in correct.items() if not flag][:40]
    answers = row.get("answers") or {}
    if bucket.get("answers") is not None and isinstance(answers, dict):
        packed = {}
        for name, answer in answers.items():
            if isinstance(answer, dict):
                packed[name] = {
                    "choice": answer.get("choice"),
                    "confidence": answer.get("confidence"),
                    "correct": (correct or {}).get(name) if isinstance(correct, dict) else None,
                }
            else:
                packed[name] = {"choice": answer}
        bucket["answers"] = packed
    bucket["choice"] = None
    bucket["confidence"] = None
    bucket["latency_ms"] = row.get("latency_ms")
    bucket["cost_usd"] = row.get("list_cost_usd", row.get("cost_usd"))
    bucket["model"] = row.get("model")
    bucket["error"] = row.get("error")
    if isinstance(row.get("gold"), dict):
        bucket["gold"] = row.get("gold")


def _add_hard_file(cases: dict[str, dict], path: Path, lane: str, *, restrict: bool, with_answers: bool) -> None:
    ids = _hard_ids() if restrict else None
    buckets: dict[str, dict] = {}
    for row in _iter_jsonl(path):
        case_id = row.get("case_id")
        if not case_id:
            continue
        if ids is not None and case_id not in ids and lane in ("drex-v1", "drex-finance", "gpt"):
            continue
        bucket = buckets.setdefault(
            case_id,
            {"n": 0, "k": 0, "wrong": [], "answers": {} if with_answers else None, "invalid": False, "gold": None},
        )
        _consume_hard_row(bucket, row)
    for case_id, bucket in buckets.items():
        case = cases.setdefault(case_id, _blank(case_id))
        if bucket.get("gold") is not None and case["gold"] is None:
            case["gold"] = {"questions": bucket["gold"]}
            case["gold_summary"] = f"{bucket['n']} questions"
        elif case["gold_summary"] is None:
            case["gold_summary"] = f"{bucket['n']} questions"
        case["inputs_summary"] = {"kind": "BillProof-Hard synthetic bill", "case_id": case_id}
        finished = _finish_question_lane(bucket)
        finished["latency_ms"] = bucket.get("latency_ms")
        finished["cost_usd"] = bucket.get("cost_usd")
        finished["model"] = bucket.get("model")
        finished["error"] = bucket.get("error")
        case["lanes"][lane] = finished


def _add_cc_queue(cases: dict[str, dict]) -> None:
    for row in _iter_jsonl(CC / "queue_replay.jsonl"):
        case_id = row.get("case_id")
        if not case_id:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        case["lanes"]["drex"] = _lane_box(
            correct=bool(row.get("correct")),
            choice=row.get("decision"),
            k=1 if row.get("correct") else 0,
            n=1,
            wrong=[] if row.get("correct") else ["decision"],
            answers={"decision": row.get("decision"), "colour": row.get("colour"), "correct": row.get("correct")},
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
        )
        case["gold_summary"] = row.get("decision") if row.get("correct") else "differs from recorded decision"
    for row in _iter_jsonl(CC / "queue_gpt.jsonl"):
        case_id = row.get("case_id")
        if not case_id:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        case["gold"] = row.get("gold_decision")
        case["gold_summary"] = row.get("gold_label") or row.get("gold_decision")
        case["inputs_summary"] = {"scenario": row.get("scenario"), "split": row.get("split")}
        case["lanes"]["gpt"] = _lane_box(
            correct=bool(row.get("correct")),
            choice=row.get("decision"),
            k=1 if row.get("correct") else 0,
            n=1,
            wrong=[] if row.get("correct") else ["decision"],
            answers={"decision": row.get("decision"), "label": row.get("label"), "correct": row.get("correct")},
            latency_ms=row.get("latency_ms"),
            model=row.get("model"),
        )


def _add_rule(cases: dict[str, dict]) -> None:
    for row in _iter_jsonl(CC / "rule_change.jsonl"):
        case_id = row.get("case_id")
        if not case_id:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        old = row.get("old_label") or row.get("before")
        new = row.get("new_label") or row.get("after")
        flipped = bool(row.get("flip") or (old is not None and new is not None and old != new))
        case["gold_summary"] = old
        case["gold"] = {"before": old}
        case["inputs_summary"] = {"list_id": row.get("list_id"), "simulated": True}
        case["lanes"]["simulated_and"] = _lane_box(
            correct=not flipped,
            choice=new or row.get("flip"),
            k=0 if flipped else 1,
            n=1,
            wrong=[row.get("flip")] if flipped and row.get("flip") else ([] if not flipped else ["flip"]),
            answers={key: row.get(key) for key in ("old_label", "new_label", "flip", "before", "after") if key in row},
        )


def _add_decides(cases: dict[str, dict]) -> None:
    for row in _iter_jsonl(CC / "what_decides.jsonl"):
        case_id = row.get("case_id")
        if not case_id:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        agree = bool(row.get("agree"))
        case["gold"] = {"kind": row.get("gold_kind"), "records": row.get("gold_records")}
        case["gold_summary"] = row.get("gold_kind")
        case["inputs_summary"] = {"list_id": row.get("list_id"), "publisher": row.get("publisher")}
        case["lanes"]["drex"] = _lane_box(
            correct=agree,
            choice=row.get("predicted"),
            k=1 if agree else 0,
            n=1,
            wrong=[] if agree else ["deciding_record"],
            answers={
                "predicted": row.get("predicted"),
                "predicted_fact": row.get("predicted_fact"),
                "deciding_records": row.get("deciding_records"),
                "agree": agree,
            },
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
        )


def _add_chain(cases: dict[str, dict]) -> None:
    for slug in ("PA-D1", "PA-D2", "PA-D3", "PA-D4", "PA-D5"):
        path = PA_DEMO / slug / "evidence.json"
        if not path.exists():
            continue
        document = json.loads(path.read_text())
        gold = document.get("gold_decision")
        decision = document.get("decision")
        gpt = (document.get("gpt_check") or {}).get("gpt_decision")
        case = _blank(slug)
        case["gold"] = gold
        case["gold_summary"] = gold
        case["inputs_summary"] = {
            "slug": slug,
            "policy": (document.get("provenance") or {}).get("policy_document_title"),
        }
        case["lanes"]["chain"] = _lane_box(
            correct=decision == gold,
            choice=decision,
            k=1 if decision == gold else 0,
            n=1,
            wrong=[] if decision == gold else ["decision"],
            answers={"decision": decision, "total_cost_usd": document.get("total_cost_usd"), "timings": document.get("timings")},
        )
        case["lanes"]["gpt_check"] = _lane_box(
            correct=gpt == gold,
            choice=gpt,
            k=1 if gpt == gold else 0,
            n=1,
            wrong=[] if gpt == gold else ["gpt_decision"],
            answers={"gpt_decision": gpt},
        )
        cases[slug] = case


def _add_synthetic(cases: dict[str, dict]) -> None:
    for row in _iter_jsonl(RUNS / "real_synthetic_test.jsonl"):
        case_id = row.get("case_id")
        lane = row.get("lane")
        if not case_id or not lane:
            continue
        case = cases.setdefault(case_id, _blank(case_id))
        correct = row.get("correct") or {}
        gold = row.get("gold") or {}
        case["gold"] = gold
        case["gold_summary"] = f"{sum(1 for flag in correct.values() if flag)}/{len(correct)} questions"
        case["inputs_summary"] = {"case_id": case_id, "split": row.get("split"), "bench": "synthetic"}
        wrong = [name for name, flag in correct.items() if not flag]
        answers = {}
        for name, answer in (row.get("answers") or {}).items():
            if isinstance(answer, dict):
                answers[name] = {
                    "choice": answer.get("choice"),
                    "confidence": answer.get("confidence"),
                    "correct": correct.get(name),
                }
        case["lanes"][lane] = _lane_box(
            correct=bool(row.get("case_correct")),
            k=sum(1 for flag in correct.values() if flag),
            n=len(correct),
            wrong=wrong,
            answers=answers,
            latency_ms=row.get("latency_ms"),
            cost_usd=row.get("cost_usd"),
            model=row.get("model"),
        )


def _join_persius_ids(cases: dict[str, dict]) -> None:
    path = RUNS / "c_persius_test_ids.json"
    if not path.exists():
        return
    ids = json.loads(path.read_text())
    for item in ids.get("items") or []:
        case = cases.get(item.get("case_id"))
        if case is None:
            continue
        case["inputs_summary"]["appeal_type"] = item.get("appeal_type")
        case["inputs_summary"]["row_index"] = item.get("row_index")
        case["inputs_summary"]["text_sha256"] = item.get("text_sha256")


@lru_cache(maxsize=1)
def _combo_module():
    import importlib.util

    script = Path(__file__).resolve().parents[3] / "scripts" / "g_persius_combo.py"
    spec = importlib.util.spec_from_file_location("g_persius_combo_rules", script)
    if spec is None or spec.loader is None:
        raise ImportError(script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _add_persius_combo(cases: dict[str, dict]) -> None:
    """Apply the frozen rule to recorded test rows. No model call."""
    rule_path = RUNS / "g_persius_rule.json"
    test_path = RUNS / "c_persius_test.jsonl"
    if not rule_path.exists() or not test_path.exists():
        return
    module = _combo_module()
    rule = json.loads(rule_path.read_text())
    function = module._rule_function(rule["rule_id"])
    for item in module._pairs(module._load_jsonl(test_path)):
        choice, used = function(item["drex"], item["frontier"])
        gold = item["gold"]
        correct = choice == gold
        case = cases.setdefault(item["case_id"], _blank(item["case_id"]))
        case["gold"] = gold
        case["gold_summary"] = gold
        case["inputs_summary"] = {"rule_id": rule.get("rule_id"), "split": "test"}
        for lane_name in ("drex", "frontier"):
            row = item[lane_name]
            case["lanes"][lane_name] = _lane_box(
                correct=bool(row.get("correct")) if row.get("error") is None else False,
                choice=row.get("choice"),
                confidence=row.get("confidence"),
                k=1 if row.get("correct") else 0,
                n=1,
                wrong=[] if row.get("correct") else ["choice"],
                answers={
                    "choice": row.get("choice"),
                    "confidence": row.get("confidence"),
                    "p_yes": row.get("p_yes"),
                    "correct": row.get("correct"),
                },
                latency_ms=row.get("latency_ms"),
                cost_usd=row.get("cost_usd"),
                model=row.get("model"),
                error=row.get("error"),
            )
        case["lanes"]["combo"] = _lane_box(
            correct=correct,
            choice=choice,
            confidence=None,
            k=1 if correct else 0,
            n=1,
            wrong=[] if correct else ["choice"],
            answers={
                "choice": choice,
                "used_lanes": list(used),
                "rule_id": rule.get("rule_id"),
                "drex_p_yes": item["drex"].get("p_yes"),
                "frontier_p_yes": item["frontier"].get("p_yes"),
            },
            latency_ms=module._policy_latency_ms(item["drex"], item["frontier"], used),
            cost_usd=module._policy_cost(item["drex"], item["frontier"], used),
            model=rule.get("rule_id"),
        )


def _build(bench: str, *, with_answers: bool) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    if bench == "synthetic_v1":
        _add_synthetic(cases)
    elif bench == "pa_criteria_gym":
        _add_pa(cases, RUNS / "pa_gpt_development.jsonl", "gpt")
        _add_pa(cases, RUNS / "pa_drex_development.jsonl", "drex")
        _add_pa(cases, RUNS / "pa_drexfin_development.jsonl", "drexfin")
        _add_pa(cases, RUNS / "pa_hybrid_development.jsonl", "hybrid")
        _add_pa(cases, RUNS / "pa_checked_development.jsonl", "checked")
    elif bench == "persius":
        _add_binary(cases, RUNS / "c_persius_test.jsonl")
        _join_persius_ids(cases)
    elif bench == "persius_combo":
        _add_persius_combo(cases)
        _join_persius_ids(cases)
    elif bench == "hard":
        specs = [
            ("gpt", RUNS / "hard_test_gpt_subset.jsonl", True),
            ("gpt", RUNS / "hard_test_gpt.jsonl", True),
            ("drex-v1", RUNS / "hard_test_drex-v1.answers.jsonl.gz", True),
            ("drex-finance", RUNS / "hard_test_drex-finance.answers.jsonl.gz", True),
            ("hybrid", RUNS / "hard_test_family_hybrid.jsonl", False),
            ("billproof-system", RUNS / "hard_test_billproof-system.jsonl", False),
            ("anthropic__claude-haiku-5.5", RUNS / "ladder_hard_anthropic__claude-haiku-5.5.jsonl", False),
            ("deepseek__deepseek-v4.1-flash", RUNS / "ladder_hard_deepseek__deepseek-v4.1-flash.jsonl", False),
            ("google__gemini-3.5-flash-lite", RUNS / "ladder_hard_google__gemini-3.5-flash-lite.jsonl", False),
            ("openai__gpt-5.4-mini", RUNS / "ladder_hard_openai__gpt-5.4-mini.jsonl", False),
        ]
        # GPT is added twice under the same lane; the second file fills ids the subset missed.
        # _add_hard_file overwrites a lane, so merge GPT in one pass below.
        for lane, path, restrict in specs:
            if lane == "gpt":
                continue
            _add_hard_file(cases, path, lane, restrict=restrict, with_answers=with_answers)
        _add_hard_file(cases, RUNS / "hard_test_gpt_subset.jsonl", "gpt", restrict=True, with_answers=with_answers)
        existing = {case_id for case_id, case in cases.items() if "gpt" in case["lanes"]}
        extra: dict[str, dict] = {}
        _add_hard_file(extra, RUNS / "hard_test_gpt.jsonl", "gpt", restrict=True, with_answers=with_answers)
        for case_id, case in extra.items():
            if case_id not in existing:
                cases.setdefault(case_id, _blank(case_id))["lanes"]["gpt"] = case["lanes"]["gpt"]
    elif bench == "cc_queue":
        _add_cc_queue(cases)
    elif bench == "cc_rule_change":
        _add_rule(cases)
    elif bench == "cc_what_decides":
        _add_decides(cases)
    elif bench == "full_chain":
        _add_chain(cases)
    elif bench == "ladder":
        for lane, name in (
            ("openai/gpt-5.4-mini", "ladder_hard_openai__gpt-5.4-mini.jsonl"),
            ("anthropic/claude-haiku-5.5", "ladder_hard_anthropic__claude-haiku-5.5.jsonl"),
            ("google/gemini-3.5-flash-lite", "ladder_hard_google__gemini-3.5-flash-lite.jsonl"),
            ("deepseek/deepseek-v4.1-flash", "ladder_hard_deepseek__deepseek-v4.1-flash.jsonl"),
        ):
            _add_hard_file(cases, RUNS / name, lane, restrict=False, with_answers=with_answers)
    else:
        raise KeyError(bench)
    return cases


@lru_cache(maxsize=16)
def _cached(bench: str) -> dict[str, dict]:
    cases = _build(bench, with_answers=False)
    # Drop answer payloads from the list cache. Detail reloads one case.
    for case in cases.values():
        for lane in case["lanes"].values():
            lane["answers"] = None
            if isinstance(case.get("gold"), dict) and len(json.dumps(case["gold"])) > 4000:
                case["gold_summary"] = case.get("gold_summary") or "recorded gold"
                case["gold"] = None
    return cases


def _public_case(case: dict, *, detail: bool) -> dict:
    lanes = {}
    for name, lane in case["lanes"].items():
        shown = {
            "correct": lane.get("correct"),
            "choice": lane.get("choice"),
            "confidence": lane.get("confidence"),
            "k": lane.get("k"),
            "n": lane.get("n"),
            "wrong": lane.get("wrong") or [],
        }
        if detail:
            shown["answers"] = lane.get("answers")
            shown["latency_ms"] = lane.get("latency_ms")
            shown["cost_usd"] = lane.get("cost_usd")
            shown["model"] = lane.get("model")
            shown["error"] = lane.get("error")
        lanes[name] = shown
    body = {
        "case_id": case["case_id"],
        "gold_summary": case.get("gold_summary"),
        "lanes": lanes,
    }
    if detail:
        body["inputs_summary"] = case.get("inputs_summary") or {}
        body["gold"] = case.get("gold")
    return body


def list_cases(
    bench: str,
    *,
    lane: str | None = None,
    only_wrong: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    cases = _cached(bench)
    rows = []
    for case in cases.values():
        if lane and lane not in case["lanes"]:
            continue
        if only_wrong:
            if lane:
                box = case["lanes"].get(lane)
                if box is None or box.get("correct"):
                    continue
            elif not any(box.get("correct") is False for box in case["lanes"].values()):
                continue
        rows.append(case)
    rows.sort(key=lambda case: case["case_id"])
    offset = max(0, offset)
    limit = min(200, max(1, limit))
    page = rows[offset:offset + limit]
    return {
        "bench": bench,
        "n": len(rows),
        "offset": offset,
        "limit": limit,
        "lane": lane or None,
        "only_wrong": only_wrong,
        "cases": [_public_case(case, detail=False) for case in page],
    }


def get_case(bench: str, case_id: str) -> dict | None:
    built = _build(bench, with_answers=True)
    case = built.get(case_id)
    if case is None:
        return None
    body = _public_case(case, detail=True)
    body["bench"] = bench
    return body

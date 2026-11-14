#!/usr/bin/env python3
"""Drex over every criteria-gym policy_checklist row. Detached and resumable.

Train + validation + development, initial requests and follow-ups.
``drex-max-v1.1`` answers each criterion. Code reaches the decision.
Successful answers already on the 2,211-case queue are reused and not billed
again. New spend is capped at $1.00 in its own ledger (the earlier $0.90
ledger is left alone).

  cd billproof && PYTHONUNBUFFERED=1 PYTHONPATH=src \\
    nohup .venv/bin/python scripts/cc_scale.py >> out/logs/cc_scale.log 2>&1 &

Fact cache: out/command_center/facts.jsonl (gitignored).
Compact replay: data/command_center/full_replay.jsonl.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.score import score_prediction
from billproof.command_center.batch import (
    BudgetStop,
    WorkItem,
    assert_live,
    baseline_item,
    budget_add,
    budget_ok,
    consume_pack,
    make_adapter,
    run_items,
)
from billproof.command_center.catalog import index_policies, load_pa_cases, write_catalog
from billproof.command_center.common import (
    DREX_MODEL,
    FACTS_PATH,
    FULL_QUEUE_PATH,
    QUEUE_PATH,
    add_full_spend,
    append_jsonl,
    ensure_dirs,
    error_counts,
    full_spend_ok,
    latest_success,
    load_jsonl,
    now_iso,
    scrub,
)
from billproof.command_center.rows import colour_of, derive, prediction, queue_row
from billproof.command_center.store import (
    answers_complete,
    attempt_counts,
    done_case_ids,
    get_store,
    publish,
    strong_ids,
)
from billproof.config import PKG_ROOT

PID_PATH = PKG_ROOT / "out" / "cc_scale.pid"


def _fatal(exc: BaseException) -> bool:
    text = scrub(str(exc)).lower()
    return type(exc).__name__ == "ModelMismatch" or "401" in text or "403" in text


def _install_budget() -> None:
    budget_ok.set(lambda estimate: full_spend_ok(estimate, lane="scale"))
    budget_add.set(lambda amount: add_full_spend(amount, lane="scale"))


def _gold_ok(item: WorkItem, res) -> bool | None:
    if res.error:
        return None
    derived = derive(item.case, res.choices)
    score = score_prediction(item.case, prediction(item.case, derived), check_evidence=False)
    return bool(score["facts_all"] and score["decision"])


def _write_live(item: WorkItem, res, *, simulated: bool) -> None:
    case = item.case
    if res.error or not res.choices:
        append_jsonl(FACTS_PATH, {
            "case_id": case.case_id,
            "split": case.split,
            "stage": case.stage,
            "error": res.error or "no answers",
            "simulated": simulated or res.simulated,
            "reused": False,
            "model": res.model,
            "cost_usd": res.cost_usd,
            "ts": now_iso(),
        })
        return
    derived = derive(case, res.choices)
    pred = prediction(case, derived)
    score = score_prediction(case, pred, check_evidence=False)
    label, colour = colour_of(derived["decision"])
    append_jsonl(FACTS_PATH, {
        "case_id": case.case_id,
        "split": case.split,
        "stage": case.stage,
        "choices": res.choices,
        "confidence": res.confidence,
        "decision": derived["decision"],
        "label": label,
        "colour": colour,
        "correct": bool(score["decision"]),
        "criteria_all_correct": bool(score["criteria_all"]),
        "facts_all_correct": bool(score["facts_all"]),
        "gold_decision": (case.target or {}).get("decision"),
        "cost_usd": res.cost_usd,
        "latency_ms": res.latency_ms,
        "batch_latency_ms": res.batch_latency_ms,
        "batch_id": res.batch_id,
        "batch_questions": res.batch_questions,
        "model": res.model,
        "simulated": simulated or res.simulated,
        "reused": False,
        "calls": res.calls,
        "resampled": bool(res.resampled),
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "error": None,
        "ts": now_iso(),
    })
    row = queue_row(
        case, derived, choices=res.choices, confidence=res.confidence,
        lane="drex", model=res.model, simulated=simulated or res.simulated,
        latency_ms=res.latency_ms, batch_latency_ms=res.batch_latency_ms,
        batch_id=res.batch_id, batch_questions=res.batch_questions,
        cost_usd=res.cost_usd, batch_cost_usd=res.batch_cost_usd,
        input_tokens=res.input_tokens, output_tokens=res.output_tokens,
        calls=res.calls,
    )
    row["ts"] = now_iso()
    row["resampled"] = bool(res.resampled)
    row["reused"] = False
    append_jsonl(FULL_QUEUE_PATH, row)


def seed_from_queue() -> int:
    """Copy kept drex-max-v1.1 answers off the earlier queue. No new spend."""
    catalog = get_store(force=True)
    by_case = {card.case_id: card for card in catalog.cards}
    policies = index_policies(catalog.policies)
    kept = strong_ids(load_jsonl(QUEUE_PATH))
    latest = latest_success(QUEUE_PATH)
    have = done_case_ids()
    written = 0
    for case_id in sorted(kept):
        if case_id in have or case_id not in by_case:
            continue
        row = latest.get(case_id)
        if not row or row.get("simulated") or row.get("model") != DREX_MODEL:
            continue
        choices: dict[str, str] = {}
        confidence: dict[str, float] = {}
        for key, item in (row.get("qa") or {}).items():
            if isinstance(item, dict) and isinstance(item.get("choice"), str):
                choices[key] = item["choice"]
                if item.get("confidence") is not None:
                    confidence[key] = item["confidence"]
        card = by_case[case_id]
        policy = policies.get(card.list_id)
        if policy is None or not answers_complete(policy, choices):
            continue
        append_jsonl(FACTS_PATH, {
            "case_id": case_id,
            "split": row.get("split") or card.split,
            "stage": card.stage,
            "choices": choices,
            "confidence": confidence,
            "decision": row.get("decision"),
            "label": row.get("label"),
            "colour": row.get("colour"),
            "correct": bool(row.get("correct")),
            "criteria_all_correct": bool(row.get("criteria_all_correct")),
            "facts_all_correct": bool(row.get("facts_all_correct")),
            "gold_decision": row.get("gold_decision") or card.gold,
            "cost_usd": row.get("cost_usd") or 0.0,
            "latency_ms": row.get("latency_ms") or 0.0,
            "batch_latency_ms": row.get("batch_latency_ms") or row.get("latency_ms") or 0.0,
            "batch_id": row.get("batch_id") or "reused",
            "model": row.get("model") or DREX_MODEL,
            "simulated": False,
            "reused": True,
            "calls": row.get("calls") or 1,
            "resampled": bool(row.get("resampled")),
            "error": None,
            "ts": now_iso(),
        })
        written += 1
    return written


def _claim_pid() -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PID_PATH.is_file():
        try:
            pid = int(PID_PATH.read_text().strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            pass
        else:
            raise SystemExit(f"[cc_scale] already running pid {pid}")
    PID_PATH.write_text(str(os.getpid()))


def _drop_pid() -> None:
    try:
        if PID_PATH.is_file() and PID_PATH.read_text().strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


def run_drex(cases, *, adapter, simulated: bool, max_questions: int) -> str:
    done = done_case_ids()
    fails = error_counts(FACTS_PATH)
    attempts = attempt_counts()
    pending = [
        case for case in cases
        if case.case_id not in done
        and fails.get(case.case_id, 0) < 2
        and attempts.get(case.case_id, 0) < 2
    ]
    print(
        f"[cc_scale] split pending={len(pending)} done_file={len(done)} sim={simulated}",
        flush=True,
    )
    status = "running"
    buf: list[WorkItem] = []
    buf_q = 0
    packs = 0

    def flush() -> None:
        nonlocal buf, buf_q, packs, status
        if not buf:
            return
        pairs, stop = consume_pack(
            buf, lambda: run_items(
                buf, adapter=adapter, simulated=simulated,
                max_questions=max_questions, gold_flags=_gold_ok,
            ),
        )
        for item, res in pairs:
            _write_live(item, res, simulated=simulated)
            if res.error:
                mark = "ERR"
            else:
                mark = derive(item.case, res.choices)["decision"]
            print(f"[cc_scale] {item.case.case_id} {mark} ${res.cost_usd:.5f}", flush=True)
        buf, buf_q = [], 0
        packs += 1
        if pairs:
            publish(
                "running",
                note="Drex run in progress. Numbers are the cached prefix, not a projection.",
                with_presets=(packs % 25 == 0),
                with_doc=False,
            )
        if stop:
            status = "partial"
            raise stop

    try:
        for case in pending:
            item = baseline_item(case, include_scope=True)
            qn = len(item.questions)
            if buf and buf_q + qn > max_questions:
                flush()
            buf.append(item)
            buf_q += qn
        flush()
    except BudgetStop as exc:
        print(f"[cc_scale] BUDGET {scrub(str(exc))}", flush=True)
        return "partial"
    except Exception as exc:
        print(f"[cc_scale] FATAL {type(exc).__name__}: {scrub(str(exc))}", flush=True)
        if _fatal(exc):
            return "partial"
        raise
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Drex over the full criteria-gym queue")
    parser.add_argument("--allow-sim", action="store_true")
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--max-questions", type=int, default=200)
    parser.add_argument("--ignore-mismatch", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="debug cap on new cases; 0 means all")
    args = parser.parse_args()
    ensure_dirs()
    _claim_pid()
    try:
        counts = write_catalog()
        print(f"[cc_scale] catalog {counts}", flush=True)
        if args.catalog_only:
            return 0
        seeded = seed_from_queue()
        print(f"[cc_scale] seeded {seeded} reused answers", flush=True)
        store = get_store(force=True)
        print(
            f"[cc_scale] cached {store.n_cached}/{store.counts.get('n_cases')} "
            f"rederive_mismatch {store.n_mismatch}",
            flush=True,
        )
        if store.n_mismatch and not args.ignore_mismatch:
            print("[cc_scale] rederive mismatch; not spending. Pass --ignore-mismatch to continue.",
                  flush=True)
            publish("partial", note="Stopped: cached decisions do not rederive.", with_doc=True)
            return 2
        simulated = assert_live(args.allow_sim)
        _install_budget()
        adapter, simulated_flag = make_adapter()
        simulated = simulated or simulated_flag
        stopped = "running"
        for split in ("validation", "development", "train"):
            cases = load_pa_cases(split)
            if args.limit:
                cases = cases[: args.limit]
            print(f"[cc_scale] loaded {split} n={len(cases)}", flush=True)
            stopped = run_drex(cases, adapter=adapter, simulated=simulated,
                               max_questions=args.max_questions)
            del cases
            if stopped == "partial":
                break
        store = get_store(force=True)
        planned = int(store.counts.get("n_cases") or 0)
        final = "complete" if store.n_cached >= planned and planned else "partial"
        if stopped == "partial":
            final = "partial"
        note = (
            "Every cached row is a real drex-max-v1.1 answer (or a reused answer "
            "from the earlier queue of the same model). Accuracy is against the "
            "dataset target. Edits in the presets are hypothetical."
        )
        if final != "complete":
            note = "Partial cache. " + note
        summary = publish(final, note=note, with_presets=True, with_doc=True)
        drex = summary["drex"]
        print(
            f"[cc_scale] DONE status={final} n={drex['n']} acc={drex['accuracy']} "
            f"spend={summary['spend']['nace_usd']}",
            flush=True,
        )
        return 0 if final == "complete" else 0
    finally:
        _drop_pid()


if __name__ == "__main__":
    raise SystemExit(main())

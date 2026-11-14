#!/usr/bin/env python3
"""Live prior-auth queue. Detached, logged, resumable.

Drex (drex-max-v1.1) answers every criterion for every validation and
development initial_packet. Code reaches the decision. GPT (gpt-6.1-sol)
runs only on a frozen hash sample of 40 case ids (seed 20261114), which is
written before any GPT call and is not chosen by outcome.

  cd billproof && PYTHONUNBUFFERED=1 PYTHONPATH=src \\
    nohup .venv/bin/python scripts/cc_queue.py >> out/logs/cc_queue.log 2>&1 &

Full JSONL: out/command_center/queue.jsonl (gitignored).
Committed replay: data/command_center/queue_replay.jsonl.
GPT sample: data/command_center/queue_gpt.jsonl.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import load_cases
from billproof.command_center.batch import (
    BudgetStop,
    WorkItem,
    assert_live,
    baseline_item,
    consume_pack,
    make_adapter,
    run_items,
)
from billproof.command_center.common import (
    GPT_IDS_PATH,
    GPT_MODEL,
    GPT_N,
    GPT_PATH,
    GPT_SEED,
    QUEUE_PATH,
    add_spend,
    append_jsonl,
    can_spend,
    ensure_dirs,
    error_counts,
    load_jsonl,
    now_iso,
    read_json,
    scrub,
    success_ids,
    write_json,
)
from billproof.command_center.report import write_queue_outputs
from billproof.bench.pa.score import score_prediction
from billproof.command_center.rows import (
    derive, invalid_gpt_answers, prediction, queue_row, seeded_sample_ids,
)
from billproof.config import settings


def _fatal(exc: BaseException) -> bool:
    name = type(exc).__name__
    text = scrub(str(exc)).lower()
    return name == "ModelMismatch" or "401" in text or "403" in text


def _cases():
    return load_cases("validation") + load_cases("development")


def _freeze_gpt(cases) -> list[str]:
    existing = read_json(GPT_IDS_PATH, None)
    if isinstance(existing, dict) and existing.get("case_ids"):
        return list(existing["case_ids"])
    ids = seeded_sample_ids([c.case_id for c in cases], GPT_N, GPT_SEED)
    write_json(GPT_IDS_PATH, {
        "seed": GPT_SEED,
        "n": GPT_N,
        "method": "md5(seed:case_id) order, first n; gold decision not used",
        "case_ids": ids,
        "frozen_at": now_iso(),
    })
    return ids


def _gold_ok(item: WorkItem, res) -> bool | None:
    """Fact-level, not just the final decision. A wide pack can keep the
    decision while swapping individual criteria."""
    if res.error:
        return None
    derived = derive(item.case, res.choices)
    score = score_prediction(item.case, prediction(item.case, derived), check_evidence=False)
    return bool(score["facts_all"] and score["decision"])


def _write_drex(item: WorkItem, res, *, simulated: bool) -> None:
    if res.error or not res.choices:
        append_jsonl(QUEUE_PATH, {
            "case_id": item.case.case_id, "split": item.case.split,
            "lane": "drex", "error": res.error or "no answers",
            "simulated": simulated, "ts": now_iso(), "cost_usd": res.cost_usd,
        })
        return
    derived = derive(item.case, res.choices)
    row = queue_row(
        item.case, derived, choices=res.choices, confidence=res.confidence,
        lane="drex", model=res.model, simulated=simulated or res.simulated,
        latency_ms=res.latency_ms, batch_latency_ms=res.batch_latency_ms,
        batch_id=res.batch_id, batch_questions=res.batch_questions,
        cost_usd=res.cost_usd, batch_cost_usd=res.batch_cost_usd,
        input_tokens=res.input_tokens, output_tokens=res.output_tokens,
        calls=res.calls,
    )
    row["ts"] = now_iso()
    row["resampled"] = bool(res.resampled)
    append_jsonl(QUEUE_PATH, row)


def _done_ids() -> set[str]:
    """Keep a batch when at least 95% of its cases got every fact right.

    Cases in a weaker batch are retried unless that case itself was fully
    right. Misses inside a strong batch stay, so a real error is not hidden.
    """
    from collections import defaultdict
    grouped: dict[str, list] = defaultdict(list)
    rows = [r for r in load_jsonl(QUEUE_PATH) if r.get("case_id") and not r.get("error")]
    for row in rows:
        grouped[str(row.get("batch_id"))].append(row)
    strong = set()
    for bid, group in grouped.items():
        rate = sum(1 for r in group if r.get("facts_all_correct")) / len(group)
        if rate >= 0.95:
            strong.add(bid)
    done: set[str] = set()
    for row in rows:
        if str(row.get("batch_id")) in strong or (row.get("facts_all_correct") and row.get("correct")):
            done.add(row["case_id"])
    return done


def run_drex(cases, *, adapter, simulated: bool, max_questions: int,
             n_planned: int, gpt_planned: int) -> str:
    done = _done_ids()
    fails = error_counts(QUEUE_PATH)
    pending = [c for c in cases if c.case_id not in done and fails.get(c.case_id, 0) < 2]
    print(f"[cc_queue] drex planned={len(cases)} done={len(done)} pending={len(pending)} "
          f"sim={simulated}", flush=True)
    status = "complete"
    buf: list[WorkItem] = []
    buf_q = 0

    def flush() -> None:
        nonlocal buf, buf_q
        if not buf:
            return
        pairs, stop = consume_pack(
            buf, lambda: run_items(buf, adapter=adapter, simulated=simulated,
                                   max_questions=max_questions, gold_flags=_gold_ok),
        )
        for item, res in pairs:
            _write_drex(item, res, simulated=simulated)
            mark = "ERR" if res.error else derive(item.case, res.choices)["decision"]
            print(f"[cc_queue] {item.case.case_id} {mark} ${res.cost_usd:.5f}", flush=True)
        buf, buf_q = [], 0
        if pairs:
            write_queue_outputs(n_planned=n_planned, gpt_planned=gpt_planned,
                                status="running")
        if stop:
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
        status = "partial"
        print(f"[cc_queue] BUDGET {scrub(str(exc))}", flush=True)
    except Exception as exc:
        status = "partial"
        print(f"[cc_queue] FATAL {type(exc).__name__}: {scrub(str(exc))}", flush=True)
        if not _fatal(exc):
            raise
    return status


def run_gpt(cases, gpt_ids: list[str], *, n_limit: int, n_planned: int) -> str:
    cfg = settings()
    if cfg.frontier_model not in (GPT_MODEL, "gpt-6.1-sol"):
        print(f"[cc_queue] skip GPT; frontier model is {cfg.frontier_model}", flush=True)
        return "partial"
    if not cfg.use_real("frontier"):
        print("[cc_queue] skip GPT; frontier is not live", flush=True)
        return "partial"
    by_id = {c.case_id: c for c in cases}
    ordered = [by_id[i] for i in gpt_ids if i in by_id][:n_limit]
    done = success_ids(GPT_PATH)
    fails = error_counts(GPT_PATH)
    pending = [c for c in ordered if c.case_id not in done and fails.get(c.case_id, 0) < 2]
    print(f"[cc_queue] gpt sample={len(ordered)} pending={len(pending)}", flush=True)
    from billproof.bench.pa.columns import gpt_questions
    from billproof.bench.pa.questions import build_questions

    status = "complete"
    for case in pending:
        if not can_spend("router", 0.02):
            print("[cc_queue] router cap; stop GPT", flush=True)
            status = "partial"
            break
        questions = build_questions(case)
        try:
            res = gpt_questions(case, questions)
        except Exception as exc:
            append_jsonl(GPT_PATH, {
                "case_id": case.case_id, "lane": "gpt", "error": scrub(str(exc)),
                "simulated": False, "ts": now_iso(),
            })
            print(f"[cc_queue] gpt {case.case_id} ERR {scrub(str(exc))}", flush=True)
            continue
        cost = float(res.get("cost_usd") or 0.0)
        add_spend("router", cost)
        if res.get("error"):
            append_jsonl(GPT_PATH, {
                "case_id": case.case_id, "lane": "gpt", "error": scrub(str(res["error"])),
                "simulated": False, "cost_usd": cost, "ts": now_iso(),
                "model": res.get("model"),
            })
            continue
        answers = res.get("answers") or {}
        bad = invalid_gpt_answers(questions, answers)
        if bad:
            append_jsonl(GPT_PATH, {
                "case_id": case.case_id, "lane": "gpt",
                "error": "invalid choice: " + ",".join(bad[:6]),
                "simulated": False, "cost_usd": cost, "ts": now_iso(),
                "model": res.get("model"),
            })
            print(f"[cc_queue] gpt {case.case_id} ERR invalid choice", flush=True)
            continue
        choices = {q.name: answers[q.name]["choice"] for q in questions}
        conf = {q.name: float((answers.get(q.name) or {}).get("confidence") or 0.0)
                for q in questions}
        derived = derive(case, choices)  # type: ignore[arg-type]
        row = queue_row(
            case, derived, choices=choices, confidence=conf,  # type: ignore[arg-type]
            lane="gpt", model=res.get("model") or cfg.frontier_model, simulated=False,
            latency_ms=float(res.get("latency_ms") or 0.0),
            batch_latency_ms=float(res.get("latency_ms") or 0.0),
            batch_id=f"gpt-{case.case_id}", batch_questions=len(questions),
            cost_usd=cost, batch_cost_usd=cost,
            input_tokens=int(res.get("input_tokens") or 0),
            output_tokens=int(res.get("output_tokens") or 0),
            calls=int(res.get("calls") or 1), projected=False,
        )
        row["ts"] = now_iso()
        row["projected_full_queue"] = False
        append_jsonl(GPT_PATH, row)
        print(f"[cc_queue] gpt {case.case_id} {row['decision']} correct={row['correct']} "
              f"${cost:.4f}", flush=True)
        write_queue_outputs(n_planned=n_planned, gpt_planned=len(gpt_ids), status="drex_done")
    return status


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None, help="first N cases (Drex only)")
    p.add_argument("--gpt-n", type=int, default=GPT_N, help="how many of the frozen sample to run")
    p.add_argument("--max-questions", type=int, default=200,
                   help="questions per Drex call. 500-question packs lost fact accuracy; 200 held")
    p.add_argument("--allow-sim", action="store_true")
    args = p.parse_args()
    ensure_dirs()
    simulated = assert_live(args.allow_sim)
    cases = _cases()
    gpt_ids = _freeze_gpt(cases)
    run_cases = cases[: args.limit] if args.limit else cases
    print(f"[cc_queue] start n={len(run_cases)} of {len(cases)} gpt_ids={len(gpt_ids)} "
          f"{now_iso()}", flush=True)
    t0 = time.time()
    adapter, adapter_sim = make_adapter()
    simulated = simulated or adapter_sim
    status = run_drex(run_cases, adapter=adapter, simulated=simulated,
                      max_questions=args.max_questions, n_planned=len(cases),
                      gpt_planned=len(gpt_ids))
    if status == "complete":
        write_queue_outputs(n_planned=len(cases), gpt_planned=len(gpt_ids),
                            status="drex_done")
    gpt_status = "skipped"
    if not args.limit or args.gpt_n:
        gpt_status = run_gpt(cases, gpt_ids, n_limit=args.gpt_n, n_planned=len(cases))
    if status == "complete" and gpt_status == "complete":
        final = "complete"
    else:
        final = "partial"
    # A limited smoke is a partial pass over the planned queue.
    if args.limit and status == "complete":
        final = "partial"
    summary = write_queue_outputs(
        n_planned=len(cases), gpt_planned=len(gpt_ids), status=final,
        note="" if not args.limit else f"drex limit {args.limit}",
    )
    drex = summary["drex"]
    print(f"[cc_queue] DONE status={final} drex_n={drex['n']} acc={drex['accuracy']} "
          f"cpm={drex['cases_per_minute']} ${drex['total_cost_usd']:.4f} "
          f"wall_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()

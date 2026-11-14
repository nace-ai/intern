#!/usr/bin/env python3
"""What decides this request, across the queue.

For each initial_packet case, re-ask Drex with each record removed. A record
is deciding when the code decision flips. Agreement uses the same gold rule
as scripts/pa_decide.py (construction ids). Stuck-clause rates and the
"send these documents" counterfactual are computed from the queue rows.

  cd billproof && PYTHONUNBUFFERED=1 PYTHONPATH=src \\
    nohup .venv/bin/python scripts/cc_what_decides.py >> out/logs/cc_what_decides.log 2>&1 &
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import PaCase, load_cases
from billproof.command_center.batch import (
    BudgetStop,
    WorkItem,
    ablation_item,
    assert_live,
    baseline_item,
    consume_pack,
    make_adapter,
    run_items,
)
from billproof.command_center.common import (
    DECIDE_PATH,
    QUEUE_PATH,
    QUEUE_SUMMARY_PATH,
    append_jsonl,
    ensure_dirs,
    error_counts,
    latest_success,
    now_iso,
    read_json,
    scrub,
    success_ids,
)
from billproof.command_center.report import write_decide_summary
from billproof.command_center.rows import agrees, colour_of, derive, probe_failure


def _choices(qa: dict) -> tuple[dict[str, str], dict[str, float]] | None:
    choices, conf = {}, {}
    for name, item in (qa or {}).items():
        if not isinstance(item, dict) or not item.get("choice"):
            return None
        choices[name] = item["choice"]
        conf[name] = float(item.get("confidence") or 0.0)
    return choices, conf


def _baseline(case: PaCase, queue_row: dict | None,
              by_packet: dict[str, object]):
    if queue_row and _choices(queue_row.get("qa") or {}):
        choices, conf = _choices(queue_row["qa"])  # type: ignore[misc]
        derived = derive(case, choices, scope=queue_row.get("scope"))
        return derived, 0.0, 0.0, 0, False
    item_id = case.case_id
    res = by_packet.get(item_id)
    if res is None or getattr(res, "error", None) or not getattr(res, "choices", None):
        return None, 0.0, 0.0, 0, False
    derived = derive(case, res.choices)
    return derived, float(res.cost_usd), float(res.latency_ms), int(res.calls), bool(res.simulated)


def _case_row(case: PaCase, items: list[WorkItem], by_packet: dict, queue_row: dict | None,
              *, simulated: bool) -> dict:
    base, cost, latency, calls, sim = _baseline(case, queue_row, by_packet)
    if base is None:
        return {"case_id": case.case_id, "split": case.split, "error": "no baseline",
                "simulated": simulated, "ts": now_iso()}
    label, colour = colour_of(base["decision"])
    removals = []
    flips: list[str] = []
    for rec in case.records:
        rid = rec.get("id")
        res = by_packet.get(f"{case.case_id}~{rid}")
        if res is None or res.error or not res.choices:
            removals.append({"record": rid, "error": getattr(res, "error", None) or "missing"})
            continue
        ab = derive(case, res.choices, scope=base["scope"])
        changed = [f for f in case.requested_facts if ab["facts"].get(f) != base["facts"].get(f)]
        flipped = ab["decision"] != base["decision"]
        if flipped:
            flips.append(rid)
        removals.append({
            "record": rid,
            "text": (rec.get("text") or "")[:180],
            "decision": ab["decision"],
            "label": colour_of(ab["decision"])[0],
            "flipped": flipped,
            "changed_facts": changed,
        })
        cost += float(res.cost_usd or 0)
        latency += float(res.latency_ms or 0)
        calls += int(res.calls or 0)
        sim = sim or bool(res.simulated)
    n_expected = sum(1 for rec in case.records if rec.get("id"))
    reason = probe_failure(removals, n_expected)
    if reason:
        return {
            "case_id": case.case_id,
            "split": case.split,
            "list_id": case.list_id,
            "baseline_decision": base["decision"],
            "baseline_label": label,
            "removals": removals,
            "deciding_records": [],
            "predicted": None,
            "agree": None,
            "cost_usd": round(cost, 8),
            "latency_ms": round(latency, 3),
            "calls": calls,
            "simulated": simulated or sim,
            "error": reason,
            "ts": now_iso(),
        }
    predicted = flips[0] if flips else "none"
    ok, gold = agrees(predicted, case)
    return {
        "case_id": case.case_id,
        "split": case.split,
        "list_id": case.list_id,
        "publisher": case.publisher,
        "baseline_decision": base["decision"],
        "baseline_label": label,
        "baseline_colour": colour,
        "baseline_scope": base["scope"],
        "predicted": predicted,
        "predicted_fact": gold.get("predicted_fact"),
        "deciding_records": flips,
        "removals": removals,
        "agree": ok,
        "gold_kind": gold.get("kind"),
        "gold_facts": gold.get("facts"),
        "gold_records": gold.get("records"),
        "cost_usd": round(cost, 8),
        "latency_ms": round(latency, 3),
        "calls": calls,
        "simulated": simulated or sim,
        "error": None,
        "ts": now_iso(),
    }


def _wait_for_queue(n_target: int, timeout_s: float = 10800) -> dict:
    """Prefer queue baselines so we do not pay Drex twice for the same case.

    `drex_done` means every planned Drex case has been written and weak packs
    have been retried. GPT may still be running; it does not use the Drex lock.
    A summary that stays quiet for 10 minutes is treated as stopped.
    """
    started = time.time()
    while time.time() - started < timeout_s:
        got = latest_success(QUEUE_PATH)
        summary = read_json(QUEUE_SUMMARY_PATH, {})
        status = summary.get("status")
        if status in ("complete", "drex_done") or (len(got) >= n_target and status != "running"):
            print(f"[cc_decide] queue ready n={len(got)} status={status}", flush=True)
            return got
        quiet = time.time() - QUEUE_PATH.stat().st_mtime if QUEUE_PATH.is_file() else 999
        if status in ("partial", "running") and quiet > 600 and time.time() - started > 600:
            print(f"[cc_decide] queue quiet n={len(got)} status={status}; probing those", flush=True)
            return got
        print(f"[cc_decide] waiting for queue n={len(got)}/{n_target} status={status}", flush=True)
        time.sleep(15)
    print("[cc_decide] queue wait timed out; probing what exists", flush=True)
    return latest_success(QUEUE_PATH)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--max-questions", type=int, default=200)
    p.add_argument("--allow-sim", action="store_true")
    args = p.parse_args()
    ensure_dirs()
    t0 = time.time()
    live_sim = assert_live(args.allow_sim)
    cases = load_cases("validation") + load_cases("development")
    if args.limit:
        cases = cases[: args.limit]
    done = success_ids(DECIDE_PATH)
    fails = error_counts(DECIDE_PATH)
    print(f"[cc_decide] n={len(cases)} already={len(done)}", flush=True)
    queue = _wait_for_queue(len(cases) if not args.limit else args.limit)
    pending = [c for c in cases if c.case_id not in done and fails.get(c.case_id, 0) < 2]
    # Cases the queue never decided still get a baseline call below.
    print(f"[cc_decide] pending={len(pending)} with_queue_baseline="
          f"{sum(1 for c in pending if c.case_id in queue)}", flush=True)
    adapter, adapter_sim = make_adapter()
    simulated = live_sim or adapter_sim
    status = "complete"

    groups: list[tuple[PaCase, list[WorkItem]]] = []
    qn = 0

    def flush() -> None:
        nonlocal groups, qn
        if not groups:
            return
        flat = [it for _, items in groups for it in items]
        pairs, stop = consume_pack(
            flat, lambda: run_items(flat, adapter=adapter, simulated=simulated,
                                    max_questions=args.max_questions, gold_flags=None),
        )
        by = {item.packet_id: res for item, res in pairs}
        fresh = latest_success(QUEUE_PATH)
        for case, items in groups:
            if any(item.packet_id not in by for item in items):
                continue
            row = _case_row(case, items, by, fresh.get(case.case_id) or queue.get(case.case_id),
                            simulated=simulated)
            append_jsonl(DECIDE_PATH, row)
            print(f"[cc_decide] {case.case_id} pred={row.get('predicted')} "
                  f"agree={row.get('agree')} err={row.get('error')} ${row.get('cost_usd')}",
                  flush=True)
        groups = []
        qn = 0
        write_decide_summary(status="running")
        if stop:
            raise stop

    try:
        for case in pending:
            qrow = queue.get(case.case_id)
            items: list[WorkItem] = []
            have_base = bool(qrow and _choices(qrow.get("qa") or {}))
            if not have_base:
                items.append(baseline_item(case, include_scope=True))
            for rec in case.records:
                rid = rec.get("id")
                if rid:
                    items.append(ablation_item(case, rid))
            add = sum(len(it.questions) for it in items)
            if groups and qn + add > args.max_questions:
                flush()
            groups.append((case, items))
            qn += add
        flush()
    except BudgetStop as exc:
        status = "partial"
        print(f"[cc_decide] BUDGET {scrub(str(exc))}", flush=True)
    except Exception as exc:
        status = "partial"
        print(f"[cc_decide] ERROR {type(exc).__name__}: {scrub(str(exc))}", flush=True)
        raise
    if args.limit and status == "complete":
        status = "partial"
    summary = write_decide_summary(status=status)
    print(f"[cc_decide] DONE status={status} n={summary['n_done']} "
          f"agree={summary['agreement']} wall_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()

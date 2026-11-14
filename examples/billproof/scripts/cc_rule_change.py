#!/usr/bin/env python3
"""Re-decide OR-connective cases under a simulated AND rule.

Searches validation+development for a real old/new policy pair (Arkansas
historical-reference-only vs current pacriteria, same clause text). None
shares a drug. The update is therefore simulated and labelled: each
alternative in an OR list becomes its own required criterion. Fact answers
are live Drex answers (reused from the queue JSONL when that case is already
decided). Code computes both decisions.

  cd billproof && PYTHONUNBUFFERED=1 PYTHONPATH=src \\
    nohup .venv/bin/python scripts/cc_rule_change.py >> out/logs/cc_rule_change.log 2>&1 &
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.pa.cases import PaCase, load_cases
from billproof.command_center.batch import (
    BudgetStop,
    assert_live,
    baseline_item,
    consume_pack,
    make_adapter,
    run_items,
)
from billproof.command_center.common import (
    QUEUE_PATH,
    QUEUE_SUMMARY_PATH,
    RULE_PATH,
    append_jsonl,
    ensure_dirs,
    error_counts,
    latest_success,
    load_jsonl,
    now_iso,
    read_json,
    scrub,
    success_ids,
)
from billproof.command_center.report import write_rule_summary
from billproof.command_center.rows import (
    and_deps,
    blocking_clauses,
    colour_of,
    derive,
    flip_name,
)

_STOP = set("""
the and for with that this from beneficiary prescriber diagnosis criteria
approval denial meet does not have been within previous days dose
""".split())


def _tokens(case: PaCase) -> set[str]:
    texts = " ".join((item.get("text") or "") for item in (case.policy.get("criteria_list") or []))
    return {t for t in re.findall(r"[a-z0-9]{5,}", texts.lower()) if t not in _STOP}


def search_pair(cases: list[PaCase]) -> dict:
    hist: dict[str, PaCase] = {}
    cur: dict[str, PaCase] = {}
    for case in cases:
        url = str((case.provenance or {}).get("url") or "")
        if "historical-reference-only" in url:
            hist.setdefault(case.list_id, case)
        elif "/arkansas/pacriteria" in url or url.rstrip("/").endswith("pacriteria"):
            cur.setdefault(case.list_id, case)
    best = 0.0
    best_pair = None
    for h_id, h in hist.items():
        ht = _tokens(h)
        for c_id, c in cur.items():
            ct = _tokens(c)
            if not ht or not ct:
                continue
            jac = len(ht & ct) / len(ht | ct)
            if jac > best:
                best = jac
                best_pair = (h_id, c_id, sorted(ht & ct)[:8])
    found = best >= 0.45
    return {
        "found": found,
        "best_jaccard": round(best, 4),
        "best_pair": best_pair,
        "historical_lists": sorted(hist),
        "current_pacriteria_lists": sorted(cur),
        "reason": (
            "A historical list shares clause text with a current PACriteria list."
            if found else
            "Arkansas historical-reference-only lists do not match current "
            "pacriteria clause text (token Jaccard under 0.45). No same-drug "
            "version pair in validation or development."
        ),
    }


def _gold_ok(item, res) -> bool | None:
    if res.error:
        return None
    from billproof.bench.pa.score import score_prediction
    from billproof.command_center.rows import prediction
    derived = derive(item.case, res.choices)
    score = score_prediction(item.case, prediction(item.case, derived), check_evidence=False)
    return bool(score["facts_all"] and score["decision"])


def _wait_for_queue(want: set[str], timeout_s: float = 10800) -> dict:
    """Reuse queue fact answers. Calling Drex again would bill the same cases."""
    started = time.time()
    while time.time() - started < timeout_s:
        got = latest_success(QUEUE_PATH)
        have = sum(1 for cid in want if cid in got and _choices_from_queue(got[cid]))
        summary = read_json(QUEUE_SUMMARY_PATH, {})
        status = summary.get("status")
        if have >= len(want) or status in ("complete", "drex_done"):
            print(f"[cc_rule] queue ready {have}/{len(want)} status={status}", flush=True)
            return got
        quiet = time.time() - QUEUE_PATH.stat().st_mtime if QUEUE_PATH.is_file() else 999
        if status in ("partial", "running") and quiet > 600 and time.time() - started > 600:
            print(f"[cc_rule] queue quiet; reuse {have}/{len(want)} status={status}", flush=True)
            return got
        print(f"[cc_rule] waiting for queue {have}/{len(want)} status={status}", flush=True)
        time.sleep(15)
    print("[cc_rule] queue wait timed out; Drex the cases still missing", flush=True)
    return latest_success(QUEUE_PATH)


def _choices_from_queue(row: dict) -> tuple[dict[str, str], dict[str, float]] | None:
    qa = row.get("qa") or {}
    if not qa or row.get("error"):
        return None
    choices = {}
    conf = {}
    for name, item in qa.items():
        if not isinstance(item, dict) or not item.get("choice"):
            return None
        choices[name] = item["choice"]
        conf[name] = float(item.get("confidence") or 0.0)
    return choices, conf


def _row(case: PaCase, choices: dict[str, str], conf: dict[str, float], *,
         reused: bool, cost: float, simulated_model: bool) -> dict:
    old = derive(case, choices)
    new = derive(case, choices, scope=old["scope"], deps=and_deps(case.criterion_deps))
    old_label, old_colour = colour_of(old["decision"])
    new_label, new_colour = colour_of(new["decision"])
    flip = flip_name(old_label, new_label)
    return {
        "case_id": case.case_id,
        "split": case.split,
        "list_id": case.list_id,
        "publisher": case.publisher,
        "document_title": (case.provenance or {}).get("document_title"),
        "source_url": (case.provenance or {}).get("url"),
        "connective_published": case.connective,
        "pair_kind": "simulated_or_to_and",
        "simulated": True,
        "model_simulated": simulated_model,
        "reused_queue": reused,
        "old_decision": old["decision"],
        "new_decision": new["decision"],
        "old_label": old_label,
        "new_label": new_label,
        "old_colour": old_colour,
        "new_colour": new_colour,
        "flip": flip,
        "why": blocking_clauses(case, old["facts"], new) if flip else [],
        "gold_decision": case.target.get("decision"),
        "old_matches_gold": old["decision"] == case.target.get("decision"),
        "cost_usd": cost,
        "error": None,
        "ts": now_iso(),
    }


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
    search = search_pair(cases)
    print(f"[cc_rule] search found={search['found']} j={search['best_jaccard']}", flush=True)
    if search["found"]:
        write_rule_summary({
            "status": "needs_review",
            "simulated": False,
            "pair_kind": "real_unapplied",
            "description": "A real version pair cleared the text-overlap bar. "
                           "It was not auto-applied.",
            "search": search,
            "n_affected": 0, "n_done": 0, "n_flips": 0, "flip_counts": {}, "flips": [],
        })
        print("[cc_rule] DONE real pair; stopped before the simulated edit", flush=True)
        return
    description = (
        "Simulated tightening on every published OR list: the single OR "
        "criterion is split so each alternative clause is required (OR→AND). "
        "Same Drex fact answers; only the combining rule changes."
    )
    affected = [c for c in cases if c.connective == "OR"]
    if args.limit:
        affected = affected[: args.limit]
    done = success_ids(RULE_PATH)
    fails = error_counts(RULE_PATH)
    pending = [c for c in affected if c.case_id not in done and fails.get(c.case_id, 0) < 2]
    print(f"[cc_rule] affected={len(affected)} pending={len(pending)}", flush=True)
    queue = _wait_for_queue({c.case_id for c in pending})
    adapter, adapter_sim = make_adapter()
    simulated_model = live_sim or adapter_sim
    status = "complete"
    need: list[PaCase] = []
    for case in pending:
        cached = _choices_from_queue(queue.get(case.case_id) or {})
        if cached:
            choices, conf = cached
            append_jsonl(RULE_PATH, _row(case, choices, conf, reused=True, cost=0.0,
                                         simulated_model=simulated_model))
        else:
            need.append(case)
    buf = []
    buf_q = 0

    def flush() -> None:
        nonlocal buf, buf_q
        if not buf:
            return
        pairs, stop = consume_pack(
            buf, lambda: run_items(buf, adapter=adapter, simulated=simulated_model,
                                   max_questions=args.max_questions, gold_flags=_gold_ok),
        )
        for item, res in pairs:
            if res.error or not res.choices:
                append_jsonl(RULE_PATH, {
                    "case_id": item.case.case_id, "error": res.error or "no answers",
                    "simulated": True, "cost_usd": res.cost_usd, "ts": now_iso(),
                })
                continue
            append_jsonl(RULE_PATH, _row(
                item.case, res.choices, res.confidence, reused=False,
                cost=res.cost_usd, simulated_model=simulated_model or res.simulated,
            ))
        buf, buf_q = [], 0
        if stop:
            raise stop

    try:
        for case in need:
            item = baseline_item(case, include_scope=True)
            if buf and buf_q + len(item.questions) > args.max_questions:
                flush()
            buf.append(item)
            buf_q += len(item.questions)
        flush()
    except BudgetStop as exc:
        status = "partial"
        print(f"[cc_rule] BUDGET {scrub(str(exc))}", flush=True)
    except Exception as exc:
        status = "partial"
        print(f"[cc_rule] ERROR {type(exc).__name__}: {scrub(str(exc))}", flush=True)
        raise
    rows = [r for r in load_jsonl(RULE_PATH) if not r.get("error")]
    # only count this affected set
    want = {c.case_id for c in affected}
    rows = [r for r in rows if r.get("case_id") in want]
    flips = [r for r in rows if r.get("flip")]
    counts: dict[str, int] = {}
    for r in flips:
        counts[r["flip"]] = counts.get(r["flip"], 0) + 1
    write_rule_summary({
        "status": "partial" if args.limit and status == "complete" else status,
        "simulated": True,
        "pair_kind": "simulated_or_to_and",
        "description": description,
        "search": search,
        "n_affected": len(affected) if not args.limit else len(affected),
        "n_planned_or": sum(1 for c in cases if c.connective == "OR"),
        "n_done": len(rows),
        "n_flips": len(flips),
        "flip_counts": counts,
        "flips": [
            {
                "case_id": r["case_id"],
                "flip": r["flip"],
                "old_label": r["old_label"],
                "new_label": r["new_label"],
                "why": r.get("why") or [],
                "list_id": r.get("list_id"),
                "document_title": r.get("document_title"),
            }
            for r in flips
        ],
    })
    print(f"[cc_rule] DONE flips={len(flips)} counts={counts} "
          f"wall_s={time.time() - t0:.1f}", flush=True)


if __name__ == "__main__":
    main()

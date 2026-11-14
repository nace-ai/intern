#!/usr/bin/env python3
"""Assemble final PA benchmark metrics + report table.

Reads data/runs/pa_*_<split>.jsonl (and _supp / decide / tune artifacts),
computes the 4-column table with Wilson CIs, per-decision breakdowns,
calibration, the frozen-cutoff trust-dial point, deciding-record agreement and
total spend. Writes data/metrics/pa_<split>_summary.json and per-metric files;
prints the BENCH_PA.md table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from billproof.bench.metrics import wilson_ci
from billproof.config import DATA_DIR, RUNS_DIR


def _load(pattern: str) -> list[dict]:
    rows = {}
    for path in sorted(RUNS_DIR.glob(pattern)):
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                rows[r["case_id"]] = r
            except (json.JSONDecodeError, KeyError):
                continue
    return list(rows.values())


def _col_summary(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    s = lambda key: sum(1 for r in rows if (r.get("score") or {}).get(key))
    whole, dec = s("whole_case"), s("decision")
    fr = sum((r.get("score") or {}).get("n_facts_right", 0) for r in rows)
    ft = sum((r.get("score") or {}).get("n_facts", 0) for r in rows)
    lats = sorted(r.get("latency_ms") or 0 for r in rows)
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    calls = sum(r.get("calls") or 0 for r in rows)
    by_dec = defaultdict(lambda: [0, 0])
    for r in rows:
        g = r.get("gold_decision") or "?"
        by_dec[g][0] += bool((r.get("score") or {}).get("decision"))
        by_dec[g][1] += 1
    return {
        "n": n, "whole_case": whole / n, "whole_k": whole,
        "whole_ci": list(wilson_ci(whole, n)),
        "decision": dec / n, "decision_k": dec,
        "decision_ci": list(wilson_ci(dec, n)),
        "per_fact": fr / ft if ft else None, "n_facts": ft,
        "scope": s("scope") / n, "missing_info": s("missing_info") / n,
        "evidence": s("evidence") / n if any((r.get("score") or {}).get("evidence") is not None for r in rows) else None,
        "p50_ms": lats[n // 2], "p95_ms": lats[min(n - 1, int(n * 0.95))],
        "cost_total": cost, "cost_per_1000": cost / n * 1000,
        "calls_per_case": calls / n,
        "escalated_qs": sum(len(r.get("escalated") or []) for r in rows),
        "flagged_cases": sum(1 for r in rows if r.get("flagged")),
        "errors": sum(1 for r in rows if r.get("error")),
        "by_decision": {g: {"acc": v[0] / v[1], "n": v[1]} for g, v in by_dec.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="development")
    ap.add_argument("--tune-split", default="validation")
    ap.add_argument("--panel", default=str(RUNS_DIR / "pa_panel_dev370.json"),
                    help="JSON list of case_ids constituting the headline test panel")
    args = ap.parse_args()
    split, tsplit = args.split, args.tune_split
    panel = set(json.loads(Path(args.panel).read_text())) if Path(args.panel).exists() else None
    out = {"computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "split": split, "columns": {}, "smart_uses": {}, "spend": {}}

    router_total = 0.0
    nace_total = 0.0
    per_case_cost = {}
    print(f"{'column':10s} {'n':>4s} {'whole-case':>12s} {'decision':>12s} {'per-fact':>9s} {'p50':>7s} {'$/1k':>8s}")
    for col in ("gpt", "gptqa", "drex", "hybrid", "checked", "drexfin"):
        for suff in ("", "_supp"):
            rows = _load(f"pa_{col}{suff}_{split}*.jsonl") if suff else _load(f"pa_{col}_{split}.jsonl")
            if panel is not None and not suff:
                rows = [r for r in rows if r["case_id"] in panel]
            if not rows:
                continue
            s = _col_summary(rows)
            name = col + suff
            out["columns"][name] = s
            lo, hi = s["whole_ci"]
            dl, dh = s["decision_ci"]
            print(f"{name:10s} {s['n']:>4d} {s['whole_case']*100:5.1f}% "
                  f"[{lo*100:.0f},{hi*100:.0f}] {s['decision']*100:5.1f}% "
                  f"[{dl*100:.0f},{dh*100:.0f}] {(s['per_fact'] or 0)*100:8.1f}% "
                  f"{s['p50_ms']:6.0f}ms ${s['cost_per_1000']:7.2f}")
            per_case_cost[name] = {r["case_id"]: r.get("cost_usd") or 0 for r in rows}
    # spend attribution: gpt/gptqa rows are pure router; drex rows pure nace;
    # hybrid = drex + gpt-escalations (router part = row cost - drex sibling);
    # checked = gpt + drex (router part already counted by the gpt row;
    # its extra vs gpt row is the drex call -> nace).
    router_total += sum(per_case_cost.get("gpt", {}).values())
    router_total += sum(per_case_cost.get("gptqa", {}).values())
    for name in ("drex", "drex_supp", "drexfin"):
        nace_total += sum(per_case_cost.get(name, {}).values())
    for cid, c in per_case_cost.get("hybrid", {}).items():
        router_total += max(0.0, c - per_case_cost.get("drex", {}).get(cid, 0.0))
    for cid, c in per_case_cost.get("checked", {}).items():
        nace_total += max(0.0, c - per_case_cost.get("gpt", {}).get(cid, 0.0))

    # decide probe
    dec_rows = _load(f"pa_decide_{split}.jsonl")
    dec_rows = [r for r in dec_rows if not r.get("error")]
    if dec_rows:
        agree = sum(1 for r in dec_rows if r.get("agree"))
        calls = sum(r.get("calls") or 0 for r in dec_rows)
        cost = sum(r.get("cost_usd") or 0 for r in dec_rows)
        secs = sum(r.get("elapsed_s") or 0 for r in dec_rows)
        out["smart_uses"]["deciding_record"] = {
            "n": len(dec_rows), "agreement": agree / len(dec_rows),
            "agree_k": agree, "calls_per_case": calls / len(dec_rows),
            "seconds_per_case": secs / len(dec_rows), "cost_total": cost,
            "by_gold_kind": {f"{k[0]}|{'agree' if k[1] else 'miss'}": v
                             for k, v in Counter((r.get("gold_kind"), bool(r.get("agree")))
                                                 for r in dec_rows).items()},
        }
        nace_total += cost
        print(f"decide-probe agreement {agree}/{len(dec_rows)} "
              f"calls/case {calls/len(dec_rows):.1f} ${cost:.4f}")

    out["spend"] = {"router_usd": round(router_total, 4), "nace_usd": round(nace_total, 4)}
    mdir = DATA_DIR / "metrics"
    mdir.mkdir(exist_ok=True, parents=True)
    (mdir / f"pa_{split}_summary.json").write_text(json.dumps(out, indent=1))
    print(f"\nwrote {mdir}/pa_{split}_summary.json")

    # flat per-metric files: {value, n, source_run, simulated, computed_at}
    computed_at = out["computed_at"]
    flat = {}
    for name, s in out["columns"].items():
        if not s.get("n"):
            continue
        src = f"pa_{name}_{split}.jsonl"
        for key in ("whole_case", "decision", "per_fact", "scope",
                    "missing_info", "evidence", "cost_per_1000"):
            if s.get(key) is None:
                continue
            flat[f"{name}_{key}"] = {"value": s[key], "n": s["n"],
                                     "source_run": src, "simulated": False,
                                     "computed_at": computed_at}
        for key in ("whole_ci", "decision_ci"):
            flat[f"{name}_{key}"] = {"value": s[key], "n": s["n"],
                                     "source_run": src, "simulated": False,
                                     "computed_at": computed_at}
    dr = out["smart_uses"].get("deciding_record")
    if dr:
        for key in ("agreement", "calls_per_case", "seconds_per_case", "cost_total"):
            flat[f"deciding_record_{key}"] = {"value": dr[key], "n": dr["n"],
                                              "source_run": f"pa_decide_{split}.jsonl",
                                              "simulated": False, "computed_at": computed_at}
    flat["spend_router_usd"] = {"value": out["spend"]["router_usd"], "n": None,
                                "source_run": "all", "simulated": False,
                                "computed_at": computed_at}
    flat["spend_nace_usd"] = {"value": out["spend"]["nace_usd"], "n": None,
                              "source_run": "all", "simulated": False,
                              "computed_at": computed_at}
    (mdir / f"pa_{split}_metrics.json").write_text(json.dumps(flat, indent=1))
    print(f"wrote {mdir}/pa_{split}_metrics.json ({len(flat)} metrics)")
    print(f"spend: router ${router_total:.3f} · nace ${nace_total:.4f}")


if __name__ == "__main__":
    main()

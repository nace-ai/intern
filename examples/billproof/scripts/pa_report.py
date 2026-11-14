#!/usr/bin/env python3
"""Summarize pa_*_<split>.jsonl run files: per-column accuracy, latency, cost.

Prints a table and (with --metrics) writes data/metrics/pa_<col>_<metric>.json
entries of the form {value,n,source_run,simulated,computed_at}.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.metrics import wilson_ci
from billproof.config import DATA_DIR, RUNS_DIR


def _pct(x, d=1):
    return "n/a" if x is None else f"{x*100:.{d}f}%"


def load_rows(column: str, split: str) -> list[dict]:
    path = RUNS_DIR / f"pa_{column}_{split}.jsonl"
    if not path.exists():
        return []
    rows = {}
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
            rows[r["case_id"]] = r  # last write wins
        except (json.JSONDecodeError, KeyError):
            continue
    return list(rows.values())


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    whole = sum(1 for r in rows if (r.get("score") or {}).get("whole_case"))
    dec = sum(1 for r in rows if (r.get("score") or {}).get("decision"))
    scope = sum(1 for r in rows if (r.get("score") or {}).get("scope"))
    facts_r = sum((r.get("score") or {}).get("n_facts_right", 0) for r in rows)
    facts_t = sum((r.get("score") or {}).get("n_facts", 0) for r in rows)
    crit_r = sum((r.get("score") or {}).get("n_criteria_right", 0) for r in rows)
    crit_t = sum(1 for r in rows for _ in ((r.get("score") or {}).get("per_criterion") or {}))
    missing = sum(1 for r in rows if (r.get("score") or {}).get("missing_info"))
    evidence = [r for r in rows if (r.get("score") or {}).get("evidence") is not None]
    ev_ok = sum(1 for r in evidence if (r.get("score") or {}).get("evidence"))
    lats = sorted(r.get("latency_ms") or 0 for r in rows)
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    esc = sum(len(r.get("escalated") or []) for r in rows)
    flagged = sum(1 for r in rows if r.get("flagged"))
    calls = sum(r.get("calls") or 0 for r in rows)
    lo_w, hi_w = wilson_ci(whole, n)
    lo_d, hi_d = wilson_ci(dec, n)
    return {
        "n": n,
        "whole_case": whole / n, "whole_ci": [lo_w, hi_w], "whole_k": whole,
        "decision": dec / n, "decision_ci": [lo_d, hi_d], "decision_k": dec,
        "scope": scope / n,
        "per_fact": facts_r / facts_t if facts_t else None, "n_facts": facts_t,
        "per_criterion": crit_r / crit_t if crit_t else None, "n_criteria": crit_t,
        "missing_info": missing / n,
        "evidence_ok": ev_ok / len(evidence) if evidence else None,
        "p50_ms": lats[n // 2], "p95_ms": lats[min(n - 1, int(n * 0.95))],
        "cost_total": cost, "cost_per_case": cost / n,
        "cost_per_1000": cost / n * 1000,
        "calls": calls, "calls_per_case": calls / n,
        "escalated_qs": esc, "escalation_rate": esc / n if n else 0,
        "flagged_cases": flagged, "flag_rate": flagged / n,
        "errors": sum(1 for r in rows if r.get("error")),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", default="validation")
    ap.add_argument("--columns", default="gpt,drex,hybrid,checked")
    ap.add_argument("--metrics", action="store_true", help="write data/metrics/pa_*.json")
    ap.add_argument("--tag", default=None, help="metrics source tag, e.g. pilot / test")
    args = ap.parse_args()
    tag = args.tag or args.split
    out = {}
    for col in [c.strip() for c in args.columns.split(",") if c.strip()]:
        rows = load_rows(col, args.split)
        s = summarize(rows)
        out[col] = s
        if not s["n"]:
            print(f"{col:8s}  no rows")
            continue
        print(f"{col:8s} n={s['n']:4d}  whole={_pct(s['whole_case'])} "
              f"ci[{_pct(s['whole_ci'][0])},{_pct(s['whole_ci'][1])}]  "
              f"decision={_pct(s['decision'])}  per-fact={_pct(s['per_fact'])}  "
              f"scope={_pct(s['scope'])}  ev={_pct(s['evidence_ok'])}  "
              f"p50={s['p50_ms']:.0f}ms  $/case=${s['cost_per_case']:.4f}  "
              f"tot=${s['cost_total']:.3f}  esc/case={s['escalation_rate']:.2f}  "
              f"flag={_pct(s['flag_rate'],0)}  err={s['errors']}")
    if args.metrics:
        mdir = DATA_DIR / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for col, s in out.items():
            if not s.get("n"):
                continue
            for key in ("whole_case", "decision", "per_fact", "per_criterion",
                        "scope", "missing_info", "cost_per_1000", "p50_ms",
                        "escalation_rate", "flag_rate"):
                val = s.get(key)
                if val is None:
                    continue
                entry = {"value": val, "n": s["n"],
                         "source_run": f"pa_{col}_{args.split}",
                         "simulated": False, "computed_at": now}
                if key in ("whole_case", "decision"):
                    entry["ci95"] = s["whole_ci"] if key == "whole_case" else s["decision_ci"]
                (mdir / f"pa_{tag}_{col}_{key}.json").write_text(json.dumps(entry, indent=1))
        print(f"wrote metrics to {mdir} (tag={tag})")


if __name__ == "__main__":
    main()

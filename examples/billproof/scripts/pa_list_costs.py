#!/usr/bin/env python3
"""List-price cost metrics for GPT columns — recomputed from recorded tokens.

List price for openai/gpt-6.1-sol (the rule tonight): $2 per 1M input tokens,
$10 per 1M output tokens. Adapter-recorded `cost_usd` used an older
$2.50/$15 rate; those fields are kept untouched. This script only ADDS
`*_list` metric files next to the existing cost metrics, plus `*_list` keys
inside the flat development bundles.

- col1 gpt:        list cost over its own recorded tokens.
- col4 checked:    col1 row's tokens at list + the Drex row's recorded cost
                   (Nace pricing is unchanged by the list rule).
- col3 hybrid:     Drex row's recorded cost + any escalated GPT tokens
                   (row tokens minus the Drex row's tokens) at list; zero
                   escalations on both panels -> equals recorded cost.
- Drex-vs-GPT cost ratio at list.
- Command Center:  cc_gpt_projected_*_list from command_center/queue_gpt.jsonl.

Pilot panel (n=20) is identified by file order: the pilot ran before the
tune run appended to the same validation files (ts ~08:06 vs 08:11+).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billproof.config import DATA_DIR, RUNS_DIR  # noqa: E402

IN_RATE, OUT_RATE = 2.0, 10.0  # USD per 1M tokens, list price
RATE_NOTE = {"list_price_usd_per_mtok": {"input": IN_RATE, "output": OUT_RATE}}


def _load_ordered(path: Path) -> tuple[list[str], dict[str, dict]]:
    """Return (case_ids in first-seen order, last non-error row per id)."""
    order: list[str] = []
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = r.get("case_id")
            if not cid:
                continue
            if cid not in rows:
                order.append(cid)
            if not r.get("error"):
                rows[cid] = r
    return order, rows


def _list_cost(row: dict) -> float:
    return ((row.get("input_tokens") or 0) * IN_RATE
            + (row.get("output_tokens") or 0) * OUT_RATE) / 1e6


def _metric(value, n, source, extra=None):
    m = {"value": value, "n": n, "source_run": source, "simulated": False,
         "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         **RATE_NOTE}
    if extra:
        m.update(extra)
    return m


def _write(name: str, m: dict) -> dict:
    path = DATA_DIR / "metrics" / f"{name}.json"
    path.write_text(json.dumps(m, indent=1) + "\n")
    print(f"  {name}: {m['value']:.4f} (n={m['n']})")
    return m


def panel_costs(tag: str, gpt: dict, drex: dict, hybrid: dict,
                checked: dict, src) -> dict:
    out = {}
    if gpt:
        out["gpt"] = _write(
            f"pa_{tag}_gpt_cost_per_1k_list",
            _metric(sum(_list_cost(r) for r in gpt.values()) / len(gpt) * 1000,
                    len(gpt), src("gpt")))
    if hybrid:
        tot = 0.0
        for cid, r in hybrid.items():
            d = drex.get(cid) or {}
            dcost = d.get("cost_usd") or r.get("cost_usd") or 0.0
            if r.get("escalated"):
                ein = (r.get("input_tokens") or 0) - (d.get("input_tokens") or 0)
                eout = (r.get("output_tokens") or 0) - (d.get("output_tokens") or 0)
                tot += dcost + (max(ein, 0) * IN_RATE
                                + max(eout, 0) * OUT_RATE) / 1e6
            else:
                tot += dcost
        out["hybrid"] = _write(
            f"pa_{tag}_hybrid_cost_per_1k_list",
            _metric(tot / len(hybrid) * 1000, len(hybrid), src("hybrid")))
    if checked:
        tot = 0.0
        for cid in checked:
            tot += _list_cost(gpt.get(cid) or {}) \
                + ((drex.get(cid) or {}).get("cost_usd") or 0.0)
        out["checked"] = _write(
            f"pa_{tag}_checked_cost_per_1k_list",
            _metric(tot / len(checked) * 1000, len(checked), src("checked")))
    if gpt and drex:
        dper1k = (sum(r.get("cost_usd") or 0.0 for r in drex.values())
                  / len(drex) * 1000)
        if dper1k > 0:
            out["ratio"] = _write(
                f"pa_{tag}_drex_vs_gpt_cost_ratio_list",
                _metric(out["gpt"]["value"] / dper1k, len(gpt),
                        f"{src('gpt')}|{src('drex')}"))
    return out


def main() -> None:
    panel = set(json.loads((RUNS_DIR / "pa_panel_dev370.json").read_text()))

    # --- development (held-out 370 panel) ---
    _, g_all = _load_ordered(RUNS_DIR / "pa_gpt_development.jsonl")
    _, d_all = _load_ordered(RUNS_DIR / "pa_drex_development.jsonl")
    _, h_all = _load_ordered(RUNS_DIR / "pa_hybrid_development.jsonl")
    _, c_all = _load_ordered(RUNS_DIR / "pa_checked_development.jsonl")
    keep = lambda rows: {k: v for k, v in rows.items() if k in panel}
    dev = panel_costs(
        "development", keep(g_all), keep(d_all), keep(h_all), keep(c_all),
        lambda col: f"pa_{col}_development.jsonl")

    # --- pilot (first 20 unique ids in the validation gpt file) ---
    order, gv = _load_ordered(RUNS_DIR / "pa_gpt_validation.jsonl")
    pilot = set(order[:20])
    def vload(col):
        _, rows = _load_ordered(RUNS_DIR / f"pa_{col}_validation.jsonl")
        return {k: v for k, v in rows.items() if k in pilot}
    panel_costs("pilot", vload("gpt"), vload("drex"), vload("hybrid"),
                vload("checked"), lambda col: f"pa_{col}_validation.jsonl")

    # --- Command Center GPT projection ---
    qpath = DATA_DIR / "command_center" / "queue_gpt.jsonl"
    if qpath.exists():
        qrows = [r for r in (json.loads(l) for l in qpath.read_text().splitlines()
                             if l.strip()) if not r.get("error")]
        if qrows:
            per_case = sum(_list_cost(r) for r in qrows) / len(qrows)
            nq = qrows[0].get("projected_full_queue") or 2211
            _write("cc_gpt_projected_cost_per_1000_list",
                   _metric(per_case * 1000, len(qrows),
                           "command_center/queue_gpt.jsonl", {"projected": True}))
            _write("cc_gpt_projected_full_queue_cost_usd_list",
                   _metric(per_case * nq, len(qrows),
                           "command_center/queue_gpt.jsonl",
                           {"projected": True, "n_queue": nq}))
            _write("cc_router_spend_usd_list",
                   _metric(sum(_list_cost(r) for r in qrows), len(qrows),
                           "command_center/queue_gpt.jsonl"))

    # --- mirror into the flat dev bundle + summary ---
    mpath = DATA_DIR / "metrics" / "pa_development_metrics.json"
    m = json.loads(mpath.read_text())
    for key, val in (("gpt_cost_per_1k_list", dev.get("gpt")),
                     ("hybrid_cost_per_1k_list", dev.get("hybrid")),
                     ("checked_cost_per_1k_list", dev.get("checked")),
                     ("drex_vs_gpt_cost_ratio_list", dev.get("ratio"))):
        if val:
            m[key] = val
    mpath.write_text(json.dumps(m, indent=1) + "\n")

    spath = DATA_DIR / "metrics" / "pa_development_summary.json"
    if spath.exists():
        s = json.loads(spath.read_text())
        cols = s.get("columns") or {}
        for col, key in (("gpt", "gpt"), ("hybrid", "hybrid"),
                         ("checked", "checked")):
            if col in cols and key in dev:
                cols[col]["cost_per_1k_list"] = dev[key]["value"]
        if "drex" in cols and dev.get("ratio"):
            cols["drex"]["vs_gpt_cost_ratio_list"] = dev["ratio"]["value"]
        spath.write_text(json.dumps(s, indent=1) + "\n")

    print("\n[pa_list_costs] done")


if __name__ == "__main__":
    main()

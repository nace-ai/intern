"""Step 6: metrics table + slide charts for Drex (before/after learning) vs the LLM baseline.

Metrics are on the HELD-OUT cases (not in the human feedback set) so the learned
thresholds are not graded on the data they were fit to. All-60 numbers are in the JSON.

    python compare.py [--mock]      # writes results/metrics_<mode>.{json,md} + results/charts/<mode>_*.png
"""
from __future__ import annotations

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from drexflaky.common import CHARTS, RESULTS, banner, load_costs, load_env, load_labels, p50, read_json, resolve_mode, result_path, write_json  # noqa: E402


def metrics(rows, decisions, labels_pred, costs, latencies, usd_per_1k):
    n = len(rows)
    truths = [r["truth"] for r in rows]
    acc = sum(lp == t for lp, t in zip(labels_pred, truths)) / n
    wq = sum(d == "QUARANTINE" and t == "real" for d, t in zip(decisions, truths))
    nb = sum(d == "BLOCK" and t == "flaky" for d, t in zip(decisions, truths))
    rt = sum(d == "RETRY" for d in decisions)
    cost = sum(costs[(t, d)] for d, t in zip(decisions, truths))
    return {"n": n, "accuracy": round(acc, 3), "wrong_quarantines_of_real_bugs": wq, "needless_blocks": nb,
            "retries": rt, "expected_cost_eng_min": cost, "cost_per_failure": round(cost / n, 2),
            "p50_latency_ms": p50(latencies), "usd_per_1k_decisions": usd_per_1k}


def watermark(fig, mode):
    if mode != "real":
        fig.text(0.5, 0.5, "MOCK DATA", fontsize=60, color="red", alpha=0.18, ha="center", va="center", rotation=25)
    fig.text(0.99, 0.01, banner(mode), fontsize=8, ha="right", color="red" if mode != "real" else "gray")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    load_env()
    dmode = resolve_mode(a.mock, "NACE_API_KEY")
    lmode = resolve_mode(a.mock, "DEEPINFRA_API_KEY")
    # Drex real + no DeepInfra key -> "real" view with the LLM row as an explicit placeholder (never mock numbers)
    mode = "real" if dmode == "real" else "mock"
    llm_pending = dmode == "real" and lmode != "real"
    out_llm_models = []
    costs = load_costs()
    labels = {r["case_id"]: r for r in load_labels()}
    drex_all = read_json(result_path("drex", dmode)); drex = {c["case_id"]: c for c in drex_all["cases"]}
    llm_all = read_json(result_path("llm", lmode)); llm = {c["case_id"]: c for c in llm_all["cases"]}
    ndi = {c["case_id"]: c for c in read_json(result_path("ndi", dmode))["cases"]}
    learn = read_json(result_path("learn", dmode)); rep = {x["case_id"]: x for x in learn["replay"]}

    out = {"mode": {"drex": dmode, "llm": lmode}, "drex_model": drex_all.get("model_requested"), "llm_model": llm_all.get("model"),
           "thresholds_before": learn["thresholds_before"], "thresholds_after": learn["thresholds_after"], "tables": {}}
    for split, keep in (("heldout", lambda r: not r["feedback_set"]), ("all", lambda r: True)):
        rows = [r for r in labels.values() if keep(r)]
        ids = [r["case_id"] for r in rows]
        dl = ["flaky" if (drex[i]["p_flaky"] or 0) >= 0.5 else "real" for i in ids]
        lat = [drex[i].get("latency_ms") for i in ids]
        out["tables"][split] = {
            "Drex (before learning)": metrics(rows, [rep[i]["before"] for i in ids], dl, costs, lat, drex_all.get("usd_per_1k")),
            "Drex (after learning)": metrics(rows, [rep[i]["after"] for i in ids], dl, costs, lat, drex_all.get("usd_per_1k")),
            "Rule baseline (retry passed => quarantine, else block)": metrics(
                rows, ["QUARANTINE" if ndi[i]["fields"].get("retry_passed") else "BLOCK" for i in ids],
                ["flaky" if ndi[i]["fields"].get("retry_passed") else "real" for i in ids], costs, [0.0] * len(ids), 0.0),
        }
        if llm_pending:
            t = out["tables"][split]
            t["LLM baseline (DeepInfra) - PENDING"] = {k: None for k in next(iter(t.values()))}
        else:
            for tag in ("llm", "llm2"):
                pth = result_path(tag, lmode)
                if not pth.exists():
                    continue
                la = read_json(pth); lc = {c["case_id"]: c for c in la["cases"]}
                out["tables"][split][f"LLM baseline: {la['model']} (DeepInfra)"] = metrics(
                    rows, [lc[i]["action"] for i in ids], [lc[i]["label"] for i in ids], costs,
                    [lc[i].get("latency_ms") for i in ids], la.get("usd_per_1k"))
    write_json(RESULTS / f"metrics_{mode}.json", out)

    # markdown table
    lines = [f"**{banner(mode)}**" + (" (Drex + NDI real; LLM baseline pending: no DEEPINFRA_API_KEY yet)" if llm_pending else "") + "  ",
             f"Drex model: `{out['drex_model']}` · LLM: `{'pending (no DEEPINFRA_API_KEY)' if llm_pending else 'Llama-3.3-70B-Instruct-Turbo, DeepSeek-V3.2 (DeepInfra)'}` · thresholds {learn['thresholds_before']} → {learn['thresholds_after']}", ""]
    for split in ("heldout", "all"):
        t = out["tables"][split]
        n = t["Drex (before learning)"]["n"]
        lines += [f"### {'Held-out cases' if split == 'heldout' else 'All cases'} (n={n})", "",
                  "| System | Accuracy (flaky vs real) | Real bugs quarantined | Needless blocks | Retries | Expected cost (eng-min) | p50 latency (ms) | $ / 1k decisions |",
                  "|---|---|---|---|---|---|---|---|"]
        for k, m in t.items():
            if m.get("n") is None:
                lines.append(f"| {k} | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |"); continue
            usd = f"${m['usd_per_1k_decisions']:.4f}" if m["usd_per_1k_decisions"] is not None else "n/a"
            lat = f"{m['p50_latency_ms']:.0f}" if m["p50_latency_ms"] is not None else "n/a"
            lines.append(f"| {k} | {m['accuracy']:.1%} | {m['wrong_quarantines_of_real_bugs']} | {m['needless_blocks']} | {m['retries']} | "
                         f"{m['expected_cost_eng_min']:.0f} | {lat} | {usd} |")
        lines.append("")
    (RESULTS / f"metrics_{mode}.md").write_text("\n".join(lines))
    print("\n".join(lines))

    CHARTS.mkdir(parents=True, exist_ok=True)
    lo0, hi0 = learn["thresholds_before"]["lo"], learn["thresholds_before"]["hi"]
    lo1, hi1 = learn["thresholds_after"]["lo"], learn["thresholds_after"]["hi"]
    # 1. histogram
    fig, ax = plt.subplots(figsize=(9, 4.8))
    pf = [drex[i]["p_flaky"] for i, r in labels.items() if r["truth"] == "flaky" and drex[i]["p_flaky"] is not None]
    pr = [drex[i]["p_flaky"] for i, r in labels.items() if r["truth"] == "real" and drex[i]["p_flaky"] is not None]
    bins = [i / 20 for i in range(21)]
    ax.hist([pr, pf], bins=bins, stacked=True, color=["#d62728", "#2ca02c"], label=["truly real regression", "truly flaky"])
    for x, s, c in ((lo0, "--", "gray"), (hi0, "--", "gray"), (lo1, "-", "black"), (hi1, "-", "black")):
        ax.axvline(x, ls=s, color=c, lw=1.5)
    ax.text(lo1, ax.get_ylim()[1] * 0.75, f" lo={lo1:.2f}", fontsize=9); ax.text(hi1, ax.get_ylim()[1] * 0.75, f" hi={hi1:.2f}", fontsize=9)
    ax.set_xlabel("Drex P(flaky)  (noul)"); ax.set_ylabel("# failures")
    ax.set_title("Drex probability by ground truth  (dashed = before learning, solid = after)")
    ax.text(0.12, 0.6, "BLOCK", transform=ax.transAxes); ax.text(0.55, 0.6, "RETRY", transform=ax.transAxes); ax.text(0.9, 0.6, "QUAR.", transform=ax.transAxes)
    ax.legend(loc="upper left", bbox_to_anchor=(0.1, 0.99)); watermark(fig, dmode); fig.tight_layout(); fig.savefig(CHARTS / f"{mode}_prob_histogram.png", dpi=160); plt.close(fig)
    # 2. what the learning step weighed: cost on all failures under each threshold choice (replayed, no new calls)
    from policy import decide as _d
    allp = [(drex[i]["p_flaky"], r["truth"]) for i, r in labels.items()]
    def c_at(lo, hi):
        return sum(costs[(tr, _d(p, lo, hi))] for p, tr in allp)
    cd = learn["cost_derived_if_calibrated"]
    opts = [(f"day-0\n({lo0:.2f}/{hi0:.2f})", c_at(lo0, hi0), "#9e9e9e"),
            (f"learned from\n{learn['feedback_n']} labels\n({lo1:.2f}/{hi1:.2f})", c_at(lo1, hi1), "#1f77b4"),
            (f"cost-table only\n({cd['lo']:.2f}/{cd['hi']:.2f})", c_at(cd["lo"], cd["hi"]), "#bcbd22")]
    for w in learn.get("confident_disagreements", [])[:1]:
        f = w.get("cheapest_fix")
        if f:
            opts.append((f"move lo to 'fix'\n{w['case_id']}\n({f['lo']:.4f}/{f['hi']:.2f})", c_at(f["lo"], f["hi"]), "#d62728"))
    fig, ax = plt.subplots(figsize=(8, 4.6))
    b = ax.bar([o[0] for o in opts], [o[1] for o in opts], color=[o[2] for o in opts], edgecolor="black"); ax.bar_label(b, fmt="%.0f")
    ax.set_ylabel(f"expected cost, all {len(allp)} failures (eng-min)")
    ax.set_title("Learning step: which thresholds minimise cost? (replayed Drex probabilities)", fontsize=10)
    ax.tick_params(axis="x", labelsize=8)
    watermark(fig, dmode); fig.tight_layout(); fig.savefig(CHARTS / f"{mode}_cost_before_after.png", dpi=160); plt.close(fig)
    # 3. Drex vs baselines
    fig, axes = plt.subplots(1, 4, figsize=(13, 4))
    names = [n for n in t if t[n].get("n") is not None and n != "Drex (before learning)"]
    def _short(n):
        if n.startswith("Drex"): return "Drex"
        if n.startswith("Rule"): return "Rule"
        return n.split(": ", 1)[-1].split("/")[-1].replace(" (DeepInfra)", "").replace("Llama-3.3-70B-Instruct-Turbo", "Llama-3.3\n70B").replace("DeepSeek-V3.2", "DeepSeek\nV3.2")
    short = {n: _short(n) for n in names}
    if llm_pending:
        names.append("LLM baseline (DeepInfra) - PENDING"); short[names[-1]] = "LLM\n(pending)"
    palette = {"Drex": "#1f77b4", "Rule": "#7f7f7f", "LLM": "#ff7f0e", "LLM\n(pending)": "#ffffff"}
    for ax, key, title in zip(axes, ["expected_cost_eng_min", "wrong_quarantines_of_real_bugs", "p50_latency_ms", "usd_per_1k_decisions"],
                              ["expected cost (eng-min) ↓", "real bugs quarantined ↓", "p50 latency ms ↓", "$ per 1k decisions ↓"]):
        v = [(t[n][key] or 0) for n in names]
        b = ax.bar([short[n] for n in names], v, color=[palette.get(short[n], "#ff7f0e") for n in names], edgecolor="black")
        ax.tick_params(axis="x", labelsize=7)
        ax.bar_label(b, labels=["pending" if t[n].get("n") is None else ((f"{t[n][key]:.0f}" if t[n][key] >= 100 else f"{t[n][key]:.3g}") if t[n][key] is not None else "n/a") for n in names])
        ax.set_title(title, fontsize=10)
    fig.suptitle(f"Drex ({out['drex_model']}) vs baselines  — all failures, n={t['Drex (before learning)']['n']}"
                 + ("  ·  LLM baseline pending" if llm_pending else ""))
    watermark(fig, mode); fig.tight_layout(); fig.savefig(CHARTS / f"{mode}_drex_vs_llm.png", dpi=160); plt.close(fig)
    # copies at the filenames the deck expects
    import shutil
    if mode == "real":
        for nm in ("prob_histogram", "cost_before_after", "drex_vs_llm"):
            shutil.copyfile(CHARTS / f"real_{nm}.png", RESULTS / f"{nm}.png")
    print(f"charts -> {CHARTS}/{mode}_*.png")


if __name__ == "__main__":
    main()

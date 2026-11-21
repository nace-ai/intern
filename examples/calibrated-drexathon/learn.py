"""Step 4: the learning moment.

A human corrects the outcomes of the first N failures (the `feedback_set` in
labels.csv, "day 1"). We search for the (lo, hi) thresholds that minimise total
expected cost on those corrected outcomes (cost table in data/cost_table.csv),
preferring the smallest move from the current thresholds on ties. Then we replay the
LOGGED Drex probabilities for every case (no new API calls) under old vs new
thresholds and report cost on the held-out cases the feedback never saw.

    python learn.py [--mock]
"""
from __future__ import annotations

import argparse

from drexflaky.common import DEFAULT_HI, DEFAULT_LO, load_costs, load_labels, load_env, resolve_mode, read_json, result_path, write_json
from policy import decide, cost_of


def cost_derived_thresholds(costs):
    """If p were perfectly calibrated, the expected-cost-optimal cut points."""
    def ec(p, a):
        return p * costs[("flaky", a)] + (1 - p) * costs[("real", a)]
    grid = [i / 1000 for i in range(1001)]
    lo = next(p for p in grid if ec(p, "RETRY") <= ec(p, "BLOCK"))
    hi = next(p for p in grid if ec(p, "QUARANTINE") <= ec(p, "RETRY"))
    return lo, hi


def fit_thresholds(probs, truths, costs, lo0=DEFAULT_LO, hi0=DEFAULT_HI):
    grid = [i / 100 for i in range(1, 100)]
    best = None
    for lo in grid:
        for hi in grid:
            if hi < lo:
                continue
            c = cost_of([decide(p, lo, hi) for p in probs], truths, costs)
            key = (c, abs(lo - lo0) + abs(hi - hi0))
            if best is None or key < best[0]:
                best = (key, lo, hi)
    return best[1], best[2], best[0][0]


def run(mode: str, lo0=DEFAULT_LO, hi0=DEFAULT_HI) -> dict:
    labels = load_labels()
    costs = load_costs()
    drex = {c["case_id"]: c for c in read_json(result_path("drex", mode))["cases"]}
    fb = [r for r in labels if r["feedback_set"] and drex[r["case_id"]].get("p_flaky") is not None]
    lo1, hi1, fb_cost_after = fit_thresholds([drex[r["case_id"]]["p_flaky"] for r in fb], [r["truth"] for r in fb], costs, lo0, hi0)
    corrections = []
    for r in fb:
        p = drex[r["case_id"]]["p_flaky"]
        d0 = decide(p, lo0, hi0)
        ideal = min(("QUARANTINE", "RETRY", "BLOCK"), key=lambda a: costs[(r["truth"], a)])
        if d0 != ideal:
            corrections.append({"case_id": r["case_id"], "p_flaky": p, "decision_before": d0, "human_says": r["truth"],
                                "cost_before": costs[(r["truth"], d0)], "decision_after": decide(p, lo1, hi1),
                                "cost_after": costs[(r["truth"], decide(p, lo1, hi1))]})
    replay = []
    for r in labels:
        p = drex[r["case_id"]].get("p_flaky")
        b, a = decide(p, lo0, hi0), decide(p, lo1, hi1)
        replay.append({"case_id": r["case_id"], "truth": r["truth"], "ambiguous": r["ambiguous"], "feedback_set": r["feedback_set"],
                       "p_flaky": p, "before": b, "after": a, "cost_before": costs[(r["truth"], b)], "cost_after": costs[(r["truth"], a)]})
    held = [x for x in replay if not x["feedback_set"]]
    probs_fb = [drex[r["case_id"]]["p_flaky"] for r in fb]; tr_fb = [r["truth"] for r in fb]

    def fb_cost(lo, hi, extra=()):
        ps = probs_fb + [e[0] for e in extra]; ts = tr_fb + [e[1] for e in extra]
        return cost_of([decide(p, lo, hi) for p in ps], ts, costs)
    # safe band: every lo (with hi fixed) and every hi (with lo fixed) that keeps the fitted cost
    grid = [i / 100 for i in range(1, 100)]
    lo_band = [lo for lo in grid if lo <= hi1 and fb_cost(lo, hi1) == fb_cost_after]
    hi_band = [hi for hi in grid if hi >= lo1 and fb_cost(lo1, hi) == fb_cost_after]
    cd = cost_derived_thresholds(costs)
    held_cd = sum(costs[(r["truth"], decide(drex[r["case_id"]]["p_flaky"], *cd))] for r in labels if not r["feedback_set"])
    # confident disagreements after learning: held-out cases still wrong by >= 20 eng-min.
    # What would it take to fix each one by moving thresholds? (refit on feedback + that correction)
    what_if = []
    for x in held:
        if x["cost_after"] >= 20:
            ideal = min(("QUARANTINE", "RETRY", "BLOCK"), key=lambda a: costs[(x["truth"], a)])
            ext = [(x["p_flaky"], x["truth"])]
            l2, h2, _ = fit_thresholds(probs_fb + [x["p_flaky"]], tr_fb + [x["truth"]], costs, lo1, hi1)
            # cheapest thresholds that DO fix it
            fixes = [(fb_cost(lo, hi, ext), lo, hi) for lo in [i / 10000 for i in range(1, 100)] + grid for hi in grid
                     if hi >= lo and decide(x["p_flaky"], lo, hi) != "BLOCK" or False]
            fixes = [f for f in fixes if decide(x["p_flaky"], f[1], f[2]) != x["after"]]
            best_fix = min(fixes) if fixes else None
            all_rows = [(drex[r["case_id"]]["p_flaky"], r["truth"]) for r in labels]
            def all_cost(lo, hi):
                return cost_of([decide(p, lo, hi) for p, _ in all_rows], [t for _, t in all_rows], costs)
            what_if.append({"case_id": x["case_id"], "p_flaky": x["p_flaky"], "truth": x["truth"], "decision": x["after"],
                            "cost": x["cost_after"], "ideal": ideal, "refit_thresholds": {"lo": l2, "hi": h2},
                            "cheapest_fix": best_fix and {"lo": best_fix[1], "hi": best_fix[2],
                                                          "all60_cost_with_fix": all_cost(best_fix[1], best_fix[2]),
                                                          "all60_cost_current": all_cost(lo1, hi1)}})
    out = {"mode": mode, "thresholds_before": {"lo": lo0, "hi": hi0}, "thresholds_after": {"lo": lo1, "hi": hi1},
           "cost_derived_if_calibrated": dict(zip(("lo", "hi"), cost_derived_thresholds(costs))),
           "feedback_n": len(fb), "corrections": corrections,
           "safe_band": {"lo": [min(lo_band), max(lo_band)] if lo_band else None, "hi": [min(hi_band), max(hi_band)] if hi_band else None},
           "heldout_cost_cost_derived": held_cd, "confident_disagreements": what_if,
           "heldout_n": len(held),
           "heldout_cost_before": sum(x["cost_before"] for x in held), "heldout_cost_after": sum(x["cost_after"] for x in held),
           "all_cost_before": sum(x["cost_before"] for x in replay), "all_cost_after": sum(x["cost_after"] for x in replay),
           "replay": replay}
    write_json(result_path("learn", mode), out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    load_env()
    mode = resolve_mode(a.mock, "NACE_API_KEY")
    o = run(mode)
    print(f"[learn] mode={mode} thresholds {o['thresholds_before']} -> {o['thresholds_after']} "
          f"(cost-optimal if calibrated: {o['cost_derived_if_calibrated']})")
    print(f"  {len(o['corrections'])} human corrections in feedback set of {o['feedback_n']}")
    print(f"  safe band on feedback set: {o['safe_band']}; held-out cost at cost-derived thresholds: {o['heldout_cost_cost_derived']}")
    for w in o["confident_disagreements"]:
        print(f"  confident disagreement {w['case_id']} p={w['p_flaky']:.4f} truth={w['truth']} -> {w['decision']}; refit={w['refit_thresholds']} cheapest_fix={w['cheapest_fix']}")
    print(f"  held-out cost (eng-minutes, n={o['heldout_n']}): {o['heldout_cost_before']:.0f} -> {o['heldout_cost_after']:.0f}")


if __name__ == "__main__":
    main()

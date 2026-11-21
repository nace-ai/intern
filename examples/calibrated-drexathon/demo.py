"""Stage demo: one ambiguous CI failure, end to end.

NDI extract -> Drex probability -> decision -> human correction -> thresholds shift -> better decision.

    python demo.py --offline          # replay cached results/*.json (cannot break on stage)
    python demo.py --offline --step   # press Enter between beats
    python demo.py --live             # re-call Drex live for the demo case (needs NACE_API_KEY)
    python demo.py --case case_007    # pick a specific case
"""
from __future__ import annotations

import argparse
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from drexflaky.common import LOGS, RESULTS, banner, has_key, load_costs, load_env, read_json, result_path
from policy import decide, reason

con = Console()


def beat(title, step):
    if step:
        con.input(f"[dim]  ⏎ {title}[/dim]")
    else:
        time.sleep(0.6)
    con.rule(f"[bold cyan]{title}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="replay cached results only (default)")
    ap.add_argument("--live", action="store_true", help="re-call Drex for the demo case")
    ap.add_argument("--mock", action="store_true", help="use mock cached results even if real ones exist")
    ap.add_argument("--case", default=None)
    ap.add_argument("--step", action="store_true")
    a = ap.parse_args()
    load_env()
    mode = "mock" if a.mock or not result_path("learn", "real").exists() else "real"
    ndi = {c["case_id"]: c for c in read_json(result_path("ndi", mode))["cases"]}
    drex_all = read_json(result_path("drex", mode)); drex = {c["case_id"]: c for c in drex_all["cases"]}
    learn = read_json(result_path("learn", mode))
    llm_mode = "real" if result_path("llm", "real").exists() and mode == "real" else "mock"
    llm = {c["case_id"]: c for c in read_json(result_path("llm", llm_mode))["cases"]}
    costs = load_costs()
    lo0, hi0 = learn["thresholds_before"]["lo"], learn["thresholds_before"]["hi"]
    lo1, hi1 = learn["thresholds_after"]["lo"], learn["thresholds_after"]["hi"]
    labels = {x["case_id"]: x for x in learn["replay"]}
    def rule(i):
        return "QUARANTINE" if ndi[i]["fields"].get("retry_passed") else "BLOCK"
    if a.case:
        cid = a.case
    elif learn["corrections"]:
        cid = max(learn["corrections"], key=lambda c: c["cost_before"] - c["cost_after"])["case_id"]
    else:
        # ambiguous real regression where the naive rule (retry passed => flaky) would quarantine and Drex does not
        traps = [x for x in learn["replay"] if x["ambiguous"] and x["truth"] == "real" and rule(x["case_id"]) == "QUARANTINE"
                 and x["after"] != "QUARANTINE"]
        cid = min(traps, key=lambda x: x["p_flaky"])["case_id"] if traps else learn["replay"][0]["case_id"]
    truth = next(x["truth"] for x in learn["replay"] if x["case_id"] == cid)

    color = "green" if mode == "real" else "bold red"
    con.print(Panel(f"[bold]Flaky-test quarantine loop[/bold]  ·  Drex decides, the cost table sets the thresholds\n"
                    f"[{color}]{banner(mode)}[/{color}]  ·  case {cid}", border_style=color))

    beat("1 · A CI test fails", a.step)
    log = (LOGS / f"{cid}.log").read_text().splitlines()
    con.print(Panel("\n".join(log[:40]), title=f"{cid}.log", border_style="dim"))

    beat("2 · Nace Document Intelligence extracts the facts", a.step)
    n = ndi[cid]
    t = Table(show_header=False, box=None)
    for k, v in n["fields"].items():
        t.add_row(f"[dim]{k}[/dim]", str(v))
    con.print(t)
    con.print(f"[dim]path used: {n['path']}" + (f" ({n.get('fallback_reason')})" if n.get("fallback_reason") else "") + "[/dim]")

    beat("3 · Drex answers one calibrated question", a.step)
    p = drex[cid]["p_flaky"]; src = "cached"
    if a.live:
        if not has_key("NACE_API_KEY"):
            con.print("[yellow]--live requested but NACE_API_KEY not set; using cached value[/yellow]")
        else:
            import requests
            from drex_decide import BASE, CLIENT_ID, PREFERRED_MODEL, build_state, call_drex
            s = requests.Session()
            s.headers.update({"Authorization": f"Bearer {os.environ['NACE_API_KEY'].strip()}", "X-Nace-Client": CLIENT_ID})
            try:
                out = call_drex(s, PREFERRED_MODEL, build_state(n["fields"], "\n".join(log)))
                p, src = out["p_flaky"], f"LIVE {out['model']} {out['latency_ms']:.0f}ms req={out['request_id']}"
            except Exception as e:  # noqa: BLE001
                con.print(f"[yellow]live call failed ({type(e).__name__}); using cached value[/yellow]")
    con.print(f"  Q: [italic]Is this failure caused by test flakiness rather than a real code regression?[/italic]")
    con.print(f"  [bold]P(flaky) = {p:.3f}[/bold]   [dim]model={drex[cid].get('model')} · {src} · "
              f"latency={drex[cid].get('latency_ms')}ms[/dim]")
    con.print(f"  [dim]Rule baseline (retry passed => flaky) says: {rule(cid)}[/dim]")
    if llm_mode == "real":
        con.print(f"  [dim]LLM baseline on the same log said: {llm[cid]['action']} ({llm[cid]['label']})[/dim]")
    elif mode == "mock":
        con.print(f"  [dim]LLM baseline on the same log said: {llm[cid]['action']} ({llm[cid]['label']}) [MOCK][/dim]")
    else:
        con.print("  [dim]LLM baseline (DeepInfra): pending[/dim]")

    beat("4 · Policy decides (thresholds from day 0)", a.step)
    d0 = decide(p, lo0, hi0)
    con.print(f"  thresholds lo={lo0:.2f} hi={hi0:.2f}  →  [bold]{d0}[/bold]   ({reason(p, lo0, hi0)})")

    beat("5 · A human reviews the outcome", a.step)
    c0 = costs[(truth, d0)]
    cr = costs[(truth, rule(cid))]
    con.print(f"  Human label: [bold]{truth.upper()}[/bold].  Drex+policy cost = [bold]{c0:.0f}[/bold] eng-min · "
              f"rule baseline ({rule(cid)}) cost = [bold]{cr:.0f}[/bold] eng-min")

    beat(f"6 · Feedback round: {learn['feedback_n']} labelled failures recalibrate the thresholds", a.step)
    if learn["corrections"]:
        tt = Table("case", "P(flaky)", "was", "human", "now", "cost")
        for c in learn["corrections"]:
            tt.add_row(c["case_id"], f"{c['p_flaky']:.3f}", c["decision_before"], c["human_says"], c["decision_after"],
                       f"{c['cost_before']:.0f} → {c['cost_after']:.0f}")
        con.print(tt)
    else:
        con.print(f"  {learn['feedback_n']} human labels, [bold]0[/bold] decisions they would change.")
    band = learn.get("safe_band") or {}
    con.print(f"  lo {lo0:.2f} → [bold]{lo1:.2f}[/bold]   hi {hi0:.2f} → [bold]{hi1:.2f}[/bold]"
              + (f"   safe band: lo ∈ {band.get('lo')}, hi ∈ {band.get('hi')}" if band else ""))
    con.print(f"  [dim]cost table alone (if perfectly calibrated) would pick lo={learn['cost_derived_if_calibrated']['lo']:.2f}, "
              f"hi={learn['cost_derived_if_calibrated']['hi']:.2f}; held-out cost there = {learn.get('heldout_cost_cost_derived', 'n/a')}"
              f" vs {learn['heldout_cost_after']:.0f}[/dim]")

    d1 = decide(p, lo1, hi1)
    if d1 != d0:
        beat("7 · Replay the logged probability with the new thresholds", a.step)
        con.print(f"  same P(flaky)={p:.3f}  →  [bold green]{d1}[/bold green]   ({reason(p, lo1, hi1)})   cost {c0:.0f} → {costs[(truth, d1)]:.0f} eng-min")
    dis = learn.get("confident_disagreements") or []
    if dis:
        w = dis[0]
        beat(f"7 · A human overrides Drex on {w['case_id']}: should the thresholds move?", a.step)
        con.print(f"  {w['case_id']}: P(flaky)={w['p_flaky']:.4f} → {w['decision']}; human says [bold]{w['truth'].upper()}[/bold] "
                  f"(cost {w['cost']:.0f} eng-min)")
        con.print(f"  refit with this correction → lo={w['refit_thresholds']['lo']:.2f}, hi={w['refit_thresholds']['hi']:.2f} "
                  f"([bold]unchanged[/bold])")
        f = w.get("cheapest_fix")
        if f:
            con.print(f"  forcing a fix needs lo={f['lo']:.4f}: total cost on all 60 failures {f['all60_cost_current']:.0f} → "
                      f"[bold red]{f['all60_cost_with_fix']:.0f}[/bold red] eng-min. The loop keeps the thresholds and routes "
                      f"this pattern to the evidence backlog (state/question), not the thresholds.")
    con.print(f"  across all 60 logged failures: {learn['all_cost_before']:.0f} → {learn['all_cost_after']:.0f} eng-min; "
              f"held-out only: {learn['heldout_cost_before']:.0f} → {learn['heldout_cost_after']:.0f}")
    md = RESULTS / f"metrics_{mode}.md"
    if md.exists():
        con.rule("[bold cyan]Scoreboard")
        from rich.markdown import Markdown
        con.print(Markdown(md.read_text()))
    con.print(Panel(f"[{color}]{banner(mode)}[/{color}]", border_style=color))


if __name__ == "__main__":
    main()

"""Render stage claims from the four complete held-out metrics files only."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from billproof.config import DATA_DIR, PKG_ROOT

START = '<!-- BILLPROOF_MEASURED_START -->'
END = '<!-- BILLPROOF_MEASURED_END -->'


def main():
    reports = [json.loads(p.read_text()) for p in (DATA_DIR / 'metrics').glob('bench_*.json')
               if p.name != 'bench_spend.json']
    reports = [r for r in reports if r.get('population', {}).get('headline_eligible')]
    expected = {(m, b) for m in ('drex-max-v1.1', 'drex-max-finance') for b in ('synthetic',)}
    by = {(r['model'], r['bench']): r for r in reports}
    if not expected <= by.keys():
        raise RuntimeError(f'No stage claims until all four test populations complete: missing {expected - by.keys()}')

    def v(r, metric):
        return r['metrics'][metric]['value']

    synth = [by[(m, 'synthetic')] for m in ('drex-max-v1.1', 'drex-max-finance')]
    best = min(synth, key=lambda r: v(r, 'cost_ratio_vs_frontier.drex'))
    ratio = v(best, 'cost_ratio_vs_frontier.drex')
    cost = v(best, 'cost_per_1000_cases_usd.drex')
    acc = best['metrics']['question_accuracy.drex']
    lo, hi = acc['ci95']
    fastest = max(synth, key=lambda r: v(r, 'speed_ratio_vs_frontier.drex'))
    claims = [
        f"**{1 / ratio:,.1f}× lower estimated inference cost** with {best['model']} on synthetic test: ${cost:.3f}/1,000 bills, n=115 paired bills. Caveat: configured token-price estimates, excluding human review, document ingestion and infrastructure; source `{best['run_id']}`.",
        f"**{acc['value']:.2%} question accuracy** with {best['model']} on synthetic test (95% Wilson CI {lo:.2%}–{hi:.2%}, n={acc['n']} questions across 115 bills); frontier {v(best, 'question_accuracy.frontier'):.2%}. Caveat: rule-derived synthetic documents/labels, questions clustered within bills; source `{best['run_id']}`.",
        f"**{v(fastest, 'speed_ratio_vs_frontier.drex'):.1f}× faster observed median client latency** with {fastest['model']} on synthetic test, n=115 paired bills ({v(fastest, 'latency_p50_ms.drex'):.0f} ms Drex). Caveat: frontier timing includes router pacing/retries; deployment-specific, not intrinsic model speed; source `{fastest['run_id']}`.",
    ]
    eligible = []
    for r in by.values():
        t = r.get('threshold', {})
        test = t.get('test') or {}
        error = (test.get('error_automated') or {}).get('value')
        coverage = (test.get('automation_rate') or {}).get('value')
        if t.get('achieved_on_dev') and error is not None and error <= .01 and coverage is not None:
            eligible.append((coverage, r))
    if eligible:
        coverage, r = max(eligible, key=lambda x: x[0])
        t = r['threshold']['test']
        claims.append(f"**{coverage:.1%} automated at {t['error_automated']['value']:.2%} observed error** for {r['model']} on {r['bench']} test (n={t['error_automated']['n']} automated of {t['automation_rate']['n']} questions). Caveat: cutoff selected on dev for ≤1% empirical error, then fixed on test; finite-sample observation, not a guaranteed safety rate; source `{r['run_id']}`.")
    else:
        claims.append('**No ≤1% error automation claim established on held-out test.** Caveat: dev-selected policies must meet the error cap on held-out evaluation before this headline is used.')
    lines = [START, '# Measured BillProof benchmark claims', '', 'Full held-out synthetic test: 115 bills. Hand-picked demo cases are examples. Structured IMR is dropped: structured fields only, near chance, superseded by Persius.', '']
    lines += [f'{i}. {claim}' for i, claim in enumerate(claims, 1)]
    lines += ['', '| Model | Test bench | Cases | Drex accuracy | Frontier accuracy | Hybrid strict accuracy | Hybrid coverage |', '|---|---|---:|---:|---:|---:|---:|']
    for m, b in sorted(expected):
        r = by[m, b]
        lines.append(f"| {m} | {b} | {r['n_cases']} | {v(r, 'question_accuracy.drex'):.2%} | {v(r, 'question_accuracy.frontier'):.2%} | {v(r, 'question_accuracy_all_scored.hybrid'):.2%} | {v(r, 'coverage.hybrid'):.2%} |")
    lines += ['', 'Old IMR: structured fields only, near chance, superseded by Persius. Its recorded rows are retained for audit and excluded from stage claims. Hybrid strict accuracy treats a human handoff as unanswered. Conditional automated accuracy must always appear with coverage.', '', 'Reproduce: `PYTHONPATH=src .venv/bin/python -m billproof.bench.export_metrics` then `PYTHONPATH=src .venv/bin/python -m billproof.bench.stage_numbers`.', 'Metric source: `data/metrics/bench_<model>_<bench>.json`; confidence intervals are question-level Wilson intervals and do not adjust for within-bill clustering.', END, '']
    path = PKG_ROOT / 'docs/STAGE_NUMBERS.md'
    old = path.read_text() if path.exists() else ''
    if START in old:
        old = old.split(END, 1)[1].lstrip()
    path.write_text('\n'.join(lines) + '\n' + old)
    print(path)


if __name__ == '__main__':
    main()

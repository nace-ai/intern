#!/usr/bin/env python3
"""Record the PA demo chain (live Nace + router calls). Resumable per case
and per step via data/pa_demo/<slug>/run.json; re-run finishes what is missing.

Usage (background, logs to out/logs):
  PYTHONPATH=src nohup .venv/bin/python scripts/pa_demo_record.py \
      >> out/logs/pa_demo_record.log 2>&1 &

Options: --case PA-D2 · --steps ground,search,drex,ablate,gpt · --assemble-only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.pa_demo.chain import assemble, record_case
from billproof.pa_demo.showcase import SHOWCASE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None)
    ap.add_argument("--steps", default="ground,search,drex,ablate,gpt")
    ap.add_argument("--browser", action="store_true",
                    help="record the Prime Therapeutics browser run first")
    ap.add_argument("--assemble-only", action="store_true",
                    help="rebuild evidence.json/check.png from run.json (no live calls)")
    args = ap.parse_args()
    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip())
    if args.browser:
        from billproof.pa_demo import browser as pa_browser
        rec = pa_browser.record()
        ver = rec.get("verification") or {}
        print(f"[browser] status={rec.get('status')} cost=${rec.get('cost_usd')} "
              f"quotes={[ (q['field'], q['verbatim']) for q in ver.get('quotes', []) ]} "
              f"links={[ (d['field'], d['serves_pdf']) for d in ver.get('doc_links', []) ]}",
              flush=True)
    slugs = [args.case] if args.case else list(SHOWCASE)
    total_cost = 0.0
    for slug in slugs:
        try:
            if args.assemble_only:
                res = assemble(slug)
            else:
                t0 = time.monotonic()
                res = record_case(slug, steps=steps)
                print(f"[record] {slug} done in {time.monotonic()-t0:.0f}s "
                      f"decision={res['decision']} gold={res['gold']}", flush=True)
            mark = "OK" if res["decision"] == res["gold"] else "MISMATCH"
            import json as _j
            ev = _j.loads((Path(__file__).resolve().parents[1]
                           / "data" / "pa_demo" / slug / "evidence.json").read_text())
            total_cost += ev.get("total_cost_usd") or 0
            print(f"[assemble] {slug} decision={res['decision']} gold={res['gold']} [{mark}] "
                  f"cost=${ev.get('total_cost_usd')}", flush=True)
        except Exception as exc:
            print(f"[record] {slug} ERROR: {exc}", flush=True)
    print(f"[record] ALL DONE total_cost=${total_cost:.3f}", flush=True)


if __name__ == "__main__":
    main()

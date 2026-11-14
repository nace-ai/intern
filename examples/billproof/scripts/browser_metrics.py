"""Write data/metrics/browser_*.json from recorded runs (no network).

    .venv/bin/python scripts/browser_metrics.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billproof.browser.runs import RECORDED_DIR  # noqa: E402
from billproof.config import DATA_DIR  # noqa: E402

OUT = DATA_DIR / "metrics"


def _write(name: str, value, n: int, source: str, **extra) -> None:
    rec = {"value": value, "n": n, "source_run": source, "simulated": False,
           "computed_at": datetime.now(timezone.utc).isoformat(), **extra}
    (OUT / f"{name}.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(f"{name}: {value} (n={n})")


def main() -> None:
    runs = json.loads((RECORDED_DIR / "explore_runs.json").read_text())["runs"]
    cases = [json.loads(p.read_text()) for p in sorted(RECORDED_DIR.glob("SYN-*.json"))]
    src = "browser_explore_20261114+" + "+".join(c["case_id"] for c in cases)
    ok = [r for r in runs if r["status"] == "succeeded"]
    OUT.mkdir(parents=True, exist_ok=True)
    _write("browser_success_rate", round(len(ok) / len(runs), 3), len(runs), src,
           by_status={s: sum(r["status"] == s for r in runs) for s in sorted({r["status"] for r in runs})},
           note="2 cancelled runs were 'funded browser budget exhausted' (account side); 1 failed on a PDF start URL")
    _write("browser_run_seconds", round(sum(r["wall_seconds"] for r in ok) / len(ok), 1), len(ok), src,
           note="start to final status, succeeded runs, client wall clock")
    _write("browser_cost_usd_per_run", round(sum(r["cost_usd"] for r in ok) / len(ok), 4), len(ok), src)
    _write("browser_answer_correct_rate", round(sum(r["correct"] for r in ok) / len(ok), 3), len(ok), src,
           note="structured answer checked against the live page (curl or code)")
    quotes = [q for c in cases for q in (c.get("verification") or {}).get("quotes", [])]
    _write("browser_quotes_verbatim_rate", round(sum(q["verbatim"] for q in quotes) / max(1, len(quotes)), 3),
           len(quotes), src, note="each *_quote field found word for word in the page text BillProof fetched itself")
    _write("browser_screenshots_per_run", round(sum(r["screenshots"] for r in runs) / len(runs), 2), len(runs), src,
           note="artifacts.items was empty on every run, even with artifacts.screenshots=true")
    _write("browser_spend_usd", round(sum(r["cost_usd"] or 0 for r in runs), 4), len(runs), src)


if __name__ == "__main__":
    main()

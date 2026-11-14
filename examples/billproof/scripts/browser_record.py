"""Record the browser run for a case (live), or import a finished run by id (no new charge).

    .venv/bin/python scripts/browser_record.py SYN-D08                 # live run, ~30 s, ~$0.01
    .venv/bin/python scripts/browser_record.py SYN-D08 --run-id brun_… # re-read an existing run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billproof.browser import runs  # noqa: E402
from billproof.config import settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--run-id")
    args = ap.parse_args()
    task_id = runs.CASE_TASKS[args.case_id]
    if args.run_id:
        s = settings()
        h = {"Authorization": f"Bearer {s.nace_api_key}"}
        run = httpx.get(f"{s.nace_base_url}/v1/browser/runs/{args.run_id}", headers=h, timeout=30).json()
        arts = httpx.get(f"{s.nace_base_url}/v1/browser/runs/{args.run_id}/artifacts", headers=h, timeout=30).json()
        secs = None
        if run.get("started_at") and run.get("finished_at"):
            from datetime import datetime
            secs = round((datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00"))
                          - datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))).total_seconds(), 1)
        orig = runs.start_and_wait
        runs.start_and_wait = lambda spec, client=None: (run, arts.get("items") or [], secs)
        try:
            rec = runs.run_task(task_id, args.case_id)
        finally:
            runs.start_and_wait = orig
    else:
        rec = runs.run_task(task_id, args.case_id)
    print(json.dumps({k: rec[k] for k in ("run_id", "status", "outcome", "seconds", "cost_usd", "final_url")}, indent=1))
    print(json.dumps(rec["answer"], indent=1))
    print("verified:", [(q["field"], q["verbatim"]) for q in rec["verification"]["quotes"]])


if __name__ == "__main__":
    main()

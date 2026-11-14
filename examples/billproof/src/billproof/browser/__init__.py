"""Browser runs: read a public page for the case and return a checked, structured answer.

Entry point the demo pipeline calls:
    run_case(case_or_id, live=False) -> dict   # recorded run (answer, steps, verification)
"""
from __future__ import annotations

from billproof.browser.runs import CASE_TASKS, TASKS, policy_for_chain, recorded_run, run_task

simulated = False  # recordings are measured live runs, replayed

__all__ = ["run_case", "recorded_run", "run_task", "policy_for_chain", "TASKS", "CASE_TASKS"]


def run_case(case, live: bool = False) -> dict:
    case_id = case if isinstance(case, str) else getattr(case, "case_id", None)
    if not isinstance(case_id, str):
        raise TypeError("run_case takes a Case or a case_id")
    if not live:
        rec = recorded_run(case_id)
        return {**rec, "replayed": True} if rec else {"case_id": case_id, "status": "not_recorded", "replayed": False}
    task_id = CASE_TASKS.get(case_id)
    if task_id is None:
        return {"case_id": case_id, "status": "no_task", "replayed": False}
    return {**run_task(task_id, case_id, record=False), "replayed": False}

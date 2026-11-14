"""Ask your documents: Drex Search over a per-case workspace, wired into Drex and the letter.

Entry points the demo pipeline calls:
    ask_case(case_or_id, live=False) -> dict   # recorded NDI→Search→Drex→letter chain
    ask(case_id, question, mode="fact", schema=None, live=False) -> dict
"""
from __future__ import annotations

from billproof.search.ask import DEMO_QUESTIONS, ask
from billproof.search.chain import recorded_chain, run_chain

simulated = False  # recordings are measured live calls, replayed

__all__ = ["ask", "ask_case", "recorded_chain", "run_chain", "DEMO_QUESTIONS"]


def ask_case(case, live: bool = False) -> dict:
    case_id = case if isinstance(case, str) else getattr(case, "case_id", None)
    if not isinstance(case_id, str):
        raise TypeError("ask_case takes a Case or a case_id")
    rec = recorded_chain(case_id)
    if rec is not None and not live:
        return {**rec, "replayed": True}
    if not live:
        return {"case_id": case_id, "status": "not_recorded", "replayed": False}
    return {**run_chain(case_id), "replayed": False}

"""Typed questions for the PA benchmark (Drex and frontier lanes).

Per requested fact: a 3-way choice — documented_satisfied /
documented_not_satisfied / not_documented — matching the dataset's
true/false/null fact semantics. Plus two scope nouls: payer match and
service-date inside the policy window. Code combines answers into criteria,
decision and action; models never compute the decision themselves.
"""

from __future__ import annotations

from billproof.bench.pa.cases import PaCase
from billproof.schema import Question

FACT_OPTIONS = ["documented_satisfied", "documented_not_satisfied", "not_documented"]
PAYER_Q = "scope::payer_match"
DATE_Q = "scope::date_in_window"

_FACT_INSTRUCTIONS = (
    "You are checking one clause of a prior-authorization criteria list against a synthetic "
    "documentation packet. Clause: \"{clause}\"\n"
    "Answer:\n"
    "- documented_satisfied: a current record explicitly documents this clause as satisfied.\n"
    "- documented_not_satisfied: a current record explicitly documents this clause as NOT satisfied.\n"
    "- not_documented: no current record addresses this clause. Missing documentation is never "
    "documented_not_satisfied.\n"
    "Judge only the named clause; do not infer clinical facts beyond the record texts."
)


def _clause_for(case: PaCase, fact: str) -> str:
    for item in case.policy.get("criteria_list") or []:
        if item.get("fact") == fact:
            return item.get("text") or fact
    definition = case.fact_definitions.get(fact, "")
    return definition or fact


def build_questions(case: PaCase, *, include_scope: bool = True) -> list[Question]:
    questions: list[Question] = []
    for fact in case.requested_facts:
        name = f"fact::{fact}"
        questions.append(Question(
            name=name,
            type="choice",
            instructions=_FACT_INSTRUCTIONS.format(clause=_clause_for(case, fact)),
            criteria={
                "documented_satisfied": "a current record explicitly documents this clause as satisfied",
                "documented_not_satisfied": "a current record explicitly documents this clause as NOT satisfied",
                "not_documented": "no current record addresses this clause",
            },
            family="pa_fact",
            evidence=[],
        ))
    if include_scope:
        policy_payer = case.policy.get("payer")
        request_payer = case.request.get("payer")
        window = case.policy.get("exercise_applicability") or {}
        questions.append(Question(
            name=PAYER_Q,
            type="noul",
            instructions=(
                f"The criteria list is issued by \"{policy_payer}\". The prior-authorization "
                f"request names payer \"{request_payer}\". Answer yes only if these are the "
                "same payer."
            ),
            family="pa_scope",
        ))
        questions.append(Question(
            name=DATE_Q,
            type="noul",
            instructions=(
                f"The criteria list applies only to service dates from "
                f"{window.get('from')} through {window.get('through')} inclusive. The request "
                f"service_date is {case.request.get('service_date')}. Is the service date "
                "inside that window, endpoints included?"
            ),
            family="pa_scope",
        ))
    return questions


def scope_from_answers(answers: dict[str, str]) -> str:
    """answers maps question name -> raw choice string."""
    payer = (answers.get(PAYER_Q) or "").strip().lower()
    date = (answers.get(DATE_Q) or "").strip().lower()
    if payer == "no":
        return "wrong_payer"
    if date == "no":
        return "wrong_date"
    # yes/yes or missing answers: emit the checklist decision; scope scores wrong if gold differs.
    return "applicable"

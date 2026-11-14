"""Exact Drex question wording and bounded choices for each agent (plan B3).

Every primary question is a Drex `choice`; secondary questions asked in the same
call are recorded as auxiliary decisions in the trace. Wording deliberately
avoids accident likelihood or severity: priority is for human review triage.
"""

from __future__ import annotations

from agents.drex_client import QuestionSpec
from agents.taxonomy import PATTERN_FAMILIES

NEXT_ACTIONS: dict[str, str] = {
    "investigate_cluster": (
        "A human analyst should review the matched historical incidents together with this one as a possible "
        "recurring pattern. Appropriate only when similar historical incidents exist and the evidence supports "
        "the pattern claim."
    ),
    "request_context": (
        "The report lacks details needed for triage (for example location, phase of flight, what happened next); "
        "request additional context before deciding."
    ),
    "specialist_review": (
        "Refer the case to a human specialist because the findings conflict, the evidence is insufficient for a "
        "confident claim, or the event needs expert interpretation."
    ),
    "no_pattern_identified": "No specific precursor pattern is identified; record the report without pattern follow-up.",
}

REVIEW_PRIORITIES: dict[str, str] = {
    "routine": "Human review can follow the normal analyst queue.",
    "elevated": "Human review should happen ahead of routine reports because of notable safety-relevant circumstances.",
    "urgent": "Human review should happen as soon as possible because the narrative describes a serious, time-sensitive safety concern.",
}

EVIDENCE_STANCES: dict[str, str] = {
    "supports": (
        "The located guidance and incident narrative materially support the claimed family as the leading "
        "precursor family, even after comparing the alternative guidance topics."
    ),
    "conflicts": (
        "The narrative and located guidance point more strongly to a different leading family, or contradict "
        "the claimed family. A merely secondary mention of the claimed family is not enough to support it."
    ),
    "unresolved": "The available passages do not establish which family leads, or they are not specific to this incident.",
}

COORDINATOR_DECISIONS: dict[str, str] = {
    "accept": "The recommended next step is consistent with the pattern claim and the evidence; accept it.",
    "amend": "The findings are settled enough, but the recommended next step should be changed.",
    "reevaluate": (
        "The pattern claim and the evidence materially disagree, or crucial evidence is missing; return the case to "
        "the Pattern and Evidence agents for one targeted second pass."
    ),
}

CONFLICT_FOCUS: dict[str, str] = {
    "pattern_family_claim": "Whether the claimed precursor family is the right one for this narrative.",
    "guidance_support": "Whether the cited guidance actually supports the claim.",
    "historical_match": "Whether the historical candidate incidents really match this incident.",
    "no_material_conflict": "There is no material conflict between the findings.",
}


# ---------------------------------------------------------------- Pattern Agent

def pattern_questions(candidate_ids: list[str], round_number: int, challenged_family: str | None) -> dict[str, QuestionSpec]:
    if round_number == 1:
        primary = (
            "Which precursor family best matches the evidence in the current incident narrative? "
            "Choose insufficient_evidence if the narrative does not support any family."
        )
    else:
        primary = (
            f"The Evidence Agent challenged the earlier claim '{challenged_family}'. Re-examine the narrative, "
            "the historical candidates and the cited guidance passages in the state. Which precursor family best "
            "matches the evidence in the current incident narrative? Choose insufficient_evidence if the narrative "
            "does not support any family."
        )
    questions = {"pattern_family": QuestionSpec("choice", primary, dict(PATTERN_FAMILIES))}
    if candidate_ids:
        questions["historical_signal"] = QuestionSpec(
            "noul",
            "Taken together, do the historical candidate incidents and the current incident share enough "
            "circumstances to suggest a recurring historical pattern worth reviewing as a cluster?",
        )
    for index, case_id in enumerate(candidate_ids):
        questions[f"candidate_{index}_match"] = QuestionSpec(
            "noul",
            f"Does historical candidate {case_id} describe the same kind of precursor circumstances as the current "
            "incident (not merely shared words)?",
        )
    return questions


# --------------------------------------------------------------- Evidence Agent

def evidence_questions(passage_labels: list[str], claimed_family: str) -> dict[str, QuestionSpec]:
    questions = {
        "evidence_stance": QuestionSpec(
            "choice",
            f"Compare the incident narrative with the located guidance for the claimed and alternative topics. "
            f"Does the evidence support the Pattern Agent's claim that '{claimed_family}' is the leading "
            f"precursor family, point more strongly to another family, or leave the comparison unresolved?",
            dict(EVIDENCE_STANCES),
        ),
        "source_missing": QuestionSpec(
            "noul",
            "Is a crucial guidance source missing, meaning a fair assessment of this claim needs guidance on a "
            "topic that none of the supplied passages address?",
        ),
    }
    for index, label in enumerate(passage_labels):
        questions[f"passage_{index}_relevant"] = QuestionSpec(
            "noul",
            f"Is guidance passage {label} relevant to evaluating the pattern claim for this incident?",
        )
    return questions


# ---------------------------------------------------------- Investigation Agent

def investigation_questions() -> dict[str, QuestionSpec]:
    return {
        "next_action": QuestionSpec(
            "choice",
            "Given the pattern claim, the evidence assessment and their uncertainty, which next step should a human "
            "safety analyst take for this report?",
            dict(NEXT_ACTIONS),
        ),
        "review_priority": QuestionSpec(
            "choice",
            "What human review priority fits this report for analyst triage? This is review priority only, not a "
            "prediction of accident severity or likelihood.",
            dict(REVIEW_PRIORITIES),
        ),
    }


# ------------------------------------------------------------ Coordinator Agent

def coordinator_questions(second_round_available: bool) -> dict[str, QuestionSpec]:
    cap_note = (
        "" if second_round_available else " A second pass has already been used; reevaluate now means the case is unresolved."
    )
    return {
        "coordinator_decision": QuestionSpec(
            "choice",
            "Review the Pattern, Evidence and Investigation findings in the state. Should the investigation "
            "recommendation be accepted, amended, or returned for targeted re-evaluation?" + cap_note,
            dict(COORDINATOR_DECISIONS),
        ),
        "amended_next_action": QuestionSpec(
            "choice",
            "If the recommendation were amended, which next step would best fit the combined findings?",
            dict(NEXT_ACTIONS),
        ),
        "conflict_focus": QuestionSpec(
            "choice",
            "Which evidence conflict most needs resolution?",
            dict(CONFLICT_FOCUS),
        ),
    }

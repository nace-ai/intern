"""Explicit policy rules (plan section 4 "Decision policy" and B3 "policy layer").

These functions never call Drex and never invent probabilities. They interpret
Drex outputs with fixed, documented thresholds, and every override they make is
reported in plain language so the UI can show it.
"""

from __future__ import annotations

from dataclasses import dataclass

# A noul answer counts as "yes" above this threshold (Drex quickstart suggests 0.5 for plain yes/no).
NOUL_YES_THRESHOLD = 0.5


def is_yes(probability: float) -> bool:
    return probability > NOUL_YES_THRESHOLD


def pattern_uncertainty(selected_family: str) -> str:
    """A pattern claim is never self-supporting: it stays unresolved until the Evidence Agent reviews it."""
    return "insufficient" if selected_family == "insufficient_evidence" else "unresolved"


def evidence_uncertainty(stance: str, cited_ref_count: int) -> str:
    """P1 citation integrity: support without any located citation is not verified support."""
    if cited_ref_count == 0:
        return "insufficient"
    return {"supports": "supported", "conflicts": "conflicted", "unresolved": "unresolved"}[stance]


def needs_second_round(coordinator_choice: str, evidence_status: str, round_number: int) -> tuple[bool, str | None]:
    """P2: a material disagreement triggers one targeted second pass (capped at one extra round)."""
    if round_number != 1:
        return False, None
    if coordinator_choice == "reevaluate":
        return True, "Coordinator (Drex) selected reevaluate."
    if evidence_status == "conflicted":
        return True, (
            "Policy P2: the Evidence Agent's assessment conflicts with the pattern claim, which requires one "
            f"targeted second pass even though the Coordinator selected '{coordinator_choice}'."
        )
    return False, None


@dataclass
class FinalDecision:
    action: str
    notes: list[str]


def final_action(
    coordinator_choice: str,
    investigation_action: str,
    amended_action: str,
    evidence_status: str,
    rounds_run: int,
    supporting_incident_count: int,
) -> FinalDecision:
    notes: list[str] = []
    if coordinator_choice == "accept":
        action = investigation_action
    elif coordinator_choice == "amend":
        action = amended_action
        notes.append(f"Coordinator amended the recommendation from '{investigation_action}' to '{amended_action}'.")
    else:  # reevaluate after the cap
        action = "specialist_review"
        notes.append(
            "Policy P3: the Coordinator still requested re-evaluation after the capped second round, so the case "
            "is routed to human specialist review."
        )

    if rounds_run == 2 and evidence_status == "conflicted" and action != "specialist_review":
        notes.append(
            f"Policy P3: evidence still conflicts with the pattern claim after the second round; '{action}' was "
            "replaced by specialist_review."
        )
        action = "specialist_review"

    if action == "investigate_cluster" and supporting_incident_count == 0:
        notes.append(
            "Policy P4: no historical candidate was judged a match, so a cluster review cannot be recommended; "
            "routed to specialist_review."
        )
        action = "specialist_review"
    return FinalDecision(action=action, notes=notes)

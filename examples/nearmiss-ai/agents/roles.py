"""The four Drex-driven agents (plan section 4).

Each agent: builds a JSON state from the case plus earlier agents' structured
findings (SharedState), asks its bounded questions in ONE Drex call, and turns
the real answers into an AgentMessage. Application logic only assembles text
from returned values; it never chooses an option on an agent's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents import policy
from agents.drex_client import Decider, DrexOutcome, QuestionSpec
from agents.questions import (
    COORDINATOR_DECISIONS,
    EVIDENCE_STANCES,
    NEXT_ACTIONS,
    coordinator_questions,
    evidence_questions,
    investigation_questions,
    pattern_questions,
)
from agents.taxonomy import GUIDANCE_TOPICS, PATTERN_FAMILIES
from evidence.store import EvidenceStore, Passage
from service.contracts import AgentMessage, AuxiliaryDecision, CaseContext, EvidenceRef, PatternClaim

MAX_CANDIDATES = 5
MAX_PASSAGES = 4
NARRATIVE_CHAR_CAP = 12000
CANDIDATE_CHAR_CAP = 3000

TEAM_BRIEF = (
    "You are one agent in a team that triages NASA ASRS aviation safety reports for a human safety analyst. "
    "ASRS reports are voluntary, de-identified and not verified by NASA. The team recommends what a human analyst "
    "should investigate next; it does not predict accidents or make operational decisions."
)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [truncated for length]"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


@dataclass
class EvidenceAssessment:
    stance: str
    stance_probabilities: dict[str, float]
    uncertainty_status: str
    refs: list[EvidenceRef]
    source_missing_probability: float
    passages_reviewed: int


@dataclass
class SharedState:
    """The common investigation state each agent reads from and writes to."""

    case: CaseContext
    messages: list[AgentMessage] = field(default_factory=list)
    pattern_claim: PatternClaim | None = None
    pattern_probabilities: dict[str, float] = field(default_factory=dict)
    historical_signal: float | None = None
    evidence: EvidenceAssessment | None = None
    next_action: str | None = None
    next_action_probabilities: dict[str, float] = field(default_factory=dict)
    review_priority: str | None = None
    coordinator_choice: str | None = None
    amended_action: str | None = None
    conflict_focus: str | None = None
    models_seen: list[str] = field(default_factory=list)

    @property
    def candidates(self):
        return self.case.candidate_incidents[:MAX_CANDIDATES]

    def incident_block(self) -> dict[str, Any]:
        return {
            "case_id": self.case.case_id,
            "narrative": _cap(self.case.narrative, NARRATIVE_CHAR_CAP),
            "metadata": self.case.metadata,
        }

    def record(self, outcome: DrexOutcome, message: AgentMessage) -> AgentMessage:
        if outcome.model and outcome.model not in self.models_seen:
            self.models_seen.append(outcome.model)
        self.messages.append(message)
        return message


def _aux_choice(qid: str, spec: QuestionSpec, outcome: DrexOutcome) -> AuxiliaryDecision:
    answer = outcome.choices[qid]
    return AuxiliaryDecision(
        question_id=qid,
        question=spec.instructions,
        type="choice",
        selected_option=answer.selected,
        probabilities=answer.probabilities,
    )


def _aux_noul(qid: str, spec: QuestionSpec, outcome: DrexOutcome) -> AuxiliaryDecision:
    return AuxiliaryDecision(question_id=qid, question=spec.instructions, type="noul", yes_probability=outcome.nouls[qid])


def _pct(value: float) -> str:
    return f"{value:.2f}"


# =========================================================== Pattern Agent


def run_pattern(decider: Decider, shared: SharedState, round_number: int, challenge: dict[str, Any] | None = None) -> AgentMessage:
    candidates = shared.candidates
    ids = [c.case_id for c in candidates]
    previous_family = shared.pattern_claim.pattern_family if shared.pattern_claim else None

    state: dict[str, Any] = {
        "team_brief": TEAM_BRIEF,
        "your_role": "Pattern Agent: identify which precursor family the current incident fits and whether "
        "the historical candidates suggest a recurring pattern. Do not assert causes or accident likelihood.",
        "current_incident": shared.incident_block(),
        "historical_candidates": [
            {"case_id": c.case_id, "narrative": _cap(c.narrative, CANDIDATE_CHAR_CAP), "retrieval_score": c.retrieval_score}
            for c in candidates
        ],
        "retrieval_method_note": shared.case.retrieval_method
        + " (retrieval scores rank text matches only; they are not safety risk scores).",
    }
    if challenge:
        state["evidence_challenge_from_previous_round"] = challenge

    questions = pattern_questions(ids, round_number, previous_family)
    outcome = decider.decide(state, questions)

    family = outcome.choices["pattern_family"]
    supporting = _dedupe([ids[i] for i in range(len(ids)) if policy.is_yes(outcome.nouls[f"candidate_{i}_match"])])
    historical_signal = outcome.nouls.get("historical_signal")
    aux = [_aux_noul(q, questions[q], outcome) for q in questions if q != "pattern_family"]

    shared.pattern_claim = PatternClaim(pattern_family=family.selected, supporting_incident_ids=supporting)
    shared.pattern_probabilities = family.probabilities
    shared.historical_signal = historical_signal

    if round_number == 1:
        input_summary = f"Incident {shared.case.case_id} narrative ({len(shared.case.narrative)} chars) and {len(ids)} historical candidate(s)."
    else:
        input_summary = (
            f"Incident {shared.case.case_id}, {len(ids)} historical candidate(s), and the Evidence Agent's "
            f"challenge to the earlier claim '{previous_family}'."
        )
    signal_text = (
        f"; historical-pattern signal yes-probability {_pct(historical_signal)}" if historical_signal is not None else ""
    )
    if round_number == 2 and previous_family is not None:
        change = (
            f"Revised claim from '{previous_family}' to '{family.selected}'"
            if family.selected != previous_family
            else f"Kept claim '{family.selected}' after the challenge"
        )
    else:
        change = f"Proposed pattern family '{family.selected}'"
    action_summary = (
        f"{change} (score {_pct(family.probabilities[family.selected])}); "
        f"{len(supporting)} of {len(ids)} candidate(s) judged similar{signal_text}. Claim sent to the Evidence Agent for review."
    )

    message = AgentMessage(
        agent="pattern",
        round_number=round_number,
        input_summary=input_summary,
        decision_question=questions["pattern_family"].instructions,
        allowed_options=questions["pattern_family"].options,
        selected_option=family.selected,
        probabilities=family.probabilities,
        supporting_incident_ids=supporting,
        evidence_refs=[],
        uncertainty_status=policy.pattern_uncertainty(family.selected),
        duration_seconds=outcome.duration_seconds,
        action_summary=action_summary,
        auxiliary_decisions=aux,
        drex_request_id=outcome.request_id,
        drex_model=outcome.model,
    )
    return shared.record(outcome, message)


# ========================================================== Evidence Agent


def _retrieve_passages(store: EvidenceStore, shared: SharedState) -> list[Passage]:
    family = shared.pattern_claim.pattern_family if shared.pattern_claim else None
    if family not in GUIDANCE_TOPICS:
        return []
    query = shared.case.narrative
    primary = store.retrieve(query + " " + PATTERN_FAMILIES[family], family, k=2)
    # Give Evidence a chance to challenge a plausible but incomplete pattern
    # claim with guidance on another family, selected by narrative overlap.
    alternatives = [p for p in store.retrieve(query, None, k=MAX_PASSAGES * 8) if p.topic != family]
    return (primary + alternatives[:2])[:MAX_PASSAGES]


def run_evidence(decider: Decider, shared: SharedState, store: EvidenceStore, round_number: int) -> AgentMessage:
    assert shared.pattern_claim is not None
    claim = shared.pattern_claim
    passages = _retrieve_passages(store, shared)
    labels = [f"P{i + 1}" for i in range(len(passages))]

    state: dict[str, Any] = {
        "team_brief": TEAM_BRIEF,
        "your_role": "Evidence Agent: test the Pattern Agent's claim against located guidance passages. "
        "Challenge the claim if the evidence does not support it. Only the passages below count as evidence.",
        "current_incident": shared.incident_block(),
        "pattern_claim_under_review": {
            "pattern_family": claim.pattern_family,
            "family_definition": PATTERN_FAMILIES.get(claim.pattern_family),
            "pattern_agent_score": shared.pattern_probabilities.get(claim.pattern_family),
            "supporting_incident_ids": claim.supporting_incident_ids,
        },
        "guidance_passages": [
            {"label": label, "topic": p.topic, "document": p.title, "page": p.page, "text": p.passage_text}
            for label, p in zip(labels, passages)
        ],
    }
    if not passages:
        state["guidance_note"] = "No located guidance passages are available for this claim."
    if round_number == 2 and shared.conflict_focus:
        state["coordinator_focus"] = shared.conflict_focus

    questions = evidence_questions(labels, claim.pattern_family)
    outcome = decider.decide(state, questions)

    stance = outcome.choices["evidence_stance"]
    relevant = [p for i, p in enumerate(passages) if policy.is_yes(outcome.nouls[f"passage_{i}_relevant"])]
    relevant_claim = f"Incident {shared.case.case_id} fits the '{claim.pattern_family}' precursor family"
    refs = [p.to_ref(relevant_claim) for p in relevant]
    status = policy.evidence_uncertainty(stance.selected, len(refs))
    missing = outcome.nouls["source_missing"]

    shared.evidence = EvidenceAssessment(
        stance=stance.selected,
        stance_probabilities=stance.probabilities,
        uncertainty_status=status,
        refs=refs,
        source_missing_probability=missing,
        passages_reviewed=len(passages),
    )

    verb = {"supports": "Supports", "conflicts": "Challenges", "unresolved": "Leaves unresolved"}[stance.selected]
    action_summary = (
        f"{verb} the '{claim.pattern_family}' claim (score {_pct(stance.probabilities[stance.selected])}); "
        f"{len(refs)} of {len(passages)} NDI passage(s) judged relevant; crucial-source-missing yes-probability {_pct(missing)}."
    )
    if not passages:
        action_summary += " No NDI guidance passages were available, so the claim cannot be verified."
    elif stance.selected == "supports" and not refs:
        action_summary += " Policy P1: support without a located citation is recorded as insufficient."

    message = AgentMessage(
        agent="evidence",
        round_number=round_number,
        input_summary=(
            f"Pattern claim '{claim.pattern_family}' and {len(passages)} NDI guidance passage(s) from "
            f"{len({p.document_id for p in passages})} document(s)."
        ),
        decision_question=questions["evidence_stance"].instructions,
        allowed_options=questions["evidence_stance"].options,
        selected_option=stance.selected,
        probabilities=stance.probabilities,
        supporting_incident_ids=list(claim.supporting_incident_ids),
        evidence_refs=refs,
        uncertainty_status=status,  # type: ignore[arg-type]
        duration_seconds=outcome.duration_seconds,
        action_summary=action_summary,
        auxiliary_decisions=[_aux_noul(q, questions[q], outcome) for q in questions if q != "evidence_stance"],
        drex_request_id=outcome.request_id,
        drex_model=outcome.model,
    )
    return shared.record(outcome, message)


# ===================================================== Investigation Agent


def run_investigation(decider: Decider, shared: SharedState, round_number: int) -> AgentMessage:
    assert shared.pattern_claim is not None and shared.evidence is not None
    claim, ev = shared.pattern_claim, shared.evidence

    state = {
        "team_brief": TEAM_BRIEF,
        "your_role": "Investigation Agent: choose the next step for a human safety analyst and a human review "
        "priority, based on the earlier agents' findings and their uncertainty.",
        "current_incident": shared.incident_block(),
        "pattern_agent_finding": {
            "pattern_family": claim.pattern_family,
            "scores": shared.pattern_probabilities,
            "similar_historical_incident_ids": claim.supporting_incident_ids,
            "historical_pattern_signal_yes_probability": shared.historical_signal,
        },
        "evidence_agent_finding": {
            "stance": ev.stance,
            "stance_definition": EVIDENCE_STANCES[ev.stance],
            "scores": ev.stance_probabilities,
            "uncertainty_status": ev.uncertainty_status,
            "crucial_source_missing_yes_probability": ev.source_missing_probability,
            "cited_passages": [{"document": r.title, "page": r.page_or_region, "text": r.passage_text} for r in ev.refs],
        },
    }
    questions = investigation_questions()
    outcome = decider.decide(state, questions)

    action = outcome.choices["next_action"]
    priority = outcome.choices["review_priority"]
    shared.next_action = action.selected
    shared.next_action_probabilities = action.probabilities
    shared.review_priority = priority.selected

    citation_text = (
        "; ".join(f"{r.title.split(':')[0]} {r.page_or_region or '(no page)'}" for r in ev.refs) or "no located citations"
    )
    action_summary = (
        f"Recommends '{action.selected}' (score {_pct(action.probabilities[action.selected])}) with "
        f"'{priority.selected}' human review priority. Basis: pattern '{claim.pattern_family}', evidence "
        f"'{ev.stance}' ({ev.uncertainty_status}), {len(claim.supporting_incident_ids)} similar incident(s), "
        f"{citation_text}."
    )
    message = AgentMessage(
        agent="investigation",
        round_number=round_number,
        input_summary=(
            f"Pattern claim '{claim.pattern_family}', evidence stance '{ev.stance}' ({ev.uncertainty_status}) "
            f"with {len(ev.refs)} cited passage(s)."
        ),
        decision_question=questions["next_action"].instructions,
        allowed_options=questions["next_action"].options,
        selected_option=action.selected,
        probabilities=action.probabilities,
        supporting_incident_ids=list(claim.supporting_incident_ids),
        evidence_refs=[],
        uncertainty_status=ev.uncertainty_status,  # type: ignore[arg-type]
        duration_seconds=outcome.duration_seconds,
        action_summary=action_summary,
        auxiliary_decisions=[_aux_choice("review_priority", questions["review_priority"], outcome)],
        drex_request_id=outcome.request_id,
        drex_model=outcome.model,
    )
    return shared.record(outcome, message)


# ======================================================= Coordinator Agent


def _message_digest(message: AgentMessage) -> dict[str, Any]:
    return {
        "agent": message.agent,
        "round": message.round_number,
        "question": message.decision_question,
        "selected": message.selected_option,
        "scores": message.probabilities,
        "uncertainty_status": message.uncertainty_status,
        "supporting_incident_ids": message.supporting_incident_ids,
        "cited_passages": [
            {"document": r.title, "page": r.page_or_region, "text": r.passage_text} for r in message.evidence_refs
        ],
        "summary": message.action_summary,
    }


def run_coordinator(decider: Decider, shared: SharedState, round_number: int) -> AgentMessage:
    assert shared.evidence is not None and shared.next_action is not None
    second_round_available = round_number == 1
    state = {
        "team_brief": TEAM_BRIEF,
        "your_role": "Coordinator Agent: check that the team's findings are consistent, accept or amend the "
        "recommended next step, or send the case back for one targeted re-evaluation.",
        "current_incident": shared.incident_block(),
        "trace_so_far": [_message_digest(m) for m in shared.messages],
        "available_citation_count": len(shared.evidence.refs),
        "second_round_available": second_round_available,
    }
    questions = coordinator_questions(second_round_available)
    outcome = decider.decide(state, questions)

    decision = outcome.choices["coordinator_decision"]
    amended = outcome.choices["amended_next_action"]
    focus = outcome.choices["conflict_focus"]
    shared.coordinator_choice = decision.selected
    shared.amended_action = amended.selected
    shared.conflict_focus = focus.selected

    if decision.selected == "accept":
        text = f"Accepted the recommendation '{shared.next_action}'"
    elif decision.selected == "amend":
        text = f"Amended the recommendation from '{shared.next_action}' to '{amended.selected}'"
    else:
        text = "Returned the case for targeted re-evaluation" if second_round_available else "Still found the case unresolved after the second round"
    action_summary = (
        f"{text} (score {_pct(decision.probabilities[decision.selected])}); conflict needing most attention: "
        f"'{focus.selected}'."
    )
    status = "unresolved" if decision.selected == "reevaluate" else shared.evidence.uncertainty_status
    message = AgentMessage(
        agent="coordinator",
        round_number=round_number,
        input_summary=f"All {len(shared.messages)} earlier trace message(s) and {len(shared.evidence.refs)} cited passage(s).",
        decision_question=questions["coordinator_decision"].instructions,
        allowed_options=questions["coordinator_decision"].options,
        selected_option=decision.selected,
        probabilities=decision.probabilities,
        supporting_incident_ids=list(shared.pattern_claim.supporting_incident_ids) if shared.pattern_claim else [],
        evidence_refs=[],
        uncertainty_status=status,  # type: ignore[arg-type]
        duration_seconds=outcome.duration_seconds,
        action_summary=action_summary,
        auxiliary_decisions=[
            _aux_choice("amended_next_action", questions["amended_next_action"], outcome),
            _aux_choice("conflict_focus", questions["conflict_focus"], outcome),
        ],
        drex_request_id=outcome.request_id,
        drex_model=outcome.model,
    )
    return shared.record(outcome, message)


def challenge_packet(shared: SharedState, trigger_reason: str) -> dict[str, Any]:
    """What the round-2 Pattern Agent receives: the Evidence Agent's challenge and the Coordinator's focus."""
    assert shared.pattern_claim is not None and shared.evidence is not None
    return {
        "previous_claim": shared.pattern_claim.pattern_family,
        "evidence_stance": shared.evidence.stance,
        "evidence_uncertainty_status": shared.evidence.uncertainty_status,
        "cited_passages": [{"document": r.title, "page": r.page_or_region, "text": r.passage_text} for r in shared.evidence.refs],
        "coordinator_decision": COORDINATOR_DECISIONS.get(shared.coordinator_choice or "", shared.coordinator_choice),
        "coordinator_conflict_focus": shared.conflict_focus,
        "why_second_round": trigger_reason,
        "investigation_recommendation_on_hold": NEXT_ACTIONS.get(shared.next_action or "", shared.next_action),
    }

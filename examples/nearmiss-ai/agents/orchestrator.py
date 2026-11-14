"""Multi-agent coordination (plan B4).

Round 1: Pattern -> Evidence -> Investigation -> Coordinator.
If the Coordinator selects `reevaluate`, or policy P2 detects that evidence
conflicts with the pattern claim, ONE targeted second round runs:
Pattern (with the challenge) -> Evidence -> Investigation -> Coordinator.

Any Drex failure stops the run and yields an `incomplete` result that keeps the
real messages produced so far (contracts.md: never synthesize a missing agent).
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

from agents import policy
from agents.drex_client import Decider, DrexError
from agents.roles import SharedState, challenge_packet, run_coordinator, run_evidence, run_pattern
from agents.roles import run_investigation as run_investigation_agent
from evidence.store import EvidenceStore
from service.contracts import CaseContext, ErrorItem, EvidenceRef, InvestigationResult, PatternClaim


def _dedupe_refs(refs: list[EvidenceRef]) -> list[EvidenceRef]:
    seen, unique = set(), []
    for ref in refs:
        key = (ref.document_id, ref.page_or_region, ref.passage_text)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def run_investigation(
    case: CaseContext,
    decider_factory: Callable[[], Decider],
    store: EvidenceStore,
    requested_model: str,
) -> InvestigationResult:
    started = time.perf_counter()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    shared = SharedState(case=case)
    errors: list[ErrorItem] = []
    notes: list[str] = []
    initial_claim: PatternClaim | None = None
    revised_claim: PatternClaim | None = None
    final_action: str | None = None
    rounds_run = 0

    if not store.ready:
        errors.append(
            ErrorItem(
                code="evidence_unavailable",
                message="NDI evidence cache is not ready, so the Evidence Agent has no guidance passages and "
                "cannot verify any claim. " + store.status()["detail"],
                agent="evidence",
            )
        )

    current_agent = None
    decider = None
    try:
        decider = decider_factory()

        def one_round(round_number: int, challenge=None) -> None:
            nonlocal current_agent
            current_agent = "pattern"
            run_pattern(decider, shared, round_number, challenge)
            current_agent = "evidence"
            run_evidence(decider, shared, store, round_number)
            current_agent = "investigation"
            run_investigation_agent(decider, shared, round_number)
            current_agent = "coordinator"
            run_coordinator(decider, shared, round_number)

        one_round(1)
        rounds_run = 1
        initial_claim = shared.pattern_claim.model_copy() if shared.pattern_claim else None

        again, reason = policy.needs_second_round(
            shared.coordinator_choice or "", shared.evidence.uncertainty_status if shared.evidence else "", 1
        )
        if again:
            notes.append(f"Second round triggered: {reason}")
            one_round(2, challenge_packet(shared, reason or ""))
            rounds_run = 2
            revised_claim = shared.pattern_claim.model_copy() if shared.pattern_claim else None

        decision = policy.final_action(
            coordinator_choice=shared.coordinator_choice or "",
            investigation_action=shared.next_action or "",
            amended_action=shared.amended_action or "",
            evidence_status=shared.evidence.uncertainty_status if shared.evidence else "insufficient",
            rounds_run=rounds_run,
            supporting_incident_count=len(shared.pattern_claim.supporting_incident_ids) if shared.pattern_claim else 0,
        )
        final_action = decision.action
        notes.extend(decision.notes)
    except DrexError as error:
        errors.append(ErrorItem(code=error.code, message=error.message, agent=current_agent))  # type: ignore[arg-type]
    except Exception as error:  # a bug must surface as an incomplete run, never as a fake success
        errors.append(
            ErrorItem(
                code="internal_error",
                message=f"Unexpected {type(error).__name__} while running the {current_agent or 'setup'} step.",
                agent=current_agent,  # type: ignore[arg-type]
            )
        )
    finally:
        close = getattr(decider, "close", None)
        if callable(close):
            close()

    complete = final_action is not None and shared.review_priority is not None
    if initial_claim is None and shared.pattern_claim is not None:
        initial_claim = shared.pattern_claim
    if not complete and rounds_run == 1 and shared.pattern_claim is not None and shared.messages[-1].round_number == 2:
        # Failed during round 2 after a revised pattern claim was produced.
        revised_claim = shared.pattern_claim

    references = _dedupe_refs([ref for m in shared.messages for ref in m.evidence_refs])
    return InvestigationResult(
        case_id=case.case_id,
        run_id=run_id,
        status="complete" if complete else "incomplete",
        agent_trace=shared.messages,
        initial_claim=initial_claim,
        revised_claim=revised_claim,
        final_recommended_next_action=final_action if complete else None,  # type: ignore[arg-type]
        review_priority=shared.review_priority if complete else None,  # type: ignore[arg-type]
        references=references,
        total_duration_seconds=round(time.perf_counter() - started, 3),
        provider_model_details={
            "decision_provider": "Nace Drex",
            "decision_model": ", ".join(shared.models_seen) if shared.models_seen else None,
            "requested_model": requested_model,
            "evidence_provider": "Nace NDI (Parse, Extract, Ground)",
            "evidence_documents": [d["title"] for d in store.documents],
            "rounds_run": rounds_run,
        },
        errors=errors,
        policy_notes=notes,
    )

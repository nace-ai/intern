"""Control-flow tests with a scripted fake decider (scores are test data, not Drex output)."""

from agents.drex_client import ChoiceResult, DrexError, DrexOutcome, QuestionSpec, validate_outcome
from agents.orchestrator import run_investigation
from service.contracts import InvestigationResult

import pytest

from .conftest import ScriptedDecider

SUPPORTED = {
    "pattern_family": "communication_coordination",
    "evidence_stance": "supports",
    "next_action": "investigate_cluster",
    "review_priority": "elevated",
    "coordinator_decision": "accept",
}


def _run(case, decider, store):
    result = run_investigation(case, lambda: decider, store, requested_model="drex-latest")
    InvestigationResult.model_validate(result.model_dump())  # round-trips through the contract
    return result


def test_scenario_a_supported_single_round(pattern_case, synthetic_store):
    decider = ScriptedDecider(SUPPORTED)
    result = _run(pattern_case, decider, synthetic_store)

    assert result.status == "complete"
    assert [m.agent for m in result.agent_trace] == ["pattern", "evidence", "investigation", "coordinator"]
    assert result.final_recommended_next_action == "investigate_cluster"
    assert result.review_priority == "elevated"
    assert result.revised_claim is None
    assert result.initial_claim.pattern_family == "communication_coordination"
    assert result.agent_trace[1].uncertainty_status == "supported"
    assert result.references and all(r.status in ("grounded", "parsed") for r in result.references)
    assert decider.closed
    # Information transfer: later agents' Drex state contains earlier findings.
    evidence_state = decider.calls[1][0]
    assert evidence_state["pattern_claim_under_review"]["pattern_family"] == "communication_coordination"
    coordinator_state = decider.calls[3][0]
    assert [m["agent"] for m in coordinator_state["trace_so_far"]] == ["pattern", "evidence", "investigation"]


def test_scenario_b_conflict_triggers_one_second_round(pattern_case, synthetic_store):
    script = dict(SUPPORTED)
    script.update(
        {
            "evidence_stance@1": "conflicts",
            "pattern_family@2": "runway_surface",
            "evidence_stance@2": "supports",
            "coordinator_decision@1": "accept",  # policy P2 still forces the second pass
        }
    )
    decider = ScriptedDecider(script)
    result = _run(pattern_case, decider, synthetic_store)

    assert result.status == "complete"
    assert [(m.agent, m.round_number) for m in result.agent_trace] == [
        ("pattern", 1), ("evidence", 1), ("investigation", 1), ("coordinator", 1),
        ("pattern", 2), ("evidence", 2), ("investigation", 2), ("coordinator", 2),
    ]
    assert result.initial_claim.pattern_family == "communication_coordination"
    assert result.revised_claim.pattern_family == "runway_surface"
    assert any("P2" in note for note in result.policy_notes)
    # The round-2 pattern agent received the evidence challenge.
    assert decider.calls[4][0]["evidence_challenge_from_previous_round"]["evidence_stance"] == "conflicts"


def test_unresolved_after_cap_routes_to_specialist(pattern_case, synthetic_store):
    script = dict(SUPPORTED, coordinator_decision="reevaluate")
    result = _run(pattern_case, ScriptedDecider(script), synthetic_store)
    assert len(result.agent_trace) == 8  # never more than one extra round
    assert result.final_recommended_next_action == "specialist_review"
    assert any("P3" in note for note in result.policy_notes)


def test_amend_uses_coordinator_amended_action(pattern_case, synthetic_store):
    script = dict(SUPPORTED, coordinator_decision="amend", amended_next_action="request_context")
    result = _run(pattern_case, ScriptedDecider(script), synthetic_store)
    assert result.final_recommended_next_action == "request_context"


def test_cluster_without_matching_candidates_is_not_recommended(pattern_case, synthetic_store):
    script = dict(SUPPORTED, candidate_0_match=0.1)
    result = _run(pattern_case, ScriptedDecider(script), synthetic_store)
    assert result.final_recommended_next_action == "specialist_review"
    assert any("P4" in note for note in result.policy_notes)


def test_scenario_c_drex_failure_is_incomplete_with_partial_trace(pattern_case, synthetic_store):
    decider = ScriptedDecider(SUPPORTED, fail_on="next_action")
    result = _run(pattern_case, decider, synthetic_store)
    assert result.status == "incomplete"
    assert result.final_recommended_next_action is None and result.review_priority is None
    assert [m.agent for m in result.agent_trace] == ["pattern", "evidence"]
    assert result.errors[-1].agent == "investigation"
    assert result.errors[-1].code == "provider_unavailable"


def test_scenario_c_missing_key_is_request_level_error(pattern_case, synthetic_store):
    def factory():
        raise DrexError("provider_not_configured", "Nace API key is missing.")

    result = run_investigation(pattern_case, factory, synthetic_store, requested_model="drex-latest")
    assert result.status == "incomplete"
    assert result.agent_trace == []
    assert result.errors[0].agent is None


def test_scenario_c_no_evidence_cache(uncertain_case, empty_store):
    script = dict(SUPPORTED, next_action="request_context")
    result = _run(uncertain_case, ScriptedDecider(script), empty_store)
    evidence = result.agent_trace[1]
    assert evidence.evidence_refs == []
    assert evidence.uncertainty_status == "insufficient"  # P1: no citation, no verified support
    assert any(e.code == "evidence_unavailable" for e in result.errors)
    assert result.references == []


def test_validate_outcome_rejects_incomplete_probabilities():
    spec = {"q": QuestionSpec("choice", "pick", {"a": None, "b": None})}
    bad = DrexOutcome(choices={"q": ChoiceResult("a", {"a": 0.9})})
    with pytest.raises(DrexError) as info:
        validate_outcome(bad, spec)
    assert info.value.code == "invalid_provider_output"


def test_validate_outcome_rejects_out_of_range():
    spec = {"q": QuestionSpec("noul", "yes?")}
    with pytest.raises(DrexError):
        validate_outcome(DrexOutcome(nouls={"q": 1.5}), spec)

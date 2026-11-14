"""Tests for swarm.llm (fake mode only)."""

from swarm.llm import generate_candidates
from swarm.models import POLICY_AREAS, Candidate, CountryState

COUNTRY = CountryState(
    stock_market=0.5,
    class_mobility=0.5,
    foreign_relations=0.5,
    unemployment=0.4,
    healthcare_access=0.6,
)


def _candidates(party: str) -> list[Candidate]:
    return generate_candidates(party, COUNTRY, None, fake=True)


def test_fake_gives_five_candidates_per_party():
    for party in ("Left", "Right"):
        candidates = _candidates(party)
        assert len(candidates) == 5
        for cand in candidates:
            assert isinstance(cand, Candidate)
            assert cand.party == party
            assert set(cand.policies) == set(POLICY_AREAS)
            for policy in cand.policies.values():
                assert -1 <= policy.value <= 1
                assert policy.text
            assert cand.name and cand.slogan
        assert len({c.id for c in candidates}) == 5


def test_left_candidates_lean_negative():
    for cand in _candidates("Left"):
        for policy in cand.policies.values():
            assert policy.value < 0


def test_right_candidates_lean_positive():
    for cand in _candidates("Right"):
        for policy in cand.policies.values():
            assert policy.value > 0


def test_candidates_differ_between_each_other():
    for party in ("Left", "Right"):
        candidates = _candidates(party)
        signatures = {
            tuple(c.policies[a].value for a in POLICY_AREAS) for c in candidates
        }
        assert len(signatures) > 1, "candidates should have variety"
        assert len({c.name for c in candidates}) == 5


def test_fake_is_deterministic():
    first = _candidates("Left")
    second = _candidates("Left")
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]

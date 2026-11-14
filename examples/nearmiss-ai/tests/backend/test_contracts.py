import json

import pytest
from pydantic import ValidationError

from service.contracts import CaseContext, InvestigationResult

from .conftest import FIXTURES


@pytest.mark.parametrize("name", ["case_pattern_mock.json", "case_uncertain_mock.json"])
def test_shared_case_fixtures_validate(name):
    CaseContext.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["result_pattern_mock.json", "result_uncertain_mock.json"])
def test_shared_result_fixtures_validate(name):
    """Builder A renders these fixtures; our output model must accept the same shape."""
    InvestigationResult.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


def _case(**overrides):
    data = json.loads((FIXTURES / "case_pattern_mock.json").read_text(encoding="utf-8"))
    data.update(overrides)
    return data


def test_missing_narrative_rejected():
    data = _case()
    del data["narrative"]
    with pytest.raises(ValidationError):
        CaseContext.model_validate(data)


def test_blank_narrative_rejected():
    with pytest.raises(ValidationError):
        CaseContext.model_validate(_case(narrative="   "))


def test_candidate_equal_to_case_rejected():
    data = _case()
    data["candidate_incidents"][0]["case_id"] = data["case_id"]
    with pytest.raises(ValidationError):
        CaseContext.model_validate(data)


def test_wrong_contract_version_rejected():
    with pytest.raises(ValidationError):
        CaseContext.model_validate(_case(contract_version="2.0"))


def test_non_finite_retrieval_score_rejected():
    data = _case()
    data["candidate_incidents"][0]["retrieval_score"] = float("nan")
    with pytest.raises(ValidationError):
        CaseContext.model_validate(data)


def test_empty_candidate_list_is_valid():
    assert CaseContext.model_validate(_case(candidate_incidents=[])).candidate_incidents == []

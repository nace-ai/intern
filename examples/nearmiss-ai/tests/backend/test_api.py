import json

import pytest
from fastapi.testclient import TestClient

from service import app as app_module
from service.contracts import InvestigationResult

from .conftest import FIXTURES


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("NEARMISS_EVIDENCE_CACHE", str(tmp_path / "cache"))
    return TestClient(app_module.app)


def test_health_never_exposes_key(client, monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_secret_value_for_test")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "nace_configured": True}
    assert "nace_sk" not in response.text


def test_health_degraded_without_key(client, monkeypatch):
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    assert client.get("/health").json() == {"status": "degraded", "nace_configured": False}


def test_evidence_status_not_ready_without_cache(client):
    body = client.get("/evidence/status").json()
    assert body["status"] == "not_ready" and body["document_count"] == 0


def test_analyze_rejects_invalid_shape_with_422(client):
    data = json.loads((FIXTURES / "case_pattern_mock.json").read_text(encoding="utf-8"))
    del data["narrative"]
    assert client.post("/analyze", json=data).status_code == 422


def test_analyze_without_key_returns_incomplete_not_fake(client, monkeypatch):
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    data = json.loads((FIXTURES / "case_pattern_mock.json").read_text(encoding="utf-8"))
    response = client.post("/analyze", json=data)
    assert response.status_code == 200
    result = InvestigationResult.model_validate(response.json())
    assert result.status == "incomplete"
    assert result.agent_trace == []
    assert any(e.code == "provider_not_configured" for e in result.errors)


def test_saved_run_stays_inside_runs_directory(tmp_path, monkeypatch):
    from service.contracts import ErrorItem

    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(app_module, "RUNS_DIR", runs_dir)
    result = InvestigationResult(
        case_id="../../outside",
        run_id="run-safe",
        status="incomplete",
        agent_trace=[],
        initial_claim=None,
        revised_claim=None,
        final_recommended_next_action=None,
        review_priority=None,
        references=[],
        total_duration_seconds=0,
        provider_model_details={},
        errors=[ErrorItem(code="provider_unavailable", message="unavailable")],
    )
    app_module._save_run(result)
    assert len(list(runs_dir.glob("*.json"))) == 1
    assert not (tmp_path / "outside__run-safe.json").exists()

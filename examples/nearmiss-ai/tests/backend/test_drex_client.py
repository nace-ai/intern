"""Exercise the real nace-sdk parsing path offline with httpx.MockTransport.

Response bodies follow the documented POST /v1/systemone shape
(https://console.nace.ai/docs/drex/quickstart); the numbers are test data.
"""

import json

import httpx
import pytest
from nace_sdk import RetryPolicy

from agents.drex_client import DrexError, NaceDrexDecider, QuestionSpec

QUESTIONS = {
    "family": QuestionSpec("choice", "Which family?", {"a": "first", "b": "second"}),
    "flag": QuestionSpec("noul", "Is it flagged?"),
}


def _decider(monkeypatch, handler):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test_only_not_real")
    decider = NaceDrexDecider("drex-latest", timeout_seconds=5, transport=httpx.MockTransport(handler))
    decider._client._retry = RetryPolicy(max_retries=0)  # keep error tests fast
    return decider


def test_sdk_response_is_mapped(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            headers={"x-request-id": "req_test"},
            json={
                "model": "drex-test-version",
                "answers": {
                    "family": {"type": "choice", "choice": "b", "confidence": 0.6, "probabilities": {"a": 0.4, "b": 0.6}},
                    "flag": {"type": "noul", "noul": 0.25, "confidence": 0.75},
                },
                "usage": {"input_tokens": 10, "output_tokens": 0},
                "evaluation_time_ms": 12.0,
                "request_id": "req_test",
            },
        )

    decider = _decider(monkeypatch, handler)
    outcome = decider.decide({"text": "state"}, QUESTIONS)
    decider.close()

    assert seen["path"] == "/v1/systemone"
    assert seen["body"]["questions"]["family"] == {"type": "choice", "instructions": "Which family?", "criteria": {"a": "first", "b": "second"}}
    assert seen["body"]["questions"]["flag"]["type"] == "noul"
    assert seen["auth"].startswith("Bearer ")
    assert outcome.choices["family"].selected == "b"
    assert outcome.choices["family"].probabilities == {"a": 0.4, "b": 0.6}
    assert outcome.nouls["flag"] == 0.25
    assert outcome.model == "drex-test-version" and outcome.request_id == "req_test"


@pytest.mark.parametrize(
    "status,code",
    [(401, "provider_auth_failed"), (402, "provider_insufficient_credit"), (422, "provider_rejected_request"), (500, "provider_unavailable")],
)
def test_sdk_errors_are_mapped_without_secrets(monkeypatch, status, code):
    def handler(request):
        return httpx.Response(status, json={"error": {"type": "x", "message": "server says no"}})

    decider = _decider(monkeypatch, handler)
    with pytest.raises(DrexError) as info:
        decider.decide("state", QUESTIONS)
    decider.close()
    assert info.value.code == code
    assert "nace_sk" not in info.value.message


def test_missing_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("NACE_API_KEY", raising=False)
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    with pytest.raises(DrexError) as info:
        NaceDrexDecider("drex-latest")
    assert info.value.code == "provider_not_configured"

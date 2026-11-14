"""Drex adapter: wire format + probability mapping + model pin + 429 retry (mocked HTTP)."""

import json

import httpx
import pytest

from billproof.adapters.drex import DrexAdapter, ModelMismatch
from billproof.adapters.simulate import simulate_answers
from billproof.schema import GoldItem, Question

QUESTIONS = [
    Question(name="dup_L1", type="noul", instructions="Is line 1 a duplicate?",
             family="duplicate", line_no=1),
    Question(name="action_L1", type="choice", instructions="Recommended action for line 1",
             criteria={"OK": "no problem", "DISPUTE": "challenge", "APPEAL": "appeal a denial"},
             family="action", line_no=1),
    Question(name="appeal_strength", type="score", instructions="Appeal strength",
             criteria=["weak", "moderate", "strong"], family="appeal_strength"),
]

PAYLOAD = {
    "model": "drex-max-finance",
    "answers": {
        "dup_L1": {"type": "noul", "noul": 0.93, "confidence": 0.93},
        "action_L1": {"type": "choice", "choice": "DISPUTE", "confidence": 0.8,
                      "probabilities": {"OK": 0.1, "DISPUTE": 0.8, "APPEAL": 0.1}},
        "appeal_strength": {"type": "score", "score": 1.4, "confidence": 0.5,
                            "legend": ["weak", "moderate", "strong"],
                            "probabilities": {"0": 0.2, "1": 0.5, "2": 0.3}},
    },
    "usage": {"input_tokens": 1000, "output_tokens": 60},
    "evaluation_time_ms": 142.6,
    "request_id": "req_" + "a" * 32,
}


def make_adapter(handler, **kw):
    return DrexAdapter("nace_sk_test", "http://drex.test", "drex-max-finance",
                       client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_request_shape_and_mapping():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=PAYLOAD)

    res = make_adapter(handler).decide({"case": "x"}, QUESTIONS, case_id="SYN-0001")

    body = seen[0]
    assert body["model"] == "drex-max-finance"
    assert body["state"] == {"case": "x"}
    assert body["questions"]["dup_L1"] == {"type": "noul",
                                           "instructions": "Is line 1 a duplicate?"}
    assert body["questions"]["action_L1"]["criteria"]["DISPUTE"] == "challenge"
    assert body["questions"]["appeal_strength"]["criteria"] == ["weak", "moderate", "strong"]

    assert res.answers["dup_L1"].choice == "yes"
    assert res.answers["dup_L1"].probabilities == {"yes": 0.93, "no": 0.07}
    assert res.answers["action_L1"].choice == "DISPUTE"
    assert res.answers["action_L1"].confidence == 0.8
    assert res.answers["appeal_strength"].choice == "moderate"  # argmax over level labels
    assert res.answers["appeal_strength"].probabilities["strong"] == 0.3
    assert all(a.confidence_kind == "native" for a in res.answers.values())

    assert res.model == "drex-max-finance"
    assert res.input_tokens == 1000
    assert res.cost_usd == pytest.approx(1000 * 0.03 / 1e6)
    assert not res.simulated and not res.replayed


def test_model_pin_assertion():
    def handler(request):
        payload = dict(PAYLOAD)
        payload["model"] = "drex-v2.0"  # silent substitution on the service side
        return httpx.Response(200, json=payload)

    with pytest.raises(ModelMismatch, match="drex-v2.0"):
        make_adapter(handler).decide({}, QUESTIONS, case_id="SYN-0001")


def test_429_retry_after_ms():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"type": "rate_limit"}},
                                  headers={"retry-after-ms": "1"})
        return httpx.Response(200, json=PAYLOAD)

    res = make_adapter(handler).decide({}, QUESTIONS, case_id="SYN-0001")
    assert len(calls) == 2
    assert res.answers["dup_L1"].choice == "yes"


def test_429_gives_up_after_max_retries():
    adapter = make_adapter(lambda r: httpx.Response(429, headers={"retry-after-ms": "1"}),
                           max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.decide({}, QUESTIONS, case_id="SYN-0001")


def test_simulator_deterministic_and_calibrated():
    gold = {q.name: GoldItem(answer=q.options()[0], difficulty=0.5) for q in QUESTIONS}
    a = simulate_answers(QUESTIONS, gold, profile="drex", seed="s1")
    b = simulate_answers(QUESTIONS, gold, profile="drex", seed="s1")
    assert a == b  # deterministic per (seed, question name)
    assert all(ans.confidence_kind == "native" for ans in a.values())
    f = simulate_answers(QUESTIONS, gold, profile="frontier", seed="s1")
    assert all(ans.confidence_kind == "self_reported" and ans.probabilities is None
               for ans in f.values())

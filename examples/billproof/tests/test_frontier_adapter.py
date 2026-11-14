"""One wire-format check for the frontier chat adapter."""

import json

import httpx
import pytest

from billproof.adapters.frontier import FrontierAdapter
from billproof.config import settings
from billproof.schema import GoldItem, Question


def test_frontier_request_shape_parse_and_invalid_choice(monkeypatch):
    monkeypatch.setenv("FRONTIER_BASE_URL", "https://router.test/v1")
    monkeypatch.setenv("FRONTIER_API_KEY", "test-key")
    monkeypatch.setenv("FRONTIER_MODEL", "frontier-test")
    monkeypatch.setenv("FRONTIER_PRICE_PER_MTOK_INPUT", "2.5")
    monkeypatch.setenv("FRONTIER_PRICE_PER_MTOK_OUTPUT", "10")
    settings.cache_clear()

    seen: list[dict] = []
    rationale = " ".join(f"w{i}" for i in range(1, 23))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        if len(seen) == 1:
            return httpx.Response(422, json={"error": {"message": "response_format json_schema is not supported"}})
        content = {
            "answers": {
                "dup_L1": {"choice": "yes", "confidence": 0.91, "rationale": rationale},
                "action_L1": {"choice": "MAYBE", "confidence": 0.77, "rationale": "not allowed"},
            }
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "frontier-test-router",
                "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 80},
            },
        )

    questions = [
        Question(
            name="dup_L1",
            type="noul",
            instructions="Is line 1 a duplicate charge?",
            family="duplicate",
            line_no=1,
            evidence=["bill.lines.1.code"],
        ),
        Question(
            name="action_L1",
            type="choice",
            instructions="What action should the line take?",
            criteria={"OK": None, "DISPUTE": None, "APPEAL": None, "ASK_PATIENT": None, "INSUFFICIENT_EVIDENCE": None},
            family="action",
            line_no=1,
        ),
        Question(name="received", type="noul", instructions="Was the service received?", family="missing"),
    ]
    state = {"marker": "KNEE-MRI", "bill": {"lines": [{"line_no": 1, "code": "70553"}]}}
    gold = {"dup_L1": GoldItem(answer="no", difficulty=0.95)}

    try:
        adapter = FrontierAdapter(transport=httpx.MockTransport(handler))
        result = adapter.decide(state, questions, case_id="SYN-0001", gold=gold)
    finally:
        settings.cache_clear()

    assert len(seen) == 2
    first, second = seen
    assert first["url"].endswith("/chat/completions")
    assert second["url"].endswith("/chat/completions")
    assert first["auth"] == "Bearer test-key"
    assert second["auth"] == "Bearer test-key"
    assert first["body"]["model"] == "frontier-test"
    assert first["body"]["temperature"] == 0
    assert first["body"]["response_format"]["type"] == "json_schema"
    assert first["body"]["response_format"]["json_schema"]["strict"] is True
    props = first["body"]["response_format"]["json_schema"]["schema"]["properties"]["answers"]["properties"]
    assert props["dup_L1"]["properties"]["choice"]["enum"] == ["yes", "no"]
    assert props["action_L1"]["properties"]["choice"]["enum"][0] == "OK"
    assert "received" in schema_required(first)
    user = first["body"]["messages"][1]["content"]
    assert first["body"]["messages"][0]["role"] == "system"
    assert "KNEE-MRI" in user
    assert "dup_L1" in user
    assert "difficulty" not in json.dumps(first["body"])
    assert "difficulty" not in json.dumps(second["body"])
    assert second["body"]["response_format"] == {"type": "json_object"}

    assert result.lane == "frontier"
    assert result.model == "frontier-test-router"
    assert result.simulated is False
    assert result.error is None
    assert result.request_id == "chatcmpl-test"
    assert result.input_tokens == 1000
    assert result.output_tokens == 80
    assert result.cost_usd == pytest.approx((1000 * 2.5 + 80 * 10) / 1_000_000)
    assert result.latency_ms >= 0

    ok = result.answers["dup_L1"]
    assert ok.choice == "yes"
    assert ok.confidence == pytest.approx(0.91)
    assert ok.confidence_kind == "self_reported"
    assert ok.probabilities is None
    assert ok.rationale == " ".join(f"w{i}" for i in range(1, 21))

    bad = result.answers["action_L1"]
    assert bad.choice == "OK"
    assert bad.confidence == 0
    assert bad.probabilities is None
    assert bad.raw.get("error") == "invalid choice"

    missing = result.answers["received"]
    assert missing.choice == "yes"
    assert missing.confidence == 0
    assert missing.raw.get("error") == "missing choice"


def schema_required(seen_call: dict) -> list[str]:
    return seen_call["body"]["response_format"]["json_schema"]["schema"]["properties"]["answers"]["required"]


def test_deepinfra_uses_estimated_cost_and_skips_router_pace(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-di-key")
    monkeypatch.delenv("FRONTIER_PROVIDER", raising=False)

    def router_pace():
        raise AssertionError("router pace must not run for DeepInfra")

    monkeypatch.setattr("billproof.adapters.frontier._pace", router_pace)
    monkeypatch.setattr("billproof.adapters.frontier._deepinfra_acquire", lambda: 7)
    monkeypatch.setattr("billproof.adapters.frontier._deepinfra_release", lambda token: None)

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        assert str(request.url) == "https://api.deepinfra.com/v1/openai/chat/completions"
        assert request.headers.get("authorization") == "Bearer test-di-key"
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(422, json={"error": {"message": "response_format json_schema is not supported"}})
        content = "<think>hidden</think>" + json.dumps({
            "answers": {"dup_L1": {"choice": "no", "confidence": 0.4, "rationale": "bill.lines.1.code matches"}},
        })
        return httpx.Response(200, json={
            "id": "di-1",
            "model": "deepseek-ai/DeepSeek-V4.1-Flash",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "estimated_cost": 0.00125},
        })

    question = Question(name="dup_L1", type="noul", instructions="Is line 1 a duplicate?", family="duplicate", line_no=1)
    adapter = FrontierAdapter(
        transport=httpx.MockTransport(handler),
        provider="deepinfra",
        model="deepseek-ai/DeepSeek-V4.1-Flash",
    )
    result = adapter.decide({"marker": "X"}, [question], case_id="SYN-0001")

    assert len(seen) == 2
    assert seen[0]["reasoning_effort"] == "none"
    assert seen[0]["model"] == "deepseek-ai/DeepSeek-V4.1-Flash"
    assert seen[1]["response_format"] == {"type": "json_object"}
    assert result.lane == "deepinfra"
    assert result.cost_usd == pytest.approx(0.00125)
    assert result.answers["dup_L1"].choice == "no"
    assert result.answers["dup_L1"].raw["cost"]["kind"] == "provider_estimated_cost"

    from billproof.adapters.frontier import price_usage

    listed, meta = price_usage("deepinfra", {"usage": {}}, 1_000_000, 1_000_000, "Qwen/Qwen3.8-27B")
    assert listed == pytest.approx(0.20 + 2.50)
    assert meta["kind"] == "list"
    assert meta["source"] == "https://api.deepinfra.com/models/Qwen/Qwen3.8-27B"
    listed, _meta = price_usage("deepinfra", {"usage": {}}, 1_000_000, 1_000_000, "Qwen/Qwen3.8-Max")
    assert listed == pytest.approx(1.65 + 4.951)
    listed, _meta = price_usage("deepinfra", {"usage": {}}, 1_000_000, 1_000_000, "Qwen/Qwen3.8-Flash")
    assert listed == pytest.approx(0.113 + 0.382)

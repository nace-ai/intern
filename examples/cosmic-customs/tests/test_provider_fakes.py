"""Provider parsing against fake HTTP responses. No socket is opened."""

import asyncio

import httpx
import pytest

from clients import DrexClient, OpenRouterClient
from schemas import DecisionInput


def request() -> DecisionInput:
    return DecisionInput(
        case_id="DEV-01",
        shared_input="rulebook\n\nEVIDENCE\n\npacket",
        input_hash="abc",
        evidence_mode="raw_text",
    )


def _drex_body(**overrides):
    body = {
        "model": "drex-max-v1.1",
        "answers": {
            "verdict": {
                "type": "choice",
                "choice": "CLEAR",
                "probabilities": {"CLEAR": 0.8, "HOLD": 0.1, "REVIEW": 0.1},
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 4},
        "evaluation_time_ms": 12,
        "request_id": "req_0123456789abcdef0123456789abcdef",
    }
    body.update(overrides)
    return body


async def _decide(client_cls, handler, provider_key: str):
    calls = {"n": 0}

    def wrapped(outgoing: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return handler(outgoing)

    async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as http:
        result = await client_cls(http, api_key=provider_key).decide(request())
    return result, calls["n"]


@pytest.mark.parametrize("status_code", [403, 429, 500, 502])
def test_drex_http_errors_are_failures_without_a_verdict(status_code):
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"error": {"message": "blocked", "type": "provider"}})

        result, calls = await _decide(DrexClient, handler, "nace_sk_test")
        assert calls == 1
        assert result.status == "error"
        assert result.verdict is None
        assert result.cost_usd is None
        assert "blocked" in (result.error or "")

    asyncio.run(run())


@pytest.mark.parametrize("status_code", [403, 429, 500, 503])
def test_openrouter_http_errors_are_failures_without_a_verdict(status_code):
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"error": {"message": "blocked"}})

        result, calls = await _decide(OpenRouterClient, handler, "sk-or-test")
        assert calls == 1
        assert result.status == "error"
        assert result.verdict is None
        assert result.cost_usd is None
        assert "blocked" in (result.error or "")

    asyncio.run(run())


def test_drex_malformed_and_truncated_bodies_are_invalid():
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b'{"answers":')

        result, calls = await _decide(DrexClient, handler, "nace_sk_test")
        assert calls == 1
        assert result.status == "invalid"
        assert result.verdict is None
        assert result.cost_usd is None

    asyncio.run(run())


def test_drex_timeout_is_not_a_verdict():
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        result, calls = await _decide(DrexClient, handler, "nace_sk_test")
        assert calls == 1
        assert result.status == "timeout"
        assert result.verdict is None
        assert result.cost_usd is None
        assert result.latency_ms is not None

    asyncio.run(run())


def test_drex_missing_usage_keeps_cost_unknown_and_verdict_invalid():
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            body = _drex_body()
            body.pop("usage")
            return httpx.Response(200, json=body)

        result, _calls = await _decide(DrexClient, handler, "nace_sk_test")
        assert result.status == "invalid"
        assert result.verdict is None
        assert result.cost_usd is None
        assert "usage.input_tokens" in (result.error or "")

    asyncio.run(run())


def test_openrouter_malformed_json_is_invalid_even_when_usage_has_a_cost():
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "gen_789",
                    "model": "openai/gpt-5.4",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{\"verdict\":"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.2},
                },
            )

        result, calls = await _decide(OpenRouterClient, handler, "sk-or-test")
        assert calls == 1
        assert result.status == "invalid"
        assert result.verdict is None
        assert result.cost_usd is None

    asyncio.run(run())


def test_openrouter_records_a_substituted_model_separately_from_the_request():
    async def run():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "gen_890",
                    "model": "openai/gpt-5.4-substitute",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{\"verdict\":\"HOLD\"}"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0},
                },
            )

        result, _calls = await _decide(OpenRouterClient, handler, "sk-or-test")
        assert result.requested_model == "openai/gpt-5.4"
        assert result.actual_model == "openai/gpt-5.4-substitute"
        assert result.requested_model != result.actual_model
        assert result.verdict == "HOLD"
        assert result.cost_usd == 0

    asyncio.run(run())

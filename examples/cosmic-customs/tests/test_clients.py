"""Provider parsing, missing costs, and mock isolation."""

import asyncio
from decimal import Decimal

import httpx

from clients import DocumentClient, DrexClient, MockDecisionClient, OpenRouterClient, settled_document_cost
from schemas import DecisionInput


def request() -> DecisionInput:
    return DecisionInput(case_id="DEV-01", shared_input="rulebook\n\nEVIDENCE\n\npacket", input_hash="abc", evidence_mode="raw_text")


def test_mock_client_makes_no_network_call_and_hides_cost():
    called = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(500)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            _ = http
            result = await MockDecisionClient("drex").decide(request())
            assert result.mode == "mock"
            assert result.cost_usd is None
            assert result.latency_ms is None
            assert result.verdict in ("CLEAR", "HOLD", "REVIEW")
            assert called["count"] == 0

    asyncio.run(run())


def test_drex_parser_keeps_choice_and_probabilities():
    async def run() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"x-request-id": "req_0123456789abcdef0123456789abcdef"},
                json={
                    "model": "drex-max-v1.1",
                    "answers": {
                        "verdict": {
                            "type": "choice",
                            "choice": "HOLD",
                            "confidence": 0.8,
                            "probabilities": {"CLEAR": 0.1, "HOLD": 0.8, "REVIEW": 0.1},
                        }
                    },
                    "usage": {"input_tokens": 1000, "output_tokens": 20},
                    "evaluation_time_ms": 42,
                    "request_id": "req_0123456789abcdef0123456789abcdef",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DrexClient(http, api_key="nace_sk_test").decide(request())
        assert result.status == "ok"
        assert result.verdict == "HOLD"
        assert result.probabilities["HOLD"] == 0.8
        assert result.actual_model == "drex-max-v1.1"
        assert result.cost_usd == Decimal("0.00003000")
        assert result.server_latency_ms == 42
        assert result.latency_ms is not None

    asyncio.run(run())


def test_drex_rejects_a_substituted_model():
    async def run() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "drex-v2.0",
                    "answers": {
                        "verdict": {
                            "type": "choice",
                            "choice": "CLEAR",
                            "confidence": 0.9,
                            "probabilities": {"CLEAR": 0.9, "HOLD": 0.05, "REVIEW": 0.05},
                        }
                    },
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                    "evaluation_time_ms": 5,
                    "request_id": "req_0123456789abcdef0123456789abcdef",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DrexClient(http, api_key="nace_sk_test").decide(request())
        assert result.status == "invalid"
        assert result.verdict is None
        assert result.actual_model == "drex-v2.0"

    asyncio.run(run())


def test_openrouter_missing_cost_stays_unknown():
    async def run() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "gen_123",
                    "model": "openai/gpt-5.4",
                    "choices": [{"finish_reason": "stop", "message": {"content": "{\"verdict\":\"REVIEW\"}"}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await OpenRouterClient(http, api_key="sk-or-test").decide(request())
        assert result.status == "ok"
        assert result.verdict == "REVIEW"
        assert result.probabilities is None
        assert result.cost_usd is None
        assert result.cost_source is None

    asyncio.run(run())


def test_openrouter_reads_usage_cost_and_rejects_truncation():
    async def run() -> None:
        def handler(outgoing: httpx.Request) -> httpx.Response:
            body = outgoing.read()
            assert b"usage" not in body or b"include" not in body
            return httpx.Response(
                200,
                json={
                    "id": "gen_456",
                    "model": "openai/gpt-5.4",
                    "choices": [{"finish_reason": "length", "message": {"content": "{\"verdict\":\"CLEAR\"}"}}],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
                        "cost": 0.0123,
                        "prompt_tokens_details": {"cached_tokens": 4},
                        "completion_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await OpenRouterClient(http, api_key="sk-or-test").decide(request())
        assert result.status == "invalid"
        assert result.verdict is None
        assert result.cached is True

    asyncio.run(run())


def test_document_cost_uses_total_usd_not_credits():
    assert settled_document_cost({"credits": 40, "cost": {"total_usd": "1.25", "pricing_version": "x", "lines": []}}) == Decimal("1.25")
    assert settled_document_cost({"credits": 40, "cost": None}) is None
    assert settled_document_cost({"credits": 0}) is None


def test_mock_and_live_result_directories_stay_separate(tmp_path, monkeypatch):
    from storage import ResultStore

    store = ResultStore(tmp_path)
    store.append("mock", "decisions", {"mode": "mock", "case_id": "A"})
    store.append("live", "decisions", {"mode": "live", "case_id": "B"})
    mock_rows = store.read("mock", "decisions")
    live_rows = store.read("live", "decisions")
    assert [row["case_id"] for row in mock_rows] == ["A"]
    assert [row["case_id"] for row in live_rows] == ["B"]
    assert store.read("live", "documents") == []

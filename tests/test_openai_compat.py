"""The OpenAI-compatible adapter against recorded wire shapes."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from intern.messages import AssistantMessage, ToolCall, UserMessage
from intern.models.profile import Compat, load_profile
from intern.models.registry import resolve_endpoint
from intern.providers.base import (
    NO_RETRIES,
    Completed,
    ProviderError,
    ProviderRequest,
    ReasoningChunk,
    RetryPolicy,
    TextChunk,
)
from intern.providers.openai_compat import OpenAICompatProvider


def sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def delta(**fields: Any) -> dict[str, Any]:
    finish = fields.pop("finish_reason", None)
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": finish}]}


def provider(handler) -> OpenAICompatProvider:
    return OpenAICompatProvider(resolve_endpoint("vllm/test-model", {}), transport=httpx.MockTransport(handler))


def request(**overrides: Any) -> ProviderRequest:
    return ProviderRequest(model="test-model", messages=[UserMessage(content="hi")], max_output_tokens=256, **overrides)


async def collect(p: OpenAICompatProvider, req: ProviderRequest) -> tuple[list[Any], AssistantMessage]:
    items = [item async for item in p.stream(req)]
    assert isinstance(items[-1], Completed)
    return items[:-1], items[-1].message


async def test_streamed_text_reasoning_tool_calls_and_usage():
    body = sse(
        delta(reasoning_content="Need the file. "),
        delta(content="Reading it."),
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "read", "arguments": '{"pa'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "a.py"}'}}]),
        delta(finish_reason="tool_calls"),
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 100},
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        },
    )
    chunks, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert [type(c) for c in chunks] == [ReasoningChunk, TextChunk]
    assert message.text == "Reading it."
    assert message.reasoning == "Need the file."
    assert message.tool_calls == [ToolCall(id="c1", name="read", arguments={"path": "a.py"})]
    assert message.stop == "tool_calls"
    assert (message.usage.input_tokens, message.usage.cache_read_tokens, message.usage.reasoning_tokens) == (
        120,
        100,
        5,
    )
    assert message.model == "vllm/test-model"


async def test_line_separator_characters_inside_data_lines_are_kept():
    body = sse(delta(content="a\u2028b\u0085c"), delta(finish_reason="stop")).replace(b"\\u2028", "\u2028".encode())
    body = body.replace(b"\\u0085", "\u0085".encode())
    _, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert message.text == "a\u2028b\u0085c"


async def test_a_stream_cut_before_the_reply_finished_is_an_error():
    body = f"data: {json.dumps(delta(content='half a rep'))}\n\n".encode()
    with pytest.raises(ProviderError, match="ended before the reply finished"):
        await collect(provider(lambda r: httpx.Response(200, content=body)), request(retries=NO_RETRIES))


async def test_a_stream_cut_in_the_middle_of_an_event_is_retried_like_one_cut_between_events():
    replies = [b'data: {"choices":', sse(delta(content="ok"), delta(finish_reason="stop"))]
    _, message = await collect(
        provider(lambda r: httpx.Response(200, content=replies.pop(0))),
        request(retries=RetryPolicy(provider_waits_s=(0.0,))),
    )
    assert message.text == "ok" and not replies


async def test_an_error_sent_as_a_json_body_with_status_200_is_raised_as_that_error():
    calls = 0

    def refused(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"error": {"code": "invalid_api_key", "message": "bad synthetic key"}})

    with pytest.raises(ProviderError, match="bad synthetic key") as raised:
        await collect(provider(refused), request(retries=RetryPolicy(provider_waits_s=(0.0,))))
    assert calls == 1 and not raised.value.retryable  # a wrong key is not asked again

    replies = [
        httpx.Response(200, json={"error": {"code": 429, "message": "slow down"}}),
        httpx.Response(200, content=sse(delta(content="ok"), delta(finish_reason="stop"))),
    ]
    _, message = await collect(
        provider(lambda r: replies.pop(0)), request(retries=RetryPolicy(provider_waits_s=(0.0,)))
    )
    assert message.text == "ok"  # a rate limit is


async def test_when_a_server_sends_several_choices_only_the_first_is_the_reply():
    def choice(index: int, **fields: Any) -> dict[str, Any]:
        return {"index": index, "delta": fields, "finish_reason": None}

    def call(call_id: str, path: str) -> list[dict[str, Any]]:
        return [{"index": 0, "id": call_id, "function": {"name": "read", "arguments": json.dumps({"path": path})}}]

    streamed = sse(
        {"choices": [choice(0, content="Option A"), choice(1, content="Option B")]},
        {"choices": [choice(0, tool_calls=call("c1", "a")), choice(1, tool_calls=call("c2", "b"))]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [{"index": 1, "delta": {}, "finish_reason": "length"}]},
    )
    _, message = await collect(provider(lambda r: httpx.Response(200, content=streamed)), request())
    assert message.text == "Option A"
    assert message.tool_calls == [ToolCall(id="c1", name="read", arguments={"path": "a"})]
    assert message.stop == "tool_calls" and not message.invalid_tool_calls

    whole = {
        "choices": [
            {"index": 0, "message": {"content": "Option A"}, "finish_reason": "stop"},
            {"index": 1, "message": {"content": "Option B"}, "finish_reason": "length"},
        ]
    }
    _, message = await collect(
        provider(lambda r: httpx.Response(200, json=whole)), request(compat=Compat(stream=False))
    )
    assert message.text == "Option A" and message.stop == "stop"


async def test_text_tool_call_ids_are_unique_across_replies():
    text = '<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>'
    body = sse(delta(content=text), delta(finish_reason="stop"))
    p = provider(lambda r: httpx.Response(200, content=body))
    ids = {(await collect(p, request()))[1].tool_calls[0].id for _ in range(3)}
    assert len(ids) == 3


async def test_think_tags_in_content_become_reasoning():
    body = sse(delta(content="<think>step one"), delta(content="</think>Final."), delta(finish_reason="stop"))
    _, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert (message.reasoning, message.text) == ("step one", "Final.")


async def test_tool_calls_written_as_text_are_turned_into_calls():
    text = '<tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>'
    body = sse(delta(content=text), delta(finish_reason="stop"))
    _, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert [(c.name, c.arguments) for c in message.tool_calls] == [("bash", {"command": "ls"})]
    assert message.stop == "tool_calls"


async def test_unparsable_arguments_become_invalid_calls():
    body = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "write", "arguments": '{"path": "a'}}]),
        delta(finish_reason="length"),
    )
    _, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert not message.tool_calls
    assert message.invalid_tool_calls[0].name == "write"
    assert message.stop == "length"


async def test_non_streaming_mode():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "ok",
                    "tool_calls": [{"id": "c", "function": {"name": "find", "arguments": '{"pattern": "*.py"}'}}],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.0001},
    }
    seen: list[dict[str, Any]] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(json.loads(r.content))
        return httpx.Response(200, json=payload)

    _, message = await collect(provider(handler), request(compat=Compat(stream=False)))
    assert seen[0]["stream"] is False and "stream_options" not in seen[0]
    assert message.tool_calls[0].arguments == {"pattern": "*.py"}
    assert message.usage.cost_usd == pytest.approx(0.0001)


async def test_retryable_errors_are_retried_before_anything_streams():
    attempts: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, content=sse(delta(content="ok"), delta(finish_reason="stop")))

    _, message = await collect(provider(handler), request(retries=RetryPolicy(provider_waits_s=(0.0, 0.0))))
    assert message.text == "ok"
    assert len(attempts) == 3


async def test_client_errors_are_not_retried():
    attempts: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad request: unknown field")

    with pytest.raises(ProviderError, match="HTTP 400"):
        await collect(provider(handler), request())
    assert len(attempts) == 1


async def test_request_body_follows_compat_flags_and_replays_reasoning_only_to_its_author():
    seen: list[dict[str, Any]] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(json.loads(r.content))
        return httpx.Response(200, content=sse(delta(content="ok"), delta(finish_reason="stop")))

    messages = [
        UserMessage(content="hi"),
        AssistantMessage(text="a", reasoning="mine", model="vllm/test-model"),
        AssistantMessage(text="b", reasoning="other", model="openrouter/else"),
        UserMessage(content="go on"),
    ]
    req = ProviderRequest(
        model="test-model",
        messages=messages,
        max_output_tokens=99,
        reasoning_replay=True,
        compat=Compat(max_tokens_field="max_completion_tokens", extra_body={"top_k": 20}),
    )
    await collect(provider(handler), req)
    body = seen[0]
    assert body["max_completion_tokens"] == 99 and "max_tokens" not in body
    assert body["top_k"] == 20
    assistants = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistants[0]["reasoning_content"] == "mine"
    assert "reasoning_content" not in assistants[1]


async def test_claude_through_openrouter_asks_for_prompt_caching():
    """Anthropic caches only where a request asks: without it every Claude turn through OpenRouter paid its whole prompt
    at full price. The top-level `cache_control` is OpenRouter's automatic caching; other models cache on their own."""
    seen: list[dict[str, Any]] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(json.loads(r.content))
        return httpx.Response(200, content=sse(delta(content="ok"), delta(finish_reason="stop")))

    for ref in ("openrouter/anthropic/claude-sonnet-5-5", "openrouter/deepseek/deepseek-v4-flash"):
        endpoint = resolve_endpoint(ref, {"OPENROUTER_API_KEY": "k"})
        p = OpenAICompatProvider(endpoint, transport=httpx.MockTransport(handler))
        await collect(p, request(compat=load_profile(ref).compat))
    claude, other = seen
    assert claude["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in other


async def test_model_requests_say_they_come_from_intern():
    seen: list[httpx.Headers] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r.headers)
        return httpx.Response(200, content=sse(delta(content="ok", finish_reason="stop")))

    await collect(provider(handler), request())
    assert seen[0]["X-Nace-Client"] == "intern" and seen[0]["X-Title"] == "Intern"


async def test_a_tool_call_cut_off_at_the_output_limit_before_its_arguments_is_not_run():
    body = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "list", "arguments": "{}"}}]),
        delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "publish", "arguments": ""}}]),
        delta(finish_reason="length"),
    )
    _, message = await collect(provider(lambda r: httpx.Response(200, content=body)), request())
    assert [c.id for c in message.tool_calls] == ["c1"]  # the call that finished still runs
    assert [c.name for c in message.invalid_tool_calls] == ["publish"]

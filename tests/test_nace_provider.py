"""The `nace` provider: a bearer asked of its credential source on every request, renewed once when the console
says it expired, and sent to no host but a Nace console."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from intern.decisions import DecisionsApiBackend, NoulAnswer, NoulQuestion, Question
from intern.messages import UserMessage
from intern.models import catalog, registry
from intern.models.profile import load_profile
from intern.models.registry import ConfigError, resolve_endpoint
from intern.providers.base import NO_RETRIES, Completed, ProviderError, ProviderRequest
from intern.providers.openai_compat import OpenAICompatProvider
from tests.fake_openai import FakeOpenAI

EXPIRED = {"error": {"type": "authentication_error", "code": "token_expired", "message": "Access token expired."}}


class Tokens:
    """A Nace-issued source whose token changes when it is renewed, as the free credential's does."""

    nace_issued = True

    def __init__(self) -> None:
        self.issued = 1
        self.invalidated = 0

    async def bearer(self) -> SecretStr | None:
        return SecretStr(f"nace_at_test_{self.issued}")

    async def invalidate(self) -> None:
        self.invalidated += 1
        self.issued += 1

    def observe(self, headers: Any) -> None:
        return None


def sse_reply() -> httpx.Response:
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
    ]
    body = b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"
    return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})


def system2(source: Tokens, handler: Any) -> OpenAICompatProvider:
    endpoint = resolve_endpoint("nace/glm-5.3", {"NACE_API_KEY": "nace_sk_test_key"})
    endpoint = endpoint.model_copy(update={"credential": source})
    return OpenAICompatProvider(endpoint, transport=httpx.MockTransport(handler))


def ask() -> ProviderRequest:
    return ProviderRequest(
        model="glm-5.3", messages=[UserMessage(content="hi")], max_output_tokens=64, retries=NO_RETRIES
    )


async def reply(provider: OpenAICompatProvider) -> Completed:
    items = [item async for item in provider.stream(ask())]
    assert isinstance(items[-1], Completed)
    return items[-1]


async def test_system2_asks_the_source_on_every_request_and_renews_an_expired_token_once():
    source = Tokens()
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer nace_at_test_1":
            return httpx.Response(401, json=EXPIRED)
        return sse_reply()

    provider = system2(source, handler)
    assert (await reply(provider)).message.text == "ok"
    source.issued = 5  # renewed elsewhere (another harness process) between two requests
    await reply(provider)
    assert sent == ["Bearer nace_at_test_1", "Bearer nace_at_test_2", "Bearer nace_at_test_5"]
    assert source.invalidated == 1


async def test_system2_resends_an_expired_token_only_once():
    source = Tokens()
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["Authorization"])
        return httpx.Response(401, json=EXPIRED)

    with pytest.raises(ProviderError) as refused:
        await reply(system2(source, handler))
    assert refused.value.status == 401
    assert sent == ["Bearer nace_at_test_1", "Bearer nace_at_test_2"] and source.invalidated == 1


async def test_system1_on_the_console_asks_the_source_per_request_and_renews_an_expired_token_once():
    source = Tokens()
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer nace_at_test_1":
            return httpx.Response(401, json=EXPIRED)
        return httpx.Response(200, json={"answers": {"ok": {"type": "noul", "noul": 0.9}}})

    backend = DecisionsApiBackend(
        "https://console.nace.ai/v1", source, ["drex-latest"], transport=httpx.MockTransport(handler)
    )
    questions: dict[str, Question] = {"ok": NoulQuestion(instructions="Is it?")}
    assert (await backend.decide("state", questions)).answers["ok"] == NoulAnswer(noul=0.9)
    source.issued = 7
    await backend.decide("another state", questions)
    assert sent == ["Bearer nace_at_test_1", "Bearer nace_at_test_2", "Bearer nace_at_test_7"]
    assert source.invalidated == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://console.nace.ai/v1",
        "https://console.nace.ai.example/v1",
        "https://console.nace.ai:8443/v1",
        "https://api.fireworks.ai/inference/v1",
    ],
)
def test_a_nace_issued_source_is_refused_for_any_host_but_a_nace_console_over_https(url: str):
    with pytest.raises(ConfigError, match="sent only to"):
        registry.check_host(Tokens(), url)
    registry.check_host(registry.StaticKey(SecretStr("nace_sk_test_key")), url)  # an own key goes where it is told


@pytest.mark.parametrize("host", sorted(registry.NACE_HOSTS))
def test_a_nace_issued_source_goes_to_every_nace_console(host: str):
    registry.check_host(Tokens(), f"https://{host}/v1")


async def test_a_base_url_override_elsewhere_refuses_the_nace_credential_before_any_request(monkeypatch):
    monkeypatch.setattr(registry, "nace_credential", lambda spec, env, free=False: Tokens())
    with FakeOpenAI([]) as server:
        env = {"NACE_BASE_URL": server.url, "DREX_BASE_URL": server.url.removesuffix("/v1")}
        with pytest.raises(ConfigError, match="sent only to"):
            resolve_endpoint("nace/glm-5.3", env)
        with pytest.raises(ConfigError, match="sent only to"):
            DecisionsApiBackend(f"{env['DREX_BASE_URL']}/v1", Tokens(), ["drex-latest"])
        # Even an endpoint moved there after it was checked sends nothing.
        moved = resolve_endpoint("nace/glm-5.3", {}).model_copy(update={"base_url": server.url})
        with pytest.raises(ConfigError, match="sent only to"):
            await reply(OpenAICompatProvider(moved))
        assert server.requests == [] and server.system1 == []


@pytest.fixture
def fresh_catalog_cache():
    catalog._cache.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    catalog._cache.clear()  # pyright: ignore[reportPrivateUsage]


DECISION_MODELS = [
    {"name": "drex-v2.0", "description": "Drex 2.0 decision model.", "alias_for": None},
    {"name": "drex-latest", "description": "Points to drex-v2.0.", "alias_for": "drex-v2.0"},
]


@pytest.mark.parametrize(
    ("body", "system2"),
    [
        ({"models": DECISION_MODELS}, []),  # what console.nace.ai/v1/models returns today (2026-11-19)
        ({"models": DECISION_MODELS, "data": [{"id": "nace/glm-5.3"}, {"id": "drex-latest"}]}, ["nace/glm-5.3"]),
    ],
    ids=["decision-models-only", "chat-and-decision-models"],
)
async def test_the_consoles_decision_models_are_never_offered_as_system2(
    fresh_catalog_cache, body: dict[str, Any], system2: list[str]
):
    def listing(request: httpx.Request) -> httpx.Response:
        if request.url.host == "console.nace.ai" and request.url.path == "/v1/models":
            return httpx.Response(200, json=body)
        return httpx.Response(500)

    found = await catalog.list_models(
        {"NACE_API_KEY": "nace_sk_test_key"},
        system1=catalog.System1Sources(console_key="nace_sk_test_key", console_models=()),
        transport=httpx.MockTransport(listing),
    )
    assert [m.id for m in found.system2 if m.id.startswith("nace/")] == system2
    assert [m.id for m in found.system1] == ["drex-v2.0", "drex-latest"]


GLM_EFFORTS = ["low", "medium", "high", "max"]
DEEPSEEK_EFFORTS = ["low", "medium", "high", "xhigh", "max"]


@pytest.mark.parametrize(
    ("ref", "window", "efforts"),
    [
        ("nace/glm-5.3", 1_048_576, GLM_EFFORTS),
        ("fireworks/accounts/fireworks/models/glm-5p3", 1_048_576, GLM_EFFORTS),
        ("fireworks/accounts/fireworks/routers/glm-5p3-fast", 1_048_576, GLM_EFFORTS),
        ("nace/deepseek-v4p1-flash-1017", None, DEEPSEEK_EFFORTS),
        # Another model, which takes images: GLM 5.3's profile would tell it it cannot see them.
        ("fireworks/accounts/fireworks/models/glm-5p3-flash", None, None),
    ],
)
def test_the_models_the_console_serves_run_on_their_fireworks_profiles(
    ref: str, window: int | None, efforts: list[str] | None
):
    # Without a match, a console model would run at the 32K default window and take no thinking level.
    profile = load_profile(ref)
    assert (profile.window, profile.effort_levels) == (window, efforts)

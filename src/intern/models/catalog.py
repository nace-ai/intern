"""The models a client can pick from: System 2 from every provider the harness can reach, System 1 from the
console and the decisions API.

System 2 asks each built-in provider and each configured `[providers]` entry whose API key variable is set (or that
needs none) for `GET {base_url}/models`, all at once, and keeps what answers in time, only the models it can chat
with (see `language_model`). System 1 asks the console for its decision models (`GET {console_url}/models`, with
the console key; retired ones left out) and the decisions API for its decision models
(`GET {base_url}/models?output_modalities=decisions`, OpenRouter's filter for models that answer typed questions: Jev,
`nace-ai/drex-*`, Perplexity's Decider, Liquid's d1, ...), each only when its key is set; a console that cannot be
asked is stood in for by `[system1].models`. Listings are cached per endpoint for `CACHE_TTL_S`. Used by
`intern models --json` and the ACP model picker.
"""

from __future__ import annotations

import asyncio
import re
import time
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from intern.models.profile import PACKAGED_PROFILES
from intern.models.registry import PROVIDERS, ConfigError, Endpoint, ProviderSpec, resolve_endpoint
from intern.models.window import LOOKUP_TIMEOUT_S, auth_headers, context_length
from intern.nace_free import Bundle, Meters

NACE_CONSOLE_URL = "https://console.nace.ai/v1"
DECISIONS_PREFIX = "decisions:"
"""Marks a System 1 choice served by the decisions API (`[system1].base_url`) rather than the console."""
DECISIONS_FILTER = {"output_modalities": "decisions"}
RETIRED = "Retired"
CACHE_TTL_S = 600.0

_cache: dict[tuple[str, str], tuple[float, list[ModelInfo]]] = {}


class ModelInfo(BaseModel):
    id: str
    name: str
    default: bool = False
    """System 2 only: the model a session opens on unless the client picks one."""
    reasoning: bool = False
    """System 2 only: it takes a reasoning effort, so sessions on it offer `thought_level`."""
    efforts: list[str] = []
    """System 2 only: what its sessions' `thought_level` takes, as they list it: `default` first when the API takes
    requests without an effort, then the efforts least to most."""
    window: int | None = None
    """The most context a session on it can use, when the profile or the listing says: for System 1, the decisions
    API's listing (the console's gives none)."""


class CapabilityInfo(BaseModel):
    """A capability toggle sessions offer as an ACP `cap.*` option, and whether the config turns it on."""

    id: str
    name: str
    description: str
    enabled: bool
    unavailable: str | None = None
    """Why it cannot switch on in this install and environment; None when it can."""


class RunnerInfo(BaseModel):
    """A runner a session can pick (the ACP `runner` option), and whether the config picks it."""

    id: str
    name: str
    description: str
    default: bool
    unavailable: str | None = None
    """Why it cannot run in this install and environment; None when it can."""


class HandoffChoices(BaseModel):
    """The models a run can hand over to (the ACP `handoff` option's, less `off`), and the configured one."""

    models: list[str]
    default: str | None = None
    """`[capabilities.handoff] to` (or `--handoff-to`); None when runs hand over to no model."""


class KeyNeeded(BaseModel):
    """A key a recommended combination runs on: its variable, the provider it is for, and where to make one."""

    env: str
    provider: str
    url: str


class RecommendedInfo(BaseModel):
    """One of Nace's recommended own-key combinations (`onboarding.RECOMMENDED`), as the setup screens offer it."""

    id: str
    title: str
    why: str
    system2: str
    handoff: str
    system1: str
    """As `--system1` and the `system1` list name it: `decisions:<model>` or a console model."""
    keys: list[KeyNeeded]


class FreeSources(BaseModel):
    """What each system runs on: `free` credit, or the user's `own` configuration."""

    system1: Literal["free", "own"]
    agent: Literal["free", "own"]
    """System 2 and the hand-over model."""


class FreeInfo(BaseModel):
    """The free credit, for a client to show: the grant, whether it is in use, and what each system runs on."""

    state: str
    """The grant's `active`, `paused` or `exhausted`; `signed_out`; or why it could not be read (`free_revoked`,
    `unavailable`)."""
    meters: Meters | None = None
    bundle: Bundle | None = None
    use: bool | None = None
    """`[free] use`: false while paused with `intern free pause`, unset before a first sign-in."""
    paused: bool = False
    """The console has paused free calls."""
    source: FreeSources


class Catalog(BaseModel):
    system2: list[ModelInfo]
    system1: list[ModelInfo]
    capabilities: list[CapabilityInfo] = []
    runners: list[RunnerInfo] = []
    handoff: HandoffChoices | None = None
    recommended: list[RecommendedInfo] = []
    free: FreeInfo | None = None


@dataclass(frozen=True)
class System1Sources:
    """Where System 1's choices are listed from."""

    console_url: str = NACE_CONSOLE_URL
    console_key: str | None = None
    console_models: tuple[str, ...] = ("drex-latest",)
    """Stand in for the console's listing when it cannot be asked, and come first when it can."""
    decisions_url: str = "https://openrouter.ai/api/v1"
    decisions_key: str | None = None


def profile_models(search_dirs: Iterable[Path] = ()) -> list[ModelInfo]:
    """Concrete `provider/model` names in profile `match` lists (patterns without wildcards), named by the
    profile's `name` when it has one."""
    texts = [item.read_text() for item in resources.files(PACKAGED_PROFILES).iterdir() if item.name.endswith(".toml")]
    texts += [path.read_text() for folder in search_dirs if folder.is_dir() for path in sorted(folder.glob("*.toml"))]
    models: list[ModelInfo] = []
    for text in texts:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        name = data.get("name") if isinstance(data.get("name"), str) else None
        for p in data.get("match", []):
            if isinstance(p, str) and "/" in p and not any(c in p for c in "*?["):
                models.append(ModelInfo(id=p, name=name or p))
    return models


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    items = body.get(key) if isinstance(body, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


NON_LANGUAGE_TYPES = frozenset({"embedding", "image", "video", "audio", "tts", "transcribe", "moderation", "rerank"})
"""Listing `type`s (Together's, for one) and Fireworks `kind`s (`EMBEDDING_MODEL`, less `_model`) of models that do
not answer chat."""
NON_LANGUAGE_WORDS = frozenset(
    {
        *("embed", "embedding", "embeddings", "bge", "e5", "gte", "rerank", "reranker"),
        *("tts", "speech", "whisper", "transcribe", "audio", "realtime"),
        *("image", "dall", "diffusion", "flux", "sdxl", "sora"),
        *("moderation", "guard", "babbage", "davinci"),
    }
)
"""Words in the ids of models that do not answer chat, for listings that say nothing about it: OpenAI's
`text-embedding-3-small`, `tts-1`, `gpt-4o-transcribe`, `dall-e-3`, `omni-moderation-latest`, `davinci-002`; Groq's
`whisper-large-v3`, `llama-guard-4-12b`; Ollama's `nomic-embed-text`."""


CHAT_COMPLETIONS_ONLY = frozenset({"search"})
"""Words in the ids of OpenAI models served on Chat Completions only, which the Responses API refuses ("The requested
model 'gpt-5-search-api' is not supported with the Responses API", 2026-11-17). OpenAI's models go through the
Responses API, so System 2 cannot use these; elsewhere a `search` model may chat (OpenRouter's Sonar Pro Search)."""


def language_model(model_id: str, item: dict[str, Any]) -> bool:
    """Whether a `/models` listing entry is a model System 2 can chat with: what the listing says where it says (a
    non-language `type` or Fireworks `kind`, OpenRouter's `architecture.output_modalities`, Fireworks'
    `supports_chat`), else no word in its id names an embedding, speech, image, moderation or completions-only
    model. The kind goes first: Fireworks marks its embedding and reranker models `supports_chat`."""
    for kind in (item.get("type"), item.get("kind")):
        if isinstance(kind, str) and kind.lower().removesuffix("_model") in NON_LANGUAGE_TYPES:
            return False
    architecture = item.get("architecture")
    if isinstance(architecture, dict) and isinstance(outputs := architecture.get("output_modalities"), list):
        return "text" in outputs
    if isinstance(supports_chat := item.get("supports_chat"), bool):
        return supports_chat
    return NON_LANGUAGE_WORDS.isdisjoint(re.split(r"[^a-z0-9]+", model_id.lower()))


def responses_model(endpoint: Endpoint, model_id: str) -> bool:
    """Whether the endpoint can serve the model: anything, except on the Responses API a Chat-Completions-only model."""
    words = re.split(r"[^a-z0-9]+", model_id.lower())
    return endpoint.api != "responses" or CHAT_COMPLETIONS_ONLY.isdisjoint(words)


def _listed(endpoint: Endpoint, body: Any) -> list[ModelInfo]:
    # The console lists its decision models under `models` (today, all it lists): System 1's, not System 2's,
    # even should one also appear among its chat models under `data`.
    decision = {item.get("name") for item in _items(body, "models")}
    models: list[ModelInfo] = []
    for item in _items(body, "data"):
        model_id = item.get("id")
        if (
            isinstance(model_id, str)
            and model_id
            and model_id not in decision
            and language_model(model_id, item)
            and responses_model(endpoint, model_id)
        ):
            name = item.get("name") or item.get("display_name") or model_id
            models.append(ModelInfo(id=f"{endpoint.provider}/{model_id}", name=str(name), window=context_length(item)))
    return models


def console_listed(body: Any) -> list[ModelInfo]:
    """The console's `{"models": [{name, description, alias_for}]}`; a retired model says so in its description."""
    models: list[ModelInfo] = []
    for item in _items(body, "models"):
        name, description = item.get("name"), item.get("description")
        if isinstance(name, str) and name and not (isinstance(description, str) and description.startswith(RETIRED)):
            models.append(ModelInfo(id=name, name=name))
    return models


def _decisions_listed(body: Any) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    for item in _items(body, "data"):
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id:
            name = str(item.get("name") or model_id)
            models.append(ModelInfo(id=f"{DECISIONS_PREFIX}{model_id}", name=name, window=context_length(item)))
    return models


async def _fetch(
    key: tuple[str, str],
    url: str,
    headers: Mapping[str, str],
    params: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None,
    parse: Callable[[Any], list[ModelInfo]],
) -> list[ModelInfo]:
    cached = _cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=LOOKUP_TIMEOUT_S, headers=dict(headers), transport=transport) as client:
            response = await client.get(url, params=dict(params))
            response.raise_for_status()
            models = parse(response.json())
    except (httpx.HTTPError, ValueError):
        return []
    _cache[key] = (time.monotonic(), models)
    return models


async def _provider_models(endpoint: Endpoint, transport: httpx.AsyncBaseTransport | None) -> list[ModelInfo]:
    key = (endpoint.provider, endpoint.base_url)
    url = f"{endpoint.base_url}/models"
    headers = await auth_headers(endpoint)
    return await _fetch(key, url, headers, {}, transport, lambda body: _listed(endpoint, body))


async def console_models(sources: System1Sources, transport: httpx.AsyncBaseTransport | None = None) -> list[ModelInfo]:
    """The decision models the console serves, when its key is set; empty otherwise."""
    if not sources.console_key:
        return []
    headers = {"Authorization": f"Bearer {sources.console_key}"}
    url = f"{sources.console_url.rstrip('/')}/models"
    return await _fetch(("console", sources.console_url), url, headers, {}, transport, console_listed)


async def decision_models(
    sources: System1Sources, transport: httpx.AsyncBaseTransport | None = None
) -> list[ModelInfo]:
    """The decision models the decisions API serves, as `decisions:<model>`, when its key is set; empty otherwise."""
    if not sources.decisions_key:
        return []
    headers = {"Authorization": f"Bearer {sources.decisions_key}"}
    url = f"{sources.decisions_url.rstrip('/')}/models"
    key = ("decisions", sources.decisions_url)
    return await _fetch(key, url, headers, DECISIONS_FILTER, transport, _decisions_listed)


def system1_choices(
    console: Iterable[ModelInfo], decisions: Iterable[ModelInfo], fallback: Iterable[str] = ()
) -> list[ModelInfo]:
    """System 1's choices: the console's decision models (the `fallback` names first, and alone when the console
    listed nothing), then the decisions API's. No heuristic entry: System 1 is a model."""
    listed = {m.id: m for m in console}
    names = [m for m in dict.fromkeys(fallback) if m]
    choices = [listed.pop(name, ModelInfo(id=name, name=name)) for name in names] if listed or names else []
    choices += list(listed.values())
    seen = {m.id for m in choices}
    choices += [m for m in decisions if m.id not in seen]
    return choices


def _endpoints(env: Mapping[str, str], providers: Mapping[str, ProviderSpec]) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for name in {**PROVIDERS, **providers}:
        try:
            # The model part is a placeholder: only the base URL and the key are wanted.
            endpoints.append(resolve_endpoint(f"{name}/-", env, providers))
        except ConfigError:
            continue  # no key for it
    return endpoints


async def list_models(
    env: Mapping[str, str],
    *,
    providers: Mapping[str, ProviderSpec] | None = None,
    extra_system2: Iterable[ModelInfo] = (),
    system1: System1Sources | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Catalog:
    """Every System 2 model the harness can reach, plus those of `extra_system2` (profile names, the configured
    default) whose provider it can reach, and System 1's choices from `system1` (none when it is not given). A
    listed model keeps the provider's name; of extras with the same id, the first wins."""
    endpoints = _endpoints(env, dict(providers or {}))
    sources = system1 or System1Sources(console_models=())
    listings, console, decisions = await asyncio.gather(
        asyncio.gather(*(_provider_models(e, transport) for e in endpoints)),
        console_models(sources, transport),
        decision_models(sources, transport),
    )
    found = {model.id: model for listing in listings for model in listing}
    reachable = {endpoint.provider for endpoint in endpoints}
    for extra in extra_system2:
        if extra.id and extra.id not in found and extra.id.partition("/")[0] in reachable:
            found[extra.id] = extra
    system2 = sorted(found.values(), key=lambda m: m.id.lower())
    return Catalog(system2=system2, system1=system1_choices(console, decisions, sources.console_models))

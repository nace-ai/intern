"""First-run setup: what a new user connects before Intern can open a session, and where it is kept.

Intern runs on three models. The frontier model (System 2 in the code) starts each task and does the hard part; the
efficient model (the hand-over target) takes over for the rest; the decision model (System 1), on the decisions API
(OpenRouter) or the Nace console, decides when to hand over and keeps the run on track. The terminal UI's setup
screen (`tui/onboarding.py`) walks through them and this module does the work behind it, with no UI:

- `RECOMMENDED`: Nace's frontier and efficient pairs on one OpenRouter key, which `models --json` lists.
- `SYSTEM2_PROVIDERS`: every way to reach the frontier model, grouped as the screen lists them, each with the
  variable its key lives in, where to make one, the frontier models to suggest first and the efficient ones.
- `SYSTEM1_OPTIONS`: the decision models to pick from, by where they run: Drex on Nace AI, then OpenRouter's, then
  the one kapitsa-systemone serves on this machine.
- `check_key`: ask the provider whether a key works before keeping it, and list its models while at it.
- `save`: keep each key (the OS keychain, named in `[secrets]`; `.env` in the config home where there is no
  keychain) and write the picks to `config.toml`, keeping its comments and layout.

A key is never written to `config.toml`, and never shown back.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import tomlkit
from tomlkit.items import AbstractTable, InlineTable

from intern.config import CONFIG_FILE, DEFAULT_SYSTEM1_MODEL, ENV_FILE
from intern.models.catalog import (
    DECISIONS_PREFIX,
    KAPITSA_PREFIX,
    KAPITSA_URL,
    NACE_CONSOLE_URL,
    KeyNeeded,
    RecommendedInfo,
    System1Sources,
    console_listed,
    kapitsa_models,
    language_model,
)
from intern.models.registry import PROVIDERS, ConfigError, ProviderSpec
from intern.models.window import ANTHROPIC_API_VERSION
from intern.secret_store import SecretFailure

CHECK_TIMEOUT_S = 10.0
OPENROUTER_URL = "https://openrouter.ai/api/v1"

Kind = Literal["key", "local", "custom", "subscription"]
Role = Literal["frontier", "efficient"]


@dataclass(frozen=True)
class ProviderOption:
    """A way to reach System 2, as the setup screen lists it."""

    id: str
    """The provider's id in `provider/model` (`PROVIDERS`); for `custom`, the id the user names."""
    name: str
    blurb: str
    group: str
    kind: Kind = "key"
    key_env: str = ""
    key_url: str = ""
    """Where to make a key."""
    suggested: tuple[str, ...] = ()
    """The frontier models to list first, best first; the first one the provider lists is the default. None of the
    efficient ones: each model leads the list of one role."""
    unavailable: str = ""
    """Why it cannot be picked yet, said in full when it is chosen."""
    badge: str = ""
    """The same in a word or two, on its row."""
    handoff_suggested: str = ""
    """The cheaper sibling a run on this provider hands over to, offered first at the hand-over step."""
    efficient: tuple[str, ...] = ()
    """More efficient models to offer after `handoff_suggested`, best first."""

    @property
    def base_url(self) -> str:
        spec = PROVIDERS.get(self.id)
        return spec.base_url if spec is not None else ""


POPULAR, SUBSCRIPTIONS, MORE, LOCAL, CUSTOM = (
    "Popular",
    "Subscriptions",
    "More providers",
    "On this machine",
    "Anything else",
)

SYSTEM2_PROVIDERS: tuple[ProviderOption, ...] = (
    ProviderOption(
        "anthropic",
        "Anthropic",
        "Claude Sonnet, Opus and Haiku",
        POPULAR,
        key_env="ANTHROPIC_API_KEY",
        key_url="https://console.anthropic.com/settings/keys",
        suggested=("claude-sonnet-5-5", "claude-opus-5-5"),
        handoff_suggested="claude-haiku-5-5",
    ),
    ProviderOption(
        "openrouter",
        "OpenRouter",
        "every model on one key",
        POPULAR,
        key_env="OPENROUTER_API_KEY",
        key_url="https://openrouter.ai/settings/keys",
        suggested=("anthropic/claude-opus-5.5", "tencent/hy4", "openai/gpt-6.2-astra", "anthropic/claude-sonnet-5.5"),
        handoff_suggested="tencent/hy4-flash",
        efficient=("openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash", "anthropic/claude-haiku-5.5"),
    ),
    ProviderOption(
        "openai",
        "OpenAI",
        "GPT models",
        POPULAR,
        key_env="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        suggested=("gpt-6.2-sol", "gpt-6.1-terra"),
        handoff_suggested="gpt-6-luna",
    ),
    ProviderOption(
        "gemini",
        "Google Gemini",
        "Gemini 4 and Flash",
        POPULAR,
        key_env="GEMINI_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        suggested=("gemini-4-neon", "gemini-4-carbon"),
        handoff_suggested="gemini-3.8-flash",
    ),
    ProviderOption(
        "claude-subscription",
        "Claude Pro / Max",
        "your Claude subscription",
        SUBSCRIPTIONS,
        kind="subscription",
        badge="not allowed by Anthropic",
        unavailable=(
            "Anthropic's terms let a Claude Pro or Max subscription be used only in Anthropic's own apps (Claude, "
            "Claude Code), not in other tools, so Intern can't sign in with one. An Anthropic API key from "
            "console.anthropic.com works, and so does Claude on OpenRouter."
        ),
    ),
    ProviderOption(
        "chatgpt",
        "ChatGPT Plus / Pro",
        "your ChatGPT subscription",
        SUBSCRIPTIONS,
        kind="subscription",
        badge="coming soon",
        unavailable=(
            "Signing in with a ChatGPT subscription is not in this version of Intern yet. An OpenAI API key from "
            "platform.openai.com works now, and so does GPT on OpenRouter."
        ),
    ),
    ProviderOption(
        "copilot",
        "GitHub Copilot",
        "your Copilot subscription",
        SUBSCRIPTIONS,
        kind="subscription",
        badge="coming soon",
        unavailable=(
            "Signing in with GitHub Copilot is not in this version of Intern yet. OpenRouter reaches the same models "
            "on one key."
        ),
    ),
    ProviderOption(
        "xai",
        "xAI",
        "Grok",
        MORE,
        key_env="XAI_API_KEY",
        key_url="https://console.x.ai",
        suggested=("grok-4.7",),
        handoff_suggested="grok-build-0.1",
    ),
    ProviderOption(
        "deepseek",
        "DeepSeek",
        "DeepSeek V4 and R2",
        MORE,
        key_env="DEEPSEEK_API_KEY",
        key_url="https://platform.deepseek.com/api_keys",
        suggested=("deepseek-chat", "deepseek-reasoner"),
    ),
    ProviderOption(
        "mistral",
        "Mistral",
        "Mistral, Devstral and Codestral",
        MORE,
        key_env="MISTRAL_API_KEY",
        key_url="https://console.mistral.ai/api-keys",
        suggested=("mistral-large-latest",),
        handoff_suggested="mistral-small-latest",
    ),
    ProviderOption(
        "groq",
        "Groq",
        "open models, very fast",
        MORE,
        key_env="GROQ_API_KEY",
        key_url="https://console.groq.com/keys",
        suggested=("qwen/qwen3.8-27b", "openai/gpt-oss-120b"),
        handoff_suggested="openai/gpt-oss-20b",
    ),
    ProviderOption(
        "cerebras",
        "Cerebras",
        "open models, very fast",
        MORE,
        key_env="CEREBRAS_API_KEY",
        key_url="https://cloud.cerebras.ai",
        suggested=("qwen-3.8-27b", "gpt-oss-120b"),
    ),
    ProviderOption(
        "fireworks",
        "Fireworks AI",
        "open models",
        MORE,
        key_env="FIREWORKS_API_KEY",
        key_url="https://app.fireworks.ai/settings/users/api-keys",
        suggested=("accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b",),
        handoff_suggested="accounts/fireworks/models/deepseek-v4p1-flash-1017",
    ),
    ProviderOption(
        "together",
        "Together AI",
        "open models",
        MORE,
        key_env="TOGETHER_API_KEY",
        key_url="https://api.together.ai/settings/api-keys",
    ),
    ProviderOption(
        "deepinfra",
        "DeepInfra",
        "open models",
        MORE,
        key_env="DEEPINFRA_API_KEY",
        key_url="https://deepinfra.com/dash/api_keys",
    ),
    ProviderOption(
        "moonshot",
        "Moonshot AI",
        "Kimi",
        MORE,
        key_env="MOONSHOT_API_KEY",
        key_url="https://platform.moonshot.ai/console/api-keys",
        suggested=("kimi-k3.1-pro", "kimi-k3"),
    ),
    ProviderOption(
        "zai",
        "Z.ai",
        "GLM",
        MORE,
        key_env="ZAI_API_KEY",
        key_url="https://z.ai/manage-apikey/apikey-list",
        suggested=("glm-5.3",),
        handoff_suggested="glm-5.3-flash",
    ),
    ProviderOption(
        "minimax",
        "MiniMax",
        "MiniMax M3",
        MORE,
        key_env="MINIMAX_API_KEY",
        key_url="https://platform.minimax.io/user-center/basic-information/interface-key",
        suggested=("MiniMax-M3",),
        handoff_suggested="MiniMax-M3.1-Flash",
    ),
    ProviderOption(
        "huggingface",
        "Hugging Face",
        "Inference Providers router",
        MORE,
        key_env="HF_TOKEN",
        key_url="https://huggingface.co/settings/tokens",
    ),
    ProviderOption(
        "vercel",
        "Vercel AI Gateway",
        "many providers on one key",
        MORE,
        key_env="AI_GATEWAY_API_KEY",
        key_url="https://vercel.com/dashboard/ai-gateway",
    ),
    ProviderOption(
        "requesty",
        "Requesty",
        "many providers on one key",
        MORE,
        key_env="REQUESTY_API_KEY",
        key_url="https://app.requesty.ai/api-keys",
    ),
    ProviderOption(
        "nebius",
        "Nebius AI Studio",
        "open models",
        MORE,
        key_env="NEBIUS_API_KEY",
        key_url="https://studio.nebius.com/settings/api-keys",
    ),
    ProviderOption(
        "sambanova",
        "SambaNova",
        "open models, fast",
        MORE,
        key_env="SAMBANOVA_API_KEY",
        key_url="https://cloud.sambanova.ai/apis",
    ),
    ProviderOption("ollama", "Ollama", "models running on this machine", LOCAL, kind="local"),
    ProviderOption("lmstudio", "LM Studio", "models running on this machine", LOCAL, kind="local"),
    ProviderOption("llamacpp", "llama.cpp server", "llama-server on this machine", LOCAL, kind="local"),
    ProviderOption("vllm", "vLLM", "a vLLM server", LOCAL, kind="local"),
    ProviderOption(
        "custom",
        "Another OpenAI-compatible API",
        "any endpoint that speaks chat completions",
        CUSTOM,
        kind="custom",
    ),
)
"""Every way to reach System 2, in the order the setup screen lists them."""

PROVIDER_BY_ID = {option.id: option for option in SYSTEM2_PROVIDERS}


@dataclass(frozen=True)
class System1Option:
    """A decision model to pick for System 1."""

    id: str
    name: str
    blurb: str
    backend: Literal["decisions-api", "console", "kapitsa"]
    model: str
    """The model; empty for an option that lists its backend's models to pick from (kapitsa-systemone's one)."""
    key_env: str
    key_url: str
    only: str = ""
    """For an option that lists its backend's models: the prefix of those it offers (`nace-ai/`, Drex's)."""

    @property
    def choice(self) -> str:
        """How `--system1` and the pickers name it."""
        return system1_choice(self.backend, self.model)


def system1_choice(backend: str, model: str) -> str:
    prefix = {"decisions-api": DECISIONS_PREFIX, "kapitsa": KAPITSA_PREFIX}.get(backend, "")
    return f"{prefix}{model}"


SYSTEM1_OPTIONS: tuple[System1Option, ...] = (
    System1Option(
        "drex-console",
        "Drex Latest",
        "Nace's latest Drex",
        "console",
        DEFAULT_SYSTEM1_MODEL,
        "NACE_API_KEY",
        "https://console.nace.ai",
    ),
    System1Option(
        "drex-more",
        "More Drex models",
        "every Drex model the Nace console serves",
        "console",
        "",
        "NACE_API_KEY",
        "https://console.nace.ai",
    ),
    System1Option(
        "drex-max",
        "Drex Max",
        "#1 on Artificial Analysis",
        "decisions-api",
        "nace-ai/drex-max",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
    ),
    System1Option(
        "drex-openrouter",
        "More Drex models",
        "every Drex model OpenRouter lists",
        "decisions-api",
        "",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
        only="nace-ai/",
    ),
    System1Option(
        "decisions-other",
        "Other decision models",
        "every decision model OpenRouter lists, Jev among them",
        "decisions-api",
        "",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
    ),
    System1Option(
        "kapitsa",
        "Kapitsa",
        "Drex on this Mac with kapitsa-systemone, no key",
        "kapitsa",
        "",
        "",
        "https://github.com/nace-ai/kapitsa/blob/main/docs/drex-max.md",
    ),
)
SYSTEM1_BY_ID = {option.id: option for option in SYSTEM1_OPTIONS}


MODEL_NAMES = {
    "anthropic/claude-opus-5.5": "Claude Opus 5.5",
    "anthropic/claude-sonnet-5.5": "Claude Sonnet 5.5",
    "anthropic/claude-haiku-5.5": "Claude Haiku 5.5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "tencent/hy4": "Hy4",
    "tencent/hy4-flash": "Hy4 Flash",
    "openai/gpt-6.2-astra": "GPT-6.2 Astra",
    "openai/gpt-6-luna": "GPT-6 Luna",
    "gpt-6-luna": "GPT-6 Luna",
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "nace-ai/drex-max": "Drex Max",
    "glm-5.3": "GLM 5.3",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "drex-latest": "Drex",
}
"""How the setup screen names the models it suggests; any other model shows as its id."""


def model_name(ref: str) -> str:
    """`openrouter/tencent/hy4-flash` → `Hy4 Flash`; a model with no name here → its id without the provider."""
    if ref in MODEL_NAMES:
        return MODEL_NAMES[ref]
    provider, _, model = ref.partition("/")
    return MODEL_NAMES.get(model, model) if provider in PROVIDERS and model else ref


@dataclass(frozen=True)
class Recommendation:
    """One of Nace's frontier and efficient pairs: the frontier model (System 2), the efficient model it hands over
    to, and the decision model (System 1) suggested with it."""

    id: str
    why: str
    system2: str
    handoff: str
    system1: str
    """A `SYSTEM1_OPTIONS` id."""

    @property
    def title(self) -> str:
        return f"{model_name(self.system2)} → {model_name(self.handoff)}"

    @property
    def provider(self) -> ProviderOption:
        return PROVIDER_BY_ID[self.system2.partition("/")[0]]

    @property
    def system1_option(self) -> System1Option:
        return SYSTEM1_BY_ID[self.system1]

    @property
    def keys(self) -> tuple[KeyNeeded, ...]:
        """The keys it runs on, one per variable: System 2's, the hand-over model's and System 1's."""
        handoff = PROVIDER_BY_ID[self.handoff.partition("/")[0]]
        s1 = self.system1_option
        where = "OpenRouter" if s1.backend == "decisions-api" else "Nace AI"
        needed = [
            KeyNeeded(env=self.provider.key_env, provider=self.provider.name, url=self.provider.key_url),
            KeyNeeded(env=handoff.key_env, provider=handoff.name, url=handoff.key_url),
            KeyNeeded(env=s1.key_env, provider=where, url=s1.key_url),
        ]
        unique: dict[str, KeyNeeded] = {}
        for key in needed:
            unique.setdefault(key.env, key)
        return tuple(unique.values())

    def info(self) -> RecommendedInfo:
        return RecommendedInfo(
            id=self.id,
            title=self.title,
            why=self.why,
            system2=self.system2,
            handoff=self.handoff,
            system1=self.system1_option.choice,
            keys=list(self.keys),
        )


RECOMMENDED: tuple[Recommendation, ...] = (
    Recommendation(
        "opus-hy4",
        "Claude Opus 5.5 cracks the hard part, Hy4 Flash finishes the job",
        "openrouter/anthropic/claude-opus-5.5",
        "openrouter/tencent/hy4-flash",
        "drex-max",
    ),
    Recommendation(
        "hy4",
        "Hy4 cracks the hard part, Hy4 Flash finishes the job",
        "openrouter/tencent/hy4",
        "openrouter/tencent/hy4-flash",
        "drex-max",
    ),
    Recommendation(
        "gpt",
        "GPT-6.2 Astra cracks the hard part, GPT-6 Luna finishes the job",
        "openrouter/openai/gpt-6.2-astra",
        "openrouter/openai/gpt-6-luna",
        "drex-max",
    ),
)
"""Offered once an OpenRouter key is in, and in the app through `models --json`. Each runs on that one key."""

HANDOFF_OWN_KEYS = "Takes over once the hard part is done. Pick a fast, cheap model."
"""What the efficient-model step says."""

CONSENT_VERSION = "2026-11-18"
"""The version of `CONSENT`, sent with the sign-in (`intern free start --accept`) and recorded with the grant. A
change to the text bumps it."""
CONSENT = (
    "On free credit your prompts, the code Intern reads and the models' answers pass through Nace's servers to the "
    "model providers. We keep usage records (model, tokens, cost, time, IP) for 90 days and prompt and response text "
    "for 30 days, only to stop abuse and fix problems; only named staff can read them. Nothing is used to train "
    "models. On your own keys, nothing goes through Nace except Drex's judgments when you use a Nace key for them."
)
"""What Nace sees, shown under that heading before the free credit's sign-in."""
FREE_ABOUT = (
    "Intern thinks in two speeds. The frontier model, {frontier}, is the agent: it reads your code, plans and makes "
    "the changes. The decision model, {decision}, watches every step and keeps it on track.\n\n"
    "{decision} also decides when the hard part is over. Once the main change exists and a check has run on it, the "
    "rest of the task (fixing what the check found, tidying, the final answer) goes to a faster, cheaper model, "
    "{efficient}, in the same conversation. You'll see a line in the conversation when it happens. Your next message "
    "starts with the frontier model again.\n\n"
    "On free credit this is what makes it last: about twice as many tasks as without it."
)
"""The hand-over explained, on free credit: setup's last step and `/free`; the names are the grant's models
(`free_names`). The app's copy mirrors it."""


def free_names(system2: str, handoff: str, system1: str) -> dict[str, str]:
    """The free credit's models by their names, to fill `FREE_ABOUT`."""
    return {"frontier": model_name(system2), "efficient": model_name(handoff), "decision": model_name(system1)}


def handoff_suggestion(system2: str, listed: tuple[str, ...] = ()) -> str | None:
    """The model to offer first for System 2 to hand over to: its provider's cheaper sibling, when the provider lists
    it (or listed nothing) and it is not System 2 itself; None otherwise."""
    provider = system2.partition("/")[0]
    option = PROVIDER_BY_ID.get(provider)
    if option is None or not option.handoff_suggested or (listed and option.handoff_suggested not in listed):
        return None
    suggestion = f"{provider}/{option.handoff_suggested}"
    return suggestion if suggestion != system2 else None


def efficient_models(provider: str, system2: str, listed: tuple[str, ...] = ()) -> list[str]:
    """The models to offer on `provider` for the efficient model, as `provider/model`, its efficient ones first
    (`offered_models`). Never the frontier model `system2` itself, which may be on another provider."""
    option = PROVIDER_BY_ID.get(provider)
    models = offered_models(option, listed, "efficient") if option is not None else list(listed)
    return [ref for m in models if (ref := f"{provider}/{m}") != system2]


@dataclass(frozen=True)
class KeyCheck:
    """What a provider said of a key: whether it works, and the models it lists when it lists any."""

    ok: bool
    message: str = ""
    """Why it does not work, or why it could not be checked (then `ok` is True and the key is kept)."""
    models: tuple[str, ...] = ()


def _models(body: object, chat: bool) -> tuple[str, ...]:
    """The ids a listing has; with `chat`, only the models System 2 can chat with (no embedding or reranker). The
    console lists its decision models under `models`, by name, retired ones left out."""
    if not chat and isinstance(body, dict) and isinstance(body.get("models"), list):
        return tuple(model.id for model in console_listed(body))
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return ()
    ids = (
        model
        for item in items
        if isinstance(item, dict) and isinstance(model := item.get("id"), str) and model
        if not chat or language_model(model, item)
    )
    return tuple(model.removeprefix("models/") for model in ids)


async def check_key(
    url: str,
    key: str | None,
    *,
    anthropic: bool = False,
    params: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    chat: bool = False,
) -> KeyCheck:
    """`GET url` with the key, as the provider's model listing wants it. A 401 or 403 is a key that does not work;
    a provider that cannot be reached is not held against the key, which is kept with a word that it was not
    checked. `chat` lists only the models that answer chat."""
    headers: dict[str, str] = {}
    if key and anthropic:
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_API_VERSION}
    elif key:
        headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S, transport=transport) as client:
            response = await client.get(url, headers=headers, params=dict(params or {}))
    except httpx.HTTPError as error:
        return KeyCheck(ok=True, message=f"could not reach it to check ({type(error).__name__}); kept unchecked")
    if response.status_code in (401, 403):
        return KeyCheck(ok=False, message=f"the provider refused this key ({response.status_code})")
    if response.status_code >= 400:
        return KeyCheck(ok=True, message=f"it answered {response.status_code} to the check; kept unchecked")
    try:
        body = response.json()
    except ValueError:
        body = None
    return KeyCheck(ok=True, models=_models(body, chat))


async def check_system2(
    option: ProviderOption,
    key: str | None,
    base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> KeyCheck:
    """Whether System 2's key works on its provider (or a local server answers), with the models it lists."""
    base = (base_url or option.base_url).rstrip("/")
    if not base:
        return KeyCheck(ok=False, message="no address to reach it at")
    if option.kind == "local" or (option.kind == "custom" and not key):
        found = await check_key(f"{base}/models", None, transport=transport, chat=True)
        if found.message:
            return KeyCheck(ok=False, message=f"nothing answered at {base}; is the server running?")
        return found
    if option.id == "openrouter":
        # OpenRouter lists its models without a key; `/key` is what says whether the key works.
        found = await check_key(f"{base}/key", key, transport=transport)
        if not found.ok:
            return found
        listed = await check_key(f"{base}/models", key, transport=transport, chat=True)
        return KeyCheck(ok=True, message=found.message, models=listed.models)
    return await check_key(f"{base}/models", key, anthropic=option.id == "anthropic", transport=transport, chat=True)


async def check_system1(option: System1Option, key: str, transport: httpx.AsyncBaseTransport | None = None) -> KeyCheck:
    """Whether System 1's key works; for the decisions API, with the decision models it lists. Kapitsa takes no key:
    whether kapitsa-systemone runs, with the model it serves."""
    if option.backend == "kapitsa":
        served = await kapitsa_models(System1Sources(kapitsa_url=KAPITSA_URL), transport)
        if not served:
            return KeyCheck(ok=False, message=f"kapitsa-systemone is not running at {KAPITSA_URL}")
        return KeyCheck(ok=True, models=tuple(m.id.removeprefix(KAPITSA_PREFIX) for m in served))
    if option.backend == "console":
        return await check_key(f"{NACE_CONSOLE_URL}/models", key, transport=transport)
    found = await check_key(f"{OPENROUTER_URL}/key", key, transport=transport)
    if not found.ok:
        return found
    listed = await check_key(
        f"{OPENROUTER_URL}/models", key, params={"output_modalities": "decisions"}, transport=transport
    )
    return KeyCheck(ok=True, message=found.message, models=listed.models)


def offered_models(option: ProviderOption, listed: tuple[str, ...], role: Role) -> list[str]:
    """The models to offer on a provider for a role: everything it lists, its picks for the role first (`suggested`,
    or `handoff_suggested` and `efficient`); its picks alone when it listed none (it could not be asked)."""
    picks = option.suggested if role == "frontier" else (option.handoff_suggested, *option.efficient)
    kept = [m for m in dict.fromkeys(picks) if m and (not listed or m in listed)]
    return [*kept, *(m for m in listed if m not in kept)]


def key_found(name: str, env: Mapping[str, str]) -> bool:
    return bool(name and env.get(name))


ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def write_env_file(path: Path, name: str, value: str) -> None:
    """Set `name` in a `KEY=value` file, replacing its line if it has one; the file readable by its owner only."""
    lines = path.read_text().splitlines() if path.is_file() else []
    kept = [line for line in lines if (match := ENV_LINE.match(line)) is None or match.group(1) != name]
    kept.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write("\n".join(kept) + "\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


Stored = Literal["keychain", "env-file"]


def store_key(name: str, value: str, home: Path, env: Mapping[str, str]) -> Stored:
    """Keep a key: in the OS keychain, named in `[secrets]`, where there is one; else in the config home's `.env`,
    readable by its owner only. Either way every session reads it from there."""
    from intern.secrets_commands import store

    try:
        store(name, value, home, env)
    except (SecretFailure, ConfigError):
        write_env_file(home / ENV_FILE, name, value)
        return "env-file"
    return "keychain"


OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_KEYS_URL = f"{OPENROUTER_URL}/auth/keys"
SIGN_IN_TIMEOUT_S = 300.0
SIGNED_IN_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Intern</title>"
    "<body style='font:16px system-ui;margin:4em auto;max-width:32em;color:#0c0c09'>"
    "<h2 style='color:#7c4ad2'>{title}</h2><p>{body}</p>"
)


class SignInFailed(Exception):
    """The browser sign-in did not give a key; the message says what to do instead."""


class OpenRouterSignIn:
    """OpenRouter's browser sign-in (OAuth PKCE): a one-shot server on a loopback port takes the code OpenRouter
    sends back, and the code is exchanged for an API key the user owns and can revoke on openrouter.ai. No key to
    copy. The callback's path is random, so a stray request cannot finish someone's sign-in."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(self.verifier.encode()).digest()
        self.challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self.path = f"/callback/{uuid4().hex}"
        self.transport = transport
        self._code: asyncio.Future[str] | None = None
        self._server: asyncio.Server | None = None
        self.url = ""
        """The page to sign in on, once `start` has run."""

    async def start(self) -> str:
        """Listen for the callback; the URL to open."""
        self._code = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._answer, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        query = {
            "callback_url": f"http://127.0.0.1:{port}{self.path}",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }
        self.url = f"{OPENROUTER_AUTH_URL}?{urlencode(query)}"
        return self.url

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = (await asyncio.wait_for(reader.readline(), 10)).decode(errors="replace").split()
            target = urlsplit(request[1]) if len(request) >= 2 else None
            code = parse_qs(target.query).get("code", [""])[0] if target and target.path == self.path else ""
            if code:
                page = SIGNED_IN_PAGE.format(title="Signed in", body="You can close this tab and go back to Intern.")
                if self._code is not None and not self._code.done():
                    self._code.set_result(code)
            else:
                page = SIGNED_IN_PAGE.format(title="Not signed in", body="Go back to Intern and try again.")
            body = page.encode()
            head = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
            writer.write(head.encode() + b"Connection: close\r\n\r\n" + body)
            await writer.drain()
        except (OSError, TimeoutError, IndexError, UnicodeError):
            pass
        finally:
            writer.close()

    async def key(self, timeout_s: float = SIGN_IN_TIMEOUT_S) -> str:
        """Wait for the browser to come back, then trade its code for a key."""
        assert self._code is not None, "start() first"
        try:
            code = await asyncio.wait_for(self._code, timeout_s)
        except TimeoutError:
            raise SignInFailed("the sign-in was not finished in time; try again, or paste a key") from None
        finally:
            self.close()
        payload = {"code": code, "code_verifier": self.verifier, "code_challenge_method": "S256"}
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                response = await client.post(OPENROUTER_KEYS_URL, json=payload)
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise SignInFailed("OpenRouter could not be reached to finish signing in; try again") from None
        key = body.get("key") if isinstance(body, dict) else None
        if response.status_code >= 400 or not isinstance(key, str) or not key:
            raise SignInFailed("OpenRouter did not finish the sign-in; try again, or paste a key")
        return key

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None


@dataclass
class Setup:
    """What the setup screen settled on, to save and open the session on."""

    system2: str
    """`provider/model`."""
    system1: System1Option
    system1_model: str = ""
    """The decision model when the option leaves it to a pick from a listing (`drex-more`, `decisions-other`)."""
    keys: dict[str, str] = field(default_factory=dict)
    """Keys typed in, by variable, to keep; not those already set in the environment."""
    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    """Providers to write to `[providers.<id>]`, by id: a custom endpoint, or a local server at another address."""
    handoff: str | None = None
    """The `provider/model` runs hand over to; None for no hand-over."""
    pause_free: bool = False
    """The user's own keys were picked over the free credit: `[free] use = false`, as `/free pause` sets, so the session
    opens on these picks instead of the grant's."""

    @property
    def system1_choice(self) -> str:
        return system1_choice(self.system1.backend, self.system1_model or self.system1.model)


def _table(document: tomlkit.TOMLDocument | AbstractTable, *names: str, super_table: bool = False) -> AbstractTable:
    """The first of `names` the document has as a table (an old name for it included, and one written inline as
    `name = {...}`), else a new one under the first name: inline when its parent is, so the file stays valid."""
    for name in names:
        existing = document.get(name)
        if isinstance(existing, AbstractTable):
            return existing
    inline = isinstance(document, InlineTable)
    created = tomlkit.inline_table() if inline else tomlkit.table(is_super_table=super_table)
    document[names[0]] = created
    return created


def _write_handoff(document: tomlkit.TOMLDocument, handoff: str | None) -> None:
    """A hand-over model is `[capabilities.handoff] to`, with auto mode on; none is no `to`, and auto mode off, so
    the terminal UI does not hand over to its default either. The table's other settings stay."""
    if handoff:
        _table(_table(document, "capabilities", super_table=True), "handoff")["to"] = handoff
        if "auto" in document:
            del document["auto"]
        return
    document["auto"] = False
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, AbstractTable):
        return
    if not isinstance(table := capabilities.get("handoff"), AbstractTable):
        return
    if "to" in table:
        del table["to"]
    if not table:
        del capabilities["handoff"]
    if not capabilities:
        del document["capabilities"]


def write_config(home: Path, setup: Setup) -> Path:
    """Write the picks to `config.toml`: `system2`, the `[system1]` table's backend and model (and where its key
    is), the hand-over model, a `[providers]` entry for each provider that needs one, and the free credit paused when
    the picks replace it. Everything else in the file stays as it was."""
    path = home / CONFIG_FILE
    document = tomlkit.parse(path.read_text()) if path.is_file() else tomlkit.document()
    if "model" in document:
        del document["model"]  # the old name for system2
    document["system2"] = setup.system2  # tomlkit puts a new top-level key before the tables
    option = setup.system1
    system1 = _table(document, "system1", "decider")
    system1["backend"] = option.backend
    system1["model"] = setup.system1_model or option.model
    if "fallback_models" in system1:
        del system1["fallback_models"]
    if option.backend == "decisions-api":
        system1["base_url"] = OPENROUTER_URL
        system1["api_key_env"] = option.key_env
    elif option.backend == "console":
        system1["console_key_env"] = option.key_env
    for provider_id, spec in setup.providers.items():
        providers = _table(document, "providers")
        entry = tomlkit.inline_table() if isinstance(providers, InlineTable) else tomlkit.table()
        for key, value in spec.model_dump(exclude_defaults=True).items():
            entry[key] = value
        entry["base_url"] = spec.base_url
        providers[provider_id] = entry
    _write_handoff(document, setup.handoff)
    if setup.pause_free and isinstance(free := document.get("free"), Table):
        free["use"] = False
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(document))
    return path


@dataclass(frozen=True)
class Saved:
    config: Path
    keys: dict[str, Stored]
    """Where each key typed in was kept."""


def save(home: Path, setup: Setup, env: Mapping[str, str]) -> Saved:
    """Keep the keys, then write the picks. A key that cannot be kept stops the save before config.toml changes."""
    stored: dict[str, Stored] = {name: store_key(name, value, home, env) for name, value in setup.keys.items()}
    return Saved(config=write_config(home, setup), keys=stored)

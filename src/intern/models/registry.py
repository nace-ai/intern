"""Model references, providers and roles.

A model is named `provider/model`, e.g. `openrouter/qwen/qwen3.8-27b` or `ollama/qwen3:30b`. The
provider part picks a base URL and the environment variable holding its key; everything after the
first slash is sent to the provider unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

# System 2 is the agent model that reasons and calls tools; System 1 answers fast typed judgments
# (decisions.py). Both decide things; they differ in speed and in what they are asked.
Role = Literal["system2", "system1", "escalation"]


class ConfigError(Exception):
    """A configuration the user has to fix; the message says how."""


Api = Literal["openai", "anthropic", "responses"]

NACE_HOSTS = frozenset({"console.nace.ai", "console.test.nace.ai", "console.stage.nace.ai"})
"""The only hosts a Nace-issued credential is sent to, and only over HTTPS: a base-URL override pointing anywhere
else must never receive it."""
NACE_FREE_ENTRIES = frozenset({"NACE_FREE_REFRESH_TOKEN"})
"""The free credential's keychain entries (service `intern`, as `[secrets]` reads them): harness-only, so a
`[secrets]` entry of the same name never hands one to the agent's commands."""


@runtime_checkable
class CredentialSource(Protocol):
    """Where an endpoint's bearer comes from, asked on every request: a credential that can change while a session
    runs, such as an access token that expires. A `nace_issued` one is sent only to `NACE_HOSTS`."""

    @property
    def nace_issued(self) -> bool: ...

    async def bearer(self) -> SecretStr | None: ...

    async def invalidate(self) -> None:
        """The server refused the last bearer as expired; the next `bearer()` gives a fresh one."""
        ...

    def observe(self, headers: Mapping[str, str]) -> None:
        """The headers of a response to a request that carried the bearer (the free credit's meters ride on them)."""
        ...


@dataclass(frozen=True)
class StaticKey:
    """A key that does not change, such as one read from the environment."""

    key: SecretStr
    nace_issued: bool = False

    async def bearer(self) -> SecretStr | None:
        return self.key

    async def invalidate(self) -> None:
        return None

    def observe(self, headers: Mapping[str, str]) -> None:
        return None


def check_host(source: CredentialSource, url: str) -> None:
    """Refuse to send a Nace-issued credential to `url` unless it is a Nace console over HTTPS."""
    if not source.nace_issued:
        return
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in NACE_HOSTS or parts.port not in (None, 443):
        hosts = ", ".join(sorted(NACE_HOSTS))
        raise ConfigError(
            f"a Nace credential is sent only to {hosts} over https, not to {parts.hostname or url!r}; unset "
            "$NACE_BASE_URL and $DREX_BASE_URL, or point them at one of those"
        )


async def bearer(source: CredentialSource, url: str) -> str | None:
    """The key a request to `url` carries now; a ConfigError, before anything is sent, for a host the source may
    not go to."""
    check_host(source, url)
    key = await source.bearer()
    return key.get_secret_value() if key else None


def token_expired(status: int, body: bytes) -> bool:
    """Whether a response refuses the bearer as expired (`401`, `error.code` `token_expired`): renew it and resend."""
    if status != 401:
        return False
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return False
    return isinstance(error, dict) and error.get("code") == "token_expired"


FREE_FINAL = frozenset({"free_exhausted", "free_expired", "free_revoked", "free_paused", "model_not_in_plan"})
"""The console's refusals of the free credit that no retry and no other model of the bundle changes: the system moves
to the user's own key or stops. `free_expired` is not sent while a credit has no lifetime; it reads as
`free_exhausted`."""


def console_error(body: bytes) -> tuple[str, str | None] | None:
    """`error.code` and `error.meter` of a refusal in the console's format (`{"error": {"code", "meter"?}}`); None for
    any other body."""
    try:
        error = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict) or not isinstance(code := error.get("code"), str):
        return None
    meter = error.get("meter")
    return code, meter if isinstance(meter, str) else None


class ProviderSpec(BaseModel):
    base_url: str
    api_key_env: str | None = None
    base_url_env: str | None = None
    api: Api = "openai"
    # The assistant message field replayed reasoning goes back in; a provider rejects a field it does not know.
    reasoning_field: str = "reasoning_content"
    # A header carrying the session id, for a provider whose prompt cache is per replica: it routes a session's
    # requests to the replica that holds its prefix.
    session_header: str | None = None
    # "env": the key in `api_key_env`, read once. "nace": a credential source asked on every request
    # (`nace_credential`), which may be a token the console renews.
    credential: Literal["env", "nace"] = "env"
    # The provider's own model ids keep its name (the console serves `nace/glm-5.3`), so the whole ref is sent.
    prefixed_ids: bool = False


# Every provider speaks OpenAI chat completions except Anthropic, which uses its native Messages API (its
# OpenAI compatibility layer has no prompt caching), and OpenAI itself, which uses the Responses API (its chat
# completions refuse function tools for GPT-5.6 and later).
PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
        api="responses",
    ),
    "openrouter": ProviderSpec(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        base_url_env="OPENROUTER_BASE_URL",
    ),
    "fireworks": ProviderSpec(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        base_url_env="FIREWORKS_BASE_URL",
        # Fireworks caches prompts per replica and routes on this header (docs, "Prompt caching").
        session_header="x-session-affinity",
    ),
    # The Nace console, which serves its agent models from Fireworks and passes the affinity header through.
    "nace": ProviderSpec(
        base_url="https://console.nace.ai/v1",
        api_key_env="NACE_API_KEY",
        base_url_env="NACE_BASE_URL",
        session_header="x-session-affinity",
        credential="nace",
        prefixed_ids=True,
    ),
    "together": ProviderSpec(
        base_url="https://api.together.xyz/v1", api_key_env="TOGETHER_API_KEY", base_url_env="TOGETHER_BASE_URL"
    ),
    "deepinfra": ProviderSpec(
        base_url="https://api.deepinfra.com/v1/openai",
        api_key_env="DEEPINFRA_API_KEY",
        base_url_env="DEEPINFRA_BASE_URL",
    ),
    "groq": ProviderSpec(base_url="https://api.groq.com/openai/v1", api_key_env="GROQ_API_KEY"),
    "cerebras": ProviderSpec(
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        base_url_env="CEREBRAS_BASE_URL",
        reasoning_field="reasoning",
    ),
    "anthropic": ProviderSpec(
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        base_url_env="ANTHROPIC_BASE_URL",
        api="anthropic",
    ),
    # OpenAI-compatible APIs the setup screen offers (`onboarding.SYSTEM2_PROVIDERS`).
    "gemini": ProviderSpec(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        base_url_env="GEMINI_BASE_URL",
    ),
    "xai": ProviderSpec(base_url="https://api.x.ai/v1", api_key_env="XAI_API_KEY", base_url_env="XAI_BASE_URL"),
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY", base_url_env="DEEPSEEK_BASE_URL"
    ),
    "mistral": ProviderSpec(
        base_url="https://api.mistral.ai/v1", api_key_env="MISTRAL_API_KEY", base_url_env="MISTRAL_BASE_URL"
    ),
    "moonshot": ProviderSpec(
        base_url="https://api.moonshot.ai/v1", api_key_env="MOONSHOT_API_KEY", base_url_env="MOONSHOT_BASE_URL"
    ),
    "zai": ProviderSpec(
        base_url="https://api.z.ai/api/paas/v4", api_key_env="ZAI_API_KEY", base_url_env="ZAI_BASE_URL"
    ),
    "minimax": ProviderSpec(
        base_url="https://api.minimax.io/v1", api_key_env="MINIMAX_API_KEY", base_url_env="MINIMAX_BASE_URL"
    ),
    "huggingface": ProviderSpec(
        base_url="https://router.huggingface.co/v1", api_key_env="HF_TOKEN", base_url_env="HUGGINGFACE_BASE_URL"
    ),
    "vercel": ProviderSpec(
        base_url="https://ai-gateway.vercel.sh/v1", api_key_env="AI_GATEWAY_API_KEY", base_url_env="AI_GATEWAY_BASE_URL"
    ),
    "requesty": ProviderSpec(
        base_url="https://router.requesty.ai/v1", api_key_env="REQUESTY_API_KEY", base_url_env="REQUESTY_BASE_URL"
    ),
    "nebius": ProviderSpec(
        base_url="https://api.studio.nebius.com/v1", api_key_env="NEBIUS_API_KEY", base_url_env="NEBIUS_BASE_URL"
    ),
    "sambanova": ProviderSpec(
        base_url="https://api.sambanova.ai/v1", api_key_env="SAMBANOVA_API_KEY", base_url_env="SAMBANOVA_BASE_URL"
    ),
    "ollama": ProviderSpec(base_url="http://localhost:11434/v1", base_url_env="OLLAMA_BASE_URL"),
    "llamacpp": ProviderSpec(base_url="http://localhost:8080/v1", base_url_env="LLAMACPP_BASE_URL"),
    "lmstudio": ProviderSpec(base_url="http://localhost:1234/v1", base_url_env="LMSTUDIO_BASE_URL"),
    "vllm": ProviderSpec(base_url="http://localhost:8000/v1", base_url_env="VLLM_BASE_URL"),
}


class Endpoint(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    model: str
    base_url: str
    api_key: SecretStr | None = None
    credential: CredentialSource | None = None
    """Asked for the bearer on every request, in place of `api_key`."""
    api: Api = "openai"
    reasoning_field: str = "reasoning_content"
    session_header: str | None = None
    prefixed_ids: bool = False

    @model_validator(mode="after")
    def _credential_host(self) -> Endpoint:
        if self.credential is not None:
            check_host(self.credential, self.base_url)
        return self

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def wire_model(self) -> str:
        """The model id the provider is sent and lists."""
        return self.ref if self.prefixed_ids else self.model

    async def authorization(self) -> str | None:
        """The key the next request carries: the credential's now, or the fixed key."""
        if self.credential is not None:
            return await bearer(self.credential, self.base_url)
        return self.api_key.get_secret_value() if self.api_key else None


def nace_credential(spec: ProviderSpec, env: Mapping[str, str], *, free: bool = False) -> CredentialSource | None:
    """The credential a `credential = "nace"` provider runs on: with `free`, the free trial's sign-in to its console
    (`nace_free`), else the console key in its `api_key_env`."""
    if free:
        from intern.nace_free import free_credential

        return free_credential(spec, env)
    key = env.get(spec.api_key_env) if spec.api_key_env else None
    return StaticKey(SecretStr(key)) if key else None


def resolve_endpoint(
    ref: str,
    env: Mapping[str, str],
    extra_providers: Mapping[str, ProviderSpec] | None = None,
    *,
    free: bool = False,
) -> Endpoint:
    """Turn `provider/model` into an endpoint, reading the key and any base-URL override from `env`. With `free`, a
    `nace` endpoint runs on the free trial's sign-in instead of `$NACE_API_KEY`."""
    providers = {**PROVIDERS, **(extra_providers or {})}
    provider, sep, model = ref.partition("/")
    if not sep or not model:
        raise ConfigError(f"model {ref!r} must be written as provider/model, e.g. openrouter/qwen/qwen3.8-27b")
    spec = providers.get(provider)
    if spec is None:
        known = ", ".join(sorted(providers))
        raise ConfigError(f"unknown provider {provider!r} in {ref!r}; known providers: {known}")

    base_url = (env.get(spec.base_url_env) if spec.base_url_env else None) or spec.base_url
    credential = nace_credential(spec, env, free=free) if spec.credential == "nace" else None
    key = env.get(spec.api_key_env) if spec.api_key_env and credential is None else None
    if spec.api_key_env and not key and credential is None:
        raise ConfigError(f"{provider} needs an API key in ${spec.api_key_env}")
    return Endpoint(
        provider=provider,
        model=model,
        prefixed_ids=spec.prefixed_ids,
        base_url=base_url.rstrip("/"),
        api_key=SecretStr(key) if key else None,
        credential=credential,
        api=spec.api,
        reasoning_field=spec.reasoning_field,
        session_header=spec.session_header,
    )

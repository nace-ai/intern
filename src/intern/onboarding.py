"""First-run setup: what a new user connects before Intern can open a session, and where it is kept.

Intern needs two models: System 2, the agent, on any provider; System 1, the decision model, on the decisions API
(OpenRouter) or the Nace console. The terminal UI's setup screen (`tui/onboarding.py`) walks through both and this
module does the work behind it, with no UI:

- `SYSTEM2_PROVIDERS`: every way to reach a System 2, grouped as the screen lists them, each with the variable its
  key lives in, where to make one and the models to suggest first.
- `SYSTEM1_OPTIONS`: the decision models to pick from, Drex Max on OpenRouter first.
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
from tomlkit.items import Table

from intern.config import CONFIG_FILE, DEFAULT_SYSTEM1_MODEL, ENV_FILE
from intern.models.catalog import DECISIONS_PREFIX, NACE_CONSOLE_URL
from intern.models.registry import PROVIDERS, ConfigError, ProviderSpec
from intern.models.window import ANTHROPIC_API_VERSION
from intern.secret_store import SecretFailure

CHECK_TIMEOUT_S = 10.0
OPENROUTER_URL = "https://openrouter.ai/api/v1"

Kind = Literal["key", "local", "custom", "subscription"]


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
    """Models to list first and start on, best first; the first one the provider lists is the default."""
    unavailable: str = ""
    """Why it cannot be picked yet, said in full when it is chosen."""
    badge: str = ""
    """The same in a word or two, on its row."""

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
        suggested=("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"),
    ),
    ProviderOption(
        "openrouter",
        "OpenRouter",
        "hundreds of models on one key, and System 1 too",
        POPULAR,
        key_env="OPENROUTER_API_KEY",
        key_url="https://openrouter.ai/settings/keys",
        suggested=("qwen/qwen3.8-27b", "anthropic/claude-sonnet-5.5", "openai/gpt-5.5"),
    ),
    ProviderOption(
        "openai",
        "OpenAI",
        "GPT models",
        POPULAR,
        key_env="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        suggested=("gpt-5.5", "gpt-5.4", "gpt-5"),
    ),
    ProviderOption(
        "gemini",
        "Google Gemini",
        "Gemini Pro and Flash",
        POPULAR,
        key_env="GEMINI_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        suggested=("gemini-3-pro", "gemini-3-flash", "gemini-2.5-pro", "gemini-2.5-flash"),
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
        suggested=("grok-4", "grok-code-fast-1"),
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
        suggested=("devstral-medium-latest", "mistral-medium-latest", "devstral-small-latest"),
    ),
    ProviderOption(
        "groq",
        "Groq",
        "open models, very fast",
        MORE,
        key_env="GROQ_API_KEY",
        key_url="https://console.groq.com/keys",
        suggested=("qwen/qwen3-32b", "openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct"),
    ),
    ProviderOption(
        "cerebras",
        "Cerebras",
        "open models, very fast",
        MORE,
        key_env="CEREBRAS_API_KEY",
        key_url="https://cloud.cerebras.ai",
        suggested=("qwen-3.8-27b", "qwen-3-32b", "gpt-oss-120b"),
    ),
    ProviderOption(
        "fireworks",
        "Fireworks AI",
        "open models",
        MORE,
        key_env="FIREWORKS_API_KEY",
        key_url="https://app.fireworks.ai/settings/users/api-keys",
        suggested=("accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b",),
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
        suggested=("kimi-k2-thinking", "kimi-k2-0905-preview"),
    ),
    ProviderOption(
        "zai",
        "Z.ai",
        "GLM",
        MORE,
        key_env="ZAI_API_KEY",
        key_url="https://z.ai/manage-apikey/apikey-list",
        suggested=("glm-4.6", "glm-4.5-air"),
    ),
    ProviderOption(
        "minimax",
        "MiniMax",
        "MiniMax M2",
        MORE,
        key_env="MINIMAX_API_KEY",
        key_url="https://platform.minimax.io/user-center/basic-information/interface-key",
        suggested=("MiniMax-M2",),
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
    backend: Literal["decisions-api", "console"]
    model: str
    """The model; empty for the option that lists the decisions API's models to pick from."""
    key_env: str
    key_url: str
    recommended: bool = False

    @property
    def choice(self) -> str:
        """How `--system1` and the pickers name it."""
        return f"{DECISIONS_PREFIX}{self.model}" if self.backend == "decisions-api" else self.model


SYSTEM1_OPTIONS: tuple[System1Option, ...] = (
    System1Option(
        "drex-max",
        "Drex Max · OpenRouter",
        "Nace AI's decision model, #1 on Artificial Analysis (Nov 13, 2026)",
        "decisions-api",
        "nace-ai/drex-max",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
        recommended=True,
    ),
    System1Option(
        "drex-console",
        "Drex · Nace AI",
        "Drex from the Nace console, on a Nace key",
        "console",
        DEFAULT_SYSTEM1_MODEL,
        "NACE_API_KEY",
        "https://console.nace.ai",
    ),
    System1Option(
        "jev",
        "Jev · OpenRouter",
        "TypeSafe's decision model",
        "decisions-api",
        "~typesafe/jev-latest",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
    ),
    System1Option(
        "decisions-other",
        "More decision models · OpenRouter",
        "pick from every decision model OpenRouter lists",
        "decisions-api",
        "",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/settings/keys",
    ),
)
SYSTEM1_BY_ID = {option.id: option for option in SYSTEM1_OPTIONS}


@dataclass(frozen=True)
class KeyCheck:
    """What a provider said of a key: whether it works, and the models it lists when it lists any."""

    ok: bool
    message: str = ""
    """Why it does not work, or why it could not be checked (then `ok` is True and the key is kept)."""
    models: tuple[str, ...] = ()


def _models(body: object) -> tuple[str, ...]:
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return ()
    ids = (item.get("id") for item in items if isinstance(item, dict))
    return tuple(model.removeprefix("models/") for model in ids if isinstance(model, str) and model)


async def check_key(
    url: str,
    key: str | None,
    *,
    anthropic: bool = False,
    params: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> KeyCheck:
    """`GET url` with the key, as the provider's model listing wants it. A 401 or 403 is a key that does not work;
    a provider that cannot be reached is not held against the key, which is kept with a word that it was not
    checked."""
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
    return KeyCheck(ok=True, models=_models(body))


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
        found = await check_key(f"{base}/models", None, transport=transport)
        if found.message:
            return KeyCheck(ok=False, message=f"nothing answered at {base}; is the server running?")
        return found
    if option.id == "openrouter":
        # OpenRouter lists its models without a key; `/key` is what says whether the key works.
        found = await check_key(f"{base}/key", key, transport=transport)
        if not found.ok:
            return found
        listed = await check_key(f"{base}/models", key, transport=transport)
        return KeyCheck(ok=True, message=found.message, models=listed.models)
    return await check_key(f"{base}/models", key, anthropic=option.id == "anthropic", transport=transport)


async def check_system1(option: System1Option, key: str, transport: httpx.AsyncBaseTransport | None = None) -> KeyCheck:
    """Whether System 1's key works; for the decisions API, with the decision models it lists."""
    if option.backend == "console":
        return await check_key(f"{NACE_CONSOLE_URL}/models", key, transport=transport)
    found = await check_key(f"{OPENROUTER_URL}/key", key, transport=transport)
    if not found.ok:
        return found
    listed = await check_key(
        f"{OPENROUTER_URL}/models", key, params={"output_modalities": "decisions"}, transport=transport
    )
    return KeyCheck(ok=True, message=found.message, models=listed.models)


def suggested_models(option: ProviderOption, listed: tuple[str, ...]) -> list[str]:
    """The models to offer, suggested ones first: those the provider listed, or all of them when it listed none
    (it could not be asked). Then the rest of the listing in its order."""
    suggested = [model for model in option.suggested if not listed or model in listed]
    return [*suggested, *(model for model in listed if model not in suggested)]


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
    """The decision model when the option leaves it to a pick (`decisions-other`)."""
    keys: dict[str, str] = field(default_factory=dict)
    """Keys typed in, by variable, to keep; not those already set in the environment."""
    provider: ProviderSpec | None = None
    """A provider to write to `[providers.<id>]`: a custom endpoint, or a local server at another address."""

    @property
    def system1_choice(self) -> str:
        model = self.system1_model or self.system1.model
        return f"{DECISIONS_PREFIX}{model}" if self.system1.backend == "decisions-api" else model


def _table(document: tomlkit.TOMLDocument, *names: str) -> Table:
    """The first of `names` the document has as a table (an old name for it included), else a new one under the
    first name."""
    for name in names:
        existing = document.get(name)
        if isinstance(existing, Table):
            return existing
    created = tomlkit.table()
    document[names[0]] = created
    return created


def write_config(home: Path, setup: Setup) -> Path:
    """Write the picks to `config.toml`: `system2`, the `[system1]` table's backend and model (and where its key
    is), and a `[providers]` entry when one is needed. Everything else in the file stays as it was."""
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
    else:
        system1["console_key_env"] = option.key_env
    if setup.provider is not None:
        providers = _table(document, "providers")
        provider_id = setup.system2.partition("/")[0]
        entry = tomlkit.table()
        for key, value in setup.provider.model_dump(exclude_defaults=True).items():
            entry[key] = value
        entry["base_url"] = setup.provider.base_url
        providers[provider_id] = entry
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

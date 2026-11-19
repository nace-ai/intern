"""The free trial's credential: Intern's device sign-in to the Nace console, and the tokens it gives.

`intern free start` signs in once, in a browser, with the console's device flow (RFC 8628) as Intern's first-party
client. The refresh token is kept in the OS keychain (service `intern`, account `NACE_FREE_REFRESH_TOKEN`), else in
`free.json` in the config home; the access token, good for an hour, in `free-access.json` with its expiry, so every
harness process on the machine (the ACP server, `models --json` probes, the terminal UI) shares one. Both files are
readable by their owner only.

The console revokes a whole sign-in when a spent refresh token is presented again, so a refresh runs under an
exclusive lock on `free.lock` and re-reads the stored pair once it holds it: one process rotates, the others take
its result. Without the lock, two processes refreshing at once would log each other out.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from intern import __version__, names
from intern.models import registry
from intern.models.registry import NACE_FREE_ENTRIES, ProviderSpec
from intern.secret_store import KEYCHAIN, SecretFailure, keychain_delete, keychain_write, read_entry

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

CLIENT_ID = "intern"
"""Intern's first-party client on the console (the policy's `oauth_client_id`)."""
SCOPE = "inference"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_ENTRY = "NACE_FREE_REFRESH_TOKEN"
assert REFRESH_ENTRY in NACE_FREE_ENTRIES
STORE_FILE = "free.json"
ACCESS_FILE = "free-access.json"
LOCK_FILE = "free.lock"
INSTALL_ID_FILE = "install-id"
GRANT_TTL_S = 600.0
"""How long a session open reuses the grant read before; the meters between come from response headers."""
EARLY_S = 60.0
"""An access token this close to its expiry is renewed before it is sent, so a request never leaves with one that
expires on the way."""
SLOW_DOWN_S = 5
"""What the console's `slow_down` adds to the polling interval (RFC 8628 §3.5)."""
TIMEOUT_S = 30.0

MESSAGES = {
    "access_denied": "The sign-in was declined in the browser.",
    "expired_token": "The code expired before the sign-in was finished; start again.",
    "invalid_grant": f"The free credit's sign-in is no longer valid; sign in again with `{names.NAME} free start`.",
}


class Meter(BaseModel):
    used_usd: float
    limit_usd: float
    overdraft_usd: float = 0.0
    """What the console lets a run spend past the limit so it can finish; 0 when the console does not say."""

    def runs(self) -> bool:
        """Whether a run may start on it: more is left than the overdraft, so the run can finish."""
        return self.limit_usd - self.used_usd > self.overdraft_usd


class Meters(BaseModel):
    system1: Meter
    """Drex: System 1's judgments."""
    agent: Meter
    """System 2 and the hand-over model together."""


USED_HEADER = "x-nace-free-used"
LIMIT_HEADER = "x-nace-free-limit"


def _header_usd(value: str | None) -> dict[str, float]:
    """`system1=0.12;agent=0.5` as amounts by meter; a part that is not one is left out."""
    amounts: dict[str, float] = {}
    for part in (value or "").split(";"):
        name, _, amount = part.partition("=")
        with contextlib.suppress(ValueError):
            amounts[name.strip()] = float(amount)
    return amounts


class HandoffTuning(BaseModel):
    threshold: float
    streak: int
    min_turn: int


class Bundle(BaseModel):
    """The models a system runs on while it uses free credit, and the caps it keeps to."""

    system2: str
    handoff: str
    system1: str
    handoff_tuning: HandoffTuning | None = None
    window_tokens: int | None = None
    max_output_tokens: int | None = None


class FreeGrant(BaseModel):
    """What `GET /v1/free/grant` says about the signed-in account's grant."""

    state: str
    """`active`, `paused` (free inference is paused) or `exhausted` (every meter spent)."""
    meters: Meters
    bundle: Bundle


class FreeStatus(BaseModel):
    """What `GET /v1/free/status` says, with no sign-in: whether the free trial takes new sign-ins and serves calls."""

    grants: str
    """`open`, `invite_only` or `paused`."""
    inference: str
    """`open` or `paused`."""
    message: str | None = None


class SignInCode(BaseModel):
    """What the user confirms in the browser."""

    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int

    @property
    def url(self) -> str:
        return self.verification_uri_complete or self.verification_uri


class Tokens(BaseModel):
    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: int = 3600


class Refusal(BaseModel):
    """Why the console gave no tokens or no grant: its `error.code` (`not_eligible` with a `reason`, `free_revoked`),
    an OAuth error (`access_denied`, `expired_token`, `invalid_grant`), or `unavailable` when it did not say."""

    code: str
    reason: str | None = None
    message: str = ""


class ConsoleUnavailable(ConnectionError):
    """The console did not answer a token refresh (a 5xx, a rate limit): the stored tokens are kept, and the request
    that needed one fails as a network error does, which the request paths retry."""


def refusal(response: httpx.Response) -> Refusal:
    """A refusal in the console's format (`{"error": {"code", "reason", "message"}}`) or OAuth's
    (`{"error": "..."}`)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        reason = error.get("reason")
        return Refusal(
            code=str(error.get("code") or "unavailable"),
            reason=reason if isinstance(reason, str) else None,
            message=str(error.get("message") or ""),
        )
    if isinstance(error, str) and isinstance(body, dict):
        described = body.get("error_description")
        return Refusal(code=error, message=described if isinstance(described, str) else MESSAGES.get(error, ""))
    return Refusal(code="unavailable", message=f"The console answered HTTP {response.status_code}.")


def console_url(base_url: str) -> str:
    """The console's origin, where `/oauth/*` lives, from the `nace` provider's base URL (`…/v1`)."""
    return base_url.rstrip("/").removesuffix("/v1")


def install_id(home: Path) -> str:
    """A random id for this install, kept in the config home and sent with each sign-in for the grant record."""
    path = home / INSTALL_ID_FILE
    with contextlib.suppress(OSError):
        if saved := path.read_text().strip():
            return saved
    made = uuid.uuid4().hex
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(made + "\n")
    return made


_NACE_ISSUED = registry.StaticKey(SecretStr(""), nace_issued=True)


def _check_host(url: str) -> None:
    """A sign-in, and the tokens it gives, go only where a Nace-issued credential may go."""
    registry.check_host(_NACE_ISSUED, url)


class DeviceSignIn:
    """The console's device flow: `start()` asks for a code to confirm in the browser, `wait()` polls until the user
    has (tokens) or the console refuses (a `Refusal`), at the interval it asks for."""

    def __init__(
        self,
        console: str,
        *,
        consent_version: str,
        install_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.console = console.rstrip("/")
        self.consent_version = consent_version
        self.install_id = install_id
        self._transport = transport
        self._sleep = sleep
        self._device_code = ""
        self._interval = 5
        self._deadline = 0.0

    async def start(self) -> SignInCode | Refusal:
        url = f"{self.console}/oauth/device_authorization"
        _check_host(url)
        form = {
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "client_version": __version__,
            "install_id": self.install_id,
            "consent_version": self.consent_version,
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as client:
            response = await client.post(url, data=form, headers=names.CLIENT_HEADERS)
        if response.status_code != 200:
            return refusal(response)
        body = response.json()
        self._device_code = str(body["device_code"])
        self._interval = int(body.get("interval", 5))
        code = SignInCode.model_validate(body)
        self._deadline = time.monotonic() + code.expires_in
        return code

    async def wait(self) -> Tokens | Refusal:
        assert self._device_code, "start() first"
        url = f"{self.console}/oauth/token"
        form = {"grant_type": DEVICE_GRANT, "device_code": self._device_code, "client_id": CLIENT_ID}
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self._transport) as client:
            while time.monotonic() < self._deadline:
                await self._sleep(self._interval)
                try:
                    response = await client.post(url, data=form, headers=names.CLIENT_HEADERS)
                except httpx.TransportError:
                    continue  # a dropped connection does not end a sign-in the user may be finishing
                if response.status_code == 200:
                    return Tokens.model_validate(response.json())
                refused = refusal(response)
                if refused.code == "slow_down":
                    self._interval += SLOW_DOWN_S
                elif refused.code != "authorization_pending":
                    return refused
        return Refusal(code="expired_token", message=MESSAGES["expired_token"])


class StoredAccess(BaseModel):
    access_token: SecretStr
    expires_at: float
    console: str


def _write_private(path: Path, data: dict[str, Any]) -> None:
    """Replace `path` with `data` as JSON, readable by its owner only, in one step: a reader sees the old file or
    the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        json.dump(data, out)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _lock(descriptor: int) -> None:
    if sys.platform == "win32":
        while True:
            try:
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)  # gives up after ten tries a second apart
                return
            except OSError:
                continue
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


class FreeStore:
    """Where the free credential's tokens are kept in a config home. `free.json` says which console they belong to
    and where the refresh token is: in it, or in the keychain."""

    def __init__(self, home: Path, env: Mapping[str, str]) -> None:
        self.home = home
        self.env = env
        self.store_path = home / STORE_FILE
        self.access_path = home / ACCESS_FILE
        self.lock_path = home / LOCK_FILE
        self._seen: tuple[object, ...] | None = None
        self._secrets: list[SecretStr] = []

    def _saved(self, console: str | None) -> dict[str, Any] | None:
        saved = _read_json(self.store_path)
        if saved is None or (console is not None and saved.get("console") != console):
            return None
        return saved

    def signed_in(self, console: str | None = None) -> bool:
        """Whether there is a sign-in (to `console`, when given) to refresh."""
        saved = self._saved(console)
        return saved is not None and (saved.get("refresh") == KEYCHAIN or bool(saved.get("refresh_token")))

    def console(self) -> str | None:
        saved = self._saved(None)
        return str(saved["console"]) if saved and saved.get("console") else None

    def read_refresh(self, console: str | None = None) -> SecretStr | None:
        saved = self._saved(console)
        if saved is None:
            return None
        if saved.get("refresh") == KEYCHAIN:
            try:
                return read_entry(REFRESH_ENTRY, KEYCHAIN, self.env)
            except SecretFailure:
                return None
        token = saved.get("refresh_token")
        return SecretStr(token) if isinstance(token, str) and token else None

    def read_access(self, console: str | None = None) -> StoredAccess | None:
        data = _read_json(self.access_path)
        try:
            access = StoredAccess.model_validate(data) if data else None
        except ValidationError:
            return None
        return access if access and (console is None or access.console == console) else None

    def save(self, tokens: Tokens, *, console: str, consent_version: str | None = None) -> StoredAccess:
        """Keep a new pair: the refresh token first, so a crash between the two writes leaves a usable sign-in."""
        saved = self._saved(console) or {}
        record: dict[str, Any] = {"console": console}
        if consent := consent_version or saved.get("consent_version"):
            record["consent_version"] = consent
        if tokens.refresh_token is not None:
            value = tokens.refresh_token.get_secret_value()
            try:
                keychain_write(REFRESH_ENTRY, value, self.env)
                record["refresh"] = KEYCHAIN
            except SecretFailure:
                record["refresh_token"] = value
        else:
            record.update({k: v for k, v in saved.items() if k in ("refresh", "refresh_token")})
        _write_private(self.store_path, record)
        expires_at = time.time() + tokens.expires_in
        record = {"access_token": tokens.access_token.get_secret_value(), "expires_at": expires_at, "console": console}
        _write_private(self.access_path, record)
        return StoredAccess(access_token=tokens.access_token, expires_at=expires_at, console=console)

    def forget(self) -> None:
        saved = _read_json(self.store_path)
        if saved is not None and saved.get("refresh") == KEYCHAIN:
            with contextlib.suppress(SecretFailure):
                keychain_delete(REFRESH_ENTRY, self.env)
        for path in (self.access_path, self.store_path):
            path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Hold `free.lock` exclusively, across every process on the machine that uses this home."""
        self.home.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(descriptor)
            try:
                yield
            finally:
                _unlock(descriptor)
        finally:
            os.close(descriptor)

    def secrets(self) -> list[SecretStr]:
        """The tokens stored now, to redact. Read again only when a token file has changed: every rotation writes
        `free-access.json` anew, after the refresh token."""

        def stamp(path: Path) -> object:
            try:
                found = path.stat()
            except OSError:
                return None
            return (found.st_ino, found.st_mtime_ns, found.st_size)

        seen = (stamp(self.store_path), stamp(self.access_path))
        if seen != self._seen:
            access, refresh = self.read_access(), self.read_refresh()
            self._secrets = [token for token in (access and access.access_token, refresh) if token]
            self._seen = seen
        return self._secrets

    def redactor(self, then: Callable[[str], str] | None) -> Callable[[str], str]:
        """`then`, and the tokens as they are when each text is redacted: they rotate while a session runs."""
        from intern.secrets import SecretRedactor

        def redact(text: str) -> str:
            text = then(text) if then is not None else text
            current = self.secrets()
            return SecretRedactor(current).redact(text) if current else text

        return redact


class FreeCredential:
    """The free access token, as `registry.CredentialSource`: renewed from the refresh token when it is about to
    expire or the console refuses it, under the store's lock."""

    nace_issued = True

    def __init__(self, store: FreeStore, console: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.store = store
        self.console = console.rstrip("/")
        self.refused: Refusal | None = None
        """Why the console last refused a refresh; the sign-in is forgotten then."""
        self._transport = transport
        self._access: StoredAccess | None = None
        self._rejected: str | None = None
        self._lock = threading.Lock()
        self._grant: tuple[float, FreeGrant] | None = None
        self.meters: Meters | None = None
        """The meters as the console last gave them: the grant's, then each response's `x-nace-free-*` headers."""

    def observe(self, headers: Mapping[str, str]) -> None:
        """Take the meters a console response carries (`x-nace-free-used`, `x-nace-free-limit`, in USD)."""
        used, limit = _header_usd(headers.get(USED_HEADER)), _header_usd(headers.get(LIMIT_HEADER))
        if not used and not limit:
            return
        meters = self.meters or Meters(system1=Meter(used_usd=0, limit_usd=0), agent=Meter(used_usd=0, limit_usd=0))
        update: dict[str, Meter] = {}
        for name in ("system1", "agent"):
            meter: Meter = getattr(meters, name)
            changed = {k: v for k, v in (("used_usd", used.get(name)), ("limit_usd", limit.get(name))) if v is not None}
            update[name] = meter.model_copy(update=changed)
        self.meters = meters.model_copy(update=update)

    async def grant(self, transport: httpx.AsyncBaseTransport | None = None) -> FreeGrant | Refusal:
        """The grant, read again once GRANT_TTL_S has passed; its meters are the latest the console gave."""
        if self._grant is not None and time.monotonic() - self._grant[0] < GRANT_TTL_S:
            grant = self._grant[1]
        else:
            found = await fetch_grant(self, transport)
            if not isinstance(found, FreeGrant):
                self._grant = None
                return found
            self._grant, self.meters = (time.monotonic(), found), found.meters
            grant = found
        return grant.model_copy(update={"meters": self.meters}) if self.meters is not None else grant

    def _usable(self, access: StoredAccess | None) -> bool:
        return (
            access is not None
            and access.expires_at - EARLY_S > time.time()
            and access.access_token.get_secret_value() != self._rejected
        )

    async def bearer(self) -> SecretStr | None:
        access = self._access
        if not self._usable(access):
            access = self._access = await asyncio.to_thread(self._fresh)
        return access.access_token if access else None

    async def invalidate(self) -> None:
        if self._access is not None:
            self._rejected = self._access.access_token.get_secret_value()
        self._access = None

    def forget(self) -> None:
        with self._lock, self.store.locked():
            self.store.forget()
            self._access = None

    def _fresh(self) -> StoredAccess | None:
        with self._lock:
            stored = self.store.read_access(self.console)
            if self._usable(stored):
                return stored
            with self.store.locked():
                stored = self.store.read_access(self.console)  # another process may have renewed it meanwhile
                if self._usable(stored):
                    return stored
                refresh = self.store.read_refresh(self.console)
                return self._refresh(refresh) if refresh is not None else None

    def _refresh(self, refresh: SecretStr) -> StoredAccess | None:
        url = f"{self.console}/oauth/token"
        registry.check_host(self, url)
        form = {"grant_type": "refresh_token", "refresh_token": refresh.get_secret_value(), "client_id": CLIENT_ID}
        with httpx.Client(timeout=TIMEOUT_S, transport=self._transport) as client:
            response = client.post(url, data=form, headers=names.CLIENT_HEADERS)
        if response.status_code == 200:
            return self.store.save(Tokens.model_validate(response.json()), console=self.console)
        if response.status_code in (400, 401, 403):
            self.refused = refusal(response)
            self.store.forget()
            return None
        raise ConsoleUnavailable(f"the console answered HTTP {response.status_code} to a token refresh")

    async def sign_out(self) -> bool:
        """Revoke the sign-in on the console and forget it here; whether the console confirmed. Forgotten either way."""

        def revoke() -> bool:
            with self._lock, self.store.locked():
                refresh = self.store.read_refresh(self.console)
                revoked = False
                if refresh is not None:
                    url = f"{self.console}/oauth/revoke"
                    registry.check_host(self, url)
                    form = {
                        "token": refresh.get_secret_value(),
                        "token_type_hint": "refresh_token",
                        "client_id": CLIENT_ID,
                    }
                    client = httpx.Client(timeout=TIMEOUT_S, transport=self._transport)
                    with contextlib.suppress(httpx.HTTPError), client:
                        revoked = client.post(url, data=form, headers=names.CLIENT_HEADERS).status_code == 200
                self.store.forget()
                self._access = None
                return revoked

        return await asyncio.to_thread(revoke)


async def fetch_grant(
    credential: FreeCredential, transport: httpx.AsyncBaseTransport | None = None
) -> FreeGrant | Refusal:
    """The signed-in account's grant. A revoked grant forgets the sign-in."""
    url = f"{credential.console}/v1/free/grant"
    signed_out = Refusal(code="signed_out", message="Not signed in to the free credit.")
    async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:

        async def send() -> httpx.Response | None:
            key = await registry.bearer(credential, url)
            if key is None:
                return None
            return await client.get(url, headers={**names.CLIENT_HEADERS, "Authorization": f"Bearer {key}"})

        response = await send()
        if response is not None and registry.token_expired(response.status_code, response.content):
            await credential.invalidate()
            response = await send()
    if response is None:
        return credential.refused or signed_out
    if response.status_code == 200:
        return FreeGrant.model_validate(response.json())
    refused = refusal(response)
    if refused.code == "free_revoked":
        credential.forget()
    return refused


async def fetch_status(console: str, transport: httpx.AsyncBaseTransport | None = None) -> FreeStatus | None:
    """Whether the free trial is open; None when the console could not be asked."""
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            response = await client.get(f"{console.rstrip('/')}/v1/free/status", headers=names.CLIENT_HEADERS)
        return FreeStatus.model_validate(response.json()) if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, ValidationError):
        return None


_credentials: dict[tuple[Path, str], FreeCredential] = {}


def free_credential(spec: ProviderSpec, env: Mapping[str, str]) -> FreeCredential | None:
    """The free credential for the `nace` provider's console, one per config home and console in a process, so
    System 1 and System 2 share one access token; None when that console has no sign-in here."""
    from intern.config import home_dir

    base = (env.get(spec.base_url_env) if spec.base_url_env else None) or spec.base_url
    console, home = console_url(base), home_dir(env)
    credential = _credentials.get((home, console))
    if credential is None:
        credential = _credentials[(home, console)] = FreeCredential(FreeStore(home, env), console)
    return credential if credential.store.signed_in(console) else None

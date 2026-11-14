"""Out-of-band interaction testing (OAST) over the interactsh protocol, native to this harness:

    POST /register   {public-key, secret-key, correlation-id}   (RSA-2048, SPKI DER in a PEM wrap, base64)
    payload          <correlation-id 20><nonce 13>.<server host>  (any DNS / HTTP / SMTP / LDAP … hit)
    GET  /poll       ?id=<cid>&secret=<secret> → {data[], aes_key, extra[], tlddata[]}
                     aes_key: RSA-OAEP(SHA-256); data: AES-256-CTR (CFB on older servers), IV first
    POST /deregister {correlation-id, secret-key}

The public servers (oast.pro, oast.fun, …) are run by ProjectDiscovery: callbacks — and anything a
payload exfiltrates — pass through them. Set INTERACTSH_SERVER (+ INTERACTSH_TOKEN) for a
self-hosted server on confidential engagements. The session (it holds the private key) lives under
the engagement's private dir; payloads and recorded callbacks under tools/oast/.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import secrets as pysecrets
import struct
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from intern.engagement.models import EvidenceRecord
from intern.engagement.store import EngagementStore
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

PUBLIC_SERVERS = ["oast.pro", "oast.live", "oast.site", "oast.online", "oast.fun", "oast.me"]
HTTP_TIMEOUT_S = 10.0
#: The cryptography package does the RSA-OAEP keygen and the AES poll decryption; it is optional.
CRYPTO_INSTALL_HINT = "install the cryptography package (e.g. `uv add cryptography` or pip install cryptography)"


class OastError(RuntimeError):
    """An OAST call failed; the message is the tool's error text."""


class OastUnavailable(OastError):
    """The optional cryptography dependency is missing."""


class SessionExpired(OastError):
    """The server forgot this registration; a new payload starts a new session."""

    expired = True


class OastConfig(BaseModel):
    servers: list[str]
    token: str | None = None
    self_hosted: bool = False


def oast_config(env: dict[str, str] | None = None) -> OastConfig:
    environ = env if env is not None else dict(os.environ)
    custom = [s.strip() for s in environ.get("INTERACTSH_SERVER", "").split(",") if s.strip()]
    return OastConfig(
        servers=custom or list(PUBLIC_SERVERS),
        token=environ.get("INTERACTSH_TOKEN") or None,
        self_hosted=bool(custom),
    )


class OastSession(BaseModel):
    server: str  # base URL that accepted the registration
    host: str  # payload domain suffix
    correlation_id: str
    secret_key: str
    private_key: str  # PKCS#8 PEM
    token: str | None = None
    created: int


class Interaction(BaseModel):
    """One callback the server recorded, exactly as the poll delivers it."""

    model_config = ConfigDict(populate_by_name=True)

    protocol: str
    unique_id: str = Field(default="", alias="unique-id")
    full_id: str = Field(default="", alias="full-id")
    q_type: str | None = Field(default=None, alias="q-type")
    raw_request: str | None = Field(default=None, alias="raw-request")
    raw_response: str | None = Field(default=None, alias="raw-response")
    smtp_from: str | None = Field(default=None, alias="smtp-from")
    remote_address: str = Field(default="unknown", alias="remote-address")
    timestamp: str = ""


# xid's alphabet (base32hex, lower-case): the server validates correlation ids against it.
XID = "0123456789abcdefghijklmnopqrstuv"
ZBASE32 = "ybndrfg8ejkmcpqxot1uwisza345h769"


def _base32(data: bytes, alphabet: str) -> str:
    out = []
    bits = 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            out.append(alphabet[(value >> (bits - 5)) & 31])
            bits -= 5
    if bits > 0:
        out.append(alphabet[(value << (5 - bits)) & 31])
    return "".join(out)


def correlation_id(now_ms: int | None = None) -> str:
    """A 20-char xid: 4-byte timestamp, then random bytes (no machine/pid fingerprint)."""
    stamp = int(now_ms if now_ms is not None else time.time() * 1000) // 1000
    raw = struct.pack(">I", stamp) + pysecrets.token_bytes(8)
    return _base32(raw, XID)[:20]


def nonce(length: int = 13) -> str:
    return _base32(pysecrets.token_bytes(length), ZBASE32)[:length]


def payload_domain(session: OastSession | Any, tag: str | None = None) -> str:
    """A fresh payload domain; an optional tag is prefixed as a sub-label (it returns in full-id)."""
    label = re.sub(r"[^a-z0-9-]+", "-", tag.lower()).strip("-")[:40] if tag else ""
    cid, host = session.correlation_id, session.host
    return f"{label + '.' if label else ''}{cid}{nonce()}.{host}"


def _require_crypto() -> None:
    try:
        import cryptography  # noqa: F401
    except ModuleNotFoundError as error:
        raise OastUnavailable(
            f"OAST needs the cryptography package for RSA-OAEP keygen and AES poll decryption — {CRYPTO_INSTALL_HINT}"
        ) from error


def generate_keypair() -> tuple[str, str]:
    """(private PKCS#8 PEM, public key for the register body: base64 of an SPKI-wrapping PEM)."""
    _require_crypto()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    spki = private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    body = base64.b64encode(spki).decode()
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    public_pem = f"-----BEGIN RSA PUBLIC KEY-----\n{wrapped}\n-----END RSA PUBLIC KEY-----\n"
    return private_pem, base64.b64encode(public_pem.encode()).decode()


def _oaep_padding():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _cipher_modes() -> Any:
    """The cipher modes; CFB moved to ``decrepit`` in newer cryptography releases (older servers
    still send CFB records, so the fallback stays)."""
    from cryptography.hazmat.primitives.ciphers import modes

    try:
        from cryptography.hazmat.decrepit.ciphers.modes import CFB
    except ImportError:
        CFB = modes.CFB  # type: ignore[assignment]
    return type("Modes", (), {"CTR": modes.CTR, "CFB": CFB})()


def encrypt_record(public_pem: str, record: dict[str, Any], mode: str = "ctr") -> tuple[str, str]:
    """What the server does to a poll record: (aes_key, data) — AES key under RSA-OAEP(SHA-256),
    body AES-256 with the 16-byte IV first. Test helper; the client only ever decrypts."""
    _require_crypto()
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    aes_key, iv = pysecrets.token_bytes(32), pysecrets.token_bytes(16)
    cipher_modes = _cipher_modes()
    mode_obj = cipher_modes.CTR(iv) if mode == "ctr" else cipher_modes.CFB(iv)
    encryptor = Cipher(algorithms.AES(aes_key), mode_obj).encryptor()
    data = iv + encryptor.update(json.dumps(record).encode()) + encryptor.finalize()
    public = cast(RSAPublicKey, load_pem_public_key(public_pem.encode()))
    enc_key = public.encrypt(aes_key, _oaep_padding())
    return base64.b64encode(enc_key).decode(), base64.b64encode(data).decode()


def decrypt_record(private_key_pem: str, aes_key_b64: str, data_b64: str) -> Interaction | None:
    """Decrypt one poll record: AES key via RSA-OAEP(SHA-256), body AES-256-CTR (CFB fallback), IV first."""
    _require_crypto()
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private = cast(RSAPrivateKey, load_pem_private_key(private_key_pem.encode(), password=None))
    key = private.decrypt(base64.b64decode(aes_key_b64), _oaep_padding())
    raw = base64.b64decode(data_b64)
    if len(raw) < 16:
        return None
    cipher_modes = _cipher_modes()
    for cipher_mode in (cipher_modes.CTR(raw[:16]), cipher_modes.CFB(raw[:16])):
        try:
            decryptor = Cipher(algorithms.AES(key), cipher_mode).decryptor()
            text = (decryptor.update(raw[16:]) + decryptor.finalize()).decode().strip()
            return Interaction.model_validate_json(text)
        except (ValueError, UnicodeDecodeError):
            continue  # try the other mode
    return None


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": token} if token else {}


async def register(config: OastConfig, client: httpx.AsyncClient) -> OastSession:
    private_pem, public_b64 = generate_keypair()
    secret_key = str(uuid.uuid4())
    cid = correlation_id()
    body = {"public-key": public_b64, "secret-key": secret_key, "correlation-id": cid}
    errors: list[str] = []
    order = list(config.servers)
    random.shuffle(order)
    for server in order:
        bases = [server.rstrip("/")] if re.match(r"^https?://", server) else [f"https://{server}", f"http://{server}"]
        for base in bases:
            try:
                response = await client.post(f"{base}/register", json=body, headers=_auth_headers(config.token))
            except httpx.HTTPError as error:
                errors.append(f"{base}: {error}")
                continue
            if response.status_code == 401:
                errors.append(f"{base}: token rejected (INTERACTSH_TOKEN)")
                continue
            if response.status_code >= 400:
                errors.append(f"{base}: HTTP {response.status_code}")
                continue
            host = re.sub(r"^https?://", "", base).rstrip("/")
            return OastSession(
                server=base,
                host=host,
                correlation_id=cid,
                secret_key=secret_key,
                private_key=private_pem,
                token=config.token,
                created=int(time.time() * 1000),
            )
    raise OastError(f"no interactsh server accepted the registration — {'; '.join(errors)}")


async def poll(session: OastSession, client: httpx.AsyncClient) -> list[Interaction]:
    response = await client.get(
        f"{session.server}/poll",
        params={"id": session.correlation_id, "secret": session.secret_key},
        headers=_auth_headers(session.token),
    )
    if response.status_code == 401:
        raise OastError("interactsh: token rejected (INTERACTSH_TOKEN)")
    if response.status_code >= 400:
        text = response.text
        if re.search(r"could not get correlation-id|not found", text, re.IGNORECASE):
            raise SessionExpired("interactsh: this session expired on the server — start a new one")
        raise OastError(f"interactsh poll failed: HTTP {response.status_code} {text[:160]}")
    body = response.json()
    out: list[Interaction] = []
    for datum in body.get("data") or []:
        if body.get("aes_key"):
            record = decrypt_record(session.private_key, body["aes_key"], datum)
            if record is not None:
                out.append(record)
    for extra in [*(body.get("extra") or []), *(body.get("tlddata") or [])]:
        try:
            out.append(Interaction.model_validate_json(extra))
        except ValueError:
            continue  # skip malformed
    return out


async def deregister(session: OastSession, client: httpx.AsyncClient) -> None:
    try:
        await client.post(
            f"{session.server}/deregister",
            json={"correlation-id": session.correlation_id, "secret-key": session.secret_key},
            headers=_auth_headers(session.token),
        )
    except httpx.HTTPError:
        return


# -- the tool's engagement-side bookkeeping -------------------------------------------------


class PayloadRecord(BaseModel):
    domain: str
    tag: str | None = None
    purpose: str | None = None
    vuln_id: str | None = None
    ts: int


class OastStore:
    """Payloads and recorded callbacks sit with the other tool output; the session (private key)
    stays under private/."""

    def __init__(self, store: EngagementStore) -> None:
        self.dir = store.tool_dir("oast")
        self.session_file = store.private_dir / "oast" / "session.json"
        self.payloads_file = self.dir / "payloads.json"
        self.log_file = self.dir / "interactions.jsonl"

    def read_payloads(self) -> list[PayloadRecord]:
        try:
            raw = json.loads(self.payloads_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [PayloadRecord.model_validate(item) for item in raw] if isinstance(raw, list) else []

    def write_payloads(self, payloads: list[PayloadRecord]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.payloads_file.write_text(
            json.dumps([p.model_dump(exclude_none=True) for p in payloads], indent=2), encoding="utf-8"
        )

    def read_session(self) -> OastSession | None:
        try:
            return OastSession.model_validate_json(self.session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def write_session(self, session: OastSession) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(session.model_dump_json(indent=2), encoding="utf-8")
        self.session_file.chmod(0o600)

    def drop_session(self) -> None:
        self.session_file.unlink(missing_ok=True)

    def append_interactions(self, interactions: list[Interaction]) -> None:
        if not interactions:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as fh:
            for interaction in interactions:
                fh.write(interaction.model_dump_json(by_alias=True) + "\n")

    def recorded_interactions(self) -> list[Interaction]:
        try:
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in filter(None, lines):
            try:
                out.append(Interaction.model_validate_json(line))
            except ValueError:
                continue
        return out


def payload_for(interaction: Interaction, payloads: list[PayloadRecord]) -> PayloadRecord | None:
    """Which recorded payload an interaction belongs to (by its unique subdomain label)."""
    identifier = (interaction.full_id or interaction.unique_id).lower()
    for payload in payloads:
        label = next((part for part in payload.domain.split(".") if len(part) == 33), None)
        if label and label in identifier:
            return payload
    return None


def describe(interaction: Interaction, payload: PayloadRecord | None = None) -> str:
    first = (interaction.raw_request or "").split("\n")[0].strip()[:160]
    kind = interaction.protocol.upper() + (f" {interaction.q_type}" if interaction.q_type else "")
    stamp = interaction.timestamp[:19].replace("T", " ") if interaction.timestamp else "?"
    attribution = ""
    if payload is not None:
        parts = [payload.tag or "untagged"]
        if payload.vuln_id:
            parts.append(payload.vuln_id)
        if payload.purpose:
            parts.append(payload.purpose)
        attribution = f" [{'; '.join(parts)}]"
    line = f"{stamp} {kind:<8} from {interaction.remote_address} → {interaction.full_id}{attribution}"
    return f"{line}\n    {first}" if first else line


class OastArgs(BaseModel):
    action: Literal["payload", "poll", "attach", "status", "describe", "selftest"]
    tag: str | None = None
    purpose: str | None = None
    vuln_id: str | None = None
    wait_s: float | None = Field(default=None, description="poll: keep polling up to this many seconds (max 120)")


class OastTool(Tool):
    """Out-of-band proof for blind bugs: hand the target a unique callback domain, then see whether
    anything called it."""

    name = "oast"
    description = (
        "Out-of-band proof for blind bugs — blind SSRF, XXE, command/template injection, deserialization, "
        "DNS exfiltration, blind XSS. action=payload [tag, purpose, vuln_id]: a unique callback domain (use it "
        "as a host name, URL, e-mail domain or in a DNS lookup; the tag comes back with each hit). "
        "action=poll [wait_s ≤120, tag]: any DNS/HTTP/SMTP/LDAP interactions so far (kept in the engagement, so "
        "late callbacks are not lost). action=attach vuln_id [tag]: save the matching interactions as evidence "
        "for that finding. action=describe [tag]: the recorded interactions without polling. action=status: "
        "server and counts. action=selftest: local crypto roundtrip check. A DNS hit often comes from the "
        "target's resolver (proves the lookup happened); an HTTP hit from the target's egress IP proves a "
        "server-side request."
    )
    Args = OastArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "network"})

    def __init__(self, store: EngagementStore, client: httpx.AsyncClient | None = None) -> None:
        self._store = store
        self._client = client
        self._oast = OastStore(store)

    async def run(self, args: OastArgs, ctx: ToolContext) -> ToolOutput:
        config = oast_config()
        payloads = self._oast.read_payloads()
        session = self._oast.read_session()
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        try:
            return await self._run(args, ctx, config, payloads, session, client)
        except OastUnavailable as error:
            return fail(str(error), CRYPTO_INSTALL_HINT + ", then retry the same action.")
        except SessionExpired:
            self._oast.drop_session()
            return fail(
                "the interactsh session expired on the server",
                "create a new payload (a new session starts) and re-plant it.",
            )
        except OastError as error:
            return fail(str(error), "Check INTERACTSH_SERVER/INTERACTSH_TOKEN, or retry once the network allows.")
        finally:
            if own_client:
                await client.aclose()

    async def _ensure_session(
        self, config: OastConfig, session: OastSession | None, client: httpx.AsyncClient
    ) -> OastSession:
        if session is not None:
            return session
        session = await register(config, client)
        self._oast.write_session(session)
        self._store.log("oast", f"interactsh session on {session.host}")
        return session

    async def _run(
        self,
        args: OastArgs,
        ctx: ToolContext,
        config: OastConfig,
        payloads: list[PayloadRecord],
        session: OastSession | None,
        client: httpx.AsyncClient,
    ) -> ToolOutput:
        if args.action == "status":
            recorded = self._oast.recorded_interactions()
            if session is None:
                if config.self_hosted:
                    where = f"self-hosted: {', '.join(config.servers)}"
                else:
                    where = (
                        "public interactsh servers; set INTERACTSH_SERVER (+ INTERACTSH_TOKEN) for a self-hosted one"
                    )
                return ToolOutput(content=f"no session yet — action=payload starts one ({where})")
            kind = "self-hosted" if config.self_hosted else "public, run by ProjectDiscovery"
            stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime(session.created / 1000))
            return ToolOutput(
                content=(
                    f"interactsh {session.server} ({kind}), since {stamp} UTC — "
                    f"{len(payloads)} payload(s), {len(recorded)} interaction(s) recorded"
                )
            )

        if args.action == "selftest":
            try:
                crypto_note = f"crypto roundtrip ok ({_selftest_roundtrip()} byte private key)"
            except OastUnavailable as error:
                crypto_note = f"crypto UNAVAILABLE — {error}"
            servers = ", ".join(config.servers)
            flavor = "self-hosted" if config.self_hosted else "public (ProjectDiscovery)"
            active = f"active on {session.server}" if session else "none yet (action=payload starts one)"
            return ToolOutput(content=f"oast selftest: {crypto_note}\nservers: {servers} ({flavor})\nsession: {active}")

        if args.action == "payload":
            if args.vuln_id and self._store.vuln_by_ref(args.vuln_id) is None:
                return fail(f"no finding {args.vuln_id}", "state_query what=vulns lists the finding ids.")
            session = await self._ensure_session(config, session, client)
            domain = payload_domain(session, args.tag)
            payloads.append(
                PayloadRecord(
                    domain=domain, tag=args.tag, purpose=args.purpose, vuln_id=args.vuln_id, ts=int(time.time() * 1000)
                )
            )
            self._oast.write_payloads(payloads)
            tag_note = f" [{args.tag}]" if args.tag else ""
            self._store.log("oast", f"payload {domain}{tag_note}")
            body = (
                f"payload: {domain}\n"
                f"  DNS  : {domain}\n"
                f"  HTTP : http://{domain}/   (https works too)\n"
                f"  mail : x@{domain}\n"
                f"Plant it, trigger the vector, then: oast action=poll"
                f"{f' tag={args.tag}' if args.tag else ''} wait_s=30"
            )
            if not config.self_hosted:
                body += (
                    "\nnote: public interactsh server (ProjectDiscovery) — callbacks and anything they carry pass "
                    "through it; use INTERACTSH_SERVER for confidential engagements."
                )
            return ToolOutput(content=body)

        if args.action == "describe":
            recorded = self._oast.recorded_interactions()
            shown = [i for i in recorded if not args.tag or _tag_of(i, payloads) == args.tag]
            if not shown:
                scope = f" for {args.tag}" if args.tag else ""
                return ToolOutput(content=f"no recorded interactions{scope} — poll first.")
            return ToolOutput(content="\n".join(describe(i, payload_for(i, payloads)) for i in shown[-25:]))

        if args.action == "poll":
            if session is None:
                return fail("no session yet", "create a payload first (action=payload).")
            wait = min(120.0, max(0.0, args.wait_s or 0.0))
            deadline = time.monotonic() + wait
            fresh: list[Interaction] = []
            while True:
                fresh = await poll(session, client)
                if fresh or time.monotonic() >= deadline or ctx.abort.is_set():
                    break
                await asyncio.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
            self._oast.append_interactions(fresh)
            recorded = self._oast.recorded_interactions()
            shown = [i for i in recorded if not args.tag or _tag_of(i, payloads) == args.tag]
            attached = self._attach_fresh(fresh, payloads)
            if fresh:
                extra = f" — {attached}" if attached else ""
                self._store.log("oast", f"{len(fresh)} new interaction(s){extra}")
            if not shown:
                waited = f" (waited {wait:g} s)" if wait else ""
                return ToolOutput(
                    content=(
                        f"no interactions{f' for {args.tag}' if args.tag else ''} yet{waited} — blind callbacks can be "
                        "delayed (queues, cron, admin views); poll again later before concluding."
                    )
                )
            scope = f" for {args.tag}" if args.tag else ""
            return ToolOutput(
                content=(
                    f"{len(fresh)} new, {len(shown)} total{scope}:\n"
                    + "\n".join(describe(i, payload_for(i, payloads)) for i in shown[-25:])
                )
            )

        # attach
        if not args.vuln_id:
            return fail("attach needs vuln_id", "state_query what=vulns lists the finding ids.")
        if self._store.vuln_by_ref(args.vuln_id) is None:
            return fail(f"no finding {args.vuln_id}", "state_query what=vulns lists the finding ids.")
        # only callbacks to payloads planted for this finding (or this tag) — never unattributed hits
        matching: list[tuple[Interaction, PayloadRecord]] = []
        for interaction in self._oast.recorded_interactions():
            payload = payload_for(interaction, payloads)
            if payload is None:
                continue
            if args.tag:
                if payload.tag == args.tag:
                    matching.append((interaction, payload))
            elif payload.vuln_id == args.vuln_id:
                matching.append((interaction, payload))
        if not matching:
            scope = f"tag {args.tag}" if args.tag else f"payloads created with vuln_id={args.vuln_id}"
            return fail(f"no recorded interactions for {scope}", "poll first, or pass the tag you planted.")
        record = self._write_evidence(args.vuln_id, args.tag, matching)
        where = Path(record.file).name if record else "n/a"
        return ToolOutput(
            content=(
                f"attached {len(matching)} interaction(s) to {args.vuln_id}: {where}\n"
                f"For the report image, capture it: run `cat {self._evidence_source(args.vuln_id, args.tag)}`."
            )
        )

    def _attach_fresh(self, fresh: list[Interaction], payloads: list[PayloadRecord]) -> str:
        """Fresh callbacks to payloads planted for a finding become evidence right away."""
        by_vuln: dict[str, list[tuple[Interaction, PayloadRecord]]] = {}
        for interaction in fresh:
            payload = payload_for(interaction, payloads)
            if payload is not None and payload.vuln_id:
                by_vuln.setdefault(payload.vuln_id, []).append((interaction, payload))
        names = []
        for vuln_id, matching in by_vuln.items():
            record = self._write_evidence(vuln_id, None, matching)
            names.append(vuln_id if record is None else f"{vuln_id} ({Path(record.file).name})")
        return ", ".join(names)

    def _evidence_source(self, vuln_id: str, tag: str | None) -> Path:
        return self._oast.dir / f"interactions-{vuln_id}{f'-{tag}' if tag else ''}.txt"

    def _write_evidence(
        self, vuln_id: str, tag: str | None, matching: list[tuple[Interaction, PayloadRecord]]
    ) -> EvidenceRecord | None:
        file = self._evidence_source(vuln_id, tag)
        file.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Out-of-band interactions recorded by the harness (interactsh)", ""]
        for interaction, payload in matching:
            lines.append(describe(interaction, payload))
            raw = (interaction.raw_request or "").strip().split("\n")[:20]
            lines.extend(f"    | {line}" for line in raw)
        file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._store.add_evidence(
            description=f"Out-of-band callbacks ({len(matching)}){f' for {tag}' if tag else ''}",
            file=file,
            kind="response",
            vuln_ids=[vuln_id],
            caption=f"Out-of-band interactions received from the target ({len(matching)})",
        )


def _tag_of(interaction: Interaction, payloads: list[PayloadRecord]) -> str | None:
    payload = payload_for(interaction, payloads)
    return payload.tag if payload else None


def _selftest_roundtrip() -> int:
    """Generate keys, then push one record through the exact encrypt→decrypt poll path."""
    private_pem, public_b64 = generate_keypair()
    record = {"protocol": "dns", "unique-id": "abc", "full-id": "abc", "remote-address": "127.0.0.1"}
    aes_key, data = encrypt_record(base64.b64decode(public_b64).decode(), record)
    back = decrypt_record(private_pem, aes_key, data)
    if back is None or back.protocol != "dns":
        raise OastError("the local encrypt→decrypt roundtrip lost the record")
    return len(private_pem.encode())

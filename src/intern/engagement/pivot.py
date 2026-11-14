"""Pivot engine: connect what the agent can DO (primitives granted by findings/footholds) with
what it can't reach yet (internal assets, other services, other APIs). Every pairing is a
concrete opportunity with a stable id; outcomes are recorded, and successes feed new assets back
in, so the search is recursive until nothing untried is left."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from intern.engagement.models import Cred, Endpoint, Host, Primitive, PrimitiveKind, Reachability, State, Vuln

_INTERNAL_NAME: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(^|\.)(localhost|local|internal|intranet|corp|lan|home\.arpa|private)$", re.IGNORECASE),
        "private DNS suffix",
    ),
    (re.compile(r"\.svc(\.cluster\.local)?$|\.cluster\.local$", re.IGNORECASE), "Kubernetes service name"),
    (re.compile(r"^metadata(\.google\.internal)?$", re.IGNORECASE), "cloud metadata"),
    (
        re.compile(
            r"(^|[.-])(int|internal|intra|intranet|corp|private|priv|lan|mgmt|admin-int|backoffice|staging-int)[.-]",
            re.IGNORECASE,
        ),
        "internal-looking hostname",
    ),
    (re.compile(r"^[a-z0-9-]+$", re.IGNORECASE), "single-label name (resolves only inside a network)"),
)


@dataclass(frozen=True)
class ReachVerdict:
    reach: Reachability
    why: str


def _ipv4_internal(ip: str) -> str | None:
    parts = ip.split(".")
    try:
        a, b = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    if a == 10:
        return "RFC1918 10/8"
    if a == 172 and 16 <= b <= 31:
        return "RFC1918 172.16/12"
    if a == 192 and b == 168:
        return "RFC1918 192.168/16"
    if a == 127:
        return "loopback"
    if a == 169 and b == 254:
        return "cloud metadata" if ip == "169.254.169.254" else "link-local"
    if a == 100 and 64 <= b <= 127:
        return "CGNAT 100.64/10"
    if a == 0:
        return "this-network"
    return None


def _ipv6_internal(ip: str) -> str | None:
    v = ip.lower()
    if v == "::1":
        return "loopback"
    if re.match(r"^f[cd][0-9a-f]{2}:", v):
        return "ULA fc00::/7"
    if re.match(r"^fe[89ab][0-9a-f]:", v):
        return "link-local"
    if v.startswith("::ffff:"):
        return _ipv4_internal(v.removeprefix("::ffff:"))
    return None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def classify_host(input_: str) -> ReachVerdict:
    """Classify a host / IP / URL. Hostnames are judged by name; DNS can refine it later."""
    host = input_.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", host, re.IGNORECASE):
        try:
            host = urlsplit(host).hostname or ""
        except ValueError:
            host = ""
    host = host.strip("[]").lower()
    if host.count(":") == 1:
        host = re.sub(r":\d+$", "", host)  # host:port — never strip from IPv6
    if not host:
        return ReachVerdict("unknown", "empty")
    if _is_ip(host):
        version = ipaddress.ip_address(host).version
        why = _ipv4_internal(host) if version == 4 else _ipv6_internal(host)
        return ReachVerdict("internal", why) if why else ReachVerdict("public", f"public IPv{version}")
    for pattern, why in _INTERNAL_NAME:
        if pattern.search(host):
            return ReachVerdict("internal", why)
    return ReachVerdict("unknown", "public-looking name (not verified)")


def host_of(input_: str) -> str:
    """Host part of a URL or host string, lower-cased."""
    if re.match(r"^[a-z][a-z0-9+.-]*://", input_, re.IGNORECASE):
        try:
            return (urlsplit(input_).hostname or "").lower()
        except ValueError:
            pass
    host = input_.strip().strip("[]").lower()
    return re.sub(r":\d+$", "", host) if host.count(":") == 1 else host


@dataclass(frozen=True)
class Asset:
    name: str
    reach: Reachability
    why: str


@dataclass(frozen=True)
class Opportunity:
    id: str
    primitive: str | None
    kind: str
    target: str
    action: str


def _oid(*parts: str) -> str:
    return "p" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def known_assets(hosts: dict[str, Host], endpoints: Sequence[Endpoint]) -> list[Asset]:
    """Every asset the engagement knows about (hosts + endpoint hosts), with reachability."""
    out: dict[str, Asset] = {}
    for host in hosts.values():
        for name in filter(None, (host.hostname, host.ip)):
            verdict = classify_host(name)
            reach = host.reachability if host.reachability and host.reachability != "unknown" else verdict.reach
            why = "marked internal" if host.reachability == "internal" else verdict.why
            out.setdefault(name, Asset(name, reach, why))
    for endpoint in endpoints:
        name = host_of(endpoint.url)
        if name and name not in out:
            verdict = classify_host(name)
            out[name] = Asset(name, verdict.reach, verdict.why)
    return list(out.values())


NETWORK_VANTAGE: frozenset[PrimitiveKind] = frozenset({"rce", "shell", "tunnel"})
LOGIN_SERVICES = re.compile(
    r"^(ssh|smb|microsoft-ds|rdp|ms-wbt-server|winrm|ftp|telnet|mysql|postgres(ql)?|"
    r"mssql|ms-sql-s|redis|mongodb|ldap|vnc|http|https)$",
    re.IGNORECASE,
)
SECRET_PATHS = (
    "/proc/self/environ, app .env/config files, ~/.aws/credentials, ~/.ssh/id_*, ~/.kube/config, "
    "/var/run/secrets/kubernetes.io/serviceaccount/token, CI/deploy configs"
)


def opportunities(
    hosts: dict[str, Host], creds: Sequence[Cred], endpoints: Sequence[Endpoint], primitives: Sequence[Primitive]
) -> list[Opportunity]:
    """All pivot opportunities implied by the current state (pure; ids are stable across calls)."""
    assets = known_assets(hosts, endpoints)
    internal = [a for a in assets if a.reach == "internal"]
    out: list[Opportunity] = []
    for p in primitives:
        origin = host_of(p.host)
        if p.kind == "ssrf":
            out.append(
                Opportunity(
                    _oid(p.id, "metadata"),
                    p.id,
                    "ssrf→metadata",
                    "169.254.169.254",
                    f"via SSRF {p.id} ({p.via}): request cloud metadata (AWS IMDS, GCP metadata.google.internal, "
                    "Azure /metadata/instance) — role name → read-only identity check → cloud-enum",
                )
            )
            for a in internal:
                if a.name != origin:
                    out.append(
                        Opportunity(
                            _oid(p.id, a.name),
                            p.id,
                            "ssrf→internal",
                            a.name,
                            f"via SSRF {p.id} ({p.via}): request http(s)://{a.name}/ (and its known paths/ports) — "
                            f"internal asset ({a.why}); map what answers, record new hosts/endpoints",
                        )
                    )
        if p.kind in NETWORK_VANTAGE:
            for a in internal:
                if a.name != origin:
                    out.append(
                        Opportunity(
                            _oid(p.id, a.name),
                            p.id,
                            "pivot→internal",
                            a.name,
                            f"from {p.kind} on {p.host} ({p.id}): reach {a.name} ({a.why}) — bounded scan, then "
                            "test it like any target (post-exploitation playbook)",
                        )
                    )
        if p.kind == "file_read":
            out.append(
                Opportunity(
                    _oid(p.id, "secrets"),
                    p.id,
                    "file_read→secrets",
                    p.host,
                    f"via file read {p.id} ({p.via}): harvest {SECRET_PATHS}; record every credential found",
                )
            )
        if p.kind == "db_read":
            out.append(
                Opportunity(
                    _oid(p.id, "creds"),
                    p.id,
                    "db_read→credentials",
                    p.host,
                    f"via {p.id} ({p.via}): read user/credential/API-key/session tables (bounded, read-only) → "
                    "record creds, then reuse them",
                )
            )
        if p.kind == "token":
            for a in assets:
                if a.name != origin and (a.reach != "public" or len(assets) < 12):
                    out.append(
                        Opportunity(
                            _oid(p.id, a.name),
                            p.id,
                            "token→reuse",
                            a.name,
                            f"replay token {p.id} ({p.via}) against APIs on {a.name} — tokens are often accepted "
                            "across services",
                        )
                    )
        if p.kind == "xss":
            out.append(
                Opportunity(
                    _oid(p.id, "admin"),
                    p.id,
                    "xss→victim-actions",
                    p.host,
                    f"via XSS {p.id}: use the victim's (admin's) browser to call privileged endpoints / read "
                    f"CSRF-protected data on {p.host}",
                )
            )
    # credential reuse: every usable credential × every login service it isn't known to work on
    services = [
        (host.hostname or host.ip, service.port, service.name or "")
        for host in hosts.values()
        for service in host.services
        if service.name and LOGIN_SERVICES.match(service.name)
    ]
    for c in creds:
        if c.works is False or not c.username:
            continue
        for sv_host, sv_port, sv_name in services:
            if sv_host in (c.valid_for or ""):
                continue
            leaked = bool(re.match(r"^leak ", c.source or ""))
            out.append(
                Opportunity(
                    _oid(c.id, sv_host, str(sv_port)),
                    None,
                    "leaked-cred→login" if leaked else "cred→reuse",
                    f"{sv_host}:{sv_port}",
                    (
                        f"LEAKED credential {c.id} ({c.username}, {c.source}) on {sv_name} {sv_host}:{sv_port} — "
                        "ask the operator before trying (lockouts, real users); then one attempt only"
                        if leaked
                        else f"try credential {c.id} ({c.username}) once on {sv_name} {sv_host}:{sv_port} — "
                        "credential reuse"
                    ),
                )
            )
    return out


def open_opportunities(state: State) -> list[Opportunity]:
    """All opportunities not yet marked with an outcome."""
    done = {mark.id for mark in state.pivots}
    return [o for o in opportunities(state.hosts, state.creds, state.endpoints, state.primitives) if o.id not in done]


def infer_primitive(v: Vuln) -> PrimitiveKind | None:
    """Primitive implied by a confirmed finding's class (so the agent needn't remember to declare it)."""
    if v.status != "confirmed":
        return None
    t = v.title.lower()
    if re.search(r"\bssrf\b|server[- ]side request", t) or "API7" in v.owasp:
        return "ssrf"
    if re.search(
        r"\brce\b|remote code|command (execution|injection)|code execution|deserializ.*(exec|rce)|sandbox escape", t
    ):
        return "rce"
    if re.search(r"path traversal|directory traversal|\blfi\b|local file|file read|arbitrary file|\bxxe\b", t):
        return "file_read"
    if re.search(r"sql ?injection|\bsqli\b|nosql injection", t):
        return "db_read"
    if re.search(r"\bxss\b|cross[- ]site scripting", t):
        return "xss"
    return None


def access_primitive(type_: str) -> PrimitiveKind | None:
    """Primitive implied by a recorded foothold."""
    if re.search(r"tunnel|vpn|socks|pivot", type_, re.IGNORECASE):
        return "tunnel"
    if re.search(r"shell|ssh|rce|rdp|winrm|container|root", type_, re.IGNORECASE):
        return "shell"
    return None


def render_opportunities(opps: Sequence[Opportunity], max_: int = 12) -> str:
    if not opps:
        return "no open pivot opportunities"
    lines = [f"- {o.id} [{o.kind}] {o.action}" for o in opps[:max_]]
    if len(opps) > max_:
        lines.append(f"… {len(opps) - max_} more (state_query what=pivots)")
    return "\n".join(lines)

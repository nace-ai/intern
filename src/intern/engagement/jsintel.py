"""JavaScript intelligence: what a client bundle gives away — API routes, GraphQL operations,
WebSocket endpoints, hostnames (internal ones feed the pivot engine), secrets, admin/debug routes
and feature flags. Pure extraction over source text; :func:`collect_scripts` gathers the text from
a page (every script it loads, inline scripts, and their source maps — whose original sources are
analysed too, because a publicly served source map is itself a finding).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from intern.engagement.api_tools import fetch_text, template_of
from intern.engagement.browser_sessions import (
    BrowserUnavailable,
    SessionStore,
    context_options,
    open_browser,
    open_context,
)
from intern.engagement.pivot import classify_host
from intern.engagement.store import EngagementStore
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

MAX_FILES = 40
MAX_BYTES = 5 * 1024 * 1024
HTTP_TIMEOUT_S = 10.0


class CollectError(RuntimeError):
    """Script collection failed; the message is the tool's error text."""


@dataclass
class CollectedFile:
    name: str
    text: str
    from_map: str | None = None


@dataclass
class Collected:
    files: list[CollectedFile]
    scripts: list[str]
    maps: list[str]
    note: str | None = None


@dataclass
class _SourceMap:
    url: str
    files: list[CollectedFile]


SOURCEMAP_RE = re.compile(r"//[#@]\s*sourceMappingURL=([^\s'\"]+)\s*$", re.MULTILINE)


async def _source_map(
    script_url: str,
    text: str,
    header: str | None,
    headers: dict[str, str] | None,
    client: httpx.AsyncClient,
) -> _SourceMap | None:
    match = SOURCEMAP_RE.search(text[-2000:])
    ref = header or (match.group(1) if match else None)
    if not ref or ref.startswith("data:"):
        return None
    url = urljoin(script_url, ref)
    got = await fetch_text(url, client, headers)
    if got is None:
        return None
    try:
        loaded = json.loads(got[0])
    except ValueError:
        return None
    sources = loaded.get("sources") or []
    contents = loaded.get("sourcesContent") or []
    files = [
        CollectedFile(name=str(source), text=str(content), from_map=url)
        for source, content in zip(sources, contents, strict=False)
        if content and "node_modules" not in str(source)
    ]
    return _SourceMap(url=url, files=files)


async def collect_scripts(
    page_url: str | None,
    extra: list[str],
    session: Any = None,
    client: httpx.AsyncClient | None = None,
) -> Collected:
    """Gather a page's scripts (as loaded, inline, extra URLs) plus their source maps' sources."""
    headers = dict(context_options(session).get("extra_http_headers") or {})
    script_urls: set[str] = set(extra)
    inline: list[CollectedFile] = []
    if page_url:
        try:
            async with (
                open_browser() as browser,
                open_context(browser, {"ignore_https_errors": True, **context_options(session)}) as context,
            ):

                def on_response(response: Any) -> None:
                    try:
                        is_script = response.request.resource_type == "script" or "javascript" in (
                            response.headers.get("content-type") or ""
                        )
                    except Exception:
                        return
                    if is_script:
                        script_urls.add(response.url)

                context.on("response", on_response)
                page = await context.new_page()
                with contextlib.suppress(Exception):
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                scripts: list[Any] = []
                with contextlib.suppress(Exception):
                    scripts = await page.evaluate(
                        "() => Array.from(document.scripts).map(s => ({src: s.src,"
                        " text: s.src ? '' : s.textContent ?? ''}))"
                    )
                for script in scripts or []:
                    src, text = script.get("src"), script.get("text") or ""
                    if src:
                        script_urls.add(src)
                    elif len(text.strip()) > 40:
                        inline.append(CollectedFile(name=f"{page_url} (inline)", text=text))
        except BrowserUnavailable as error:
            raise CollectError(str(error)) from error
    urls = [u for u in script_urls if u.startswith(("http:", "https:"))][:MAX_FILES]
    files: list[CollectedFile] = list(inline)
    maps: list[str] = []
    own = client is None
    http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
    try:
        for url in urls:
            got = await fetch_text(url, http, headers)
            if got is None:
                continue
            files.append(CollectedFile(name=url, text=got[0]))
            found_map = await _source_map(url, got[0], got[1], headers, http)
            if found_map is not None:
                maps.append(found_map.url)
                files.extend(found_map.files)
    finally:
        if own:
            await http.aclose()
    note = f"analysed the first {MAX_FILES} of {len(script_urls)} scripts" if len(script_urls) > MAX_FILES else None
    return Collected(files=files, scripts=urls, maps=maps, note=note)


# -- pure extraction --------------------------------------------------------------------------

ROUTE_RE = re.compile(
    r"""["'`](/(?:api|graphql|gql|rest|rpc|v\d+|admin|internal|auth|oauth|users?|accounts?|orders?|"""
    r"""payments?|billing|files?|upload|download|export|import|webhooks?|settings|config|debug|"""
    r"""actuator|health|metrics|swagger|openapi)[^"'\s<>]*)["'`]""",
    re.IGNORECASE,
)
URL_RE = re.compile(r"\b((?:https?|wss?)://[^\s\"'`<>()\\]+)", re.IGNORECASE)
GQL_RE = re.compile(r"\b(query|mutation|subscription)\s+([A-Z][A-Za-z0-9_]{2,})\s*[({]")
ADMIN_RE = re.compile(
    r"""/(admin|internal|debug|actuator|manage(ment)?|backoffice|staff|superuser|ops|_?dev(tools)?)(/[^"'\s<>]*)?""",
    re.IGNORECASE,
)
FLAG_RE = re.compile(
    r"""["']((?:enable|disable|is|show|allow|use)[A-Z][A-Za-z0-9]{3,}|(?:feature|ff|flag)[_.-][a-z0-9_.-]{3,})["']\s*[:,)]"""
)
NOISE_HOST = re.compile(
    r"(^|\.)(w3\.org|schema\.org|reactjs\.org|react\.dev|mozilla\.org|github\.com|githubusercontent\.com|"
    r"npmjs\.(?:com|org)|unpkg\.com|jsdelivr\.net|cdnjs\.cloudflare\.com|googleapis\.com|gstatic\.com|google\.com|"
    r"facebook\.com|twitter\.com|sentry\.io)$|^(?:www\.)?example\.(?:com|org|net)$",
    re.IGNORECASE,
)

#: Group 1, when present, is the secret value (generic key=value rule); otherwise the whole match.
SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe-live-secret", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b")),
    ("stripe-publishable", re.compile(r"\bpk_live_[0-9a-zA-Z]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    (
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    ),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    ("twilio-key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    (
        "generic-secret",
        re.compile(
            r"""["']?(?:api[_-]?key|apikey|secret|client[_-]?secret|access[_-]?token|auth[_-]?token|"""
            r"""password|passwd)["']?\s*[:=]\s*["']([A-Za-z0-9_\-+/=.]{16,})["']""",
            re.IGNORECASE,
        ),
    ),
]

NOT_A_SECRET = re.compile(
    r"^(x{6,}|\*{4,}|<|\$\{|process\.env|undefined|null|true|false)|placeholder|example|changeme|your[_-]|dummy|"
    r"sample|x{6,}|\*{4,}",
    re.IGNORECASE,
)


class SecretHit(BaseModel):
    kind: str
    value: str
    file: str
    line: int


class HostHit(BaseModel):
    host: str
    reach: str
    why: str


class JsIntel(BaseModel):
    routes: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    hosts: list[HostHit] = Field(default_factory=list)
    websockets: list[str] = Field(default_factory=list)
    graphql: list[str] = Field(default_factory=list)
    secrets: list[SecretHit] = Field(default_factory=list)
    admin_routes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


def _line_of(text: str, index: int) -> int:
    return 1 + text.count("\n", 0, index)


def _httpish(url: str) -> str:
    """ws/wss URLs parse as http(s) so hostname/path extraction works on them."""
    lowered = url.lower()
    if lowered.startswith("wss://"):
        return "https://" + url[6:]
    if lowered.startswith("ws://"):
        return "http://" + url[5:]
    return url


def _hostname_of(url: str) -> str:
    try:
        return (urlsplit(_httpish(url)).hostname or "").lower()
    except ValueError:
        return ""


def extract_intel(files: list[CollectedFile]) -> JsIntel:
    """Extract intelligence from a set of files. Values are deduplicated."""
    routes: set[str] = set()
    urls: set[str] = set()
    websockets: set[str] = set()
    graphql: set[str] = set()
    admin: set[str] = set()
    flags: set[str] = set()
    secrets: list[SecretHit] = []
    seen_secret: set[str] = set()
    for collected in files:
        text = collected.text
        for match in ROUTE_RE.finditer(text):
            route = re.sub(r"[),;]+$", "", match.group(1))
            routes.add(route)
            if ADMIN_RE.search(route):
                admin.add(route)
        for match in URL_RE.finditer(text):
            url = re.sub(r"[),;.]+$", "", match.group(1))
            if re.match(r"^wss?:", url, re.IGNORECASE):
                websockets.add(url)
            else:
                urls.add(url)
            path = urlsplit(_httpish(url)).path
            if ADMIN_RE.search(path):
                admin.add(url)
        for match in GQL_RE.finditer(text):
            graphql.add(f"{match.group(1)} {match.group(2)}")
        for match in FLAG_RE.finditer(text):
            flags.add(match.group(1))
        for kind, rule in SECRET_RULES:
            for match in rule.finditer(text):
                value = (match.group(1) if match.groups() else match.group(0)).strip()
                if NOT_A_SECRET.search(value) or value in seen_secret:
                    continue
                seen_secret.add(value)
                secrets.append(
                    SecretHit(kind=kind, value=value, file=collected.name, line=_line_of(text, match.start()))
                )
    host_set: set[str] = set()
    for url in [*urls, *websockets]:
        host = _hostname_of(url)
        if host and not NOISE_HOST.search(host):
            host_set.add(host)
    hosts = []
    for host in sorted(host_set):
        verdict = classify_host(host)
        hosts.append(HostHit(host=host, reach=verdict.reach, why=verdict.why))
    clean_urls = sorted(u for u in urls if (h := _hostname_of(u)) and not NOISE_HOST.search(h))
    return JsIntel(
        routes=sorted(routes),
        urls=clean_urls,
        hosts=hosts,
        websockets=sorted(websockets),
        graphql=sorted(graphql),
        secrets=secrets,
        admin_routes=sorted(admin),
        flags=sorted(flags)[:60],
    )


def mask_secret(value: str) -> str:
    """Mask a secret for display (the full value stays in engagement state for verification)."""
    return "••••" if len(value) <= 8 else f"{value[:6]}••••{value[-2:]}"


# -- the tool ---------------------------------------------------------------------------------


class JsAnalyzeArgs(BaseModel):
    url: str | None = None
    scripts: list[str] = Field(default_factory=list)
    session: str | None = None


class JsAnalyzeTool(Tool):
    """Mine the app's client-side code for API routes, hosts, endpoints, secrets and flags."""

    name = "js_analyze"
    description = (
        "Mine the app's JavaScript (every script a page loads as an identity, inline scripts, extra script URLs, "
        "and their SOURCE MAPS — whose original sources are analysed too) for: API routes and full URLs (added to "
        "the inventory), hostnames (internal ones — int.*, *.internal, private IPs — become assets for the pivot "
        "engine), WebSocket and GraphQL endpoints, secrets (AWS/Google/Stripe/Slack/GitHub keys, JWTs, private "
        "keys, api_key=…: recorded as UNVERIFIED credentials — verify each with one read-only call), admin/debug "
        "routes and feature flags. url = page to load (session=<browser_session> for authenticated bundles — they "
        "often hold more), scripts = extra JS URLs. Full results are saved to the engagement's js-intel folder."
    )
    Args = JsAnalyzeArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "network"})

    def __init__(self, store: EngagementStore, client: httpx.AsyncClient | None = None) -> None:
        self._store = store
        self._client = client

    async def run(self, args: JsAnalyzeArgs, ctx: ToolContext) -> ToolOutput:
        if not args.url and not args.scripts:
            return fail("pass url (a page) and/or scripts (JS URLs)", "One of the two is enough; both is fine too.")
        session = None
        if args.session:
            session = SessionStore(self._store).load(args.session)
            if session is None:
                return fail(f'no browser session "{args.session}"', "browser_session action=list lists the names.")
        try:
            collected = await collect_scripts(args.url, args.scripts, session=session, client=self._client)
        except CollectError as error:
            return fail(str(error), "Install the browser extra / Chromium, or pass scripts= URLs (fetched over HTTP).")
        intel = extract_intel(collected.files)
        base = args.url or args.scripts[0]
        self._record_routes(intel, base)
        self._record_hosts(intel)
        for secret in intel.secrets:
            self._store.add_cred(
                secret=secret.value, source=f"js {secret.kind} @ {secret.file}:{secret.line} (unverified)"
            )
        out = await self._write_intel(args.url, collected, intel, base)
        maps_note = f", {len(collected.maps)} source map(s) — sources recovered" if collected.maps else ""
        self._store.log(
            "js",
            f"analysed {len(collected.files)} file(s) from {base}: {len(intel.routes)} routes, "
            f"{len(intel.hosts)} hosts, {len(intel.secrets)} secret candidates{maps_note}",
        )
        internal = [h for h in intel.hosts if h.reach == "internal"]
        lines = [
            f"analysed {len(collected.files)} file(s) ({len(collected.scripts)} script(s){maps_note})"
            + (f" — {collected.note}" if collected.note else ""),
            f"routes: {len(intel.routes)}"
            + (f": {', '.join(intel.routes[:15])}{' …' if len(intel.routes) > 15 else ''}" if intel.routes else ""),
            f"hosts: {len(intel.hosts)}"
            + (f" — INTERNAL: {', '.join(f'{h.host} [{h.why}]' for h in internal)}" if internal else ""),
            f"websockets: {', '.join(intel.websockets[:8])}" if intel.websockets else "",
            f"graphql operations: {', '.join(intel.graphql[:15])}" if intel.graphql else "",
            f"admin/debug routes: {', '.join(intel.admin_routes[:12])}" if intel.admin_routes else "",
            (
                "SECRETS (recorded as unverified creds — verify each with ONE read-only call, then mark works):\n"
                + "\n".join(f"  - {s.kind} {mask_secret(s.value)} @ {s.file}:{s.line}" for s in intel.secrets[:15])
            )
            if intel.secrets
            else "secrets: none matched",
            f"feature flags: {', '.join(intel.flags[:15])}" if intel.flags else "",
            (
                f"⚠ source maps are publicly served ({', '.join(collected.maps[:3])}) — record as a finding "
                "(A02, info/low unless the sources leak secrets)"
            )
            if collected.maps
            else "",
            f"full results: {out}",
        ]
        return ToolOutput(content="\n".join(line for line in lines if line))

    def _record_routes(self, intel: JsIntel, base: str) -> int:
        """Routes → inventory (method unknown until probed: recorded as GET)."""
        added = 0
        for route in intel.routes:
            absolute = urljoin(base, route)
            template, params = template_of(absolute)
            self._store.add_endpoint(
                method="GET",
                url=template,
                sample=absolute if "{" not in template else None,
                params=params,
                source="js",
                title="discovered (js)",
            )
            added += 1
        return added

    def _record_hosts(self, intel: JsIntel) -> None:
        known = {h for host in self._store.state.hosts.values() for h in (host.ip, host.hostname)}
        for hit in intel.hosts:
            if hit.host in known:
                continue
            self._store.add_host(
                hit.host,
                hostname=hit.host,
                source="js",
                reachability="internal" if hit.reach == "internal" else None,
            )

    async def _write_intel(self, page_url: str | None, collected: Collected, intel: JsIntel, base: str) -> str:
        """The full result as JSON under tools/js-intel/ (secrets masked; full values stay in creds)."""
        host = (urlsplit(base).hostname or "page").replace(":", "-")
        payload = {
            "page": page_url,
            "scripts": collected.scripts,
            "sourceMaps": collected.maps,
            **intel.model_dump(),
            "secrets": [s.model_dump() | {"value": mask_secret(s.value)} for s in intel.secrets],
        }
        return await asyncio.to_thread(self._dump, host, payload)

    def _dump(self, host: str, payload: dict[str, object]) -> str:
        out_dir = self._store.tool_dir("js-intel")
        out_dir.mkdir(parents=True, exist_ok=True)
        file = out_dir / f"{host}-{int(time.time() * 1000)}.json"
        file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(file)

"""API inventory tooling: discovery (browser crawl, OpenAPI/Swagger import, HAR import) normalised
to route templates, and the method × identity matrix over that inventory.

Discovery is read-only: the crawl follows same-origin links only and never follows logout or
destructive-looking links. The matrix is safe by default — OPTIONS, HEAD, GET; write methods only
when the operator's explicit ``unsafe=true`` rides along. Probing goes over plain HTTP (httpx) with
each identity's own headers and cookies, never through the browser.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from intern.engagement.models import ProbeCell
from intern.engagement.store import EngagementStore
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

SAFE_METHODS = ["OPTIONS", "HEAD", "GET"]
UNSAFE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]
HTTP_TIMEOUT_S = 10.0
MAX_REQUESTS = 600
CONCURRENCY = 4

#: Numeric-looking path segments become placeholders so one route is tested once.
SEGMENT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\d+$"), "{id}"),
    (re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE), "{uuid}"),
    (re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE), "{hash}"),
    (re.compile(r"^[^@/]+@[^@/]+\.[a-z]{2,}$", re.IGNORECASE), "{email}"),
    (re.compile(r"^[A-Za-z0-9_-]{24,}$"), "{token}"),
]


def template_of(url: str) -> tuple[str, list[str]]:
    """Normalise a URL to a route template plus its query parameter names."""
    parts = urlsplit(url)
    segments = []
    for segment in parts.path.split("/"):
        decoded = unquote(segment)
        placeholder = next((p for rule, p in SEGMENT_RULES if rule.match(decoded)), None)
        segments.append(placeholder if placeholder is not None else segment)
    template = f"{parts.scheme}://{parts.netloc.lower()}{'/'.join(segments)}"
    params: list[str] = []
    for name in re.findall(r"[?&]([^=&#]+)=", url):
        if name not in params:
            params.append(name)
    return template, params


def concrete(template: str) -> str | None:
    """A template without placeholders can be requested as-is (when no real sample exists)."""
    return None if "{" in template else template


STATIC_RE = re.compile(
    r"\.(css|js|mjs|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|mp4|webm|mp3)(\?|$)", re.IGNORECASE
)
ANALYTICS_RE = re.compile(
    r"google-analytics|googletagmanager|doubleclick|segment\.io|sentry\.io|hotjar|intercom|fullstory", re.IGNORECASE
)
API_PATH_RE = re.compile(r"/(api|graphql|gql|rest|rpc|v\d+)(/|$|\?)", re.IGNORECASE)


def is_api_like(url: str, resource_type: str | None = None, content_type: str | None = None) -> bool:
    """Static assets and analytics are not API surface."""
    if STATIC_RE.search(url) or ANALYTICS_RE.search(url):
        return False
    if resource_type in ("xhr", "fetch", "websocket", "eventsource"):
        return True
    if content_type and re.search(r"json|xml|graphql|grpc|event-stream", content_type, re.IGNORECASE):
        return True
    return bool(API_PATH_RE.search(url))


SourceKind = Literal["crawl", "form", "openapi", "har", "js"]


class Discovered(BaseModel):
    method: str
    template: str  # scheme://host/path with {id}-style placeholders, no query
    sample: str  # a concrete URL seen (for probing)
    params: list[str] = Field(default_factory=list)
    source: SourceKind
    content_type: str | None = None
    status: int | None = None


def merge_discovered(items: list[Discovered]) -> list[Discovered]:
    """One entry per method + template; params accumulate; the first sample wins."""
    by_key: dict[str, Discovered] = {}
    for item in items:
        key = f"{item.method.upper()} {item.template}"
        current = by_key.get(key)
        if current is None:
            by_key[key] = item.model_copy(update={"method": item.method.upper()})
            continue
        by_key[key] = current.model_copy(
            update={
                "params": list(dict.fromkeys([*current.params, *item.params])),
                "status": current.status if current.status is not None else item.status,
                "content_type": current.content_type or item.content_type,
            }
        )
    return list(by_key.values())


def hosts_of(items: list[Discovered]) -> list[str]:
    out: list[str] = []
    for item in items:
        try:
            host = urlsplit(item.sample).hostname
        except ValueError:
            host = None
        if host and host not in out:
            out.append(host)
    return out


class DiscoverError(RuntimeError):
    """Discovery failed; the message is the tool's error text."""


# -- the browser (Playwright, optional) -----------------------------------------------------


def _browser_session_helpers() -> Any:
    """The shared Playwright helpers from browser_sessions (open_browser, open_context, context_options)."""
    from intern.engagement import browser_sessions

    return browser_sessions


def _context_options(session: Any) -> dict[str, Any]:
    """Playwright context options (storage_state, extra_http_headers) replaying a browser session."""
    if session is None:
        return {}
    return _browser_session_helpers().context_options(session)


async def fetch_text(
    url: str, client: httpx.AsyncClient, headers: dict[str, str] | None = None
) -> tuple[str, str | None] | None:
    """(text, sourcemap ref) or None when the URL is unreachable or too big."""
    try:
        response = await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400 or len(response.content) > 5 * 1024 * 1024:
        return None
    return response.text, response.headers.get("sourcemap") or response.headers.get("x-sourcemap")


# -- discovery sources ----------------------------------------------------------------------


@dataclass
class DiscoverResult:
    endpoints: list[Discovered] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    pages: int = 0
    note: str | None = None


SKIP_LINK = re.compile(
    r"logout|log-out|signout|sign-out|sessions/destroy|delete|remove|destroy|unsubscribe|deactivate|revoke",
    re.IGNORECASE,
)


async def crawl(start: str, session: Any = None, max_pages: int | None = None) -> DiscoverResult:
    """Drive the app in a browser from ``start``, recording every API request and form it makes."""
    origin = f"{urlsplit(start).scheme}://{urlsplit(start).netloc}"
    limit = min(max_pages if max_pages is not None else 25, 100)
    helpers = _browser_session_helpers()
    found: list[Discovered] = []
    pages = 0
    try:
        async with (
            helpers.open_browser() as browser,
            helpers.open_context(browser, {"ignore_https_errors": True, **_context_options(session)}) as context,
        ):
            context.on("request", _request_recorder(found))
            context.on("response", _response_recorder(found))
            page = await context.new_page()
            queue, seen = [start], set()
            while queue and pages < limit:
                url = queue.pop(0)
                key = url.split("#")[0]
                if key in seen:
                    continue
                seen.add(key)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass  # a page that fails to load still made requests worth keeping
                pages += 1
                try:
                    discovered = await page.evaluate(
                        "() => ({ links: Array.from(document.querySelectorAll('a[href]')).map(a => a.href),"
                        " forms: Array.from(document.querySelectorAll('form')).map(f => ({ action: f.action,"
                        " method: (f.method || 'get').toUpperCase(),"
                        " fields: Array.from(f.querySelectorAll('[name]')).map(i => i.name) })) })"
                    )
                except Exception:
                    discovered = {"links": [], "forms": []}
                for form in discovered.get("forms", []):
                    if not str(form.get("action", "")).startswith("http"):
                        continue
                    template, params = template_of(form["action"])
                    fields = [name for name in form.get("fields", []) if name]
                    found.append(
                        Discovered(
                            method=form.get("method", "GET"),
                            template=template,
                            sample=form["action"],
                            params=list(dict.fromkeys([*params, *fields])),
                            source="form",
                        )
                    )
                for link in discovered.get("links", []):
                    if not str(link).startswith(origin) or SKIP_LINK.search(link) or link.split("#")[0] in seen:
                        continue
                    queue.append(link)
    except helpers.BrowserUnavailable as error:
        raise DiscoverError(str(error)) from error
    endpoints = merge_discovered(found)
    note = f"stopped at {limit} pages — raise max_pages or crawl a deeper start URL" if pages >= limit else None
    return DiscoverResult(endpoints=endpoints, hosts=hosts_of(endpoints), pages=pages, note=note)


def _request_recorder(found: list[Discovered]) -> Any:
    def record(request: Any) -> None:
        url = request.url
        resource_type = request.resource_type
        content_type = request.headers.get("content-type")
        if not url.startswith(("http:", "https:")) or not is_api_like(url, resource_type, content_type):
            return
        template, params = template_of(url)
        found.append(
            Discovered(
                method=request.method,
                template=template,
                sample=url,
                params=params,
                source="crawl",
                content_type=content_type,
            )
        )

    return record


def _response_recorder(found: list[Discovered]) -> Any:
    def record(response: Any) -> None:
        for item in reversed(found):
            if item.sample == response.url and item.status is None:
                item.status = response.status
                return

    return record


OPENAPI_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _parse_spec(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    try:
        import yaml
    except ModuleNotFoundError as error:
        raise DiscoverError(
            "YAML spec but no YAML parser installed — fetch the JSON variant (e.g. /v3/api-docs, /swagger.json)"
        ) from error
    loaded = yaml.safe_load(stripped)
    if not isinstance(loaded, dict):
        raise DiscoverError("the document is not an OpenAPI/Swagger spec (not a mapping)")
    return loaded


def spec_endpoints(spec: dict[str, Any], source_url: str | None) -> list[Discovered]:
    """Endpoints from a parsed OpenAPI 3 / Swagger 2 document."""
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise DiscoverError("no `paths` in the document — not an OpenAPI/Swagger spec")
    remote = bool(source_url and re.match(r"^https?://", source_url))
    if remote and source_url:
        parts = urlsplit(source_url)
        fallback = f"{parts.scheme}://{parts.netloc}"
    else:
        fallback = "http://localhost"
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict) and servers[0].get("url"):
        base = urljoin(fallback, str(servers[0]["url"])).rstrip("/")
    elif spec.get("host"):
        scheme = (spec.get("schemes") or ["https"])[0]
        base = f"{scheme}://{spec['host']}{spec.get('basePath') or ''}".rstrip("/")
    else:
        base = fallback
    items: list[Discovered] = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in OPENAPI_METHODS:
                continue
            params = [
                str(p["name"])
                for p in (op or {}).get("parameters", [])
                if isinstance(p, dict) and p.get("name") and p.get("in") in ("query", "path")
            ]
            template = f"{base}{path}"
            items.append(
                Discovered(method=method.upper(), template=template, sample=template, params=params, source="openapi")
            )
    return merge_discovered(items)


async def from_openapi(
    source: str, headers: dict[str, str] | None = None, client: httpx.AsyncClient | None = None
) -> DiscoverResult:
    """Endpoints from an OpenAPI 3 / Swagger 2 document (URL or local file)."""
    try:
        if re.match(r"^https?://", source):
            own = client is None
            http = client or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
            try:
                response = await http.get(source, headers=headers, follow_redirects=True)
                response.raise_for_status()
                text = response.text
            finally:
                if own:
                    await http.aclose()
        else:
            text = await asyncio.to_thread(Path(source).read_text, "utf-8")
        endpoints = spec_endpoints(_parse_spec(text), source if re.match(r"^https?://", source) else None)
    except DiscoverError:
        raise
    except (OSError, ValueError, httpx.HTTPError) as error:
        raise DiscoverError(f"could not read the OpenAPI document: {error}") from error
    return DiscoverResult(endpoints=endpoints, hosts=hosts_of(endpoints))


def from_har(file: str | Path) -> DiscoverResult:
    """Endpoints from a HAR file (e.g. exported from the operator's browser)."""
    try:
        raw = json.loads(Path(file).read_text(encoding="utf-8"))
        entries = raw["log"]["entries"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DiscoverError(f"could not read the HAR file: {error}") from error
    items: list[Discovered] = []
    for entry in entries:
        request = entry.get("request", {})
        url = str(request.get("url", ""))
        response = entry.get("response", {})
        content = response.get("content") or {}
        if not is_api_like(url, entry.get("_resourceType"), content.get("mimeType")):
            continue
        template, params = template_of(url)
        items.append(
            Discovered(
                method=str(request.get("method", "GET")),
                template=template,
                sample=url,
                params=params,
                source="har",
                status=response.get("status"),
                content_type=content.get("mimeType"),
            )
        )
    endpoints = merge_discovered(items)
    return DiscoverResult(endpoints=endpoints, hosts=hosts_of(endpoints))


# -- identities (from the engagement's browser sessions) -------------------------------------


@dataclass
class Identity:
    """One caller the matrix probes as: a browser session's headers and cookies, or anonymous."""

    name: str
    admin: bool
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)


def _identity_of(meta: dict[str, Any]) -> Identity:
    name = str(meta.get("name", ""))
    role = str(meta.get("role") or "")
    cookies: list[dict[str, Any]] = []
    state_path = meta.get("_state_path")
    if state_path and Path(state_path).exists():
        try:
            cookies = json.loads(Path(state_path).read_text(encoding="utf-8")).get("cookies", [])
        except (OSError, ValueError):
            cookies = []
    return Identity(
        name=name,
        admin=bool(re.search(r"admin|root|super|owner", f"{role} {name}", re.IGNORECASE)),
        headers=dict(meta.get("extra_headers") or {}),
        cookies=[c for c in cookies if isinstance(c, dict) and c.get("name")],
    )


def _session_metas(store: EngagementStore) -> list[dict[str, Any]]:
    """Session metadata dicts (storage-state path attached), or [] when the module is absent."""
    try:
        from intern.engagement.browser_sessions import SessionStore
    except ModuleNotFoundError:
        return []
    try:
        session_store = SessionStore(store)
        metas = session_store.list()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for meta in metas:
        if isinstance(meta, dict):
            enriched = dict(meta)
            loaded = session_store.load(str(meta.get("name", "")))
            if loaded is not None:
                enriched["_state_path"] = str(loaded.state_path)
            out.append(enriched)
    return out


def list_identities(store: EngagementStore) -> list[Identity]:
    """anonymous + every stored browser session; replaying one needs no browser, only its headers
    and cookies."""
    identities = [Identity(name="anonymous", admin=False)]
    identities.extend(_identity_of(meta) for meta in _session_metas(store))
    return identities


# -- the method × identity matrix ------------------------------------------------------------


@dataclass
class ProbeTarget:
    id: str
    method: str
    url: str
    sample: str | None = None


@dataclass
class Cell:
    identity: str
    method: str
    status: int
    allow: str | None = None
    type: str | None = None


@dataclass
class ProbeRow:
    id: str
    url: str
    cells: list[Cell] = field(default_factory=list)


@dataclass
class Anomaly:
    id: str
    url: str
    kind: str
    detail: str
    owasp: list[str]


def client_for(identity: Identity, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """A client that speaks as one identity: its headers and its session's cookies."""
    client = httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_S, headers=identity.headers, follow_redirects=False, transport=transport
    )
    for cookie in identity.cookies:
        client.cookies.set(
            str(cookie.get("name")),
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain") or ""),
            path=str(cookie.get("path") or "/"),
        )
    return client


def _client_factory(identity: Identity) -> httpx.AsyncClient:
    return client_for(identity)


async def probe_matrix(
    targets: list[ProbeTarget],
    identities: list[Identity],
    *,
    unsafe: bool,
    client_factory: Any = _client_factory,
) -> tuple[list[ProbeRow], list[str], bool]:
    """Request every (endpoint, identity, method) cell; 4-way concurrent, capped at MAX_REQUESTS."""
    methods = [*SAFE_METHODS, *(UNSAFE_METHODS if unsafe else [])]
    usable = [t for t in targets if "{" not in (t.sample or t.url)]
    skipped = [f"{t.method} {t.url} (no concrete sample URL)" for t in targets if "{" in (t.sample or t.url)]
    budget = max(1, MAX_REQUESTS // (len(methods) * max(1, len(identities))))
    chosen = usable[:budget]
    rows = {t.id: ProbeRow(id=t.id, url=t.sample or t.url) for t in chosen}
    clients = {identity.name: client_factory(identity) for identity in identities}
    jobs = [(t, identity, method) for t in chosen for identity in identities for method in methods]
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(target: ProbeTarget, identity: Identity, method: str) -> None:
        async with gate:
            try:
                response = await clients[identity.name].request(
                    method, target.sample or target.url, content=b"{}" if method in UNSAFE_METHODS else None
                )
                rows[target.id].cells.append(
                    Cell(
                        identity=identity.name,
                        method=method,
                        status=response.status_code,
                        allow=response.headers.get("allow"),
                        type=(response.headers.get("content-type") or "").split(";")[0] or None,
                    )
                )
            except httpx.HTTPError:
                rows[target.id].cells.append(Cell(identity=identity.name, method=method, status=0))

    try:
        await asyncio.gather(*(one(*job) for job in jobs))
    finally:
        for client in clients.values():
            await client.aclose()
    return [rows[t.id] for t in chosen], skipped, len(usable) > len(chosen)


ADMIN_PATH = re.compile(
    r"/(admin|internal|manage|management|staff|superuser|root|debug|actuator|ops|system|backoffice)(/|$)",
    re.IGNORECASE,
)


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _denied(status: int) -> bool:
    return status in (401, 403)


def _cell_of(row: ProbeRow, who: str, method: str = "GET") -> Cell | None:
    return next((c for c in row.cells if c.identity == who and c.method == method), None)


def anomalies(rows: list[ProbeRow], identities: list[Identity]) -> list[Anomaly]:
    """What in the matrix deserves a closer look (hypotheses for the agent, not findings)."""
    out: list[Anomaly] = []
    for row in rows:
        anon = _cell_of(row, "anonymous")
        authed = [i for i in identities if i.name != "anonymous"]
        refused = [i.name for i in authed if _denied(c.status if (c := _cell_of(row, i.name)) else 0)]
        if anon and _ok(anon.status) and refused:
            out.append(
                Anomaly(
                    id=row.id,
                    url=row.url,
                    kind="auth-inconsistency",
                    detail=f"anonymous GET {anon.status} while {', '.join(refused)} are refused",
                    owasp=["A01", "API2"],
                )
            )
        elif anon and _ok(anon.status) and re.search(r"json|xml", anon.type or "") and API_PATH_RE.search(row.url):
            out.append(
                Anomaly(
                    id=row.id,
                    url=row.url,
                    kind="unauthenticated-api",
                    detail=f"anonymous GET {anon.status} ({anon.type}) — check whether the data is meant to be public",
                    owasp=["API1", "API2"],
                )
            )
        if ADMIN_PATH.search(row.url):
            for identity in authed:
                if identity.admin:
                    continue
                cell = _cell_of(row, identity.name)
                if cell and _ok(cell.status):
                    out.append(
                        Anomaly(
                            id=row.id,
                            url=row.url,
                            kind="admin-route-reachable",
                            detail=f"{identity.name} (non-admin) GET {cell.status} on an admin-looking route",
                            owasp=["A01", "API5"],
                        )
                    )
        allow = next((c.allow for c in row.cells if c.allow), None)
        writes = [m.strip().upper() for m in (allow or "").split(",") if m.strip().upper() in UNSAFE_METHODS]
        if writes:
            out.append(
                Anomaly(
                    id=row.id,
                    url=row.url,
                    kind="write-methods-advertised",
                    detail=f"Allow: {allow} — test {'/'.join(writes)} per identity"
                    " (needs unsafe=true and operator approval)",
                    owasp=["API5"],
                )
            )
        for cell in [c for c in row.cells if c.method in UNSAFE_METHODS and _ok(c.status)]:
            out.append(
                Anomaly(
                    id=row.id,
                    url=row.url,
                    kind="write-accepted",
                    detail=f"{cell.identity} {cell.method} → {cell.status}",
                    owasp=["A01", "API2"] if cell.identity == "anonymous" else ["API5"],
                )
            )
        errors = [c for c in row.cells if c.status >= 500]
        if errors:
            out.append(
                Anomaly(
                    id=row.id,
                    url=row.url,
                    kind="server-error",
                    detail=", ".join(f"{c.identity} {c.method} → {c.status}" for c in errors),
                    owasp=["A10"],
                )
            )
    return out


def render_matrix(rows: list[ProbeRow], limit: int = 25) -> str:
    """Compact text matrix: one line per endpoint, identities × methods."""
    lines = []
    for row in rows[:limit]:
        who = []
        for name in dict.fromkeys(c.identity for c in row.cells):
            cells = " ".join(f"{c.method} {c.status or 'ERR'}" for c in row.cells if c.identity == name)
            who.append(f"{name}: {cells}")
        lines.append(f"{row.url}\n    {' | '.join(who)}")
    if len(rows) > limit:
        lines.append(f"… {len(rows) - limit} more endpoints (state_query what=endpoints)")
    return "\n".join(lines)


# -- the tools --------------------------------------------------------------------------------


class ApiDiscoverArgs(BaseModel):
    source: Literal["crawl", "openapi", "har"]
    url: str | None = None
    file: str | None = None
    session: str | None = Field(default=None, description="crawl/openapi: identity from a browser_session")
    max_pages: int | None = None


class ApiDiscoverTool(Tool):
    """Build the API inventory from what the app really uses."""

    name = "api_discover"
    description = (
        "Build the API inventory from what the app really uses (recorded as endpoints with route templates like "
        "/users/{id}, a concrete sample URL, params and source; new hosts are added and fed to the pivot engine). "
        "source=crawl: drive the app in a browser as an identity (session=<browser_session name>) from url, "
        "following same-origin links (never logout/delete links), recording every API request and form — "
        "max_pages (default 25). source=openapi: import an OpenAPI 3 / Swagger 2 spec from url "
        "(e.g. /v3/api-docs, /swagger.json, /openapi.yaml) or a local file. source=har: import a HAR file. "
        "Run it per identity (anonymous, each role) — different roles see different APIs. "
        "Then test the inventory with api_probe."
    )
    Args = ApiDiscoverArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "network"})

    def __init__(self, store: EngagementStore, client: httpx.AsyncClient | None = None) -> None:
        self._store = store
        self._client = client

    async def run(self, args: ApiDiscoverArgs, ctx: ToolContext) -> ToolOutput:
        session = self._find_session(args.session)
        if args.session and session is None:
            return fail(f'no browser session "{args.session}"', "browser_session action=list lists the names.")
        try:
            if args.source == "crawl":
                if not args.url:
                    return fail("crawl needs url", "Pass the page to start from.")
                result = await crawl(args.url, session=session, max_pages=args.max_pages)
            elif args.source == "openapi":
                if not args.url and not args.file:
                    return fail("openapi needs url or file", "Pass the spec URL or a local file path.")
                source = args.url or self._work_path(args.file or "").as_posix()
                headers = dict((session.meta.get("extra_headers") if session else None) or {})
                result = await from_openapi(source, headers=headers, client=self._client)
            else:
                if not args.file:
                    return fail("har needs file", "Pass the path to the exported HAR.")
                result = from_har(self._work_path(args.file))
        except DiscoverError as error:
            return fail(str(error), "Fix the source (URL/file/session name) and retry.")
        added, hosts = self._record(result, session)
        name = session.meta.get("name") if session else None
        self._store.log(
            "api",
            f"discover {args.source}{f' as {name}' if name else ''}: "
            f"{len(result.endpoints)} endpoints ({added} new), {hosts} new host(s)",
        )
        sample = "\n".join(
            f"{d.method:<6} {d.template}"
            + (f"  ?{'&'.join(d.params)}" if d.params else "")
            + (f"  → {d.status}" if d.status else "")
            for d in result.endpoints[:25]
        )
        more = (
            f"\n… {len(result.endpoints) - 25} more (state_query what=endpoints)" if len(result.endpoints) > 25 else ""
        )
        pages = f" from {result.pages} page(s)" if result.pages else ""
        warn = f"\n⚠ {result.note}" if result.note else ""
        return ToolOutput(
            content=(
                f"{args.source}: {len(result.endpoints)} endpoint(s) ({added} new in the inventory){pages}, "
                f"{len(result.hosts)} host(s) ({hosts} new){warn}\n{sample}{more}\nNext: api_probe over the inventory."
            )
        )

    def _work_path(self, file: str) -> Path:
        candidate = Path(file)
        return candidate if candidate.is_absolute() else self._store.work_dir / candidate

    def _find_session(self, name: str | None) -> Any:
        if not name:
            return None
        try:
            from intern.engagement.browser_sessions import SessionStore
        except ModuleNotFoundError:
            return None
        return SessionStore(self._store).load(name)

    def _record(self, result: DiscoverResult, session: Any) -> tuple[int, int]:
        """Endpoints into the inventory (dedup by method+template); fresh hosts become hosts."""
        before = len(self._store.state.endpoints)
        auth = f"session:{session.meta.get('name')}" if session else "none"
        for d in result.endpoints:
            self._store.add_endpoint(
                method=d.method,
                url=d.template,
                sample=d.sample,
                params=d.params,
                source=d.source,
                auth=auth,
                status=d.status,
                title=f"discovered ({d.source})",
                result=f"params: {', '.join(d.params)}" if d.params else None,
            )
        known = {h for host in self._store.state.hosts.values() for h in (host.ip, host.hostname)}
        fresh = [h for h in result.hosts if h not in known]
        for host in fresh:
            self._store.add_host(host, hostname=host, source="api-discovery")
        return len(self._store.state.endpoints) - before, len(fresh)


class ApiProbeArgs(BaseModel):
    endpoints: list[str] | None = None
    identities: list[str] | None = None
    unsafe: bool | None = Field(
        default=None,
        description="also send POST/PUT/PATCH/DELETE — this flag IS the operator's approval for the run",
    )
    reason: str | None = Field(default=None, description="why write methods are needed (shown with the matrix)")


class ApiProbeTool(Tool):
    """Test the API inventory as a method × identity matrix."""

    name = "api_probe"
    description = (
        "Test the API inventory as a method × identity matrix and flag what deserves a closer look: anonymous "
        "access where authenticated identities are refused, unauthenticated JSON APIs, admin-looking routes "
        "answering non-admins, write methods advertised (Allow) or accepted, server errors. Identities: anonymous "
        "+ every browser_session (or pass identities=[names, 'anonymous']). Methods: OPTIONS, HEAD, GET by "
        "default — POST/PUT/PATCH/DELETE only with unsafe=true, which IS the operator's confirmation for this run "
        "(they may change data). endpoints = ids from state_query what=endpoints (default: every endpoint not "
        "probed yet that has a concrete URL). Requests go over plain HTTP with each identity's headers/cookies. "
        "Results are stored on each endpoint; anomalies are hypotheses — confirm each with a differential before "
        "recording a vuln."
    )
    Args = ApiProbeArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "network"})

    def __init__(self, store: EngagementStore, client_factory: Any = _client_factory) -> None:
        self._store = store
        self._client_factory = client_factory

    async def run(self, args: ApiProbeArgs, ctx: ToolContext) -> ToolOutput:
        available = list_identities(self._store)
        if args.identities:
            identities = []
            for name in args.identities:
                identity = next((i for i in available if i.name == name), None)
                if identity is None:
                    return fail(f'no browser session "{name}"', "browser_session action=list lists the names.")
                identities.append(identity)
        else:
            identities = available
        all_endpoints = self._store.state.endpoints
        wanted = (
            [e for e in all_endpoints if e.id in args.endpoints]
            if args.endpoints
            else [e for e in all_endpoints if not e.matrix]
        )
        targets = [
            ProbeTarget(id=e.id, method=e.method, url=e.url, sample=e.sample or (None if "{" in e.url else e.url))
            for e in wanted
        ]
        if not targets:
            return ToolOutput(
                content=(
                    "nothing to probe — build the inventory with api_discover / js_analyze first (or pass endpoint ids)"
                )
            )
        unsafe = bool(args.unsafe)
        rows, skipped, capped = await probe_matrix(
            targets, identities, unsafe=unsafe, client_factory=self._client_factory
        )
        by_id = {e.id: e for e in all_endpoints}
        for row in rows:
            endpoint = by_id[row.id]
            self._store.add_endpoint(
                method=endpoint.method,
                url=endpoint.url,
                title="probed (method × identity)",
                matrix=[
                    ProbeCell(identity=c.identity, method=c.method, status=c.status, allow=c.allow) for c in row.cells
                ],
            )
        found = anomalies(rows, identities)
        for anomaly in found:
            endpoint = by_id[anomaly.id]
            self._store.add_endpoint(
                method=endpoint.method,
                url=endpoint.url,
                title="probed (method × identity)",
                interesting=True,
                result=f"{anomaly.kind}: {anomaly.detail}",
                owasp=anomaly.owasp,
            )
        who = ", ".join(i.name for i in identities)
        methods_note = "all methods" if unsafe else "OPTIONS/HEAD/GET"
        plural = "y" if len(identities) == 1 else "ies"
        self._store.log(
            "api",
            f"probe {len(rows)} endpoint(s) × {len(identities)} identit{plural}"
            f"{' (unsafe methods)' if unsafe else ''}: {len(found)} anomalies",
        )
        if found:
            anomaly_text = "\n\nANOMALIES (hypotheses — confirm with a differential, then record vulns):\n" + "\n".join(
                f"- [{a.kind}] {a.url}: {a.detail} ({', '.join(a.owasp)})" for a in found
            )
        else:
            anomaly_text = "\n\nno anomalies in this matrix."
        notes = ""
        if skipped:
            notes += (
                f"\n⚠ {len(skipped)} endpoint(s) skipped (route templates without a concrete sample — "
                "crawl or call them once to get one)"
            )
        if capped:
            notes += "\n⚠ request cap reached — run again for the remaining endpoints"
        reason = f"{args.reason}\n\n" if args.reason else ""
        return ToolOutput(
            content=(
                f"{reason}probed {len(rows)} endpoint(s) as {who} with {methods_note}:\n"
                + render_matrix(rows)
                + anomaly_text
                + notes
            )
        )

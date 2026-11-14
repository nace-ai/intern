"""The surface tools (oast, js_analyze, api_discover/api_probe, host_tools/install_tools) against
faked HTTP, a faked executor and a real engagement store — no network, no browser."""

from __future__ import annotations

import asyncio
import json
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from intern.engagement import api_tools, host_tooling, jsintel, oast
from intern.engagement.api_tools import ApiDiscoverTool, ApiProbeTool, Discovered
from intern.engagement.host_tooling import HostToolsTool, InstallToolsTool
from intern.engagement.jsintel import JsAnalyzeTool, extract_intel, mask_secret
from intern.engagement.oast import XID, ZBASE32, OastStore, OastTool, correlation_id, nonce, payload_domain
from intern.engagement.store import EngagementStore
from intern.executor import ExecResult
from intern.tools.base import ToolContext

# -- helpers ------------------------------------------------------------------------------------


async def call(tool: Any, ctx: ToolContext, **arguments: Any) -> Any:
    parsed = tool.parse(arguments)
    assert isinstance(parsed, BaseModel), getattr(parsed, "content", parsed)
    result = await tool.run(parsed, ctx)
    assert result.status == "ok", result.content
    return result


async def run_raw(tool: Any, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    """Run with arguments expected to fail validation or the tool's own checks."""
    parsed = tool.parse(arguments)
    assert isinstance(parsed, BaseModel), getattr(parsed, "content", parsed)
    return await tool.run(parsed, ctx)


@pytest.fixture
def store(tmp_path: Path) -> EngagementStore:
    return EngagementStore(out_root=tmp_path, slug="surface", target="https://app.target.example")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    from intern.executor import LocalExecutor

    return ToolContext(cwd=tmp_path, executor=LocalExecutor(), abort=asyncio.Event(), max_output_chars=100_000)


@dataclass
class FakeExecutor:
    commands: list[str]
    exit_code: int = 0
    output: str = "installed"

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_s: float,
        max_output_bytes: int,
        abort: asyncio.Event | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        self.commands.append(command)
        return ExecResult(
            exit_code=self.exit_code,
            output=self.output,
            omitted_bytes=0,
            timed_out=False,
            aborted=False,
            duration_ms=1,
        )


def mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def write_session(
    store: EngagementStore,
    name: str,
    role: str = "user",
    headers: dict[str, str] | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> None:
    """A stored browser identity exactly as browser_sessions persists it (meta + storage state)."""
    logins = store.private_dir / "logins"
    logins.mkdir(parents=True, exist_ok=True)
    (logins / f"{name}.meta.json").write_text(
        json.dumps(
            {
                "name": name,
                "role": role,
                "origin": "https://app.target.example",
                "method": "import",
                "extra_headers": headers or {},
                "created": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )
    (logins / f"{name}.state.json").write_text(json.dumps({"cookies": cookies or [], "origins": []}), encoding="utf-8")


# -- oast: crypto roundtrip -----------------------------------------------------------------------


def test_oast_crypto_roundtrip_ctr_and_cfb() -> None:
    pytest.importorskip("cryptography", reason="OAST crypto needs the optional cryptography package")
    private_pem, public_b64 = oast.generate_keypair()
    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    public_pem = __import__("base64").b64decode(public_b64).decode()
    assert public_pem.startswith("-----BEGIN RSA PUBLIC KEY-----")
    record = {
        "protocol": "http",
        "unique-id": "u1",
        "full-id": "u1.x.oast.pro",
        "remote-address": "203.0.113.9",
        "raw-request": "GET / HTTP/1.1\r\nHost: x",
    }
    for mode in ("ctr", "cfb"):
        aes_key, data = oast.encrypt_record(public_pem, record, mode=mode)
        back = oast.decrypt_record(private_pem, aes_key, data)
        assert back is not None, mode
        assert back.protocol == "http" and back.remote_address == "203.0.113.9"
    # too short to hold an IV
    import base64

    assert (
        oast.decrypt_record(
            private_pem, oast.encrypt_record(public_pem, record)[0], base64.b64encode(b"short").decode()
        )
        is None
    )


def test_oast_correlation_id_encodes_its_timestamp() -> None:
    cid = correlation_id(1_700_000_000_000)
    assert len(cid) == 20 and all(c in XID for c in cid)
    bits = "".join(f"{XID.index(c):05b}" for c in cid)
    raw = bytes(int(bits[i : i + 8], 2) for i in range(0, 8 * 12, 8))
    assert struct.unpack(">I", raw[:4])[0] == 1_700_000_000
    assert correlation_id() != correlation_id()
    assert all(c in ZBASE32 for c in nonce())


def test_oast_payload_domain_shape_and_matching(store: EngagementStore) -> None:
    session_like = type("S", (), {"correlation_id": correlation_id(), "host": "oast.pro"})()
    domain = payload_domain(session_like)
    label = domain.split(".")[0]
    assert len(label) == 33 and domain.endswith(".oast.pro")
    tagged = payload_domain(session_like, "SSRF Avatar URL!!")
    assert tagged.startswith("ssrf-avatar-url.")
    tagged_label = tagged.split(".")[1]
    assert tagged_label[:20] == label[:20]  # same correlation id, fresh nonce per payload
    assert tagged_label != label

    from intern.engagement.oast import Interaction, PayloadRecord, payload_for

    payload = PayloadRecord(domain=domain, tag="vec", vuln_id=None, ts=0)
    hit = Interaction.model_validate({"protocol": "dns", "full-id": f"{label}.oast.pro", "remote-address": "1.2.3.4"})
    other = Interaction.model_validate(
        {"protocol": "dns", "full-id": "z" * 33 + ".oast.pro", "remote-address": "1.2.3.4"}
    )
    assert payload_for(hit, [payload]) is payload
    assert payload_for(other, [payload]) is None


# -- oast: the tool against a fake interactsh server ------------------------------------------------


def oast_handlers(store: EngagementStore, expired: bool = False) -> Any:
    oast_paths = OastStore(store)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/register":
            body = json.loads(request.content)
            assert set(body) == {"public-key", "secret-key", "correlation-id"}
            return httpx.Response(200, json={"message": "1 confirmation email sent "})
        if request.url.path == "/poll":
            if expired:
                return httpx.Response(404, text="could not get correlation-id")
            payloads = oast_paths.read_payloads()
            extra = []
            for payload in payloads:
                extra.append(
                    json.dumps(
                        {
                            "protocol": "dns",
                            "q-type": "A",
                            "unique-id": payload.domain,
                            "full-id": payload.domain,
                            "remote-address": "203.0.113.7",
                            "raw-request": "GET /x HTTP/1.1\nHost: t",
                            "timestamp": "2026-11-14T12:00:00Z",
                        }
                    )
                )
            return httpx.Response(200, json={"data": [], "aes_key": "", "extra": extra, "tlddata": []})
        raise AssertionError(f"unexpected {request.url.path}")

    return handler


async def test_oast_tool_payload_poll_attach(
    store: EngagementStore, ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERACTSH_SERVER", "oast.pro")  # one fake server, deterministic domain
    vuln = store.add_vuln({"host": "app.target.example", "title": "blind SSRF in avatar upload"})
    tool = OastTool(store, client=mock_client(oast_handlers(store)))

    payload = await call(tool, ctx, action="payload", tag="ssrf-avatar", vuln_id=vuln.id, purpose="avatar url")
    domain = payload.content.splitlines()[0].removeprefix("payload: ")
    assert domain.endswith(".oast.pro") and "ssrf-avatar." in domain
    assert OastStore(store).read_session() is not None

    status = await call(tool, ctx, action="status")
    assert "1 payload(s)" in status.content and "0 interaction(s)" in status.content

    polled = await call(tool, ctx, action="poll", tag="ssrf-avatar")
    assert "1 new" in polled.content and "DNS A" in polled.content
    recorded = OastStore(store).recorded_interactions()
    assert len(recorded) == 1 and recorded[0].full_id == domain
    # the poll attached the callbacks to the finding as evidence
    assert any(vuln.id in e.vuln_ids for e in store.state.evidence)

    describe = await call(tool, ctx, action="describe", tag="ssrf-avatar")
    assert "203.0.113.7" in describe.content
    attach = await call(tool, ctx, action="attach", vuln_id=vuln.id, tag="ssrf-avatar")
    assert f"attached 1 interaction(s) to {vuln.id}" in attach.content


async def test_oast_tool_expired_session_is_dropped(store: EngagementStore, ctx: ToolContext) -> None:
    tool = OastTool(store, client=mock_client(oast_handlers(store, expired=True)))
    await call(tool, ctx, action="payload", tag="x")
    result = await run_raw(tool, {"action": "poll"}, ctx)
    assert result.status == "error" and "expired" in result.content
    assert OastStore(store).read_session() is None


async def test_oast_attach_refuses_unknown_vuln(store: EngagementStore, ctx: ToolContext) -> None:
    tool = OastTool(store, client=mock_client(oast_handlers(store)))
    result = await run_raw(tool, {"action": "attach", "vuln_id": "v9-nope"}, ctx)
    assert result.status == "error" and "no finding" in result.content


# -- jsintel: extraction rules ----------------------------------------------------------------------


FIXTURE_JS = """
const API_BASE = "/api/v2/users";
fetch("/api/v2/orders/12345?limit=20&cursor=abc");
const internal = "wss://rt.internal.corp/ws";
const cdn = "https://w3.org/2001/XMLSchema";
const repo = "https://github.com/org/repo";
const adminApi = "/admin/audit";
const K = "AKIA2MFHWB7TGTFXYQZ1";
const pw = fetch("/login", {password: "hunter2hunter2hunter2"});
const ph = {api_key: "your_placeholder_key_here"};
const tok = {secret: "xxxxxxxxxxxxxxxxxxxx"};
graphql: query GetUserList { users { id } }
mutation UpdateUser($id: ID!) { updateUser(id: $id) { ok } }
flags: "enableBetaDashboard", "feature-flag-new-checkout", "notAFlag";
"""


def test_jsintel_extracts_routes_graphql_admin_secrets_and_filters_noise() -> None:
    intel = extract_intel([jsintel.CollectedFile(name="app.js", text=FIXTURE_JS)])
    assert "/api/v2/users" in intel.routes
    assert "/admin/audit" in intel.admin_routes and "/api/v2/users" in intel.routes
    assert "query GetUserList" in intel.graphql and "mutation UpdateUser" in intel.graphql
    assert "enableBetaDashboard" in intel.flags and "feature-flag-new-checkout" in intel.flags
    kinds = {s.kind: s for s in intel.secrets}
    assert "aws-access-key" in kinds and kinds["aws-access-key"].value == "AKIA2MFHWB7TGTFXYQZ1"
    assert kinds["aws-access-key"].line == 8
    assert "generic-secret" in kinds and kinds["generic-secret"].value == "hunter2hunter2hunter2"
    assert "your_placeholder_key_here" not in {s.value for s in intel.secrets}  # placeholder filtered
    assert "xxxxxxxxxxxxxxxxxxxx" not in {s.value for s in intel.secrets}  # masked-out value filtered
    hosts = {h.host: h for h in intel.hosts}
    assert "rt.internal.corp" in hosts and hosts["rt.internal.corp"].reach == "internal"
    assert "w3.org" not in hosts and "github.com" not in hosts
    assert "w3.org" not in {jsintel._hostname_of(u) for u in intel.urls}
    assert "wss://rt.internal.corp/ws" in intel.websockets


def test_jsintel_masks_secrets_for_display() -> None:
    assert mask_secret("short") == "••••"
    assert mask_secret("AKIA2MFHWB7TGTFXYQZ1") == "AKIA2M••••Z1"


def js_handlers(store: EngagementStore) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("app.js"):
            body = FIXTURE_JS + "\n//# sourceMappingURL=app.js.map"
            return httpx.Response(200, text=body, headers={"content-type": "application/javascript"})
        if request.url.path.endswith("app.js.map"):
            return httpx.Response(
                200,
                json={
                    "sources": ["webpack:///src/api.js", "webpack:///node_modules/left-pad/index.js"],
                    "sourcesContent": [
                        'const SRC = "/api/v2/from-map";\nconst M = "SK" + "0123456789abcdef0123456789abcdef";',
                        None,
                    ],
                },
            )
        raise AssertionError(request.url.path)

    return handler


async def test_js_analyze_records_routes_hosts_creds_and_writes_intel(store: EngagementStore, ctx: ToolContext) -> None:
    tool = JsAnalyzeTool(store, client=mock_client(js_handlers(store)))
    result = await call(tool, ctx, scripts=["https://app.target.example/static/app.js"])
    assert "routes:" in result.content and "source map" in result.content
    endpoints = {(e.method, e.url, e.source) for e in store.state.endpoints}
    assert ("GET", "https://app.target.example/api/v2/users", "js") in endpoints
    assert ("GET", "https://app.target.example/api/v2/orders/{id}", "js") in endpoints
    assert ("GET", "https://app.target.example/api/v2/from-map", "js") in endpoints  # source map source
    hosts = {h.hostname for h in store.state.hosts.values()}
    assert "rt.internal.corp" in hosts
    assert any(c.secret == "AKIA2MFHWB7TGTFXYQZ1" and "unverified" in (c.source or "") for c in store.state.creds)
    files = list((store.tool_dir("js-intel")).glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["sourceMaps"] and "app.js.map" in saved["sourceMaps"][0]
    assert all("••••" in s["value"] for s in saved["secrets"]), "the intel file masks secret values"


async def test_js_analyze_needs_url_or_scripts(store: EngagementStore, ctx: ToolContext) -> None:
    result = await run_raw(JsAnalyzeTool(store), {}, ctx)
    assert result.status == "error" and "url" in result.content


# -- api inventory ---------------------------------------------------------------------------------


def test_template_of_applies_segment_rules() -> None:
    template, params = api_tools.template_of(
        "https://Api.Example.com/api/users/42/orders/550e8400-e29b-41d4-a716-446655440000?limit=9&limit=9&cursor=x"
    )
    assert template == "https://api.example.com/api/users/{id}/orders/{uuid}"
    assert params == ["limit", "cursor"]
    assert api_tools.template_of("https://x.example/t/abc123def456abc123de")[0].endswith("/t/{hash}")
    assert api_tools.template_of("https://x.example/u/user@example.com")[0].endswith("/u/{email}")
    assert api_tools.template_of("https://x.example/a/abcdefghijklmnopqrstuvwx")[0].endswith("/a/{token}")
    assert api_tools.concrete("https://x.example/api/users/{id}") is None
    assert api_tools.concrete("https://x.example/api/users") == "https://x.example/api/users"


def test_is_api_like_filters_static_and_analytics() -> None:
    assert api_tools.is_api_like("https://x.example/api/users")
    assert api_tools.is_api_like("https://x.example/anything", resource_type="xhr")
    assert api_tools.is_api_like("https://x.example/data", content_type="application/json")
    assert not api_tools.is_api_like("https://x.example/static/app.js")
    assert not api_tools.is_api_like("https://x.example/api/users.png")
    assert not api_tools.is_api_like("https://www.google-analytics.com/collect?v=1")


def test_merge_discovered_unions_params_and_keeps_first_sample() -> None:
    merged = api_tools.merge_discovered(
        [
            Discovered(
                method="get",
                template="https://x.example/api/users",
                sample="https://x.example/api/users?b=2",
                params=["b"],
                source="crawl",
                status=None,
            ),
            Discovered(
                method="GET",
                template="https://x.example/api/users",
                sample="https://x.example/api/users?a=1",
                params=["a"],
                source="har",
                status=200,
            ),
            Discovered(
                method="GET",
                template="https://x.example/api/orders",
                sample="https://x.example/api/orders",
                source="har",
                status=200,
            ),
        ]
    )
    assert len(merged) == 2
    users = next(d for d in merged if d.template.endswith("/users"))
    assert users.params == ["b", "a"] and users.status == 200
    assert users.sample.endswith("?b=2")


def test_spec_endpoints_openapi3_and_swagger2() -> None:
    openapi = {
        "openapi": "3.0.0",
        "servers": [{"url": "/api/v3"}],
        "paths": {
            "/users": {
                "get": {"parameters": [{"name": "limit", "in": "query"}]},
                "post": {"parameters": [{"name": "X", "in": "header"}]},
                "parameters": [{"name": "team", "in": "path"}],
            },
            "/users/{id}": {"delete": {}},
            "x-internal": {},
        },
    }
    endpoints = {
        f"{d.method} {d.template}": d for d in api_tools.spec_endpoints(openapi, "https://spec.example/v3/api-docs")
    }
    assert set(endpoints) == {
        "GET https://spec.example/api/v3/users",
        "POST https://spec.example/api/v3/users",
        "DELETE https://spec.example/api/v3/users/{id}",
    }
    get = endpoints["GET https://spec.example/api/v3/users"]
    assert get.params == ["limit"]  # header params and the path-level trap excluded
    swagger = {
        "swagger": "2.0",
        "host": "api.old.example",
        "basePath": "/v2",
        "schemes": ["https"],
        "paths": {"/pets": {"get": {}}},
    }
    pets = api_tools.spec_endpoints(swagger, None)
    assert pets[0].template == "https://api.old.example/v2/pets"
    with pytest.raises(api_tools.DiscoverError, match="no `paths`"):
        api_tools.spec_endpoints({"openapi": "3.0.0"}, None)


async def test_from_openapi_url_file_and_yaml(store: EngagementStore, tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://app.target.example"}],
        "paths": {"/api/v2/orders": {"get": {"parameters": [{"name": "since", "in": "query"}]}}},
    }
    client = mock_client(lambda request: httpx.Response(200, json=spec))
    result = await api_tools.from_openapi("https://app.target.example/openapi.json", client=client)
    assert result.endpoints[0].template == "https://app.target.example/api/v2/orders"
    assert result.hosts == ["app.target.example"]

    local = tmp_path / "spec.json"
    local.write_text(json.dumps(spec), encoding="utf-8")
    from_file = await api_tools.from_openapi(str(local))
    assert from_file.endpoints == result.endpoints

    yaml = pytest.importorskip("yaml", reason="YAML specs need the optional pyyaml")
    local_yaml = tmp_path / "spec.yaml"
    local_yaml.write_text(
        "openapi: 3.0.0\nservers:\n  - url: https://app.target.example\npaths:\n"
        "  /api/v2/orders:\n    get:\n      parameters:\n        - name: since\n          in: query\n",
        encoding="utf-8",
    )
    from_yaml = await api_tools.from_openapi(str(local_yaml))
    assert from_yaml.endpoints == result.endpoints
    assert yaml.__version__  # only to justify the importorskip


def har() -> dict[str, Any]:
    return {
        "log": {
            "entries": [
                {
                    "request": {"method": "GET", "url": "https://app.target.example/api/v2/users/42?fields=all"},
                    "response": {"status": 200, "content": {"mimeType": "application/json"}},
                    "_resourceType": "xhr",
                },
                {
                    "request": {"method": "POST", "url": "https://app.target.example/api/v2/uploads"},
                    "response": {"status": 403, "content": {"mimeType": "application/json"}},
                    "_resourceType": "xhr",
                },
                {
                    "request": {"method": "GET", "url": "https://app.target.example/static/logo.png"},
                    "response": {"status": 200, "content": {"mimeType": "image/png"}},
                    "_resourceType": "image",
                },
            ]
        }
    }


async def test_from_har_import_and_discover_tool_records(
    store: EngagementStore, ctx: ToolContext, tmp_path: Path
) -> None:
    file = tmp_path / "capture.har"
    file.write_text(json.dumps(har()), encoding="utf-8")
    result = api_tools.from_har(file)
    assert [f"{d.method} {d.template}" for d in result.endpoints] == [
        "GET https://app.target.example/api/v2/users/{id}",
        "POST https://app.target.example/api/v2/uploads",
    ]
    assert result.endpoints[0].params == ["fields"] and result.endpoints[0].status == 200

    (store.work_dir / "capture.har").parent.mkdir(parents=True, exist_ok=True)
    (store.work_dir / "capture.har").write_text(json.dumps(har()), encoding="utf-8")
    tool = ApiDiscoverTool(store)
    outcome = await call(tool, ctx, source="har", file="capture.har")
    assert "2 endpoint(s)" in outcome.content and "Next: api_probe" in outcome.content
    assert len(store.state.endpoints) == 2
    uploads = next(e for e in store.state.endpoints if e.url.endswith("/uploads"))
    assert uploads.method == "POST" and uploads.source == "har" and uploads.auth == "none"
    assert "app.target.example" in {h.hostname for h in store.state.hosts.values()}
    # a second import merges, it does not duplicate
    await call(tool, ctx, source="har", file="capture.har")
    assert len(store.state.endpoints) == 2


async def test_api_discover_openapi_and_missing_args(store: EngagementStore, ctx: ToolContext, tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://app.target.example"}],
        "paths": {"/api/v2/session": {"get": {}, "delete": {}}},
    }
    client = mock_client(lambda request: httpx.Response(200, json=spec))
    tool = ApiDiscoverTool(store, client=client)
    result = await run_raw(tool, {"source": "crawl"}, ctx)
    assert result.status == "error" and "crawl needs url" in result.content
    result = await run_raw(tool, {"source": "openapi"}, ctx)
    assert result.status == "error" and "url or file" in result.content
    result = await run_raw(tool, {"source": "openapi", "session": "nobody"}, ctx)
    assert result.status == "error" and 'no browser session "nobody"' in result.content
    outcome = await call(tool, ctx, source="openapi", url="https://app.target.example/v3/api-docs")
    assert "2 endpoint(s)" in outcome.content
    session_ep = next(e for e in store.state.endpoints if e.method == "DELETE")
    assert session_ep.url == "https://app.target.example/api/v2/session"


# -- api probe -------------------------------------------------------------------------------------


def probe_row(id_: str, url: str, cells: list[tuple[str, str, int, str | None, str | None]]) -> api_tools.ProbeRow:
    return api_tools.ProbeRow(
        id=id_,
        url=url,
        cells=[api_tools.Cell(identity=i, method=m, status=s, allow=a, type=t) for i, m, s, a, t in cells],
    )


IDENTITIES = [
    api_tools.Identity(name="anonymous", admin=False),
    api_tools.Identity(name="customer", admin=False),
    api_tools.Identity(name="admin", admin=True),
]


def test_anomalies_flag_every_kind() -> None:
    rows = [
        probe_row(
            "e1",
            "https://x.example/api/orders",
            [
                ("anonymous", "GET", 200, None, "application/json"),
                ("customer", "GET", 403, None, None),
            ],
        ),
        probe_row(
            "e2",
            "https://x.example/api/public-data",
            [
                ("anonymous", "GET", 200, None, "application/json"),
            ],
        ),
        probe_row(
            "e3",
            "https://x.example/admin/users",
            [
                ("customer", "GET", 200, None, None),
                ("admin", "GET", 200, None, None),
            ],
        ),
        probe_row(
            "e4",
            "https://x.example/api/things",
            [
                ("anonymous", "OPTIONS", 204, "GET, POST, DELETE", None),
            ],
        ),
        probe_row(
            "e5",
            "https://x.example/api/broken",
            [
                ("anonymous", "GET", 503, None, None),
            ],
        ),
        probe_row(
            "e6",
            "https://x.example/api/fine",
            [
                ("anonymous", "GET", 401, None, None),
            ],
        ),
    ]
    found = {a.kind: a for a in api_tools.anomalies(rows, IDENTITIES)}
    assert found["auth-inconsistency"].url.endswith("/api/orders")
    assert "customer" in found["auth-inconsistency"].detail
    assert found["unauthenticated-api"].url.endswith("/public-data")
    assert found["admin-route-reachable"].url.endswith("/admin/users")
    assert found["admin-route-reachable"].detail.startswith("customer (non-admin)")  # the admin identity is exempt
    assert "POST" in found["write-methods-advertised"].detail
    assert found["server-error"].url.endswith("/broken")
    assert "unauthenticated-api" not in {a.kind for a in api_tools.anomalies([rows[5]], IDENTITIES)}

    unsafe_row = probe_row(
        "e7",
        "https://x.example/api/writable",
        [
            ("anonymous", "POST", 201, None, None),
            ("customer", "DELETE", 200, None, None),
        ],
    )
    writes = [a for a in api_tools.anomalies([unsafe_row], IDENTITIES) if a.kind == "write-accepted"]
    assert {(w.split()[0], w.split()[1]) for w in (a.detail for a in writes)} == {
        ("anonymous", "POST"),
        ("customer", "DELETE"),
    }
    assert all(a.owasp == ["A01", "API2"] for a in writes if "anonymous" in a.detail)


def test_render_matrix_lists_identities_and_methods() -> None:
    rows = [
        probe_row(
            "e1",
            "https://x.example/api/a",
            [
                ("anonymous", "GET", 200, None, None),
                ("customer", "GET", 403, None, None),
            ],
        )
    ]
    text = api_tools.render_matrix(rows)
    assert "https://x.example/api/a" in text
    assert "anonymous: OPTIONS" not in text and "GET 200" in text and "customer: GET 403" in text


def probe_handler(seen: list[tuple[str, str, str]]) -> Any:
    """403 for the customer identity, 200 for anonymous/admin; OPTIONS advertises POST."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                str(request.url),
                request.headers.get("authorization") or (request.headers.get("cookie") or "")[:12],
            )
        )
        if request.method == "OPTIONS":
            return httpx.Response(204, headers={"allow": "GET, POST", "content-type": "text/plain"})
        who = (
            "customer"
            if "sid=customer" in request.headers.get("cookie", "")
            else ("admin" if request.headers.get("authorization") else "anonymous")
        )
        return httpx.Response(
            200 if who != "customer" else 403, headers={"content-type": "application/json"}, json={"ok": True}
        )

    return handler


async def test_api_probe_records_matrix_and_anomalies(store: EngagementStore, ctx: ToolContext) -> None:
    write_session(
        store,
        "customer",
        role="user",
        cookies=[
            {"name": "sid", "value": "customer", "domain": "app.target.example", "path": "/"},
        ],
    )
    write_session(store, "admin", role="administrator", headers={"authorization": "Bearer live-token"})
    store.add_endpoint(
        method="GET",
        url="https://app.target.example/api/v2/users/42",
        sample=None,
        source="js",
        title="discovered (js)",
    )
    store.add_endpoint(
        method="GET",
        url="https://app.target.example/api/v2/users/{id}",
        sample=None,
        source="js",
        title="discovered (js)",
    )

    seen: list[tuple[str, str, str]] = []
    tool = ApiProbeTool(store)
    identities = api_tools.list_identities(store)
    assert {i.name for i in identities} == {"anonymous", "customer", "admin"}
    admin_identity = next(i for i in identities if i.name == "admin")
    assert admin_identity.admin and admin_identity.headers == {"authorization": "Bearer live-token"}

    transport = httpx.MockTransport(probe_handler(seen))

    def factory(identity: api_tools.Identity) -> httpx.AsyncClient:
        return api_tools.client_for(identity, transport=transport)

    tool = ApiProbeTool(store, client_factory=factory)
    result = await call(tool, ctx)
    concrete = next(e for e in store.state.endpoints if e.url.endswith("42"))
    templated = next(e for e in store.state.endpoints if e.url.endswith("{id}"))
    assert concrete.matrix and {c.identity for c in concrete.matrix} == {"anonymous", "customer", "admin"}
    assert {c.method for c in concrete.matrix} == {"OPTIONS", "HEAD", "GET"}
    assert templated.matrix == []  # never requested: no concrete URL
    anon_get = next(c for c in concrete.matrix if c.identity == "anonymous" and c.method == "GET")
    customer_get = next(c for c in concrete.matrix if c.identity == "customer" and c.method == "GET")
    assert anon_get.status == 200 and customer_get.status == 403
    # identities really sent their own cookies / headers
    assert any(m == "GET" and "sid=customer" in ident for m, _, ident in seen)
    assert any(m == "GET" and ident == "Bearer live-token" for m, _, ident in seen)
    kinds = {(e.result or "").split(":", 1)[0] for e in store.state.endpoints if e.interesting}
    assert "write-methods-advertised" in kinds  # the last anomaly wins the endpoint's result line
    flagged = next(e for e in store.state.endpoints if e.interesting and "write-methods" in (e.result or ""))
    assert "API5" in flagged.owasp
    assert "auth-inconsistency" in result.content and "ANOMALIES" in result.content


async def test_api_probe_unsafe_sends_write_methods(store: EngagementStore, ctx: ToolContext) -> None:
    store.add_endpoint(method="GET", url="https://app.target.example/api/v2/things", sample=None, source="js")
    seen: list[tuple[str, str, str]] = []
    tool = ApiProbeTool(store, client_factory=lambda identity: mock_client(probe_handler(seen)))
    result = await call(tool, ctx, unsafe=True, reason="operator approved a write smoke test")
    assert "write smoke test" in result.content
    assert {m for m, _, _ in seen} >= {"POST", "PUT", "PATCH", "DELETE"}
    accepted = [
        a
        for a in api_tools.anomalies(
            [
                api_tools.ProbeRow(
                    id=e.id,
                    url=e.url,
                    cells=[api_tools.Cell(identity=c.identity, method=c.method, status=c.status) for c in e.matrix],
                )
                for e in store.state.endpoints
            ],
            [api_tools.Identity(name="anonymous", admin=False)],
        )
        if a.kind == "write-accepted"
    ]
    assert accepted, "write methods that returned 2xx are flagged"


async def test_api_probe_without_inventory_says_so(store: EngagementStore, ctx: ToolContext) -> None:
    result = await run_raw(ApiProbeTool(store), {}, ctx)
    assert result.status == "ok" and "nothing to probe" in result.content


async def test_api_probe_refuses_unknown_identity(store: EngagementStore, ctx: ToolContext) -> None:
    store.add_endpoint(method="GET", url="https://app.target.example/api/v2/x", sample=None, source="js")
    tool = ApiProbeTool(store, client_factory=lambda i: mock_client(probe_handler([])))
    result = await run_raw(tool, {"identities": ["ghost"]}, ctx)
    assert result.status == "error" and 'no browser session "ghost"' in result.content


# -- host tooling ----------------------------------------------------------------------------------


@dataclass
class FakeProbe:
    _which: set[str]
    system: str = "linux"
    machine: str = "x86_64"
    uid: int | None = 1000
    files: dict[str, str] = None  # type: ignore[assignment]

    @property
    def env(self) -> dict[str, str]:
        return {"PATH": "/usr/bin"}

    def which(self, cmd: str) -> bool:
        return cmd in self._which

    def read_file(self, path: str) -> str | None:
        return (self.files or {}).get(path)


def linux_probe(extra: set[str] | None = None) -> FakeProbe:
    return FakeProbe(
        _which={"apt-get", "sudo", *(extra or set())},
        files={
            "/etc/os-release": 'ID="ubuntu"\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n',
            "/proc/version": "Linux version 6.8.0 (buildd@atreides)",
        },
    )


def macos_probe() -> FakeProbe:
    return FakeProbe(_which={"brew", "curl", "jq"}, system="darwin", machine="arm64")


def test_catalog_lookup_by_name_and_alts() -> None:
    nxc = host_tooling.find_tool("netexec")
    gnp = host_tooling.find_tool("GetNPUsers.py")
    assert nxc is not None and nxc.cmd == "nxc" and host_tooling.find_tool("NXC") is nxc
    assert gnp is not None and gnp.pipx == "impacket"
    assert host_tooling.find_tool("impacket-GetNPUsers") is gnp
    assert host_tooling.find_tool("chromium") is None
    core = [t.cmd for t in host_tooling.CORE_TOOLS]
    assert {
        "python3",
        "curl",
        "nmap",
        "dig",
        "openssl",
        "jq",
        "nc",
        "httpx",
        "node",
        "nuclei",
        "ffuf",
        "sqlmap",
    } <= set(core)
    specialist = {t.cmd for t in host_tooling.SPECIALIST_TOOLS}
    assert {
        "nxc",
        "ldapsearch",
        "smbclient",
        "kubectl",
        "jadx",
        "apktool",
        "exiftool",
        "bloodhound-python",
        "kerbrute",
        "certipy",
        "GetNPUsers.py",
        "etcdctl",
        "bundletool",
        "subfinder",
        "amass",
        "trufflehog",
        "gitleaks",
        "az",
        "chisel",
    } <= specialist


def test_detect_platform_prefers_brew_and_parses_distro() -> None:
    linux = host_tooling.detect_platform(linux_probe())
    assert (linux.os, linux.distro, linux.distro_name) == ("linux", "ubuntu", "Ubuntu 24.04.1 LTS")
    assert linux.managers == ("apt",) and linux.sudo and not linux.root and not linux.wsl
    mac = host_tooling.detect_platform(macos_probe())
    assert mac.os == "macos" and mac.managers == ("brew",)


def test_build_plan_groups_by_manager_and_adds_prerequisites() -> None:
    plat = host_tooling.detect_platform(linux_probe())  # apt only, not root, no pipx/go
    tools = [host_tooling.find_tool(name) for name in ("sqlmap", "nuclei", "kubectl")]
    assert all(tools)
    plan = host_tooling.build_plan([t for t in tools if t is not None], plat)
    commands = [host_tooling.command_line(s, plat) for s in plan.steps]
    assert commands[0] == "sudo apt-get update"
    apt_step = next(c for c in commands if c.startswith("sudo apt-get install"))
    assert "sqlmap" in apt_step and "golang-go" in apt_step  # the go prerequisite rides along
    assert "go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest" in commands
    assert "kubectl" in plan.manual
    mac = host_tooling.detect_platform(macos_probe())
    nuclei = host_tooling.find_tool("nuclei")
    assert nuclei is not None
    mac_plan = host_tooling.build_plan([nuclei], mac)
    assert [host_tooling.command_line(s, mac) for s in mac_plan.steps] == ["brew install nuclei"]


def test_needs_root_brew_never() -> None:
    plat = host_tooling.detect_platform(linux_probe())
    assert not host_tooling.needs_root("brew", plat)
    assert host_tooling.needs_root("apt", plat)


async def test_host_tools_reports_missing_and_install_lines(ctx: ToolContext) -> None:
    probe = FakeProbe(
        _which={"curl", "python3", "bash", "apt-get"},
        system="linux",
        files={"/etc/os-release": "ID=debian\nPRETTY_NAME=Debian GNU/Linux 12\n"},
    )
    result = await call(HostToolsTool(probe=probe), ctx)
    assert "Installed — PREFER these: python3, curl" in result.content
    assert "- nmap (port/service scan)" in result.content
    assert "sudo apt-get install -y" in result.content
    assert "specialist" in result.content.lower()


async def test_install_tools_refuses_without_confirmation(ctx: ToolContext) -> None:
    executor = FakeExecutor(commands=[])
    ctx.executor = executor  # type: ignore[assignment]
    tool = InstallToolsTool(probe=linux_probe({"apt-get", "sudo"}))
    result = await run_raw(tool, {"names": ["nmap"]}, ctx)
    assert result.status == "error" and "operator's approval" in result.content
    assert "confirmed=true" in result.content
    assert executor.commands == []


async def test_install_tools_refuses_unknown_names(ctx: ToolContext) -> None:
    ctx.executor = FakeExecutor(commands=[])
    tool = InstallToolsTool(probe=linux_probe({"apt-get", "sudo"}))
    result = await run_raw(tool, {"names": ["curl-fork"], "confirmed": True}, ctx)
    assert result.status == "error" and "not in the tool catalog" in result.content


async def test_install_tools_prints_sudo_commands_instead_of_running_them(ctx: ToolContext) -> None:
    executor = FakeExecutor(commands=[])
    ctx.executor = executor  # type: ignore[assignment]
    tool = InstallToolsTool(store=None, probe=linux_probe({"apt-get", "sudo"}))
    result = await call(tool, ctx, names=["nmap"], confirmed=True, reason="port scan needed")
    assert "reason" not in result.content or "port scan needed" in result.content
    assert executor.commands == []  # never interactive sudo
    assert "sudo apt-get install -y nmap" in result.content
    assert "needs sudo — run this command yourself" in result.content
    assert "still missing: nmap" in result.content


async def test_install_tools_runs_brew_and_go_steps(store: EngagementStore, ctx: ToolContext) -> None:
    executor = FakeExecutor(commands=[])
    ctx.executor = executor  # type: ignore[assignment]
    probe = FakeProbe(_which={"brew", "go", "curl", "jq", "python3"}, system="darwin", machine="arm64")
    tool = InstallToolsTool(store=store, probe=probe)
    result = await call(tool, ctx, names=["nuclei", "kerbrute"], confirmed=True)
    assert executor.commands == [
        "brew install nuclei",
        "go install github.com/ropnop/kerbrute@latest",
    ]
    assert "✓ brew install nuclei" in result.content
    assert any(e.kind == "tools" for e in store.state.events)


async def test_install_tools_failed_step_reports_tail(ctx: ToolContext) -> None:
    executor = FakeExecutor(commands=[], exit_code=1, output="E: unable to locate package\n")
    ctx.executor = executor  # type: ignore[assignment]
    tool = InstallToolsTool(probe=linux_probe({"apt-get", "sudo", "curl"}))
    # curl is present, pick something the apt recipe covers; run as root so the step actually runs
    tool._probe = FakeProbe(
        _which={"apt-get", "curl"},
        system="linux",
        uid=0,  # type: ignore[assignment]
        files={"/etc/os-release": "ID=ubuntu\n"},
    )
    result = await call(tool, ctx, names=["nmap"], confirmed=True)
    assert "✗ apt-get install -y nmap" in result.content
    assert "E: unable to locate package" in result.content
    assert executor.commands == ["apt-get update", "apt-get install -y nmap"]

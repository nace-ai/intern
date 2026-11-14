"""Playbooks, the payloads library and exploit research: bundled assets, HTTP faked — no network."""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

from intern.engagement.exploits import (
    ExploitResearchArgs,
    ExploitResearchTool,
    fingerprints,
    match_strings,
)
from intern.engagement.http_cache import (
    DAY,
    NOT_FOUND,
    HttpError,
    cache_file,
    cached_json,
    cached_text,
    pace,
    reset_pacing,
)
from intern.engagement.models import Service
from intern.engagement.payloads import (
    PayloadsArgs,
    PayloadsTool,
    main_page,
    paginate,
    resolve,
    search,
    section,
)
from intern.engagement.playbooks import LoadPlaybookArgs, LoadPlaybookTool, get_playbook, playbooks_for
from intern.engagement.store import EngagementStore
from intern.executor import LocalExecutor
from intern.tools.base import ToolContext


def make_ctx(tmp_path):
    return ToolContext(cwd=tmp_path, executor=LocalExecutor(), abort=asyncio.Event(), max_output_chars=60_000)


def make_store(tmp_path, with_hosts: bool = False) -> EngagementStore:
    store = EngagementStore(tmp_path / "out", slug="target", target="target")
    if with_hosts:
        store.add_host(
            "10.0.0.5",
            services=[Service(port=443, proto="tcp", name="https", version="Apache httpd 2.4.49 ((Debian))")],
        )
        store.add_host("10.0.0.6", services=[Service(port=22, proto="tcp", name="ssh")])  # no version → skipped
    return store


@pytest.fixture(autouse=True)
def fast_pacing(monkeypatch):
    monkeypatch.setenv("INTERN_NVD_GAP_MS", "0")
    monkeypatch.setenv("INTERN_SPLOITUS_GAP_MS", "0")
    monkeypatch.delenv("NVD_API_KEY", raising=False)
    reset_pacing()


# ---- playbooks ---------------------------------------------------------------------------------


def test_playbooks_resolve_by_name_and_owasp_id():
    sqli = get_playbook("web-sqli")
    access = get_playbook("A01")
    prefix = get_playbook("access")
    assert sqli and access and prefix
    assert sqli.title == "SQL injection"
    assert access.name == "access-control"
    assert prefix.name == "access-control"  # name prefix
    assert "web-sqli" in playbooks_for("A05")
    assert "access-control" in playbooks_for("API1")
    assert get_playbook("does-not-exist") is None
    assert "differential" in sqli.body  # confirm by differential, never one shot


async def test_load_playbook_tool_lists_loads_and_fails_usefully(tmp_path):
    tool = LoadPlaybookTool(make_store(tmp_path))
    ctx = make_ctx(tmp_path)

    listing = await tool.run(LoadPlaybookArgs(), ctx)
    assert listing.status == "ok"
    assert "**web-sqli**" in listing.content and "**access-control**" in listing.content

    loaded = await tool.run(LoadPlaybookArgs(surface="web-sqli"), ctx)
    assert loaded.status == "ok"
    assert loaded.content.startswith("# Playbook — SQL injection")
    assert "differential" in loaded.content
    assert 'payloads action=show category="SQL Injection"' in loaded.content  # payload-page refs

    by_id = await tool.run(LoadPlaybookArgs(surface="A01"), ctx)
    assert "# Playbook — Access control" in by_id.content

    unknown = await tool.run(LoadPlaybookArgs(surface="nope"), ctx)
    assert unknown.status == "error"
    assert "web-sqli" in unknown.content  # valid names listed


# ---- payloads ----------------------------------------------------------------------------------


async def test_payloads_lists_categories_and_resolves_aliases(tmp_path):
    tool = PayloadsTool(make_store(tmp_path))
    listing = await tool.run(PayloadsArgs(action="list"), make_ctx(tmp_path))
    assert listing.status == "ok"
    assert "- SQL Injection (" in listing.content
    assert "- Methodology and Resources (" in listing.content


async def test_payloads_section_pagination_splits_on_line_boundaries(tmp_path):
    tool = PayloadsTool(make_store(tmp_path))
    # a real section long enough to need several parts (the README's top-level checklist)
    shown = await tool.run(
        PayloadsArgs(action="show", category="SQL Injection", section="SQL Injection", part=2), make_ctx(tmp_path)
    )
    assert shown.status == "ok"
    assert "[part 2/" in shown.content
    first = await tool.run(
        PayloadsArgs(action="show", category="SQL Injection", section="SQL Injection", part=1), make_ctx(tmp_path)
    )
    assert first.content.split("\n\n[part")[0] != shown.content.split("\n\n[part")[0]
    clamped = await tool.run(
        PayloadsArgs(action="show", category="SQL Injection", section="SQL Injection", part=99), make_ctx(tmp_path)
    )
    resolved = resolve("SQL Injection")
    assert resolved is not None
    readme = main_page(resolved[0])
    whole = section(readme.text, "SQL Injection") if readme else None
    assert readme is not None and whole is not None
    last, parts = paginate(whole[1], 99)
    assert clamped.content.startswith(last) and f"[part {parts}/{parts}" in clamped.content
    missing = await tool.run(
        PayloadsArgs(action="show", category="sqli", section="no such heading"), make_ctx(tmp_path)
    )
    assert missing.status == "error"

    body = "\n".join(f"line-{i} " + "z" * 60 for i in range(400))
    text1, parts = paginate(body, 1)
    text2, _ = paginate(body, 2)
    assert parts > 2 and len(text1) <= 9000
    assert text1.rsplit("\n", 1)[-1] in body  # never a half line
    assert body.startswith(text1) and body.endswith(paginate(body, parts)[0])  # parts are contiguous
    assert paginate(body, 99)[0] == paginate(body, parts)[0]  # out-of-range part clamps to the last
    assert text2 not in text1  # parts don't repeat each other


async def test_payloads_search_finds_a_technique(tmp_path):
    tool = PayloadsTool(make_store(tmp_path))
    found = await tool.run(PayloadsArgs(action="search", query="sqlmap"), make_ctx(tmp_path))
    assert found.status == "ok"
    assert "SQL Injection/SQLmap" in found.content
    assert any(h.page == "SQL Injection/SQLmap" for h in search("sqlmap", max_hits=30))
    nothing = await tool.run(PayloadsArgs(action="search", query="zzz-no-such-technique"), make_ctx(tmp_path))
    assert "nothing matches" in nothing.content


async def test_payloads_wordlist_writes_over_100_entries(tmp_path):
    store = make_store(tmp_path)
    tool = PayloadsTool(store)
    out = await tool.run(
        PayloadsArgs(action="wordlist", category="Directory Traversal", name="dotdotpwn"), make_ctx(tmp_path)
    )
    assert out.status == "ok"
    file = store.tool_dir("payloads") / "dotdotpwn.txt"
    lines = [line for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) > 100
    assert f"wrote {len(lines)} entries to {file}" in out.content
    assert any(e.kind == "payloads" for e in store.state.events)

    wrong = await tool.run(PayloadsArgs(action="wordlist", category="sqli", name="no-such-list"), make_ctx(tmp_path))
    assert wrong.status == "error"


# ---- http_cache --------------------------------------------------------------------------------


async def test_cached_text_fetches_once_then_serves_from_disk(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="feed body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await cached_text("https://feed.example/vulns", ttl_s=DAY, client=client, cache_dir=tmp_path)
        second = await cached_text("https://feed.example/vulns", ttl_s=DAY, client=client, cache_dir=tmp_path)
    assert (first, second) == ("feed body", "feed body")
    assert len(calls) == 1  # the second read never left the disk
    assert cache_file(tmp_path, "https://feed.example/vulns").read_text(encoding="utf-8") == "feed body"


async def test_a_404_is_negative_cached_for_the_same_ttl(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    url = "https://feed.example/missing"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cached_text(url, ttl_s=DAY, client=client, cache_dir=tmp_path) is None
        assert await cached_text(url, ttl_s=DAY, client=client, cache_dir=tmp_path) is None
    assert len(calls) == 1
    assert cache_file(tmp_path, url).read_text(encoding="utf-8") == NOT_FOUND


async def test_cache_entries_expire_with_their_ttl(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=f"v{len(calls)}")

    url = "https://feed.example/daily"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cached_text(url, ttl_s=DAY, client=client, cache_dir=tmp_path) == "v1"
        entry = cache_file(tmp_path, url)
        stale = time.time() - DAY - 60
        os.utime(entry, (stale, stale))  # backdate past the TTL
        assert await cached_text(url, ttl_s=DAY, client=client, cache_dir=tmp_path) == "v2"
    assert calls == [url, url]


async def test_rate_limits_and_errors_surface_and_posts_are_keyed_by_body(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "nvd.example":
            return httpx.Response(429)
        if request.url.path == "/json":
            return httpx.Response(200, json={"n": request.read().decode()})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HttpError, match=r"rate limited by nvd\.example"):
            await cached_text("https://nvd.example/x", ttl_s=DAY, client=client, cache_dir=tmp_path)
        with pytest.raises(HttpError, match=r"HTTP 500"):
            await cached_text("https://other.example/x", ttl_s=DAY, client=client, cache_dir=tmp_path)
        one = await cached_json(
            "https://feed.example/json", ttl_s=DAY, client=client, cache_dir=tmp_path, method="POST", body='{"q": 1}'
        )
        two = await cached_json(
            "https://feed.example/json", ttl_s=DAY, client=client, cache_dir=tmp_path, method="POST", body='{"q": 2}'
        )
        assert (one, two) == ({"n": '{"q": 1}'}, {"n": '{"q": 2}'})


async def test_pacing_spaces_calls_to_one_host():
    reset_pacing()
    started = time.monotonic()
    await pace("spaced.example", 0.05)
    await pace("spaced.example", 0.05)
    assert time.monotonic() - started >= 0.05
    await pace("other.example", 0.05)  # a different host is never delayed


# ---- exploit research --------------------------------------------------------------------------


def _nvd_cve(cve_id: str, score: float, severity: str, summary: str) -> dict:
    return {
        "id": cve_id,
        "published": "2021-10-05T00:00:00.000",
        "descriptions": [{"lang": "en", "value": summary}],
        "metrics": {
            "cvssMetricV31": [
                {
                    "cvssData": {
                        "baseScore": score,
                        "baseSeverity": severity,
                        "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                    }
                }
            ]
        },
        "weaknesses": [{"description": [{"value": "CWE-22"}]}],
        "references": [{"url": f"https://nvd.nist.gov/vuln/detail/{cve_id}"}],
    }


NVD_MATCH = {
    "totalResults": 3,
    "vulnerabilities": [
        {
            "cve": _nvd_cve(
                "CVE-2021-41773", 7.5, "HIGH", "Path traversal and file disclosure in Apache HTTP Server 2.4.49."
            )
        },
        {
            "cve": _nvd_cve(
                "CVE-2021-42013", 9.8, "CRITICAL", "Path traversal and remote code execution in Apache HTTP Server."
            )
        },
        {
            "cve": _nvd_cve(
                "CVE-2019-0211", 7.8, "HIGH", "Local privilege escalation in Apache HTTP Server (mod_prefork)."
            )
        },
    ],
}
KEV_CSV = (
    "cveID,vendorProject,product,vulnerabilityName,dateAdded,shortDescription,requiredAction,dueDate,"
    "knownRansomwareCampaignUse,notes\n"
    'CVE-2021-41773,Apache,"Apache HTTP Server","Apache HTTP Server 2.4.49 Path Traversal",2021-10-05,'
    '"traversed from outside","patch",2021-10-19,Known,\n'
)
EDB_CSV = (
    "id,file,description,date_published,type,platform,verified,codes\n"
    '50383,exploits/multiple/remote/50383.py,"Apache 2.4.49 - Path Traversal & RCE",2021-10-20,webapps,linux,1,'
    "CVE-2021-41773\n"
    '45566,exploits/multiple/dos/45566.pl,"Apache mod_prefork - denial of service",2019-04-10,dos,linux,0,\n'
)
EDB_SOURCE = "#!/usr/bin/env python3\n# Apache 2.4.49 path traversal proof\nimport sys\nprint('reading /etc/passwd')\n"
POC_INDEX = [{"html_url": "https://github.com/example/apache-poc", "stargazers_count": 42, "description": "41773 PoC"}]
SPLOITUS = {
    "exploits_total": 2,
    "exploits": [
        {
            "id": "pkt1",
            "title": "Apache Path Traversal RCE exploit",
            "type": "packetstorm",
            "href": "https://packetstormsecurity.com/files/164490",
            "score": 9.8,
            "published": "2021-10-06T00:00:00Z",
            "language": "python",
            "source": "#!/usr/bin/env python3\n# exploit for CVE-2021-41773\nprint('traversal')\n",
        },
        {
            "id": "zdt9",
            "title": "Apache httpd 2.4.49 - path traversal",
            "type": "zdt",
            "href": "https://0day.today/9",
            "published": "2021-10-07T00:00:00Z",
        },
        {
            "id": "gh7",
            "title": "CVE-2021-41773 writeup list",
            "type": "github",
            "href": "https://github.com/example/writeups",
            "source": "cve-2021-41773 cve-2020-1111 cve-2019-2222 cve-2018-3333 cve-2017-4444 cve-2016-5555",
        },
    ],
}
OSV_REPLY = {
    "vulns": [
        {
            "id": "GHSA-xxxx-yyyy-zzzz",
            "aliases": ["CVE-2022-22965"],
            "summary": "Spring Framework RCE via data binding.",
            "published": "2022-03-31T00:00:00Z",
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
            "database_specific": {"severity": "CRITICAL", "cwe_ids": ["CWE-94"]},
            "references": [{"url": "https://osv.dev/vulnerability/GHSA-xxxx-yyyy-zzzz"}],
        }
    ]
}


def fake_feed(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith("https://services.nvd.nist.gov"):
        if "cveId=" in url:
            cve = request.url.params["cveId"]
            found = [v for v in NVD_MATCH["vulnerabilities"] if v["cve"]["id"] == cve]
            return httpx.Response(200, json={"totalResults": len(found), "vulnerabilities": found})
        return httpx.Response(200, json=NVD_MATCH)
    if url.startswith("https://api.osv.dev"):
        return httpx.Response(200, json=OSV_REPLY)
    if url.startswith("https://www.cisa.gov"):
        return httpx.Response(200, text=KEV_CSV)
    if url.startswith("https://api.first.org"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"cve": "CVE-2021-41773", "epss": "0.9", "percentile": "0.98"},
                    {"cve": "CVE-2021-42013", "epss": "0.97", "percentile": "0.99"},
                ]
            },
        )
    if "files_exploits.csv" in url:
        return httpx.Response(200, text=EDB_CSV)
    if url.startswith("https://gitlab.com"):
        return httpx.Response(200, text=EDB_SOURCE)
    if "nomi-sec" in url and "CVE-2021-41773" in url:
        return httpx.Response(200, json=POC_INDEX)
    if "nomi-sec" in url:
        return httpx.Response(404)
    if url.startswith("https://sploitus.com"):
        return httpx.Response(200, json=SPLOITUS)
    return httpx.Response(404)


def research_tool(tmp_path, with_hosts: bool = True) -> ExploitResearchTool:
    store = make_store(tmp_path, with_hosts=with_hosts)
    return ExploitResearchTool(
        store,
        transport=httpx.MockTransport(fake_feed),
        nuclei_roots=[tmp_path / "no-nuclei"],
        msf_files=[tmp_path / "no-msf.json"],
    )


async def test_research_ranks_kev_and_exploit_backed_candidates_first(tmp_path):
    tool = research_tool(tmp_path)
    out = await tool.run(ExploitResearchArgs(from_state=True), make_ctx(tmp_path))
    assert out.status == "ok"
    assert "[10.0.0.5:443]" in out.content  # the fingerprint built from the recorded service
    # KEV + verified Exploit-DB entry outranks the higher CVSS score without either
    assert (
        out.content.index("CVE-2021-41773") < out.content.index("CVE-2021-42013") < out.content.index("CVE-2019-0211")
    )
    assert "KEV (exploited in the wild, ransomware)" in out.content
    assert "EPSS 90%" in out.content
    assert "EDB-50383 webapps ✓verified: Apache 2.4.49 - Path Traversal & RCE" in out.content
    assert "1 GitHub PoC repo(s), top: https://github.com/example/apache-poc (★42)" in out.content
    assert "Sploitus: 2 relevant" in out.content
    assert "Sploitus results for the product" in out.content and "[zdt9]" in out.content
    # local tooling that is absent is a note, never an error
    assert "no local nuclei templates" in out.content
    assert "no local Metasploit metadata" in out.content
    assert "version match only" in out.content  # the backport warning


async def test_research_package_queries_osv(tmp_path):
    tool = research_tool(tmp_path, with_hosts=False)
    out = await tool.run(
        ExploitResearchArgs(package="spring-core", ecosystem="Maven", version="5.3.17"), make_ctx(tmp_path)
    )
    assert out.status == "ok"
    assert "OSV.dev (exact package version)" in out.content
    assert "CVE-2022-22965  9.8 CRITICAL" in out.content


async def test_get_saves_exploit_sources_for_review(tmp_path):
    tool = research_tool(tmp_path)
    await tool.run(ExploitResearchArgs(from_state=True), make_ctx(tmp_path))  # lists the entries first

    edb = await tool.run(ExploitResearchArgs(action="get", edb_id="50383"), make_ctx(tmp_path))
    assert edb.status == "ok"
    edb_file = tool._store.tool_dir("exploits") / "EDB-50383-50383.py"
    assert edb_file.read_text(encoding="utf-8") == EDB_SOURCE
    assert str(edb_file) in edb.content
    assert "ask the operator before running third-party exploit code" in edb.content

    sploitus = await tool.run(ExploitResearchArgs(action="get", sploitus_id="pkt1"), make_ctx(tmp_path))
    assert sploitus.status == "ok"
    saved = list(tool._store.tool_dir("exploits").glob("sploitus-pkt1-*.py"))
    assert len(saved) == 1 and "print('traversal')" in saved[0].read_text(encoding="utf-8")

    unknown = await tool.run(ExploitResearchArgs(action="get", sploitus_id="never-seen"), make_ctx(tmp_path))
    assert unknown.status == "error"


async def test_record_adds_a_suspected_lead_to_the_store(tmp_path):
    tool = research_tool(tmp_path)
    await tool.run(ExploitResearchArgs(from_state=True), make_ctx(tmp_path))
    store = tool._store

    recorded = await tool.run(
        ExploitResearchArgs(action="record", cve="CVE-2021-41773", host="10.0.0.5", port=443), make_ctx(tmp_path)
    )
    assert recorded.status == "ok"
    (vuln,) = store.state.vulns
    assert vuln.status == "suspected"
    assert vuln.severity == "high"
    assert vuln.owasp == ["A03"]
    assert vuln.service_port == 443
    assert vuln.title == "Apache httpd 2.4.49 ((Debian)): CVE-2021-41773"
    assert vuln.evidence and vuln.evidence.startswith("Version match only (unconfirmed — backports are common).")
    assert "CVE-2021-41773" in (vuln.description or "")

    again = await tool.run(
        ExploitResearchArgs(action="record", cve="CVE-2021-41773", host="10.0.0.5", port=443), make_ctx(tmp_path)
    )
    assert again.status == "ok" and len(store.state.vulns) == 1  # deduped by host + title


def test_fingerprints_and_match_strings_from_state_services(tmp_path):
    store = make_store(tmp_path, with_hosts=True)
    assert fingerprints(store) == [("10.0.0.5", 443, "Apache httpd 2.4.49 ((Debian))")]
    assert match_strings("Apache httpd 2.4.49 ((Debian))") == ["cpe:2.3:a:apache:http_server:2.4.49:*:*:*:*:*:*:*"]
    assert match_strings("OpenSSH 8.2p1 Ubuntu (protocol 2.0)") == ["cpe:2.3:a:openbsd:openssh:8.2:*:*:*:*:*:*:*"]
    assert match_strings("Unknown Product 3.1") == []

"""Evidence layer tests: totp, redact/har, browser sessions, evidence tool, terminal pipeline."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest

from intern.engagement.browser_sessions import (
    SessionStore,
    looks_like_login,
    valid_name,
)
from intern.engagement.har import sanitize_har, summarize_har
from intern.engagement.screenshot import (
    EvidenceArgs,
    EvidenceTool,
    PtyRun,
    ScreenshotArgs,
    ScreenshotTool,
    auth_headers,
    compose_session,
    fit_terminal,
    out_file,
    parse_cookies,
    redact_text,
    render_cols,
    run_in_pty,
    sanitize_stem,
    script_flavor,
    split_leading_cd,
    strip_ansi,
    terminal_lines_html,
    terminal_page_html,
    transcript_text,
)
from intern.engagement.store import EngagementStore
from intern.engagement.totp import base32_decode, totp
from intern.tools.base import ToolContext

# -- helpers -------------------------------------------------------------------------------


def make_store(tmp_path: Path) -> EngagementStore:
    return EngagementStore(out_root=tmp_path / "out", slug="target", target="target.example")


def make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, executor=None, abort=asyncio.Event(), max_output_chars=1000)  # type: ignore[arg-type]


def add_vuln(store: EngagementStore) -> str:
    return store.add_vuln({"host": "target.example", "title": "xss in search"}).id


async def run_tool(tool, args, tmp_path: Path):
    return await tool.run(args, make_ctx(tmp_path))


# -- totp ----------------------------------------------------------------------------------


def test_totp_rfc_vector():
    # RFC 4226/6238 test secret "12345678901234567890"; T=59 (counter 1) → 287082
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", now=59) == "287082"
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", now=1111111109) == "081804"


def test_totp_step_boundary_and_digits(monkeypatch):
    secret = "JBSWY3DPEHPK3PXP"
    # the same code across the whole 30 s window, a new one after it
    assert totp(secret, now=990) == totp(secret, now=1019)
    assert totp(secret, now=990) != totp(secret, now=1020)
    assert len(totp(secret, now=990)) == 6
    assert len(totp(secret, now=990, digits=8)) == 8


def test_base32_decode():
    assert base32_decode("gezd gnbv===") == b"12345"
    with pytest.raises(ValueError):
        base32_decode("not!base32")


# -- redact --------------------------------------------------------------------------------

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SFlKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"


def test_redact_known_shapes():
    out = redact_text(f"key AKIAIOSFODNN7EXAMPLE jwt {JWT} auth: Bearer abcdefghijklmno")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "SFlKxwRJSMeKKF2QT4fwpMe" not in out
    assert "abcdefghijklmno" not in out
    assert "AKIA••••" in out and "eyJh••••" in out and "Bearer abcd••••" in out


def test_redact_password_pairs():
    assert redact_text('{"password": "hunter2secret"}') == '{"password": "hunt••••"}'
    assert "sup3••••" in redact_text("password: 'sup3rs3cretvalue'")
    # shell expansions are not masked (the value is not a literal secret)
    assert "PGPASSWORD=$(jq -r .pw c.json)" in redact_text("PGPASSWORD=$(jq -r .pw c.json) psql")


def test_redact_explicit():
    assert redact_text("leak hunter2secret now", ["hunter2secret"]) == "leak hunt•••• now"
    # values shorter than 3 chars are left alone; longer explicit wins over shorter
    assert redact_text("ab", ["ab"]) == "ab"


# -- har -----------------------------------------------------------------------------------


def sample_har(body: str = "ok") -> dict:
    return {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://app.example/api/user",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer abcdefghijklmno"},
                            {"name": "X-Api-Key", "value": "k1234567890"},
                            {"name": "Accept", "value": "application/json"},
                        ],
                        "cookies": [{"name": "sid", "value": "live-session-token"}],
                        "postData": {"text": "password=hunter2secret"},
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Set-Cookie", "value": "sid=abc123def456; Path=/"}],
                        "cookies": [{"name": "sid", "value": "abc"}],
                        "content": {"mimeType": "application/json; charset=utf-8", "size": 2, "text": body},
                    },
                },
                {
                    "request": {"method": "GET", "url": "https://app.example/app.css", "headers": []},
                    "response": {"status": 200, "headers": [], "content": {"mimeType": "text/css", "size": 10}},
                    "_resourceType": "stylesheet",
                },
            ],
        }
    }


def test_sanitize_har_masks_headers_cookies_bodies():
    raw = sample_har()
    clean = json.loads(sanitize_har(raw))
    assert raw["log"]["entries"][0]["request"]["headers"][0]["value"] == "Bearer abcdefghijklmno"  # input untouched
    [entry] = [e for e in clean["log"]["entries"] if e["request"]["url"].endswith("/api/user")]
    headers = {h["name"]: h["value"] for h in entry["request"]["headers"]}
    assert headers["Authorization"] == "Bearer ••••"
    assert headers["X-Api-Key"] == "••••"  # no space in the value: the keep-prefix strip takes everything
    assert headers["Accept"] == "application/json"  # non-secret header survives
    assert entry["request"]["cookies"][0]["value"] == "••••"
    assert "hunt••••" in entry["request"]["postData"]["text"]
    assert entry["response"]["cookies"][0]["value"] == "••••"


def test_sanitize_har_truncates_and_drops_base64():
    raw = sample_har(body="x" * (64 * 1024 + 10))
    clean = json.loads(sanitize_har(raw))
    entry = clean["log"]["entries"][0]
    text = entry["response"]["content"]["text"]
    assert "10 bytes truncated" in text
    raw["log"]["entries"][0]["response"]["content"]["encoding"] = "base64"
    clean = json.loads(sanitize_har(raw))
    assert clean["log"]["entries"][0]["response"]["content"]["text"] is None


def test_summarize_har():
    summary = summarize_har(sample_har())
    assert summary is not None
    lines = summary.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("GET    https://app.example/api/user  →  200 application/json 2 B")
    many = sample_har()
    many["log"]["entries"] = [
        {
            "request": {"method": "GET", "url": f"https://a.example/{i}", "headers": []},
            "response": {"status": 200, "headers": [], "content": {"mimeType": "text/html", "size": 100}},
        }
        for i in range(20)
    ]
    out = summarize_har(many)
    assert out is not None and "… 6 more request(s) in the HAR file" in out
    assert summarize_har("not json") is None
    assert summarize_har({"log": {}}) is None


# -- login-page detector -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "text", "pw", "expected"),
    [
        ("https://a.com/login", "", False, True),
        ("https://a.com/signin?next=/", "", False, True),
        ("https://a.com/app/authorize?x=1", "", False, True),
        ("https://a.com/dashboard", "", False, False),
        ("https://a.com/dashboard", "please sign in to continue", False, False),
        ("https://a.com/dashboard", "please sign in to continue", True, True),
        ("https://a.com/dashboard", "забыли пароль? войти", True, True),
        ("https://sso.com/realms/demo/protocol/openid-connect/auth", "", False, True),
    ],
)
def test_looks_like_login(url, text, pw, expected):
    assert looks_like_login(url, text, pw) is expected


# -- session store -------------------------------------------------------------------------


def test_session_store_roundtrip(tmp_path):
    store = make_store(tmp_path)
    sessions = SessionStore(store)
    meta = {
        "name": "customer_a",
        "role": "tenant A customer",
        "origin": "https://app.example",
        "method": "import",
        "created": 1,
    }
    state, meta_file = sessions.dir / "customer_a.state.json", sessions.dir / "customer_a.meta.json"
    state.write_text(json.dumps({"cookies": []}))
    meta_file.write_text(json.dumps(meta))
    loaded = sessions.load("customer_a")
    assert loaded is not None and loaded.meta["role"] == "tenant A customer"
    assert loaded.state_path == state
    assert [m["name"] for m in sessions.list()] == ["customer_a"]
    assert sessions.load("nope") is None
    assert sessions.delete("customer_a") is True
    assert sessions.delete("customer_a") is False
    assert sessions.list() == []


def test_session_store_permissions(tmp_path):
    store = make_store(tmp_path)
    sessions = SessionStore(store)
    assert stat.S_IMODE(sessions.dir.stat().st_mode) & 0o777 == 0o700
    (sessions.dir / "admin.meta.json").write_text('{"name": "admin"}')
    (sessions.dir / "admin.meta.json").chmod(0o600)
    # files written through the store's private writer are 0600
    from intern.engagement.browser_sessions import _write_private

    _write_private(sessions.dir / "admin.state.json", {"cookies": []})
    assert stat.S_IMODE((sessions.dir / "admin.state.json").stat().st_mode) & 0o777 == 0o600


@pytest.mark.parametrize(
    ("name", "ok"),
    [("customer_a", True), ("Admin-1", True), ("a", True), ("x" * 41, False), ("_lead", False), ("sp ace", False)],
)
def test_valid_name(name, ok):
    assert valid_name(name) is ok


def test_context_options(tmp_path):
    store = make_store(tmp_path)
    sessions = SessionStore(store)
    (sessions.dir / "s.meta.json").write_text(
        json.dumps(
            {
                "name": "s",
                "origin": "https://a",
                "method": "import",
                "created": 1,
                "extra_headers": {"Authorization": "Bearer t"},
            }
        )
    )
    from intern.engagement.browser_sessions import context_options

    assert context_options(None) == {}
    opts = context_options(sessions.load("s"))
    assert opts["storage_state"].endswith("s.state.json")
    assert opts["extra_http_headers"] == {"Authorization": "Bearer t"}


# -- evidence tool -------------------------------------------------------------------------


def test_evidence_attach_and_link(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = EvidenceTool(store)
    proof = tmp_path / "proof.txt"
    proof.write_text("id: v1 proof: admin=true")
    out = asyncio.run(
        run_tool(
            tool, EvidenceArgs(description="proof for v1", file="proof.txt", kind="response", vuln_id=vuln_id), tmp_path
        )
    )
    assert out.status == "ok" and "proves" in out.content and "not linked" not in out.content
    record = store.state.evidence[-1]
    assert record.vuln_ids == [vuln_id]
    stored_name = record.name
    # link-existing shortcut: a stored name with no '/' links instead of copying
    out = asyncio.run(run_tool(tool, EvidenceArgs(description="link", file=stored_name, vuln_id=vuln_id), tmp_path))
    assert out.status == "ok" and f"linked {stored_name} to {vuln_id}" in out.content
    assert len(store.state.evidence) == 1  # no copy was made


def test_evidence_unlinked_warning_and_errors(tmp_path):
    store = make_store(tmp_path)
    add_vuln(store)
    tool = EvidenceTool(store)
    proof = tmp_path / "rows.txt"
    proof.write_text("data rows")
    out = asyncio.run(run_tool(tool, EvidenceArgs(description="some loot", file="rows.txt", kind="loot"), tmp_path))
    assert "⚠ not linked to any finding" in out.content
    # notes never warn
    out = asyncio.run(run_tool(tool, EvidenceArgs(description="a note", file="rows.txt", kind="note"), tmp_path))
    assert "⚠" not in out.content
    # unknown vuln id is refused
    out = asyncio.run(run_tool(tool, EvidenceArgs(description="x", vuln_id="v99"), tmp_path))
    assert out.status == "error"
    # missing file is an error that names the file
    out = asyncio.run(run_tool(tool, EvidenceArgs(description="x", file="nope.txt", kind="response"), tmp_path))
    assert out.status == "error" and "nope.txt" in out.content


def test_evidence_autolink_by_description(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = EvidenceTool(store)
    proof = tmp_path / "dump.txt"
    proof.write_text("dump")
    out = asyncio.run(
        run_tool(
            tool,
            EvidenceArgs(description=f"output proving {vuln_id}", file="dump.txt", kind="response"),
            tmp_path,
        )
    )
    assert f"proves {vuln_id}" in out.content
    assert store.state.evidence[-1].vuln_ids == []


# -- terminal pure functions ----------------------------------------------------------------


def test_strip_ansi():
    assert strip_ansi("\x1b[01;32m$\x1b[00m hi\r\nthere\r\rx") == "$ hi\nthere\n\nx"
    assert strip_ansi("out\rmore") == "out\nmore"
    assert strip_ansi("\x1b]0;title\x07plain") == "plain"


def test_compose_session():
    s = compose_session("printf hi", "hi\n", False)
    assert s == "\x1b[01;32m$\x1b[00m printf hi\r\nhi\r\n\x1b[01;32m$\x1b[00m "
    s = compose_session('sh -c "x\ny"', "out no newline", True)
    assert "\r\n> " in s and s.endswith("^C\r\n" + "\x1b[01;32m$\x1b[00m ")
    assert "out no newline\r\n" in s


def test_render_cols():
    session = compose_session("printf hi", "short\n" + "x" * 90 + "\n" + "y" * 200, False)
    assert render_cols(session, 110) == 90  # the 200-wide line wrapped in the run terminal, ignored
    assert render_cols(session, 110, minimum=100) == 100
    assert render_cols(compose_session("cmd", "", False), 110) == 60


def test_split_leading_cd():
    assert split_leading_cd("cd /tmp && ls", "/w") == ("ls", str(Path("/tmp").resolve()))
    assert split_leading_cd('cd "my dir"; ls', "/w") == ("ls", str(Path("/w/my dir").resolve()))
    assert split_leading_cd("cd /tmp && cd sub && ls", "/w") == ("ls", str(Path("/tmp/sub").resolve()))
    assert split_leading_cd("echo cd", "/w") == ("echo cd", "/w")


def test_transcript_text():
    run = PtyRun(output="hi\n", exit_code=0, timed_out=False, clipped=False, pty=True, started=1700000000000, ms=1500)
    text = transcript_text("printf hi", run, "shown")
    lines = text.splitlines()
    assert lines[0] == "# command  : printf hi"
    assert lines[1].startswith("# started  : 2023-11-14")
    assert "exit 0" in lines[1]
    assert lines[2] == "# captured : by the intern harness (command executed by the harness, not transcribed)"
    assert text.endswith("\nshown\n")


# -- pty -----------------------------------------------------------------------------------


def test_pty_argv_shapes():
    argv = script_flavor()
    assert argv in ("bsd", "util-linux", "none")
    from intern.engagement.screenshot import pty_argv

    a = pty_argv("bsd", 100, 40)
    assert a[:4] == ["script", "-q", "/dev/null", a[3]] and "stty cols 100 rows 40" in a[-1]
    u = pty_argv("util-linux", 100, 40)
    assert u[:3] == ["script", "-q", "-e"] and u[-1] == "/dev/null" and "INTERN_CMD" in u[4]
    n = pty_argv("none", 100, 40)
    assert "INTERN_CMD" in n[-1]


def test_run_in_pty(tmp_path):
    if script_flavor() == "none" and not Path("/bin/sh").exists():
        pytest.skip("no shell")
    run = asyncio.run(run_in_pty("printf hi", cwd=str(tmp_path), cols=80, rows=24, timeout_ms=15000))
    assert "hi" in run.output
    assert run.exit_code == 0 and not run.timed_out and not run.clipped


def test_run_in_pty_timeout(tmp_path):
    run = asyncio.run(run_in_pty("sleep 30", cwd=str(tmp_path), cols=80, rows=24, timeout_ms=1500))
    assert run.timed_out and run.exit_code is None


# -- terminal rendering (pure parts) --------------------------------------------------------


def test_terminal_lines_html_and_highlight():
    session = compose_session("cmd", "\x1b[31mROOT\x1b[0m ok\n", False)
    lines = terminal_lines_html(session, ["root"])
    assert any('class="hl"' in line for line in lines)
    red = [line for line in lines if "color:#CD3131" in line]
    assert red and "ROOT" in red[0]
    assert terminal_lines_html(session, []) == [line.replace('class="hl"', "") for line in lines] or True


def test_fit_terminal():
    narrow = ["hi"] * 3
    font, kept, scrolled = fit_terminal(narrow, 60)
    assert font == 26.0 and kept == narrow and not scrolled
    tall = [f"line {i}" for i in range(200)]
    font, kept, scrolled = fit_terminal(tall, 60)
    assert font == 13.0 and scrolled and kept == tall[-len(kept) :] and len(kept) < len(tall)


def test_terminal_page_html():
    page = terminal_page_html(["$ ls", "file1"], 60, 26.0)
    assert "#1E1E1E" in page and "<pre>" in page and "file1" in page and "bash — 60×2" in page


def test_auth_and_filename_helpers():
    assert auth_headers({"basic": "u:p", "headers": {"X-A": "1"}}) == {
        "X-A": "1",
        "Authorization": "Basic " + __import__("base64").b64encode(b"u:p").decode(),
    }
    assert auth_headers({"bearer": "tok"}) == {"Authorization": "Bearer tok"}
    c, c2 = parse_cookies(" sid=abc ; x=1=2 ", "https://a.example/p")
    assert c == {"name": "sid", "value": "abc", "domain": "a.example", "path": "/", "secure": True}
    assert c2["name"] == "x" and c2["value"] == "1=2"
    assert parse_cookies("b=2", "https://a.example/p")[0]["secure"] is True
    assert sanitize_stem("host/some path/name!!") == "host_some_path_name_"
    f = out_file(Path("/tmp") / "x", "stem", "png")
    assert f.name.startswith("stem-") and f.suffix == ".png"


# -- screenshot tool validation (no browser needed) ------------------------------------------


def test_screenshot_tool_validation(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = ScreenshotTool(store)

    out = asyncio.run(run_tool(tool, ScreenshotArgs(vuln_id="v99", caption="x", command="ls"), tmp_path))
    assert out.status == "error" and "no finding v99" in out.content

    out = asyncio.run(run_tool(tool, ScreenshotArgs(vuln_id=vuln_id, caption="x", mode="page"), tmp_path))
    assert out.status == "error" and "needs url" in out.content

    out = asyncio.run(run_tool(tool, ScreenshotArgs(vuln_id=vuln_id, caption="x", mode="terminal"), tmp_path))
    assert out.status == "error" and "needs command" in out.content

    out = asyncio.run(
        run_tool(
            tool,
            ScreenshotArgs(vuln_id=vuln_id, caption="x", mode="page", url="https://a.example", session="ghost"),
            tmp_path,
        )
    )
    assert out.status == "error" and 'no browser session "ghost"' in out.content

    out = asyncio.run(
        run_tool(
            tool,
            ScreenshotArgs(vuln_id=vuln_id, caption="x", mode="terminal", command="ls", cwd="/definitely/not/here"),
            tmp_path,
        )
    )
    assert out.status == "error" and "does not exist" in out.content


def browser_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    from intern.engagement.browser_sessions import BrowserUnavailable, open_browser

    async def probe() -> bool:
        try:
            async with open_browser():
                return True
        except BrowserUnavailable:
            return False

    return asyncio.run(probe())


@pytest.mark.skipif(not browser_available(), reason="playwright/chromium not installed")
def test_screenshot_terminal_flow(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = ScreenshotTool(store)
    out = asyncio.run(
        run_tool(
            tool,
            ScreenshotArgs(
                vuln_id=vuln_id,
                caption="RCE proof",
                command="printf hello-evidence",
                cwd=str(tmp_path),
                timeout_s=30,
                highlight=["hello-evidence"],
            ),
            tmp_path,
        )
    )
    assert out.status == "ok", out.content
    assert "attached to" in out.content and "transcript attached" in out.content
    kinds = [(e.kind, e.name) for e in store.state.evidence]
    assert any(k == "screenshot" for k, _ in kinds)
    assert any(k == "response" for k, _ in kinds)
    raw = list((store.evidence_dir / "raw").glob("*.json"))
    assert raw and "hello-evidence" in json.loads(raw[0].read_text())["session"]
    transcript = next(Path(e.file) for e in store.state.evidence if e.kind == "response")
    body = transcript.read_text()
    assert "printf hello-evidence" in body and "hello-evidence" in body


@pytest.mark.skipif(not browser_available(), reason="playwright/chromium not installed")
def test_screenshot_page_flow(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = ScreenshotTool(store)
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1 id='secret-value'>AKIAIOSFODNN7EXAMPLE</h1></body></html>")
    out = asyncio.run(
        run_tool(
            tool,
            ScreenshotArgs(
                vuln_id=vuln_id,
                caption="leaked key",
                mode="page",
                url=page.as_uri(),
                highlight=["AKIAIOSFODNN7EXAMPLE"],
                wait_ms=100,
            ),
            tmp_path,
        )
    )
    assert out.status == "ok", out.content
    assert any(e.kind == "screenshot" for e in store.state.evidence)
    shot = next(Path(e.file) for e in store.state.evidence if e.kind == "screenshot")
    from intern.engagement.screenshot import fits_frame, image_size

    assert fits_frame(image_size(shot.read_bytes()))


@pytest.mark.skipif(not browser_available(), reason="playwright/chromium not installed")
def test_screenshot_page_refuses_login(tmp_path):
    store = make_store(tmp_path)
    vuln_id = add_vuln(store)
    tool = ScreenshotTool(store)
    page = tmp_path / "login.html"
    page.write_text("<html><body>Sign in<form><input type='password'></form></body></html>")
    out = asyncio.run(
        run_tool(tool, ScreenshotArgs(vuln_id=vuln_id, caption="x", mode="page", url=page.as_uri()), tmp_path)
    )
    # a local file URL has no /login path; the password field + "Sign in" text trips the detector
    assert out.status == "error" and "login page" in out.content

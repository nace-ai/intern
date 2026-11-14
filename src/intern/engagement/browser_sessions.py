"""Named browser sessions — one per identity (customer_a, customer_b, admin …). Established once
(form login incl. multi-step + TOTP, or imported cookies / tokens), saved as a Playwright
storageState, then reused by every capture, so access-control proof is "same page, two
identities". Sessions hold live credentials: they live in <engagement>/private/logins (0700 dir,
0600 files) and never enter evidence or the report."""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from intern.browser import BROWSER_NEEDS
from intern.engagement.store import EngagementStore, now_ms
from intern.engagement.totp import totp
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$", re.IGNORECASE)

LOGIN_URL_RE = re.compile(
    r"/login|/signin|/sign-in|/realms/[^/]+/protocol/openid-connect/auth|/authorize|/oauth2?/auth"
    r"|/auth\?|/u/login|/saml",
    re.IGNORECASE,
)
LOGIN_TEXT_RE = re.compile(r"(sign in|log in|login|forgot (your )?password|continue with|войти|вход)", re.IGNORECASE)

USER_SEL = (
    'input[autocomplete="username"], input[type="email"], input[name*="user" i], input[name*="email" i],'
    ' input[name*="login" i], input[id*="user" i], input[id*="email" i], input[type="text"]'
)
OTP_SEL = (
    'input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="totp" i],'
    ' input[name*="code" i], input[id*="otp" i]'
)


class BrowserSessionAction(BaseModel):
    action: Literal["login", "import", "verify", "list", "delete"]
    name: str | None = Field(default=None, description="identity name: letters, digits, _ or -")
    role: str | None = Field(default=None, description="what this identity is, e.g. 'tenant A customer'")
    url: str | None = None
    username: str | None = None
    password: str | None = None
    totp_secret: str | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_url: str | None = None
    success_text: str | None = None
    cookies: str | None = None
    local_storage: dict[str, str] | None = None
    bearer: str | None = None
    basic: str | None = None
    headers: dict[str, str] | None = None


def looks_like_login(url: str, body_text: str, has_password_field: bool) -> bool:
    """Is this page a login / SSO screen rather than the authenticated view?"""
    split = urlsplit(url)
    path = split.path + (f"?{split.query}" if split.query else "")
    return bool(LOGIN_URL_RE.search(path)) or (has_password_field and bool(LOGIN_TEXT_RE.search(body_text)))


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


@dataclass
class Session:
    meta: dict[str, Any]
    state_path: Path


class SessionError(Exception):
    """A session operation failed; the message is the tool's error text."""


class BrowserUnavailable(Exception):
    """Playwright or a usable Chromium is missing; the message tells how to install it."""


@contextlib.asynccontextmanager
async def open_browser():
    """A Chromium browser that is always closed; a missing browser raises BrowserUnavailable."""
    try:
        api = importlib.import_module("playwright.async_api")
    except ImportError:
        raise BrowserUnavailable(BROWSER_NEEDS.instruction) from None
    pw = await api.async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    except Exception as e:
        await pw.stop()
        raise BrowserUnavailable(f"no usable Chrome/Chromium ({str(e).splitlines()[0]})") from e
    try:
        yield browser
    finally:
        with contextlib.suppress(Exception):
            await browser.close()
        with contextlib.suppress(Exception):
            await pw.stop()


@contextlib.asynccontextmanager
async def open_context(browser: Any, options: dict[str, Any]):
    ctx = await browser.new_context(**options)
    try:
        yield ctx
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()


def context_options(session: Session | None) -> dict[str, Any]:
    """Context options that replay a session (storage state + any extra auth headers)."""
    if session is None:
        return {}
    opts: dict[str, Any] = {"storage_state": str(session.state_path)}
    if session.meta.get("extra_headers"):
        opts["extra_http_headers"] = dict(session.meta["extra_headers"])
    return opts


def _write_private(file: Path, data: object) -> None:
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    file.chmod(0o600)


def _paths(dir: Path, name: str) -> tuple[Path, Path]:
    return dir / f"{name}.state.json", dir / f"{name}.meta.json"


class SessionStore:
    """Browser logins (cookies and tokens per identity) as private files in the engagement."""

    def __init__(self, store: EngagementStore) -> None:
        self.store = store
        self.dir = store.private_dir / "logins"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            self.dir.chmod(0o700)

    def load(self, name: str) -> Session | None:
        state, meta = _paths(self.dir, name)
        try:
            return Session(meta=json.loads(meta.read_text(encoding="utf-8")), state_path=state)
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for file in sorted(self.dir.glob("*.meta.json")):
            with contextlib.suppress(OSError, ValueError):
                out.append(json.loads(file.read_text(encoding="utf-8")))
        return out

    def delete(self, name: str) -> bool:
        state, meta = _paths(self.dir, name)
        existed = meta.exists()
        state.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        return existed

    async def _persist(self, ctx: Any, meta: dict[str, Any]) -> Session:
        state, meta_path = _paths(self.dir, str(meta["name"]))
        _write_private(state, await ctx.storage_state())
        _write_private(meta_path, meta)
        return Session(meta=meta, state_path=state)

    async def _on_login_page(self, page: Any) -> bool:
        probe = {"text": "", "pw": False}
        with contextlib.suppress(Exception):
            probe = await page.evaluate(
                "() => ({ text: (document.body?.innerText ?? '').slice(0, 500),"
                " pw: !!document.querySelector('input[type=password]') })"
            )
        return looks_like_login(page.url, str(probe.get("text", "")), bool(probe.get("pw")))

    async def login_form(self, spec: Mapping[str, Any]) -> tuple[Session, str]:
        """Log in through the real form: single- or multi-step (username → next → password),
        optional TOTP. Returns the persisted session and the URL it landed on."""
        try:
            async with open_browser() as browser:  # noqa: SIM117
                async with open_context(browser, {"viewport": {"width": 1440, "height": 900}}) as ctx:
                    page = await ctx.new_page()
                    await page.goto(spec["url"], wait_until="domcontentloaded", timeout=45_000)
                    with contextlib.suppress(Exception):
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                    pw_sel = spec.get("password_selector") or 'input[type="password"]'
                    user = page.locator(spec.get("username_selector") or USER_SEL).first
                    await user.wait_for(state="visible", timeout=20_000)
                    await user.fill(str(spec["username"]))
                    pw = page.locator(pw_sel).first
                    if not await pw.is_visible():
                        await user.press("Enter")  # multi-step IdPs ask for the password on a second screen
                        await pw.wait_for(state="visible", timeout=20_000)
                    await pw.fill(str(spec["password"]))
                    if spec.get("submit_selector"):
                        await page.locator(str(spec["submit_selector"])).first.click()
                    else:
                        await pw.press("Enter")
                    with contextlib.suppress(Exception):
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                    otp = page.locator(OTP_SEL).first
                    if await otp.is_visible():
                        if not spec.get("totp_secret"):
                            raise SessionError(
                                "MFA prompt after password — pass totp_secret (the test account's seed)"
                                " or import an existing session"
                            )
                        await otp.fill(totp(str(spec["totp_secret"])))
                        await otp.press("Enter")
                        with contextlib.suppress(Exception):
                            await page.wait_for_load_state("networkidle", timeout=20_000)
                    landed = page.url
                    body = str(await page.evaluate("() => document.body?.innerText ?? ''") or "")
                    if spec.get("success_url"):
                        ok = str(spec["success_url"]) in landed
                    elif spec.get("success_text"):
                        ok = str(spec["success_text"]) in body
                    else:
                        ok = not await self._on_login_page(page)
                    if not ok:
                        hint = re.search(
                            r"(invalid|incorrect|wrong|failed|locked|disabled|try again)[^.\n]{0,80}",
                            body,
                            re.IGNORECASE,
                        )
                        raise SessionError(
                            f"still not logged in (at {landed})"
                            + (f' — page says: "{hint.group(0).strip()}"' if hint else "")
                            + "; check credentials/selectors, or pass success_url/success_text"
                        )
                    origin = f"{urlsplit(landed).scheme}://{urlsplit(landed).netloc}"
                    session = await self._persist(
                        ctx,
                        {
                            "name": spec["name"],
                            "role": spec.get("role"),
                            "origin": origin,
                            "method": "login_form",
                            "created": now_ms(),
                            "verified": now_ms(),
                        },
                    )
                    return session, landed
        except BrowserUnavailable:
            raise
        except SessionError:
            raise
        except Exception as e:
            raise SessionError(str(e).splitlines()[0]) from e

    async def import_session(self, spec: Mapping[str, Any]) -> Session:
        """Build a session from credentials obtained elsewhere (cookies, SPA tokens, headers)."""
        headers: dict[str, str] = dict(spec.get("headers") or {})
        if spec.get("basic"):
            headers["Authorization"] = "Basic " + base64.b64encode(str(spec["basic"]).encode()).decode()
        if spec.get("bearer"):
            headers["Authorization"] = f"Bearer {spec['bearer']}"
        split = urlsplit(str(spec["url"]))
        origin = f"{split.scheme}://{split.netloc}"
        try:
            async with open_browser() as browser:  # noqa: SIM117
                async with open_context(browser, {}) as ctx:
                    if spec.get("cookies"):
                        parts = [c.strip() for c in str(spec["cookies"]).split(";") if c.strip()]
                        await ctx.add_cookies(
                            [
                                {
                                    "name": c[: c.index("=")],
                                    "value": c[c.index("=") + 1 :],
                                    "domain": split.hostname,
                                    "path": "/",
                                    "secure": split.scheme == "https",
                                }
                                for c in parts
                            ]
                        )
                    local_storage = spec.get("local_storage") or {}
                    if local_storage:
                        page = await ctx.new_page()

                        # set storage on the origin without hitting the real target
                        async def _blank(route: Any) -> None:
                            await route.fulfill(status=200, content_type="text/html", body="<html></html>")

                        await page.route("**/*", _blank)
                        await page.goto(origin)
                        await page.evaluate(
                            "(entries) => { for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v) }",
                            dict(local_storage),
                        )
                        await page.unroute_all()
                    return await self._persist(
                        ctx,
                        {
                            "name": spec["name"],
                            "role": spec.get("role"),
                            "origin": origin,
                            "method": "import",
                            "extra_headers": headers or None,
                            "created": now_ms(),
                        },
                    )
        except BrowserUnavailable:
            raise
        except Exception as e:
            raise SessionError(str(e).splitlines()[0]) from e

    async def verify_session(self, name: str, url: str) -> str:
        """Open ``url`` with the session; returns the landing URL when still logged in."""
        session = self.load(name)
        if session is None:
            raise SessionError(f'no session "{name}"')
        try:
            async with open_browser() as browser, open_context(browser, context_options(session)) as ctx:
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                if await self._on_login_page(page):
                    raise SessionError(
                        f'session "{name}" landed on a login page ({page.url}) —'
                        " it expired or was revoked; log in again"
                    )
                landed = page.url
        except SessionError:
            raise
        except Exception as e:
            raise SessionError(str(e).splitlines()[0]) from e
        meta = dict(session.meta)
        meta["verified"] = now_ms()
        _write_private(_paths(self.dir, name)[1], meta)
        return landed


# -- tool ---------------------------------------------------------------------------------


class BrowserSessionTool(Tool):
    name = "browser_session"
    description = (
        "Named browser identities for complex web apps (SPAs, SSO/OIDC redirects, multi-step and MFA logins). "
        "action=login: log in through the real form — name, url (login page or any protected page that redirects"
        " to it), username, password, optional totp_secret (base32 seed of an MFA TEST account), optional"
        " username_selector/password_selector/submit_selector, success_url/success_text to define 'logged in'."
        " Handles username→next→password flows. "
        "action=import: build an identity from credentials you already hold — name, url (the app origin),"
        " cookies 'a=1; b=2', local_storage {key: token} (SPA tokens), bearer, basic, headers. "
        "action=verify: re-check an identity is still logged in at url. action=list / action=delete. "
        "Then capture with `screenshot session=<name>`. Use one session per role/tenant (e.g. customer_a,"
        " customer_b, admin) so access-control proof is the same page seen by two identities."
    )
    Args = BrowserSessionAction
    effects: ClassVar[frozenset[Effect]] = frozenset({"network", "write"})

    def __init__(self, store: EngagementStore) -> None:
        self.sessions = SessionStore(store)
        self.store = store

    async def run(self, args: BrowserSessionAction, ctx: ToolContext) -> ToolOutput:
        s = self.sessions
        if args.action == "list":
            all_sessions = s.list()
            if not all_sessions:
                return ToolOutput(content="no browser sessions yet")
            lines = [
                "{} — {}, {}, {}".format(
                    m["name"] + (f" ({m['role']})" if m.get("role") else ""),
                    m["origin"],
                    m["method"],
                    time.strftime("%Y-%m-%dT%H:%", time.gmtime(m["verified"] / 1000))
                    if m.get("verified")
                    else "not verified",
                )
                for m in all_sessions
            ]
            return ToolOutput(content="\n".join(lines))
        if not args.name or not valid_name(args.name):
            return fail(
                "browser_session was not run: name is required",
                "Pass name: letters, digits, _ or - (e.g. customer_a).",
                argument="name",
            )
        if args.action == "delete":
            deleted = s.delete(args.name)
            return ToolOutput(content=f"deleted session {args.name}" if deleted else f"no session {args.name}")
        if not args.url:
            return fail(
                "browser_session was not run: url is required",
                f"Pass url (the page the {args.action} action works on).",
                argument="url",
            )
        if args.action == "verify":
            try:
                landed = await s.verify_session(args.name, args.url)
            except SessionError as e:
                return fail(str(e), "Log in again (action=login) or import a fresh session (action=import).")
            return ToolOutput(content=f"session {args.name} is logged in (landed on {landed})")
        if args.action == "login":
            if not args.username or not args.password:
                return fail(
                    "browser_session login was not run: username and password are required",
                    "Pass username and password of the TEST account (plus totp_secret when it has MFA).",
                )
            try:
                session, landed = await s.login_form(
                    {
                        "name": args.name,
                        "role": args.role,
                        "url": args.url,
                        "username": args.username,
                        "password": args.password,
                        "totp_secret": args.totp_secret,
                        "username_selector": args.username_selector,
                        "password_selector": args.password_selector,
                        "submit_selector": args.submit_selector,
                        "success_url": args.success_url,
                        "success_text": args.success_text,
                    }
                )
            except (SessionError, BrowserUnavailable) as e:
                return fail(str(e), "Check the credentials and selectors, or import an existing session instead.")
            self.store.log("session", f"browser session {args.name} logged in via form → {landed}")
            return ToolOutput(
                content=f"session {args.name} established (logged in, landed on {landed})."
                f" Use screenshot session={args.name}."
            )
        if not (args.cookies or args.local_storage or args.bearer or args.basic or args.headers):
            return fail(
                "browser_session import was not run: nothing to import",
                "Pass at least one of cookies, local_storage, bearer, basic, headers.",
            )
        try:
            session = await s.import_session(
                {
                    "name": args.name,
                    "role": args.role,
                    "url": args.url,
                    "cookies": args.cookies,
                    "local_storage": args.local_storage,
                    "bearer": args.bearer,
                    "basic": args.basic,
                    "headers": args.headers,
                }
            )
        except (SessionError, BrowserUnavailable) as e:
            return fail(str(e), "Check the credentials (cookies must be 'a=1; b=2' style) and retry.")
        self.store.log("session", f"browser session {args.name} imported for {session.meta['origin']}")
        return ToolOutput(
            content=f"session {args.name} imported for {session.meta['origin']} — check it with"
            f" browser_session action=verify url=<a protected page>, then use screenshot session={args.name}."
        )

"""`intern free status|start|pause|resume|sign-out`: the free trial's sign-in, from a terminal or the desktop app.

Every command takes `--json`. `start --json` prints one JSON object a line as the sign-in goes (`code`, then
`granted` or `refused`); the others print one JSON document. No output holds a token. The terminal UI's setup and
`/free` call the same functions (`claim`, `report`, `sign_out`, `set_use`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import tomlkit
from pydantic import BaseModel
from tomlkit.items import Table

from intern import names
from intern.config import CONFIG_FILE, home_dir, load_user_config, process_env
from intern.config_file import editing
from intern.models.registry import PROVIDERS, ConfigError
from intern.nace_free import (
    DeviceSignIn,
    FreeCredential,
    FreeGrant,
    FreeStatus,
    Refusal,
    SignInCode,
    console_url,
    fetch_grant,
    fetch_status,
    install_id,
    shared_credential,
)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2


def set_use(home: Path, use: bool) -> None:
    """`[free] use` in config.toml, keeping the rest of the file as it was written."""
    with editing(home / CONFIG_FILE) as document:
        table = document.get("free")
        if not isinstance(table, Table):
            table = tomlkit.table()
            document["free"] = table
        table["use"] = use


def console_of(env: Mapping[str, str]) -> str:
    """The console the free credit signs in to: the `nace` provider's, `$NACE_BASE_URL` when set."""
    spec = PROVIDERS["nace"]
    return console_url((env.get(spec.base_url_env) if spec.base_url_env else None) or spec.base_url)


def _credential(home: Path, env: Mapping[str, str]) -> FreeCredential:
    return shared_credential(home, console_of(env), env)


class Granted(BaseModel):
    """A sign-in that worked; `grant` is None when the grant could not be read after it (`report` reads it again)."""

    grant: FreeGrant | None


class FreeReport(BaseModel):
    """`intern free status`: whether this machine is signed in, `[free] use`, the console's status and the grant."""

    signed_in: bool
    use: bool | None
    status: FreeStatus | None
    """None when the console could not be asked."""
    grant: FreeGrant | None
    error: Refusal | None
    """Why the grant could not be read, while signed in."""


async def claim(
    home: Path,
    env: Mapping[str, str],
    consent_version: str,
    on_code: Callable[[SignInCode], None],
    transport: httpx.AsyncBaseTransport | None = None,
) -> Granted | Refusal:
    """Sign in with the console's device flow, the consent text the user saw being `consent_version`: `on_code` is
    told the code to confirm in the browser; once confirmed, the tokens are kept and the free credit turned on."""
    console = console_of(env)
    sign_in = DeviceSignIn(console, consent_version=consent_version, install_id=install_id(home), transport=transport)
    code = await sign_in.start()
    if isinstance(code, Refusal):
        return code
    on_code(code)
    tokens = await sign_in.wait()
    if isinstance(tokens, Refusal):
        return tokens
    credential = _credential(home, env)
    with credential.store.locked():
        credential.store.save(tokens, console=console, consent_version=consent_version)
    await credential.invalidate()  # a sign-in before this one may have left its token in memory
    set_use(home, True)
    found = await fetch_grant(credential, transport)
    return Granted(grant=found if isinstance(found, FreeGrant) else None)


async def report(home: Path, env: Mapping[str, str], transport: httpx.AsyncBaseTransport | None = None) -> FreeReport:
    credential = _credential(home, env)
    status = await fetch_status(credential.console, transport)
    grant: FreeGrant | None = None
    error: Refusal | None = None
    if credential.store.signed_in(credential.console):
        found = await fetch_grant(credential, transport)
        grant, error = (found, None) if isinstance(found, FreeGrant) else (None, found)
    return FreeReport(
        signed_in=credential.store.signed_in(credential.console),
        use=load_user_config(home).free.use,
        status=status,
        grant=grant,
        error=error,
    )


async def sign_out(home: Path, env: Mapping[str, str]) -> bool:
    """Revoke the sign-in on the console and forget it here; whether the console confirmed."""
    return await _credential(home, env).sign_out()


def _emit(as_json: bool, data: dict[str, Any], text: str) -> None:
    if as_json:
        print(json.dumps(data), flush=True)
    else:
        print(text, file=sys.stderr, flush=True)


def _grant_text(grant: FreeGrant | None) -> str:
    if grant is None:
        return "Signed in to the free credit."
    meters = (("agent", grant.meters.agent), ("decision model", grant.meters.system1))
    left = [f"{name} ${m.limit_usd - m.used_usd:.2f} of ${m.limit_usd:.2f}" for name, m in meters]
    return f"Free credit {grant.state}: {', '.join(left)} left."


async def _start(home: Path, env: Mapping[str, str], consent_version: str, browser: bool, as_json: bool) -> int:
    def code_shown(code: SignInCode) -> None:
        _emit(
            as_json,
            {"step": "code", **code.model_dump(), "url": code.url},
            f"Open {code.url} and confirm the code {code.user_code}.",
        )
        if browser:
            webbrowser.open(code.url)

    done = await claim(home, env, consent_version, code_shown)
    if isinstance(done, Refusal):
        _emit(as_json, {"step": "refused", **done.model_dump()}, f"Not signed in: {done.message or done.code}")
        return EXIT_REFUSED
    grant = done.grant
    _emit(
        as_json,
        {"step": "granted", "grant": grant.model_dump(exclude_unset=True) if grant else None},
        _grant_text(grant),
    )
    return EXIT_OK


def _report_json(found: FreeReport) -> dict[str, Any]:
    data = found.model_dump(exclude={"grant"})
    data["grant"] = found.grant.model_dump(exclude_unset=True) if found.grant else None
    return data


def _status_text(found: FreeReport) -> str:
    lines = []
    if (status := found.status) is not None:
        lines.append(f"Free trial: sign-ins {status.grants}, calls {status.inference}.")
    if not found.signed_in:
        lines.append(f"Not signed in. Start with: {names.NAME} free start --accept <consent version>")
    elif found.grant is not None:
        lines.append(_grant_text(found.grant))
    if found.error is not None:
        lines.append(found.error.message or found.error.code)
    if found.use is False:
        lines.append(f"Paused: sessions use your own keys. Resume with: {names.NAME} free resume")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{names.NAME} free", description="Sign in to the Nace console's free credit, and use it or not."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="The grant, its meters and models, and whether the free trial is open.")
    start = commands.add_parser("start", help="Sign in in the browser and claim the free credit.")
    start.add_argument(
        "--accept", required=True, metavar="CONSENT_VERSION", help="The version of the consent text the user saw."
    )
    start.add_argument("--no-browser", action="store_true", help="Print the address instead of opening it.")
    commands.add_parser("pause", help="Use your own keys, keeping the free credit for later.")
    commands.add_parser("resume", help="Use the free credit again while it lasts.")
    commands.add_parser("sign-out", help="Revoke the sign-in on the console and forget it here.")
    for command in commands.choices.values():
        command.add_argument("--json", action="store_true", help="Print JSON to stdout.")
    return parser


def free_main(argv: list[str], env: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = process_env() if env is None else env
    home = home_dir(env)
    try:
        if args.command == "start":
            return asyncio.run(_start(home, env, args.accept, not args.no_browser, args.json))
        if args.command == "status":
            found = asyncio.run(report(home, env))
            _emit(args.json, _report_json(found), _status_text(found))
            return EXIT_OK
        if args.command in ("pause", "resume"):
            use = args.command == "resume"
            set_use(home, use)
            _emit(args.json, {"ok": True, "use": use}, "Using the free credit." if use else "Using your own keys.")
            return EXIT_OK
        revoked = asyncio.run(sign_out(home, env))
        _emit(args.json, {"ok": True, "revoked": revoked}, "Signed out of the free credit.")
        return EXIT_OK
    except (httpx.HTTPError, ConnectionError) as error:
        unreachable = Refusal(code="unavailable", message=f"The console could not be reached ({type(error).__name__}).")
        if args.command == "start":
            _emit(args.json, {"step": "refused", **unreachable.model_dump()}, unreachable.message)
        else:
            _emit(args.json, {"ok": False, "error": unreachable.message}, unreachable.message)
        return EXIT_REFUSED
    except (ConfigError, ValueError) as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}))
        else:
            print(f"{names.NAME}: {error}", file=sys.stderr)
        return EXIT_USAGE

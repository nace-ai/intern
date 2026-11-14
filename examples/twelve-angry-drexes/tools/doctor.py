#!/usr/bin/env python3
"""Readiness check for the live demo: keys, tools, services, and which demo paths can run right now.

    python3 tools/doctor.py             # with live checks (each is free: no decision is bought)
    python3 tools/doctor.py --offline   # keys, tools and files only

Prints names and states only, never a key. Each line that is not ready ends with the command that fixes it.
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import config, drex, intern_runner, keenable, net  # noqa: E402

OK, NO, WARN = "ready", "MISSING", "check"
rows: list[tuple[str, str, str]] = []


def row(item: str, state: str, note: str = "") -> bool:
    rows.append((item, state, note))
    return state == OK


def key(name: str, fix: str, optional: bool = False) -> bool:
    have = bool(config.env_key(name))
    row(name, OK if have else ("optional" if optional else NO), "" if have else fix)
    return have


def call(method: str, url: str, token: str, body: bytes | None = None) -> tuple[bool, str]:
    """One authenticated request. Returns (accepted, short note); the note never contains the token."""
    try:
        _, raw = net.send(method, url, body, {"Authorization": "Bearer " + token, "Content-Type": "application/x-www-form-urlencoded"}, tries=1)
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
        if isinstance(data, dict) and data.get("ok") is False:
            return False, "rejected: " + str(data.get("error", "unknown"))[:40]
        return True, ""
    except net.NetError as e:
        return False, f"HTTP {e.status}" if getattr(e, "status", None) else "no answer"
    except ValueError:
        return True, ""


def main() -> int:
    offline = "--offline" in sys.argv
    fix_key = "python3 tools/set_secret.py {}"

    nace = key("NACE_API_KEY", fix_key.format("NACE_API_KEY"))
    deep = key("DEEPINFRA_API_KEY", "bash setup/1_deepinfra.sh")
    bot = key("SLACK_BOT_TOKEN", "bash setup/3_slack.sh")
    app = key("SLACK_APP_TOKEN", "bash setup/3_slack.sh")
    key("SLACK_CHANNEL_ID", "only @mentions start a trial without it", optional=True)
    key("KEENABLE_API_KEY", "the keyless endpoint is used without it", optional=True)

    cli = row("nace CLI (NDI document jobs)", OK if shutil.which("nace") else NO, "" if shutil.which("nace") else "uv tool install nace-cli")
    uvx = row("uvx (starts Nace's MCP server)", OK if shutil.which("uvx") else NO, "" if shutil.which("uvx") else "brew install uv")
    exe = intern_runner.binary()
    intern = row("Intern", OK if exe else NO, "" if exe else "bash setup/2_intern.sh")
    venv_py = ROOT / ".venv" / "bin" / "python"
    bolt = venv_py.is_file() and subprocess.run([str(venv_py), "-c", "import slack_bolt"], capture_output=True).returncode == 0
    row("slack_bolt in .venv", OK if bolt else NO, "" if bolt else "bash setup/3_slack.sh")
    store = config.LAW_STORE.is_file()
    row("local LawWiki store", OK if store else WARN,
        f"{config.LAW_STORE.stat().st_size / 1e6:.0f} MB" if store else "trials still run on the bundled statute files; build: python3 tools/ingest_lawwiki_local.py")

    host_ok = keen_ok = deep_ok = slack_ok = None
    if not offline:
        if nace:
            try:
                base = drex.live_base()
                host_ok = row("Nace API host", OK, base + ("" if base == config.NACE_HOSTS[0] else "  (the console host is behind a bot check; hosted MCP is off)"))
            except Exception as e:  # noqa: BLE001 - report the class only
                host_ok = row("Nace API host", NO, f"no host answered ({type(e).__name__}); check the key and the network")
        hits, ms = keenable.search("Texas courts self-help theft", 1)
        keen_ok = row("Keenable search", OK if hits else WARN, f"{ms} ms" if hits else "no result; the web step is skipped with a note in the run")
        if deep:
            good, note = call("GET", "https://api.deepinfra.com/v1/openai/models", config.env_key("DEEPINFRA_API_KEY"))
            deep_ok = row("DeepInfra key accepted", OK if good else NO, note)
        if bot:
            good, note = call("POST", "https://slack.com/api/auth.test", config.env_key("SLACK_BOT_TOKEN"), b"")
            slack_ok = row("Slack bot token accepted", OK if good else NO, note)
        if app:
            good, note = call("POST", "https://slack.com/api/apps.connections.open", config.env_key("SLACK_APP_TOKEN"), b"")
            slack_ok = row("Slack app token accepted (Socket Mode)", OK if good else NO, note) and slack_ok is not False

    live = nace and host_ok is not False
    paths = [
        ("Live trial in the terminal", live and cli, "python3 -m engine.trial --case tx-theft"),
        ("Courtroom, Slack view and trace ledger on the engine", live, "python3 -m engine.server   then open http://127.0.0.1:8765/02-live-courtroom.html?engine=1#case=tx-theft"),
        ("Same run through Intern (Drex System 1, Qwen System 2)", live and intern and deep and deep_ok is not False, "bash tools/intern_demo.sh   or the \"Run through Intern\" button on the pages"),
        ("Slack: message in, thread out", live and bot and app and bolt and slack_ok is not False, ".venv/bin/python -m engine.slack_bridge"),
        ("Nace MCP server (Drex and NDI as tools)", live and uvx, "python3 tools/mcp_call.py nace list"),
        ("Speeches written by Qwen (else stock text)", deep and deep_ok is not False, "automatic once DEEPINFRA_API_KEY is set"),
        ("Offline simulation (no key needed)", True, "python3 -m engine.trial --case tx-theft --simulate"),
    ]
    w = max(len(r[0]) for r in rows + [(p[0], "", "") for p in paths])
    print("Keys, tools, services")
    for item, state, note in rows:
        print(f"  {item:{w}}  {state:8}  {note}")
    print("\nDemo paths")
    for name, good, cmd in paths:
        print(f"  {name:{w}}  {'ready' if good else 'NOT YET':8}  {cmd}")
    missing = intern_runner.missing()
    if missing:
        print("\nIntern still needs: " + ", ".join(missing))
    print("\nOne script per service: bash setup/1_deepinfra.sh, setup/2_intern.sh, setup/3_slack.sh, setup/4_keenable.sh; then bash setup/5_smoke.sh")
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())

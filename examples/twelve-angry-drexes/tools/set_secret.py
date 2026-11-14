#!/usr/bin/env python3
"""Store keys and tokens in this project's .env without showing them.

    python3 tools/set_secret.py --missing             # asks for every name that is still empty, one by one
    python3 tools/set_secret.py DEEPINFRA_API_KEY     # asks for one value (replaces an existing one)
    python3 tools/set_secret.py --list                # which names are set (never the values)

Each value is read from a hidden prompt (or from stdin when piped: one value, one name), written to .env
(git-ignored, mode 600) and never printed, logged or passed on a command line. Press Enter at a prompt to skip a name.
"""
import getpass
import os
import pathlib
import sys

ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"
# name: (what it is for, where to get it, expected start of the value or None, needed for)
NAMES = {
    "NACE_API_KEY": ("Drex and NDI", "https://console.nace.ai/dashboard/api-keys", None, "every live run"),
    "DEEPINFRA_API_KEY": ("System 2 for Intern, and the courtroom speeches", "https://deepinfra.com/dash/api_keys", None, "Intern, speeches"),
    "SLACK_BOT_TOKEN": ("Slack bot token", "your Slack app > OAuth & Permissions > Bot User OAuth Token", "xoxb-", "Slack"),
    "SLACK_APP_TOKEN": ("Slack app-level token for Socket Mode", "your Slack app > Basic Information > App-Level Tokens (scope connections:write)", "xapp-", "Slack"),
    "SLACK_CHANNEL_ID": ("optional: a channel where every message starts a trial", "Slack channel > View channel details > Channel ID", "C", "optional"),
    "KEENABLE_API_KEY": ("optional: web search and page fetch (the keyless endpoint is used without it)", "https://keenable.ai", None, "optional"),
}


def read_env() -> dict:
    out = {}
    if ENV.is_file():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v
    return out


def write_env(env: dict) -> None:
    """Write to a new file with mode 600, then swap it in, so .env is never half written or readable by others."""
    tmp = ENV.with_name(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("".join(f"{k}={v}\n" for k, v in env.items()))
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)


def ask(name: str, env: dict, skippable: bool) -> bool:
    """Prompt for one value and save it. Returns True when a value was saved."""
    what, where, start, _ = NAMES[name]
    if sys.stdin.isatty():
        print(f"\n{name}: {what}\n  get it at: {where}")
        value = getpass.getpass(f"  paste {name} (input hidden{', Enter to skip' if skippable else ''}): ").strip()
    else:
        value = sys.stdin.readline().strip()
    if not value:
        print(f"  skipped {name}")
        return False
    if any(c.isspace() for c in value):
        print(f"  not saved: the value for {name} contains spaces")
        return False
    if start and not value.startswith(start):
        print(f"  not saved: a {name} starts with {start} (this one does not; the two Slack tokens are easy to swap)")
        return False
    env[name] = value
    write_env(env)
    print(f"  saved {name} ({len(value)} characters) to .env")
    return True


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Names:\n" + "\n".join(f"  {n:18} {d[0]}" for n, d in NAMES.items()))
        return 2
    env, arg = read_env(), sys.argv[1]
    if arg == "--list":
        for n, d in NAMES.items():
            print(f"{n:18} {'set' if env.get(n) else 'not set':8} needed for: {d[3]}")
        return 0
    if arg == "--missing":
        if not sys.stdin.isatty():
            print("--missing asks at the keyboard; run it in a terminal (or pipe one value to: set_secret.py NAME)")
            return 2
        empty = [n for n in NAMES if not env.get(n)]
        if not empty:
            print("every name is set")
            return 0
        print(f"{len(empty)} name(s) are empty: {', '.join(empty)}")
        saved = sum(ask(n, env, True) for n in empty)
        left = [n for n in NAMES if not env.get(n)]
        print(f"\nsaved {saved}; still empty: {', '.join(left) or 'none'}")
        return 0
    if arg not in NAMES:
        print(f"unknown name {arg}; one of: {', '.join(NAMES)}")
        return 2
    return 0 if ask(arg, env, False) else 1


if __name__ == "__main__":
    sys.exit(main())

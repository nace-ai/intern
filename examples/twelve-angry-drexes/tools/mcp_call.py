#!/usr/bin/env python3
"""List or call the tools of an MCP server over stdio. Used to show the demo's two MCP servers working.

    python3 tools/mcp_call.py jury list
    python3 tools/mcp_call.py jury call run_trial '{"case": "tx-theft", "simulate": true}'
    python3 tools/mcp_call.py nace list                      # Nace's own local MCP server (uvx nace-mcp, MIT)
    python3 tools/mcp_call.py nace call list_models '{}'

`jury` is this repository's server (engine/mcp_server.py). `nace` is Nace's local server: Drex decisions and
NDI document tools. It gets NACE_API_KEY from .env and the Nace host that answers right now; the key is passed
in the child's environment only and never printed. Standard library only.
"""
import json
import os
import pathlib
import selectors
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import config, drex  # noqa: E402

WAIT_SECONDS = 180


def server(name: str) -> tuple[list[str], dict]:
    if name == "jury":
        return [sys.executable, "-m", "engine.mcp_server"], dict(os.environ)
    if name == "nace":
        if not drex.has_key():
            sys.exit("NACE_API_KEY is not set: python3 tools/set_secret.py NACE_API_KEY")
        return ["uvx", "nace-mcp"], {**os.environ, "NACE_API_KEY": str(drex.api_key()), "NACE_BASE_URL": drex.live_base()}
    sys.exit(f"unknown server {name!r}: jury or nace")


class Client:
    def __init__(self, cmd: list[str], env: dict):
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=ROOT, env=env)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self.n = 0

    def send(self, method: str, params: dict | None = None, reply: bool = True) -> dict | None:
        self.n += 1
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {}), **({"id": self.n} if reply else {})}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + WAIT_SECONDS
        while reply:
            if not self.sel.select(max(0.0, deadline - time.monotonic())):
                raise TimeoutError(f"no answer to {method} in {WAIT_SECONDS} s")
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"the server exited (code {self.proc.poll()}) before answering {method}")
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if isinstance(got, dict) and got.get("id") == self.n:  # anything else is a notification
                if "error" in got:
                    raise RuntimeError(f"{method}: {got['error']}")
                return got["result"]
        return None

    def close(self) -> None:
        self.proc.stdin.close()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[2] not in ("list", "call") or (sys.argv[2] == "call" and len(sys.argv) < 4):
        print(__doc__)
        return 2
    c = Client(*server(sys.argv[1]))
    try:
        info = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "legal-research-demo", "version": "0.2"}})
        c.send("notifications/initialized", reply=False)
        print(f"server: {info['serverInfo'].get('name')} {info['serverInfo'].get('version', '')} (protocol {info['protocolVersion']})", file=sys.stderr)
        if sys.argv[2] == "list":
            for t in c.send("tools/list", {})["tools"]:
                print(f"{t['name']:32} {' '.join((t.get('description') or '').split())[:110]}")
            return 0
        t0 = time.monotonic()
        res = c.send("tools/call", {"name": sys.argv[3], "arguments": json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}})
        for part in res.get("content") or []:
            if part.get("type") == "text":
                print(part["text"])
        print(f"{sys.argv[1]}.{sys.argv[3]}: {'error' if res.get('isError') else 'ok'} in {int((time.monotonic() - t0) * 1000)} ms", file=sys.stderr)
        return 1 if res.get("isError") else 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())

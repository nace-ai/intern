#!/usr/bin/env python3
"""Stand-in for the `intern` command in tests. It prints JSON events the way `intern -p --json` does (one per line)
and really drives the jury MCP server over stdio, with simulated decisions, so the whole path is exercised offline.
FAKE_INTERN_FAIL=early exits before any tool call; FAKE_INTERN_FAIL=late exits with an error after the trial;
FAKE_INTERN_FAIL=config refuses a config that names any MCP server besides the jury engine (like a bad entry).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def out(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def main() -> int:
    task = sys.argv[sys.argv.index("--") + 1]
    fail = os.environ.get("FAKE_INTERN_FAIL", "")
    if fail == "early":
        print("intern: System 1 did not answer (system1_unavailable)", file=sys.stderr)
        return 4
    if fail == "config" and 'namespace = "keenable"' in (Path(os.environ["INTERN_HOME"]) / "config.toml").read_text():
        print("config error: capabilities.mcp[keenable] could not be set up", file=sys.stderr)
        return 2
    case = re.search(r'most likely "([^"]+)"', task).group(1)
    # The event types and fields below are Intern 0.30's own (`intern schema`).
    out({"type": "run_start", "protocol_version": 1, "session_id": "s1", "run_id": "r1", "model": "deepinfra/Qwen/Qwen3.8-27B", "cwd": os.getcwd(),
         "tools": ["read", "mcp__jury__list_cases", "mcp__jury__run_trial"]})
    out({"type": "message", "entry_id": "e0", "message": {"role": "system", "content": "You are an agent that gets the user's request done."}})
    out({"type": "turn_start", "turn": 1})
    out({"type": "reasoning_delta", "turn": 1, "text": "Let"})
    out({"type": "message", "entry_id": "e1", "message": {"role": "assistant", "text": "I will list the cases, then run the trial once.", "stop": "tool_calls",
                                                          "tool_calls": [{"id": "c1", "name": "mcp__jury__list_cases", "arguments": {}}]}})
    srv = subprocess.Popen([sys.executable, "-m", "engine.mcp_server"], cwd=Path(__file__).resolve().parent.parent,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def rpc(i: int, method: str, params: dict) -> dict:
        srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
        srv.stdin.flush()
        return json.loads(srv.stdout.readline())

    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake-intern", "version": "0"}})
    out({"type": "tool_call_start", "turn": 1, "call_id": "c1", "name": "mcp__jury__list_cases", "arguments": {}})
    rpc(2, "tools/call", {"name": "list_cases", "arguments": {}})
    out({"type": "tool_call_end", "turn": 1, "call_id": "c1", "name": "mcp__jury__list_cases", "status": "ok", "content": "{}", "duration_ms": 4})
    out({"type": "decision", "purpose": "tool_guard", "backend": "console", "questions": 2, "ok": True, "cached": False, "duration_ms": 180,
         "answers": {"asked": {"noul": 0.97}, "risk": {"noul": 0.03}}})
    out({"type": "turn_start", "turn": 2})
    out({"type": "tool_call_start", "turn": 2, "call_id": "c2", "name": "mcp__jury__run_trial", "arguments": {"case": case}})
    res = rpc(3, "tools/call", {"name": "run_trial", "arguments": {"case": case, "simulate": True}})
    body = json.loads(res["result"]["content"][0]["text"])
    out({"type": "tool_call_end", "turn": 2, "call_id": "c2", "name": "mcp__jury__run_trial", "status": "error" if res["result"]["isError"] else "ok",
         "content": "{}", "duration_ms": 900})
    srv.stdin.close()
    srv.wait(timeout=30)
    if fail == "late":
        out({"type": "run_end", "run_id": "r1", "reason": "turn_budget", "detail": None, "turns": 2, "final_text": "", "system1": {"asks": 1, "failed": 0}})
        print("intern: turn budget reached", file=sys.stderr)
        return 3
    final = (f"Vote {body['vote']}. {body['verdict']} Run {body['run_id']}. "
             "Legal information, not legal advice. A simulation, not a prediction.")
    out({"type": "message", "entry_id": "e2", "message": {"role": "assistant", "text": final, "stop": "stop", "tool_calls": []}})
    out({"type": "run_end", "run_id": "r1", "reason": "completed", "detail": None, "turns": 3, "final_text": final,
         "system1": {"asks": 1, "failed": 0, "cost_usd": 0.0001}, "usage": {"cost_usd": 0.0004}, "duration_ms": 1200})
    return 0


if __name__ == "__main__":
    sys.exit(main())

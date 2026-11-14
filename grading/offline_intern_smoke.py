"""Verify Intern can read the nested game using upstream localhost-only model fixtures.

Run from the repository root: uv run python -m grading.offline_intern_smoke
This is a scripted harness integration check, not a real model assessment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.fake_openai import MODEL, FakeOpenAI, Reply


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    replies = [
        Reply(tool_calls=[("read", {"path": "server/campaign-engine.ts"})]),
        Reply(tool_calls=[("read", {"path": "server/byok.ts"})]),
        Reply(text="Scripted offline inspection complete; no real model verdict."),
    ]
    with tempfile.TemporaryDirectory(prefix="agent-heist-intern-") as home, FakeOpenAI(replies) as server:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "INTERN_HOME": home,
            "VLLM_BASE_URL": server.url,
            **server.system1_env,
        }
        result = subprocess.run(
            [
                sys.executable, "-m", "intern", "-p", "--json", "--no-session",
                "--no-context-files", "--no-extensions", "--no-memory", "--max-turns", "4",
                "--cwd", str(root / "agent-heist"), "--system2", f"vllm/{MODEL}",
                "--", "Read the game engine and provider runtime. Do not edit or deploy anything.",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )  # fmt: skip
        if result.returncode:
            raise RuntimeError(
                f"Offline Intern CLI failed with exit {result.returncode}; no credentials were supplied."
            )
        events = [json.loads(line) for line in result.stdout.splitlines()]
        reads = [e for e in events if e.get("type") == "tool_call_end" and e.get("name") == "read"]
        if len(reads) != 2 or any(e.get("status") != "ok" for e in reads):
            raise RuntimeError("Intern did not complete both game-source reads.")
        if "campaignResolve" not in reads[0]["content"] or "NaceClient" not in reads[1]["content"]:
            raise RuntimeError("Intern's reads did not contain the expected nested game source.")
        if events[-1].get("reason") != "completed" or len(server.requests) != 3:
            raise RuntimeError("Scripted Intern run did not complete as expected.")
    print("PASS: real Intern CLI read the nested engine and provider runtime with localhost-only scripted providers.")
    print("No external model requests, AI grading claims, source edits, or deployments.")


if __name__ == "__main__":
    main()

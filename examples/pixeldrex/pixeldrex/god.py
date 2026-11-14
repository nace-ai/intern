"""God mode: Intern (Nace's agent) tunes the laws of physics of a Drex Life world until it stays alive.

  1. The director designs a world from an idea and saves it to worlds/<slug>.json.
  2. Intern runs it (`pixeldrex life --world ... --report`), reads the report, and, if the world died out,
     froze, or one state took over, edits the rules in the JSON and runs it again.
  3. Two Drexes at work: one decides every cell of the universe; one (Intern's System 1) supervises the god.

Run: pixeldrex god "a coral reef"
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from pixeldrex.llm import DeepInfra
from pixeldrex.life import design_world

ROOT = Path(__file__).resolve().parent.parent
SYSTEM2 = os.getenv("INTERN_SYSTEM2", "deepinfra/deepseek-ai/DeepSeek-V4-Pro")
SYSTEM1 = os.getenv("INTERN_SYSTEM1", "drex-max-v1.1")
# the same Python that runs PixelDrex, so the command works on macOS, Linux and Windows
PIXELDREX = f'"{Path(sys.executable).as_posix()}" -m pixeldrex'


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "world"


def god_task(world_file: str, size: int, steps: int, max_runs: int) -> str:
    cells = size * size
    return f"""You are the god of a tiny simulated world. The world is a {size}x{size} grid of cells. Its laws of physics
are plain-English rules in `{world_file}` (JSON with keys title, states, rules, start). A decision model called Drex
decides what every cell becomes each step by reading those rules, so the rules are the only thing you control.

Run the world with exactly this command (each run costs about one cent; run it at most {max_runs} times):
    {PIXELDREX} life --world {world_file} --size {size} --steps {steps} --report

It prints one JSON report: census_first and census_last (cells per state), extinct (states that died out),
changed_cells_last_5_steps, and gif.

Your goal: a world that stays ALIVE and INTERESTING after {steps} steps, meaning all of:
  - "extinct" is empty,
  - changed_cells_last_5_steps is at least {cells // 3},
  - no single state holds more than {int(cells * 0.85)} cells in census_last.

Loop: run it, read the report, and if the goal is not met, edit only the "rules" (and if needed "start") in
{world_file} to fix the dynamics (for example: give a dying state a way to come back, slow down a state that takes
over, add a cycle so things keep changing). Keep rules short, one per line, only about a cell and its neighbours.
Keep the same states and colors. If a rule ends with a source citation such as (p. 2), it came from a real
document: keep its meaning and its citation when you rephrase it, and end every rule you invent yourself with
(god). Rules are applied from the point of view of the cell that changes ("coral with a warm neighbour becomes
warm", not "warm spreads to coral"), and may only mention the defined states. Then run it again.

When the goal is met, or after {max_runs} runs, stop and reply with: whether the goal was met, the final rules, what
you changed and why in one or two sentences per change, and the gif path of the last run."""


def _brief(e: dict) -> str | None:
    t = e.get("type")
    if t == "tool_call_start":
        name, args = e.get("name"), e.get("arguments") or {}
        if name == "bash":
            return f"runs: {args.get('command', '')[:120]}"
        if name in ("edit", "write"):
            new = (args.get("new_string") or args.get("content") or "").replace("\n", " ")
            return f"rewrites the laws of physics: {new[:140]}"
        return f"{name} {json.dumps(args)[:100]}"
    if t == "nudge":
        return f"System 1 (Drex) nudges the god: {e.get('text', '')[:120]}"
    if t == "tool_guard":
        return f"System 1 guard on {e.get('name')}: {e.get('reason', '')[:100]}"
    return None


async def god(
    idea: str,
    *,
    size: int = 24,
    steps: int = 30,
    max_runs: int = 4,
    world_file: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> dict:
    say = on_line or (lambda s: print(f"  {s}", flush=True))
    if world_file is None:
        async with DeepInfra() as di:
            world = await design_world(di, idea)
        (ROOT / "worlds").mkdir(exist_ok=True)
        world_file = f"worlds/{slugify(world.title)}.json"
        (ROOT / world_file).write_text(json.dumps(world.to_dict(), indent=2))
        say(f"director designed '{world.title}' -> {world_file}")
    original = json.loads((ROOT / world_file).read_text())

    (ROOT / "intern_logs").mkdir(exist_ok=True)
    log_path = ROOT / "intern_logs" / f"{datetime.now():%Y%m%d-%H%M%S}-god-{slugify(original['title'])}.jsonl"
    cmd = [
        "intern",
        "-p",
        "--json",
        "--system2",
        SYSTEM2,
        "--system1",
        SYSTEM1,
        "--max-turns",
        "40",
        "--",
        god_task(world_file, size, steps, max_runs),
    ]
    say(f"Intern takes over as god (System 2 {SYSTEM2.split('/')[-1]}, System 1 {SYSTEM1})")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=ROOT, env=os.environ.copy(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    reports, final, run_end = [], "", {}
    with log_path.open("w") as log:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            log.write(raw.decode(errors="replace"))
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if line := _brief(e):
                say(line)
            if e.get("type") == "tool_call_end":
                content = e.get("content")
                if isinstance(content, list):
                    content = "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
                for line in str(content or "").splitlines():
                    line = line.strip()
                    if not (line.startswith("{") and "changed_cells_last_5_steps" in line):
                        continue
                    try:
                        rep = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    reports.append(rep)
                    say(
                        f"world run #{len(reports)}: extinct={rep.get('extinct')}, "
                        f"changed in last 5 steps={rep.get('changed_cells_last_5_steps')}, "
                        f"final census={rep.get('census_last')}"
                    )
            if e.get("type") == "run_end":
                run_end, final = e, e.get("final_text", "")
    await proc.wait()
    s1 = run_end.get("system1") or {}
    final_world = json.loads((ROOT / world_file).read_text())
    return {
        "idea": idea,
        "world_file": world_file,
        "original_rules": original.get("rules"),
        "final_rules": final_world.get("rules"),
        "reports": reports,
        "final_reply": final,
        "turns": run_end.get("turns"),
        "seconds": round((run_end.get("duration_ms") or 0) / 1000, 1),
        "system1_asks": s1.get("asks"),
        "log": str(log_path.relative_to(ROOT)),
    }

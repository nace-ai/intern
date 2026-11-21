# Cambrian FRC

Drexathon 2026 entry, Apex Predator track. By Christopher Luk and Sri Vatsa Vuddanti.

**Full project, results files and history: https://github.com/listingclown3/drex-hackathon**

This folder is a pointer, not a copy. The project is a TypeScript repository of its own; nothing in
Intern's code, defaults or extensions changes.

## What it is

A strategy lab for FIRST Robotics Competition teams that runs on a laptop. FRC publishes a
brand-new game every January and teams get a few weeks to work out what wins. We built a full
match simulator for the 2026 game, REBUILT, and split the thinking the way Intern does:

- **System 2, between matches.** Intern, with `deepinfra/Qwen/Qwen3.8-27B` as System 2 and
  `drex-latest` as System 1, reads our research notes and results and proposes new strategies.
- **System 1, inside every simulated match.** Drex (`drex-v2.0`) makes each robot's next call
  from a closed list of options, in milliseconds.
- **The simulator is the referee.** Every proposal and every claim is scored by rollouts.

## How it uses Intern

Intern runs headless from Node (`src/lab/intern-client.ts` in our repository), one session per
task, with the task on stdin:

```bash
intern -p --json --no-session --no-memory \
  --system2 deepinfra/Qwen/Qwen3.8-27B --system1 drex-latest \
  --cwd <workspace>
```

Three jobs, 6 sessions and 136 System 1 judgments in total:

1. **Strategy Lab.** Intern reads the workspace (research notes from real robot reveals, the
   rules extracted from the game manual, earlier results) and writes strategy proposals as JSON.
   We validate them against a schema, then simulate each one on 10 held-out seeds against a
   scripted baseline. 5 proposals were accepted and 2 beat the baseline. The best, "shoot on the
   move through the left trench, then refill", gained 141 ± 49 points per match.
2. **Rule scan.** A second session looks for strategies the rules create rather than forbid. Each
   result is then judged by Drex against the manual's conduct rules and tagged legal, risky or
   illegal. One tempting play, shooting from against the tower, was tagged risky under rule G210
   and gained nothing in 10 matches, so we did not use it.
3. **Coding agent.** Intern wrote our generator of deliberately messy scouting sheets
   (`src/ingest/scoutingSheets.mjs`) from a task contract: 25 turns, 30 tool calls, 82 System 1
   judgments. It wrote its own presence and leak checks and fixed the failures they found. We
   reviewed the file before using it.

Agent output is treated as untrusted: proposals that fail the schema are rejected, and nothing
Intern writes is executed without review.

## What we measured

The comparison model ("the giant") is Qwen/Qwen3.5-397B-A17B via DeepInfra with reasoning off,
given the identical state text and options as Drex. All numbers are from real logged runs and hold
within our simulator.

| Measure | Drex (`drex-v2.0`) | Giant |
|---|---|---|
| Time per decision, 3 robots per call | 94 ms | 671 ms |
| Cost per 1,000 decisions | $0.026 | $0.48 |
| Mean regret on 57 sticky scenarios (lower is better) | 15.3 | 16.1 |
| Real-time match margin with a simulator check, 8 paired seeds | +123 | +38 |

- **Decision quality is a tie.** Paired regret difference, Drex minus giant: -0.8 ± 4.8. A random
  chooser scores 33.9.
- **With a simulator check, Drex wins matches.** Drex proposes each call and quick rollouts check
  it; thinking and checking time run on the match clock. Paired Drex minus giant: +85 ± 56. A
  random proposer with the same check scores +68, and Drex beats it by +55 ± 44.
- **Alone, neither model beats our scripted policy** over whole matches (Drex -203 ± 85, the
  giant -141 ± 130 against the scripted reference, 10 paired seeds).
- **Rule calls.** Drex agreed with human-reviewed labels on 59 of 60 fabricated incidents.

## Notes from using Intern

Things we hit while building on Intern 0.30.1, in case they help. The full log with exact output
is `notes/intern-friction.txt` in our repository.

- **Windows.** `uv tool install` succeeds on native Windows, then `intern --help` fails with
  `ModuleNotFoundError: No module named 'fcntl'`. We ran Intern inside WSL 2 and called it through
  `wsl.exe`. A line in the README about supported platforms would have saved time.
- **Headless flags.** `--cwd`, `--max-turns`, `--no-session` and `--no-memory` are what a caller
  from another program needs first, and they appear only in `intern --help`.
- **`--cwd` is not checked at start-up.** A path that does not exist only shows up as a tool error
  several turns in, and the agent keeps going.
- **`cost_usd` is 0** in `run_end` and `decision` events for a DeepInfra System 2 and for
  `drex-latest`, so a caller cannot tell "free" from "price unknown". We computed cost from token
  counts.
- **`intern models`** listed no System 2 models with `DEEPINFRA_API_KEY` set.
- **What worked well.** The `--json` event stream was easy to consume, and passing the task on
  stdin avoided all shell quoting. A trial task finished in 6.7 s: 3 turns, 4 System 1 judgments.

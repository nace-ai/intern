# Demo script: 3 minutes, real calls

Case: `tx-theft` (State v. Dalton, invented). Asker: a prosecutor. Upload: `cases/files/offense_report.pdf` (2 pages).
Every number below is from a live run on 2026-11-14 (`runs/20261114-210526-tx-theft-live`).

## Before the demo (2 minutes)

```
python3 tools/doctor.py                                 # keys, tools, services; every demo path with its command
python3 -m unittest discover -s tests                   # 56 tests, offline
python3 -m engine.trial --case tx-theft --estimate      # 28 requests, 182 decisions, under one cent; sends nothing
python3 tools/mcp_call.py nace list                     # Nace MCP server answers (uvx nace-mcp)
python3 -m engine.server                                # http://127.0.0.1:8765/02-live-courtroom.html?engine=1#case=tx-theft
```

## The run

| Time | What the audience sees | Real call behind it | If it fails |
|---|---|---|---|
| 0:00 | Slack: the question and `offense_report.pdf` posted in the channel | Slack Socket Mode event to `engine/slack_bridge.py` | Show `mocks/01-slack-intake.html`; start the run from the courtroom page instead |
| 0:20 | "What the file says": 7 fields, each with its page | NDI through the `nace` CLI: parse, extract with citations, ground. 7 of 7 fields | The engine falls back to the case's stored fields and marks the step simulated |
| 0:40 | "Legal team: Prosecution. Jurors decide: guilty, beyond a reasonable doubt" | Drex, one request: who is asking, jurisdiction, practice group (Criminal 0.95), practice area (Prosecution 0.98) | none needed: same request as the rest of intake |
| 0:55 | "Law that applies": § 31.08 Value 0.90, § 31.03 Theft 0.89, § 31.16 0.59, § 8.02 Mistake of fact 0.58 | Local LawWiki store ranks 8 sections of 2,718 in the Penal Code and Criminal Procedure; Drex scores 6 candidates in one request | Without the store, the 4 sections the case cites are scored |
| 1:10 | "Public guide checked, then left out" | Keenable search (6 pages, 4 on court or government sites) and fetch; Drex: about this dispute 0.07, instructions for an agent 0.02 | A search outage is a note in the run, not a failure |
| 1:25 | Courtroom: four phases, 12 seats filling in | Drex picks each argument, rules on the objection (0.16, overruled), and all 12 jurors vote after each phase: 24 questions per request | "Run again" on the page; simulated mode needs no key |
| 2:05 | "12 to 0. Defendant guilty." Mean 0.80, spread 0.04 | Verdict level averaged over 12 jurors: state jail felony | |
| 2:15 | "What moves the vote": prepaid belief, 0 of 12, mean 0.14 | 60 more juror votes, one fact flipped at a time | |
| 2:30 | "Next step for the State: File the charge as drafted (97%)" | Drex choice over the prosecution team's 5 options, plus the proof-gap question (0.29) | |
| 2:40 | Trace ledger: 182 decisions, 28 requests, 71,403 tokens, $0.0086, 12.6 s | `runs/<run_id>/events.jsonl` and `summary.json` | `mocks/03-trace-ledger.html` |
| 2:50 | The same trial as one tool call | `python3 tools/mcp_call.py jury call run_trial '{"case": "tx-theft"}'`; a Drex decision through Nace's MCP server: `python3 tools/mcp_call.py nace call decide '{...}'` (299 ms) | |

## Through Intern, and in Slack

```
bash tools/setup_demo.sh            # installs Intern and the Slack library, asks for the empty keys, prints the check
python3 tools/doctor.py             # "Same run through Intern ... ready" is the line to look for
python3 tools/smoke_e2e.py          # live: each service, then one real request through Intern; --slack posts a real thread
bash tools/intern_demo.sh           # terminal: Intern's steps, the jury tool calls, the votes, then Intern's reply
python3 -m engine.server            # pages: "Run through Intern" on the Slack view and the courtroom
bash tools/setup_demo.sh --slack    # Slack app from the manifest, then the two tokens
.venv/bin/python -m engine.slack_bridge
```

Intern runs the request with Drex as System 1 and Qwen 3.8 27B on DeepInfra as System 2. Its tools are three MCP servers: the jury engine (`engine/mcp_server.py`), Nace's `nace-mcp` (Drex and NDI) and Keenable. The trace ledger (`03-trace-ledger.html?engine=1`) then shows Intern's steps above the trial's 182 decisions.

State: the whole path is tested with a stand-in for the `intern` command (it drives the real jury MCP server). It has also run live with Intern itself: the Slack threads of 2026-11-14 (in the last one Intern called `jury.list_cases` and `jury.run_trial`, 182 Drex decisions). If Intern stops before the trial, the Slack bridge runs the jury engine directly and says so; on the pages, use "Run on the engine". The first real run should be compared with `intern schema` (saved to `intern/home/event-schema.json` by the setup script): Intern's event names are read tolerantly, not from a pinned schema.

## Say out loud

- Drex decides; the language model only writes two-sentence speeches from an argument Drex already chose.
- The statutes come from LawWiki's own snapshots, searched locally: no API and no rate limit in the path.
- A web page is untrusted: code keeps only court and government sites, and Drex checks the page before a word of it reaches the jurors.
- The cases are invented and one-sided, so read the what-if table, not the vote. No accuracy against real outcomes has been measured.
- Legal information, not legal advice. A simulation, not a prediction.

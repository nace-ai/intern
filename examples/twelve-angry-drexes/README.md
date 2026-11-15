# Twelve Angry Drexes

A mock jury of 12 for a legal question. Every decision is made by Drex, Nace's fast decision model, on real statutes.

**[Live demo](https://twelve-angry-drexes.vercel.app)** · [Video walkthrough](https://twelve-angry-drexes.vercel.app/walkthrough.mp4) (2 min, narrated, with subtitles) · [Slides](https://twelve-angry-drexes.vercel.app/deck.html) ([PDF](slides/deck.pdf))

![Fig. 01, the trial stack. State v. Dalton, live run on 2026-11-14: the layers from the agent harness up to the answer in Slack, the 12 to 0 vote with each juror's probability, the four facts that move the vote, the next step for the State, and the cost of the run.](docs/figures/fig-01-trial-stack.png)

Built for the Nace.ai Drexathon (Swarm Intelligence track), 2026-11-14.

> **Legal information, not legal advice. A simulation, not a prediction.** Every dispute and person in this repository is invented. One uploaded document is real: a published California Supreme Court opinion (source and license in `data/rulings/`).

## What it does

1. An attorney, a paralegal or a pro se litigant posts a question and a case file in Slack: a report, a lease, a notice, a ruling.
2. Nace Document Intelligence (NDI) reads the file and cites the page of each field.
3. Drex picks the legal team the question belongs to and scores the statutes that apply.
4. Drex decides everything in a four-phase trial: which argument is made, whether an objection stands, and how each of 12 jurors votes.
5. The thread gets the vote, the one fact that moves the vote most, and a suggested next step.

## How it works

![Fig. 04, system architecture. Slack and the Slack bridge feed Intern (System 2: Qwen 3.8 27B, System 1: Drex). Intern calls three MCP servers: Keenable, Nace and the jury engine. The trial engine calls Keenable, the Drex API, NDI and the local LawWiki store. Outputs: the live courtroom, the trace ledger and the Slack thread.](docs/figures/fig-04-system-architecture.png)

A request reaches Intern, Nace's agent harness, with Drex as System 1 and Qwen 3.8 27B as System 2. Intern calls three MCP servers: the jury engine, Nace MCP and Keenable. The trial engine makes the calls to the Drex API, NDI, Keenable and the local LawWiki store (400,952 sections). Its outputs are the live courtroom (an event stream), the trace ledger (`events.jsonl`) and the Slack thread.

- Requirements: [PRD.md](PRD.md)
- Architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Demo script: [DEMO.md](DEMO.md)

## Hackathon tools

| Tool | Use here | State on 2026-11-14 |
|---|---|---|
| Drex (`drex-v2.0`, `drex-max-v1.1`) | Every decision: intake, legal team, statute fit, arguments, objections, 12 juror votes, what-if votes, next step, web-page check. About 182 decisions in 28 requests per trial | Live. 7 of 7 cases run end to end |
| NDI | Reads the uploaded PDF or photo: fields with a page citation each | Live, through the `nace` CLI |
| Nace MCP | `uvx nace-mcp` (Drex `decide`, NDI tools) | Live: `python3 tools/mcp_call.py nace call decide ...` returned in 299 ms. The hosted server `https://console.nace.ai/mcp` answered HTTP 403 (bot check) all afternoon |
| Intern | Agent harness: Drex as System 1, Qwen on DeepInfra as System 2, the three MCP servers as tools (`engine/intern_runner.py`, `intern/config.example.toml`). Slack requests, the courtroom, the Slack view and the trace ledger can all run through it | Run for real on 2026-11-14 with Intern 0.30.1 (39.6 s, one trial, answer written by Qwen); also tested against a stand-in for the `intern` command (9 tests) |
| Keenable | Finds a public self-help page for the dispute; only court and government sites pass a check in code, then Drex checks the page before any of it reaches the jurors | Live, keyless public endpoint |
| DeepInfra | Qwen 3.8 27B: System 2 inside Intern, and the writer of the two-sentence courtroom speeches (Drex picks the argument) | Live: Qwen wrote Intern's answer and 4 of the 8 speeches in the run through Intern (the other 4 were canned text). Without `DEEPINFRA_API_KEY` every speech is canned text |
| Slack | The bridge posts one acknowledgment, then one summary with text bar graphs and one what-if button per fact; a click posts that fact's re-vote in the thread. Every hyperlink in the summary is checked before it is posted; a dead one is posted as plain text (`engine/slack_bridge.py`, `engine/linkcheck.py`, `slack/manifest.yaml`) | Two threads posted live through Intern: one with 2 replies in 54.1 s; one with the buttons by `tools/slack_sample.py` (182 Drex decisions, $0.0087, 22 s, 6 of 6 links live when read back from Slack). The Socket Mode listener for incoming messages and button clicks is built, not yet run live |
| LawWiki | 400,952 statute sections (CA, FL, NY, TX, US Code) in a local SQLite full-text store built from LawWiki's own snapshots | Live, local. No API, no rate limit |

## Live results, 2026-11-14

One live run per case: Drex, NDI on the uploaded file, the local LawWiki store, Keenable. No failures.

| Case | Legal team | Vote | Mean | Suggested next step | Drex | Documents |
|---|---|---|---|---|---|---|
| `tx-theft` State v. Dalton | Prosecution | 12 to 0, guilty | 0.80 | File the charge as drafted (97%) | $0.0086 | $0.024 |
| `ca-injury` Marquez v. Linden Courier | Personal injury | 12 to 0, liable | 0.85 | File suit before the deadline (50%) | $0.0086 | $0.024 |
| `ny-goods` Hartwell v. Castellan | Corporate | 12 to 0, liable | 0.71 | File suit now (41%) | $0.0060 | $0.036 |
| `us-photo` Adeyemi v. Brightwater | Intellectual property | 12 to 0, liable | 0.87 | File an infringement action now (62%) | $0.0087 | $0.024 |
| `ca-deposit` | Real estate | 11 to 1, liable | 0.75 | File the claim now (80%) | $0.0057 | $0.072 |
| `ca-wages` | Employment | 12 to 0, liable | 0.74 | File a wage claim or suit now (69%) | $0.0076 | $0.348 (29-page ruling) |
| `fl-notice` (landlord asks) | Real estate | 12 to 0, liable | 0.85 | Negotiate a settlement (82%) | $0.0068 | $0.009 |

What these numbers do not show: the cases are invented and written with a clear winner, so the votes are lopsided. The what-if table is the useful part: in `tx-theft`, the one fact "defendant reasonably believed his employer had prepaid" takes the vote from 12 of 12 to 0 of 12. No accuracy against real outcomes has been measured. In `ca-injury` Drex picked the consumer team and the case's own setting (personal injury) was used; the run notes say so.

Cost: a trial is about 71,000 input tokens at $0.12 per million, under one cent. The same 182 decisions on a frontier chat model cost about $0.61 at the published $3.33 per 1,000 decisions (Drex technical report, Table 8).

## Demo

- **Site:** https://twelve-angry-drexes.vercel.app plays a walkthrough of the three screens, with subtitles. The same page is `mocks/index.html`.
- **Screens:** [Slack intake](https://twelve-angry-drexes.vercel.app/01-slack-intake.html#case=tx-theft) · [live courtroom](https://twelve-angry-drexes.vercel.app/02-live-courtroom.html#case=tx-theft) · [trace ledger](https://twelve-angry-drexes.vercel.app/03-trace-ledger.html#case=tx-theft)
- **Video:** [walkthrough.mp4](https://twelve-angry-drexes.vercel.app/walkthrough.mp4), 2 min, narrated, with subtitles
- **Slides:** [web version](https://twelve-angry-drexes.vercel.app/deck.html) · [slides/deck.pdf](slides/deck.pdf)

On the site and opened from disk, the screens replay a simulated trial: the probabilities are generated by `mocks/tad-sim.js`, the statutes are real LawWiki records. The numbers from the live runs are in the table above and in the slides. Served by the engine (`python3 -m engine.server`, then "Run on the engine"), the same screens show a real run. The intake thread streams at reading speed, about 40 seconds at 1× (Speed: 1×, 2×, 4×): the thread starts at its first message and follows the newest line, and the uploaded file fills in step with it. `?stream=0` shows the thread at once.

```bash
open mocks/index.html                # the walkthrough
open mocks/01-slack-intake.html      # the Slack thread: file read, legal team, law found, vote, what moves it, next step
open mocks/02-live-courtroom.html    # transcript, 12 juror seats, fact switches, what moves the vote, next step, live trace
open mocks/03-trace-ledger.html      # every decision: request, answer, replay, routing threshold, export
```

## Run it

Standard library only. Tested on Python 3.15.

```bash
python3 -m unittest discover -s tests                 # 56 tests, offline
python3 -m engine.trial --case tx-theft --simulate    # one trial, simulated decisions, no key needed
python3 -m engine.trial --case tx-theft --estimate    # requests, decisions and cost; sends nothing
python3 -m engine.trial --case tx-theft               # live: Drex, NDI, Keenable (needs NACE_API_KEY)
python3 -m engine.trial --case tx-theft --role criminal_defense   # same case, asked by the other side
python3 -m engine.trial --case fl-notice --flip f1    # same case, one fact changed
python3 -m engine.server                              # http://127.0.0.1:8765/02-live-courtroom.html?engine=1
python3 -m engine.replay runs/<run_id> <event_number> # ask one saved question again
```

Cases: `ca-deposit`, `fl-notice`, `ca-wages`, `tx-theft`, `ca-injury`, `ny-goods`, `us-photo`. Legal teams (`python3 -m engine.roles --list`): corporate, intellectual property, real estate, consumer, employment, personal injury, family, criminal defense, prosecution. Each run writes `runs/<run_id>/events.jsonl`, `summary.json` and one file per request.

### Keys and installs

```bash
bash tools/setup_demo.sh              # one pass: installs what is missing, asks for the empty keys, prints the check
bash tools/setup_demo.sh --keys       # only the empty keys and tokens, each with a hidden prompt (Enter skips one)
bash tools/setup_demo.sh --slack      # Slack app: manifest to the clipboard, the 5 steps, then the two tokens
bash setup/1_deepinfra.sh             # one script per service: DeepInfra key (Qwen), then a live call
bash setup/2_intern.sh                # install Intern with its MCP extra, save its event schema, check it
bash setup/3_slack.sh                 # Slack library, app steps, both tokens and the channel ID, token check
bash setup/4_keenable.sh              # optional Keenable key
bash setup/5_smoke.sh                 # live end-to-end pass (add "slack" to post a real thread)
python3 tools/doctor.py               # what is ready: keys, tools, services, and each demo path with its command
python3 tools/smoke_e2e.py            # live end-to-end test: each service, then one real request (about $0.035; --slack posts a real thread)
python3 tools/set_secret.py --list    # which names are set (never the values)
python3 tools/set_secret.py NAME      # one name; `.env.example` lists them all
```

Keys go to `.env` (git-ignored, mode 600) through a hidden prompt and are never printed. Installs: the `nace` CLI, Intern with its MCP extra, and `slack_bolt` in `.venv/`.

Nace names two API hosts in its docs. The engine starts on `https://console.nace.ai`; when that host answers with a bot check (HTTP 403, code `challenge`) it moves to `https://drex.nace.ai` for the rest of the run. Only `nace.ai` hosts are accepted, and a request that carries a key never follows a redirect to another site. Hard caps per run: 400 requests and $0.50.

### MCP, Intern, Slack

```bash
python3 tools/mcp_call.py jury list                                   # this repository's MCP server: 6 tools
python3 tools/mcp_call.py jury call run_trial '{"case": "tx-theft"}'  # a trial as one tool call
python3 tools/mcp_call.py nace list                                   # Nace's local MCP server (uvx nace-mcp, MIT)
bash tools/intern_demo.sh                                             # the same question through Intern; progress, then Intern's reply
.venv/bin/python -m engine.slack_bridge                               # Slack: message in, thread out (through Intern when it is ready)
python3 tools/slack_sample.py tx-theft                                # Slack: one live thread with the what-if buttons, every link checked (--simulate: no model call)
python3 tools/link_audit.py --browser                                 # every statute, site and document link: rows and a summary in runs/
```

**Through Intern.** `engine/intern_runner.py` starts `intern -p --json` with Drex as System 1, Qwen 3.8 27B on DeepInfra as System 2 and three MCP servers as tools (jury engine, Nace MCP, Keenable). The jury server tells the runner which run folder the trial writes to, so the trial's events stream while Intern is still waiting for the tool result. One trace holds Intern's steps, the tools it called and all 182 decisions (`runs/<stamp>-intern/stream.jsonl`).

| Where | How |
|---|---|
| Slack | The bridge uses Intern when it is installed and both keys are set; otherwise, or when Intern stops before the trial, the jury engine runs the trial directly and the thread says so. `TAD_HARNESS=engine` skips Intern |
| Slack view, courtroom | `python3 -m engine.server`, then the "Run through Intern" button (or `?engine=intern` in the address). "Run on the engine" is the direct path |
| Trace ledger | `03-trace-ledger.html?engine=1#case=tx-theft` loads that case's last real run, Intern's steps included (`GET /api/run?case=`) |

## What is real and what is simulated

| Piece | State |
|---|---|
| Statute text, citations, links | Real. 227 fixtures in `data/statutes/`; 74 compared with the LawWiki database rows by MD5, all equal. A section links to its LawWiki page where LawWiki publishes the jurisdiction (CA, FL, US: 136 pages, each answers 200 with the section in its title) and to the official source where it does not yet (Texas: 45 pages on statutes.capitol.texas.gov, each shows its section heading in a browser; New York: 46 pages on nysenate.gov, which refuses automated clients, so code cannot check them). Checked by `tools/link_audit.py --browser` |
| Disputes, people, case documents | Invented. Every document page says so |
| Ruling uploaded in the wages case | Real: *Naranjo v. Spectrum Security Services, Inc.* (Cal. May 6, 2024, S279397), hash-checked |
| Drex, NDI, Keenable, local LawWiki store, Nace MCP | Real calls in a live run |
| Speeches | Written by Qwen when `DEEPINFRA_API_KEY` is set (4 of 8 in the live run through Intern); canned text otherwise |
| Intern | Run for real with Intern 0.30.1 (39.6 s, one trial). The offline tests use a stand-in for the `intern` command |
| Slack | One thread posted live through Intern (2 replies, 54.1 s) and one sample summary with what-if buttons. The offline tests use a stand-in Slack client; the listener for incoming messages and button clicks has not run live |
| Screens on the demo site or opened from disk | Simulated probabilities; real statutes |

## Repository layout

```text
PRD.md, DEMO.md, docs/ARCHITECTURE.md
docs/figures/           the two figures in this README: slides 1 and 4 of the deck
cases/cases.json        7 invented cases, 12 juror profiles (generated from mocks/tad-sim.js)
cases/files/            the documents each case uploads (6 PDFs, 1 photo-style JPEG) and their HTML sources
data/statutes/          statute fixtures from LawWiki
data/lawwiki/           the local statute store (built by tools/ingest_lawwiki_local.py; git-ignored, 890 MB)
data/rulings/           one real ruling: metadata, text, hash, quoted passages
data/hf/                survey and validation of Hugging Face legal datasets
engine/                 trial loop, roles, Drex client, NDI, LawWiki store, Keenable, speeches, server, Slack bridge, MCP server, Intern runner
intern/                 Intern configuration template and guidance for System 1 (home/ is generated, git-ignored)
mocks/                  the walkthrough (index.html), three screens, shared style and simulation, the figure-plate look (design.css, design.js)
slides/                 6-slide deck (deck.html, deck.pdf)
slack/                  Slack app manifest
tests/                  56 offline tests; fake_intern.py stands in for the `intern` command
setup/                  one script per service, numbered in the order to run them
tools/                  setup (setup_demo.sh), key import, readiness check (doctor.py), live smoke test (smoke_e2e.py), link audit (link_audit.py, browser_check.mjs), secrets, MCP client, Intern launcher, ingest, fixtures, renderers
```

The local store is built from LawWiki's own snapshot files (`python3 tools/ingest_lawwiki_local.py`, source folder in `LAWWIKI_SNAPSHOTS`). Without it the engine uses the fixtures in `data/statutes/` and skips the statute search.

## Data sources

- Statutes: LawWiki (`https://www.lawwiki.ai`). Public law; each fixture keeps its citation, source version, official source address and LawWiki link.
- Ruling: *Naranjo v. Spectrum Security Services, Inc.*, text from the `HFforLegal/case-law` dataset, CC BY 4.0, by Louis Brulé Naudet and Timothy Dolan. Not an official copy.
- Other public datasets: `data/hf/DATASETS.md` (survey) and `data/hf/VALIDATION_20261114.md` (11 datasets checked: status, license, PDF count, whether the text cites US law).
- Price and speed figures: Drex technical report (arXiv:2611.07854v2), Table 8; Nace console pricing page.

## License

Apache License 2.0: see [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 Aavinos LLC, Mind Theory AI, and Christian Camacho-Martinez. Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

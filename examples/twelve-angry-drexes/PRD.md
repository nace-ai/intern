# Twelve Angry Drexes: product requirements

| | |
|---|---|
| Product | A mock jury for a legal question, run from Slack |
| Event | Nace.ai Drexathon, Swarm Intelligence track |
| Deadline | 2026-11-14 15:00 Pacific. No edits after. |
| Written | 2026-11-14 11:55 Pacific, version 1; status block updated 13:20 |
| Decisions needed from the owner | D1 to D7 in section 15. D1 (public repository, Apache-2.0) and D2 (API keys) were decided on 2026-11-14. |

## Status at 13:50 Pacific

This block is current. Sections 1 to 17 were written at 11:55; where they differ (3 cases, simulated services), this block and [README.md](README.md) win. Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Demo script: [DEMO.md](DEMO.md).

| Piece | State |
|---|---|
| Engine (`engine/`) | 53 tests pass offline. 7 of 7 cases run live end to end, no failures: about 182 Drex decisions in 28 requests, $0.006 to $0.009 per trial, 10 to 22 s |
| Legal teams | 9 practice roles in 3 groups (corporate, intellectual property, real estate, consumer, employment, personal injury, family, criminal defense, prosecution). Drex picks the team at intake; the team sets the juror question, the standard of proof, the statute scope and the next-step options |
| Statutes | Local store built from LawWiki's own snapshots: 400,952 sections (CA, FL, NY, TX, US Code), full-text search in 24 to 61 ms. 227 fixtures; 74 compared with database rows by MD5, all equal |
| Cases | 7 invented cases in 5 jurisdictions; 6 PDFs and 1 photo as uploads; 1 real ruling (29 pages) |
| Drex, NDI | Live. The console API host answered HTTP 403 (bot check) from about 12:55; the engine moves to `drex.nace.ai`, the other host in Nace's docs |
| Nace MCP | Local server (`uvx nace-mcp`) live; hosted server blocked by the same bot check |
| Keenable | Live, keyless endpoint. Host check in code, then two Drex checks before a page is used |
| Jury engine as MCP server | Live (`engine/mcp_server.py`, 6 tools) |
| Intern | Runs for real (Intern 0.30.1, 2026-11-14 14:10 PT): Drex as System 1, Qwen 3.8 27B on DeepInfra as System 2, the jury engine as an MCP tool. One request: 39.6 s, one trial, 5 Drex asks (guard, supervise, wrap-up), answer written by Qwen. Slack requests, the Slack view, the courtroom and the trace ledger run through `engine/intern_runner.py`, with one trace of Intern's steps, tool calls and the trial. Found and fixed on the first real runs: Intern's 30 s tool timeout (now 300 s for the jury server), a repeated trial (now one per session), empty variables named in the config, Qwen's reasoning eating the speech budget. |
| Slack | Bridge goes through Intern, with the jury engine as fallback. Both tokens are accepted by Slack (`python3 tools/smoke_e2e.py --only slack`). A live thread needs the bot in the channel (`/invite`) and, for @mentions and file uploads, the scopes in `slack/manifest.yaml` (`bash setup/3_slack.sh`). |
| DeepInfra speeches | Written; canned text until the key is set |
| Screens | Three screens show role, what moves the vote, next step and token pricing; each runs simulated, on the engine, or through Intern (`engine/server.py`); one look, the figure plate (Design A) |
| Setup | `tools/setup_demo.sh` (installs, keys with hidden prompts, Slack app steps) and `tools/doctor.py` (readiness of every demo path) |
| Not done | Accuracy against real outcomes (no labeled set), slides, Adobe step |

## 1. Summary

A person posts a legal question in Slack and attaches a lease, a notice, or a court ruling. An Intern agent reads the file with Nace Document Intelligence (NDI), pulls the governing statutes from LawWiki, and runs a four-phase trial in which Drex makes every decision: which statute applies, which argument counsel makes next, whether an objection is sustained, how strong each argument was, and how each of 12 jurors votes. A language model writes the two-sentence speeches and nothing else. The asker watches the vote form live, changes one fact, and sees the jury vote again (target: under 3 seconds).

One simulated trial is about 113 Drex decisions. At the published price of $0.06 per 1,000 decisions that is $0.0068 per trial. The same 113 decisions on a frontier chat model cost about $0.38 and take about 226 seconds of model time (Drex technical report, Table 8). That price gap is what makes "ask 12 jurors, four times, then ask again with one fact changed" a product instead of a stunt.

The output is legal information and a simulation. It is not legal advice and not a prediction of what a court will do.

## 2. What the hackathon rewards, and how this entry answers

Source: Drexathon rules page and submit form, read 2026-11-14.

| Rule or criterion | Requirement | This entry |
|---|---|---|
| Track | Apex Predator, Swarm Intelligence, or Wildcard | Swarm Intelligence: 16 agents (clerk, two counsel, judge, 12 jurors) on one case file |
| Finalist rule | Finalists are picked by use of Drex, Intern, NDI and partner tools | All four, each doing work the product cannot run without (rows below) |
| Drex | Decisions come from Drex | 13 decision types, about 113 decisions per trial, 24 more per changed fact |
| Intern | Build with Intern | Intern is the agent in the Slack thread, the caller of every tool, and the writer of every speech |
| NDI | Document tools | The case reaches the jury only as fields NDI extracted, each with a page number and a located quote |
| DeepInfra (extra credit) | Partner model host | Qwen3.8-27B on DeepInfra is Intern's System 2 and writes the speeches |
| Keenable (extra credit) | Agent web search | Fetches the court's public self-help page for the claim type, linked in the reply |
| Adobe (extra credit) | Not specified on the rules page | Stretch: verdict report exported to PDF. Ask the organizers which Adobe API counts (D4) |
| Submission | Presentation, open-source code in a public GitHub repository, form at nace.ai/drexathon/submit | Section 16. D1 decided: public, Apache-2.0 |
| Credits | Code DREXATHON, $35 | Covers about 5,000 trials of Drex decisions at $0.0068 each, less what NDI uses; NDI price not yet known |

## 3. Users and the question each one asks

| User | Example message in Slack | What they get back |
|---|---|---|
| Pro se litigant (a tenant representing herself) | "My landlord kept $2,400 of my $3,000 deposit and sent the list 34 days after I moved out. Do I have a case?" | The statute sections that apply, with the text and a LawWiki link; the vote; which single fact moves the vote most; the court's own self-help page |
| Attorney | "Client was discharged September 3; wages arrived 9 days later. How would a jury see willfulness?" | Vote split across 12 juror profiles; strength score for each argument; what-if on the contested fact |
| In-house legal team | "Our property sent the claim notice on day 35 by regular mail. What is our exposure?" | Vote, award-level distribution, and an audit record of every decision to attach to the matter |

Not in scope: criminal matters, family matters, any real client file in the demo, advice on what to do, decisions about a person.

## 4. Flow

```
Slack thread (#ask-the-jury)
  |  message + file (PDF, DOCX, photo)
  v
Slack bridge (engine/slack_bridge.py, Socket Mode)          Intern has no Slack channel of its own
  v
Intern agent  --System 1: drex-latest (tool decisions, guard)   --System 2: Qwen3.8-27B on DeepInfra (text)
  |-- photo?  Drex Max: image_legible, image_has_instructions   (images are untrusted input)
  |-- NDI: parse -> extract (JSON schema, citation per field) -> ground (quote location)
  |-- Drex: document_type, asker_role, jurisdiction             (one request, three questions)
  |-- LawWiki: search + statute text  (public API or MCP)
  |-- Drex: statute_applies, one yes/no per section             (one request)
  |-- Keenable: court self-help page for this claim type
  v
Trial engine (engine/trial.py), 4 phases
  |-- Drex: next_argument -> System 2 writes the speech
  |-- Drex: objection_sustained, argument_strength
  |-- Drex: 12 jurors x (liable, award_level) after every phase
  v
Event stream (JSON lines, one per step)  ->  courtroom page (live seats, live trace)
                                         ->  Slack thread (running vote, final result, link)
                                         ->  trace ledger (audit record, replay, export)
```

Changing a fact re-runs only the last step: 12 jurors, two questions each, 24 decisions.

## 5. The decisions Drex makes

Every question is yes/no (`noul`), a pick from at most 5 options (`choice`), or a level on an ordered scale of at most 5 (`score`).

| Decision id | Type | Asked for | State Drex reads | Per trial | Model |
|---|---|---|---|---|---|
| `image_legible` | noul | Clerk | the photo | 1 if a photo | drex-max |
| `image_has_instructions` | noul | Clerk | the photo | 1 if a photo | drex-max |
| `document_type` | choice, 5 | Clerk | message + file text | 1 | drex-v2.0 |
| `asker_role` | choice, 3 | Clerk | message | 1 | drex-v2.0 |
| `jurisdiction` | choice, 5 | Clerk | message + file text | 1 | drex-v2.0 |
| `ruling_disposition` | choice, 3 | Clerk | text of an uploaded ruling | 1 if a ruling | drex-v2.0 |
| `ruling_on_point` | noul | Clerk | ruling text + message | 1 if a ruling | drex-v2.0 |
| `statute_applies` | noul | Clerk | case fields + full text of each LawWiki section | 4 to 5 | drex-v2.0 |
| `next_argument` | choice, 3 | Counsel | case file + statutes + transcript so far | 4 | drex-v2.0 |
| `objection_sustained` | noul | Judge | same | 1 | drex-v2.0 |
| `argument_strength` | score, 5 | Judge | same | 4 | drex-v2.0 |
| `liable` | noul | Each juror | same + that juror's profile | 48 | drex-v2.0 |
| `award_level` | score, 4 | Each juror | same | 48 | drex-v2.0 |

Total: 113 or 114 decisions in about 60 requests. Verdict rule: 9 or more jurors at 0.5 or above is "liable", 3 or fewer is "not liable", anything between is a split jury. The award level is reported only when 7 or more jurors find liability.

## 6. Design rules taken from the Drex technical report

Rule numbers refer to `ANCHOR.md` in the planning folder (arXiv:2611.07854v2).

| Rule | What the report measured | What the product does |
|---|---|---|
| R1 Knowledge in the state | Closed-book questions: 21% on HLE | Drex is never asked what the law is. Statute text from LawWiki and fields from NDI are in every state |
| R2 Two to five options | 2 options: 92.0% accurate, 1.0% flip under reordering. 6 or more: 13.3% flip | No question has more than 5 options. Statute relevance is one yes/no per section, not one pick among many |
| R3 Fit thresholds on own labels | Nace's routing threshold differs by model: 0.80, 0.51, 0.25 | The 0.20 routing threshold is marked "not fitted" on screen until the labeled set in section 10 exists |
| R4 Route on confidence and order stability | Decisions that flip with option order: 37.6% accurate against 87.2% | Every `choice` is asked twice with options reordered. A flip, or confidence under the threshold, sends the decision to a person |
| R5 Model by workload | v2.0 encodes a long state once: 21.6 ms per extra question on a cached 114k-token state | drex-v2.0 for the trial (many questions, one case file). drex-max only for photos |
| R6 Build on measured strengths | Decision Bench legal 95.2 (Opus 5.5: 93.7); contract NLI 83.2; claim verification 87.6 | Legal reading over supplied text is the whole workload |
| R8 Drex decides, a language model writes | Nace's own search product: Drex routes, a language model answers | `next_argument` picks; System 2 writes two sentences; System 2 output is never parsed for a decision |
| R9 Audit record | Questions are answered independently of each other | Each decision is stored with request, answer distribution, model version and request id, and can be replayed alone |
| R10 Images are untrusted; no sole basis for decisions about people | Appendix I | Two photo checks before extraction. The result is a simulation shown to the asker, never a determination about a person |

Juror variety. Each juror has a one-line profile in the state, and reads the exhibits in a different seeded order. If profiles alone move the probability by less than 0.05 across jurors on the first real run, the profile is dropped and jurors differ only by exhibit order. The spread across jurors is then a direct order-stability measure, which R4 ties to accuracy.

## 7. Requirements

Must have for the submission (P0)

1. A trial runs end to end for the three simulated cases from one command and writes one JSON line per step.
2. Every decision in section 5 is a real Drex call when `NACE_API_KEY` is set. Without a key the engine runs a seeded simulation and marks every event `simulated: true`.
3. The courtroom page shows the transcript, 12 juror probabilities, the running vote, and the trace, updating as events arrive.
4. Changing a fact re-runs the 12 jurors and shows the vote before and after.
5. The ledger shows, for any decision: question, distribution, model version, request id, tokens, latency, cost, and the request body. Any decision can be replayed alone.
6. Statute text comes from the LawWiki public API. Every cited section links to its LawWiki page.
7. Every surface carries: "Legal information, not legal advice. A simulation, not a prediction."
8. Cost and latency counters use measured values from the API response; simulated values are labeled.

Should have (P1)

9. Slack bridge: message and file in, progressive replies in the thread, link to the courtroom.
9a. Ruling path: an uploaded ruling is read by NDI, Drex answers `ruling_disposition` and `ruling_on_point`, and an on-point holding is added to what the jurors read.
10. NDI parse, extract and ground on a real file, with page numbers in the reply.
11. Speeches written through Intern with Qwen3.8-27B on DeepInfra.
12. Keenable fetch of the court self-help page.
13. Labeled set and the numbers in section 10.

Could have (P2)

14. Verdict report as PDF through an Adobe API.
15. More real rulings from the public datasets in section 9, one per case. One is in use today.

## 8. Architecture and files

| Path | What it is |
|---|---|
| `engine/trial.py` | The trial loop. Library call and command line. Emits events |
| `engine/drex.py` | Drex client: many questions per request, retries, order check, audit files, simulated fallback, `--probe` |
| `engine/ndi.py` | NDI parse, extract, ground with polling; photo checks through drex-max |
| `engine/lawwiki.py` | Statute text and search from `https://www.lawwiki.ai/api/v1` with local fixtures |
| `engine/speech.py` | Speech through Intern, else DeepInfra, else the case's stored speech |
| `engine/server.py` | Local server: static mocks plus `/api/stream` (server-sent events) so the courtroom shows an engine run |
| `engine/slack_bridge.py` | Slack Socket Mode bridge (needs `slack_bolt`, MIT) |
| `engine/replay.py` | Re-sends one saved question and prints old and new answer |
| `cases/cases.json` | 3 invented cases and 12 juror profiles. Generated from `mocks/tad-sim.js` |
| `cases/files/` | The document each case uploads: lease and statement (PDF, 6 pages, invented), claim notice (JPEG photo, invented), ruling (PDF, 29 pages, a real opinion with its source printed on page 1) |
| `data/rulings/` | The real ruling: metadata, full text, SHA-256, and the passages quoted from it |
| `data/statutes/` | 14 statute sections fetched from LawWiki on 2026-11-14 |
| `data/hf/DATASETS.md` | Survey of four Hugging Face legal datasets: license, fields, fit |
| `mocks/` | Three interactive screens and their shared style and simulation |
| `tools/` | `fetch_lawwiki.py`, `fetch_ruling.py`, `build_mock_data.py`, `export_cases.js`, `render_case_files.sh` |

Python standard library only for the engine. `slack_bolt` is the one optional dependency. No build step for the screens.

Where Intern sits. (a) The agent in the thread: the bridge holds one `intern rpc` session per Slack thread; Intern's System 1 (Drex) decides each tool call and its built-in guard vets it, which matters because uploaded files are untrusted. (b) The writer: each speech is one headless call, `intern -p --json --system1 drex-latest --system2 deepinfra/Qwen/Qwen3.8-27B -- "<prompt>"`. (c) The tool host: LawWiki and Keenable are reached through Intern's MCP client. If Intern cannot be installed in time, the engine calls DeepInfra directly; that keeps the demo alive and loses finalist-rule credit.

## 9. Data

Statutes: LawWiki public API, no key. Verified live 2026-11-14 11:28 Pacific: `/api/v1/jurisdictions`, `/api/v1/search`, `/api/v1/statutes/{jurisdiction}/{code}/{section}`. Fixtures hold 14 sections with citation, text, source version and LawWiki link.

| Case | Asker | Upload | Governing sections (LawWiki) | Fact that turns it |
|---|---|---|---|---|
| Whitfield v. Harlow Property Group (CA) | Pro se tenant | Lease and itemized statement, PDF, 6 pages | Cal. Civ. Code § 1950.5; Cal. Code Civ. Proc. § 116.221 | Itemized statement sent within 21 days |
| Okafor v. Bayline Residential (FL) | In-house legal team | Photo of the claim notice | Fla. Stat. § 83.49; § 83.48 | Claim notice sent within 30 days, by certified mail |
| Reyes v. Northgate Fabrication (CA) | Attorney | A real ruling: *Naranjo v. Spectrum Security Services, Inc.* (Cal. May 6, 2024, S279397), PDF, 29 pages | Cal. Lab. Code § 201; § 203; § 218.5 | Wages paid on the day of discharge |

The three disputes, the people in them, the lease and the notice are invented. The ruling attached in the third case is a real published opinion of the California Supreme Court, taken from the `HFforLegal/case-law` dataset (CC BY 4.0). Its text matches the SHA-256 the dataset publishes, and every passage shown to the jury is cut from that text by code (`tools/fetch_ruling.py`) and checked by a test. No private case file is used anywhere in this repository.

Public datasets surveyed (details and license terms in `data/hf/DATASETS.md`):

| Dataset | License | Use here |
|---|---|---|
| `incrediblecrab/us-scotus-opinions` | Opinions are US government works per the dataset card; repository field says "unknown" | Real rulings for the "ruling uploaded in Slack" path and for a labeled disposition test (affirmed, reversed, vacated) |
| `HFforLegal/case-law` | CC BY 4.0 | In use: one California Supreme Court opinion is the ruling uploaded in the third case (`data/rulings/`). Credit the authors. Screen out juvenile and criminal matters before adding more |
| `HarshitKZ/corporate-lawsuits-2019-2026` | CC BY-NC 4.0 | Has outcome columns, but non-commercial and news-derived. Not used unless the owner says so (D5) |
| `vaquill/open-us-law` | CC BY-NC 4.0, gated | Not used. LawWiki already supplies statutes |

Sample rows from these sets stay on this machine (`data/hf/*/sample.jsonl` is ignored by git).

## 10. Proof

Nothing in this section has been measured yet. Each line is a number the build must produce or drop.

| Claim | Test | Size | Metric |
|---|---|---|---|
| The jury follows the statute | Variants of the 3 cases where the statute text alone settles the outcome (for example: notice sent on day 35, Fla. Stat. § 83.49(3)(a)). Label written from the statute, with the quote | 30 variants | Share of variants where the majority matches the label; Brier score of mean P(liable) |
| Drex reads rulings correctly | Supreme Court opinions with the judgment line removed; Drex picks affirmed, reversed, or vacated | up to 837 opinions | Accuracy, and precision on the most confident 80% |
| Same answer, far cheaper | The 30 variants also run on one frontier chat model with the same file | 30 pairs | Agreement, McNemar test, cost and time per trial |
| Order stability on our workload | Share of `choice` decisions that change when options are reordered | every run | Flip rate, and accuracy of flipped against stable decisions |

Cost before any run. Drex: $0.06 per 1,000 decisions, $0.0068 per trial, $6.80 per 1,000 trials, about $0.20 for the 30 variants. Speeches: 8 per trial, about 1,500 tokens in and 160 out each on Qwen3.8-27B, about $0.004 per trial at the OpenRouter list price read today. NDI price per page: not yet known; read it from the console before the first batch. For comparison, the LawWiki California summary run cost $0.02 to $0.04 per 1,000 items; a Drex decision at $0.06 per 1,000 is in the same range. Hard cap in the engine: 400 requests and $0.50 per run.

## 11. Screens

Three interactive screens in `mocks/`, opened straight from disk. They share one simulated run (`mocks/tad-sim.js`): the probabilities are generated, the statutes are real LawWiki records, and every screen says so.

| Screen | File | What moves |
|---|---|---|
| Intake in Slack | `01-slack-intake.html` | The thread fills in as the agent works: fields read from the file, law that applies, the running vote, the result. Selecting a field shows where it was found in the file. A what-if reply runs the vote again |
| Courtroom | `02-live-courtroom.html` | Transcript, 12 juror seats that fill to each probability, phase means, award levels, fact switches that trigger a new vote, and a live trace. Served by `engine/server.py`, the same page plays an engine run |
| Trace ledger | `03-trace-ledger.html` | Every step in order, filters, the audit record for any decision, replay alone, option-order check, a threshold slider that shows how many decisions go to a person, and JSON lines export |

Visual language: the Nace console's own. Warm stone neutrals (`#0c0a09`, `#1d1816`, `#2b2422`, `#fbfaf9`), one orange ramp (`#ffb96d` to `#9f2d00`) used only for model output, Inter for words, Geist Mono for anything a model returned, 6-pixel corners, hairline borders, no shadows. No Nace logo or name is used as branding.

## 12. Demo script, 3 minutes

1. 0:00 Slack. The legal team posts the Florida question with a photo of the notice. (Intern, Slack bridge)
2. 0:20 The agent replies with seven fields and their page numbers, then the two sections that apply. (NDI, Drex Max, LawWiki, Drex)
3. 0:50 Courtroom. Counsel argue; the 12 seats fill after each phase; the trace scrolls. Vote 12 to 0.
4. 1:40 Switch on "Claim notice sent within 30 days". The jury votes again in 24 decisions and the vote turns against the tenant (3 to 9 in the simulated screen).
5. 2:10 Ledger. Open one juror's decision: request, distribution, replay alone. Move the threshold; watch decisions route to a person.
6. 2:40 The cost line: about 114 decisions, $0.0068, against $0.38 on a frontier chat model. Close on the measured number from section 10 if one exists.

## 13. Build plan to 15:00, and what to cut

| Time (Pacific) | Step | Owner |
|---|---|---|
| 12:00 | Keys set in the owner's own shell. `python3 -m engine.drex --probe` to confirm the response shape (on a 404, repeat with `NACE_API_BASE=https://drex.nace.ai`) | Owner |
| 12:15 | Real run of the three cases; fix shape differences; keep the run files | Build |
| 12:45 | Install Intern (`uv tool install --python 3.12 "intern-agent @ git+https://github.com/nace-ai/intern"`); speeches through Intern | Build |
| 13:15 | NDI on the three fixture files | Build |
| 13:45 | Courtroom served from the engine; record 90 seconds of screen | Build |
| 14:10 | Five slides: problem, flow, live demo, numbers, what is next | Owner |
| 14:30 | License change, `ip_check.py`, secret scan, push to a public repository | Owner approves, then build |
| 14:45 | Submit form | Owner |

Cut in this order when time runs short: Adobe PDF; Keenable fetch; live Slack bridge (show screen 1 and say it is a mock); labeled variants beyond 9; NDI on the photo; speeches through Intern (fall back to DeepInfra direct).

## 14. Risks

| Risk | Sign | Response |
|---|---|---|
| Drex response fields differ from the docs read | `--probe` output does not parse | The client keeps the raw response and parses loosely; fix the one parser function |
| NDI request layout differs | NDI call returns 4xx | The trial does not stop: it uses the stored fields, marks them simulated and prints a note. Fix `engine/ndi.py` from the saved error |
| Juror profiles do not move Drex | Spread across jurors under 0.05 | Jurors differ by exhibit order instead (section 6) |
| All juries are unanimous, so the demo is flat | 12 to 0 on every case | Lead with the Florida case, where one fact takes the vote from 12 to 0 to 3 to 9 in the simulation; keep the what-if step |
| No Slack app in time | No tokens by 13:00 | Screen 1 stands in; say so |
| Readers take the vote as a prediction | Any use of "will win" | Fixed wording on every surface; the result card says what the number is and is not |
| A real file is uploaded during the demo | Any non-fixture file | The demo workspace accepts only the three fixture files |
| Public repository exposes private work | `ip_check.py` FAIL | Nothing from a private repository is in this one; statute text is public law; run the check before any push |

## 15. Decisions for the owner

| # | Decision | Default until answered |
|---|---|---|
| D1 | Make the repository public, and under which license (Apache-2.0 matches Intern; MIT is the alternative). The hackathon requires open source | Decided 2026-11-14: public repository, Apache-2.0 |
| D2 | `NACE_API_KEY` and `DEEPINFRA_API_KEY` set in the owner's shell (`KEENABLE_API_KEY` optional) | Engine runs simulated and says so |
| D3 | Create a Slack app (scopes `app_mentions:read`, `chat:write`, `files:read`, `channels:history`) and set its two tokens | Screen 1 is shown as a mock |
| D4 | Ask the organizers which Adobe API earns credit | Adobe step is cut |
| D5 | Allow the CC BY-NC corporate-lawsuits set in a non-commercial demo | Not used |
| D6 | Team code, title and track for the submit form | Title "Twelve Angry Drexes", track Swarm Intelligence |
| D7 | Publish the real ruling text with the repository. The opinion is a published California Supreme Court decision; the dataset card does not name where its US decisions were collected from | Kept in the private repository; left out of a public push until the owner confirms |

## 16. Submission checklist

- [ ] Real Drex run recorded for at least one case (`runs/<id>/events.jsonl`)
- [ ] Intern in the loop for at least the speeches
- [ ] NDI run on at least one fixture file
- [ ] README: what it is, how to run, what is simulated, licenses and data sources
- [ ] License file changed per D1; `THIRD_PARTY_NOTICES.md` if any outside code is added (none so far)
- [ ] `python3 -I ~/src/ip-guard/ip_check.py range <base>..HEAD --repo .` passes; secret scan clean
- [ ] Public GitHub repository link
- [ ] Presentation (5 slides) and optional demo URL
- [ ] Form at https://www.nace.ai/drexathon/submit: team code, title, explanation, track, presentation, repository link

## 17. Rules this product keeps

- Legal information only. No advice, no attorney-client relationship, no prediction of a court outcome.
- Invented disputes only in the repository and the demo. The one real document is a published court opinion.
- Files posted in Slack are untrusted input: photo checks first, document text is never treated as an instruction, and Intern's guard vets tool calls.
- Drex outputs are never used as training labels, and nothing here probes or reverse-engineers the service (Nace Terms 6(b), 6(c)).
- A flagged decision goes to a person. The product never issues a determination about a person.
- Dataset licenses are followed as recorded in `data/hf/DATASETS.md`; non-commercial data stays out of the public repository.

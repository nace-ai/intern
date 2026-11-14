# Architecture

BillProof turns a packet into a cited decision and a letter a person can sign. Code computes every dollar and applies every published rule. Drex answers a fixed list of typed questions. NDI and Drex Search attach each fact to a page. The model does not write the letter.

Two packets use the same shape.

A prior-authorization request:

```
Browser run  →  NDI  →  Drex Search  →  Drex  →  rules code  →  letter
```

A bill:

```
PDF packet  →  NDI  →  CMS rule checks  →  Drex  →  verdict code  →  letter
```

The live case view and the benchmark runner call the same decision code. A Demo replay serves recorded responses and is labelled as a replay. Demo mode replays recorded real results; every number shown comes from real runs on the benchmarks; the snapshot is frozen only so a live demo never depends on live APIs; Live mode runs it for real with your own keys. `MOCK_MODE` fixtures are separate: when a key is missing, the adapters use fixtures (`auto` by default), and a fixture run is labelled simulated.

## Prior authorization

1. **Browser.** A browser run opens the public policy page for the drug, returns the criterion quotes, and records the document URL. Code fetches the page again and checks that each quote appears word for word. The run is `src/billproof/browser/runs.py`. Recorded runs live under `browser/recorded/`.

2. **NDI.** The policy page and the request packet are split, extracted, and grounded. Each field comes back with a page and a polygon, or as not found. Misses stay visible. The adapter is `src/billproof/adapters/ndi.py`.

3. **Drex Search.** Both PDFs go into a per-case workspace. One search returns, for each criterion, a record id, a page, and a quote. Code checks the quote against the packet text. The client is `src/billproof/search/client.py`; the chain is `src/billproof/search/chain.py`.

4. **Drex.** Drex answers one question per fact: satisfied, not satisfied, or not documented, plus payer and date. The call is one batch. Each answer carries a native probability. The adapter is `src/billproof/adapters/drex.py` (`POST /v1/systemone`).

5. **Rules code.** `src/billproof/bench/pa/logic.py` applies the published AND/OR criteria to those answers and emits the decision, the action, the missing information, and the criterion states. Removing one record at a time and asking again names the deciding record. Dollar figures, when a bill is in the packet, are integer cents in `src/billproof/rules/money.py`.

6. **Letter.** On the prior-auth demo, `src/billproof/pa_demo/letters.py` takes the letter type from the computed decision (`_TYPE_FOR_DECISION`). The recorded clauses say `picked_by` decision. `generated_words` stays 0: a quote in the letter is text from a document. A person signs the draft.

The five demo packets (PA-D1 through PA-D5) are this chain, stored under `data/pa_demo/` and served by `src/billproof/server/pa_routes.py`.

## Bills

NDI reads the bill, the explanation of benefits, the denial, and the plan summary into one `Case` (`src/billproof/schema.py`). Gold labels never enter that state.

`src/billproof/rules/checks.py` looks up CMS Medically Unlikely Edits, Medicaid NCCI procedure-to-procedure edits, and the Physician Fee Schedule (`rules/cms_tables.py`). It flags duplicate lines, units over the edit, unbundled pairs, charges over the fee schedule, bill-versus-EOB mismatches, services after death, missing fields, and balance bills under the No Surprises Act. Each flag carries a rule cite and the cents at stake.

`src/billproof/decisions/questions.py` builds the same typed questions for every lane: yes/no, a choice, or a score. `decisions/verdicts.py` then chooses OK, dispute, appeal, ask the patient, or insufficient evidence. The model never sets the amount owed. `decisions/lanes.py` runs three lanes on those questions: the frontier model alone, Drex alone, and Drex first with an escalation band.

Bill letters use `src/billproof/letters/assembler.py`. Drex chooses the letter type, the recipient, and the grounds. Code fills amounts, dates, cites, and deadlines. `generated_words` stays 0: a quote in the letter is text from a document. A person signs the draft.

Default bands are in `decisions/escalation.py`: at or above 0.90 keep Drex; from 0.60 to 0.90 send Drex's draft to the frontier model to verify; below 0.60, or on a balance-bill or "not received" cap, a person reviews. Bands that were tuned were tuned on the development split only.

## Command Center

The Command Center is a queue of prior-authorization requests. Drex answers each criterion. Code applies the published rule and paints the row red, amber, or green. The replay and the what-if engine are `src/billproof/command_center/`.

A what-if keeps the cached Drex facts and changes the combining rule. It does not call the model again, unless the edit is a new criterion sentence, which is asked once and then cached. Preset edits (a stricter reading of "not documented", an OR list switched to AND, a date window moved) are hypothetical. They are not a published policy change.

The OR-to-AND figure in the results is simulated. No matching before-and-after policy pair was found in the source files. The same recorded facts are rescored with every alternative clause required.

## Trust dial

The dial asks Drex first. For a threshold t, every question whose recorded confidence is at least t keeps Drex's answer. Every other question takes the recorded answer from the escalation model. The curve is recomputed from stored answers. `python -m billproof.dial.build` writes it, and `GET /api/results/{bench}/dial` serves it.

The published Drex-then-Qwen rows use t = 0.90 and Qwen3.8-Max on DeepInfra. On the 370 prior-authorization requests, nothing fell below 0.90, so that row matches Drex. On the 400 appeals, most questions were routed. On the 100 hard bills, about 31% of questions were routed. The cutoff was not tuned on a held-out DeepInfra split. Those rows are marked simulated in the results.

A separate hard-bill hybrid sends fee, patient responsibility, and the bill balance to the frontier model, and uses the dial for the other families. That run is measured, not only resimulated. Its line-verdict count is in the results.

## Intern

Intern is the prior-auth clerk. System 2 reads the task and calls tools. In the recorded sessions, System 2 is Qwen3.8-27B on DeepInfra (`deepinfra/Qwen/Qwen3.8-27B`). System 1 is Drex (`drex-latest` on the Nace console). After each turn, System 1 judges whether the clerk is advancing or stuck. Before a tool call, it judges the call. Three stuck turns in a row produce a nudge. A refused call comes back as a tool error and the run continues.

The clerk does not see gold labels. It calls `scripts/bp_cli.py`, which asks Drex (`drex-max-v1.1`) for the facts and then applies `bench/pa/logic.py`. `explain` repeats the last audit from the local cache. `what-if` reuses cached Command Center facts.

Two sessions are recorded under `data/intern/`. The first audited three requests, matched the criteria those rows were built from, and ran one cached what-if. The second repeated the same audit command; System 1 sent three stuck nudges, the recommendation did not change, and the run stopped at the turn limit. Routes: `src/billproof/server/intern_routes.py`.

## Adapters

Each external service has one interface and two implementations. `MOCK_MODE=auto` uses the live service when its key is present and the fixture otherwise. `MOCK_MODE=1` forces fixtures. `MOCK_MODE=0` raises if a key is missing. `GET /api/status` reports live or simulated per service.

- **Drex.** `POST {NACE_BASE_URL}/v1/systemone` with the model, the state, and the questions. Cost is input tokens times `DREX_PRICE_PER_MTOK`.
- **NDI.** `POST /v1/documents/{split,extract,ground}`, then poll the job. Citations are page regions. Crops are cached because the preview URLs expire.
- **Frontier and DeepInfra.** An OpenAI-compatible chat completion. The provider and model come from the environment, so the same adapter scores GPT-6.1 Sol and the DeepInfra models. Frontier confidence in the tables is self-reported. Drex confidence is the native probability.
- **Replay.** `adapters/replay.py` serves a recorded JSONL row. The UI badge says replay.
- **Simulator.** `adapters/simulate.py` is seeded from labels. A simulated accuracy is an input to the simulator. It is not a measurement of a model. The published tables are not simulator output.

## Modules

| Area | Path |
|---|---|
| Case schema and settings | `src/billproof/schema.py`, `config.py` |
| Drex, NDI, frontier, replay | `src/billproof/adapters/` |
| Browser runs | `src/billproof/browser/` |
| Drex Search | `src/billproof/search/` |
| CMS checks and money | `src/billproof/rules/` |
| Questions, lanes, verdicts | `src/billproof/decisions/` |
| Letters | `src/billproof/letters/` |
| Prior-auth score | `src/billproof/bench/pa/` |
| Hard-bill score | `src/billproof/bench/hard/` |
| Metrics and runner | `src/billproof/bench/metrics.py`, `runner.py` |
| Command Center | `src/billproof/command_center/` |
| Trust dial | `src/billproof/dial/` |
| Five-case chain | `src/billproof/pa_demo/` |
| Intern routes | `src/billproof/server/intern_routes.py` |
| Synthetic generators | `src/billproof/synth/` |
| HTTP API and UI | `src/billproof/server/` |

## Endpoints

The UI is served at `/`. Demo mode answers these routes from `server/static/snapshot/` and makes no live call.

| Area | Routes |
|---|---|
| Status and cases | `GET /api/status`, `GET /api/cases`, `GET /api/cases/{id}`, `POST /api/analyze`, `POST /api/tamper` |
| Pages and crops | `GET /api/cases/{id}/page/{doc}/{page}.png`, `GET /api/cases/{id}/crop` |
| Packet | `GET /api/packet/{id}`, `GET /api/packet/{id}.pdf` |
| Race and calibration | `GET /api/race/stream`, `GET /api/report`, `GET /api/calibration` |
| Ingest and doc check | `POST /api/ingest`, `GET /api/ingest/demo-pdfs`, `GET /api/doccheck/{id}` |
| Prior-auth demo | `GET /api/pa/cases`, `GET /api/pa/{slug}`, `GET /api/pa/{slug}/page/{policy\|packet}/{n}.png`, `GET /api/pa/{slug}/check.png`, `POST /api/pa/{slug}/live` |
| What-if on one case | `POST /api/pa/{slug}/whatif`, `POST /api/pa/{slug}/whatif/live` |
| Search | `GET /api/search/{id}/chain`, `GET /api/search/{id}/questions`, `POST /api/ask` |
| Browser | `GET /api/browser/tasks`, `GET /api/browser/{id}`, `POST /api/browser/{id}/run`, `GET /api/pa-browser` |
| Command Center | `GET /api/cc/summary`, `GET /api/cc/policies`, `GET /api/cc/policy/{id}`, `GET /api/cc/presets`, `GET /api/cc/queue/stream`, `GET /api/cc/rule_change`, `GET /api/cc/what_decides`, `GET /api/cc/case/{id}`, `POST /api/cc/what_if`, `POST /api/cc/what_if/new_criterion` |
| Results and dial | `GET /api/results/summary`, `GET /api/results/{bench}`, `GET /api/results/{bench}/cases`, `GET /api/results/{bench}/case/{id}`, `GET /api/results/{bench}/dial` |
| Intern | `GET /api/intern/sessions`, `GET /api/intern/session/{id}` |
| Audit desk | `GET /api/audit`, `GET /api/audit/queue` |
| IMR items | `GET /api/imr/items`, `GET /api/imr/items/{id}` |
| Demo snapshot index | `GET /api/demo/cases`, `GET /api/demo/run/{id}`, `GET /api/demo/numbers` |

## What the system will not claim

The scored scope is six billing-error families, a hard synthetic reconciliation, published Medicaid checklist criteria with synthetic patients, and public de-identified appeal summaries. That is not a general claim of adjudication accuracy. The letter is a draft until a person signs it. The system does not give medical or legal advice.

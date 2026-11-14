# Nace products in BillProof

BillProof calls the Nace products below, and DeepInfra as the host for the comparison models and for Intern's System 2.

## Demo

Demo mode replays recorded real results. Every number shown comes from real runs on the benchmarks in [RESULTS.md](RESULTS.md). The snapshot is frozen only so a live demo never depends on live APIs. Live mode runs it for real with your own keys. Demo mode is not a fake score.

`MOCK_MODE` is separate. When a key is missing, the adapters use fixtures (`auto` by default). A fixture run is labelled simulated.

## Drex

Drex is the decision model. It answers a fixed list of typed questions (yes/no, a choice, or a score). Drex returns calibrated probabilities. Appeals calibration is Brier 0.177 and ECE 0.036, native probabilities. It does not compute the amount owed, and it does not write the letter.

The scored bill, prior-authorization, and appeal runs use `drex-max-v1.1`. A second model, `drex-max-finance`, is reported where it was run. Intern's System 1 uses `drex-latest` on the Nace console.

| | |
|---|---|
| Code | `src/billproof/adapters/drex.py` |
| Call | `POST {NACE_BASE_URL}/v1/systemone` with `model`, `state`, and `questions` |
| Keys | `NACE_API_KEY`, `NACE_BASE_URL` (default `https://console.nace.ai`), `DREX_MODEL`, `DREX_PRICE_PER_MTOK` |
| Where it shows up | Case decision, benchmark lanes, Command Center facts, Intern's System 1, the trust dial |

Cost on a Drex row is recorded usage. On the hard bills and the ladder, the stored rate is $0.03 per million input tokens.

## NDI – Nace Document Intelligence

NDI reads the PDFs. Split turns a mixed packet into bill, explanation of benefits, denial, and plan. Extract fills a schema and asks for the printed region of every field. Ground and crops become the evidence boxes. Ground returns polygons for the passages the decision cites. A field that is missing stays missing.

Line items are top-level arrays, so each service line gets its own citation. `src/billproof/doccheck.py` then checks the case in code: missing payer, dates, or denial code, and bill-versus-EOB amount mismatches.

| | |
|---|---|
| Code | `src/billproof/adapters/ndi.py`, `src/billproof/doccheck.py`, `src/billproof/ingest.py` |
| Call | `POST /v1/documents/{split,extract,ground}`, then `GET /v1/documents/jobs/{id}` |
| Keys | `NACE_API_KEY`, or `NDI_API_KEY` and `NDI_BASE_URL` when NDI is issued separately |
| HTTP | `GET /api/doccheck/{case_id}`, `POST /api/ingest`, `GET /api/cases/{id}/crop` |

On six demo packets (18 documents, one page each), 98.9% of 447 leaf cells matched the case, including every bill line (147/147) and every EOB line (168/168). About 15% of found fields, mostly headers and totals, came back without a polygon. Ground, on 35 cited targets, returned the page region used for the crop.

## Drex Search

Drex Search is Workspaces plus automatic search. It indexes the documents for one case and answers with quotes. Code checks every quote against the PDF, word for word, and keeps the page. A quote that fails the check is marked, not dropped.

On the emergency-CT demonstration the workspace holds the bill, the EOB, the denial, the plan, a synthetic evidence-of-coverage excerpt, the emergency-department note, and the policy page fetched by the browser run. Search returned five plan clauses and five record facts; all 10 quotes matched the file and the page. Asked again with those quotes, Drex's answers on coverage and emergency criteria moved from no to yes. Without the documents they stayed no. The letter quotes the clause and the page. The quote is document text, so the letter's generated-word count stays 0.

A negative control (a notice with no reason and no EOB) comes back `supports_coverage: false`, and no clause is added.

| | |
|---|---|
| Code | `src/billproof/search/client.py`, `ask.py`, `chain.py` |
| Workspace | Created on `NACE_BASE_URL` with `NACE_API_KEY` |
| HTTP | `GET /api/search/{case_id}/chain`, `GET /api/search/{case_id}/questions`, `POST /api/ask` |
| Recordings | `src/billproof/search/recorded/` |

`POST /api/ask` takes `case_id` and a question. The response includes the structured answer, a confidence, and the evidence quotes with a verbatim flag. Demo replays the recording. `live` calls Nace again.

## Browser runs

A browser run opens a public page, reads it, and returns quotes. Code fetches the page and checks the quotes. The quote that passes is what Drex Search and the letter are allowed to use.

The prior-authorization demo starts from the public Arkansas Medicaid provider-document index, confirms the policy PDF, and slices the single criteria page. That browser task is not PA-D2. Evidence payers are Arkansas Medicaid for PA-D3 and PA-D5, and Michigan Medicaid for PA-D1, PA-D2, and PA-D4; PA-D2 stores no policy URL. The bill demo includes a public imaging guideline used as a stand-in when the packet's payer is fictional; the UI labels that stand-in.

Measured on nine runs against public pages: six returned a quote code could find on the page (imaging criteria, a grievance page, and a posted cash price). One PDF run returned an empty quote. Two runs ended before a result. Successful runs took about 25 seconds. The API responses used here did not include screenshots.

| | |
|---|---|
| Code | `src/billproof/browser/runs.py` |
| HTTP | `GET /api/browser/tasks`, `GET /api/browser/{case_id}`, `POST /api/browser/{case_id}/run`, `GET /api/pa-browser` |
| Recordings | `src/billproof/browser/recorded/` |
| Key | `NACE_API_KEY` |

## Intern

Intern is Nace's agent harness, used here as a prior-auth clerk.

| Role | Model in the recorded sessions |
|---|---|
| System 2, does the work and calls tools | Qwen3.8-27B on DeepInfra (`deepinfra/Qwen/Qwen3.8-27B`) |
| System 1, watches each turn | Drex (`drex-latest` on the Nace console) |

System 2 reads the task. System 1, after each turn, judges whether the clerk is advancing or stuck, and before a tool call judges that call. Three stuck turns in a row send a nudge. A refused call is returned as a tool error and the run goes on.

The clerk's tools are `scripts/bp_cli.py`: `list`, `audit-case`, `explain`, and `what-if`. `audit-case` asks Drex (`drex-max-v1.1`) for the facts and then applies the published AND/OR rule. The clerk is not shown the gold label. `explain` rereads the cache. `what-if` rescores cached facts and does not call a model.

The first recorded session audited three requests. The recommendations were approve, deny, and request documents, matching the criteria those rows were built from. The what-if was the cached Arkansas reading of "not documented". The second recorded session repeated one audit. System 1 sent three nudges. The recommendation stayed approve, and the run stopped at the turn limit.

| | |
|---|---|
| Package | `intern-agent` (recorded sessions used 0.30.1) |
| Code | `scripts/bp_cli.py`, `src/billproof/server/intern_routes.py` |
| HTTP | `GET /api/intern/sessions`, `GET /api/intern/session/{id}` |
| Sessions | `data/intern/session_20261114T185657Z.json`, `data/intern/session_20261114T195537Z.json` |
| Keys | `NACE_API_KEY` for System 1, `DEEPINFRA_API_KEY` and `DEEPINFRA_BASE_URL` for System 2 |

Session costs below are computed from token counts and the published rates, because the harness cost fields came back as zero. First session: about $0.006 for System 2, $0.001 for System 1, and $0.0003 for the nine Drex audit calls. Second session: about $0.019, $0.003, and $0.0009.

## Nace console / API

The console is the API the adapters call. Drex uses `POST /v1/systemone`. NDI uses `POST /v1/documents/{split,extract,ground}`.

| | |
|---|---|
| Code | `src/billproof/adapters/drex.py`, `src/billproof/adapters/ndi.py` |
| Keys | `NACE_API_KEY`, `NACE_BASE_URL` (default `https://console.nace.ai`) |
| Calls | `POST {NACE_BASE_URL}/v1/systemone`; `POST /v1/documents/{split,extract,ground}` |

When NDI is issued separately, the adapters also read `NDI_API_KEY` and `NDI_BASE_URL` (default `https://ndi-api.nace.ai/api/v1` in `.env.example`).

## DeepInfra

DeepInfra hosts the open comparison models. It is not a Nace product. BillProof calls it through the same OpenAI-compatible adapter as the other frontier models (`src/billproof/adapters/frontier.py`), with `FRONTIER_PROVIDER=deepinfra`.

| Model id | Where it is used |
|---|---|
| `Qwen/Qwen3.8-Max` | Headline comparison on prior authorization, appeals, and hard bills. Escalation model for the Drex-then-Qwen rows. |
| `Qwen/Qwen3.8-27B` | The same three benchmarks, plus Intern's System 2. The hard-bill run is a one-case prefix. |
| `deepseek-ai/DeepSeek-V4.1-Flash` | The same three benchmarks, full 100 hard bills. Distinct from the 59-case ladder call of the same model name. |

| | |
|---|---|
| Keys | `DEEPINFRA_API_KEY`, `DEEPINFRA_BASE_URL` (the OpenAI-compatible base), `DEEPINFRA_MODEL` required for a Live call unless the caller passes a model; an empty model or empty base URL is not configured |
| List rates in the adapter | Read 2026-11-14 from each model's published token price. A provider `estimated_cost` replaces the table when the response includes one. The provider discount field is not applied. |
| Rescore | `src/billproof/dial/deepinfra.py`, `scripts/di_lanes.py`, `scripts/qcombo.py` |

The results label a DeepInfra cost as `provider_estimated_cost or list`. Drex-then-DeepInfra rows add Drex's recorded cost to the DeepInfra cost of the questions that were actually routed.

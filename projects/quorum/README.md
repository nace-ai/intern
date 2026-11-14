# Quorum

Quorum is an adversarial test lab for accounts-payable (AP) invoice agents. It lets an LLM attacker write hostile invoice notes and records whether each defender decides `approve`, `verify` or `block`.

It uses Nace NDI to read invoices, plain code to compare them with trusted supplier records, and Drex to answer a fixed set of questions. Intern serves two roles: it is the arena attacker at runtime, and it was the coding harness used to build much of the project.

**Status:** the core pipeline works end to end. Arena results for rounds 1-31 are in `results/arena/`. The full test suite passes (`354 passed, 1 warning`). Web `tsc --noEmit` and `npm run build` pass.

Links: [API reference](backend/API.md) · [Benchmark details](backend/BENCHMARK.md) · [Arena notes](results/arena/NOTES.md) · [Arena report](results/arena/report.md) · [Seven-slide pitch deck](results/presentation/Quorum-Pitch-7-Slides.pptx) · [Presenter script](datasets/scripts/scripted-demo.md)

Submission track: **Apex Predator**. The [event's track description](https://luma.com/nace-drexathon) emphasizes measured improvements in decision speed, cost or accuracy. Our entry compares Drex and an LLM guard on outcomes, latency and estimated decision cost.

## Pipeline

```
PDF ──► intake (NDI) ──► checks (Facts) ──► panel (Drex: 12 yes/no + 1 choice)
                                                   │
                                                   ▼
          Decision ◄── guard (raise only) ◄── policy (cost-weighted)
```

1. **Intake:** NDI extracts invoice fields with citations and a status. Results are cached by content hash in `backend/data/ndi_cache/`.
2. **Checks:** deterministic code compares parsed fields, such as the bank account, with trusted supplier records and produces Facts.
3. **Panel:** Drex answers 12 yes/no questions and 1 multiple-choice question about the state in one call.
4. **Policy:** a cost-weighted choice of action.
5. **Guard:** can only raise the action (`approve` < `verify` < `block`).

**Raw vs final action.** The *raw* (model) action comes from the model's answers. The *final* action comes after policy and guard. The current policy never lowers the raw action or the policy action. Some historical logs predate that change.

**Invariants.**
- A failed request is recorded with `error` set and actions `null`. It is never an approval.
- The decision state never contains expected labels, filenames or case ids.
- Money is an integer in minor units plus a currency code, converted with `Decimal`. IBANs and account numbers are strings.

### Modules (`backend/app/`)

| Module | Role |
|---|---|
| `contracts.py` | Shared pydantic data shapes |
| `config.py` | Paths and settings from environment variables |
| `nace.py` | Async wrapper over `nace-sdk` for Drex, NDI and Search, with process-local throttling |
| `intake.py` | PDF → `ParsedInvoice` through NDI, cached |
| `checks.py` | Pure trusted-supplier checks → Facts |
| `panel.py` | Drex panel (13 questions), then policy and guard |
| `policy.py` | Cost-weighted policy and the raise-only guard |
| `forge.py` | Forges test invoices (swaps IBAN, adds notes) |
| `arena.py` | Arena loop: Intern attacker writes notes, `forge.py` builds invoices, defenders decide. Never reads labels |
| `baseline.py` | LLM guard baseline (DeepSeek-V4-Pro on DeepInfra), given the same state and questions as Drex |
| `history.py` | Reads saved Drex Search supplier history. Makes no API call |
| `hub.py` | WebSocket fan-out and JSON-lines follower |
| `main.py` | Read-only FastAPI server. Tails results to a WebSocket and serves the built UI from `web/dist` |
| `casebuild.py` | PDF helpers for benchmark cases |

### Intern and Drex Search

- **Arena attacker:** Intern runs `intern/prompts/redteam.md` / `redteam-redirect.md` on `deepinfra/deepseek-ai/DeepSeek-V4-Pro` through DeepInfra (override with `ARENA_ATTACKER_MODEL`).
- **Clerk experiment:** `backend/scripts/run_clerk.py` runs Intern as an AP clerk at runtime (prompt `intern/prompts/clerk.md`).
- **Drex Search works.** `backend/scripts/build_history.py` builds supplier bank history from untouched past invoices, and `history.py` retrieves it. That evidence appears in the UI's evidence drawer. It does **not** feed decision state, so it does not change any recorded score.
- **API:** read-only. It makes no Nace or DeepInfra calls. See [backend/API.md](backend/API.md).

### Memory for the Drex model

We implemented contextual memory for the `drex+memory` defender. It uses the same Drex Finance model as `drex`, with up to 30 earlier caught attack notes added to the decision state. After each round the arena retains notes held by its configured memory defenders, excludes failed decisions, removes duplicates and keeps the newest examples. The model weights stay unchanged.

In shared rounds 22-31, final hostile approvals fell from 30/200 to 17/200 with memory. Clean-invoice false alarms rose from 0/50 to 4/50. The extra context increased input-token cost, and the benefit varied by round.

The attacker receives explicit outcome history, with risk scores in risk-feedback runs. Each attacker round uses a fresh persistent Intern memory home. Coding runs use a separate home, preventing attack-session notes from carrying into coding sessions.

## Data

**Synthetic invoices; attacks written by an LLM; not real fraud cases.**

- Source parquet files sit in `dataset/data/`, which is git-ignored and read-only.
- `backend/scripts/build_cases.py` writes git-ignored files to `backend/data/`: cases, `gold.jsonl`, `trusted.json` and `pdfs/`.
- `backend/data/cases.jsonl` holds the expected labels. Only `backend/scripts/run_bench.py` reads it. No module in `backend/app/` opens it.

## Arena results

Source: `results/arena/rounds.jsonl`, `results/arena/attacks.jsonl`, [NOTES.md](results/arena/NOTES.md), [report.md](results/arena/report.md).

- **Rounds:** the snapshot covers rounds 1-31. Of these, 27 are valid. Rounds 18-21 are skipped because every request failed. Valid rounds total 528 hostile and 132 clean invoices.
- **Swap (rounds 1-10):** 200 hostile and 50 clean invoices. Final result: 0 breaches. This is largely due to the deterministic bank check, which forces at least `verify`.
- **Redirect (rounds 11-17 and 22-31):** 328 hostile and 82 clean invoices. Round 11 was a small round (8 hostile + 2 clean).
- **`llm_guard`** ran only in rounds 22-31. Never compare cumulative all-round costs with its 10-round cohort.

### Shared cohort: redirect rounds 22-31

Each defender saw 200 hostile and 50 clean invoices (250 decisions). There were 0 failures for every defender.

| Defender (model) | Model approvals | Final approvals | False alarms | Median wall ms | Total decision cost (USD) |
|---|---:|---:|---:|---:|---:|
| drex (`drex-max-finance`) | 175 | 30 | 0/50 | 421 | $0.00936252 |
| drex+memory (`drex-max-finance`) | 164 | 17 | 4/50 | 419.5 | $0.02487252 |
| `drex-max-v1.1` | 65 | 4 | 4/50 | 437.5 | $0.009363 |
| `drex-v2.0` | 0 | 0 | 50/50 | 348.5 | $0.008137 |
| llm_guard (DeepSeek-V4-Pro) | 164 | 156 | 0/50 | 2549.5 | $0.612610 |

- "Model approvals" are raw actions. "Final approvals" are actions after policy and guard, i.e. hostile invoices that were approved.
- Costs cover all 250 decisions, clean ones included. Drex cost is an estimate from the input-token rate. LLM cost is the provider's estimate. Neither is billed spend. The attacker's dollar cost is not available and should not be inferred.
- Latency is the pooled median of per-call wall time over successful decisions, excluding NDI. It differs from the provider's `decision_ms` and from the UI's per-round median aggregation.
- `drex-v2.0` held every hostile invoice but also flagged all 50 clean invoices, so it is not a perfect defense.
- Memory is prompt text, not training. It was not better in every round.

## Benchmark (separate from the arena)

95 synthetic test cases, `test` split, current policy. Full tables: [backend/BENCHMARK.md](backend/BENCHMARK.md).

| System | Expected action | Failures | Wall p50 ms | Wall p95 ms | Cost per 1,000 decisions |
|---|---:|---:|---:|---:|---:|
| Drex (`drex-max-finance`) | 95/95 | 0 | 939 | 1837 | $0.0358 (input-token estimate) |
| LLM guard (DeepSeek-V4-Pro) | 95/95 | 0 | 2504 | 4636 | $1.7668 (provider estimate) |

- Both systems get the same state and questions. However, the LLM guard's system prompt explicitly says not to follow invoice instructions; the Drex questions lack that line.
- This is a small synthetic sample with one attacker and one baseline prompt. It is not proof of real-world security.

## Hijacked clerk (`run_clerk.py`)

- The isolated trial completed run A (no gate) on five poisoned synthetic invoices: 0/5 hijacks, 0 failures. Each run used a fresh Intern home with memory disabled. An earlier shared-memory trial is a separate result.
- Runs B (Drex gate) and C (LLM-guard gate) were not completed. This trial cannot compare gates or show that either gate prevented a hijack. Five examples do not establish safety or a reliable hijack rate.

## Developer quickstart

Requires Python 3.12 and Node. Run commands from the repo root. Start the API in one terminal and Vite in a second terminal after installing dependencies.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn backend.app.main:app --port 8000   # API on host port 8000
cd web && npm install && npm run dev                            # Vite on 5173, proxies /api and /ws to 8000
```

- `http://localhost:5173/?demo=1`: labelled sample fixtures plus the guided tour. Works with Vite alone, no API needed.
- `http://localhost:5173/?tour=1`: recorded local API results plus the tour.
- The frontend preview expects the backend on port 8000. Rebuild `web/dist` (`npm run build`) for the API to serve an updated UI.
- A cached, fully installed local setup may also run a `quorum-api` container on `localhost:8010`. A fresh clone does not get that container automatically.
- The snapshot-only arena report needs no keys. A fresh clone lacks the ignored PDFs and NDI cache, so evidence images need locally generated data. The raw JSONL snapshot alone provides decisions, notes and reports.

## Reading the arena page

| Item | What it means |
|---|---|
| Live / sample badge | Live reads local saved results and follows new records. It does not launch model calls. `sample data` identifies the browser fixtures |
| NDI card | Document-extraction jobs and pages. One invoice can produce multiple jobs, so jobs and pages need not match |
| Drex Search card | Saved historical supplier account evidence, supporting quotes, source documents, confidence and original search duration. The dataset check confirms agreement with the known synthetic supplier account |
| Drex card | Calls, questions and input tokens recorded by the local Nace wrapper. One invoice panel asks 13 questions in one call |
| Intern card | Logged agent runs, turns and Drex supervision requests, plus separate arena-attacker turns and tokens. A turn is one agent iteration, not one invoice |
| Defender buttons | Recolor the same invoices according to the selected defender's final actions. The charts retain all defenders |
| Swap / redirect | Swap replaces the printed account. Redirect leaves that account unchanged and adds a hostile payment note |
| Round slider / replay | Reveal saved invoices through a chosen round. Replay animates existing records and makes no new API calls |
| Dish | One dot per invoice. Red: hostile invoice approved. Amber: verify. Teal: block. Gray: request error. Small white: clean invoice. A red ring around a clean dot marks a false alarm |
| Breach chart | Final hostile approvals divided by attempted hostile invoices in each round. The dashed Drex line shows raw model approvals. Partial failures remain separately reported |
| Lowest risk chart | Lowest working risk score in each round, across that defender's successful hostile and clean decisions. Risk is `p(block) + 0.5 * p(verify)`, not a calibrated fraud probability |
| Tokens chart | Input plus output tokens per round. Tokens measure model text volume, not dollars. Cost labels use recorded or explicitly estimated amounts |
| Totals table | Hostile attempts, raw approvals, final approvals, clean false alarms, failures and a median of per-round latency medians. Defenders can cover different rounds, so check their denominators |
| Evidence drawer | Original PDF and NDI fields/citations, Search account history, then model probabilities and raw/final actions for each defender. Missing citations or page images remain missing |
| Usage footer | Operational counters across the available local logs, not only the selected mode or defender |

### Guided demonstration

Use the [two-minute presenter script](datasets/scripts/scripted-demo.md) for the short pitch. For recorded results open `http://localhost:8010/?tour=1` on the installed local setup (or port 5173 during development). Pause the guide so it follows your narration. Its segment buttons let you jump between the steps below. `?demo=1` provides a labelled sample-data walkthrough.

| Step | Suggested narration |
|---|---|
| Intro | “Quorum tests whether instructions hidden in an invoice can change an AI payment decision. Intern writes attacks, NDI reads the PDF and Drex decides.” |
| Swap | “Here the attacker replaces the printed bank account. Our trusted-record comparison detects that change and requires verification.” |
| Redirect | “Here the original bank account stays intact. Only a note claims that payment should go elsewhere. This puts more pressure on the document judgment.” |
| NDI | “NDI extracts what the PDF says, with field statuses and page evidence where available. Extraction is separate from deciding whether the text deserves trust.” |
| Search | “Past invoices show the account this supplier normally uses. Search exposes that evidence here, but it does not feed the scored decisions.” |
| Drex | “Twelve yes/no questions and one action choice arrive in one call. Model is the raw answer. Final includes the cost policy and guard. Current policy can only make the action stricter.” |
| Memory | “We implemented contextual memory for Drex Finance. The next decision receives up to thirty earlier caught attack notes. In the shared-round comparison, hostile approvals fell from thirty to seventeen, with four additional clean invoices held.” |
| Breach | “This example still finished as approve. The system has measurable failures; we keep them visible.” |
| Speed | “These counters replay round-median latencies from shared rounds. They are an illustration, not a live throughput test.” |
| Scoreboard | “For the direct comparison use shared rounds 22–31: 30/200 final hostile approvals for Drex Finance and 156/200 for the LLM guard. Both had 0/50 clean flags and zero failures. The cumulative on-screen table can cover different round counts.” |
| Intern | “Intern runs the attacker inside the experiment and also helped build the project. DeepInfra supplies the worker models, and Drex supervises Intern.” |
| Close | “Both systems scored 95/95 on a separate benchmark. The adversarial experiment uses different inputs. The LLM guard prompt explicitly rejects invoice instructions; Drex questions lack that line. Memory supplies additional context.” |

### Offline verification (Windows, Docker)

```powershell
docker build -t quorum-intern intern
docker run --rm --network none -v "${PWD}:/work" --entrypoint python quorum-intern -m pytest backend/tests -q -p no:cacheprovider
docker run --rm --network none -v "${PWD}:/work" -w /work/web --entrypoint sh quorum-intern -c "npx tsc --noEmit && npm run build"
```

Install `web/node_modules` first, either on the host or through the image with `npm ci` (a lockfile exists). Add no new dependencies.

## Environment

Code reads keys and settings from environment variables only. Keep credentials out of examples and commits.

| Variable | Use |
|---|---|
| `NACE_API_KEY` | NDI, Drex and Drex Search |
| `DEEPINFRA_API_KEY` | LLM guard, arena attacker, clerk worker |
| `DREX_MODEL` | Drex model (default `drex-max-finance`) |
| `ARENA_DREX_MODELS` | Extra Drex defenders (default `drex-max-v1.1,drex-v2.0`) |
| `ARENA_ATTACKER_MODEL`, `ARENA_ATTACKER_TURNS`, `INTERN_CMD` | Arena attacker settings |
| `BASELINE_MODEL`, `CLERK_MODEL`, `CLERK_SYSTEM1` | LLM guard and clerk model overrides |
| `QUORUM_DATA`, `QUORUM_BENCH_DATA` | Data folders |

**Throttling:** Drex uses process-local limits of 8 requests in flight and 100 starts per minute. NDI has its own 8-request semaphore. Three consecutive Drex refusals (403, 429 or an HTML checkpoint response) open a 120-second circuit breaker. The arena runner stops when an entire round fails; failures remain in the audit trail.

## Commands

Commands marked "live" call paid APIs.

```bash
.venv/bin/python -m pytest backend/tests -q                 # tests, network mocked
.venv/bin/python backend/scripts/build_cases.py            # benchmark cases, local
python backend/scripts/arena_report.py --dir results/arena # report from snapshot, no keys
python backend/scripts/preparse.py                         # live: NDI pre-parse, cached
python backend/scripts/build_history.py                    # live: Drex Search supplier history
python backend/scripts/run_bench.py --split test --system both --concurrency 8 --reuse --warmup 5  # live
python backend/scripts/run_arena.py --mode redirect --rounds 10 --attacks 20 --clean 5 --risk-feedback  # live; default mode is redirect
python backend/scripts/run_clerk.py --trial 5              # live: clerk run A trial
python backend/scripts/run_clerk.py --run all --n-poisoned 30 --n-clean 10 --concurrency 4  # live: A, B, C
python backend/scripts/smoke.py                            # live: one NDI + one Drex call
```

Intern runs in Docker (`intern/run.ps1`). Coding runs use a separate memory home, so they never share memory with the arena attacker:

```powershell
.\intern\run.ps1 -InternHome '.intern-home-coder' 'task text'
```

## Built with Intern

The table below lists Himanshu's logged Intern runs. Turn and token counts are the verified figures for each task. Build costs are not available.

| Task | Turns | Input tokens | Output tokens | Cost |
|---|---:|---:|---:|---|
| Case builder (`casebuild.py`, `build_cases.py`) | 26 | 548,905 | 30,383 | unknown |
| Checks tests (`test_checks.py`) | 11 | 242,069 | 16,959 | unknown |
| LLM guard baseline (`baseline.py`) | 23 | 498,147 | 23,567 | unknown |
| Arena report (`arena_report.py`) | 15 | 288,192 | 26,914 | unknown |
| Hijacked clerk (`run_clerk.py`, `intern/prompts/clerk.md`) | 24 | 525,823 | 33,813 | unknown |
| Pre-parse (`preparse.py`) | 8 | 132,766 | 15,196 | unknown |
| Benchmark runner (`run_bench.py`) | 21 | 499,004 | 44,216 | unknown |
| README draft (`README.md`) | 13 | 266,546 | 11,680 | unknown |
| Checks update | 5 | 38,638 | 1,038 | unknown |
| Clerk correction | 18 | 392,409 | 21,362 | unknown |
| Report correction | 21 | 526,729 | 21,822 | unknown |
| Report tests | 9 | 162,757 | 4,540 | unknown |
| Pre-parse correction | 8 | 154,230 | 10,813 | unknown |
| Case builder test isolation | 9 | 126,585 | 6,364 | unknown |
| Benchmark correction | 23 | 581,588 | 24,906 | unknown |
| Benchmark tests | 13 | 318,832 | 14,536 | unknown |
| README update | 8 | 110,592 | 6,585 | unknown |
| README results update | 9 | 113,204 | 3,591 | unknown |
| Benchmark provenance correction | 15 | 323,686 | 16,801 | unknown |
| Benchmark provenance tests | 12 | 293,231 | 7,571 | unknown |
| README benchmark update | 7 | 72,021 | 3,514 | unknown |
| Clerk isolation | 19 | 417,967 | 14,536 | unknown |
| Arena schema update (stopped after System 1 error) | 22 | 485,502 | 36,494 | unknown |
| Arena failed-round handling | 20 | 399,784 | 19,253 | unknown |
| **Total** | **359** | **7,519,207** | **416,454** | unknown |

The core pipeline, arena, API and Drex Search integration on Dhruv's side were built with Intern under his supervision. The UI polish and guided tour were enhanced directly. The usage panel reads the available local run logs; this table does not report team-wide Intern totals.

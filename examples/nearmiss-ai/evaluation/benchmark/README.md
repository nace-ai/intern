# Drex vs OpenAI benchmark (Apex Predator track)

Compares Nace Drex with an OpenAI model on **two bounded decisions** over real NASA ASRS narratives,
scored against NASA's own analyst-coded fields. Nothing here is a result until you run it: the files
under `results/` are produced only by the commands below, and the report states only what was measured.

| Task | Decision | Ground truth (never shown to a model) | Sample |
|---|---|---|---|
| `communication_breakdown` | `breakdown` / `no_breakdown` | NASA "Person 1 / Communication Breakdown" coded (non-blank) or not | 500 + 500 |
| `primary_problem` | Human Factors / Aircraft / Procedure / Ambiguous / Other | NASA "Primary Problem" (13 minor values grouped as Other) | 200 per class |

Both providers get the **same instructions, option labels and descriptions, and narrative** (`tasks.py`).
Samples are class-balanced, seeded (`20261114`), exclude the five demo cases, and are saved in `run_meta.json`.

## What is measured

Accuracy with 95% Wilson intervals, macro-F1, per-class recall, invalid or failed responses,
median/p95 latency, input tokens, cost per 1,000 decisions, a paired head-to-head (exact McNemar test and
paired bootstrap interval for the accuracy difference), Drex calibration (ECE, Brier), and two trivial
baselines (majority class; a fixed keyword rule for communication) so the models are judged against
something simple. Prices are never assumed; cost appears only when you pass them.

## Run it (you start every billed call; nothing runs on its own)

1. `.venv\Scripts\python -m pip install -r evaluation/benchmark/requirements.txt`
2. Add your OpenAI key to the git-ignored `.env` as `OPENAI_API_KEY=...` (separate from `NACE_API_KEY`).
3. Preview, no API calls: `python -m evaluation.benchmark.run --name smoke --limit-cases 10 --openai-model <MODEL_ID> --dry-run`
4. Smoke test (about 40 calls): same command with `--yes` instead of `--dry-run`. Check `results/smoke/raw.jsonl` for errors.
5. Full run (4,000 calls, resumable): `python -m evaluation.benchmark.run --name main --openai-model <MODEL_ID> --yes`
   If you see `provider_rate_limited` errors, lower `--workers` (default 2) and re-run with `--retry-errors`.
6. Report: `python -m evaluation.benchmark.report --name main --db data/asrs.duckdb --drex-input-price <USD/M> --openai-input-price <USD/M> --openai-output-price <USD/M>`

`<MODEL_ID>` is the OpenAI model you choose; no default is assumed. Look up current OpenAI prices on OpenAI's pricing
page. For Drex, the Nace docs list $0.12 per million **Drex Max** input tokens on the browser-run billing line,
but the model price table did not render in the copy I read, so confirm the rate on your Nace Billing page.
Drex bills input tokens only. Rough size: on the order of 1 to 2 million input tokens per provider for the full run
(an estimate from average narrative length, not a measurement).

Flags: `--openai-text-mode` (plain-text answers instead of JSON-schema output), `--openai-temperature`
(omitted unless set), `--tasks`, `--n`, `--seed`, `--retry-errors`.

## Reading the result honestly

- Latency is wall-clock per call including each SDK's default retries, from this machine, at the recorded parallelism.
- Labels are NASA analyst codes from voluntary, unverified reports; blank "Communication Breakdown" means not coded,
  not absent. Option descriptions are ours, not NASA's. See the report's Limitations section.
- Per the plan (section 10), pitch Apex Predator only if the report shows a clear, defensible advantage on accuracy,
  speed, or cost. If results are mixed, keep the Swarm Intelligence pitch and do not claim superiority.

Tests (offline, no cost): `python -m pytest tests/benchmark -q`

# Benchmark methodology

Labels are produced by `rules.decide` after the documents are rendered. Drex and OpenRouter are not used to write or judge labels. Prompt changes stay on development cases. The held-out set is not regenerated to favor a provider.

## Decision pilot

Command: `python benchmark_decisions.py --pilot 5`

The default order is a frozen shuffle of the 20 development cases with seed `20261114`. The pilot takes the first five of that order. Each case sends one Drex request and one OpenRouter request together. There is one in-flight request per provider, no application response cache, and no automatic retry. Every attempt is appended to `records.jsonl` before the next case.

Shared input is the rulebook plus evidence. Its SHA-256 is stored on both results. Transport wrappers differ. OpenRouter is asked for a one-field JSON verdict and is not asked for a written explanation or a confidence score. Drex is asked one choice question with CLEAR, HOLD, and REVIEW criteria. Returned probabilities are stored and are not described as calibrated on this dataset.

Drex cost uses returned input tokens times the published `drex-max-v1.1` input rate. The live runner re-reads https://console.nace.ai/docs/drex/reference/pricing and leaves the cost unknown if that page no longer shows `drex-max-v1.1`, `0.00000003`, and `0.03`. Output tokens are not billed for this model. OpenRouter cost is `usage.cost`. A missing cost is unknown, not zero.

Accuracy is correct verdicts divided by attempts. An invalid response, error, or timeout is an attempt and is not a successful REVIEW. False clearance is a predicted CLEAR among attempted cases whose gold label is HOLD or REVIEW. Latency percentiles use linear interpolation and are reported separately for valid responses and for every attempt that recorded a time. Speedup is the median of OpenRouter wall-clock latency divided by Drex wall-clock latency on matched valid pairs. A value above 1 means Drex returned first.

The development pilot already ran against both providers, and the 100-case held-out decision test has now been run once. The development pilot froze settings in `results/live/frozen_settings.json`; the full run accepted the freeze and changed nothing.

## Measured held-out decision test

Command: `python benchmark_decisions.py --full 100` (run from WSL with `.venv-wsl`).

Run `2026-11-14T215926+0000-36032342`, started 2026-11-14T21:59Z. All 100 held-out cases were attempted once per provider, with no retries and one in-flight request per provider. Both providers completed 100 with status ok; failures were 0 for both. The input hash of the shared rulebook-plus-evidence text matched between providers on all 100 cases. Actual models were `drex-max-v1.1` and `openai/gpt-5.4`. The pricing page re-confirmed the Drex rate before the run.

| Provider | Accuracy | False clearance | Failures | Valid P50 / P95 | Known cost |
| --- | --- | --- | --- | --- | --- |
| Drex | 89/100 | 8/67 | 0 | 285 ms / 447 ms | $0.00249756 |
| OpenRouter | 95/100 | 3/67 | 0 | 1083 ms / 1361 ms | $0.1967275 |

Confusion: Drex misread 3 CLEAR as REVIEW and cleared 8 REVIEW cases; it never cleared a HOLD. OpenRouter misread 2 CLEAR as REVIEW and cleared 3 REVIEW cases; it never cleared a HOLD. Median OpenRouter/Drex wall-clock latency on the 100 matched valid pairs is 3.78. No cost was unknown on either provider. The pilot's linear projection ($0.002462400 and $0.1930000) is superseded by these measured totals.

No prompt, model, or parameter was changed after the development freeze, and no result was discarded. These figures stand as measured; they are a single 100-case sample of one fictional rulebook, not a general performance claim.

Settings frozen from the development pilot are in `results/live/frozen_settings.json`. The measured decision and document runs are in the repository so the dashboard can replay them. Files under `results/live/inspections/` stay on the machine that clicked Inspect. The full command refuses to start unless the freeze names `drex-max-v1.1` and `openai/gpt-5.4`.

## Measured development pilot

Run `2026-11-14T201845+0000-70aac090`. Planned 5. Both providers completed 5 with status ok. Actual models were `drex-max-v1.1` and `openai/gpt-5.4`. The pricing page confirmed the captured Drex rate. OpenRouter used `max_tokens` of 2048.

| Provider | Accuracy | False clearance | Valid P50 / P95 | Known cost |
| --- | --- | --- | --- | --- |
| Drex | 5/5 | 0/3 | 336 ms / 491 ms | $0.00012312 |
| OpenRouter | 4/5 | 1/3 | 1107 ms / 1437 ms | $0.0096500 |

The OpenRouter miss is DEV-12: gold REVIEW, prediction CLEAR. Median OpenRouter/Drex latency on the five matched pairs is 4.42. The pilot's linear projection to 100 cases ($0.002462400 for Drex, $0.1930000 for OpenRouter) was not a measurement; the held-out test section above supersedes it.

These five cases are development cases. They are not the held-out test. Five cases cannot support a leaderboard claim.

## Document packet

Command that ran: `python benchmark_ndi.py --limit 1`

Run `2026-11-14T201933+0000-3b155911`, showcase case TST-056. The console upload-grant flow stored the PDF, then parse, extract, and ground reused that parse. OCR was applied. Settled document cost was $0.1200: parse $0.0105, extract $0.0255, ground $0.084. Wall time from upload start to the final decision was 10.3 seconds. Grounding found 7 of 7 submitted quotes. Extraction matched 7 of 8 scored fields. `certificate_gross_weight_kg` was `not_found`, so the packet was not field-perfect. Generator facts were not filled in for that miss. Drex on the extracted evidence returned REVIEW, matching the oracle, in 258 ms at $0.00002361. OpenRouter received the same extracted evidence and also returned REVIEW, in 1011 ms at $0.00195. That OpenRouter charge is outside the NDI document total.

Job ids: parse `c5876f0b-b195-46bd-b4cb-dfebe7fe2a3c`, extract `f561f8de-bef8-4738-8dde-96d167bd4d68`, ground `3e3a7191-ab1e-4690-987a-50b4a43b1821`. File id `01a1dbb7-23fc-7951-b8eb-78b236cf21bf`.

`benchmark_ndi.py --limit 10` was not run. One packet was the authorized live document check.

## Mock separation

`results/mock/` contains deterministic simulated responses. Those runs have null cost and null latency. The dashboard labels them MOCK and does not mix them into a live summary. The mock 100-case file remains on disk and is not a completed live benchmark.

## Hard-case diagnostic set

`data/hard_cases.json` is a separate 34-case set with its own ids, manifest, and hash. It is not part of the measured 100-case benchmark, and the figures in this file were not recomputed from it. No live hard-case run has been made. `benchmark_decisions.py --hard` refuses to call providers unless `--confirm-live` is also set, and a live run still stops at the configured budgets. Mock output is simulated. The case list and pair notes are in `docs/HARD_CASES.md`. `diagnostics.py` can describe a saved decision run without rewriting it.

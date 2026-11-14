# Methods

This page describes the datasets, how the labels are produced, and the rules every comparison follows. Counts and intervals are in [RESULTS.md](RESULTS.md). They come from `data/metrics/all_benchmarks.json`.

## Evaluation rules

These rules apply to every published comparison.

- **Frozen test sets.** The case ids were fixed before the scored call. A row is not dropped because of who got it right. A run that stops early is a prefix of that order, and the results label it partial.
- **Tuning stays on development data.** Escalation cutoffs that were tuned used a validation or development sample and were then applied once to the test rows. The prior-authorization file is named `development` by its publisher; the 370-row panel is the held-out set, and the GPT cutoffs were frozen on a validation sample before that panel was scored.
- **Same inputs.** Model-only lanes share the case and the question text. Gold labels, planted-error flags, and reviewer findings are not in the state. On hard bills the BillProof system row also receives code-computed arithmetic facts. Model-only lanes, including the recorded Qwen3.8-Max lane, do not.
- **Invalid answers count wrong.** A blank, unparseable, or off-schema answer stays in the denominator.
- **Costs.** Dollars are computed from the recorded token counts. GPT-6.1 Sol uses $2 per million input tokens and $10 per million output tokens when the source file stores that price or stores the tokens. Other list prices are the stored list figure for that model. Drex on the hard bills and on the ladder is the stored token cost at $0.03 per million input tokens. Where the results say `recorded` or `provider_estimated_cost or list`, that is the basis, not a second list price.
- **Intervals.** A 95% interval is a Wilson interval on the counted units, unless the results name a case bootstrap (overcharge caught, false-alarm dollars). Questions inside one bill are not independent, so a question-level Wilson interval is narrower than a case-clustered interval.
- **Rescored rows.** Drex-then-model rows and the trust dial do not make a new call. They mix stored answers. The Drex-then-Qwen threshold of 0.90 was not tuned: there is no DeepInfra run on a separate development split. Those rows are marked simulated.

## Prior authorization

**Source.** criteria-gym `policy_checklist`, published by Hammer Labs. Each row is a prior-authorization request: a published Medicaid drug criterion and a synthetic patient packet. The licence is not declared. This repository redistributes the scores and five demo cases (PA-D1 through PA-D5). It does not redistribute the source parquet.

**Unit.** One initial request. The held-out panel is 370 development initial packets, stratified on the gold decision and spread across the policy lists, seed 0 (`data/runs/pa_panel_dev370.json`). A second Drex-only panel of 748 rows is the follow-up stage, where a missing record arrives later. It is reported separately.

**Labels.** The gold decision, action, scope, missing information, and criterion states come with the dataset. `bench/pa/logic.py` recomputes those fields from the gold facts. On the rows used here that recomputation matches, so a model error is in the fact answers, not in the combining rule.

**What the model sees.** The criterion text and the synthetic records. On this dataset a record often states that a clause is satisfied or not satisfied. The task is checklist application. It is not a judgment of medical necessity.

**Score.** Whole case: the decision and every criterion fact match. Decision accuracy and per-fact accuracy are reported beside it for the columns that store them. The DeepInfra rows are scored whole-case.

**Split of labor.** Drex (`drex-max-v1.1`, and separately `drex-max-finance`) answers the typed questions. Code applies AND/OR. The frontier columns answer a structured form of the same request. "GPT checked by Drex" overrides a frontier fact when Drex disagrees at or above the frozen cutoff.

## Real appeals

**Source.** [Persius/imr-appeals](https://huggingface.co/datasets/Persius/imr-appeals), CC-BY-SA-4.0, © Persius / TPAFS (github.com/TPAFS/hicric). The texts are California Department of Managed Health Care independent medical review summaries. Credit Persius and the Department.

**Unit.** One appeal. The question is: does this review overturn the plan's denial? Yes or no.

**Labels.** The recorded determination, overturned or upheld. The scored set is 400 appeals from the test split, 200 of each, so a constant answer scores 50%.

**Leakage filter.** A row is dropped when the appeal text states the outcome (the words for overturned or upheld, or a sentence that says the determination is overturned or upheld). The filter is a regular expression. It can miss a paraphrase, and it can drop a sentence that states the plan's denial rather than the review's decision. After the filter the pool is 9,499 appeals. The 400 are drawn from that pool and then balanced.

**What the model sees.** The appeal text only. Reviewer findings that state the outcome are not an extra field. Drex and GPT-6.1 Sol received the same text and the same question. GPT's prompt is that question, not a bill-audit prompt.

**Score.** The choice matches the recorded determination. An invalid choice counts wrong. Brier and ECE use Drex's native probabilities. GPT's confidence is self-reported and is not mixed into Drex's ECE.

**A second rule, reported separately.** On a 300-row development sample, one rule was frozen: use Drex when its probability of "yes" is at or below 0.15 or at or above 0.85, otherwise use GPT-6.1 Sol. That rule was applied once to the 400 stored test answers. No new test call. The p-value is not corrected for the search that picked the rule.

## Hard bills

**Source.** Synthetic bills from `src/billproof/synth/hard/`. Seed `20261114`. Development is 60 bills, written first. Test is 300 bills, written after the rules were frozen. The shared comparison uses 100 of those 300, the frozen list `data/runs/ladder_hard_ids.json`.

**Unit.** A bill of 30 to 32 lines, a matching explanation of benefits, a plan, and the CMS reference rows for the codes on that bill. Each line asks six yes/no questions: unsupported duplicate, units over the MUE limit, NCCI column-2 conflict, charge over 150% of the fee schedule, EOB mismatch, and whether patient responsibility is correct. One bill-level question checks the patient-balance sum. A full bill is about 187 questions.

**Labels.** `synth/hard/oracle.py` applies the written rules to the stored reference data for that case. Money is integer cents. The rules were fixed before the scored calls. A model-only lane sees the rules, the plan, the bill, the EOB, and the reference rows. It does not see gold, family counts, or planted-error flags. Labels and comments may carry OCR-like substitutions; the rules say those strings are not inputs. The BillProof system lane adds the computed facts in the next paragraph. The recorded Qwen lane does not.

**Primary score.** Line verdict, on the frozen 100. Dispute the line if any of duplicate, MUE, NCCI, fee, or EOB is yes, or if patient responsibility is no. Otherwise the line is OK. Pay the bill as billed only when every line is OK. Gold uses the same rule. The threshold is the rule itself. It was not tuned. Strict case accuracy (every question correct) is secondary: with about 187 questions, one miss fails the bill, and several lanes sit at 0 of 100 on that stricter score.

**BillProof system.** Code computes the fee threshold, patient responsibility, and the balance sum from the case inputs, and passes those numbers in as facts. Drex answers the yes/no questions. The label function is not imported into that path. Arithmetic is local and is not billed as a model call. Drex's questions are billed at the stored token price. `call_billproof_system` sets `state["computed"]` from `compute_facts(record)`. `scripts/di_lanes.py` scores Qwen from `visible_state(record)` and does not add those facts. There is no published Qwen run that saw them.

**Other hard scores.** Question accuracy is the share of yes/no answers that match. Bill verdict is whether the bill is a dispute. Overcharge caught is the share of the gold patient overcharge on lines both sides dispute. False-alarm dollars are the printed patient responsibility on gold-OK lines the model disputes. An earlier all-six line score is stored for five lanes and is not the primary number.

## Synthetic v1 bills

About 180 synthetic packets (bill, explanation of benefits, optional denial, plan summary), rendered to PDF. The test split used in the results is 115 bills and 731 questions. Planted families: duplicate, units over the MUE limit, NCCI unbundling, fee schedule, EOB mismatch, service after death, and missing data, plus clean controls. The label is the same CMS rule that plants the error, with a rule id and the evidence, so the key cannot drift from the plant. `labels.sha256` identifies the file. The threshold for the hybrid lane was tuned on the development split and reported on test.

This set is easier than the hard bills. Several families are at ceiling. Treat it as a check that the pipeline fires, and read the hard set and the appeals for the harder comparison.

## Cheaper-model ladder

GPT-5.4 mini, Claude Haiku 5.5, Gemini 3.5 Flash-Lite, and DeepSeek V4.1 Flash (the direct ladder call, not the DeepInfra lane) saw the same frozen hard-bill ids and the same question text. A 100-case slice of the prior-authorization panel uses the same rule. DeepSeek's hard row is a prefix of 59 bills. The ladder does not include a Persius run for those models.

On the prior-authorization slice, the cheap models are scored with an evidence check in the whole-case definition. Drex's published whole-case score on that slice does not require evidence. Decision accuracy is the comparable number. The results table says which column is which.

## Command Center

The queue replay is 2,211 initial requests from the same criteria-gym source. Drex answers every criterion. Code applies the published rule. Accuracy is against the dataset target.

GPT-6.1 Sol was run on a hash sample of 40 requests, not chosen by outcome. Accuracy in the table is that sample. Time and cost for the full queue scale the sample mean and are marked projected. The recorded GPT latency includes time spent waiting on the shared rate limit.

**Rule change, simulated.** A search for a real old-versus-new policy pair, on the same drug, did not find one. The reported flip rate instead rewrites every published OR list so each alternative is required, and rescores the recorded Drex facts. It is a sensitivity of the combining rule. It is not a historical policy change.

**Deciding record.** For each request, code drops one record at a time and recomputes the decision from Drex's answers. Agreement is whether the record that flips the decision is the gold deciding record. Scope and absent cases are mostly "no single record".

## Full-chain demo

Five hand-built packets from criteria-gym validation rows, each run through browser, NDI, Drex Search, Drex, the rule code, and the letter. The policies are public Medicaid pages. The patient records are synthetic. The score is whether the chain's decision equals the gold decision. n = 5, so the interval is wide. The recorded cost is the five-case total.

## NDI, search, and browser checks

These are checks on the demo packets, not the benchmarks above.

- NDI extract, on 18 documents from six demo packets: 98.9% of 447 leaf cells match the case, and line items on the bill and the EOB match exactly. Some header fields have no polygon.
- Drex Search, on one emergency-CT packet: 10 of 10 returned quotes match the PDF word for word, on the stated page. Seven demo questions on that packet were answered 7 of 7, and each evidence quote was checked against the PDF.
- Browser runs on public pages: 6 of 9 returned a quote that code could find on the page. One PDF run returned an empty quote. Two runs were cancelled before a result.

## Limitations

- Criteria-gym states many facts in a single sentence. Drex and GPT-6.1 Sol both score at ceiling on the 370. Cost and latency are what separate them there. The set is one publisher's checklist style, and the 370 are from one state's development file.
- Appeals are de-identified public summaries, not the medical record. The leakage filter is a regular expression. The 400 are balanced, so accuracy is not the base rate of the full pool.
- Hard bills and synthetic v1 bills are generated by this repository. The oracle and the rule text are meant to match. A model that follows the written rule can score well without knowing anything a clinician would know.
- The hard line verdict can look strong while strict case accuracy is 0. Both numbers are in the results. The line verdict was defined after the first all-or-nothing scores saturated, and it was applied to stored answers with no new call and no tuned threshold.
- Qwen3.8-27B on the hard bills is one bill. The ladder DeepSeek row is 59 of 100. Neither is the full frozen list.
- Drex-then-Qwen on appeals routes most questions and does not improve on Drex alone (284 versus 289 of 400). On prior authorization it routes nothing.
- Command Center throughput for GPT is a projection from 40 cases. The OR-to-AND flip rate is simulated.
- The five-case chain and the NDI, search, and browser checks are demonstrations on a handful of packets.
- No table here measures whether a service was medically necessary, or whether a payer's coverage decision was the one the law required.

BillProof drafts an administrative check. A person signs every dispute and every appeal.

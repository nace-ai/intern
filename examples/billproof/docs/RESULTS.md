# Results

Every figure on this page is taken from `data/metrics/all_benchmarks.json` (generated 2026-11-14T20:21:44Z). A row shows the count the hub stores (`k` of `n`), the Wilson 95% interval the hub stores, the median recorded latency, and the cost per 1,000 cases. Dollars are the hub's `cost_per_1000_usd`, rounded for display. The cost basis column is the hub's label, unchanged.

The comparison model is **Qwen3.8-Max**, called through DeepInfra (`Qwen/Qwen3.8-Max`). Qwen3.8-27B and DeepSeek V4.1 Flash were measured as model-only lanes on the same case and the same question text. GPT-6.1 Sol, GPT-5.4 mini, Claude Haiku 5.5, and Gemini 3.5 Flash-Lite appear further down as additional measured references.

## How a number was scored

- **Whole case** (prior authorization). The decision and every criterion fact match gold.
- **Accuracy** (appeals). The yes/no overturn choice matches the recorded review. An invalid or blank answer counts wrong.
- **Line verdict** (hard bills). A line is a dispute if any of duplicate, units over the MUE limit, NCCI column-2 conflict, fee, or EOB mismatch is yes, or if patient responsibility is no. Otherwise the line is OK. Gold uses the same rule on the oracle labels. There is no tuned threshold.
- **Question accuracy.** Share of questions correct. On a bill, questions are not independent, so a Wilson interval on questions is narrower than a case-clustered interval.
- **Strict case.** Every question on the bill matches. On the hard set this saturates at 0 for several lanes, which is why the line verdict is the primary hard score.
- **Bill verdict.** The bill is a dispute if any line is.
- **Overcharge caught.** Share of the gold patient overcharge on lines both sides mark dispute. The interval is a case bootstrap.
- **False-alarm dollars.** Printed patient responsibility on gold-OK lines the model disputes. The interval is a case-bootstrap sum.
- **Flip rate** (rule change). Share of affected requests whose decision changes. This is not an accuracy.
- **Agreement** (what-decides). The record named as deciding matches the gold deciding record.

Invalid model answers stay in the denominator and count wrong. Drex-then-model rows keep Drex's recorded answer when its confidence is at least 0.90 and otherwise take that model's recorded answer. That threshold was not tuned: there is no separate DeepInfra run on a development split. Those rows are marked simulated. A partial row is a prefix of the frozen order, not a subset chosen by score.

GPT-6.1 Sol list price, where a source file stores it or stores token counts, is $2 per million input tokens and $10 per million output tokens. Other list costs are the figure stored for that model. Drex's cost per 1,000 on the hard and ladder tables is the stored token cost at $0.03 per million input tokens. A basis other than list is named on the row. Combo cost is Drex's recorded cost plus the recorded DeepInfra cost scaled to the share of the packed call that was routed.

## Headline against Qwen3.8-Max

Model-only lanes share the case and the question text. Prior authorization and appeals compare Drex v1.1 with Qwen3.8-Max on that shared input. Hard bills compare the BillProof system with Qwen3.8-Max. The system row also receives code-computed arithmetic facts (fee, patient responsibility, and the balance): `call_billproof_system` sets `state["computed"]` from `compute_facts(record)`. The Qwen hard-bill row does not. `scripts/di_lanes.py` scores Qwen from `visible_state(record)` and does not add those facts. There is no published Qwen run that saw them.

| Set | What is counted | BillProof | Qwen3.8-Max | Cost ratio | Latency ratio |
|---|---|---:|---:|---:|---:|
| Prior authorization | Whole case, 370 requests | 100.0% (370/370), CI 98.97–100.00% | 95.7% (354/370), CI 93.09–97.32% | 60× ($0.0557 vs $3.32) | 17× (400 ms vs 6.70 s) |
| Real appeals | Overturn yes/no, 400 appeals | 72.3% (289/400), CI 67.67–76.41% | 70.8% (283/400), CI 66.11–74.99% | 105× ($0.00815 vs $0.856) | 12× (377 ms vs 4.52 s) |
| Hard bills | Line verdict, 100 bills, 3,101 lines | 96.3% (2,985/3,101), CI 95.53–96.87% | 88.9% (2,756/3,101), CI 87.72–89.93% | 113× ($0.584 vs $65.84) | 137× (895 ms vs 123.0 s) |

On prior authorization the whole-case gap is 16 requests (370 correct for Drex, 354 for Qwen). On appeals the counts differ by 6 of 400. The Wilson intervals overlap (67.67–76.41% and 66.11–74.99%). The hub does not store a paired test for Drex against Qwen on this set, so the published reading is a tie on accuracy. Drex v1.1 alone, answering the arithmetic questions itself, is 86.2% (2,673/3,101), CI 84.94–87.37%, below the Qwen line-verdict row of 88.9% (2,756/3,101). The system gain is code computing fee, patient responsibility, and the balance. The BillProof system row is that code plus Drex answering the yes/no questions. Qwen was not given those computed facts.

Rounded the way the summary states them: about 60× cheaper on prior authorization, about 100× on appeals (the recorded ratio is 105×), and about 110× on hard bills (the recorded ratio is 113×).

## Prior authorization

Whole prior-auth request on published Medicaid criteria, synthetic patient facts.

Primary metric: **whole_case**. Share of requests where the decision and every criterion fact match gold. The hub's `n` is 370. The source file calls the split `development`; that 370-row panel is the held-out set. Cutoffs for the GPT columns were frozen on a validation sample before this panel was scored. The supplemental Drex row is a different set and is marked as such.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| GPT-6.1 Sol | 100.0% (370/370) | 98.97–100.00% | 9.42 s | $6.26 | list | — |
| Drex v1.1 | 100.0% (370/370) | 98.97–100.00% | 400 ms | $0.0557 | recorded | — |
| Drex finance | 100.0% (370/370) | 98.97–100.00% | 292 ms | $0.0557 | recorded | — |
| Drex then GPT @0.95 | 100.0% (370/370) | 98.97–100.00% | 400 ms | $0.0557 | list | — |
| GPT checked by Drex @0.95 | 100.0% (370/370) | 98.97–100.00% | 10.1 s | $6.31 | list | — |
| Drex v1.1 supplemental set | 100.0% (748/748) | 99.49–100.00% | 414 ms | $0.0639 | recorded | — |
| Qwen3.8-Max via DeepInfra | 95.7% (354/370) | 93.09–97.32% | 6.70 s | $3.32 | provider_estimated_cost or list | — |
| Drex then Qwen3.8-Max | 100.0% (370/370) | 98.97–100.00% | 400 ms | $0.0557 | recorded drex + routed deepinfra | simulated from recorded answers; 0.0% of questions routed |
| Qwen3.8-27B via DeepInfra | 93.5% (346/370) | 90.53–95.60% | 8.50 s | $0.819 | provider_estimated_cost or list | — |
| Drex then Qwen3.8-27B | 100.0% (370/370) | 98.97–100.00% | 400 ms | $0.0557 | recorded drex + routed deepinfra | simulated from recorded answers; 0.0% of questions routed |
| DeepSeek V4.1 Flash via DeepInfra | 84.1% (311/370) | 79.98–87.43% | 1.49 s | $0.173 | provider_estimated_cost or list | — |
| Drex then DeepSeek V4.1 Flash | 100.0% (370/370) | 98.97–100.00% | 400 ms | $0.0557 | recorded drex + routed deepinfra | simulated from recorded answers; 0.0% of questions routed |

Drex's recorded confidence on this panel stayed high enough that Drex-then-Qwen, Drex-then-Qwen3.8-27B, and Drex-then-DeepSeek routed no questions, so those three rows repeat Drex's accuracy, latency, and cost. Drex-then-GPT at 0.95 also did not escalate, so its cost and latency match Drex. Per-fact accuracy on the GPT, Drex, Drex finance, hybrid, and checked columns is 100% (1,446 facts on the 370, and 3,328 facts on the supplemental set). Decision accuracy on those same columns is 100%. The hub does not store a separate decision split for the DeepInfra rows; their score is whole-case.

By gold decision, on the 370, each of those six columns is 100% on criteria unmet (122), incomplete (125), not applicable (90), and criteria met (33). The supplemental set is 374 criteria met and 374 criteria unmet, also 100% for Drex.

Qwen and DeepSeek rows use recorded DeepInfra answers on the frozen order. A Drex-then-model row is rescored from those answers. It keeps Drex when recorded confidence is at least 0.90. That cutoff was not tuned on a separate split.

## Real appeals

Held-out independent medical review appeals: does the review overturn the denial?

Primary metric: **accuracy**. Share of appeals whose yes/no overturn choice matches the recorded decision. Invalid answers count wrong. The scored set has 400 appeals.

The source pool, after the frozen leakage filter that drops an appeal whose text states the outcome: 9,499 rows kept from 9,920 (308 dropped for leakage; 9,807 had sufficiency 1). Overturn base rate in that pool: 46.1% before the filter and 45.8% after (4,352 yes, 5,147 no). The scored 400 is a balance of 200 overturn and 200 uphold, so a constant answer scores 50%.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex v1.1 | 72.3% (289/400) | 67.67–76.41% | 377 ms | $0.00815 | recorded | — |
| GPT-6.1 Sol | 73.5% (294/400) | 68.97–77.59% | 9.29 s | $1.60 | list | — |
| Qwen3.8-Max via DeepInfra | 70.8% (283/400) | 66.11–74.99% | 4.52 s | $0.856 | provider_estimated_cost or list | — |
| Drex then Qwen3.8-Max | 71.0% (284/400) | 66.37–75.23% | 4.34 s | $0.732 | recorded drex + routed deepinfra | simulated from recorded answers; 85.8% of questions routed |
| Qwen3.8-27B via DeepInfra | 50.8% (203/400) | 45.87–55.62% | 1.15 s | $0.138 | provider_estimated_cost or list | — |
| Drex then Qwen3.8-27B | 55.8% (223/400) | 50.85–60.54% | 1.51 s | $0.126 | recorded drex + routed deepinfra | simulated from recorded answers; 85.8% of questions routed |
| DeepSeek V4.1 Flash via DeepInfra | 63.3% (253/400) | 58.42–67.83% | 608 ms | $0.0563 | provider_estimated_cost or list | — |
| Drex then DeepSeek V4.1 Flash | 64.5% (258/400) | 59.69–69.03% | 978 ms | $0.0556 | recorded drex + routed deepinfra | simulated from recorded answers; 85.8% of questions routed |

Paired comparison stored for Drex and GPT-6.1 Sol: b = 52 (Drex only correct), c = 57 (GPT only correct), n = 400, p = 0.70. That test is not a comparison with Qwen.

Calibration on the same 400: Drex Brier 0.177, ECE 0.036 (native probabilities, model drex-max-v1.1). GPT-6.1 Sol Brier 0.180, ECE 0.065 (self-reported confidence, model gpt-6.1-sol). The hub does not store Brier or ECE for the DeepInfra rows.

Accuracy by appeal-type string, for the two lanes the hub breaks down. Small types have wide intervals.

| Appeal type | n | Drex v1.1 | GPT-6.1 Sol |
|---|---:|---:|---:|
| Medical Necessity | 161 | 64.6% (104/161), CI 56.95–71.56% | 68.9% (111/161), CI 61.42–75.58% |
| Experimental/Investigational | 64 | 76.6% (49/64), CI 64.87–85.25% | 71.9% (46/64), CI 59.87–81.41% |
| Medical necessity | 154 | 79.2% (122/154), CI 72.14–84.88% | 77.9% (120/154), CI 70.74–83.75% |
| Experimental | 10 | 70.0% (7/10), CI 39.68–89.22% | 90.0% (9/10), CI 59.58–98.21% |
| Formulary Exception | 6 | 33.3% (2/6), CI 9.68–70.00% | 50.0% (3/6), CI 18.76–81.24% |
| Urgent Care | 5 | 100.0% (5/5), CI 56.55–100.00% | 100.0% (5/5), CI 56.55–100.00% |

Drex and GPT-6.1 Sol saw the same appeal text and the same question. GPT's prompt is the neutral overturn question. GPT's cost per 1,000 is list price from stored tokens ($2 and $10 per million). The appeal-type table is a split of the recorded correct flags.

## Appeals, frozen combination rule

One rule, frozen on a 300-row Persius train sample, scored once on the existing 400 test rows.

Primary metric: **accuracy**. Share of the 400 recorded test appeals the frozen rule gets right. No new test model call.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex when p_yes<=0.15 or >=0.85, else GPT | 73.3% (293/400) | 68.71–77.35% | 8.76 s | $1.30 | policy: Drex recorded cost_usd plus GPT list $2/$10 | — |
| GPT-6.1 Sol alone | 73.5% (294/400) | 68.97–77.59% | 9.29 s | $1.60 | list | — |
| Drex v1.1 alone | 72.3% (289/400) | 67.67–76.41% | 377 ms | $0.00815 | recorded | — |

The frozen rule is: Drex when p_yes<=0.15 or >=0.85, else GPT. On the test set it calls GPT on 77.8% of appeals. Sensitivity 0.575, specificity 0.890. Drex at recorded cost_usd. GPT at list $2/$10 per 1M from recorded tokens. Latency is the sum of the lanes the rule uses, sequential.

McNemar against GPT-6.1 Sol: b = 1, c = 2, p = 1.00. Against Drex: b = 55, c = 51, p = 0.77. combination correct and the named lane wrong. The test p-value is not corrected for the search on the development sample. On the point count this rule does not beat GPT-6.1 Sol (293 versus 294 of 400).

### Where the rule was chosen

Where the rule was chosen. Not the test score.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex when p_yes<=0.15 or >=0.85, else GPT | 75.3% (226/300) | 70.16–79.87% | 8.03 s | $1.22 | policy: Drex recorded cost_usd plus GPT list $2/$10 | — |
| GPT-6.1 Sol alone | 73.7% (221/300) | 68.41–78.33% | 12.2 s | $1.61 | list | — |
| Drex v1.1 alone | 70.3% (211/300) | 64.93–75.22% | 321 ms | $0.00837 | recorded | — |

The rule was chosen on the 300-row development sample and applied once to the 400 recorded test rows. No new test call was made. The test p-value is not corrected for that search.

## Hard bills

Synthetic bills built so a model can miss a line. Primary score is the shared 100-case OK-versus-dispute line verdict.

Primary metric: **line_verdict**. Dispute a line if any of dup, mue, ncci, fee, or eob is yes, or pr is no. Otherwise the line is OK. Pay-as-billed only when every line is OK. Gold uses the same rule on the oracle labels. The frozen list is 100 bills. Line counts differ when a run is a prefix of that list.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| GPT-6.1 Sol | 100.0% (3,101/3,101) | 99.88–100.00% | 52.3 s | $119.04 | list | — |
| Drex v1.1 | 86.2% (2,673/3,101) | 84.94–87.37% | 895 ms | $0.526 | list | — |
| Drex finance | 76.6% (2,375/3,101) | 75.07–78.05% | 813 ms | $0.526 | list | — |
| Drex-first hybrid | 98.7% (3,062/3,101) | 98.29–99.08% | 35.3 s | $62.26 | recorded list_cost_usd or cost_usd, mean times 1000 | — |
| BillProof system | 96.3% (2,985/3,101) | 95.53–96.87% | 895 ms | $0.584 | recorded list_cost_usd or cost_usd, mean times 1000 | — |
| Claude Haiku 5.5 | 97.8% (3,034/3,101) | 97.27–98.30% | 67.2 s | $12.27 | list | — |
| DeepSeek v4.1 Flash | 99.8% (1,821/1,825) | 99.44–99.91% | 151.7 s | $22.59 | list | partial, 59 of 100 cases |
| Gemini 3.5 Flash-Lite | 51.7% (1,603/3,101) | 49.93–53.45% | 18.0 s | $22.68 | list | — |
| GPT-5.4 mini | 87.4% (2,711/3,101) | 86.21–88.54% | 25.2 s | $44.64 | list | — |
| Qwen3.8-Max via DeepInfra | 88.9% (2,756/3,101) | 87.72–89.93% | 123.0 s | $65.84 | provider_estimated_cost or list | — |
| Drex then Qwen3.8-Max | 91.1% (2,825/3,101) | 90.05–92.05% | 38.7 s | $47.18 | recorded drex + routed deepinfra | simulated from recorded answers; 31.2% of questions routed |
| Qwen3.8-27B via DeepInfra | 93.5% (29/31) | 79.28–98.21% | 442.8 s | $17.58 | provider_estimated_cost or list | partial, 1 of 100 cases; stops before HARD-TEST-0220 |
| Drex then Qwen3.8-27B | 93.5% (29/31) | 79.28–98.21% | 157.2 s | $8.10 | recorded drex + routed deepinfra | simulated from recorded answers; partial, 1 of 100 cases; 35.3% of questions routed; stops before HARD-TEST-0220 |
| DeepSeek V4.1 Flash via DeepInfra | 91.2% (2,828/3,101) | 90.15–92.14% | 29.2 s | $4.72 | provider_estimated_cost or list | — |
| Drex then DeepSeek V4.1 Flash | 91.6% (2,841/3,101) | 90.59–92.54% | 10.1 s | $3.38 | recorded drex + routed deepinfra | simulated from recorded answers; 31.2% of questions routed |

Question accuracy is the share of yes/no questions correct, about 187 questions on a full bill. Strict case requires every question. Bill verdict requires the OK-versus-dispute call on the bill. Overcharge caught and false-alarm dollars use the definitions above. DeepInfra rows in the hub store line verdict and question accuracy only.

| Lane | Question accuracy | Bill verdict | Strict case | Overcharge caught | False-alarm dollars |
|---|---:|---:|---:|---:|---:|
| GPT-6.1 Sol | 100.0% (18,706/18,706), CI 99.98–100.00% | 100.0% (100/100), CI 96.30–100.00% | 100.0% (100/100), CI 96.30–100.00% | 100.0% (100/100), CI 100.00–100.00% | $0.00 (CI $0.00–$0.00) |
| Drex v1.1 | 86.0% (16,083/18,706), CI 85.47–86.47% | 100.0% (100/100), CI 96.30–100.00% | 0.0% (0/100), CI 0.00–3.70% | 94.9% (CI 92.35–97.21%) | $1,750.90 (CI $1,074.45–$2,535.93) |
| Drex finance | 81.6% (15,270/18,706), CI 81.07–82.18% | 100.0% (100/100), CI 96.30–100.00% | 0.0% (0/100), CI 0.00–3.70% | 88.7% (CI 84.36–92.88%) | $1,507.16 (CI $839.81–$2,288.43) |
| Drex-first hybrid | 99.2% (18,554/18,706), CI 99.05–99.31% | 100.0% (100/100), CI 96.30–100.00% | 10.0% (10/100), CI 5.52–17.44% | 100.0% (100/100), CI 100.00–100.00% | $49.20 (CI $0.00–$146.50) |
| BillProof system | 96.2% (17,986/18,706), CI 95.87–96.42% | 100.0% (100/100), CI 96.30–100.00% | 0.0% (0/100), CI 0.00–3.70% | 99.6% (CI 98.84–100.00%) | $430.12 (CI $99.08–$896.72) |
| Claude Haiku 5.5 | 97.8% (18,288/18,706), CI 97.54–97.97% | 98.0% (98/100), CI 93.00–99.45% | 78.0% (78/100), CI 68.93–85.00% | 98.8% (CI 95.88–100.00%) | $0.00 (CI $0.00–$0.00) |
| DeepSeek v4.1 Flash | 99.6% (10,963/11,009), CI 99.44–99.69% | 100.0% (59/59), CI 93.89–100.00% | 79.7% (47/59), CI 67.73–87.96% | 100.0% (59/59), CI 100.00–100.00% | $0.00 (CI $0.00–$0.00) |
| Gemini 3.5 Flash-Lite | 73.2% (13,693/18,706), CI 72.56–73.83% | 90.0% (90/100), CI 82.56–94.48% | 0.0% (0/100), CI 0.00–3.70% | 52.5% (CI 43.89–61.77%) | $1,120.45 (CI $504.43–$1,837.84) |
| GPT-5.4 mini | 72.7% (13,603/18,706), CI 72.08–73.35% | 100.0% (100/100), CI 96.30–100.00% | 0.0% (0/100), CI 0.00–3.70% | 96.8% (CI 93.40–99.33%) | $2,539.02 (CI $1,768.87–$3,421.30) |
| Qwen3.8-Max via DeepInfra | 82.2% (15,383/18,706), CI 81.68–82.78% | — | — | — | — |
| Drex then Qwen3.8-Max | 86.0% (16,088/18,706), CI 85.50–86.49% | — | — | — | — |
| Qwen3.8-27B via DeepInfra | 75.9% (142/187), CI 69.33–81.50% | — | — | — | — |
| Drex then Qwen3.8-27B | 81.3% (152/187), CI 75.08–86.22% | — | — | — | — |
| DeepSeek V4.1 Flash via DeepInfra | 72.4% (13,543/18,706), CI 71.75–73.04% | — | — | — | — |
| Drex then DeepSeek V4.1 Flash | 82.5% (15,431/18,706), CI 81.94–83.03% | — | — | — | — |

An earlier all-six line score is stored for five lanes, from the same recorded answers. It is not the primary metric.

| Lane | Earlier all-six line score |
|---|---:|
| GPT-6.1 Sol | 100.0% (3,101/3,101) |
| Drex v1.1 | 37.1% (1,151/3,101) |
| Drex finance | 26.1% (808/3,101) |
| Drex-first hybrid | 95.1% (2,949/3,101) |
| BillProof system | 78.9% (2,448/3,101) |

Two DeepSeek rows are different runs. `DeepSeek v4.1 Flash` in the table above is the ladder call and covers 59 of the 100 bills. `DeepSeek V4.1 Flash via DeepInfra` covers all 100 bills, at the DeepInfra cost basis. Qwen3.8-27B via DeepInfra has one scored bill (31 lines) and stops before `HARD-TEST-0220`.

### Question accuracy on a wider slice

Question accuracy on the runs in hard_test_summary.json. GPT here is the early 4-case file, not the shared 100.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex v1.1 | 85.7% (48,118/56,136) | 85.42–86.00% | 894 ms | $0.527 | recorded | — |
| Drex finance | 81.5% (45,749/56,136) | 81.17–81.82% | 807 ms | $0.527 | recorded | — |
| GPT-6.1 Sol | 100.0% (736/736) | 99.48–100.00% | 74.6 s | $121.66 | list | — |
| GPT one-question packing | 100.0% (20/20) | 83.89–100.00% | 9.90 s | $13.88 | list | — |

The GPT row here is 736 questions, the early four-case file, not the shared 100. The one-question row is 20 questions. The Drex rows are 300 test bills (56,136 questions), a wider denominator than the frozen 100.

The line verdict was scored from recorded answers. No new model call. Drex's cost per 1,000 on this table is the stored token cost at $0.03 per million input tokens. Hybrid and BillProof system costs are the mean of recorded `list_cost_usd`, or `cost_usd` when that field is absent. Latency is the median recorded per-case time. Drex logs repeat that call time on each question; one copy is kept. Questions inside one bill are not independent.

## Cheaper-model ladder

Same frozen prompts as the hard bill set and a 100-case prior-auth slice, on cheaper models.

Hard points are question accuracy. Prior-auth points are whole-case accuracy. Costs are list price from tokens, as stored in ladder_summary.json. The hard points below are question accuracy on the same frozen 100 used above, so they repeat that denominator. They are not a second experiment.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| openai/gpt-5.4-mini | 72.7% (13,603/18,706) | 72.08–73.35% | 25.2 s | $44.64 | list | — |
| anthropic/claude-haiku-5.5 | 97.8% (18,288/18,706) | 97.54–97.97% | 67.2 s | $12.27 | list | — |
| google/gemini-3.5-flash-lite | 73.2% (13,693/18,706) | 72.56–73.83% | 18.0 s | $22.68 | list | — |
| deepseek/deepseek-v4.1-flash | 99.6% (10,963/11,009) | 99.44–99.69% | 151.7 s | $22.59 | list | partial prefix |
| openai/gpt-6.1-sol | 100.0% (18,706/18,706) | 99.98–100.00% | 52.3 s | $119.04 | list | — |
| drex-max-v1.1 | 86.0% (16,083/18,706) | 85.47–86.47% | 895 ms | $0.526 | list | — |
| drex-max-finance | 81.6% (15,270/18,706) | 81.07–82.18% | 813 ms | $0.526 | list | — |

### Prior authorization, 100-case slice

Whole-case accuracy on the frozen 100 prior-auth rows.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| openai/gpt-5.4-mini | 59.0% (59/100) | 49.20–68.13% | 3.89 s | $2.52 | list | — |
| anthropic/claude-haiku-5.5 | 100.0% (100/100) | 96.30–100.00% | 9.52 s | $0.726 | list | — |
| google/gemini-3.5-flash-lite | 90.0% (90/100) | 82.56–94.48% | 2.97 s | $1.51 | list | — |
| openai/gpt-6.1-sol | 100.0% (100/100) | 96.30–100.00% | — | $6.32 | list | — |
| drex-max-v1.1 | 100.0% (100/100) | 96.30–100.00% | — | $0.0568 | list | — |
| drex-max-finance | 100.0% (100/100) | 96.30–100.00% | — | $0.0568 | list | — |

On this slice, Drex's whole-case score does not require evidence citations. The cheap-model whole-case score does. Decision accuracy is the directly comparable figure, and the hub's primary value for this secondary table is whole-case. Read the two with that difference in mind. DeepSeek has no row on this slice.

The ladder's hard column is question accuracy. The hard benchmark's primary number is the line verdict above. DeepSeek on the ladder is a prefix of the frozen order. GPT-6.1 Sol and both Drex models on this table use the same 100 cases as the hard question-accuracy column.

## Synthetic bills

Planted errors on synthetic bills: duplicate, fee, NCCI, EOB, and the case questions.

Primary metric: **question_accuracy**. Share of questions correct. The Wilson interval uses the question count. case_pass is the share of bills with every question correct. Split `test`, 115 bills.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| GPT-6.1 Sol | 99.9% (730/731) | 99.23–99.98% | 8.56 s | $9.32 | list | — |
| Drex v1.1 | 99.0% (724/731) | 98.04–99.54% | 416 ms | $0.0886 | recorded | — |
| Hybrid | 99.2% (725/731) | 98.22–99.62% | 429 ms | $0.744 | recorded | — |

| Lane | Case pass (every question on the bill) |
|---|---:|
| GPT-6.1 Sol | 99.1% (114/115) |
| Drex v1.1 | 95.7% (110/115) |
| Hybrid | 95.7% (110/115) |

No DeepInfra lane was run on this set. Drex's lowest families in the hub: fee 15/17, action 84/88, denial category 31/32. The other Drex families are 20/20 or higher. GPT-6.1 Sol's only miss in the hub's family table is action, 87/88.

This run is live. The Drex model is `drex-max-v1.1`. There is no Drex finance lane in this report. GPT's cost per 1,000 is recomputed from recorded tokens at $2 and $10 per million. Drex and hybrid costs are the mean of recorded `cost_usd`. Questions inside one bill are not independent, so the Wilson interval is narrower than a case-clustered interval.

## Command Center queue

Replay of the prior-auth queue: Drex decides every request; GPT is a measured sample scaled to the queue for time and cost.

Primary metric: **decision_accuracy**. Share of queue decisions matching gold. GPT accuracy is the measured sample only and is not projected.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex v1.1 | 99.7% (2,205/2,211) | 99.41–99.88% | 91 ms | $0.0676 | recorded | — |
| GPT-6.1 Sol measured sample | 100.0% (40/40) | 91.24–100.00% | 9.05 s | $5.96 | list | time and cost projected from the sample |

Drex throughput on summed call latency: 603.1 cases per minute. Criteria accuracy on the same replay: 99.1%. Colours: red 692, amber 1,279, green 240. Labels: deny 692, incomplete 743, approve 240, not applicable 536.

GPT-6.1 Sol accuracy is the measured sample of 40 and is not projected. Projected full-queue cost at list: $17.32. Projected throughput: 3.66 cases per minute. Mean recorded sample latency: 16.4 s. The hub says that GPT latency includes shared router rate-limit wait, and that the projection scales the measured per-case mean onto the queue.

GPT time and cost on the full queue are projected from the measured sample. Accuracy is not projected. Drex latency is the median of recorded `latency_ms` on the queue replay.

## Command Center rule change

**Simulated.** What flips if every published OR list is tightened to AND, using the same recorded Drex facts.

Metric: **flip_rate**. Share of affected requests whose decision changes when the combining rule changes. k is the flip count. Not an accuracy.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Simulated OR to AND | 58.9% (189/321) | 53.42–64.13% | — | — | — | simulated from recorded answers |

Flip counts: incomplete to deny 70, approve to deny 91, approve to incomplete 28. The search for a real before-and-after policy pair found none (best token Jaccard 0.037). The same recorded Drex facts are reused. Only the combining rule changes: each published OR list is split so every alternative clause is required.

This figure is simulated. There was no published before-and-after policy pair. Each OR list is rewritten so every alternative clause is required, and the recorded Drex facts are scored again.

## Command Center, deciding record

Does the record Drex names as deciding match the gold deciding record?

Metric: **agreement**. Share of scored requests where the named deciding record agrees with gold. This run is not simulated.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| Drex what-decides | 90.0% (1,991/2,211) | 88.73–91.23% | 338 ms | $0.293 | recorded | — |

| Gold kind | Agree | n |
|---|---:|---:|
| joint unmet | 110 | 134 |
| absent | 738 | 739 |
| criteria met | 196 | 243 |
| scope | 536 | 536 |
| criteria unmet | 411 | 559 |

Cost per 1,000 is the recorded total divided by the number scored, times 1,000. The median latency is computed from the replay, because the summary file does not store one.

## Full-chain demo

Five scripted prior-auth packets: public policy page, NDI boxes, search, Drex, letter.

Metric: **decision_matches_gold**. Share of the five evidence files whose top-level decision equals gold_decision.

| Lane | Result | 95% CI | p50 latency | $ / 1,000 | Cost basis | Note |
|---|---:|---:|---:|---:|---|---|
| BillProof chain | 100.0% (5/5) | 56.55–100.00% | 2.90 s | — | five-case total | — |
| GPT check | 100.0% (5/5) | 56.55–100.00% | — | — | five-case total | — |

The cost column is empty because the hub stores a five-case total, not a scaled cost per 1,000. Chain total $0.504. GPT check total $0.0327. Drex latency is `timings.drex_ms` only. Browser, NDI, and search time sit in the same timings object and are not in this median.

| Case | Gold | Chain | GPT check | Recorded cost |
|---|---|---|---|---:|
| PA-D1 | criteria_met | criteria_met | criteria_met | $0.106 |
| PA-D2 | criteria_unmet | criteria_unmet | criteria_unmet | $0.0806 |
| PA-D3 | incomplete | incomplete | incomplete | $0.0813 |
| PA-D4 | not_applicable | not_applicable | not_applicable | $0.131 |
| PA-D5 | criteria_met | criteria_met | criteria_met | $0.105 |

These five packets were built by hand. They are not a random sample, and the interval is wide because n is 5. Scaling that total to a cost per 1,000 would overstate a five-case demo.

## What these tables do not support

A higher point estimate with overlapping intervals is not a finding that one model is more accurate, except where a paired test is printed above. The hard line verdict can be high while strict case accuracy is 0, because one missed question fails the bill and the line rule does not require that. Prior-authorization accuracy is saturated for Drex and for GPT-6.1 Sol; the measured differences there are cost and latency. None of these scores is a measure of clinical correctness or of what a payer must cover. A person signs every letter the system drafts.

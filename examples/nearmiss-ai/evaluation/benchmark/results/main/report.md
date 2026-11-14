# Drex vs OpenAI benchmark: `main`

Run created 2026-11-14T22:30:46+00:00; seed 20261114; 1000 cases requested per task; 4 parallel workers; OpenAI mode `json_schema`. Models requested: drex = `drex-latest`, openai = `gpt-6-luna`.

## Communication breakdown detection (`communication_breakdown`)

Ground truth: Person 1 / Communication Breakdown (NASA analyst-coded; non-blank = breakdown). Classes: breakdown, no_breakdown. Cases: 1000.

| Provider (model answering) | Valid / attempted | Accuracy (95% CI) | Macro-F1 | Errors | Median latency | p95 latency | Mean input tokens | Cost / 1,000 decisions |
|---|---|---|---|---|---|---|---|---|
| drex (`drex-max-v1.1`) | 1000 / 1000 | 72.7% (69.9% to 75.4%) | 0.720 | none | 0.300 s | 0.417 s | 474 | $0.0569 |
| openai (`gpt-6-luna`) | 1000 / 1000 | 77.2% (74.5% to 79.7%) | 0.772 | none | 1.408 s | 3.437 s | 450 | $0.0900 |
| baseline: Always 'breakdown' (majority class) | 1000 / 1000 | 50.0% (46.9% to 53.1%) | 0.333 | n/a | n/a | n/a | n/a | $0 |
| baseline: Keyword rule (19 fixed terms) | 1000 / 1000 | 62.4% (59.4% to 65.3%) | 0.594 | n/a | n/a | n/a | n/a | $0 |

**Head to head** (cases where both returned a valid answer):

- 1000 paired cases: both correct 681, only Drex correct 46, only OpenAI correct 91, neither 182.
- Accuracy difference (Drex minus OpenAI): -4.5 points, 95% bootstrap CI -6.9 to -2.3; exact McNemar p = 0.0002.
- The two providers gave the same answer on 86.3% of paired cases.

**Measured outcome:**

- Accuracy: OpenAI scored higher; the 95% interval of the paired difference excludes 0.
- Latency: Drex was faster at the median (4.7x).
- Cost: Drex was cheaper per 1,000 decisions (1.6x) at the supplied prices.

Drex scores as probabilities: mean confidence 0.842, ECE 0.115, Brier 0.380 (lower is better for the last two; Drex scores are decision scores, not guaranteed probabilities).

Per-class recall, drex: breakdown 56.4% (n=500), no_breakdown 89.0% (n=500)

Per-class recall, openai: breakdown 73.0% (n=500), no_breakdown 81.4% (n=500)

## Primary problem category (`primary_problem`)

Ground truth: Assessments / Primary Problem (NASA analyst-coded; 13 minor values grouped as Other). Classes: Human Factors, Aircraft, Procedure, Ambiguous, Other. Cases: 1000.

| Provider (model answering) | Valid / attempted | Accuracy (95% CI) | Macro-F1 | Errors | Median latency | p95 latency | Mean input tokens | Cost / 1,000 decisions |
|---|---|---|---|---|---|---|---|---|
| drex (`drex-max-v1.1`) | 1000 / 1000 | 49.3% (46.2% to 52.4%) | 0.438 | none | 0.301 s | 0.503 s | 522 | $0.0626 |
| openai (`gpt-6-luna`) | 1000 / 1000 | 49.2% (46.1% to 52.3%) | 0.441 | none | 1.568 s | 3.636 s | 497 | $0.1047 |
| baseline: Always 'Human Factors' (majority class) | 1000 / 1000 | 20.0% (17.6% to 22.6%) | 0.067 | n/a | n/a | n/a | n/a | $0 |

**Head to head** (cases where both returned a valid answer):

- 1000 paired cases: both correct 430, only Drex correct 63, only OpenAI correct 62, neither 445.
- Accuracy difference (Drex minus OpenAI): +0.1 points, 95% bootstrap CI -2.1 to +2.4; exact McNemar p = 1.0000.
- The two providers gave the same answer on 80.2% of paired cases.

**Measured outcome:**

- Accuracy: no statistically clear difference (the 95% interval of the paired difference includes 0).
- Latency: Drex was faster at the median (5.2x).
- Cost: Drex was cheaper per 1,000 decisions (1.7x) at the supplied prices.

Drex scores as probabilities: mean confidence 0.626, ECE 0.149, Brier 0.639 (lower is better for the last two; Drex scores are decision scores, not guaranteed probabilities).

Per-class recall, drex: Human Factors 64.5% (n=200), Aircraft 93.5% (n=200), Procedure 23.5% (n=200), Ambiguous 4.5% (n=200), Other 60.5% (n=200)

Per-class recall, openai: Human Factors 68.5% (n=200), Aircraft 88.0% (n=200), Procedure 27.0% (n=200), Ambiguous 2.0% (n=200), Other 60.5% (n=200)

## Limitations

- **Labels are NASA analyst codes, not verified truth.** ASRS reports are voluntary and unverified. For the
  communication task a blank "Communication Breakdown" cell means *none was coded*, not that none occurred, and
  the field covers Person 1 only, so some "no_breakdown" cases may genuinely describe a breakdown.
- **Option descriptions are ours.** They are written for this benchmark and are not NASA's official definitions;
  both providers see identical text.
- **Class-balanced samples, not natural prevalence.** Accuracy here is not the rate on the raw ASRS stream.
- **One export, one time window.** July 2023 to June 2024, seeded sample; results will vary with other samples.
- **Single prompt per task, zero examples, no tuning for either provider.** Better prompts could move either side.
- **Latency is wall-clock per call including each SDK's default retries,** measured from this machine with the
  recorded parallelism; it is not a controlled network benchmark.
- **No document evidence (NDI) is used.** These tasks classify the narrative alone, unlike the 4-agent swarm.
- **Costs use the prices you supplied** and the token counts each API returned; no price was assumed.

<!-- agent: claude-code  2026-11-14 -->
# For the judges: Calibrated, "Flaky or Real?"

**Track: Apex Predator, Drex vs. the Giants.** *Take a frontier-LLM task, hand the decisions to Drex, and prove it faster, cheaper, or more accurate.*

**The task:** a CI build goes red. Is the failing test flaky (quarantine it), worth a retry, or a real regression (block the merge)? Engineers and frontier LLMs both do this today.

**The result, in one line:** on 60 real CI failures, Drex plus a cost-aware policy beat two frontier LLMs on all three counts: more accurate, cheaper in engineer time, and faster.

## Why this wins on each part of the track

| The track asks for | Drex + policy | Llama-3.3-70B (DeepInfra) | DeepSeek-V3.2 (DeepInfra) |
|---|---|---|---|
| **More accurate** (flaky vs real, 60 cases) | **96.7%** | 85.0% | 88.3% |
| **Real bugs quarantined** (the costly mistake) | **0** | 0 | 0 |
| **Cheaper** (expected cost, engineer-minutes, lower is better) | **118** | 228 | 210 |
| **Faster** (p50 latency) | **292 ms** | 2,456 ms | 1,618 ms |
| Cases where it said "RETRY" (pushed the decision back) | **0 of 60** | 41 | 36 |

Held-out set (44 cases the thresholds were never fitted on): cost 98 for Drex vs 176 and 162 for the LLMs.
All numbers are from real API runs on 2026-11-14. Source: [`results/metrics_real.md`](results/metrics_real.md).

## What is different about how we used Drex

1. **We used the probability, not a verdict.** Drex answers one `noul` question per failure and returns P(flaky). A two-threshold policy (0.40 / 0.80) turns it into QUARANTINE, RETRY or BLOCK using a cost table: a shipped bug costs 120 engineer-minutes, a needless block costs 25.
2. **The probabilities separate the classes cleanly.** All 24 real regressions scored P(flaky) at or below 0.35. 34 of 36 flaky failures scored 0.93 or higher. Nothing landed in the retry band.
3. **A "retry passed, so it's flaky" rule fails.** It quarantined 2 real race-condition bugs and cost 358. Drex gave those two P = 0.07 and 0.09, so it did not.
4. **The loop does not overreact.** One human override of a confident Drex answer would raise total cost from 118 to 484 if we moved the thresholds to match it, so the loop keeps them and logs the pattern. Details in the README.

## All four Nace tools are in the loop, with evidence

| Tool | Role | Evidence |
|---|---|---|
| Drex (`drex-max-v1.1`) | The decision on all 60 cases, and System 1 supervisor for Intern | [`results/drex_real.json`](results/drex_real.json) |
| Intern | Ran the triage loop headless: 6 turns, 39 s, 16 Drex checks, 0 failed | [`results/intern_excerpt.md`](results/intern_excerpt.md) |
| NDI | Extracted structured fields from all 60 raw logs | [`results/ndi_real.json`](results/ndi_real.json) |
| DeepInfra | Ran both LLM baselines and Intern's worker model | [`results/llm_real.json`](results/llm_real.json), [`results/llm2_real.json`](results/llm2_real.json) |

## Reproduce it in two minutes, no keys needed

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install rich requests python-dotenv
python demo.py --offline        # replays the real saved results
```

With keys in `.env` (see `.env.example`): `./run_all.sh` re-runs every stage, and `python demo.py --live` calls Drex again.

## Honest limits (we would rather you hear them from us)

- **60 seeded cases.** The logs are generated pytest/jest-style failures with human labels, not production data. That is why our ask is a pilot on a real team's CI.
- **The learning step changed 0 decisions.** The 16 human labels did not move the thresholds, because Drex already separated the classes. The loop's value here is that it checks a correction against the cost table before acting on it.
- **Per-call price is about the same as Llama** ($0.072 vs $0.064 per 1,000 decisions), and lower than DeepSeek ($0.154). The savings come from better decisions, not cheaper tokens.
- **Drex Max is not bit-identical between calls.** Example: case_007 scored 0.074 cached and 0.107 live. The demo replays logged values by default for that reason.

## Where to look

- Start with this file, then [`README.md`](README.md) (architecture, verified API shapes, full tables).
- Pitch deck: [`pitch/index.html`](pitch/index.html) and [`pitch/Calibrated_Drexathon.pdf`](pitch/Calibrated_Drexathon.pdf).
- [`AGENTS.md`](AGENTS.md) is the instruction file for the Intern agent run, not a pitch.

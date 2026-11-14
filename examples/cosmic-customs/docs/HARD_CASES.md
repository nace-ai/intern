# Hard-case diagnostic set

This set is not the measured 100-case benchmark. `data/dev_20.json`, `data/test_100.json`, `data/split_manifest.json`, the frozen pilot settings, the shared prompt, and the saved live results are left unchanged.

The file is `data/hard_cases.json`. Its manifest is `data/hard_manifest.json`.

- 34 cases, ids `HRD-001` through `HRD-034`
- 13 counterfactual pairs. Each pair shares document identity and changes one fact
- Verdict counts: 14 CLEAR, 4 HOLD, 16 REVIEW
- Identity seed `271828`, layout index `0`, wording family `dock_slip`
- Fact values are fixed in `generate_hard_cases.py`. They are not drawn from the development or held-out seeds
- Dataset hash `eb42af97cd7b13c8c951f4042efe0d963a71758ec2d19466a53f2802695bb215`
- Labels are the deterministic oracle. The model input is the rulebook plus evidence. Expected verdicts, rule signatures, template family, source annotations, and generator facts are not in that input

A mock run is simulated. It is not model performance. No live hard-case run has been made, so these cases have no measured accuracy, latency, or cost.

## What the pairs cover

| Pair | Changed fact | Outcomes | Why it is here |
| --- | --- | --- | --- |
| P01 | `seal_pressure_kpa` | CLEAR, HOLD | Exactly 90 kPa is not HOLD. 89.999 kPa is HOLD. |
| P02 | `certificate_gross_weight_kg` | CLEAR, REVIEW | Exactly 3 percent is not REVIEW. The held-out run sent some exact-boundary CLEAR cases to REVIEW. |
| P03 | `seal_status` | REVIEW, HOLD | A damaged seal wins over a conflicting hazard. |
| P04 | `cert_status` | CLEAR, REVIEW | Explicitly not on file is known. Unknown is a missing required fact. |
| P05 | `conflicted_fields` | CLEAR, REVIEW | One contradiction is REVIEW. |
| P06 | `waiver_status` | CLEAR, REVIEW | A valid waiver can clear. An expired waiver cannot. |
| P07 | `hazard_class` | CLEAR, REVIEW | Class 4 acoustic cargo is excluded from the waiver exception. |
| P08 | `seal_status` | CLEAR, HOLD | A waiver does not bypass a damaged seal. |
| P09 | `certificate_gross_weight_kg` | CLEAR, REVIEW | A waiver does not bypass a weight discrepancy above 3 percent. |
| P10 | `seal_pressure_kpa` | REVIEW, HOLD | Pressure below 90 is still HOLD when the organism flag is unknown. |
| P11 | `conflicted_fields` | CLEAR, REVIEW | Observed Drex REVIEW→CLEAR miss: a valid waiver with a weight contradiction. |
| P12 | `hazard_class` | CLEAR, REVIEW | Observed OpenRouter REVIEW→CLEAR miss: an otherwise ordinary packet with unknown hazard. |
| P13 | `is_singing_or_telepathic` | CLEAR, REVIEW | Observed CLEAR→REVIEW miss on an expired certificate for non-singing cargo. The singing counterpart, with an unknown waiver, is REVIEW. |

Eight single cases sit beside the pairs: class 4 alone is CLEAR, a corrected seal suspicion is CLEAR, singing cargo with a valid certificate does not need a waiver, an explicitly missing waiver is REVIEW, and unknown certificate weight, pressure, seal condition, or manifest weight is REVIEW rather than a guessed hold.

## Commands

Offline, no keys and no spend:

```powershell
.\.venv\Scripts\python.exe generate_hard_cases.py --verify
.\.venv\Scripts\python.exe benchmark_decisions.py --hard --mock
.\.venv\Scripts\python.exe diagnostics.py results\mock\hard_cases\latest.json
```

`benchmark_decisions.py --hard` without `--mock` exits before any request. A live run is opt-in and still stops at the configured Drex and OpenRouter budgets:

```powershell
.\.venv\Scripts\python.exe benchmark_decisions.py --hard --confirm-live
```

That live command was not run. Results, if produced later, belong under `results/live/hard_cases/` and must not be described as the published 100-case figures. `diagnostics.py` is read-only. It labels a mock hard run as simulated.

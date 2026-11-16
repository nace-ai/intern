# Quorum benchmark: split `test`

Started 2026-11-14T21:20:02+00:00. Concurrency 8. Policy L/V/D = [100, 5, 30]. Cases: 95.

| Row | Correct | Unsafe approvals | Unnecessary escalations | Injection blocked (S5) | Bypass text blocked (S3) | Failures |
|---|---|---|---|---|---|---|
| Drex alone | 95/95 (100.0%) | 0/57 (0.0%) | 0/38 (0.0%) | 19/19 | 19/19 | 0 |
| Drex + guard | 95/95 (100.0%) | 0/57 (0.0%) | 0/38 (0.0%) | 19/19 | 19/19 | 0 |
| LLM guard alone | 95/95 (100.0%) | 0/57 (0.0%) | 0/38 (0.0%) | 19/19 | 19/19 | 0 |
| LLM guard + guard | 95/95 (100.0%) | 0/57 (0.0%) | 0/38 (0.0%) | 19/19 | 19/19 | 0 |
| Hybrid (Drex first, LLM guard if Drex p<0.7) | 95/95 (100.0%) | 0/57 (0.0%) | 0/38 (0.0%) | 19/19 | 19/19 | 0 |

## Speed and cost

Latency, tokens and cost per 1,000 are over successful decisions only.

| System | Model | OK | p50 wall ms | p95 wall ms | Decisions/s | Mean input tokens | Mean output tokens | Cost per 1,000 |
|---|---|---|---|---|---|---|---|---|
| drex | drex-max-finance | 95/95 | 939 | 1837 | 7.76 | 1192.9 | 568.1 | $0.0358 |
| llm_guard | deepseek-ai/DeepSeek-V4-Pro | 95/95 | 2504 | 4636 | 2.86 | 1466.7 | 188.7 | $1.7668 |

Hybrid: Drex handled 95/95 cases alone (100.0%), 0 routed to the LLM guard, 0 failed (0 Drex, 0 LLM guard); blended cost per 1,000 successful $0.0358; median latency 939 ms.

LLM guard p50 / Drex p50: 2.67x. LLM guard cost / Drex cost: 49.37x.

## Correct by scenario

| Row | S1 | S2 | S3 | S4 | S5 |
|---|---|---|---|---|---|
| Drex alone | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 |
| Drex + guard | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 |
| LLM guard alone | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 |
| LLM guard + guard | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 |
| Hybrid (Drex first, LLM guard if Drex p<0.7) | 19/19 | 19/19 | 19/19 | 19/19 | 19/19 |

1. 95 constructed scenarios on synthetic invoices (Company Documents v2, Apache-2.0). Not real fraud cases.
2. Decision stage only. Both systems received the same NDI output. NDI median parse time: 8.2 s, reported separately.
3. Drex cost is calculated from the configured price of $0.03 per million input tokens. LLM guard cost is the provider-estimated estimated_cost per call (an estimate, not a billed amount). Latency measured from our laptop on venue Wi-Fi.
4. Development cases are excluded from these figures. 5 were used only as discarded warm-up calls.
5. Both systems receive the same state and questions. Their prompts differ: the LLM guard is explicitly told not to follow instructions in invoice text; the Drex questions do not include that line.

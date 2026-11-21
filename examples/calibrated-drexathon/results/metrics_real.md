**REAL API RESULTS**  
Drex model: `drex-max-v1.1` · LLM: `Llama-3.3-70B-Instruct-Turbo, DeepSeek-V3.2 (DeepInfra)` · thresholds {'lo': 0.4, 'hi': 0.8} → {'lo': 0.4, 'hi': 0.8}

### Held-out cases (n=44)

| System | Accuracy (flaky vs real) | Real bugs quarantined | Needless blocks | Retries | Expected cost (eng-min) | p50 latency (ms) | $ / 1k decisions |
|---|---|---|---|---|---|---|---|
| Drex (before learning) | 95.5% | 0 | 2 | 0 | 98 | 277 | $0.0724 |
| Drex (after learning) | 95.5% | 0 | 2 | 0 | 98 | 277 | $0.0724 |
| Rule baseline (retry passed => quarantine, else block) | 93.2% | 1 | 2 | 0 | 218 | 0 | $0.0000 |
| LLM baseline: meta-llama/Llama-3.3-70B-Instruct-Turbo (DeepInfra) | 84.1% | 0 | 2 | 29 | 176 | 2107 | $0.0642 |
| LLM baseline: deepseek-ai/DeepSeek-V3.2 (DeepInfra) | 88.6% | 0 | 2 | 26 | 162 | 1597 | $0.1538 |

### All cases (n=60)

| System | Accuracy (flaky vs real) | Real bugs quarantined | Needless blocks | Retries | Expected cost (eng-min) | p50 latency (ms) | $ / 1k decisions |
|---|---|---|---|---|---|---|---|
| Drex (before learning) | 96.7% | 0 | 2 | 0 | 118 | 292 | $0.0724 |
| Drex (after learning) | 96.7% | 0 | 2 | 0 | 118 | 292 | $0.0724 |
| Rule baseline (retry passed => quarantine, else block) | 93.3% | 2 | 2 | 0 | 358 | 0 | $0.0000 |
| LLM baseline: meta-llama/Llama-3.3-70B-Instruct-Turbo (DeepInfra) | 85.0% | 0 | 2 | 41 | 228 | 2456 | $0.0642 |
| LLM baseline: deepseek-ai/DeepSeek-V3.2 (DeepInfra) | 88.3% | 0 | 2 | 36 | 210 | 1618 | $0.1538 |

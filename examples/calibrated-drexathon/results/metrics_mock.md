**MOCK DATA - NOT REAL API OUTPUT**  
Drex model: `drex-max-v1.1 (MOCK)` · LLM: `Llama-3.3-70B-Instruct-Turbo, DeepSeek-V3.2 (DeepInfra)` · thresholds {'lo': 0.4, 'hi': 0.8} → {'lo': 0.4, 'hi': 0.82}

### Held-out cases (n=44)

| System | Accuracy (flaky vs real) | Real bugs quarantined | Needless blocks | Retries | Expected cost (eng-min) | p50 latency (ms) | $ / 1k decisions |
|---|---|---|---|---|---|---|---|
| Drex (before learning) | 93.2% | 0 | 2 | 4 | 122 | 258 | $0.0606 |
| Drex (after learning) | 93.2% | 0 | 2 | 4 | 122 | 258 | $0.0606 |
| Rule baseline (retry passed => quarantine, else block) | 93.2% | 1 | 2 | 0 | 218 | 0 | $0.0000 |
| LLM baseline: meta-llama/Llama-3.3-70B-Instruct-Turbo (MOCK) (DeepInfra) | 68.2% | 3 | 8 | 7 | 622 | 1590 | $0.0582 |

### All cases (n=60)

| System | Accuracy (flaky vs real) | Real bugs quarantined | Needless blocks | Retries | Expected cost (eng-min) | p50 latency (ms) | $ / 1k decisions |
|---|---|---|---|---|---|---|---|
| Drex (before learning) | 93.3% | 1 | 2 | 5 | 264 | 256 | $0.0606 |
| Drex (after learning) | 93.3% | 0 | 2 | 6 | 150 | 256 | $0.0606 |
| Rule baseline (retry passed => quarantine, else block) | 93.3% | 2 | 2 | 0 | 358 | 0 | $0.0000 |
| LLM baseline: meta-llama/Llama-3.3-70B-Instruct-Turbo (MOCK) (DeepInfra) | 73.3% | 3 | 9 | 10 | 675 | 1577 | $0.0582 |

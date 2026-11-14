## Benchmark: drex (blue) vs llm:DeepSeek-V3 (red)
2026-11-14T21:30:20.680Z | commit 346a237 | 5 matches/mode | 10 units/side | models: drex=drex-latest, llm=deepseek-ai/DeepSeek-V3

| Mode | Valid/Run | Blue win % | Red win % | Draws | Blue latency mean / p95 (ms) | Red latency mean / p95 (ms) | Blue decisions/sim-sec | Red decisions/sim-sec | Blue $/match | Red $/match | Blue invalid | Red invalid | Tainted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.25x | 5/5 | 100% | 0% | 0 | 429 / 643 | 2315 / 5050 | 57.17 | 7.42 | $0.00980 | $0.01145 | 0 | 0 | 0 |
| 0.5x | 5/5 | 100% | 0% | 0 | 457 / 806 | 1648 / 5026 | 27.58 | 3.37 | $0.00654 | $0.00954 | 0 | 0 | 0 |
| paused | 5/5 | 100% | 0% | 0 | 433 / 676 | 2834 / 4881 | 17.77 | 13.73 | $0.00305 | $0.01701 | 0 | 0 | 0 |
| 1x | 5/5 | 60% | 40% | 0 | 388 / 563 | 2805 / 5420 | 17.02 | 1.79 | $0.00319 | $0.00282 | 0 | 0 | 0 |
| 2x | 5/5 | 80% | 20% | 0 | 510 / 1204 | 4107 / 5748 | 7.81 | 0.6 | $0.00134 | $0.00088 | 0 | 0 | 0 |

Total cost: blue $0.11961, red $0.20852

- Cost ratio (red/blue) per match 0.25x: 1.17
- Cost ratio (red/blue) per match 0.5x: 1.46
- Cost ratio (red/blue) per match paused: 5.58
- Cost ratio (red/blue) per match 1x: 0.89
- Cost ratio (red/blue) per match 2x: 0.65

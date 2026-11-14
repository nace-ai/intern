# Benchmark takeaways (computed from results/latest.json)

Run: 2026-11-14T21:30:20.680Z, n=5 per mode, Drex `drex-latest` (drex-max-v1.1) vs LLM `deepseek-ai/DeepSeek-V3`.

| Mode | Drex win rate | LLM/Drex mean latency | Drex/LLM decision cycles per match | Drex $ per 1k unit orders | LLM $ per 1k unit orders | LLM/Drex cost per unit order |
|---|---|---|---|---|---|---|
| 0.25x | 100% (5/5) | 5.4x | 4.2x | $0.0087 | $0.0787 | 9.0x |
| 0.5x | 100% (5/5) | 3.6x | 3.1x | $0.0084 | $0.0997 | 11.9x |
| paused | 100% (5/5) | 6.6x | 1.0x | $0.0088 | $0.0632 | 7.2x |
| 1x | 60% (3/5) | 7.2x | 6.6x | $0.0093 | $0.0780 | 8.4x |
| 2x | 80% (4/5) | 8.1x | 9.2x | $0.0092 | $0.0783 | 8.5x |

Overall: Drex won 22/25 matches, LLM won 3/25.

Earlier run (results/bench-2026-11-14T21-22-05-299Z.json, n=3): Drex won 9/15 (Drex-LLM per mode: 0.25x 2-1, 0.5x 1-2, paused 2-1, 1x 2-1, 2x 2-1). Drex lost the 0.5x mode in that run.

Unit order = one order applied to one unit. Cost per unit order = cost per match / (decisions per sim-second x mean sim seconds).

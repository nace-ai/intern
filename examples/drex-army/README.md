# Drex Army vs LLM Army

Real-time capture the flag where **every unit is an agent**. Team **BLUE** is commanded by **Drex** (Nace AI's decision model: state + typed questions → a probability per option in one forward pass, no text generation). Team **RED** is commanded by a large LLM (**DeepSeek-V3** on DeepInfra). Same rules, same information, same action space.

**Thesis:** in real time, fast decisions win, and Drex is fast and cheap enough to command an army. Paused mode removes latency from the equation so only decision quality counts. We report both honestly.

## How it works

Each match is played on a 1000x600 map with a base and a flag per team, and 10 units per side.
Every unit has 100 hp, moves 60 px per second, has a 70 px range, automatically deals 20 damage per second to the nearest enemy in range, and regenerates 8 hp per second while inside its own base.
You win by carrying the enemy flag into your own base, by eliminating all enemies, or — if no winner has emerged at the 60-second mark — by having more kills.
All movement targets are computed before any unit moves, and damage is applied simultaneously, so neither team has a tick-order advantage.

The simulation ticks at a fixed 10 ticks per second; the 0.25x, 0.5x, 1x, and 2x speed settings only change how fast real time elapses.
Each team's brain runs its own async loop: take a snapshot of the state, send a request to its model, and apply the orders when they come back.
Units keep their last order in the meantime, so a slow brain acts on stale state.
In paused mode the simulation freezes every 0.5 seconds of game time and both brains decide on the same snapshot, so only decision quality counts.

Brains never steer units: each unit gets one of six orders — attack_nearest_enemy, push_enemy_flag, defend_own_flag, support_nearest_ally, retreat_to_base, hold — and the engine handles movement and combat.
Both brains receive the identical team-perspective JSON (src/brains/view.ts): your units, the enemy units, both flags, both bases, the kill count, and the time left.

BLUE is Drex: one request per cycle to Nace's /v1/systemone endpoint, with the team-perspective state shared and one choice question per unit whose options are the six actions.
Drex returns a probability for every option; the most likely one becomes the unit's order, and clicking a unit in the UI shows its probability bars.

RED is the LLM: DeepSeek-V3 on DeepInfra, one call per cycle for all 10 units, in JSON mode.
The model returns a plan of 1–3 orders per unit, and the next planned order starts every 2 seconds of game time if no new orders arrive.
Output is parsed strictly; invalid output makes all units hold for that cycle, and the cycle is counted as invalid.

```
            +----------------------+
            |      tick loop       |
            |   (10 ticks/s sim)   |
            +--+----+-----+-----+--+
                |    |     |    |
                v    ^     v    ^
            +--------+   +--------+
            |  Drex  |   |  LLM   |
            | (blue) |   | (red)  |
            +--------------+-------+
      (v = snapshot sent to the brain, ^ = orders back to the tick loop)
```

The sim server (src/server/index.ts) streams the game state over WebSocket to the Vite + HTML canvas UI in the web folder.
The bench harness (src/server/bench.ts) runs headless matches in parallel; matches where a brain's API failed are marked tainted and left out of win rates.

## Results

### Benchmark: drex (blue) vs llm:DeepSeek-V3 (red)
2026-11-14T21:30:20.680Z | commit 346a237 | 5 matches/mode | 10 units/side | models: drex=drex-latest, llm=deepseek-ai/DeepSeek-V3

| Mode | Valid/Run | Blue win % | Red win % | Draws | Blue latency mean / p95 (ms) | Red latency mean / p95 (ms) | Blue decisions/sim-sec | Red decisions/sim-sec | Blue $/match | Red $/match | Blue invalid | Red invalid | Tainted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.25x | 5/5 | 100% | 0% | 0 | 429 / 643 | 2315 / 5050 | 57.17 | 7.42 | $0.00980 | $0.01145 | 0 | 0 | 0 |
| 0.5x | 5/5 | 100% | 0% | 0 | 457 / 806 | 1648 / 5026 | 27.58 | 3.37 | $0.00654 | $0.00954 | 0 | 0 | 0 |
| paused | 5/5 | 100% | 0% | 0 | 433 / 676 | 2834 / 4881 | 17.77 | 13.73 | $0.00305 | $0.01701 | 0 | 0 | 0 |
| 1x | 5/5 | 60% | 40% | 0 | 388 / 563 | 2805 / 5420 | 17.02 | 1.79 | $0.00319 | $0.00282 | 0 | 0 | 0 |
| 2x | 5/5 | 80% | 20% | 0 | 510 / 1204 | 4107 / 5748 | 7.81 | 0.6 | $0.00134 | $0.00088 | 0 | 0 | 0 |

Total cost: blue $0.11961, red $0.20852

Takeaways (from `results/takeaways.md`):

- **Win rate.** Drex won 22 of 25 matches: 100% (5/5) at 0.25x, 100% (5/5) at 0.5x and in paused mode, 80% (4/5) at 2x, 60% (3/5) at 1x.
- **Latency.** The LLM's mean decision latency was 3.6x–8.1x slower than Drex's across modes (e.g. 2315 ms vs 429 ms at 0.25x, 4107 ms vs 510 ms at 2x).
- **Decision cycles.** Drex got 3.1x–9.2x more decision cycles per match than the LLM (129 vs 31 at 0.25x, 46.2 vs 7 at 1x); in paused mode the two are equal (39.4 cycles each) since both decide on the same frozen snapshot.
- **Cost per unit order.** Drex costs $0.0084–$0.0093 per 1k unit orders; the LLM costs $0.0632–$0.0997 per 1k — 7.2x to 11.9x more per order, even though Drex's brains run more often.
- **Paused mode.** With latency removed from the equation, Drex still won 5/5 (and at 17.77 vs 13.73 decisions/sim-sec, $0.00305 vs $0.01701 per match), so decision quality alone was enough to win.
- **Caveats.** Sample size is small (5 matches per mode); LLM latency was measured while about 4 matches ran at the same time, so DeepInfra was under load; the Drex model was `drex-latest` (drex-max-v1.1) and the LLM was `deepseek-ai/DeepSeek-V3`; and Drex lost real matches — 2 of 5 at 1x and 1 of 5 at 2x.
- **Earlier run.** A first run with 3 matches per mode (`results/bench-2026-11-14T21-22-05-299Z.json`) had Drex winning 9 of 15, and Drex **lost the 0.5x mode 1–2** there. Across both runs Drex won 31 of 40.

To reproduce: `npm run bench -- --n 5 --modes 0.25,0.5,1,2,paused --blue drex --red llm --drex-parallel 4`

## Run it

Requirements: Node 20+.

```bash
npm install
cp .env.example .env    # then fill in NACE_API_KEY and DEEPINFRA_API_KEY
npm run drex:test       # one Drex call: orders + probabilities for 10 units, latency, cost
npm run dev             # sim server (ws://localhost:8787) + UI (http://localhost:5173)
```

In the UI pick a brain per team (`drex`, `llm`, `scripted`), a speed (0.25x–2x) or **Paused mode**, and press **Start match**. Click a unit to see its Drex probability bars.

**Supply crates (optional, off by default):** tick *Supply crates* to spawn a crate on the midline every 8 s and add a 7th order, `collect_supply` (+1 point, +30 hp). Both brains get the action and the crate positions in the same shared view. The benchmark results above were run with this **off** (6 actions); supply mode is not benchmarked.

Headless:

```bash
npx tsx src/server/sim.ts drex llm paused     # one match, prints result JSON
npm run bench -- --n 3 --modes 0.25,0.5,1,2,paused --blue drex --red llm --drex-parallel 4
npx tsx scripts/report.ts results/latest.json # markdown table + CSV
```

Benchmark output goes to `results/bench-<timestamp>.json` and `results/latest.json`; the UI shows the latest summary.

### Config (env)

| Var | Default | Meaning |
|---|---|---|
| `NACE_API_KEY` | (required) | Drex API key |
| `DEEPINFRA_API_KEY` | (required) | DeepInfra API key |
| `DREX_MODEL` | `drex-latest` (→ `drex-max-v1.1`) | Drex model |
| `LLM_MODEL` | `deepseek-ai/DeepSeek-V3` | DeepInfra model |
| `DREX_PRICE_PER_MTOK` | `0.03` | Drex USD per 1M input tokens (only input billed) |
| `LLM_PRICE_IN_PER_MTOK` / `LLM_PRICE_OUT_PER_MTOK` | `0.32` / `0.89` | Fallback if DeepInfra omits `estimated_cost` |

## Fairness

- Both AI brains receive the **identical** team-perspective JSON (`src/brains/view.ts`) and the same 6 actions.
- The simulation ticks at a fixed 10 ticks/s regardless of brains; each brain runs its own async loop and units keep their last order until new orders arrive. Slow brains act on stale state, and that is the point.
- The LLM gets its best setup: one call for all units, JSON mode, and a 1–3 step plan per unit so units don't idle between calls. Invalid output → all units `hold`, counted.
- Engine resolves all movement targets before moving and applies damage simultaneously, so neither side has a tick-order advantage (scripted mirror match = exact draw).
- Matches where a brain's API failed (0 decision cycles or >10% errored cycles) are flagged **tainted** and excluded from win rates, never silently counted.

## Tools used

- **Nace AI Drex API** (`POST /v1/systemone`, `drex-max-v1.1`): one request per decision cycle; shared battlefield `state` + one `choice` question per unit.
- **Nace Intern** (Nace's coding agent): built `src/brains/scripted.ts`, `scripts/report.ts`, and README sections (commits tagged `[intern]`).
- **DeepInfra** (OpenAI-compatible API) serving DeepSeek-V3 for the LLM army.
- TypeScript, Node, `ws`, Vite, HTML canvas.

## Notes

- If the Drex API returns `403` with `x-vercel-mitigated: challenge`, the hosting edge is rate-limiting your IP (common on shared venue Wi-Fi). The client honors `Retry-After` with backoff; the HUD shows the error. Use another network or reduce parallelism.
- Sprites: drop `blue_unit.png`, `red_unit.png`, `flag_blue.png`, `flag_red.png`, `map.png` into `web/public/assets/` (circle/flag fallbacks otherwise).

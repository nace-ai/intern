# VICE EXCHANGE — swarm brief (read fully before writing code)

Repo root: `/Users/manubaba/Documents/vice city/vice-exchange-claude` (quote the path; it has a space).
The PARENT folder holds another, abandoned attempt. Never read from, write to, or copy from it (except the three
`VICE_EXCHANGE_*.md` spec docs, read-only). Hard cutoff: demo-ready by 14:20 local time today.

## What we are building
An original neon city ("Coral Bay") where each outlaw character is a tradable fictional ticker, eight NPCs decide
with real Nace Drex `choice` calls from their own private memory, information spreads only through recorded
messages, and capture sets the ticker to exactly V$0. Original names and art only: no existing game's names,
characters, maps, logos or look.

## Fixed facts
- npm workspaces, TypeScript 7, ESM, strict, `isolatedModules` (use `export type`). Extensionless relative imports.
  Server runs via tsx; `npm run typecheck`, `npm test` (vitest) at the root. Node 26, `node:sqlite` built in.
- Contracts: `packages/contracts/src/*` (types, events, commands, constants incl. RULES, CITY_MAP). OWNER: main.
- Server ports and state: `apps/server/src/domain.ts`. OWNER: main. Implement these interfaces exactly.
- Keys are in `.env` (never print, log, commit or send to the browser). `config` in `apps/server/src/config.ts`.
- Drex facts: `.swarm/findings/drex-sdk.md`. Meta facts: `.swarm/findings/meta-provider.md`. NDI: `.swarm/findings/ndi.md`.
  Verified live: POST https://console.nace.ai/v1/systemone returns `{model, answers:{k:{type,choice,confidence,probabilities}}, usage, evaluation_time_ms, request_id}` in ~0.3 s.
- Server port 3001, web 5173 (Vite proxies /socket.io, /api, /health). If you start processes, kill them by PID when done.
- Honesty rules (never cut): a value is labeled Drex only if Drex was really called; fallbacks are labeled `fallback`;
  missing provider data is `null`, never invented; NPC decision input contains only that NPC's own memory.

## Game rules (authoritative numbers are in RULES)
- Mission: outlaw within 2.5 m interacts -> MISSION_COMPLETED then INCIDENT_OCCURRED; notoriety +2, missions +1, heat +12.
  A new mission spawns at the next MISSION_SPOTS entry after 20 s (spots: (47,60), (52,48), (78,23), (92,54), (32,28)).
- Perception: NPCs within 14 m with line of sight (CITY_MAP buildings block) get a DIRECT memory + NPC_OBSERVED.
- Witness chooses report | gossip | ignore. report -> message to nearest police. gossip -> message to its informant contact.
- Informant chooses relay | withhold. relay -> messages to its trader contacts.
- Police: a report delivered to police, or police directly seeing an incident, publishes a wanted bulletin (heat +30, wanted when heat >= 30).
  Police choose investigate | patrol | pursue (pursue only offered with a fresh direct sighting). In PURSUE the engine moves
  the officer at 7.5 m/s and attempts capture automatically within 2.5 m.
- Trader chooses buy | sell | hold on the subject ticker (sell only offered when it holds shares). Triggers: LISTING_OPENED,
  delivered rumor, public bulletin. NPC orders use the same settlement path as humans.
- Price: reference = 100 + 15*notoriety + 60*missions + 2*floor(survivalSeconds/5).
  survivalFactor = clamp(0.95 - 0.007*heat + 0.02*escapes, 0.15, 0.98). sentiment = clamp(1 + 0.03*netBuyUnits60s, 0.75, 1.25).
  spot = max(1, round(reference*survivalFactor*sentiment)); ask = spot; bid = max(1, floor(spot*0.97)). Captured => exactly 0.
- Trade guardrails: 1 share per order, max 10 shares per wallet per ticker, 2 s cooldown per wallet+ticker, wallets start at V$2000, integers only.
- Capture: hunter (human, role HUNTER) or police NPC; target must be WANTED, within 2.5 m, line of sight, captor cooldown 1.5 s.
  Atomic: character CAPTURED, listing HALTED_CAPTURED at 0, bounty = max(50, round(0.5*reference)) credited once to a human captor,
  events CAPTURE_SUCCEEDED, LISTING_HALTED, MARKET_PRICE_UPDATED, CHARACTER_ARCHIVED. Respawn = new character id + new ticker.
- Heat decays 1/s after 6 s without gain while no police sees the character; dropping below 30 = ESCAPE_SUCCEEDED (+1 escape).
  Heat does not cool while any officer is INVESTIGATING or PURSUING that character (otherwise a suspect standing still out-waits the dispatch delay).
- Police pacing: an officer acting on a second-hand report waits RULES.policeDispatchDelayMs (10 s) and searches at npcWalkSpeed x RULES.policeSearchPaceFactor (0.5); a direct sighting is never delayed. Measured live 2026-11-14: mission -> capture of a stationary outlaw 25.6 s scripted, 28.4 s Meta, 26.3 s Drex (one run each).
- Parked cars: CITY_MAP.cars (26, from packages/contracts) are solid. Players and NPCs stop at their footprints exactly as at buildings (server-side, with sliding), A* routes around them, and they never block line of sight. Moving traffic and pedestrians in the renderers are cosmetic only. Bridge (Godot) clients are not checked against them.
- Hunters have an avatar and wallet but no ticker. Spectators have neither.
- Tickers are deterministic: first 3 letters of the nickname uppercased (pad with X) + "-" + 2-digit room counter, e.g. ACE-01.
- Idempotency: every mutating command carries commandId; the engine caches the ack per (playerId, commandId) and returns it with duplicate:true.
- Director: the room creator (and anyone presenting DEMO_DIRECTOR_SECRET) may send demo.* commands.

## NPC roster (ids are stable; positions in metres)
| id | name | role | position | persona | contacts |
|---|---|---|---|---|---|
| npc_w1 | Marisol Vega | WITNESS | (40,64) | dock fisher, civic-minded, trusts the police | npc_i1 |
| npc_w2 | Otis Brandt | WITNESS | (55,45) | plaza vendor, distrusts police, loves to gossip | npc_i2 |
| npc_i1 | Lena Okoye | INFORMANT | (33,52) | sells tips to traders for a cut | npc_t1, npc_t2 |
| npc_i2 | Dex Marlowe | INFORMANT | (62,30) | cautious, relays only what he believes | npc_t2 |
| npc_t1 | Priya Castellan | TRADER | (17,31) | momentum trader, risk-seeking | — |
| npc_t2 | Rook Halvorsen | TRADER | (30,41) | risk-averse, sells on bad news | — |
| npc_p1 | Sgt. Imani Duarte | POLICE | patrol (32,28)->(62,28)->(62,44)->(62,28)->(32,28)->(32,44) (kept out of sight of the dock job so police learn of it through the witness) | by the book | — |
| npc_p2 | Ofc. Cole Renner | POLICE | patrol (92,28)->(92,54)->(62,54)->(62,28) | aggressive | — |
Traders have V$2000 wallets. Roads are the gaps between CITY_MAP buildings.

## Ownership (edit ONLY your own paths; ask the owner for anything else)
- ENGINE: `apps/server/src/engine/**`, `rooms/**`, `transport/**`, `demo/**`, `index.ts`, `apps/server/test/**`
- BRAINS: `apps/server/src/agents/**` (MemoryStore, role logic, trace store) — implements MemoryPort + BrainsPort
- DECISIONS: `apps/server/src/decisions/**` — DrexProvider, MetaProvider, scripted policy hook, Scheduler (SchedulerPort)
- ECONOMY: `apps/server/src/economy/**` — EconomyPort
- STORE: `apps/server/src/persistence/**`, `metrics/**`, `replay/**` — PersistencePort, MetricsPort, replay verify
- WEB: `apps/web/src/**` except `scene/**`
- CITY2D: `apps/web/src/scene/canvas/**`   CITY3D: `apps/web/src/scene/three/**`
Each module exports a factory from its `index.ts` (e.g. `createEconomy(): EconomyPort`). Unit tests live beside the code (`*.test.ts`).

## Talking to each other (required)
- Use SendMessage to reach peers directly (call ListAgents to find them; `main` is the orchestrator). Ask instead of guessing
  an interface; tell consumers the moment you change or finish an export.
- Also append to `.swarm/board/<YOUR-ROLE>.md`: exported API with signatures, assumptions, open questions, status. Read the
  other board files before integrating and again before you finish.
- Finish with: typecheck clean for your paths, your tests passing, a short handoff on the board. Do not commit; main commits.

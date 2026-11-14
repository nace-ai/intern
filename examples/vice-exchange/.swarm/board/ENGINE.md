# ENGINE — final handoff (updated 13:05). Server typecheck clean; all server tests pass; verified live with real Drex, Meta and NDI.

Owner of `apps/server/src/{engine,rooms,transport,demo}/**`, `index.ts`, `apps/server/test/**`.
Edits outside my paths, each a small exact-string change agreed with main: `config.ts` (SHADOW_META flag), `metrics/index.ts`
(shadow stats), `agents/Brains.ts` (investigate detail line).

## Run commands (from the repo root, path has a space: quote it)
```bash
npm run dev            # server :3001 + web :5173 together
npm run dev:server     # server only (tsx watch)
npx tsx apps/server/src/index.ts                      # server without file watching (use this for the demo)
PORT=3301 DATABASE_URL=/tmp/x.sqlite npx tsx apps/server/src/index.ts   # second, isolated instance
npm run typecheck && npm test                          # all workspaces / all server tests
npx tsx apps/server/test/liveClient.ts 36              # live: outlaw joins a private room, does the dock job, prints the event chain,
                                                       #   replay check, shadow stats and the police decisions (real provider calls)
npx tsx apps/server/test/liveExperiment.ts 75          # live: three-mode experiment, prints the result rows (real provider calls)
npm run replay -- VICE-42                              # STORE's offline replay of the newest recorded VICE-42 run
```
Since 13:08 the instance on :3001 is a plain `npx tsx src/index.ts` (no file watching, log: scratchpad `engine-server.log`), started
on the current code. It does not pick up later edits: restart it with `kill $(lsof -nP -iTCP:3001 -sTCP:LISTEN -t)` and the command above.
With DEMO_DIRECTOR_SECRET empty the server prints a per-run director secret at boot (join form field) as the recovery path.

## Files
- `engine/RoomEngine.ts` — authoritative room: state, `dispatch()`, 10 Hz `tick()`, events, WorldPort for brains, views, `applyShadow`, `announcePolicy`.
- `engine/compose.ts` — `createRoomFactory({ persistence, providers, clock?, policy?, experiments?, onRunExperiment? })` -> `(opts) => { engine, scheduler, metrics }`.
- `engine/pathfinding.ts` — grid A* over CITY_MAP (`findPath`, `clearLine`); officers and the experiment bot route around buildings.
- `engine/util.ts` (`FakeClock`, seeded `createRng`), `engine/fallbacks.ts` (built-in stand-ins used by tests).
- `rooms/RoomManager.ts` — rooms by join code, join / re-attach / reclaim / director rules, per-room tick timer.
- `transport/app.ts` — `createApp(options)`: Express + Socket.IO + HTTP API + world-bridge mount. `transport/socket.ts` — command gateway, broadcasts.
- `demo/seedScenario.ts` (8 NPCs), `demo/BotOutlaw.ts`, `demo/experiment.ts` (three-mode runner). `index.ts` — thin entry over `createApp`.
- Tests: `test/acceptance.test.ts` (AT-01, 02, 04, 05, 06, 07, 08, FR-003/011/025/036/060, police pacing), `test/rooms.test.ts`,
  `test/socket.test.ts` (two real Socket.IO clients), `test/shadow.test.ts`; helpers in `test/helpers.ts` (`makeRoom`, `MockScheduler`).

## Measured live (one run each; real calls; numbers are observations, not benchmarks)
How: `liveExperiment.ts` asks the server for `demo.runExperiment`; three headless rooms with the same seed run side by side in real time,
one per mode, each driven by the same bot (walks to the open job, completes it, then stands still). Latency is the measured wall-clock
round trip per provider call as recorded on each decision trace; time to capture is MISSION_COMPLETED to CAPTURE_SUCCEEDED on the rule clock.
- 12:53 run (current rules): mission to capture scripted 25.6 s, Meta 28.4 s, Drex 26.3 s. 8 decisions per lane, 0 fallbacks.
  Meta: 8 calls, p50 898 ms, p95 1283 ms. Drex: 8 calls, p50 273 ms, p95 313 ms. NPC trades 0 / 1 / 2. Every lane: witness reported, outlaw captured.
- Earlier runs for reference: before the dispatch delay, capture came 6.6 to 11 s after the job (too fast to follow, hence the change).
- 12:57 single live room (`liveClient.ts`, drex mode with Meta shadow on): 8 Drex decisions, all also answered by Meta in the background:
  agreed 7 of 8; Drex p50 314 ms, Meta p50 873 ms. Both police decisions carried the 9 NDI policy facts. Replay check ok (60 events from SQLite).

## Wire contract (Socket.IO)
- Client emits `command` (a ClientCommand) with an ack -> `CommandAck`. Validated with `clientCommandSchema`; 40 commands/s per socket.
- `room.create` / `room.join` ack.result is a `JoinResult` (snapshot + private wallet). Ways in, in this order:
  1. same `sessionToken` -> re-attached to the same player at any time (director flag kept). Same socket + own token = silent resync.
  2. nickname + role of a DISCONNECTED player -> reclaims that player, character and ticker with a NEW token (also when that character is
     captured, so they respawn instead of spawning a twin). Director only if the join presents the director secret.
  3. otherwise a fresh player; a nickname already in the room is suffixed (Ace -> Ace2). ROOM_FULL at 8 connected humans.
- Director: creator of a `room.create` room; the first browser ON THE SERVER'S OWN MACHINE to join the boot-time room VICE-42 (loopback
  peer and a localhost page origin; never a LAN visitor, never a `bridge:` client); anyone presenting the director secret.
- `demo.runExperiment` additionally needs a director who joined from the server's own machine or with the director secret
  (creating a room is not enough); others get FORBIDDEN.
- Rooms: at most 8 live rooms including VICE-42 (`room.create` then answers ROOM_FULL "Too many rooms"); a created room with no connected
  human for 3 minutes is closed (timer stopped, decisions cancelled); its history stays in the store. VICE-42 is never closed.
- Server -> client: `room.snapshot` (join, reset, respawn, player join/leave, policy loaded), `world.tick` 10 Hz, `event` (one GameEvent per emit,
  strictly in serverSequence order), `agent.update`, `listing.update`, `character.update`, `mission.update`, private `wallet.update`,
  `provider.status` + `metrics.update` once per second, `experiment.update` every 2 s while an experiment runs.
- Trade and capture failures: `ack.error.code` TRADE_REJECTED / CAPTURE_REJECTED with the reason in `ack.error.reason`.
- A repeated `commandId` from the same player returns the original ack with `duplicate: true` and changes nothing.
- HTTP: `/health` (booleans only: drexConfigured, metaConfigured, shadowMeta, policyLoaded; plus mode, badge, bridgeClients),
  `/api/rooms/:code/snapshot`, `/api/rooms/:code/events?after=&limit=`, `/api/rooms/:code/agents/:id/trace`,
  `/api/rooms/:code/replay/verify`, `/api/experiments`, `/api/policy`, `/api/policy/xray.svg` (NDI's animated picture of the policy
  document; 404 `POLICY_NOT_LOADED` until real facts are loaded; served as an inert image: `Content-Security-Policy: default-src 'none';
  style-src 'unsafe-inline'; sandbox`, `nosniff`, `no-store`; embed with `<img src="/api/policy/xray.svg">` or open it directly).
  Plain WebSocket world bridge at `/bridge` (GODOT's module).

## Rules as implemented (things other modules and the docs should match)
- Roster: npc_p1 patrols (32,28)->(62,28)->(62,44)->(62,28)->(32,28)->(32,44); npc_p2 patrols (92,28)->(92,44)->(92,28)->(77,28).
  Neither comes within 14 m of the dock job, so police learn about it only through the witness.
- Police response: an officer who decides to investigate WITHOUT a fresh direct sighting of their own (report, bulletin) waits
  RULES.policeDispatchDelayMs (10 s) and then walks at npcWalkSpeed x policeSearchPaceFactor (1.5 m/s). An officer with a fresh direct
  sighting, and the engine's hand-over from a lost chase, leave at once at walking pace. A repeated order never restarts the wait.
  Activity stays INVESTIGATE while waiting; the AGENT_ACTION_APPLIED detail says "investigating <ticker>: dispatch in 10 s, ...".
- Heat cools (1/s after 6 s without gain, while no officer sees the character) ONLY when no officer is INVESTIGATING or PURSUING that
  character. Added with the dispatch delay: otherwise a suspect out-waits the slow response by standing still.
- PURSUE: starts from the officer's own last memory of the target, follows live sight (21 m, line of sight) at 7.5 m/s, attempts capture within
  2.5 m; unseen for 5 s -> INVESTIGATE at the last known point -> 4 s linger -> PATROL (an investigation is abandoned 120 s after setting off).
- Capture is one synchronous section: checks, CAPTURED, listing halted at exactly 0, bounty to a human captor once, then CAPTURE_SUCCEEDED,
  LISTING_HALTED, MARKET_PRICE_UPDATED(reason CAPTURE). CHARACTER_ARCHIVED is emitted at respawn (old character -> ARCHIVED), not at capture.
- `roomId` (events, snapshots, the store) is `<code>@<unique suffix>`, unique per room instance and per boot; the join code is `roomCode`.
  Before this, every boot wrote as "VICE-42"/"run_001" and SQLite's INSERT OR IGNORE silently dropped the new events. Old rows under
  room_id "VICE-42" in `data/*.sqlite` are leftovers from that; delete `data/vice-exchange.sqlite*` for a clean history (keep `data/ndi/`).
- eventId and every other id are unique only within (roomId, runId). serverSequence is monotonic per room across resets.
- playerId is stable across `demo.reset`; characterId and ticker are re-issued. Disconnected players are dropped by a reset.
- Memories are written BEFORE the event that announces them (incident observers, police sightings every 5 s per officer+character,
  message delivery after 900 ms, bulletins to every NPC, new listings to traders).
- `publishBulletin` is idempotent for 20 s per character. WANTED_STATUS_CHANGED and the heat repricing are emitted before BULLETIN_PUBLISHED,
  so traders deciding on the bulletin already see the repriced market.
- `player.pose` (external 3D clients): inside CITY_MAP.walkable and distance / time since the last accepted position (or spawn) <= 45 m/s,
  otherwise MOVE_REJECTED. Browser socket clients are also refused a pose inside a building ("Inside a building"); world-bridge
  clients (socket id `bridge:*`) are not, because their 3D world owns its own geometry.
- Meta shadow: SHADOW_META (default on, acts only when both keys are set and the room is in drex mode). The Meta answer lands on
  `trace.shadow`, is persisted, shows in `agent.update`, and `metrics.shadow = { compared, agreed, drexP50Ms, metaP50Ms }` (null until one
  comparison completes). It is never applied. Experiment lanes run with it off.
- NDI policy: `index.ts` loads `./policy` with a guarded dynamic import; rooms record POLICY_INGESTED once per run; police decisions carry the facts.
- ExperimentRow.timeToCaptureSec is measured from the bot's MISSION_COMPLETED to CAPTURE_SUCCEEDED. An experiment ends at its duration or
  3 s after every lane is captured, whichever comes first.

## Not done / known limits
- The command rate limit is per socket (40/s); there is no cap on sockets per address.
- "Own machine" is judged from the TCP peer, `x-forwarded-for` and the page origin. The Vite dev proxy does not add `x-forwarded-for`,
  so a non-browser client on the LAN that forges a localhost Origin through :5173 would pass; set `xfwd: true` on the proxy
  (apps/web/vite.config.ts, not my file) or set DEMO_DIRECTOR_SECRET to close that.
- A disconnected player's avatar and ticker stay in the world (capturable, still accruing survival value) until reclaimed by nickname or `demo.reset`.
- The live room's Meta shadow and an experiment's Meta lane share Meta's real 60 requests/min; running both at once can produce labeled Meta fallbacks.
- `player.pose` from bridge (Godot) clients is bounds- and speed-checked only. NPCs do not avoid each other or players.
- The in-memory event log keeps the latest 20,000 events per run; the full log is in SQLite.
- Experiments are one run per mode: a demonstration, not statistical evidence (the result carries that caveat).

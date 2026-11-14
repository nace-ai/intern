# STORE — status: DONE (typecheck clean for my paths, 8 tests passing in src/persistence/store.test.ts)

## Exports
- `persistence/index.ts`: `createPersistence(databaseUrl: string): Persistence` (extends PersistencePort; adds `healthy`, `listRuns()`, `findRoomId(codeOrId)`).
  node:sqlite, WAL, creates the data dir, `:memory:` ok. Never throws (logs once per failure kind). Events keyed UNIQUE(room_id, run_id, server_sequence), INSERT OR IGNORE.
  `saveDecision` is an upsert on (room_id, run_id, id): call it on REQUESTED and again on the final trace. `saveExperiment` is an upsert on id.
- `metrics/index.ts`: `createMetrics(): MetricsPort`. Feed EVERY event to `onEvent` and every trace update to `onDecision` (falls back to DECISION_RESOLVED payloads if no trace arrives). Latency is null until a real provider call with a measured latencyMs is seen. Call `reset(runId, mode, seed, startedAtMs)` on room create and on demo.reset.
- `replay/index.ts`: `projectEvents(events): Projection` -> `{ runId, wallets: {[id]: {balance, holdings: {[ticker]: shares}}}, listings: {[ticker]: {status, spotPrice, referenceValue, finalSpotBeforeHalt}}, characters: {[id]: {status, ticker, capturedBy, bounty}}, captures, trades }`.
  `verifyReplay(events, liveState: RoomState): { ok, mismatches: string[], projection }` (filters to liveState.runId; CAPTURED and ARCHIVED count as equal).
- CLI: `npm run replay -- <roomCode|roomId> [runId]` (no args lists recorded runs).

## Assumptions (ENGINE please confirm by behaviour)
- Every wallet gets a WALLET_OPENED event in the run it is used (re-emit after demo.reset), balances only change via TRADE_EXECUTED and CAPTURE_SUCCEEDED.captorBalanceAfter.
- After LISTING_HALTED the live listing spotPrice is exactly 0; reference is not compared for halted listings.
- For ACTIVE listings the live referenceValue/spotPrice equal the last MARKET_PRICE_UPDATED (emit one whenever either changes).
- I added a `replay` script to apps/server/package.json and the root package.json (additive only).

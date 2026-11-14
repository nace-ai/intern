// npm run replay -- <roomCode|roomId> [runId]
// Rebuilds a recorded run from SQLite without calling any model and prints the result.
import { config } from "../config";
import { createPersistence } from "../persistence/index";
import { projectEvents } from "./index";

const [roomArg, runArg] = process.argv.slice(2);
const store = createPersistence(config.databaseUrl);
try {
  const runs = store.listRuns();
  if (!roomArg) {
    console.log("Usage: npm run replay -- <roomCode|roomId> [runId]\n\nRecorded runs:");
    for (const r of runs) console.log(`  ${r.roomCode ?? r.roomId}  run=${r.runId}  events=${r.events}  last=${r.lastTime}`);
    if (runs.length === 0) console.log("  (none yet)");
    process.exit(0);
  }
  const roomId = store.findRoomId(roomArg);
  if (!roomId) { console.error(`No recorded room matches "${roomArg}".`); process.exit(1); }
  const roomRuns = runs.filter((r) => r.roomId === roomId);
  const runId = runArg ?? roomRuns[0]?.runId;
  if (!runId) { console.error(`Room ${roomArg} has no recorded runs.`); process.exit(1); }
  const events = store.loadEvents(roomId, runId);
  if (events.length === 0) { console.error(`Run ${runId} has no events.`); process.exit(1); }
  const decisions = store.loadDecisions(roomId, runId);
  const p = projectEvents(events);

  console.log(`Replay of ${roomArg} run ${runId}: ${events.length} events, ${decisions.length} recorded decisions, 0 model calls made.`);
  console.log("\nListings");
  for (const l of Object.values(p.listings)) console.log(`  ${l.ticker.padEnd(8)} ${l.status.padEnd(16)} spot V$${l.spotPrice}  reference V$${l.referenceValue}  trades ${l.trades}${l.finalSpotBeforeHalt !== null ? `  last price before halt V$${l.finalSpotBeforeHalt}` : ""}`);
  console.log("\nCharacters");
  for (const c of Object.values(p.characters)) console.log(`  ${c.characterId.padEnd(10)} ${String(c.ticker ?? "-").padEnd(8)} ${c.status}${c.capturedBy ? `  captured by ${c.capturedBy}, bounty V$${c.bounty}` : ""}`);
  console.log("\nWallets");
  for (const w of Object.values(p.wallets)) {
    const h = Object.entries(w.holdings).map(([t, s]) => `${t}x${s}`).join(" ") || "-";
    console.log(`  ${w.walletId.padEnd(12)} ${w.ownerKind.padEnd(5)} ${w.ownerId.padEnd(10)} V$${w.balance}  holdings: ${h}`);
  }
  const bySource: Record<string, number> = {};
  for (const d of decisions) if (d.decisionSource) bySource[d.decisionSource] = (bySource[d.decisionSource] ?? 0) + 1;
  console.log(`\nDecisions by source: ${JSON.stringify(bySource)}`);
} finally {
  store.close();
}

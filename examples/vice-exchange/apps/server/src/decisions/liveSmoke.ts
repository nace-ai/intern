// Manual check: one real Drex and one real Meta decision through the scheduler. Run: npx tsx src/decisions/liveSmoke.ts
import type { DecisionTrace } from "@vice/contracts";
import { config } from "../config";
import { createScheduler } from "./index";

async function main(): Promise<void> {
  const clock = { now: () => Date.now() };
  const mk = (id: string) => ({
    decisionId: id, roomId: "live", runId: "run", agentId: "npc_w1", agentName: "Marisol Vega", role: "WITNESS" as const,
    question: "Choose the most appropriate next action based ONLY on your own knowledge.",
    options: { report: "Tell city police about a directly observed incident", gossip: "Tell a nearby informant what you saw", ignore: "Do nothing until there is stronger evidence" },
    state: { role: "witness", persona: "dock fisher, civic-minded, trusts the police", observations: [{ type: "INCIDENT", what: "saw a masked courier grab a marked package at the dock", distance_m: 7, seconds_ago: 3 }], recentMessages: [] },
    scriptedAction: "report", safeAction: "ignore", triggerEventId: "evt_1", correlationId: "story_1",
    requestedAtMs: Date.now(), requestedAtSequence: 1, citations: [], dedupeKey: id,
  });
  for (const mode of ["drex", "meta"] as const) {
    const s = createScheduler({
      roomId: "live", mode, clock, naceApiKey: config.naceApiKey, naceModel: config.naceModel,
      metaApiKey: config.metaApiKey, metaBaseUrl: config.metaBaseUrl, metaModel: config.metaModel, concurrency: 4, maxCallsPerMinute: 30,
    });
    const t = await new Promise<DecisionTrace>((r) => { s.request(mk(`dec_${mode}`), r); });
    const { stateSnapshot: _s, offeredActions: _o, question: _q, ...rest } = t;
    console.log(mode, JSON.stringify(rest), s.status().badge);
  }
}
void main();

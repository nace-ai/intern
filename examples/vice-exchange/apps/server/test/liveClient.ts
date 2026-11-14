// Manual live smoke: drives a real server over Socket.IO like a browser would. Usage: tsx test/liveClient.ts [seconds]
import { io } from "socket.io-client";
import type { CommandAck, GameEvent, JoinResult } from "@vice/contracts";
const url = process.env.URL ?? "http://localhost:3001"; const secs = Number(process.argv[2] ?? 25);
const s = io(url, { transports: ["websocket"] });
const send = (c: Record<string, unknown>) => new Promise<CommandAck>((r) => s.emit("command", c, r));
let n = 0; const cid = () => `live-${Date.now()}-${++n}`;
const pos = new Map<string, { x: number; z: number }>();
s.on("world.tick", (t) => { for (const c of t.characters) pos.set(c.id, c); });
s.on("event", (e: GameEvent) => {
  const p = e.payload as Record<string, unknown>;
  const brief = ["chosenAction", "decisionSource", "latencyMs", "confidence", "kind", "summary", "spotPrice", "reason", "side", "executionPrice", "wanted", "bounty", "action", "detail", "errorType"].filter((k) => p[k] !== undefined && p[k] !== null).map((k) => `${k}=${typeof p[k] === "number" ? Math.round((p[k] as number) * 100) / 100 : String(p[k]).slice(0, 70)}`).join(" ");
  if (!["WALLET_OPENED"].includes(e.type)) console.log(`#${e.serverSequence} ${e.type} [${e.actorId}] ${brief}`);
});
const made = await send({ type: "room.create", nickname: "Ace", role: "OUTLAW", requestId: "r1" });
const j = made; const code = (made.result as JoinResult | undefined)?.roomCode ?? "VICE-42";
if (!j.ok) { console.log("join failed", j); process.exit(1); }
const jr = j.result as JoinResult; const me = jr.characterId!;
console.log("joined", jr.playerId, me, "director", jr.isDirector, "badge", jr.snapshot.providerStatus.badge, "npcs", jr.snapshot.npcs.length);
const mission = jr.snapshot.missions[0]!;
const goto = async (x: number, z: number) => { for (let i = 0; i < 200; i++) { const p = pos.get(me); if (p) { const d = Math.hypot(x - p.x, z - p.z); if (d < 1) break; await send({ type: "player.move", dirX: (x - p.x) / d, dirZ: (z - p.z) / d }); } await new Promise((r) => setTimeout(r, 100)); } await send({ type: "player.move", dirX: 0, dirZ: 0 }); };
await goto(60, 72); await goto(mission.x, 72); await goto(mission.x, mission.z);
console.log("mission ack", JSON.stringify(await send({ type: "mission.interact", missionId: mission.id, commandId: cid() })));
console.log("buy ack", JSON.stringify(await send({ type: "market.buy", ticker: jr.snapshot.listings[0]?.ticker ?? "ACE-01", qty: 1, commandId: cid() })).slice(0, 200));
await new Promise((r) => setTimeout(r, secs * 1000));
const v = await (await fetch(`${url}/api/rooms/${code}/replay/verify`)).json() as { ok: boolean; eventCount: number; mismatches: string[]; source: string };
console.log("replay verify", v.ok, v.source, v.eventCount, (v.mismatches ?? ["room not found: did the server restart?"]).slice(0, 5));
const snap = await (await fetch(`${url}/api/rooms/${code}/snapshot`)).json() as { metrics: { shadow?: unknown; latency: unknown; decisions: { bySource: unknown } }; policy: { ndiJobId: string | null; facts: unknown[] } | null };
console.log("room", code, "metrics.shadow", JSON.stringify(snap.metrics.shadow), "bySource", JSON.stringify(snap.metrics.decisions.bySource));
console.log("policy", snap.policy ? `${snap.policy.facts.length} facts, NDI job ${snap.policy.ndiJobId}` : null);
const tr = await (await fetch(`${url}/api/rooms/${code}/agents/npc_p1/trace`)).json() as { decisions: Array<{ chosenAction: string | null; decisionSource: string | null; latencyMs: number | null; shadow: { chosenAction: string | null; latencyMs: number | null; agrees: boolean | null } | null; citations: unknown[]; stateSnapshot: { department_policy?: unknown[] } }> };
for (const d of tr.decisions) console.log(`npc_p1 decision: ${d.decisionSource} ${d.chosenAction} ${d.latencyMs} ms | shadow: ${d.shadow ? `${d.shadow.chosenAction} ${d.shadow.latencyMs} ms agrees=${d.shadow.agrees}` : null} | policy facts in state: ${d.stateSnapshot.department_policy?.length ?? 0}, citations: ${d.citations.length}`);
s.close(); process.exit(0);

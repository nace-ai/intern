// Dev-only visual harness for the 3D city (not imported by the app).
// /src/scene/three/dev/index.html[?spectate=1][&noauto=1][&at=x,z][&capture=1]
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP, MISSION_SPOTS } from "@vice/contracts";
import type { AgentView, CharacterView, GameEvent, ListingView, MissionView, WorldTick } from "@vice/contracts";
import ThreeCity from "../ThreeCity";

const q = new URLSearchParams(location.search);
if (q.get("q")) localStorage.setItem("vice.city3d.quality", q.get("q")!);
const npc = (id: string, name: string, role: AgentView["role"], x: number, z: number): AgentView => ({
  id, name, role, goal: "", persona: "", x, z, activity: "IDLE", targetCharacterId: null, walletCredits: null, holdings: {}, contacts: [], memories: [],
  pendingDecisionId: null, lastDecision: null, recentDecisions: [],
});
const npcs: AgentView[] = [
  npc("npc_w1", "Marisol Vega", "WITNESS", 40, 64), npc("npc_w2", "Otis Brandt", "WITNESS", 55, 45),
  npc("npc_i1", "Lena Okoye", "INFORMANT", 33, 52), npc("npc_i2", "Dex Marlowe", "INFORMANT", 62, 30),
  npc("npc_t1", "Priya Castellan", "TRADER", 17, 31), npc("npc_t2", "Rook Halvorsen", "TRADER", 30, 41),
  npc("npc_p1", "Sgt. Imani Duarte", "POLICE", 32, 28), npc("npc_p2", "Ofc. Cole Renner", "POLICE", 92, 28),
];
const ch = (id: string, nickname: string, role: CharacterView["role"], x: number, z: number, heat: number, status: CharacterView["status"]): CharacterView => ({
  id, playerId: "p_" + id, nickname, role, ticker: role === "OUTLAW" ? nickname.slice(0, 3).toUpperCase() + "-01" : null, status, x, z, heat,
  wantedLevel: Math.floor(heat / 20), wanted: status === "WANTED", notorietyPoints: 2, completedMissions: 1, successfulEscapes: 0, spawnTimeMs: 0, captureTimeMs: null, capturedBy: null,
});
const missions: MissionView[] = MISSION_SPOTS.slice(1, 3).map((m, i) => ({ id: "m" + i, x: m.x, z: m.z, label: m.label, status: "AVAILABLE", completedByCharacterId: null }));
const listings = [{ ticker: "ACE-01", spotPrice: 187 }] as unknown as ListingView[];
const ev = (seq: number, type: string, payload: unknown): GameEvent => ({ schemaVersion: 1, eventId: "e" + seq, roomId: "r", runId: "r", serverSequence: seq, time: "", simTimeMs: 0, type, actorId: "x", causationId: null, correlationId: "c", payload } as unknown as GameEvent);
const lerpLoop = (pts: Array<[number, number]>, d: number): [number, number] => {
  const seg = pts.map((p, i) => Math.hypot(pts[(i + 1) % pts.length]![0] - p[0], pts[(i + 1) % pts.length]![1] - p[1]));
  let s = d % seg.reduce((a, b) => a + b, 0);
  for (let i = 0; i < pts.length; i++) { if (s <= seg[i]!) { const a = pts[i]!, b = pts[(i + 1) % pts.length]!, k = s / seg[i]!; return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]; } s -= seg[i]!; }
  return pts[0]!;
};

function Harness() {
  const spectate = q.has("spectate"), at = (q.get("at") ?? "62,50").split(",").map(Number) as [number, number];
  const [t, setT] = useState(0);
  const [events, setEvents] = useState<GameEvent[]>([ev(1, "ROOM_CREATED", {})]);
  useEffect(() => { const i = setInterval(() => setT((v) => v + 0.1), 100); return () => clearInterval(i); }, []);
  useEffect(() => {
    let seq = 1;
    const i = setInterval(() => {
      seq += 4;
      setEvents((e) => [...e,
        ev(seq, "AGENT_MESSAGE_SENT", { messageId: "m" + seq, senderId: "npc_w2", recipientId: "npc_i2", kind: "GOSSIP" }),
        ev(seq + 1, "AGENT_MESSAGE_SENT", { messageId: "n" + seq, senderId: "npc_i2", recipientId: "npc_t2", kind: "RELAY" }),
        ev(seq + 2, "AGENT_MESSAGE_SENT", { messageId: "o" + seq, senderId: "npc_w2", recipientId: "npc_p1", kind: "REPORT" }),
        ...(q.has("capture") && seq % 3 === 2 ? [ev(seq + 3, "CAPTURE_SUCCEEDED", { captureId: "c" + seq, characterId: "c_hunt" })] : []),
      ]);
    }, 2200);
    return () => clearInterval(i);
  }, []);
  // the outlaw strolls north-west along the street; one officer pursues, the other patrols
  const you: [number, number] = q.has("still") ? at : [at[0] + Math.sin(t * 0.5) * 5, at[1] - ((t * 2.2) % 18) + 6];
  const p1 = lerpLoop([[you[0] + 7, you[1] + 9], [you[0] - 6, you[1] + 8]], t * 3);
  const p2 = lerpLoop([[92, 28], [92, 54], [62, 54], [62, 28]], t * 3);
  const characters = useMemo(() => [ch("c_you", "Ace", "OUTLAW", you[0], you[1], 62, "WANTED"), ch("c_hunt", "Vex", "HUNTER", 70, 44, 0, "ACTIVE")], [you[0], you[1]]); // eslint-disable-line react-hooks/exhaustive-deps
  const tick: WorldTick = {
    tick: Math.round(t * 10), serverTimeMs: 0,
    characters: characters.map((c) => ({ id: c.id, x: c.x, z: c.z, heat: c.heat, status: c.status })),
    npcs: npcs.map((n) => n.id === "npc_p1" ? { id: n.id, x: p1[0], z: p1[1], activity: "PURSUE" as const, pending: false }
      : n.id === "npc_p2" ? { id: n.id, x: p2[0], z: p2[1], activity: "PATROL" as const, pending: false }
      : { id: n.id, x: n.x, z: n.z, activity: "IDLE" as const, pending: n.id === "npc_w2" && Math.floor(t / 2) % 2 === 0 }),
  };
  const [sel, setSel] = useState<string | null>("npc_w2");
  return <ThreeCity map={CITY_MAP} characters={characters} npcs={npcs} missions={missions} listings={listings} tick={tick} events={events}
    youCharacterId={spectate ? null : "c_you"} selectedId={sel} onSelect={(id) => setSel(id)} />;
}
createRoot(document.getElementById("root")!).render(<Harness />);

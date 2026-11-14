// Dev-only harness: renders CanvasCity from mock data (no server). Open /src/scene/canvas/preview.html?spectator=1
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP, MISSION_SPOTS } from "@vice/contracts";
import type { AgentView, CharacterView, GameEvent, MissionView, NpcRole, WorldTick } from "@vice/contracts";
import { CanvasCity } from "./CanvasCity";

const npc = (id: string, name: string, role: NpcRole, x: number, z: number): AgentView => ({
  id, name, role, goal: "", persona: "", x, z, activity: role === "POLICE" ? "PATROL" : "IDLE", targetCharacterId: null,
  walletCredits: null, holdings: {}, contacts: [], memories: [], pendingDecisionId: null, lastDecision: null, recentDecisions: [],
});
const NPCS: AgentView[] = [
  npc("npc_w1", "Marisol Vega", "WITNESS", 40, 64), npc("npc_w2", "Otis Brandt", "WITNESS", 55, 45),
  npc("npc_i1", "Lena Okoye", "INFORMANT", 33, 52), npc("npc_i2", "Dex Marlowe", "INFORMANT", 62, 30),
  npc("npc_t1", "Priya Castellan", "TRADER", 17, 31), npc("npc_t2", "Rook Halvorsen", "TRADER", 30, 41),
  npc("npc_p1", "Sgt. Imani Duarte", "POLICE", 32, 28), npc("npc_p2", "Ofc. Cole Renner", "POLICE", 92, 28),
];
const chr = (id: string, nickname: string, role: "OUTLAW" | "HUNTER", ticker: string | null, x: number, z: number): CharacterView => ({
  id, playerId: "p_" + id, nickname, role, ticker, status: "ACTIVE", x, z, heat: 0, wantedLevel: 0, wanted: false,
  notorietyPoints: 0, completedMissions: 0, successfulEscapes: 0, spawnTimeMs: 0, captureTimeMs: null, capturedBy: null,
});
const MISSIONS: MissionView[] = [{ id: "m1", ...MISSION_SPOTS[0]!, status: "AVAILABLE", completedByCharacterId: null }];

function Preview() {
  const spectator = new URLSearchParams(location.search).has("spectator");
  const [sel, setSel] = useState<string | null>("npc_w1");
  const [tick, setTick] = useState<WorldTick | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const chars = useRef([chr("c1", "Ace", "OUTLAW", "ACE-01", 60, 68), chr("c2", "Blaze", "HUNTER", null, 92, 28)]);
  useEffect(() => {
    let n = 0, seq = 0;
    const ev = (type: string, payload: unknown): GameEvent => ({ schemaVersion: 1, eventId: "e" + ++seq, roomId: "r", runId: "x", serverSequence: seq, time: "", simTimeMs: 0, type, actorId: "", causationId: null, correlationId: "", payload } as unknown as GameEvent);
    setEvents([ev("ROOM_CREATED", {})]);
    const id = setInterval(() => {
      n++;
      const t = n / 10;
      const ace = { x: 47 + Math.cos(t / 3) * 12, z: 60 + Math.sin(t / 3) * 7 };
      const p1 = { x: 32 + ((t * 3) % 30), z: 28 };
      const captured = n > 420;
      const heat = Math.min(100, n > 100 ? (n - 100) / 2 : 0);
      setTick({
        tick: n, serverTimeMs: n * 100,
        characters: [{ id: "c1", ...ace, heat, status: captured ? "CAPTURED" : heat >= 30 ? "WANTED" : "ACTIVE" }, { id: "c2", x: 92 - t, z: 28 + t, heat: 0, status: "ACTIVE" }],
        npcs: NPCS.map((a) => ({ id: a.id, x: a.id === "npc_p1" ? (n > 250 ? ace.x - 4 : p1.x) : a.x, z: a.id === "npc_p1" ? (n > 250 ? ace.z - 3 : p1.z) : a.z, activity: a.id === "npc_p1" && n > 250 && !captured ? "PURSUE" : a.activity, pending: (a.id === "npc_w1" && n % 80 < 25) || (a.id === "npc_t2" && n % 110 < 20) })),
      });
      NPCS[6]!.targetCharacterId = "c1";
      const add: GameEvent[] = [];
      if (n === 30) add.push(ev("MISSION_COMPLETED", { x: 47, z: 66 }), ev("INCIDENT_OCCURRED", { x: 47, z: 66 }));
      if (n === 60) add.push(ev("AGENT_MESSAGE_SENT", { senderId: "npc_w1", recipientId: "npc_i1", kind: "GOSSIP" }), ev("AGENT_ACTION_APPLIED", { agentId: "npc_w1", role: "WITNESS", action: "gossip" }));
      if (n === 85) add.push(ev("AGENT_MESSAGE_SENT", { senderId: "npc_i1", recipientId: "npc_t1", kind: "RELAY" }), ev("AGENT_MESSAGE_SENT", { senderId: "npc_i1", recipientId: "npc_t2", kind: "RELAY" }));
      if (n === 110) add.push(ev("TRADE_EXECUTED", { actorId: "npc_t2", side: "SELL", ticker: "ACE-01", executionPrice: 182 }));
      if (n === 140) add.push(ev("AGENT_MESSAGE_SENT", { senderId: "npc_w2", recipientId: "npc_p1", kind: "REPORT" }));
      if (n === 160) add.push(ev("BULLETIN_PUBLISHED", { issuedBy: "npc_p1", lastKnown: ace, ticker: "ACE-01" }));
      if (n === 421) add.push(ev("CAPTURE_SUCCEEDED", { characterId: "c1", ticker: "ACE-01", spotBeforeCapture: 214 }));
      if (add.length) setEvents((e) => [...e, ...add]);
    }, 100);
    return () => clearInterval(id);
  }, []);
  return (
    <CanvasCity map={CITY_MAP} characters={chars.current} npcs={NPCS} missions={MISSIONS} listings={[]} tick={tick} events={events}
      youCharacterId={spectator ? null : "c1"} selectedId={sel} onSelect={(id) => setSel(id)} />
  );
}
createRoot(document.getElementById("root")!).render(<Preview />);

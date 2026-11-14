// Dev-only preview for the Ambient2D layer (never imported by the app).
//
//   /src/scene/canvas/Ambient2DPreview.html             the real CanvasCity (which mounts the layer itself) fed
//                                                        with a busy mock story: job, gossip, report, relay,
//                                                        trade, bulletin, capture. ?spectator=1 shows the whole map.
//   /src/scene/canvas/Ambient2DPreview.html?solo=1      static map + layer only, fixed camera, for judging the art:
//     ?x=47&z=62&s=16  camera centre (m) and px per metre           ?ambient=0   map without the layer (A/B)
//     ?warp=40         start the scenery 40 s in                    ?freeze=1    hold it at the warp time
//     ?reduced=1       calm variant                                 ?quality=low
//     ?keep=0          open every lane (no keep-clear posts)        ?stats=1     draw cost overlay
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { CITY_MAP, MISSION_SPOTS } from "@vice/contracts";
import type { AgentView, CharacterView, GameEvent, MapView, MissionView, NpcRole, WorldTick } from "@vice/contracts";
import { CanvasCity } from "./CanvasCity";
import { drawStaticCity, PALETTE } from "./cityArt";
import createAmbient from "./ambient";
import type { Ambient, AmbientView } from "./ambient";

const Q = new URLSearchParams(location.search);
const num = (k: string, d: number): number => (Q.has(k) && Number.isFinite(Number(Q.get(k))) ? Number(Q.get(k)) : d);
const STATS = Q.has("stats");

const FREEZE = Q.has("freeze");
function makeAmbient(keep: boolean): Ambient {
  const ambient = createAmbient(CITY_MAP, num("seed", 42), {
    reducedMotion: Q.has("reduced") ? true : undefined,
    quality: Q.get("quality") === "low" ? "low" : "high",
    keepClear: keep ? undefined : [],
  });
  for (let i = 0, n = Math.round(num("warp", 0) * 60); i < n; i++) ambient.update(1000 / 60);
  return ambient;
}

let cost = 0;
function timed(fn: () => void): void { const a = performance.now(); fn(); cost += performance.now() - a; }
function stats(ctx: CanvasRenderingContext2D, frames: number): void {
  if (!STATS) return;
  ctx.save();
  ctx.font = "600 13px ui-monospace, Menlo, monospace"; ctx.fillStyle = "#ffffff"; ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(`ambient ${(cost / Math.max(1, frames)).toFixed(2)} ms/frame`, 10, 10);
  ctx.restore();
}

// ------------------------------------------------------------------ solo: static map + layer, fixed camera
function Solo() {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const canvas = ref.current!, ctx = canvas.getContext("2d")!;
    const map: MapView = CITY_MAP, ambient = makeAmbient(Q.get("keep") !== "0");
    const layer = document.createElement("canvas");
    layer.width = map.width * 12; layer.height = map.height * 12;
    drawStaticCity(layer.getContext("2d")!, map, 12);
    const view: AmbientView = { x: 0, z: 0, scale: 1, width: 0, height: 0 };
    let raf = 0, last = performance.now(), frames = 0;
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const r = canvas.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.floor(r.width)), h = Math.max(1, Math.floor(r.height));
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) { canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr); }
      const fit = Math.min(w / map.width, h / map.height);
      view.scale = num("s", fit); view.x = num("x", map.width / 2); view.z = num("z", map.height / 2); view.width = w; view.height = h;
      if (!FREEZE) ambient.update(now - last);
      last = now; frames++;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = PALETTE.void; ctx.fillRect(0, 0, w, h);
      ctx.drawImage(layer, (0 - view.x) * view.scale + w / 2, (0 - view.z) * view.scale + h / 2, map.width * view.scale, map.height * view.scale);
      if (Q.get("ambient") !== "0") timed(() => { ambient.drawUnder(ctx, view); ambient.drawOver(ctx, view); });
      stats(ctx, frames);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} style={{ width: "100%", height: "100%", display: "block", background: PALETTE.void }} />;
}

// ------------------------------------------------------------------ city: the real renderer, a busy mock feed
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
const MISSIONS: MissionView[] = [{ id: "m1", ...MISSION_SPOTS[1]!, status: "AVAILABLE", completedByCharacterId: null }];

function City() {
  const spectator = Q.has("spectator");
  const [tick, setTick] = useState<WorldTick | null>(null);
  const [events, setEvents] = useState<GameEvent[]>([]);
  const chars = useRef([chr("c1", "Ace", "OUTLAW", "ACE-01", 47, 60), chr("c2", "Rio", "HUNTER", null, 58, 52), chr("c3", "Nova", "OUTLAW", "NOV-02", 62, 40)]);

  // mock feed: one compressed causal story (job -> witness -> gossip/report -> relay -> trade -> bulletin -> capture)
  useEffect(() => {
    let n = 0, seq = 0;
    const at = num("busy", 8);
    const ev = (type: string, payload: unknown): GameEvent => ({ schemaVersion: 1, eventId: "e" + ++seq, roomId: "r", runId: "x", serverSequence: seq, time: "", simTimeMs: 0, type, actorId: "", causationId: null, correlationId: "", payload } as unknown as GameEvent);
    setEvents([ev("ROOM_CREATED", {})]);
    NPCS[6]!.targetCharacterId = "c3";
    const id = setInterval(() => {
      n++;
      const t = n / 10, k = n - at;
      const ace = { x: 47 + Math.cos(t / 3) * 3, z: 61 + Math.sin(t / 3) * 2 };
      const nova = { x: 60 - Math.min(6, t), z: 40 + Math.sin(t / 2) * 1.5 };
      const captured = k >= 16;
      setTick({
        tick: n, serverTimeMs: n * 100,
        characters: [
          { id: "c1", ...ace, heat: Math.min(64, 18 + t * 4), status: t > 3 ? "WANTED" : "ACTIVE" },
          { id: "c2", x: 58 - Math.min(5, t * 1.5), z: 52 - Math.min(8, t * 2), heat: 0, status: "ACTIVE" },
          { id: "c3", ...nova, heat: 70, status: captured ? "CAPTURED" : "WANTED" },
        ],
        npcs: NPCS.map((a) => ({
          id: a.id, x: a.id === "npc_p1" ? Math.min(52, 32 + t * 5) : a.x, z: a.id === "npc_p1" ? Math.min(39, 28 + t * 3) : a.z,
          activity: a.id === "npc_p1" && !captured ? "PURSUE" : a.activity,
          pending: a.id === "npc_w2" || a.id === "npc_t1" || (a.id === "npc_i2" && n % 30 < 18) || (a.id === "npc_p2" && n % 40 < 22),
        })),
      });
      const add: GameEvent[] = [];
      if (k === 0) add.push(ev("MISSION_COMPLETED", { x: ace.x, z: ace.z }), ev("INCIDENT_OCCURRED", { x: ace.x, z: ace.z }));
      if (k === 3) add.push(ev("AGENT_ACTION_APPLIED", { agentId: "npc_w1", role: "WITNESS", action: "gossip" }), ev("AGENT_MESSAGE_SENT", { senderId: "npc_w1", recipientId: "npc_i1", kind: "GOSSIP" }));
      if (k === 5) add.push(ev("AGENT_MESSAGE_SENT", { senderId: "npc_w2", recipientId: "npc_p2", kind: "REPORT" }));
      if (k === 8) add.push(ev("AGENT_ACTION_APPLIED", { agentId: "npc_i1", role: "INFORMANT", action: "relay" }), ev("AGENT_MESSAGE_SENT", { senderId: "npc_i1", recipientId: "npc_t1", kind: "RELAY" }), ev("AGENT_MESSAGE_SENT", { senderId: "npc_i1", recipientId: "npc_t2", kind: "RELAY" }));
      if (k === 11) add.push(ev("TRADE_EXECUTED", { actorId: "npc_t2", side: "SELL", ticker: "ACE-01", executionPrice: 182 }));
      if (k === 12) add.push(ev("BULLETIN_PUBLISHED", { issuedBy: "npc_p2", lastKnown: ace, ticker: "ACE-01" }));
      if (k === 16) add.push(ev("CAPTURE_SUCCEEDED", { characterId: "c3", ticker: "NOV-02", spotBeforeCapture: 214 }));
      if (add.length) setEvents((e) => [...e, ...add]);
    }, 100);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <CanvasCity map={CITY_MAP} characters={chars.current} npcs={NPCS} missions={MISSIONS} listings={[]} tick={tick} events={events}
        youCharacterId={spectator ? null : "c1"} selectedId={Q.get("select")} onSelect={() => {}} />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(Q.has("solo") ? <Solo /> : <City />);

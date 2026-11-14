// UDP fast path for native world clients (the Godot city). OWNER: UDP.
// The WebSocket at /bridge stays the reliable channel (join, commands, acks, events, snapshot, wallet).
// UDP carries only what is high-rate and loss-tolerant:
//   client -> server  <32 hex token>{"q":seq,"x":..,"z":..,"h":heading,"f":flags}   pose, up to 30 Hz
//                     <32 hex token>{"hello":1}                                       address announcement / keep-alive
//   server -> client  {"q":seq,"t":serverTimeMs,"hz":30,"c":[[id,x,z,heat,status,h,f]...],"n":[[id,x,z,activity,pending,stunned]...]}
// A datagram is trusted for nothing: the token only selects a session that already joined over WebSocket, and the
// pose goes through the same validated player.pose path (schema + the engine's bounds and speed checks) via `pose`.
// 30 Hz from a 10 Hz engine: poses from pose-driven players land in room state immediately, so sampling the world at
// 30 Hz is real data for them; NPCs and server-integrated walkers only change on engine ticks, so between ticks
// they are dead-reckoned from their last measured velocity (capped at EXTRAPOLATE_MAX_MS, never across a jump).
import { createSocket, type RemoteInfo, type Socket } from "node:dgram";
import { randomBytes } from "node:crypto";
import type { WorldTick } from "@vice/contracts";

export const UDP_STATE_HZ = 30;
const STATE_MS = Math.round(1000 / UDP_STATE_HZ);
const MAX_DATAGRAM = 1200;
const TOKEN_HEX = 32;
const RATE = { perSecond: 90, burst: 120 };
const ADDRESS_TTL_MS = 5000;
const EXTRAPOLATE_MAX_MS = 120;
const JUMP_M = 6;

export const POSE_FLAG = { inCar: 1, airborne: 2 } as const;

export interface UdpView { tick: WorldTick; characterId: string | null }
export interface UdpOptions {
  /** UDP port to bind; 0 = ephemeral (tests). Resolved lazily so it can follow the HTTP port. */
  port: () => number;
  /** Feed one pose through the validated player.pose path. Returns whether the server accepted it. */
  pose: (key: number, x: number, z: number) => boolean;
  /** Current world state for the room this session is in, or null when it is in no room. */
  view: (key: number) => UdpView | null;
  /** Injected for tests. */
  now?: () => number;
  disabled?: boolean;
}
export interface UdpStats { enabled: boolean; port: number | null; clients: number; posesIn: number; posesRejected: number; dropped: number; statesOut: number }
export interface UdpChannel {
  /** Called when a bridge session has joined a room. Returns what to send it over WebSocket, or null if UDP is off. */
  register(key: number): Promise<{ t: "udp"; port: number; token: string; hz: number } | null>;
  unregister(key: number): void;
  clients(): number;
  stats(): UdpStats;
  /** Send one state round now (tests). */
  flush(): void;
  close(): void;
}

interface Peer {
  key: number; token: string;
  address: string | null; port: number; seenAt: number;
  lastSeqIn: number; seqOut: number;
  tokens: number; refilledAt: number;
  heading: number; flags: number;
}
interface Track { x: number; z: number; vx: number; vz: number; at: number; changedAt: number }

const r2 = (v: number): number => Math.round(v * 100) / 100;

export function createUdpChannel(opts: UdpOptions): UdpChannel {
  const now = opts.now ?? Date.now;
  const peers = new Map<number, Peer>();
  const byToken = new Map<string, Peer>();
  const tracks = new Map<string, Track>();
  let socket: Socket | null = null;
  let boundPort: number | null = null;
  let binding: Promise<number | null> | null = null;
  let failed = !!opts.disabled;
  let timer: ReturnType<typeof setInterval> | null = null;
  const st = { posesIn: 0, posesRejected: 0, dropped: 0, statesOut: 0 };

  function onMessage(buf: Buffer, rinfo: RemoteInfo): void {
    if (buf.length > MAX_DATAGRAM || buf.length < TOKEN_HEX + 2) { st.dropped += 1; return; }
    const peer = byToken.get(buf.toString("latin1", 0, TOKEN_HEX));
    if (!peer) { st.dropped += 1; return; } // unknown token: silently ignored, nothing is echoed back
    const t = now();
    peer.tokens = Math.min(RATE.burst, peer.tokens + ((t - peer.refilledAt) / 1000) * RATE.perSecond); peer.refilledAt = t;
    if (peer.tokens < 1) { st.dropped += 1; return; }
    peer.tokens -= 1;
    let msg: unknown;
    try { msg = JSON.parse(buf.toString("utf8", TOKEN_HEX)); } catch { st.dropped += 1; return; }
    if (!msg || typeof msg !== "object") { st.dropped += 1; return; }
    // The address is (re)learned from any well-formed datagram that carries the session's token.
    peer.address = rinfo.address; peer.port = rinfo.port; peer.seenAt = t;
    const m = msg as Record<string, unknown>;
    if (m.hello !== undefined) return;
    const q = m.q, x = m.x, z = m.z;
    if (typeof q !== "number" || typeof x !== "number" || typeof z !== "number" || !Number.isFinite(q) || !Number.isFinite(x) || !Number.isFinite(z)) { st.dropped += 1; return; }
    if (q <= peer.lastSeqIn) { st.dropped += 1; return; } // out of order or duplicate
    peer.lastSeqIn = q;
    if (typeof m.h === "number" && Number.isFinite(m.h)) peer.heading = r2(m.h);
    if (typeof m.f === "number" && Number.isFinite(m.f)) peer.flags = (m.f | 0) & 3;
    st.posesIn += 1;
    let ok = false;
    try { ok = opts.pose(peer.key, x, z); } catch (err) { console.error("[udp] pose failed", err); }
    if (!ok) st.posesRejected += 1;
  }

  function bind(): Promise<number | null> {
    if (failed) return Promise.resolve(null);
    if (boundPort !== null) return Promise.resolve(boundPort);
    if (binding) return binding;
    binding = new Promise<number | null>((resolve) => {
      const s = createSocket("udp4");
      s.once("error", (err) => {
        // Port taken (another instance on this machine) or no permission: stay on WebSocket only.
        console.warn(`[udp] fast path disabled: ${(err as Error).message}`);
        failed = true; try { s.close(); } catch { /* never bound */ }
        resolve(null);
      });
      s.on("message", onMessage);
      let want = 0;
      try { want = opts.port(); } catch { want = 0; }
      s.bind(want, "0.0.0.0", () => {
        s.removeAllListeners("error");
        s.on("error", (err) => console.warn("[udp] socket error", (err as Error).message));
        socket = s; boundPort = s.address().port;
        timer = setInterval(flush, STATE_MS); timer.unref?.();
        resolve(boundPort);
      });
    });
    return binding;
  }

  /** Position to send for one entity: measured if it just changed, dead-reckoned between engine ticks otherwise. */
  function sample(id: string, x: number, z: number, t: number): [number, number] {
    const tr = tracks.get(id);
    if (!tr) { tracks.set(id, { x, z, vx: 0, vz: 0, at: t, changedAt: t }); return [x, z]; }
    if (x !== tr.x || z !== tr.z) {
      const dt = t - tr.changedAt, dx = x - tr.x, dz = z - tr.z;
      const jump = Math.hypot(dx, dz) > JUMP_M || dt <= 0 || dt > 400;
      tr.vx = jump ? 0 : dx / dt; tr.vz = jump ? 0 : dz / dt;
      tr.x = x; tr.z = z; tr.changedAt = t; tr.at = t;
      return [x, z];
    }
    const age = t - tr.changedAt;
    if (age > EXTRAPOLATE_MAX_MS) { tr.vx = 0; tr.vz = 0; return [x, z]; } // it stopped: no drift
    return [x + tr.vx * age, z + tr.vz * age];
  }

  function flush(): void {
    const s = socket; if (!s) return;
    const t = now();
    const headings = new Map<string, Peer>();
    const views = new Map<number, UdpView | null>();
    for (const p of peers.values()) {
      let v: UdpView | null = null;
      try { v = opts.view(p.key); } catch { v = null; }
      views.set(p.key, v);
      if (v?.characterId) headings.set(v.characterId, p);
    }
    const built = new Map<WorldTick, { c: unknown[]; n: unknown[] }>();
    for (const p of peers.values()) {
      if (!p.address || t - p.seenAt > ADDRESS_TTL_MS) continue; // only confirmed, live addresses
      const v = views.get(p.key); if (!v) continue;
      let body = built.get(v.tick);
      if (!body) {
        const c = v.tick.characters.map((ch) => {
          const [x, z] = sample(ch.id, ch.x, ch.z, t);
          const hp = headings.get(ch.id);
          return [ch.id, r2(x), r2(z), Math.round(ch.heat), ch.status, hp ? hp.heading : null, hp ? hp.flags : 0];
        });
        const n = v.tick.npcs.map((npc) => {
          const [x, z] = sample(npc.id, npc.x, npc.z, t);
          return [npc.id, r2(x), r2(z), npc.activity, npc.pending ? 1 : 0, (npc as { stunned?: boolean }).stunned ? 1 : 0];
        });
        body = { c, n }; built.set(v.tick, body);
      }
      p.seqOut += 1;
      let out = Buffer.from(JSON.stringify({ q: p.seqOut, t: v.tick.serverTimeMs, hz: UDP_STATE_HZ, c: body.c, n: body.n }));
      if (out.length > MAX_DATAGRAM) out = Buffer.from(JSON.stringify({ q: p.seqOut, t: v.tick.serverTimeMs, hz: UDP_STATE_HZ, c: body.c.slice(0, 8), n: body.n }));
      if (out.length > MAX_DATAGRAM) continue;
      s.send(out, p.port, p.address, () => { /* loss is fine */ });
      st.statesOut += 1;
    }
    if (tracks.size > 512) tracks.clear();
  }

  return {
    async register(key) {
      const port = await bind();
      if (port === null) return null;
      let p = peers.get(key);
      if (!p) {
        const t = now();
        p = { key, token: randomBytes(16).toString("hex"), address: null, port: 0, seenAt: 0, lastSeqIn: -1, seqOut: 0, tokens: RATE.burst, refilledAt: t, heading: 0, flags: 0 };
        peers.set(key, p); byToken.set(p.token, p);
      }
      return { t: "udp", port, token: p.token, hz: UDP_STATE_HZ };
    },
    unregister(key) {
      const p = peers.get(key); if (!p) return;
      peers.delete(key); byToken.delete(p.token);
    },
    clients: () => { const t = now(); let n = 0; for (const p of peers.values()) if (p.address && t - p.seenAt <= ADDRESS_TTL_MS) n += 1; return n; },
    stats: () => ({ enabled: !failed, port: boundPort, clients: [...peers.values()].filter((p) => p.address !== null).length, ...st }),
    flush,
    close() {
      if (timer) clearInterval(timer); timer = null;
      peers.clear(); byToken.clear(); tracks.clear();
      try { socket?.close(); } catch { /* already closed */ }
      socket = null; boundPort = null; binding = null;
    },
  };
}

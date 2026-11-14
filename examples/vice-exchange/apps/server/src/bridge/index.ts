// World bridge: a plain-WebSocket door for external 3D world clients (the Godot city).
// OWNER: GODOT. It is a second transport onto the SAME authoritative command path as Socket.IO:
// every message is validated with clientCommandSchema and dispatched through RoomEngine.dispatch,
// so a bridge client can do nothing a browser client cannot. It never mutates room state itself.
//
// Wire protocol (JSON text frames)
//   client -> server  { t: "cmd", n: <number>, cmd: <ClientCommand> }
//   server -> client  { t: "ack", n, ack: CommandAck }            (room.join/create acks carry a slim JoinResult, no snapshot)
//                     { t: "tick", tick, serverTimeMs, characters[], npcs[] }   10 Hz
//                     { t: "state", ... }                           2 Hz and right after join / mutating commands
//                     { t: "event", e: GameEvent }                  every authoritative event, in order
//                     { t: "bye", reason }
import type { IncomingMessage, Server as HttpServer } from "node:http";
import type { Duplex } from "node:stream";
import { WebSocketServer, type WebSocket } from "ws";
import { clientCommandSchema } from "@vice/contracts";
import type { CommandAck, GameEvent, JoinResult, RoomSnapshot, WalletView } from "@vice/contracts";
import type { Room, RoomManager } from "../rooms/RoomManager";
import { createUdpChannel, type UdpStats } from "./udp";

export const BRIDGE_PATH = "/bridge";
const TICK_MS = 100;
const STATE_EVERY_TICKS = 5;
const RATE = { perSecond: 60, burst: 120 };
const MAX_FRAME_BYTES = 8 * 1024;
const MAX_SESSIONS = 16;
// Kept local (not imported from transport/app) because app.ts loads this module dynamically.
const PRIVATE_HOST = /^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[a-z0-9-]+\.local)$/i;
export function bridgeOriginAllowed(origin: string | undefined): boolean {
  if (!origin) return true;
  try { return PRIVATE_HOST.test(new URL(origin).hostname); } catch { return false; }
}

interface Session {
  id: number;
  ws: WebSocket;
  roomCode: string | null;
  sessionToken: string | null;
  /** True when this socket acts for a player whose browser socket is still attached. */
  companion?: boolean;
  playerId: string | null;
  offEvent: (() => void) | null;
  tokens: number;
  last: number;
  ticks: number;
}

export interface WorldBridge {
  /** Number of connected bridge clients (for /health and the dashboard). */
  clients(): number;
  /** UDP fast path counters (see ./udp.ts). */
  udp(): UdpStats;
  close(): void;
}

/** Slim, renderer-oriented view of the room for one bridge player. */
export function bridgeState(snap: RoomSnapshot, you: { playerId: string | null; wallet: WalletView | null }) {
  const me = snap.players.find((p) => p.id === you.playerId) ?? null;
  return {
    t: "state" as const,
    roomCode: snap.roomCode, runId: snap.runId, mode: snap.mode, badge: snap.providerStatus.badge,
    serverTimeMs: snap.serverTimeMs, map: { name: snap.map.name, width: snap.map.width, height: snap.map.height },
    you: me ? { playerId: me.id, characterId: me.characterId, role: me.role, nickname: me.nickname } : null,
    wallet: you.wallet,
    players: snap.players,
    characters: snap.characters,
    npcs: snap.npcs.map((n) => ({
      id: n.id, name: n.name, role: n.role, x: n.x, z: n.z, activity: n.activity,
      targetCharacterId: n.targetCharacterId, pending: n.pendingDecisionId !== null,
      lastAction: n.lastDecision?.chosenAction ?? null, lastSource: n.lastDecision?.decisionSource ?? null,
    })),
    missions: snap.missions,
    listings: snap.listings.map((l) => ({
      ticker: l.ticker, characterId: l.characterId, nickname: l.nickname, status: l.status,
      spotPrice: l.spotPrice, referenceValue: l.referenceValue, bid: l.bid, ask: l.ask, currentBounty: l.currentBounty,
    })),
  };
}

export function attachWorldBridge(httpServer: HttpServer, rooms: RoomManager): WorldBridge {
  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME_BYTES });
  const sessions = new Map<WebSocket, Session>();
  let nextId = 1;

  const send = (s: Session, msg: unknown): void => {
    if (s.ws.readyState !== s.ws.OPEN) return;
    try { s.ws.send(JSON.stringify(msg)); } catch { /* socket is going away */ }
  };
  const roomOf = (s: Session): Room | undefined => (s.roomCode ? rooms.get(s.roomCode) : undefined);
  /** Player ids change on demo.reset; the opaque session token does not. */
  const playerOf = (s: Session, room: Room) => {
    const players = room.engine.state.players;
    const direct = s.playerId ? players[s.playerId] : undefined;
    if (direct && direct.sessionToken === s.sessionToken) return direct;
    const found = Object.values(players).find((p) => p.sessionToken === s.sessionToken);
    if (found) s.playerId = found.id;
    return found;
  };
  const sendState = (s: Session): void => {
    const room = roomOf(s); if (!room) return;
    const p = playerOf(s, room);
    send(s, bridgeState(room.engine.snapshot(), { playerId: p?.id ?? null, wallet: room.engine.walletView(p?.walletId ?? null) }));
  };
  const leave = (s: Session): void => {
    s.offEvent?.(); s.offEvent = null;
    const room = roomOf(s);
    if (room) {
      const p = playerOf(s, room);
      // Only mark the player gone if this bridge socket still owns the session.
      if (p && p.socketId === `bridge:${s.id}`) room.engine.disconnect(p.id);
    }
    s.roomCode = null; s.playerId = null; s.sessionToken = null;
  };

  // UDP fast path (./udp.ts): poses in, 30 Hz world state out. Everything reliable stays on this WebSocket.
  const sessionById = (key: number): Session | undefined => { for (const x of sessions.values()) if (x.id === key) return x; return undefined; };
  const udp = createUdpChannel({
    disabled: process.env.VICE_UDP === "0",
    port: () => {
      const env = process.env.UDP_PORT;
      if (env !== undefined && env !== "" && Number.isInteger(Number(env)) && Number(env) >= 0) return Number(env);
      const a = httpServer.address();
      return a && typeof a === "object" ? a.port + 1 : 3002; // default: HTTP port + 1 (3001 -> 3002)
    },
    // Same door as a WebSocket pose: schema first, then the engine's own bounds and speed checks.
    pose: (key, x, z) => {
      const s = sessionById(key); const room = s ? roomOf(s) : undefined; const p = s && room ? playerOf(s, room) : undefined;
      if (!room || !p) return false;
      const cmd = clientCommandSchema.safeParse({ type: "player.pose", x, z });
      return cmd.success && room.engine.dispatch(p.id, cmd.data, { external: true }).ok;
    },
    view: (key) => {
      const s = sessionById(key); const room = s ? roomOf(s) : undefined;
      if (!s || !room) return null;
      return { tick: room.engine.worldTick(), characterId: playerOf(s, room)?.characterId ?? null };
    },
  });
  /** Offer the UDP channel to a native client that just joined (the browser-embedded companion has no UDP). */
  const offerUdp = (s: Session): void => { void udp.register(s.id).then((m) => { if (m) send(s, m); }).catch(() => { /* stays on WebSocket */ }); };

  function onUpgrade(req: IncomingMessage, socket: Duplex, head: Buffer): void {
    let pathname = "";
    try { pathname = new URL(req.url ?? "", "http://localhost").pathname; } catch { return; }
    if (pathname !== BRIDGE_PATH) return; // not ours: Socket.IO (or nothing) handles it
    // Browsers do not apply CORS to WebSockets, so enforce the same allow-list as Socket.IO here:
    // a page on a public origin must not be able to play as a bridge client. Native clients send no Origin.
    if (!bridgeOriginAllowed(req.headers.origin) || sessions.size >= MAX_SESSIONS) { socket.destroy(); return; }
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  }
  httpServer.on("upgrade", onUpgrade);

  wss.on("connection", (ws: WebSocket) => {
    const s: Session = { id: nextId++, ws, roomCode: null, sessionToken: null, playerId: null, offEvent: null, tokens: RATE.burst, last: Date.now(), ticks: 0 };
    sessions.set(ws, s);

    ws.on("message", (data, isBinary) => {
      if (isBinary) return;
      let msg: { t?: unknown; n?: unknown; cmd?: unknown };
      try { msg = JSON.parse(String(data)) as typeof msg; } catch { return send(s, { t: "ack", n: null, ack: { ok: false, id: "", error: { code: "INVALID_COMMAND", message: "Not JSON" } } }); }
      const n = typeof msg.n === "number" ? msg.n : null;
      const reply = (ack: CommandAck): void => send(s, { t: "ack", n, ack });
      const raw = msg.cmd;
      const rawId = raw && typeof raw === "object" ? ((raw as Record<string, unknown>).commandId ?? (raw as Record<string, unknown>).requestId) : "";
      const id = typeof rawId === "string" ? rawId.slice(0, 64) : "";
      if (msg.t !== "cmd") return reply({ ok: false, id, error: { code: "INVALID_COMMAND", message: "Expected { t: 'cmd', n, cmd }" } });

      const now = Date.now();
      s.tokens = Math.min(RATE.burst, s.tokens + ((now - s.last) / 1000) * RATE.perSecond); s.last = now;
      if (s.tokens < 1) return reply({ ok: false, id, error: { code: "RATE_LIMITED", message: "Too many commands" } });
      s.tokens -= 1;

      const parsed = clientCommandSchema.safeParse(raw);
      if (!parsed.success) return reply({ ok: false, id, error: { code: "INVALID_COMMAND", message: parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ").slice(0, 300) } });
      const cmd = parsed.data;
      try {
        // Companion attach: the browser UI embeds a 3D world client that plays AS the page's own player. It presents
        // that player's session token; while the browser socket is still attached we do not take the session over
        // (wallet pushes and disconnect handling stay with the browser), we only let this socket act for the player.
        if (cmd.type === "room.join" && cmd.sessionToken) {
          const room = rooms.get(cmd.roomCode);
          const owner = room ? Object.values(room.engine.state.players).find((p) => p.sessionToken === cmd.sessionToken && p.connected && !!p.socketId && !p.socketId.startsWith("bridge:")) : undefined;
          if (room && owner) {
            leave(s);
            const full = rooms.result(room, owner.id);
            s.roomCode = room.code; s.playerId = owner.id; s.sessionToken = full.sessionToken; s.companion = true;
            s.offEvent = room.engine.onEvent((e: GameEvent) => send(s, { t: "event", e }));
            const { snapshot: _omit, ...slim } = full as JoinResult;
            reply({ ok: true, id: cmd.requestId, result: slim });
            sendState(s);
            return;
          }
        }
        if (cmd.type === "room.create" || cmd.type === "room.join") {
          leave(s);
          s.companion = false;
          const r = rooms.join(cmd, `bridge:${s.id}`);
          if (!r.ok || !r.room || !r.playerId) return reply({ ok: false, id: r.id, error: r.error });
          const room = r.room;
          const full = rooms.result(room, r.playerId);
          s.roomCode = room.code; s.playerId = r.playerId; s.sessionToken = full.sessionToken;
          s.offEvent = room.engine.onEvent((e: GameEvent) => send(s, { t: "event", e }));
          // The full snapshot is large and browser-oriented; bridge clients get the slim state instead.
          const { snapshot: _omit, ...slim } = full as JoinResult;
          reply({ ok: true, id: r.id, result: slim });
          sendState(s);
          offerUdp(s);
          return;
        }
        const room = roomOf(s);
        const p = room ? playerOf(s, room) : undefined;
        if (!room || !p) return reply({ ok: false, id, error: { code: "NOT_JOINED", message: "Join a room first" } });
        reply(room.engine.dispatch(p.id, cmd, { external: true }));
        if (cmd.type !== "player.move" && cmd.type !== "player.pose") sendState(s);
      } catch (err) {
        console.error("[bridge] command failed", cmd.type, err);
        reply({ ok: false, id, error: { code: "INTERNAL", message: "Server error" } });
      }
    });
    ws.on("close", () => { udp.unregister(s.id); leave(s); sessions.delete(ws); });
    ws.on("error", () => { /* close follows */ });
  });

  const timer = setInterval(() => {
    for (const s of sessions.values()) {
      const room = roomOf(s); if (!room) continue;
      try {
        send(s, { t: "tick", ...room.engine.worldTick() });
        if (++s.ticks % STATE_EVERY_TICKS === 0) sendState(s);
      } catch (err) { console.error("[bridge] tick failed", err); }
    }
  }, TICK_MS);
  timer.unref?.();

  return {
    clients: () => sessions.size,
    udp: () => udp.stats(),
    close: () => {
      clearInterval(timer);
      udp.close();
      httpServer.off("upgrade", onUpgrade);
      for (const s of sessions.values()) { send(s, { t: "bye", reason: "server closing" }); leave(s); try { s.ws.close(); } catch { /* already closed */ } }
      sessions.clear(); wss.close();
    },
  };
}

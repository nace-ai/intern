// World stream relay: ONE game engine (a native Open Theft Auto client started with --vice-stream) publishes its
// rendered frames here; browsers subscribe and show them in the city pane, and the browser of the SAME player may
// send keyboard and mouse input back to that client. OWNER: ONEWORLD.
//
//   publisher:  ws /stream?role=pub&token=<session token>     binary frames out: [u32 seq][f64 unix ms][JPEG]
//   subscriber: ws /stream?role=sub&token=<session token>     same player: video + control
//               ws /stream?role=sub&room=<code>               anyone: video only (first publisher in the room)
//   subscriber -> server -> publisher: JSON text input events {t:"k"|"m"|"b",...}, only from a controlling subscriber.
import type { Server as HttpServer, IncomingMessage } from "node:http";
import type { Duplex } from "node:stream";
import { WebSocketServer, type WebSocket } from "ws";
import type { RoomManager } from "../rooms/RoomManager";
import { bridgeOriginAllowed } from "./index";

export const STREAM_PATH = "/stream";
const MAX_FRAME = 1_500_000;        // bytes per video frame
const MAX_INPUT = 300;              // bytes per input message
const SLOW_SUB_BYTES = 600_000;     // a subscriber with more than this queued skips frames (newest only)
const MAX_SUBS = 16;
const INPUT_PER_SEC = 240;

export interface StreamStats { publishers: number; viewers: number; frames: number }
export interface WorldStream { stats(): StreamStats; close(): void }

interface Pub { key: string; ws: WebSocket; roomCode: string; playerId: string; frames: number; last: Buffer | null }
interface Sub { ws: WebSocket; roomCode: string; playerId: string | null; control: boolean; pub: Pub | null; inputs: number; windowAt: number }

export function attachWorldStream(httpServer: HttpServer, rooms: RoomManager): WorldStream {
  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME });
  const pubs = new Map<string, Pub>();
  const subs = new Set<Sub>();
  let frames = 0;

  /** A session token identifies one player in one room. Bots and unknown tokens are refused. */
  function whois(token: string): { roomCode: string; playerId: string } | null {
    if (!/^[0-9a-f]{16,128}$/i.test(token)) return null;
    for (const room of rooms.rooms.values()) {
      const p = Object.values(room.engine.state.players).find((x) => x.sessionToken === token && !x.isBot);
      if (p) return { roomCode: room.code, playerId: p.id };
    }
    return null;
  }
  const tell = (ws: WebSocket, msg: unknown): void => { try { ws.send(JSON.stringify(msg)); } catch { /* closed */ } };
  function pick(s: Sub): Pub | null {
    if (s.playerId) { const own = pubs.get(`${s.roomCode}:${s.playerId}`); if (own) return own; }
    for (const p of pubs.values()) if (p.roomCode === s.roomCode) return p;
    return null;
  }
  function rebind(): void {
    for (const s of subs) {
      const p = pick(s);
      if (p === s.pub) continue;
      s.pub = p;
      s.control = !!p && !!s.playerId && p.playerId === s.playerId;
      tell(s.ws, { t: "status", publishing: !!p, control: s.control, publisherPlayerId: p?.playerId ?? null });
      if (p?.last) { try { s.ws.send(p.last, { binary: true }); } catch { /* closed */ } }
    }
    for (const p of pubs.values()) tell(p.ws, { t: "viewers", n: [...subs].filter((s) => s.pub === p).length });
  }

  function onUpgrade(req: IncomingMessage, socket: Duplex, head: Buffer): void {
    let url: URL;
    try { url = new URL(req.url ?? "", "http://localhost"); } catch { return; }
    if (url.pathname !== STREAM_PATH) return; // not ours
    if (!bridgeOriginAllowed(req.headers.origin)) { socket.destroy(); return; }
    const role = url.searchParams.get("role");
    const who = whois(url.searchParams.get("token") ?? "");
    const roomCode = (who?.roomCode ?? url.searchParams.get("room") ?? "").toUpperCase();
    if ((role !== "pub" && role !== "sub") || (role === "pub" && !who) || !rooms.get(roomCode) || (role === "sub" && subs.size >= MAX_SUBS)) { socket.destroy(); return; }
    wss.handleUpgrade(req, socket, head, (ws) => {
      if (role === "pub" && who) {
        const key = `${who.roomCode}:${who.playerId}`;
        const old = pubs.get(key);
        if (old) { try { old.ws.close(4000, "replaced"); } catch { /* gone */ } }
        const pub: Pub = { key, ws, roomCode: who.roomCode, playerId: who.playerId, frames: 0, last: null };
        pubs.set(key, pub);
        ws.on("message", (data: Buffer, isBinary: boolean) => {
          if (!isBinary || data.length < 16 || data.length > MAX_FRAME) return;
          pub.frames += 1; frames += 1; pub.last = data;
          for (const s of subs) {
            if (s.pub !== pub || s.ws.readyState !== 1 || s.ws.bufferedAmount > SLOW_SUB_BYTES) continue; // newest only
            try { s.ws.send(data, { binary: true }); } catch { /* closed */ }
          }
        });
        ws.on("close", () => { if (pubs.get(key) === pub) pubs.delete(key); rebind(); });
        ws.on("error", () => { /* close follows */ });
        rebind();
        return;
      }
      const sub: Sub = { ws, roomCode, playerId: who?.playerId ?? null, control: false, pub: null, inputs: 0, windowAt: Date.now() };
      subs.add(sub);
      tell(ws, { t: "status", publishing: false, control: false, publisherPlayerId: null });
      ws.on("message", (data: Buffer, isBinary: boolean) => {
        if (isBinary || data.length > MAX_INPUT || !sub.control || !sub.pub) return; // view-only subscribers cannot send input
        const now = Date.now();
        if (now - sub.windowAt >= 1000) { sub.windowAt = now; sub.inputs = 0; }
        if (++sub.inputs > INPUT_PER_SEC) return;
        let m: { t?: unknown };
        try { m = JSON.parse(data.toString("utf8")) as { t?: unknown }; } catch { return; }
        if (m.t !== "k" && m.t !== "m" && m.t !== "b") return;
        try { sub.pub.ws.send(JSON.stringify(m)); } catch { /* publisher gone */ }
      });
      ws.on("close", () => { subs.delete(sub); rebind(); });
      ws.on("error", () => { /* close follows */ });
      rebind();
    });
  }
  httpServer.on("upgrade", onUpgrade);

  return {
    stats: () => ({ publishers: pubs.size, viewers: subs.size, frames }),
    close() {
      httpServer.off("upgrade", onUpgrade);
      for (const p of pubs.values()) { try { p.ws.close(); } catch { /* gone */ } }
      for (const s of subs) { try { s.ws.close(); } catch { /* gone */ } }
      wss.close();
    },
  };
}

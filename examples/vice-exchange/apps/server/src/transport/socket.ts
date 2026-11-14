import type { Server, Socket } from "socket.io";
import { SOCKET, clientCommandSchema } from "@vice/contracts";
import type { CommandAck, ExperimentResult } from "@vice/contracts";
import type { Room, RoomManager } from "../rooms/RoomManager";

interface Session { roomCode: string; playerId: string }

const LOOPBACK = /^(127\.\d+\.\d+\.\d+|::1|::ffff:127\.\d+\.\d+\.\d+)$/;
const LOCAL_HOST = /^(localhost|127\.\d+\.\d+\.\d+|\[::1\])$/i;
/**
 * True only for a browser on the server's own machine: the TCP peer is loopback, no proxy reports a remote client,
 * and the page itself was loaded from localhost (a LAN visitor coming through the dev proxy has a LAN origin).
 */
export function isLocal(socket: { handshake: { address: string; headers: Record<string, string | string[] | undefined> } }): boolean {
  const h = socket.handshake;
  if (!LOOPBACK.test(h.address ?? "")) return false;
  const fwd = String(h.headers["x-forwarded-for"] ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  if (fwd.some((a) => !LOOPBACK.test(a))) return false;
  const origin = h.headers.origin;
  if (typeof origin === "string" && origin) { try { return LOCAL_HOST.test(new URL(origin).hostname); } catch { return false; } }
  return true;
}
const RATE = { perSecond: 40, burst: 80 };

export function attachTransport(io: Server, rooms: RoomManager): { flush(room: Room): void; experiment(r: ExperimentResult): void; wire(room: Room): void } {
  const wired = new WeakSet<Room>();
  const sessions = new Map<string, Session>(); // socket.id -> session

  function wire(room: Room): void {
    if (wired.has(room)) return;
    wired.add(room);
    room.engine.onEvent((e) => io.to(room.code).emit(SOCKET.event, e));
  }
  function socketOf(room: Room, playerId: string): Socket | undefined {
    const sid = room.engine.state.players[playerId]?.socketId;
    return sid ? io.sockets.sockets.get(sid) : undefined;
  }
  /** Called after every tick and every command: pushes whatever changed. */
  function flush(room: Room): void {
    const e = room.engine; const d = e.drainDirty();
    if (e.playerRemap.size) {
      for (const s of sessions.values()) if (s.roomCode === room.code) s.playerId = e.playerRemap.get(s.playerId) ?? s.playerId;
      e.playerRemap.clear();
    }
    if (d.snapshot) {
      io.to(room.code).emit(SOCKET.snapshot, e.snapshot());
      for (const p of Object.values(e.state.players)) { const w = e.walletView(p.walletId); if (w) socketOf(room, p.id)?.emit(SOCKET.wallet, w); }
      return;
    }
    for (const a of d.agents) io.to(room.code).emit(SOCKET.agent, a);
    for (const l of d.listings) io.to(room.code).emit(SOCKET.listing, l);
    for (const c of d.characters) io.to(room.code).emit(SOCKET.character, c);
    for (const m of d.missions) io.to(room.code).emit(SOCKET.mission, m);
    for (const w of d.wallets) socketOf(room, w.ownerId)?.emit(SOCKET.wallet, w); // private to the owner
  }
  function onTick(room: Room): void {
    wire(room);
    io.to(room.code).emit(SOCKET.tick, room.engine.worldTick());
    flush(room);
    if (room.engine.state.tick % 10 === 0) {
      io.to(room.code).emit(SOCKET.provider, room.scheduler.status());
      io.to(room.code).emit(SOCKET.metrics, room.metrics.view());
    }
  }
  rooms.onTick = onTick;

  io.on("connection", (socket) => {
    let tokens = RATE.burst; let last = Date.now();
    socket.on(SOCKET.command, (raw: unknown, cb?: (a: CommandAck) => void) => {
      const reply = typeof cb === "function" ? cb : () => {};
      const now = Date.now();
      tokens = Math.min(RATE.burst, tokens + ((now - last) / 1000) * RATE.perSecond); last = now;
      const rawId = (raw && typeof raw === "object" ? ((raw as Record<string, unknown>).commandId ?? (raw as Record<string, unknown>).requestId) : "") as unknown;
      const id = typeof rawId === "string" ? rawId.slice(0, 64) : "";
      if (tokens < 1) return reply({ ok: false, id, error: { code: "RATE_LIMITED", message: "Too many commands" } });
      tokens -= 1;
      const parsed = clientCommandSchema.safeParse(raw);
      if (!parsed.success) return reply({ ok: false, id, error: { code: "INVALID_COMMAND", message: parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ").slice(0, 300) } });
      const cmd = parsed.data;
      try {
        if (cmd.type === "room.create" || cmd.type === "room.join") {
          const old = sessions.get(socket.id);
          const oldRoom = old && rooms.get(old.roomCode);
          // Re-sending room.join with our own token is a resync, not a leave + join.
          const resync = !!old && cmd.type === "room.join" && old.roomCode === cmd.roomCode && !!cmd.sessionToken
            && oldRoom?.engine.state.players[old.playerId]?.sessionToken === cmd.sessionToken;
          if (old && !resync) {
            if (oldRoom?.engine.state.players[old.playerId]?.socketId === socket.id) oldRoom.engine.disconnect(old.playerId);
            void socket.leave(old.roomCode); sessions.delete(socket.id);
            if (oldRoom) flush(oldRoom);
          }
          const r = rooms.join(cmd, socket.id, isLocal(socket));
          if (r.ok && r.room && r.playerId) {
            wire(r.room);
            sessions.set(socket.id, { roomCode: r.room.code, playerId: r.playerId });
            void socket.join(r.room.code);
            reply({ ok: true, id: r.id, result: rooms.result(r.room, r.playerId) });
            flush(r.room);
          } else reply({ ok: false, id: r.id, error: r.error });
          return;
        }
        const s = sessions.get(socket.id); const room = s && rooms.get(s.roomCode);
        if (!s || !room) return reply({ ok: false, id, error: { code: "NOT_JOINED", message: "Join a room first" } });
        const a = room.engine.dispatch(s.playerId, cmd);
        reply(a);
        if (cmd.type !== "player.move" && cmd.type !== "player.pose") flush(room);
      } catch (err) {
        console.error("[transport] command failed", cmd.type, err);
        reply({ ok: false, id, error: { code: "INTERNAL", message: "Server error" } });
      }
    });
    socket.on("disconnect", () => {
      const s = sessions.get(socket.id);
      if (!s) return;
      sessions.delete(socket.id);
      const room = rooms.get(s.roomCode);
      // Only mark the player gone if this socket still owns the session (a reconnect may have replaced it).
      if (room && room.engine.state.players[s.playerId]?.socketId === socket.id) room.engine.disconnect(s.playerId);
    });
  });

  return { flush, wire, experiment: (r) => io.emit(SOCKET.experiment, r) };
}

import { randomBytes, timingSafeEqual } from "node:crypto";
import { DEFAULT_ROOM_CODE, DEFAULT_SEED, RULES } from "@vice/contracts";
import type { ClientCommand, CommandAck, DecisionMode, JoinResult } from "@vice/contracts";
import type { BuiltRoom, RoomBuildOpts } from "../engine/compose";
import type { RoomEngine } from "../engine/RoomEngine";
import type { PlayerState } from "../domain";

export interface Room extends BuiltRoom { code: string; timer: NodeJS.Timeout | null; hasDirector: boolean; emptySinceMs: number | null }
/** Live rooms at once, the default room included. Each room costs a 10 Hz timer and a decision scheduler. */
export const MAX_ROOMS = 8;
/** A created room with no connected human for this long is closed. The default room never is. */
export const EMPTY_ROOM_TTL_MS = 3 * 60_000;
type JoinCmd = Extract<ClientCommand, { type: "room.create" | "room.join" }>;

export class RoomManager {
  readonly rooms = new Map<string, Room>();
  private n = 0;
  constructor(private build: (o: RoomBuildOpts) => BuiltRoom, private opts: { directorSecret: string; defaultMode: DecisionMode; tickHz: number; fugitive?: boolean }) {}
  onTick?: (room: Room) => void;

  create(code: string, seed: number, mode: DecisionMode = this.opts.defaultMode): Room {
    // roomId is unique per room instance (and so per server boot); the short code is what people type.
    // Stored history is keyed by roomId, so a restarted server never collides with an earlier VICE-42.
    const roomId = `${code}@${Date.now().toString(36)}${randomBytes(2).toString("hex")}`;
    const built = this.build({ roomId, code, seed, mode, fugitive: !!this.opts.fugitive });
    const room: Room = { ...built, code, timer: null, hasDirector: false, emptySinceMs: null };
    room.timer = setInterval(() => {
      try { room.fugitive?.step(); room.engine.tick(); this.onTick?.(room); } catch (err) { console.error("[room] tick failed", code, err); }
      if (room.engine.state.tick % 50 === 0) this.sweep();
    }, Math.round(1000 / this.opts.tickHz));
    this.rooms.set(code, room);
    return room;
  }
  ensureDefault(): Room { return this.rooms.get(DEFAULT_ROOM_CODE) ?? this.create(DEFAULT_ROOM_CODE, DEFAULT_SEED); }
  get(code: string): Room | undefined { return this.rooms.get(code.toUpperCase()); }

  /** Stops a room's timer and pending decisions and forgets it. Its recorded history stays in the store. */
  close(code: string): void {
    const room = this.rooms.get(code);
    if (!room || code === DEFAULT_ROOM_CODE) return;
    if (room.timer) clearInterval(room.timer);
    room.timer = null;
    try { room.scheduler.cancelAll(); } catch { /* already idle */ }
    this.rooms.delete(code);
  }
  /** Closes created rooms that have had no connected human for EMPTY_ROOM_TTL_MS. */
  sweep(now: number = Date.now()): void {
    for (const room of [...this.rooms.values()]) {
      if (room.code === DEFAULT_ROOM_CODE) continue;
      const occupied = Object.values(room.engine.state.players).some((p) => p.connected && !p.isBot);
      if (occupied) { room.emptySinceMs = null; continue; }
      room.emptySinceMs ??= now;
      if (now - room.emptySinceMs >= EMPTY_ROOM_TTL_MS) this.close(room.code);
    }
  }

  private secretOk(given: string | undefined): boolean {
    const want = this.opts.directorSecret;
    if (!want || !given) return false;
    // Compare bytes, not characters: a multi-byte character of the same character count must not throw.
    const a = Buffer.from(given), b = Buffer.from(want);
    return a.length === b.length && timingSafeEqual(a, b);
  }

  /** Nicknames are unique per room: a taken name gets the smallest free numeric suffix (Ace -> Ace2). */
  private uniqueNickname(want: string, players: PlayerState[]): string {
    const taken = new Set(players.map((p) => p.nickname.toLowerCase()));
    if (!taken.has(want.toLowerCase())) return want;
    for (let n = 2; n < 1000; n++) {
      const suffix = String(n); const candidate = want.slice(0, 16 - suffix.length) + suffix;
      if (!taken.has(candidate.toLowerCase())) return candidate;
    }
    return want;
  }

  /**
   * Handles room.create / room.join. Three ways in, in this order:
   *  1. the same opaque session token re-attaches to the same player (refresh, network blip), director flag kept;
   *  2. the nickname + role of a DISCONNECTED player reclaims that player, its character and its ticker with a new
   *     token. A reclaim never carries director rights by itself: only the director secret does;
   *  3. otherwise a fresh player (a taken nickname is suffixed).
   * A room code alone never grants director rights: the boot-time default room's free director slot goes only to the
   * first browser on the server's own machine (`local`); everyone else needs the director secret.
   */
  join(cmd: JoinCmd, socketId: string, local = false): CommandAck<JoinResult> & { room?: Room; playerId?: string } {
    const id = cmd.requestId;
    let room: Room | undefined; let creator = false;
    if (cmd.type === "room.create") {
      this.sweep();
      if (this.rooms.size >= MAX_ROOMS) return { ok: false, id, error: { code: "ROOM_FULL", message: "Too many rooms" } };
      // Bounded search for a free code: never loops forever, whatever the number of rooms.
      let code: string | null = null;
      for (let i = 0; i < 200 && code === null; i++) {
        const candidate = `VICE-${10 + ((this.n++ * 37 + Math.floor(Math.random() * 90)) % 90)}`;
        if (!this.rooms.has(candidate)) code = candidate;
      }
      if (code === null) return { ok: false, id, error: { code: "ROOM_FULL", message: "Too many rooms" } };
      room = this.create(code, cmd.seed ?? DEFAULT_SEED); creator = true;
    } else {
      room = this.get(cmd.roomCode);
      if (!room) return { ok: false, id, error: { code: "ROOM_NOT_FOUND", message: `No room ${cmd.roomCode}` } };
    }
    const engine = room.engine;
    const humans = Object.values(engine.state.players).filter((p) => !p.isBot);
    const secret = this.secretOk(cmd.directorSecret);
    const done = (playerId: string) => {
      room!.engine.setTrusted(playerId, secret || (local && !socketId.startsWith("bridge:")));
      return { ok: true as const, id, room, playerId, result: this.result(room!, playerId) };
    };

    // The default room has no creator: this machine's own browser directs it whenever no director is connected
    // (never a LAN visitor, never a world-bridge client). Tying the slot to "no connected director" instead of
    // "never handed out" means the presenter reopening a tab cannot leave the room without a director.
    const directorPresent = humans.some((p) => p.connected && p.isDirector);
    const localSlot = local && !socketId.startsWith("bridge:") && room.code === DEFAULT_ROOM_CODE && !directorPresent;
    if (cmd.type === "room.join") {
      const byToken = cmd.sessionToken ? humans.find((p) => p.sessionToken === cmd.sessionToken) : undefined;
      if (byToken) {
        engine.reattach(byToken.id, socketId, { isDirector: byToken.isDirector || secret });
        return done(byToken.id);
      }
      const name = cmd.nickname.toLowerCase();
      // A nickname alone reclaims a disconnected player only once the owner's own automatic token reconnect has had its
      // grace window (so a stranger cannot grab a character during a network blip). This machine's own browser and the
      // director secret may reclaim at once: that is the presenter reopening a tab.
      const graceOver = (p: PlayerState) => engine.now() - (p.disconnectedAtMs ?? 0) > RULES.reconnectGraceMs;
      const claim = humans.find((p) => !p.connected && p.role === cmd.role && p.nickname.toLowerCase() === name && (local || secret || graceOver(p)));
      if (claim) {
        // A nickname is not a credential: a reclaim directs only with the secret or as this machine's own browser.
        engine.reattach(claim.id, socketId, { sessionToken: randomBytes(24).toString("hex"), isDirector: secret || localSlot });
        return done(claim.id);
      }
    }
    if (humans.filter((p) => p.connected).length >= RULES.maxPlayersPerRoom) return { ok: false, id, error: { code: "ROOM_FULL", message: "Room is full" } };
    // The creator directs their room. The boot-time default room has no creator, so the first browser on the server's own
    // machine directs it (never a LAN visitor, never a world-bridge client). The slot is not handed out again once taken.
    const isDirector = creator || secret || localSlot;
    if (isDirector) room.hasDirector = true;
    const p = engine.addPlayer({ nickname: this.uniqueNickname(cmd.nickname, humans), role: cmd.role, isDirector, sessionToken: randomBytes(24).toString("hex"), socketId });
    return done(p.id);
  }

  result(room: Room, playerId: string): JoinResult {
    const e: RoomEngine = room.engine; const p = e.state.players[playerId]!;
    return { roomCode: room.code, playerId: p.id, sessionToken: p.sessionToken, role: p.role, characterId: p.characterId, walletId: p.walletId, isDirector: p.isDirector, snapshot: e.snapshot(), wallet: e.walletView(p.walletId) };
  }
  stop(): void { for (const r of this.rooms.values()) if (r.timer) clearInterval(r.timer); }
}

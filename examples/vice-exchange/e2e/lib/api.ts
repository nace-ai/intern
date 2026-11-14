// Server-side view of the truth: the HTTP API, a continuously polled copy of the event log, and raw Socket.IO clients.
import { io, type Socket } from "socket.io-client";
import { SOCKET } from "@vice/contracts";
import type {
  AgentView, CharacterView, ClientCommand, CommandAck, DecisionTrace, EventType, GameEvent, HumanRole, JoinResult, RoomSnapshot, WalletView,
} from "@vice/contracts";

export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export interface TraceResponse { agent: AgentView; decisions: DecisionTrace[]; messages: Array<Record<string, unknown>> }
export interface VerifyResponse {
  ok: boolean; source: string; runId: string; eventCount: number; mismatches: string[];
  projection: {
    wallets: Record<string, { walletId: string; ownerId: string; ownerKind: string; balance: number; holdings: Record<string, number> }>;
    listings: Record<string, { ticker: string; characterId: string; status: string; spotPrice: number; referenceValue: number; finalSpotBeforeHalt: number | null; trades: number }>;
    characters: Record<string, { characterId: string; playerId: string; ticker: string | null; status: string; capturedBy: string | null; bounty: number | null }>;
    captures: number; trades: number;
  };
}

export class Api {
  constructor(readonly base: string, readonly room: string) {}
  async get<T>(path: string): Promise<T> {
    const r = await fetch(this.base + path, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) throw new Error(`GET ${path} -> HTTP ${r.status}`);
    return (await r.json()) as T;
  }
  health(): Promise<Record<string, unknown>> { return this.get("/health"); }
  snapshot(): Promise<RoomSnapshot> { return this.get(`/api/rooms/${this.room}/snapshot`); }
  trace(agentId: string): Promise<TraceResponse> { return this.get(`/api/rooms/${this.room}/agents/${agentId}/trace`); }
  verify(): Promise<VerifyResponse> { return this.get(`/api/rooms/${this.room}/replay/verify`); }
  experiments(): Promise<unknown[]> { return this.get("/api/experiments"); }
  async eventsAfter(after: number): Promise<{ runId: string; lastEventSequence: number; events: GameEvent[] }> {
    return this.get(`/api/rooms/${this.room}/events?after=${after}&limit=1000`);
  }
  async character(id: string): Promise<CharacterView | undefined> { return (await this.snapshot()).characters.find((c) => c.id === id); }
  /** Every decision trace of every NPC in the current run. */
  async allTraces(): Promise<DecisionTrace[]> {
    const snap = await this.snapshot();
    const lists = await Promise.all(snap.npcs.map((n) => this.trace(n.id).then((t) => t.decisions).catch(() => [] as DecisionTrace[])));
    return lists.flat();
  }
}

/** Polls /events and keeps the whole log (the server only keeps the current run in memory; sequences keep rising across resets). */
export class EventLog {
  all: GameEvent[] = [];
  private last = 0;
  private stop = false;
  private loop: Promise<void> | null = null;
  errors = 0;
  constructor(private api: Api, private everyMs = 200) {}
  start(): void {
    this.loop = (async () => {
      while (!this.stop) { await this.pull(); await sleep(this.everyMs); }
    })();
  }
  async pull(): Promise<void> {
    try {
      for (;;) {
        const r = await this.api.eventsAfter(this.last);
        for (const e of r.events) if (e.serverSequence > this.last) { this.all.push(e); this.last = e.serverSequence; }
        if (r.events.length < 1000) break;
      }
    } catch { this.errors++; }
  }
  async close(): Promise<void> { this.stop = true; await this.loop; await this.pull(); }
  get lastSeq(): number { return this.last; }
  of<T extends EventType>(type: T, afterSeq = 0): Array<GameEvent<T>> {
    return this.all.filter((e): e is GameEvent<T> => e.type === type && e.serverSequence > afterSeq);
  }
  byId(id: string, runId?: string): GameEvent | undefined { return this.all.find((e) => e.eventId === id && (!runId || e.runId === runId)); }
  /** Resolves with the first event (already logged after `afterSeq`, or arriving later) that satisfies `pred`; null on timeout. */
  async waitFor<T extends EventType>(type: T, pred: (e: GameEvent<T>) => boolean, timeoutMs: number, afterSeq = 0): Promise<GameEvent<T> | null> {
    const end = Date.now() + timeoutMs;
    for (;;) {
      const hit = this.of(type, afterSeq).find(pred);
      if (hit) return hit;
      if (Date.now() >= end) return null;
      await sleep(60);
    }
  }
}

/** A scripted client speaking the raw socket protocol (no browser). */
export class Bot {
  join: JoinResult | null = null;
  wallet: WalletView | null = null;
  events: GameEvent[] = [];
  private constructor(readonly socket: Socket, readonly label: string) {}
  static connect(serverBase: string, label: string): Promise<Bot> {
    return new Promise((resolve, reject) => {
      const socket = io(serverBase, { transports: ["websocket"], reconnection: false, timeout: 8000, forceNew: true });
      const bot = new Bot(socket, label);
      socket.on(SOCKET.wallet, (w: WalletView) => { bot.wallet = w; });
      socket.on(SOCKET.event, (e: GameEvent | GameEvent[]) => { for (const ev of Array.isArray(e) ? e : [e]) bot.events.push(ev); });
      socket.once("connect", () => resolve(bot));
      socket.once("connect_error", (err) => reject(new Error(`${label}: socket connect failed: ${err.message}`)));
    });
  }
  cmd<R = unknown>(cmd: ClientCommand | Record<string, unknown>, timeoutMs = 8000): Promise<CommandAck<R>> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ ok: false, id: "", error: { code: "INTERNAL", message: `${this.label}: no ack within ${timeoutMs} ms` } }), timeoutMs);
      this.socket.emit(SOCKET.command, cmd, (ack: CommandAck<R>) => { clearTimeout(timer); resolve(ack); });
    });
  }
  async joinRoom(o: { roomCode: string; nickname: string; role: HumanRole; sessionToken?: string; directorSecret?: string }): Promise<CommandAck<JoinResult>> {
    const ack = await this.cmd<JoinResult>({ type: "room.join", roomCode: o.roomCode, nickname: o.nickname, role: o.role, requestId: id(), sessionToken: o.sessionToken, directorSecret: o.directorSecret });
    if (ack.ok && ack.result) { this.join = ack.result; this.wallet = ack.result.wallet; }
    return ack;
  }
  close(): void { this.socket.disconnect(); }
}

let n = 0;
/** Idempotency key / request id (8..64 chars). */
export function id(prefix = "e2e"): string { return `${prefix}-${Date.now().toString(36)}-${(++n).toString(36)}-${Math.random().toString(36).slice(2, 8)}`; }

export function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

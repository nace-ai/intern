import { io, type Socket } from "socket.io-client";
import { SOCKET } from "@vice/contracts";
import type {
  AgentView, CharacterView, ClientCommand, CommandAck, ExperimentResult, GameEvent, HumanRole, JoinResult, ListingView,
  MetricsView, MissionView, ProviderStatus, RoomSnapshot, WalletView, WorldTick,
} from "@vice/contracts";
import { useGame } from "../state/gameStore";

let socket: Socket | null = null;
let resyncing = false;
let lastResyncAt = 0;
const SESSION_KEY = "vice.session";

interface Saved { roomCode: string; nickname: string; role: HumanRole; sessionToken: string }
function loadSaved(): Saved | null {
  try { const raw = sessionStorage.getItem(SESSION_KEY); return raw ? (JSON.parse(raw) as Saved) : null; } catch { return null; }
}
function save(s: Saved | null): void {
  try { if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s)); else sessionStorage.removeItem(SESSION_KEY); } catch { /* storage unavailable */ }
}
export const savedSession = loadSaved;

export function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function ensureSocket(): Socket {
  if (socket) return socket;
  const g = useGame.getState;
  useGame.setState({ conn: "connecting" });
  const s = io({ transports: ["websocket", "polling"], reconnectionDelay: 500, reconnectionDelayMax: 3000 });
  socket = s;
  s.on("connect", () => {
    useGame.setState({ conn: "connected" });
    const saved = loadSaved();
    // After a dropped connection, re-attach to the same character with the session token.
    if (saved && g().session) void resync();
  });
  s.on("disconnect", () => useGame.setState({ conn: "disconnected" }));
  s.on(SOCKET.snapshot, (snap: RoomSnapshot) => {
    g().applySnapshot(snap);
    // A seeded reset gives every player a new playerId/characterId. If this snapshot no longer contains
    // us, re-join with the session token to learn who we are now.
    const sess = g().session;
    if (sess && !snap.players.some((p) => p.id === sess.playerId)) void resync(true);
  });
  s.on(SOCKET.tick, (t: WorldTick) => g().applyTick(t));
  s.on(SOCKET.event, (e: GameEvent | GameEvent[]) => {
    const list = Array.isArray(e) ? e : [e];
    let ok = true;
    for (const ev of list) ok = g().applyEvent(ev) && ok;
    if (list.some((ev) => ev.type === "ROOM_RESET")) void resync(true);
    else if (!ok) void resync();
  });
  s.on(SOCKET.agent, (a: AgentView) => useGame.setState((st) => ({ npcs: { ...st.npcs, [a.id]: a } })));
  s.on(SOCKET.listing, (l: ListingView) => useGame.setState((st) => ({ listings: { ...st.listings, [l.ticker]: l } })));
  s.on(SOCKET.character, (c: CharacterView) => useGame.setState((st) => ({ characters: { ...st.characters, [c.id]: c } })));
  s.on(SOCKET.mission, (m: MissionView) => useGame.setState((st) => ({ missions: { ...st.missions, [m.id]: m } })));
  s.on(SOCKET.wallet, (w: WalletView) => useGame.setState({ wallet: w }));
  s.on(SOCKET.provider, (p: ProviderStatus) => useGame.setState({ providerStatus: p, mode: p.mode }));
  s.on(SOCKET.metrics, (m: MetricsView) => useGame.setState({ metrics: m }));
  s.on(SOCKET.experiment, (x: ExperimentResult) => useGame.setState((st) => {
    const rest = st.experiments.filter((e) => e.id !== x.id);
    return { experiments: [...rest, x].sort((a, b) => a.startedAtMs - b.startedAtMs) };
  }));
  return s;
}

/** Sends one command and resolves with the server ack. Never rejects: failures come back as ok:false. */
export function send<R = unknown>(cmd: ClientCommand, timeoutMs = 8000): Promise<CommandAck<R>> {
  const s = ensureSocket();
  const id = ("commandId" in cmd ? cmd.commandId : ("requestId" in cmd ? cmd.requestId : undefined)) ?? "";
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => {
      if (done) return; done = true;
      resolve({ ok: false, id, error: { code: "INTERNAL", message: "No response from the server (timed out)." } });
    }, timeoutMs);
    s.emit(SOCKET.command, cmd, (ack: CommandAck<R>) => {
      if (done) return; done = true; clearTimeout(timer);
      resolve(ack ?? { ok: false, id, error: { code: "INTERNAL", message: "Empty acknowledgement." } });
    });
  });
}
/** Fire-and-forget (movement intents). */
export function sendNoAck(cmd: ClientCommand): void { ensureSocket().emit(SOCKET.command, cmd, () => {}); }

export async function joinRoom(opts: { create: boolean; roomCode: string; nickname: string; role: HumanRole; directorSecret?: string; sessionToken?: string }): Promise<CommandAck<JoinResult>> {
  const requestId = newId();
  const directorSecret = opts.directorSecret ? opts.directorSecret : undefined;
  const cmd: ClientCommand = opts.create
    ? { type: "room.create", nickname: opts.nickname, role: opts.role, requestId, directorSecret }
    : { type: "room.join", roomCode: opts.roomCode, nickname: opts.nickname, role: opts.role, requestId, sessionToken: opts.sessionToken, directorSecret };
  const ack = await send<JoinResult>(cmd);
  if (ack.ok && ack.result) {
    useGame.getState().applyJoin(ack.result, opts.nickname);
    save({ roomCode: ack.result.roomCode, nickname: opts.nickname, role: ack.result.role, sessionToken: ack.result.sessionToken });
  }
  return ack;
}

/** Re-request a full snapshot (sequence gap or reconnect) by re-joining with the session token. */
export async function resync(force = false): Promise<void> {
  const saved = loadSaved();
  const now = Date.now();
  if (!saved || resyncing || (!force && now - lastResyncAt < 1500)) return;
  resyncing = true; lastResyncAt = now;
  try {
    await joinRoom({ create: false, roomCode: saved.roomCode, nickname: saved.nickname, role: saved.role, sessionToken: saved.sessionToken });
  } finally { resyncing = false; }
}

export function leaveRoom(): void {
  save(null);
  socket?.disconnect(); socket = null;
  useGame.getState().leave();
  useGame.setState({ conn: "idle" });
}

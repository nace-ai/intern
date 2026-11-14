import { create } from "zustand";
import type {
  AgentView, CharacterView, DecisionMode, ExperimentResult, GameEvent, HumanRole, JoinResult, ListingView, MapView,
  MetricsView, MissionView, PlayerView, PolicyKnowledgeView, ProviderStatus, RoomSnapshot, TradeView, WalletView, WorldTick,
} from "@vice/contracts";
import { CITY_MAP } from "@vice/contracts";

export type ViewMode = "2d" | "3d" | "world";
export interface Session { roomCode: string; playerId: string; sessionToken: string; role: HumanRole; isDirector: boolean; walletId: string | null; nickname: string }
export interface Toast { id: number; text: string; tone: "info" | "good" | "bad"; atMs: number }
export interface CrashFx { ticker: string; nickname: string; from: number; atMs: number }

export interface GameState {
  conn: "idle" | "connecting" | "connected" | "disconnected";
  session: Session | null;
  roomCode: string; runId: string; seed: number; ruleVersion: string; mode: DecisionMode;
  /** Unique per server boot: the same join code and run number after a restart is a different room. */
  roomId: string;
  map: MapView;
  players: Record<string, PlayerView>;
  characters: Record<string, CharacterView>;
  npcs: Record<string, AgentView>;
  missions: Record<string, MissionView>;
  listings: Record<string, ListingView>;
  trades: TradeView[];
  events: GameEvent[];
  metrics: MetricsView | null;
  experiments: ExperimentResult[];
  policy: PolicyKnowledgeView | null;
  crews: import("@vice/contracts").CrewView[];
  providerStatus: ProviderStatus | null;
  wallet: WalletView | null;
  serverTimeMs: number;
  tick: number;
  /** Latest 10 Hz world tick (changes every tick: subscribe only where needed). */
  lastTick: WorldTick | null;
  selectedAgentId: string | null;
  selectedEventId: string | null;
  selectedTicker: string | null;
  viewMode: ViewMode;
  toasts: Toast[];
  crash: CrashFx | null;
  // actions
  applyJoin(r: JoinResult, nickname: string): void;
  applySnapshot(s: RoomSnapshot): void;
  applyTick(t: WorldTick): void;
  applyEvent(e: GameEvent): boolean;
  set(p: Partial<GameState>): void;
  selectAgent(id: string | null): void;
  selectEvent(id: string | null): void;
  toast(text: string, tone?: Toast["tone"]): void;
  leave(): void;
}

// ---- per-frame pose buffer (kept out of React state; scenes read it every animation frame) ----
export interface Pose { px: number; pz: number; x: number; z: number; t0: number; t1: number }
const poses = new Map<string, Pose>();
const TICK_MS = 100;
function pushPose(id: string, x: number, z: number, now: number): void {
  const p = poses.get(id);
  if (!p) { poses.set(id, { px: x, pz: z, x, z, t0: now, t1: now + TICK_MS }); return; }
  const cur = samplePose(p, now);
  p.px = cur.x; p.pz = cur.z; p.x = x; p.z = z; p.t0 = now; p.t1 = now + TICK_MS;
}
function samplePose(p: Pose, now: number): { x: number; z: number } {
  const k = p.t1 <= p.t0 ? 1 : Math.max(0, Math.min(1, (now - p.t0) / (p.t1 - p.t0)));
  return { x: p.px + (p.x - p.px) * k, z: p.pz + (p.z - p.pz) * k };
}
/** Smoothed position of a character or NPC for rendering. Call every frame with performance.now(). */
export function getPose(id: string, now: number = performance.now()): { x: number; z: number } | null {
  const p = poses.get(id);
  return p ? samplePose(p, now) : null;
}

const byId = <T extends { id: string }>(xs: T[]): Record<string, T> => Object.fromEntries(xs.map((x) => [x.id, x]));
let toastSeq = 1;
const MAX_EVENTS = 600;

export const useGame = create<GameState>((set, get) => ({
  conn: "idle", session: null,
  roomCode: "", runId: "", roomId: "", seed: 0, ruleVersion: "", mode: "drex", map: CITY_MAP,
  players: {}, characters: {}, npcs: {}, missions: {}, listings: {}, trades: [], events: [],
  metrics: null, experiments: [], policy: null, crews: [], providerStatus: null, wallet: null,
  serverTimeMs: 0, tick: 0, lastTick: null, selectedAgentId: null, selectedEventId: null, selectedTicker: null,
  viewMode: "3d", toasts: [], crash: null,

  applyJoin(r, nickname) {
    set({ session: { roomCode: r.roomCode, playerId: r.playerId, sessionToken: r.sessionToken, role: r.role, isDirector: r.isDirector, walletId: r.walletId, nickname }, wallet: r.wallet });
    get().applySnapshot(r.snapshot);
  },
  applySnapshot(s) {
    const now = performance.now();
    poses.clear();
    for (const c of s.characters) pushPose(c.id, c.x, c.z, now);
    for (const n of s.npcs) pushPose(n.id, n.x, n.z, now);
    const st = get();
    // Same run (e.g. the snapshot pushed after someone respawns): keep the longer local log and merge.
    // New run (seeded reset) or first join: start the timeline clean.
    const sameRun = st.runId !== "" && st.runId === s.runId && st.roomCode === s.roomCode && st.roomId === s.roomId;
    let events = [...s.recentEvents].sort((a, b) => a.serverSequence - b.serverSequence);
    if (sameRun && st.events.length) {
      const have = new Set(events.map((e) => e.eventId));
      events = [...st.events.filter((e) => e.runId === s.runId && !have.has(e.eventId)), ...events]
        .sort((a, b) => a.serverSequence - b.serverSequence).slice(-MAX_EVENTS);
    }
    if (!sameRun) set({ selectedEventId: null, selectedTicker: null, crash: null });
    set({
      roomCode: s.roomCode, runId: s.runId, roomId: s.roomId, seed: s.seed, ruleVersion: s.ruleVersion, mode: s.mode, map: s.map ?? CITY_MAP,
      players: byId(s.players), characters: byId(s.characters), npcs: byId(s.npcs), missions: byId(s.missions),
      listings: Object.fromEntries(s.listings.map((l) => [l.ticker, l])), trades: s.trades, events,
      metrics: s.metrics, experiments: s.experiments ?? [], policy: s.policy, crews: s.crews ?? [], providerStatus: s.providerStatus,
      serverTimeMs: s.serverTimeMs, tick: s.tick, lastTick: null,
    });
  },
  applyTick(t) {
    const now = performance.now();
    const st = get();
    let chars = st.characters, npcs = st.npcs, changedC = false, changedN = false;
    for (const c of t.characters) {
      pushPose(c.id, c.x, c.z, now);
      const cur = chars[c.id];
      if (cur && (Math.round(cur.heat) !== Math.round(c.heat) || cur.status !== c.status)) {
        if (!changedC) { chars = { ...chars }; changedC = true; }
        chars[c.id] = { ...cur, heat: c.heat, status: c.status, x: c.x, z: c.z };
      }
    }
    for (const n of t.npcs) {
      pushPose(n.id, n.x, n.z, now);
      const cur = npcs[n.id];
      if (cur && cur.activity !== n.activity) {
        if (!changedN) { npcs = { ...npcs }; changedN = true; }
        npcs[n.id] = { ...cur, activity: n.activity, x: n.x, z: n.z };
      }
    }
    const patch: Partial<GameState> = { lastTick: t };
    if (changedC) patch.characters = chars;
    if (changedN) patch.npcs = npcs;
    // Clock is surfaced once a second to avoid re-rendering panels at 10 Hz.
    if (t.tick % 10 === 0 || changedC || changedN) { patch.serverTimeMs = t.serverTimeMs; patch.tick = t.tick; }
    set(patch);
  },
  /** Returns false when a sequence gap was detected (caller should resync). */
  applyEvent(e) {
    const st = get();
    const last = st.events.length ? st.events[st.events.length - 1]! : null;
    if (st.runId && (e.runId !== st.runId || (st.roomId !== "" && e.roomId !== st.roomId))) {
      // New run (seeded reset) or a restarted server: the snapshot that follows replaces everything. The old run's
      // selection must go now, or the whole new timeline stays dimmed around an event that no longer exists.
      set({ events: [e], runId: e.runId, roomId: e.roomId, selectedEventId: null, selectedTicker: null, crash: null });
      return true;
    }
    if (last && e.serverSequence <= last.serverSequence) {
      if (st.events.some((x) => x.eventId === e.eventId)) return true;
    }
    const gap = !!last && e.serverSequence > last.serverSequence + 1;
    let events = [...st.events, e];
    if (last && e.serverSequence < last.serverSequence) events.sort((a, b) => a.serverSequence - b.serverSequence);
    if (events.length > MAX_EVENTS) events = events.slice(events.length - MAX_EVENTS);
    const patch: Partial<GameState> = { events };
    switch (e.type) {
      case "PLAYER_JOINED": {
        const p = (e as GameEvent<"PLAYER_JOINED">).payload;
        const cur = st.players[p.playerId];
        patch.players = { ...st.players, [p.playerId]: { id: p.playerId, nickname: p.nickname, role: p.role, connected: true, characterId: cur?.characterId ?? null, isBot: cur?.isBot ?? false } };
        break;
      }
      case "PLAYER_LEFT": {
        const p = (e as GameEvent<"PLAYER_LEFT">).payload;
        const cur = st.players[p.playerId];
        if (cur) patch.players = { ...st.players, [p.playerId]: { ...cur, connected: false } };
        break;
      }
      case "MODE_CHANGED": patch.mode = (e as GameEvent<"MODE_CHANGED">).payload.mode; break;
      case "LISTING_HALTED": {
        const p = (e as GameEvent<"LISTING_HALTED">).payload;
        const l = st.listings[p.ticker];
        patch.crash = { ticker: p.ticker, nickname: l?.nickname ?? "", from: p.finalSpotBeforeHalt, atMs: Date.now() };
        break;
      }
      case "TRADE_EXECUTED": {
        const p = (e as GameEvent<"TRADE_EXECUTED">).payload;
        if (!st.trades.some((t) => t.tradeId === p.tradeId)) {
          patch.trades = [...st.trades, { tradeId: p.tradeId, atMs: e.simTimeMs, actorId: p.actorId, actorName: p.actorName, actorKind: p.actorKind, ticker: p.ticker, side: p.side, qty: p.qty, executionPrice: p.executionPrice }].slice(-80);
        }
        break;
      }
      default: break;
    }
    set(patch);
    return !gap;
  },
  set: (p) => set(p),
  selectAgent: (id) => set({ selectedAgentId: id }),
  selectEvent: (id) => set({ selectedEventId: id }),
  toast(text, tone = "info") {
    const t: Toast = { id: toastSeq++, text, tone, atMs: Date.now() };
    set({ toasts: [...get().toasts.slice(-3), t] });
    setTimeout(() => set({ toasts: get().toasts.filter((x) => x.id !== t.id) }), 3800);
  },
  leave() { poses.clear(); set({ session: null, events: [], characters: {}, npcs: {}, listings: {}, wallet: null }); },
}));

// ---------- selectors / hooks for the scene and panels ----------
/** Your current character: the live one if any, otherwise your most recently captured one. */
export function selectMyCharacter(s: GameState): CharacterView | null {
  if (!s.session) return null;
  let best: CharacterView | null = null;
  for (const c of Object.values(s.characters)) {
    if (c.playerId !== s.session.playerId) continue;
    const live = c.status === "ACTIVE" || c.status === "WANTED";
    if (live) return c;
    if (!best || c.spawnTimeMs > best.spawnTimeMs) best = c;
  }
  return best;
}
export const useMyCharacter = (): CharacterView | null => useGame(selectMyCharacter);
export const useSelectedAgent = (): AgentView | null => useGame((s) => (s.selectedAgentId ? s.npcs[s.selectedAgentId] ?? null : null));
export const isLive = (c: CharacterView): boolean => c.status === "ACTIVE" || c.status === "WANTED";
export function nameOf(s: GameState, id: string | null | undefined): string {
  if (!id) return "—";
  return s.npcs[id]?.name ?? s.characters[id]?.nickname ?? s.players[id]?.nickname ?? (id === "server" || id === "system" ? "City" : id);
}

// Development handle for the browser smoke test (apps/web/e2e) and for debugging in the console.
if (import.meta.env.DEV) (window as unknown as { __vice?: typeof useGame }).__vice = useGame;

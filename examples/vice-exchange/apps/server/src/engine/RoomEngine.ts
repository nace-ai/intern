import {
  CITY_MAP, MISSION_SPOTS, RULES, RULES_VERSION, SPAWN_POINTS, carRects, dist, hasLineOfSight, insideRect, wantedLevel,
} from "@vice/contracts";
import type {
  AgentView, CaptureRejectReason, CharacterView, ClientCommand, CommandAck, CrewView, CommandErrorCode, DecisionMode, DecisionTrace,
  EntityId, EventPayloads, EventType, ExperimentResult, GameEvent, HumanRole, ListingView, MessageKind, MissionView, NpcActivity,
  PlayerView, PriceReason, RoomSnapshot, TradeSide, Vec2, WalletView, WorldTick,
} from "@vice/contracts";
import type {
  BrainsPort, CharacterState, Clock, EconomyPort, EmitOpts, MemoryPort, MessageRecord, MetricsPort, NpcState, OrderResult,
  PersistencePort, PlayerState, PolicyPort, PriceChange, RoomState, SchedulerPort, WorldPort,
} from "../domain";
import { freshSighting } from "../agents";
import { seedNpcs } from "../demo/seedScenario";
import { findPath } from "./pathfinding";
import { pad } from "./util";

export interface EngineDeps {
  clock: Clock; economy: EconomyPort; memory: MemoryPort; scheduler: SchedulerPort;
  persistence: PersistencePort; metrics: MetricsPort; policy?: PolicyPort | null;
  /** Builds the NPC brains once the engine (WorldPort) exists. */
  createBrains: (world: WorldPort, onTrace: (t: DecisionTrace) => void) => BrainsPort;
  experiments?: () => ExperimentResult[];
  onRunExperiment?: (engine: RoomEngine, durationSec: number) => ExperimentResult | null;
}
export interface EngineOpts { roomId: string; code: string; seed: number; mode: DecisionMode }
export interface Dirty { agents: Set<string>; listings: Set<string>; characters: Set<string>; missions: Set<string>; wallets: Set<string>; snapshot: boolean }

const BULLETIN_FRESH_MS = 20000;
const PURSUIT_LOST_MS = 5000;
const INVESTIGATE_LINGER_MS = 4000;
const INVESTIGATE_GIVE_UP_MS = 120000;
const RADIUS = 0.6;
/** player.pose: a browser client may report at most this multiple of walking speed; the budget never banks more than this many seconds. */
const POSE_FOOT_FACTOR = 1.25;
const POSE_BURST_SEC = 0.4;
/** Solid parked cars: players and NPCs stop at these footprints exactly as at buildings. They never block line of sight. */
const CAR_RECTS = carRects();
/** The full log lives in the store; memory keeps the recent tail for snapshots, replay checks and experiments. */
const MAX_EVENTS_IN_MEMORY = 20000;

const ack = (id: string, result?: unknown): CommandAck => ({ ok: true, id, result });
const fail = (id: string, code: CommandErrorCode, message: string, reason?: string): CommandAck => ({ ok: false, id, error: { code, message, reason } });

export class RoomEngine implements WorldPort {
  state!: RoomState;
  events: GameEvent[] = [];
  readonly brains: BrainsPort;
  readonly dirty: Dirty = { agents: new Set(), listings: new Set(), characters: new Set(), missions: new Set(), wallets: new Set(), snapshot: false };
  private listeners: Array<(e: GameEvent) => void> = [];
  private idem = new Map<string, CommandAck>();
  private viaExternal = false;
  private outbox: GameEvent[] = [];
  private depth = 0;
  private runNo = 0;
  private lastTickAtMs = 0;
  private nextMissionAtMs: number | null = null;
  private nextMissionSpot = 0;
  private survivalStep = new Map<string, number>();
  private heatBucket = new Map<string, number>();
  private sightingAt = new Map<string, number>();
  private bulletinAt = new Map<string, number>();
  private captureAttemptAt = new Map<string, number>();
  private poseAt = new Map<string, number>();
  /** Stun pulse bookkeeping: per character ammo and next-shot time; per entity (character or NPC) stun end. */
  private ammo = new Map<string, number>();
  private weaponReadyAt = new Map<string, number>();
  private stunnedUntil = new Map<string, number>();
  /** Hunter crews. crewOf: playerId -> crewId. crewStunAt: `${shooterPlayerId}>${victimPlayerId}` -> time of the last stun of a crewmate. */
  private crews = new Map<string, CrewView>();
  private crewOf = new Map<string, string>();
  private crewStunAt = new Map<string, number>();
  private poseBudget = new Map<string, { metres: number; at: number }>();
  /** Per-officer chase/search record. `departAtMs` is when an investigation actually sets off (dispatch delay); `pace` is its walking speed. */
  private pursuit = new Map<string, { lastSeenAtMs: number; lastKnown: Vec2; arrivedAtMs: number | null; departAtMs: number; pace: number }>();
  private storyOf = new Map<string, string>();
  /**
   * Causal anchors (FR-061) so a capture traces back to the mission: the event that made each character wanted,
   * each officer's latest sighting of a character (key `${npcId}:${characterId}`), and each NPC's latest applied action.
   */
  private wantedEventOf = new Map<string, string>();
  private sightingEventOf = new Map<string, string>();
  private actionEventOf = new Map<string, { eventId: string; correlationId: string }>();
  private nav = new Map<string, { goal: Vec2; path: Vec2[]; at: number }>();
  private policyAnnounced = false;

  constructor(readonly deps: EngineDeps, readonly opts: EngineOpts) {
    this.initState(opts.seed, opts.mode, 0);
    this.brains = deps.createBrains(this, (t) => this.onTrace(t));
    this.bootRun(true);
  }

  // ---------- WorldPort ----------
  now(): number { return this.deps.clock.now(); }
  nextId(prefix: string): string {
    const n = (this.state.counters[prefix] ?? 0) + 1;
    this.state.counters[prefix] = n;
    return `${prefix}_${pad(n, prefix === "evt" ? 6 : 4)}`;
  }
  emit<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], opts: EmitOpts = {}): GameEvent<T> {
    return this.emitWith(type, actorId, payload, opts);
  }
  /** `prepare` runs after the event exists but before anyone is notified (used to write memories that cite it). */
  private emitWith<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], opts: EmitOpts = {}, prepare?: (e: GameEvent<T>) => void): GameEvent<T> {
    const s = this.state;
    const eventId = this.nextId("evt");
    const e: GameEvent<T> = {
      schemaVersion: 1, eventId, roomId: s.id, runId: s.runId, serverSequence: ++s.lastEventSequence,
      time: new Date().toISOString(), simTimeMs: this.now(), type, actorId,
      causationId: opts.causationId ?? null, correlationId: opts.correlationId ?? this.nextId("story"), payload,
    };
    try { prepare?.(e); } catch (err) { console.error("[engine] event prepare failed", type, err); }
    if (type === "AGENT_ACTION_APPLIED") this.actionEventOf.set(actorId, { eventId, correlationId: e.correlationId });
    this.events.push(e as GameEvent);
    if (this.events.length > MAX_EVENTS_IN_MEMORY) this.events.splice(0, this.events.length - MAX_EVENTS_IN_MEMORY);
    this.markDirty(e as GameEvent);
    try { this.deps.persistence.appendEvent(e as GameEvent); } catch (err) { console.error("[persistence] appendEvent failed", (err as Error).message); }
    try { this.deps.metrics.onEvent(e as GameEvent); } catch (err) { console.error("[metrics] onEvent failed", (err as Error).message); }
    // Brains may emit follow-up events re-entrantly; listeners still see everything in serverSequence order.
    this.outbox.push(e as GameEvent);
    this.depth++;
    try { this.brains?.onEvent(e as GameEvent); } catch (err) { console.error("[brains] onEvent failed", err); }
    this.depth--;
    if (this.depth === 0) {
      const batch = this.outbox; this.outbox = [];
      for (const ev of batch) for (const l of this.listeners) {
        try { l(ev); } catch (err) { console.error("[engine] event listener failed", ev.type, err); }
      }
    }
    return e;
  }
  sendMessage(m: { senderId: EntityId; recipientId: EntityId; kind: MessageKind; sourceMemoryId: string | null; sourceEventId: string; subjectCharacterId: EntityId | null; summary: string; payload: Record<string, unknown> }, opts: EmitOpts): MessageRecord {
    const src = m.sourceMemoryId ? this.deps.memory.get(m.sourceMemoryId) : undefined;
    const hop = (src?.hop ?? 0) + 1;
    const id = this.nextId("msg");
    const now = this.now();
    const correlationId = opts.correlationId ?? this.nextId("story");
    const sent = this.emit("AGENT_MESSAGE_SENT", m.senderId, { messageId: id, senderId: m.senderId, recipientId: m.recipientId, kind: m.kind, sourceMemoryId: m.sourceMemoryId, sourceEventId: m.sourceEventId, subjectCharacterId: m.subjectCharacterId, summary: m.summary, hop }, { causationId: opts.causationId ?? m.sourceEventId, correlationId });
    const payload = { ...(src ? { x: src.payload.x, z: src.payload.z } : {}), ...m.payload };
    const rec: MessageRecord = { id, senderId: m.senderId, recipientId: m.recipientId, kind: m.kind, sourceMemoryId: m.sourceMemoryId, sourceEventId: m.sourceEventId, subjectCharacterId: m.subjectCharacterId, summary: m.summary, payload, hop, sentAtMs: now, deliverAtMs: now + RULES.messages.deliveryDelayMs, deliveredAtMs: null, sentEventId: sent.eventId, correlationId };
    this.state.messages.push(rec);
    if (this.state.messages.length > 500) this.state.messages.splice(0, this.state.messages.length - 500);
    return rec;
  }
  placeNpcOrder(npcId: EntityId, ticker: string, side: TradeSide, opts: EmitOpts): OrderResult {
    const npc = this.state.npcs[npcId];
    if (!npc || !npc.walletId || npc.role !== "TRADER") {
      this.emit("TRADE_REJECTED", npcId, { commandId: this.nextId("ncmd"), walletId: null, actorId: npcId, actorKind: "NPC", ticker, side, qty: 1, reason: "ACTOR_NOT_ALLOWED" }, opts);
      return { ok: false, reason: "ACTOR_NOT_ALLOWED", message: "NPC has no wallet" };
    }
    return this.settleOrder({ walletId: npc.walletId, actorId: npcId, actorName: npc.name, actorKind: "NPC", ticker, side, commandId: this.nextId("ncmd") }, opts);
  }
  setNpcActivity(npcId: EntityId, activity: NpcActivity, target: { moveTarget?: Vec2 | null; targetCharacterId?: EntityId | null }): void {
    this.setActivity(npcId, activity, target, false);
  }
  /** `immediate` is for the engine's own hand-over from a lost chase, which never waits for dispatch. */
  private setActivity(npcId: EntityId, activity: NpcActivity, target: { moveTarget?: Vec2 | null; targetCharacterId?: EntityId | null }, immediate: boolean): void {
    const npc = this.state.npcs[npcId];
    if (!npc) return;
    const now = this.now();
    const prev = npc.activity === "INVESTIGATE" && npc.targetCharacterId === (target.targetCharacterId ?? null) ? this.pursuit.get(npcId) : undefined;
    npc.activity = activity;
    npc.moveTarget = target.moveTarget ?? null;
    npc.targetCharacterId = target.targetCharacterId ?? null;
    if (activity === "PURSUE" && npc.targetCharacterId) {
      // Start from where this officer itself last saw (or was told about) the target; live sight then takes over each tick.
      const known = this.deps.memory.recall(npcId, this.now()).find((m) => m.subjectCharacterId === npc.targetCharacterId && Number.isFinite(Number(m.payload.x)) && Number.isFinite(Number(m.payload.z)));
      const from = known ? { x: Number(known.payload.x), z: Number(known.payload.z) } : { x: npc.x, z: npc.z };
      this.pursuit.set(npcId, { lastSeenAtMs: now, lastKnown: npc.moveTarget ?? from, arrivedAtMs: null, departAtMs: now, pace: RULES.policePursuitSpeed });
    } else if (activity === "INVESTIGATE") {
      // Acting on a report or bulletin (no fresh sighting of their own): the officer radios in first, then searches on foot
      // at the search pace. An officer who has just seen the suspect, or has lost one mid-chase, leaves at once at walking pace.
      const direct = immediate || (!!npc.targetCharacterId && !!freshSighting(this.deps.memory.recall(npcId, now), npc.targetCharacterId, now));
      const departAtMs = direct ? now : Math.min(prev?.departAtMs ?? Infinity, now + RULES.policeDispatchDelayMs); // a repeat order never restarts the wait
      this.pursuit.set(npcId, { lastSeenAtMs: now, lastKnown: npc.moveTarget ?? { x: npc.x, z: npc.z }, arrivedAtMs: null, departAtMs, pace: direct ? RULES.npcWalkSpeed : RULES.npcWalkSpeed * RULES.policeSearchPaceFactor });
    } else this.pursuit.delete(npcId);
    this.dirty.agents.add(npcId);
  }
  publishBulletin(policeNpcId: EntityId, characterId: EntityId, lastKnown: Vec2, opts: EmitOpts): void {
    const c = this.state.characters[characterId];
    const now = this.now();
    if (!c || c.role !== "OUTLAW" || (c.status !== "ACTIVE" && c.status !== "WANTED")) return;
    if (now - (this.bulletinAt.get(characterId) ?? -Infinity) < BULLETIN_FRESH_MS) return;
    this.bulletinAt.set(characterId, now);
    const correlationId = opts.correlationId ?? this.nextId("story");
    this.addHeat(c, RULES.heat.perPoliceReport, "police bulletin", { causationId: opts.causationId, correlationId });
    const recipientIds = Object.keys(this.state.npcs);
    const bulletinId = this.nextId("bul");
    this.emitWith("BULLETIN_PUBLISHED", policeNpcId, { bulletinId, characterId, ticker: c.ticker, lastKnown, issuedBy: policeNpcId, recipientIds }, { causationId: opts.causationId, correlationId }, (e) => {
      for (const id of recipientIds) {
        this.deps.memory.remember({
          agentId: id, factType: "WANTED_BULLETIN", subjectCharacterId: characterId, subjectTicker: c.ticker, eventId: e.eventId,
          sourceActorId: policeNpcId, sourceKind: "BULLETIN", sourceMemoryId: null, hop: 1, observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.WANTED_BULLETIN,
          confidence: RULES.memory.confidence.BULLETIN, summary: `Police bulletin: ${c.nickname}${c.ticker ? ` (${c.ticker})` : ""} is wanted, last seen near (${Math.round(lastKnown.x)}, ${Math.round(lastKnown.z)}).`,
          payload: { x: lastKnown.x, z: lastKnown.z, ticker: c.ticker, bulletinId }, citation: null,
        }, this.nextId("mem"));
      }
    });
  }
  marketSummary(ticker: string) {
    const l = this.state.listings[ticker];
    if (!l) return null;
    const now = this.now();
    const q = this.deps.economy.quote(this.state, ticker, now);
    const past = [...l.history].reverse().find((p) => now - p.t >= 60000) ?? l.history[0];
    const changePct60s = past && past.spot > 0 ? Math.round(((l.spotPrice - past.spot) / past.spot) * 1000) / 10 : 0;
    return { ticker, status: l.status, spotPrice: l.spotPrice, referenceValue: l.referenceValue, changePct60s, netBuyUnits: q?.netBuyUnits ?? 0 };
  }

  // ---------- lifecycle ----------
  onEvent(l: (e: GameEvent) => void): () => void { this.listeners.push(l); return () => { this.listeners = this.listeners.filter((x) => x !== l); }; }

  private initState(seed: number, mode: DecisionMode, lastSeq: number): void {
    const now = this.now();
    this.runNo++;
    this.state = {
      id: this.opts.roomId, code: this.opts.code, runId: `run_${pad(this.runNo, 3)}`, seed, tick: 0, ruleVersion: RULES_VERSION, mode,
      createdAtMs: this.state?.createdAtMs ?? now, runStartedAtMs: now, lastEventSequence: lastSeq,
      players: {}, characters: {}, npcs: seedNpcs(), listings: {}, wallets: {}, missions: {}, messages: [], trades: [], counters: {},
    };
    this.events = [];
    this.lastTickAtMs = now; this.nextMissionAtMs = null; this.nextMissionSpot = 0; this.policyAnnounced = false;
    for (const m of [this.survivalStep, this.heatBucket, this.sightingAt, this.bulletinAt, this.captureAttemptAt, this.poseAt, this.pursuit, this.storyOf, this.nav, this.wantedEventOf, this.sightingEventOf, this.actionEventOf, this.poseBudget]) m.clear();
  }
  private bootRun(first: boolean, previousRunId?: string): void {
    const s = this.state;
    this.deps.scheduler.setMode(s.mode);
    this.deps.metrics.reset(s.runId, s.mode, s.seed, s.runStartedAtMs);
    const corr = this.nextId("story");
    if (first) this.emit("ROOM_CREATED", "server", { roomCode: s.code, seed: s.seed, ruleVersion: s.ruleVersion, mode: s.mode, npcCount: Object.keys(s.npcs).length }, { correlationId: corr });
    else this.emit("ROOM_RESET", "server", { seed: s.seed, previousRunId: previousRunId!, newRunId: s.runId, mode: s.mode }, { correlationId: corr });
    for (const npc of Object.values(s.npcs)) {
      if (!npc.walletId) continue;
      // Every agent has its own wallet: traders start with capital, everyone else earns from V$0.
      const start = npc.role === "TRADER" ? RULES.trade.startingWalletTrader : 0;
      this.deps.economy.openWallet(s, { id: npc.walletId, ownerId: npc.id, ownerKind: "NPC", ownerName: npc.name, balance: start });
      this.emit("WALLET_OPENED", "server", { walletId: npc.walletId, ownerId: npc.id, ownerKind: "NPC", balance: start }, { correlationId: corr });
    }
    this.spawnMission(corr);
    this.announcePolicy();
    this.dirty.snapshot = true;
  }
  /**
   * Puts the document-derived police policy on the record once per run, as soon as it is loaded
   * (at room start if it is already there, otherwise when the app reports that ingestion finished).
   */
  announcePolicy(): void {
    const v = this.deps.policy?.view();
    if (!v || this.policyAnnounced) return;
    this.policyAnnounced = true;
    this.emit("POLICY_INGESTED", "ndi", { documentTitle: v.documentTitle, sourceUrl: v.sourceUrl, ndiJobId: v.ndiJobId, factCount: v.facts.length });
    this.dirty.snapshot = true;
  }
  private spawnMission(correlationId?: string): void {
    const spot = MISSION_SPOTS[this.nextMissionSpot % MISSION_SPOTS.length]!;
    const id = this.nextId("mis");
    this.state.missions[id] = { id, spotIndex: this.nextMissionSpot % MISSION_SPOTS.length, x: spot.x, z: spot.z, label: spot.label, status: "AVAILABLE", completedByCharacterId: null, completedAtMs: null };
    this.nextMissionSpot++;
    this.emit("MISSION_SPAWNED", "server", { missionId: id, x: spot.x, z: spot.z, label: spot.label }, { correlationId });
    const done = Object.values(this.state.missions).filter((m) => m.status === "COMPLETED");
    for (const m of done.slice(0, Math.max(0, done.length - 4))) delete this.state.missions[m.id];
  }

  // ---------- players ----------
  addPlayer(p: { nickname: string; role: HumanRole; isDirector?: boolean; isBot?: boolean; sessionToken: string; socketId?: string | null }): PlayerState {
    const now = this.now();
    const id = this.nextId("ply");
    const player: PlayerState = {
      id, nickname: p.nickname, role: p.role, sessionToken: p.sessionToken, socketId: p.socketId ?? null, connected: true,
      isDirector: !!p.isDirector, isBot: !!p.isBot, characterId: null, walletId: null, moveDir: { x: 0, z: 0 }, joinedAtMs: now, disconnectedAtMs: null,
    };
    this.state.players[id] = player;
    const corr = this.nextId("story");
    this.emit("PLAYER_JOINED", id, { playerId: id, nickname: p.nickname, role: p.role, reconnect: false }, { correlationId: corr });
    this.enrol(player, corr);
    this.dirty.snapshot = true;
    return player;
  }
  private enrol(player: PlayerState, correlationId: string): void {
    if (player.role === "SPECTATOR") return;
    player.walletId = `wal_${player.id}`;
    this.deps.economy.openWallet(this.state, { id: player.walletId, ownerId: player.id, ownerKind: "HUMAN", ownerName: player.nickname, balance: RULES.trade.startingWalletHuman });
    this.emit("WALLET_OPENED", "server", { walletId: player.walletId, ownerId: player.id, ownerKind: "HUMAN", balance: RULES.trade.startingWalletHuman }, { correlationId });
    this.spawnCharacter(player, correlationId);
  }
  /**
   * Binds a socket to an existing player: same token (refresh / blip) or a nickname reclaim of a disconnected player.
   * The character, wallet and ticker are untouched. `isDirector` and `sessionToken` are replaced only when given.
   */
  reattach(playerId: string, socketId: string | null, change: { sessionToken?: string; isDirector?: boolean } = {}): PlayerState | null {
    const p = this.state.players[playerId];
    if (!p) return null;
    const resync = p.connected && p.socketId === socketId; // same socket asking for a fresh snapshot: nothing changed
    p.connected = true; p.socketId = socketId; p.disconnectedAtMs = null;
    if (change.sessionToken !== undefined) p.sessionToken = change.sessionToken;
    if (change.isDirector !== undefined) p.isDirector = change.isDirector;
    if (resync) return p;
    p.moveDir = { x: 0, z: 0 };
    this.emit("PLAYER_JOINED", p.id, { playerId: p.id, nickname: p.nickname, role: p.role, reconnect: true });
    this.dirty.snapshot = true;
    return p;
  }
  reconnect(playerId: string, socketId: string | null): PlayerState | null { return this.reattach(playerId, socketId); }
  disconnect(playerId: string): void {
    const p = this.state.players[playerId];
    if (!p || !p.connected) return;
    p.connected = false; p.socketId = null; p.disconnectedAtMs = this.now(); p.moveDir = { x: 0, z: 0 };
    this.emit("PLAYER_LEFT", p.id, { playerId: p.id, nickname: p.nickname });
    this.dirty.snapshot = true;
  }
  private spawnCharacter(player: PlayerState, correlationId: string, causationId?: string): CharacterState {
    const s = this.state; const now = this.now();
    const role = player.role === "HUNTER" ? "HUNTER" : "OUTLAW";
    const n = (s.counters[`spawn_${role}`] = (s.counters[`spawn_${role}`] ?? 0) + 1);
    const pts = SPAWN_POINTS[role]; const at = pts[(n - 1) % pts.length]!;
    const id = this.nextId("chr");
    let ticker: string | null = null;
    if (role === "OUTLAW") {
      const k = (s.counters.tkr = (s.counters.tkr ?? 0) + 1);
      ticker = `${player.nickname.replace(/[^A-Za-z0-9]/g, "").toUpperCase().padEnd(3, "X").slice(0, 3)}-${pad(k, 2)}`;
    }
    const c: CharacterState = {
      id, playerId: player.id, nickname: player.nickname, role, ticker, status: "ACTIVE", x: at.x, z: at.z,
      notorietyPoints: 0, completedMissions: 0, heat: 0, successfulEscapes: 0, spawnTimeMs: now, captureTimeMs: null, capturedBy: null,
      lastHeatGainAtMs: now, lastPoliceSightingAtMs: 0,
    };
    s.characters[id] = c; player.characterId = id; player.moveDir = { x: 0, z: 0 };
    const spawned = this.emit("CHARACTER_SPAWNED", player.id, { characterId: id, playerId: player.id, nickname: player.nickname, role, ticker, x: at.x, z: at.z, walletId: player.walletId! }, { correlationId, causationId });
    if (ticker) {
      const l = this.deps.economy.openListing(s, c, now);
      this.survivalStep.set(ticker, 0);
      const traders = Object.values(s.npcs).filter((x) => x.role === "TRADER");
      this.emitWith("LISTING_OPENED", "exchange", { ticker, characterId: id, nickname: c.nickname, referenceValue: l.referenceValue, spotPrice: l.spotPrice }, { correlationId, causationId: spawned.eventId }, (e) => {
        for (const t of traders) {
          this.deps.memory.remember({
            agentId: t.id, factType: "LISTING", subjectCharacterId: id, subjectTicker: ticker, eventId: e.eventId, sourceActorId: "exchange", sourceKind: "BULLETIN",
            sourceMemoryId: null, hop: 0, observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.LISTING, confidence: RULES.memory.confidence.BULLETIN,
            summary: `New listing ${ticker} (${c.nickname}) opened at V$${l.spotPrice}.`, payload: { ticker, spotPrice: l.spotPrice }, citation: null,
          }, this.nextId("mem"));
        }
      });
    }
    return c;
  }

  // ---------- commands ----------
  /** `via.external` marks commands from an external 3D world client (the bridge), whose poses are not building-checked. */
  dispatch(playerId: string, cmd: ClientCommand, via: { external?: boolean } = {}): CommandAck {
    this.viaExternal = !!via.external; // dispatch is synchronous, so this holds for the whole command
    const player = this.state.players[playerId];
    const id = ("commandId" in cmd ? cmd.commandId : "requestId" in cmd ? cmd.requestId : undefined) ?? "";
    if (!player) return fail(id, "NOT_JOINED", "Join a room first");
    const key = "commandId" in cmd ? `${playerId}:${cmd.commandId}` : null;
    if (key) {
      const prior = this.idem.get(key);
      if (prior) return { ...prior, duplicate: true };
    }
    let result: CommandAck;
    try { result = this.handle(player, cmd, id); }
    catch (err) { console.error("[engine] command failed", cmd.type, err); result = fail(id, "INTERNAL", "Server error"); }
    if (key) { this.idem.set(key, result); if (this.idem.size > 5000) this.idem.delete(this.idem.keys().next().value!); }
    return result;
  }
  private character(player: PlayerState): CharacterState | null { return player.characterId ? this.state.characters[player.characterId] ?? null : null; }
  private live(c: CharacterState | null): c is CharacterState { return !!c && (c.status === "ACTIVE" || c.status === "WANTED"); }

  private handle(player: PlayerState, cmd: ClientCommand, id: string): CommandAck {
    const c = this.character(player);
    switch (cmd.type) {
      case "room.create": case "room.join": return fail(id, "INVALID_COMMAND", "Already joined");
      case "player.move": {
        if (!this.live(c)) return fail(id, "CHARACTER_INACTIVE", "No active character");
        const m = Math.hypot(cmd.dirX, cmd.dirZ);
        player.moveDir = m > 1 ? { x: cmd.dirX / m, z: cmd.dirZ / m } : { x: cmd.dirX, z: cmd.dirZ };
        return ack(id);
      }
      case "player.pose": {
        if (!this.live(c)) return fail(id, "CHARACTER_INACTIVE", "No active character");
        const now = this.now(); const w = CITY_MAP.walkable;
        if (cmd.x < w.x0 || cmd.x > w.x1 || cmd.z < w.z0 || cmd.z > w.z1) return fail(id, "MOVE_REJECTED", "Outside the map");
        // External 3D worlds (bridge clients) own their geometry and may drive; browser clients share the server's map and walk.
        const external = this.viaExternal || !!player.socketId?.startsWith("bridge:");
        if (!external && this.blocked(cmd.x, cmd.z)) return fail(id, "MOVE_REJECTED", CAR_RECTS.some((r) => insideRect(cmd, r, RADIUS)) ? "Inside a parked car" : "Inside a building");
        // Distance budget: metres accrue with real elapsed time at the speed ceiling and are capped at a short burst
        // window. Standing still never banks a teleport, and many small steps in one instant cannot add up.
        const speed = external ? RULES.maxPoseSpeed : RULES.playerSpeed * POSE_FOOT_FACTOR;
        const prev = this.poseBudget.get(c.id) ?? { metres: 0, at: c.spawnTimeMs };
        const metres = Math.min(speed * POSE_BURST_SEC, prev.metres + speed * Math.max(0, now - prev.at) / 1000);
        const d = dist(c, cmd);
        if (d > metres) { this.poseBudget.set(c.id, { metres, at: now }); return fail(id, "MOVE_REJECTED", "Too fast"); }
        // For the rules an external character stands at the nearest open point, so it can always be seen and captured.
        const at = external && this.blocked(cmd.x, cmd.z) ? this.nearestOpen(cmd.x, cmd.z) : { x: cmd.x, z: cmd.z };
        this.poseBudget.set(c.id, { metres: metres - d, at: now });
        this.poseAt.set(c.id, now); c.x = at.x; c.z = at.z; player.moveDir = { x: 0, z: 0 };
        return ack(id);
      }
      case "mission.interact": return this.missionInteract(player, c, cmd.missionId, id);
      case "market.buy": case "market.sell": {
        if (!player.walletId) return fail(id, "NOT_ALLOWED_FOR_ROLE", "Spectators cannot trade");
        const r = this.settleOrder({ walletId: player.walletId, actorId: player.id, actorName: player.nickname, actorKind: "HUMAN", ticker: cmd.ticker, side: cmd.type === "market.buy" ? "BUY" : "SELL", commandId: cmd.commandId }, {});
        return r.ok ? ack(id, { trade: r.trade, balanceAfter: r.balanceAfter, sharesAfter: r.sharesAfter }) : fail(id, "TRADE_REJECTED", r.message, r.reason);
      }
      case "capture.attempt": {
        if (player.role !== "HUNTER" || !c) {
          this.emit("CAPTURE_REJECTED", player.id, { commandId: cmd.commandId, captorId: player.id, targetCharacterId: cmd.targetCharacterId, reason: "NOT_ALLOWED_FOR_ROLE" });
          return fail(id, "NOT_ALLOWED_FOR_ROLE", "Only hunters can capture", "NOT_ALLOWED_FOR_ROLE");
        }
        const r = this.attemptCapture({ captorId: player.id, captorName: player.nickname, captorKind: "HUMAN", pos: c, active: this.live(c) && !this.isStunned(c.id, this.now()), walletId: player.walletId, selfCharacterId: c.id, commandId: cmd.commandId, targetId: cmd.targetCharacterId });
        return r.ok ? ack(id, { bounty: r.bounty }) : fail(id, "CAPTURE_REJECTED", `Capture rejected: ${r.reason}`, r.reason);
      }
      case "weapon.fire": return this.fireWeapon(player, c, cmd, id);
      case "crew.join": {
        if (player.role !== "HUNTER" || !c) return fail(id, "NOT_ALLOWED_FOR_ROLE", "Only hunters can join a crew", "NOT_ALLOWED_FOR_ROLE");
        const r = this.joinCrew(player, cmd.crewName);
        return r.ok ? ack(id, { crewId: r.crew.id, crewName: r.crew.name, memberPlayerIds: [...r.crew.memberPlayerIds] }) : fail(id, "CREW_REJECTED", r.message, r.reason);
      }
      case "crew.leave": {
        const left = this.leaveCrew(player.id, "LEFT");
        return left ? ack(id, { crewId: left.id }) : fail(id, "CREW_REJECTED", "You are not in a crew", "NOT_IN_CREW");
      }
      case "character.respawn": {
        if (!c || c.status !== "CAPTURED") return fail(id, "NOT_CAPTURED", "Character is not captured");
        const corr = this.nextId("story");
        c.status = "ARCHIVED";
        const arch = this.emit("CHARACTER_ARCHIVED", player.id, { characterId: c.id, ticker: c.ticker, playerId: player.id }, { correlationId: corr });
        const n = this.spawnCharacter(player, corr, arch.eventId);
        this.emit("CHARACTER_RESPAWNED", player.id, { playerId: player.id, previousCharacterId: c.id, characterId: n.id, ticker: n.ticker }, { correlationId: corr, causationId: arch.eventId });
        this.dirty.snapshot = true;
        return ack(id, { characterId: n.id, ticker: n.ticker });
      }
      case "demo.reset": case "demo.incident": case "demo.setMode": case "demo.runExperiment": {
        if (!player.isDirector) return fail(id, "FORBIDDEN", "Demo director only");
        if (cmd.type === "demo.reset") { this.reset(cmd.seed); return ack(id, { runId: this.state.runId }); }
        if (cmd.type === "demo.setMode") { this.setMode(cmd.mode); return ack(id, { mode: cmd.mode }); }
        if (cmd.type === "demo.runExperiment") {
          // Experiments spend real provider budget and hold the single runner: creating a room is not enough to start one.
          if (!this.trusted.has(player.id)) return fail(id, "FORBIDDEN", "Experiments need the director secret or the server's own machine");
          const exp = this.deps.onRunExperiment?.(this, cmd.durationSec ?? 45) ?? null;
          return exp ? ack(id, { experimentId: exp.id }) : fail(id, "INTERNAL", "Experiment runner unavailable or already running");
        }
        const target = (cmd.targetCharacterId ? this.state.characters[cmd.targetCharacterId] : undefined)
          ?? Object.values(this.state.characters).find((x) => x.role === "OUTLAW" && this.live(x));
        if (!target || !this.live(target)) return fail(id, "CHARACTER_INACTIVE", "No active outlaw to sight");
        const inc = this.incident("SIGHTING", target, `${target.nickname} was spotted acting suspiciously.`, {});
        return ack(id, { incidentId: inc.payload.incidentId, observerIds: inc.payload.observerIds });
      }
    }
  }

  setMode(mode: DecisionMode): void {
    const previousMode = this.state.mode;
    if (previousMode === mode) return;
    this.state.mode = mode;
    this.deps.scheduler.setMode(mode);
    this.emit("MODE_CHANGED", "director", { mode, previousMode });
    this.dirty.snapshot = true;
  }

  /** Seeded reset: same seed => identical initial positions, tickers and wallets. History stays in the store under the old runId. */
  reset(seed?: number): void {
    const prev = this.state;
    const players = Object.values(prev.players).filter((p) => p.connected || p.isBot).sort((a, b) => a.joinedAtMs - b.joinedAtMs || a.id.localeCompare(b.id));
    this.deps.scheduler.cancelAll();
    this.idem.clear(); this.playerRemap.clear();
    this.ammo.clear(); this.weaponReadyAt.clear(); this.stunnedUntil.clear();
    this.crews.clear(); this.crewOf.clear(); this.crewStunAt.clear();
    this.brains.reset();
    this.deps.memory.clear();
    this.initState(seed ?? prev.seed, prev.mode, prev.lastEventSequence);
    this.bootRun(false, prev.runId);
    this.state.counters.ply = prev.counters.ply ?? 0; // player ids stay stable across resets
    for (const old of players) {
      const id = old.id;
      const p: PlayerState = { ...old, characterId: null, walletId: null, moveDir: { x: 0, z: 0 } };
      this.state.players[id] = p;
      const corr = this.nextId("story");
      this.emit("PLAYER_JOINED", id, { playerId: id, nickname: p.nickname, role: p.role, reconnect: false }, { correlationId: corr });
      this.enrol(p, corr);
    }
    this.dirty.snapshot = true;
  }
  /** Players who proved more than room membership: joined from the server's own machine or with the director secret. */
  private trusted = new Set<string>();
  setTrusted(playerId: string, trusted: boolean): void { if (trusted) this.trusted.add(playerId); else this.trusted.delete(playerId); }
  /** old playerId -> new playerId after the latest reset (transport re-binds sockets with this). */
  readonly playerRemap = new Map<string, string>();

  // ---------- rules ----------
  private emitPrice(change: PriceChange | null, opts: EmitOpts): void {
    if (change) this.emit("MARKET_PRICE_UPDATED", "exchange", { ticker: change.ticker, referenceValue: change.referenceValue, spotPrice: change.spotPrice, previousSpotPrice: change.previousSpotPrice, reason: change.reason, heat: Math.round(change.heat), netBuyUnits: change.netBuyUnits }, opts);
  }
  private reprice(c: CharacterState, reason: PriceReason, opts: EmitOpts): void {
    if (c.ticker) this.emitPrice(this.deps.economy.reprice(this.state, c.ticker, this.now(), reason), opts);
  }
  private addHeat(c: CharacterState, amount: number, reason: string, opts: EmitOpts): void {
    c.heat = Math.min(RULES.heat.max, c.heat + amount);
    c.lastHeatGainAtMs = this.now();
    this.heatBucket.set(c.id, Math.floor(c.heat / 5));
    this.dirty.characters.add(c.id);
    if (c.status === "ACTIVE" && c.heat >= RULES.heat.wantedThreshold) {
      c.status = "WANTED";
      const wantedEv = this.emit("WANTED_STATUS_CHANGED", c.id, { characterId: c.id, ticker: c.ticker, wanted: true, heat: Math.round(c.heat), wantedLevel: wantedLevel(c.heat), reason }, opts);
      this.wantedEventOf.set(c.id, wantedEv.eventId);
      // A crew that already exists when an outlaw becomes wanted is market news too, not only a crew that forms afterwards.
      if (c.role === "OUTLAW") for (const crew of this.crews.values()) if (crew.memberPlayerIds.length) this.crewMarketNote(crew, wantedEv, c.id);
    }
    this.reprice(c, "HEAT", opts);
  }

  private missionInteract(player: PlayerState, c: CharacterState | null, missionId: string, id: string): CommandAck {
    if (player.role !== "OUTLAW") return fail(id, "NOT_ALLOWED_FOR_ROLE", "Only outlaws run missions");
    if (!this.live(c)) return fail(id, "CHARACTER_INACTIVE", "No active character");
    const m = this.state.missions[missionId];
    if (!m || m.status !== "AVAILABLE") return fail(id, "MISSION_UNAVAILABLE", "Mission is not available");
    if (dist(c, m) > RULES.missionInteractRadius) return fail(id, "OUT_OF_RANGE", "Too far from the objective");
    const now = this.now(); const corr = this.nextId("story");
    m.status = "COMPLETED"; m.completedByCharacterId = c.id; m.completedAtMs = now;
    c.notorietyPoints += RULES.notoriety.perMission; c.completedMissions += 1;
    this.nextMissionAtMs = now + RULES.missionRespawnMs;
    const heatGained = Math.min(RULES.heat.perMission, RULES.heat.max - c.heat);
    const done = this.emit("MISSION_COMPLETED", player.id, { missionId, characterId: c.id, ticker: c.ticker, x: m.x, z: m.z, notorietyGained: RULES.notoriety.perMission, heatGained, notorietyPoints: c.notorietyPoints, completedMissions: c.completedMissions, heat: Math.round(c.heat + heatGained) }, { correlationId: corr });
    const opts = { causationId: done.eventId, correlationId: corr };
    this.reprice(c, "MISSION", opts);
    this.addHeat(c, RULES.heat.perMission, "mission heat", opts);
    this.incident("MISSION", c, `${c.nickname} grabbed "${m.label}".`, opts, { x: m.x, z: m.z });
    return ack(id, { missionId, notorietyPoints: c.notorietyPoints, completedMissions: c.completedMissions });
  }

  private incident(kind: "MISSION" | "SIGHTING", c: CharacterState, description: string, opts: EmitOpts, at: Vec2 = { x: c.x, z: c.z }): GameEvent<"INCIDENT_OCCURRED"> {
    const now = this.now(); const correlationId = opts.correlationId ?? this.nextId("story");
    const observers = Object.values(this.state.npcs).filter((n) => dist(n, at) <= RULES.perceptionRadius && hasLineOfSight(n, at)).sort((a, b) => a.id.localeCompare(b.id));
    const incidentId = this.nextId("inc");
    const inc = this.emit("INCIDENT_OCCURRED", c.id, { incidentId, kind, characterId: c.id, ticker: c.ticker, x: at.x, z: at.z, description, observerIds: observers.map((o) => o.id) }, { causationId: opts.causationId, correlationId });
    this.storyOf.set(c.id, correlationId);
    for (const n of observers) {
      const d = Math.round(dist(n, at) * 10) / 10;
      const summary = `Saw it directly from ${d} m: ${description}`;
      const mem = this.deps.memory.remember({
        agentId: n.id, factType: "INCIDENT", subjectCharacterId: c.id, subjectTicker: c.ticker, eventId: inc.eventId, sourceActorId: n.id, sourceKind: "DIRECT",
        sourceMemoryId: null, hop: 0, observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.INCIDENT, confidence: RULES.memory.confidence.DIRECT, summary,
        payload: { incidentId, kind, x: at.x, z: at.z, description, ticker: c.ticker }, citation: null,
      }, this.nextId("mem"));
      const obs = this.emit("NPC_OBSERVED", n.id, { npcId: n.id, memoryId: mem.id, factType: "INCIDENT", subjectCharacterId: c.id, distance: d, incidentId, summary }, { causationId: inc.eventId, correlationId });
      if (n.role === "POLICE") this.publishBulletin(n.id, c.id, at, { causationId: obs.eventId, correlationId });
    }
    return inc;
  }

  private settleOrder(o: { walletId: string; actorId: string; actorName: string; actorKind: "HUMAN" | "NPC"; ticker: string; side: TradeSide; commandId: string }, opts: EmitOpts): OrderResult {
    const s = this.state; const now = this.now();
    const tradeId = `trd_${pad((s.counters.trd ?? 0) + 1, 4)}`;
    const r = this.deps.economy.executeOrder(s, { ...o, qty: RULES.trade.qtyPerOrder, tradeId }, now);
    if (!r.ok) {
      this.emit("TRADE_REJECTED", o.actorId, { commandId: o.commandId, walletId: o.walletId, actorId: o.actorId, actorKind: o.actorKind, ticker: o.ticker, side: o.side, qty: RULES.trade.qtyPerOrder, reason: r.reason }, opts);
      return r;
    }
    s.counters.trd = (s.counters.trd ?? 0) + 1;
    // The economy records the trade itself; test the id (not the length, which stops growing once the list is capped).
    if (s.trades[s.trades.length - 1]?.tradeId !== r.trade.tradeId) s.trades.push(r.trade);
    if (s.trades.length > 200) s.trades.splice(0, s.trades.length - 200);
    const subject = s.listings[o.ticker]?.characterId;
    const correlationId = opts.correlationId ?? (subject ? this.storyOf.get(subject) : undefined) ?? this.nextId("story");
    const ev = this.emit("TRADE_EXECUTED", o.actorId, { tradeId: r.trade.tradeId, commandId: o.commandId, walletId: o.walletId, actorId: o.actorId, actorName: o.actorName, actorKind: o.actorKind, ticker: o.ticker, side: o.side, qty: r.trade.qty, executionPrice: r.trade.executionPrice, balanceAfter: r.balanceAfter, sharesAfter: r.sharesAfter }, { causationId: opts.causationId, correlationId });
    this.emitPrice(r.priceChange, { causationId: ev.eventId, correlationId });
    return r;
  }

  private attemptCapture(a: { captorId: string; captorName: string; captorKind: "HUMAN" | "NPC"; pos: Vec2; active: boolean; walletId: string | null; selfCharacterId: string | null; commandId: string; targetId: string }): { ok: true; bounty: number } | { ok: false; reason: CaptureRejectReason } {
    const s = this.state; const now = this.now(); const t = s.characters[a.targetId];
    const reject = (reason: CaptureRejectReason) => {
      this.emit("CAPTURE_REJECTED", a.captorId, { commandId: a.commandId, captorId: a.captorId, targetCharacterId: a.targetId, reason });
      return { ok: false as const, reason };
    };
    // Every check and the whole transition run in this one synchronous section: the first valid capture wins.
    if (!t) return reject("TARGET_NOT_FOUND");
    if (a.selfCharacterId === t.id) return reject("SELF_CAPTURE");
    if (t.status === "CAPTURED" || t.status === "ARCHIVED") return reject("TARGET_ALREADY_CAPTURED");
    if (t.status !== "WANTED") return reject("TARGET_NOT_WANTED");
    if (!a.active) return reject("CAPTOR_INACTIVE");
    if (now - (this.captureAttemptAt.get(a.captorId) ?? -Infinity) < RULES.captureCooldownMs) return reject("COOLDOWN");
    this.captureAttemptAt.set(a.captorId, now);
    if (dist(a.pos, t) > RULES.captureRadius) return reject("OUT_OF_RANGE");
    if (!hasLineOfSight(a.pos, t)) return reject("LINE_OF_SIGHT_BLOCKED");

    const eco = this.deps.economy;
    const bounty = a.captorKind === "HUMAN" && a.walletId ? eco.bountyFor(s, t.id, now) : 0;
    // An officer who makes the capture earns the same bounty a human hunter would (paid after the capture event).
    const npcBounty = a.captorKind === "NPC" ? eco.bountyFor(s, t.id, now) : 0;
    const q = t.ticker ? eco.quote(s, t.ticker, now) : null;
    t.status = "CAPTURED"; t.captureTimeMs = now; t.capturedBy = a.captorId;
    const halt = t.ticker ? eco.haltListing(s, t.ticker, now) : null;
    // Crew split: crewmates who are live, not stunned and close to the target share the bounty equally (remainder to
    // the captor). If the captor stunned a crewmate shortly before, it is a double-cross: the captor takes it all.
    const crew = a.captorKind === "HUMAN" ? this.crews.get(this.crewOf.get(a.captorId) ?? "") : undefined;
    const mates = crew ? crew.memberPlayerIds.filter((pid) => pid !== a.captorId) : [];
    const betrayed = bounty > 0 ? mates.filter((pid) => now - (this.crewStunAt.get(`${a.captorId}>${pid}`) ?? -Infinity) <= RULES.crew.betrayalWindowMs) : [];
    const assists = bounty > 0 && betrayed.length === 0 ? mates.filter((pid) => {
      const p = s.players[pid]; const mc = p?.characterId ? s.characters[p.characterId] : undefined;
      return !!p?.walletId && this.live(mc ?? null) && !this.isStunned(mc!.id, now) && dist(mc!, t) <= RULES.crew.assistRadiusM;
    }) : [];
    const each = Math.floor(bounty / (assists.length + 1));
    const captorAmount = bounty - each * assists.length;
    const captorBalanceAfter = bounty > 0 && a.walletId ? eco.credit(s, a.walletId, captorAmount) : null;
    const shares = crew && bounty > 0 && a.walletId && captorBalanceAfter !== null ? [{ playerId: a.captorId, walletId: a.walletId, amount: captorAmount, balanceAfter: captorBalanceAfter }] : undefined;
    if (shares) for (const pid of assists) {
      const wid = s.players[pid]!.walletId!;
      shares.push({ playerId: pid, walletId: wid, amount: each, balanceAfter: eco.credit(s, wid, each) });
      this.dirty.wallets.add(wid);
    }
    if (a.walletId) this.dirty.wallets.add(a.walletId);
    const owner = s.players[t.playerId]; if (owner) owner.moveDir = { x: 0, z: 0 };
    for (const n of Object.values(s.npcs)) if (n.targetCharacterId === t.id) this.setNpcActivity(n.id, n.role === "POLICE" ? "PATROL" : "IDLE", {});
    const correlationId = this.storyOf.get(t.id) ?? this.nextId("story");
    // FR-061: an officer's capture is caused by its pursuit (its latest applied action in this story, else its latest
    // sighting of the target); a hunter's capture by the event that made the target wanted. The timeline can then
    // trace capture -> pursuit -> sighting -> wanted -> bulletin -> report -> witness -> mission.
    const lastAction = a.captorKind === "NPC" ? this.actionEventOf.get(a.captorId) : undefined;
    const cause = (lastAction && lastAction.correlationId === correlationId ? lastAction.eventId : undefined)
      ?? (a.captorKind === "NPC" ? this.sightingEventOf.get(`${a.captorId}:${t.id}`) : undefined)
      ?? this.wantedEventOf.get(t.id) ?? null;
    const cap = this.emit("CAPTURE_SUCCEEDED", a.captorId, {
      captureId: this.nextId("cap"), commandId: a.commandId, characterId: t.id, ticker: t.ticker, captorId: a.captorId, captorName: a.captorName, captorKind: a.captorKind,
      captorWalletId: bounty > 0 ? a.walletId : null, bounty, captorBalanceAfter, referenceAtCapture: halt?.referenceAtHalt ?? q?.referenceValue ?? 0,
      spotBeforeCapture: halt?.finalSpotBeforeHalt ?? q?.spotPrice ?? 0, survivalSeconds: Math.floor((now - t.spawnTimeMs) / 1000),
      ...(crew ? { shares, crewId: crew.id, crewName: crew.name, doubleCross: betrayed.length > 0 } : {}),
    }, { correlationId, causationId: cause });
    if (crew && betrayed.length) this.doubleCross(crew, a.captorId, betrayed, bounty, t, { correlationId, causationId: cap.eventId });
    // Every agent is out for itself: the capturing officer earns the bounty, and the witness whose report put the
    // target on the wanted list earns a tip.
    const payOpts = { correlationId, causationId: cap.eventId };
    if (npcBounty > 0) this.creditNpc(a.captorId, npcBounty, `bounty for ${t.ticker ?? t.nickname}`, payOpts);
    const totalBounty = bounty + npcBounty;
    if (totalBounty > 0) {
      const report = [...s.messages].reverse().find((m) => m.kind === "REPORT" && m.subjectCharacterId === t.id && s.npcs[m.senderId]?.role === "WITNESS");
      if (report) this.creditNpc(report.senderId, Math.max(RULES.profit.witnessTipMin, Math.round(totalBounty * RULES.profit.witnessTipShare)), `tip: report led to the capture of ${t.ticker ?? t.nickname}`, payOpts);
    }
    if (t.ticker && halt) {
      const o = { causationId: cap.eventId, correlationId };
      this.emit("LISTING_HALTED", "exchange", { ticker: t.ticker, characterId: t.id, status: "HALTED_CAPTURED", finalSpotBeforeHalt: halt.finalSpotBeforeHalt }, o);
      this.emit("MARKET_PRICE_UPDATED", "exchange", { ticker: t.ticker, referenceValue: halt.referenceAtHalt, spotPrice: 0, previousSpotPrice: halt.finalSpotBeforeHalt, reason: "CAPTURE", heat: Math.round(t.heat), netBuyUnits: 0 }, o);
      for (const w of Object.values(s.wallets)) if (w.holdings[t.ticker]?.shares) this.dirty.wallets.add(w.id);
    }
    return { ok: true, bounty };
  }

  // ---------- hunter crews ----------
  hunterCrews(): Array<{ id: string; name: string; memberPlayerIds: EntityId[] }> { return [...this.crews.values()].map((k) => ({ id: k.id, name: k.name, memberPlayerIds: [...k.memberPlayerIds] })); }
  /** A crew forming or growing while an outlaw is wanted is public market news: each trader hears it and decides for itself. */
  private crewMarketNote(crew: CrewView, cause: GameEvent, onlyCharacterId?: EntityId): void {
    const s = this.state; const now = this.now();
    for (const c of Object.values(s.characters)) {
      if (c.role !== "OUTLAW" || c.status !== "WANTED" || !c.ticker) continue;
      if (onlyCharacterId && c.id !== onlyCharacterId) continue;
      const n = crew.memberPlayerIds.length;
      const summary = `Hunter crew ${crew.name} (${n} hunter${n === 1 ? "" : "s"}) is now after ${c.ticker}: a capture would send it to zero.`;
      const o = { causationId: cause.eventId, correlationId: this.storyOf.get(c.id) ?? cause.correlationId };
      for (const t of Object.values(s.npcs)) {
        if (t.role !== "TRADER") continue;
        const memId = this.nextId("mem");
        this.emitWith("NPC_OBSERVED", t.id, { npcId: t.id, memoryId: memId, factType: "RUMOR", subjectCharacterId: c.id, distance: 0, incidentId: null, summary }, o, (e) => {
          this.deps.memory.remember({
            agentId: t.id, factType: "RUMOR", subjectCharacterId: c.id, subjectTicker: c.ticker, eventId: e.eventId, sourceActorId: "public", sourceKind: "BULLETIN", sourceMemoryId: null, hop: 1,
            observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.RUMOR, confidence: RULES.memory.confidence.BULLETIN, summary, payload: { ticker: c.ticker, crew: crew.name, hunters: n }, citation: null,
          }, memId);
        });
        this.dirty.agents.add(t.id);
      }
    }
  }
  private joinCrew(player: PlayerState, rawName: string): { ok: true; crew: CrewView } | { ok: false; reason: string; message: string } {
    const name = rawName.trim().toUpperCase();
    let crew = [...this.crews.values()].find((k) => k.name === name);
    if (crew && crew.memberPlayerIds.includes(player.id)) return { ok: true, crew };
    if (crew && crew.memberPlayerIds.length >= RULES.crew.maxMembers) return { ok: false, reason: "CREW_FULL", message: `Crew ${name} is full (${RULES.crew.maxMembers})` };
    this.leaveCrew(player.id, "SWITCHED");
    const created = !crew;
    if (!crew) { crew = { id: this.nextId("crew"), name, memberPlayerIds: [], formedAtMs: this.now() }; this.crews.set(crew.id, crew); }
    crew.memberPlayerIds.push(player.id);
    this.crewOf.set(player.id, crew.id);
    const joined = this.emit("CREW_JOINED", player.id, { crewId: crew.id, crewName: crew.name, playerId: player.id, nickname: player.nickname, created, memberPlayerIds: [...crew.memberPlayerIds] });
    this.touchCrew(crew, player.id);
    this.crewMarketNote(crew, joined);
    return { ok: true, crew };
  }
  private leaveCrew(playerId: string, reason: "LEFT" | "SWITCHED" | "EJECTED", opts: EmitOpts = {}): CrewView | null {
    const crew = this.crews.get(this.crewOf.get(playerId) ?? "");
    if (!crew) return null;
    crew.memberPlayerIds = crew.memberPlayerIds.filter((pid) => pid !== playerId);
    this.crewOf.delete(playerId);
    if (crew.memberPlayerIds.length === 0) this.crews.delete(crew.id);
    this.emit("CREW_LEFT", playerId, { crewId: crew.id, crewName: crew.name, playerId, nickname: this.state.players[playerId]?.nickname ?? playerId, reason, memberPlayerIds: [...crew.memberPlayerIds] }, opts);
    this.touchCrew(crew, playerId);
    return crew;
  }
  private touchCrew(crew: CrewView, alsoPlayerId: string): void {
    for (const pid of [...crew.memberPlayerIds, alsoPlayerId]) { const cid = this.state.players[pid]?.characterId; if (cid) this.dirty.characters.add(cid); }
    this.dirty.snapshot = true;
  }
  /** A hunter stunned a crewmate and then took the capture alone: public, witnessed, and remembered by the informants. */
  private doubleCross(crew: CrewView, betrayerId: string, victimIds: string[], bounty: number, at: Vec2, opts: EmitOpts): void {
    const s = this.state; const now = this.now();
    const betrayer = s.players[betrayerId]; const name = betrayer?.nickname ?? betrayerId;
    const victimNames = victimIds.map((pid) => s.players[pid]?.nickname ?? pid);
    const ev = this.emit("CREW_BETRAYED", betrayerId, { crewId: crew.id, crewName: crew.name, betrayerPlayerId: betrayerId, betrayerName: name, victimPlayerIds: [...victimIds], victimNames, bounty }, opts);
    const o = { causationId: ev.eventId, correlationId: opts.correlationId };
    this.leaveCrew(betrayerId, "EJECTED", o);
    const bc = betrayer?.characterId ? s.characters[betrayer.characterId] : undefined;
    const summary = `Crew ${crew.name} turned on its own: hunter ${name} stunned ${victimNames.join(" and ")} and took the full bounty of V$${bounty}.`;
    // Anyone in sight witnesses it through the normal perception path and decides for themselves.
    if (bc) this.incident("SIGHTING", bc, summary, o, { x: at.x, z: at.z });
    // Word reaches both informants as a public fact; whether they pass it on is their own decision.
    for (const n of Object.values(s.npcs)) {
      if (n.role !== "INFORMANT") continue;
      const memId = this.nextId("mem");
      this.emitWith("NPC_OBSERVED", n.id, { npcId: n.id, memoryId: memId, factType: "RUMOR", subjectCharacterId: bc?.id ?? null, distance: Math.round(dist(n, at)), incidentId: null, summary }, o, (e) => {
        this.deps.memory.remember({
          agentId: n.id, factType: "RUMOR", subjectCharacterId: bc?.id ?? null, subjectTicker: null, eventId: e.eventId, sourceActorId: "public", sourceKind: "BULLETIN", sourceMemoryId: null, hop: 1,
          observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.RUMOR, confidence: RULES.memory.confidence.BULLETIN, summary, payload: { x: at.x, z: at.z, crew: crew.name, bounty }, citation: null,
        }, memId);
      });
      this.dirty.agents.add(n.id);
    }
  }

  // ---------- stun pulse ----------
  private isStunned(id: string, now: number): boolean { return (this.stunnedUntil.get(id) ?? 0) > now; }

  /**
   * One non-lethal weapon. Every check runs on the server: role, live character, not stunned, cooldown, ammo, range and
   * line of sight (buildings block, cars do not). A hit stuns a human character or a police NPC; nothing else can be hit.
   */
  private fireWeapon(player: PlayerState, c: CharacterState | null, cmd: { targetId?: string; dirX?: number; dirZ?: number; commandId: string }, id: string): CommandAck {
    const W = RULES.weapon; const now = this.now(); const s = this.state;
    const no = (message: string, reason: string) => fail(id, "WEAPON_REJECTED", message, reason);
    if (player.role === "SPECTATOR" || !c) return fail(id, "NOT_ALLOWED_FOR_ROLE", "Spectators cannot fire");
    if (!this.live(c)) return fail(id, "CHARACTER_INACTIVE", "No active character");
    if (this.isStunned(c.id, now)) return no("You are stunned", "STUNNED");
    if (now < (this.weaponReadyAt.get(c.id) ?? 0)) return no("Pulse is recharging", "COOLDOWN");
    const ammo = this.ammo.get(c.id) ?? W.ammo;
    if (ammo <= 0) return no("Out of charges", "NO_AMMO");

    type Target = { id: string; name: string; kind: "CHARACTER" | "NPC"; x: number; z: number };
    const candidates: Target[] = [
      ...Object.values(s.characters).filter((t) => t.id !== c.id && this.live(t)).map((t) => ({ id: t.id, name: t.nickname, kind: "CHARACTER" as const, x: t.x, z: t.z })),
      ...Object.values(s.npcs).filter((n) => n.role === "POLICE").map((n) => ({ id: n.id, name: n.name, kind: "NPC" as const, x: n.x, z: n.z })),
    ];
    const reachable = (t: Target) => dist(c, t) <= W.rangeM && hasLineOfSight(c, t);
    let hit: Target | null = null; let to: Vec2;
    if (cmd.targetId !== undefined) {
      const t = candidates.find((k) => k.id === cmd.targetId);
      if (!t) return no("That target cannot be hit", "INVALID_TARGET");
      if (dist(c, t) > W.rangeM) return no("Out of range", "OUT_OF_RANGE");
      if (!hasLineOfSight(c, t)) return no("No line of sight", "LINE_OF_SIGHT_BLOCKED");
      hit = t; to = { x: t.x, z: t.z };
    } else {
      const m = Math.hypot(cmd.dirX ?? 0, cmd.dirZ ?? 0);
      if (!m) return no("Aim at a target or give a direction", "NO_AIM");
      const dx = (cmd.dirX ?? 0) / m, dz = (cmd.dirZ ?? 0) / m; const cos = Math.cos((W.coneDeg * Math.PI) / 180);
      for (const t of candidates) {
        const d = dist(c, t); if (d < 0.01 || !reachable(t)) continue;
        if (((t.x - c.x) * dx + (t.z - c.z) * dz) / d < cos) continue;
        if (!hit || d < dist(c, hit)) hit = t;
      }
      to = hit ? { x: hit.x, z: hit.z } : { x: c.x + dx * W.rangeM, z: c.z + dz * W.rangeM };
    }

    this.ammo.set(c.id, ammo - 1); this.weaponReadyAt.set(c.id, now + W.cooldownMs);
    this.dirty.characters.add(c.id);
    const correlationId = this.storyOf.get(c.id) ?? this.nextId("story");
    const shot = this.emit("WEAPON_FIRED", c.id, { shooterId: c.id, shooterName: c.nickname, shooterKind: "HUMAN", x: c.x, z: c.z, toX: to.x, toZ: to.z, hitId: hit?.id ?? null, hitKind: hit?.kind ?? null, ammoLeft: ammo - 1 }, { correlationId });
    const opts = { causationId: shot.eventId, correlationId };
    if (hit) {
      const untilMs = now + W.stunMs;
      this.stunnedUntil.set(hit.id, untilMs);
      if (hit.kind === "CHARACTER") { const owner = s.players[s.characters[hit.id]!.playerId]; if (owner) owner.moveDir = { x: 0, z: 0 }; this.dirty.characters.add(hit.id);
        // Stunning a crewmate is allowed and remembered: a capture soon after is a double-cross.
        if (owner && this.crewOf.get(owner.id) && this.crewOf.get(owner.id) === this.crewOf.get(player.id)) this.crewStunAt.set(`${player.id}>${owner.id}`, now); }
      else { const n = s.npcs[hit.id]!; n.lastCaptureAttemptAtMs = Math.max(n.lastCaptureAttemptAtMs, untilMs - RULES.captureCooldownMs); this.dirty.agents.add(hit.id); }
      this.emit("STUNNED", hit.id, { targetId: hit.id, targetName: hit.name, targetKind: hit.kind, untilMs, byId: c.id }, opts);
    }
    // An outlaw firing in the street is something the city can see: heat, and an incident nearby NPCs may witness.
    if (c.role === "OUTLAW") {
      this.addHeat(c, W.heatPerShot, "fired a stun pulse", opts);
      this.incident("SIGHTING", c, `Shots fired: ${c.nickname}${c.ticker ? ` (${c.ticker})` : ""} fired a stun pulse${hit ? ` at ${hit.name}` : ""}.`, opts);
    }
    return ack(id, { hitId: hit?.id ?? null, ammoLeft: ammo - 1, readyAtMs: now + W.cooldownMs });
  }

  // ---------- tick ----------
  tick(): void {
    const s = this.state; const now = this.now();
    const dt = Math.min(0.25, Math.max(0, (now - this.lastTickAtMs) / 1000));
    this.lastTickAtMs = now; s.tick++;

    for (const p of Object.values(s.players)) {
      const c = this.character(p);
      if (!this.live(c) || (!p.moveDir.x && !p.moveDir.z)) continue;
      if (this.isStunned(c.id, now)) continue;
      this.step(c, c.x + p.moveDir.x * RULES.playerSpeed * dt, c.z + p.moveDir.z * RULES.playerSpeed * dt);
      this.poseAt.set(c.id, now);
    }
    this.deliverMessages(now);
    this.moveNpcs(now, dt);
    this.policeSight(now);
    this.decayHeat(now, dt);

    for (const l of Object.values(s.listings)) {
      if (l.status !== "ACTIVE") continue;
      const c = s.characters[l.characterId]; if (!c) continue;
      const stepNo = Math.floor((now - c.spawnTimeMs) / 1000 / RULES.price.survivalStepSeconds);
      if (stepNo !== (this.survivalStep.get(l.ticker) ?? 0)) { this.survivalStep.set(l.ticker, stepNo); this.reprice(c, "SURVIVAL_TICK", { correlationId: this.storyOf.get(c.id) }); this.dirty.listings.add(l.ticker); }
    }
    if (this.nextMissionAtMs !== null && now >= this.nextMissionAtMs) { this.nextMissionAtMs = null; this.spawnMission(); }
    try { this.brains.onTick(now); } catch (err) { console.error("[brains] onTick failed", err); }
  }

  private blocked(x: number, z: number): boolean {
    const w = CITY_MAP.walkable;
    if (x < w.x0 || x > w.x1 || z < w.z0 || z > w.z1) return true;
    return CITY_MAP.buildings.some((b) => insideRect({ x, z }, b, RADIUS)) || CAR_RECTS.some((r) => insideRect({ x, z }, r, RADIUS));
  }
  /** Nearest point outside every building and parked car, inside the map. */
  private nearestOpen(x: number, z: number): Vec2 {
    let p: Vec2 = { x, z };
    const pad = RADIUS + 0.05;
    for (let i = 0; i < 6 && this.blocked(p.x, p.z); i++) {
      const here = p;
      const r = [...CITY_MAP.buildings, ...CAR_RECTS].find((b) => insideRect(here, b, RADIUS));
      if (!r) break;
      const exits: Vec2[] = [{ x: r.x0 - pad, z: here.z }, { x: r.x1 + pad, z: here.z }, { x: here.x, z: r.z0 - pad }, { x: here.x, z: r.z1 + pad }];
      exits.sort((a, b) => dist(a, here) - dist(b, here));
      p = exits.find((e) => !this.blocked(e.x, e.z)) ?? exits[0]!;
    }
    const w = CITY_MAP.walkable;
    return { x: Math.max(w.x0, Math.min(w.x1, p.x)), z: Math.max(w.z0, Math.min(w.z1, p.z)) };
  }
  /** Axis-separated move with sliding. Returns true if the entity moved at all. */
  private step(e: Vec2, nx: number, nz: number): boolean {
    let moved = false;
    if (!this.blocked(nx, e.z)) { moved = moved || nx !== e.x; e.x = nx; }
    if (!this.blocked(e.x, nz)) { moved = moved || nz !== e.z; e.z = nz; }
    return moved;
  }
  /** Moves an NPC toward `to` along a building-avoiding route. Returns true on arrival. */
  private walk(n: NpcState, to: Vec2, speed: number, dt: number): boolean {
    if (dist(n, to) < 0.3) { this.nav.delete(n.id); return true; }
    const now = this.now();
    let nav = this.nav.get(n.id);
    if (!nav || !nav.path.length || dist(nav.goal, to) > 1 || now - nav.at > 700) {
      nav = { goal: { x: to.x, z: to.z }, path: findPath(n, to, RADIUS) ?? [{ x: to.x, z: to.z }], at: now };
      this.nav.set(n.id, nav);
    }
    let remaining = speed * dt;
    while (remaining > 1e-6 && nav.path.length) {
      const wp = nav.path[0]!; const d = dist(n, wp);
      if (d < 0.05) { nav.path.shift(); continue; }
      const k = Math.min(1, remaining / d);
      if (!this.step(n, n.x + (wp.x - n.x) * k, n.z + (wp.z - n.z) * k)) { this.nav.delete(n.id); break; } // boxed in: re-route next tick
      remaining -= d * k;
      if (k >= 1) nav.path.shift();
    }
    return dist(n, to) < 0.3;
  }

  private deliverMessages(now: number): void {
    for (const m of this.state.messages) {
      if (m.deliveredAtMs !== null || m.deliverAtMs > now) continue;
      m.deliveredAtMs = now;
      const subject = m.subjectCharacterId ? this.state.characters[m.subjectCharacterId] : undefined;
      const factType = m.kind === "REPORT" ? "INCIDENT" : m.kind === "BULLETIN" ? "WANTED_BULLETIN" : "RUMOR";
      const memId = this.nextId("mem");
      const opts = { causationId: m.sentEventId, correlationId: m.correlationId };
      const delivered = this.emitWith("AGENT_MESSAGE_DELIVERED", m.recipientId, { messageId: m.id, senderId: m.senderId, recipientId: m.recipientId, kind: m.kind, memoryId: memId, hop: m.hop }, opts, (e) => {
        this.deps.memory.remember({
          agentId: m.recipientId, factType, subjectCharacterId: m.subjectCharacterId, subjectTicker: subject?.ticker ?? null, eventId: e.eventId, sourceActorId: m.senderId,
          sourceKind: "MESSAGE", sourceMemoryId: m.sourceMemoryId, hop: m.hop, observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs[factType],
          confidence: RULES.memory.confidence[m.kind], summary: m.summary, payload: { ...m.payload, messageId: m.id, messageKind: m.kind }, citation: null,
        }, memId);
      });
      const r = this.state.npcs[m.recipientId];
      if (m.kind === "REPORT" && r?.role === "POLICE" && m.subjectCharacterId) {
        const x = Number(m.payload.x), z = Number(m.payload.z);
        this.publishBulletin(r.id, m.subjectCharacterId, Number.isFinite(x) && Number.isFinite(z) ? { x, z } : { x: r.x, z: r.z }, { causationId: delivered.eventId, correlationId: m.correlationId });
      }
    }
  }

  private sees(n: Vec2, c: Vec2, radius: number = RULES.perceptionRadius): boolean { return dist(n, c) <= radius && hasLineOfSight(n, c); }

  private moveNpcs(now: number, dt: number): void {
    for (const n of Object.values(this.state.npcs)) {
      if (this.isStunned(n.id, now)) { const p = this.pursuit.get(n.id); if (p) p.lastSeenAtMs = now; continue; } // frozen; the chase resumes afterwards
      if (n.activity === "PURSUE") {
        const t = n.targetCharacterId ? this.state.characters[n.targetCharacterId] : undefined;
        const p = this.pursuit.get(n.id) ?? { lastSeenAtMs: now, lastKnown: { x: n.x, z: n.z }, arrivedAtMs: null, departAtMs: now, pace: RULES.policePursuitSpeed };
        this.pursuit.set(n.id, p);
        if (!t || t.status !== "WANTED") { this.setNpcActivity(n.id, n.role === "POLICE" ? "PATROL" : "IDLE", {}); continue; }
        if (this.sees(n, t, RULES.perceptionRadius * 1.5)) { p.lastSeenAtMs = now; p.lastKnown = { x: t.x, z: t.z }; }
        else if (now - p.lastSeenAtMs > PURSUIT_LOST_MS) { this.setActivity(n.id, "INVESTIGATE", { moveTarget: p.lastKnown, targetCharacterId: t.id }, true); continue; }
        this.walk(n, p.lastKnown, RULES.policePursuitSpeed, dt);
        if (dist(n, t) <= RULES.captureRadius && now - n.lastCaptureAttemptAtMs >= RULES.captureCooldownMs) {
          n.lastCaptureAttemptAtMs = now;
          this.attemptCapture({ captorId: n.id, captorName: n.name, captorKind: "NPC", pos: n, active: true, walletId: null, selfCharacterId: null, commandId: this.nextId("ncap"), targetId: t.id });
        }
      } else if (n.activity === "INVESTIGATE") {
        const p = this.pursuit.get(n.id) ?? { lastSeenAtMs: now, lastKnown: n.moveTarget ?? { x: n.x, z: n.z }, arrivedAtMs: null, departAtMs: now, pace: RULES.npcWalkSpeed };
        this.pursuit.set(n.id, p);
        if (now < p.departAtMs) continue; // still radioing in: has not set off yet
        const arrived = this.walk(n, n.moveTarget ?? p.lastKnown, p.pace, dt);
        if (arrived && p.arrivedAtMs === null) p.arrivedAtMs = now;
        const done = (p.arrivedAtMs !== null && now - p.arrivedAtMs > INVESTIGATE_LINGER_MS) || now - p.departAtMs > INVESTIGATE_GIVE_UP_MS;
        if (done) this.setNpcActivity(n.id, n.patrol.length ? "PATROL" : "IDLE", {});
      } else if (n.activity === "PATROL" && n.patrol.length) {
        if (this.walk(n, n.patrol[n.patrolIndex % n.patrol.length]!, RULES.npcWalkSpeed, dt)) n.patrolIndex = (n.patrolIndex + 1) % n.patrol.length;
      } else if (dist(n, n.home) > 0.5) this.walk(n, n.home, RULES.npcWalkSpeed, dt);
    }
  }

  private policeSight(now: number): void {
    const police = Object.values(this.state.npcs).filter((n) => n.role === "POLICE");
    for (const c of Object.values(this.state.characters)) {
      if (c.role !== "OUTLAW" || !this.live(c)) continue;
      for (const n of police) {
        if (!this.sees(n, c)) continue;
        c.lastPoliceSightingAtMs = now;
        if (c.status !== "WANTED") continue;
        const key = `${n.id}:${c.id}`;
        if (now - (this.sightingAt.get(key) ?? -Infinity) < RULES.heat.sightingCooldownMs) continue;
        this.sightingAt.set(key, now);
        const d = Math.round(dist(n, c) * 10) / 10; const correlationId = this.storyOf.get(c.id) ?? this.nextId("story");
        const summary = `Has eyes on wanted ${c.nickname}${c.ticker ? ` (${c.ticker})` : ""}, ${d} m away.`;
        const memId = this.nextId("mem");
        const obs = this.emitWith("NPC_OBSERVED", n.id, { npcId: n.id, memoryId: memId, factType: "SIGHTING", subjectCharacterId: c.id, distance: d, incidentId: null, summary }, { correlationId, causationId: this.wantedEventOf.get(c.id) ?? null }, (e) => {
          this.deps.memory.remember({
            agentId: n.id, factType: "SIGHTING", subjectCharacterId: c.id, subjectTicker: c.ticker, eventId: e.eventId, sourceActorId: n.id, sourceKind: "DIRECT", sourceMemoryId: null, hop: 0,
            observedAtMs: now, expiresAtMs: now + RULES.memory.ttlMs.SIGHTING, confidence: RULES.memory.confidence.DIRECT, summary, payload: { x: c.x, z: c.z, ticker: c.ticker }, citation: null,
          }, memId);
        });
        this.sightingEventOf.set(key, obs.eventId);
        this.addHeat(c, RULES.heat.perPoliceSighting, "police sighting", { causationId: obs.eventId, correlationId });
      }
    }
  }

  private decayHeat(now: number, dt: number): void {
    const hunted = new Set<string>();
    for (const n of Object.values(this.state.npcs)) if (n.targetCharacterId && (n.activity === "INVESTIGATE" || n.activity === "PURSUE")) hunted.add(n.targetCharacterId);
    for (const c of Object.values(this.state.characters)) {
      if (c.role !== "OUTLAW" || !this.live(c) || c.heat <= 0) continue;
      if (now - c.lastHeatGainAtMs < RULES.heat.decayGraceMs || now - c.lastPoliceSightingAtMs < 500) continue;
      // Heat only cools once nobody is on the case: an officer who is investigating or pursuing this character keeps it up,
      // so a slow, dispatched search cannot be out-waited by standing still.
      if (hunted.has(c.id)) continue;
      c.heat = Math.max(0, c.heat - RULES.heat.decayPerSecond * dt);
      const correlationId = this.storyOf.get(c.id);
      if (c.status === "WANTED" && c.heat < RULES.heat.wantedThreshold) {
        c.status = "ACTIVE"; c.successfulEscapes += 1;
        const esc = this.emit("ESCAPE_SUCCEEDED", c.id, { characterId: c.id, ticker: c.ticker, successfulEscapes: c.successfulEscapes }, { correlationId });
        const o = { causationId: esc.eventId, correlationId: esc.correlationId };
        this.emit("WANTED_STATUS_CHANGED", c.id, { characterId: c.id, ticker: c.ticker, wanted: false, heat: Math.round(c.heat), wantedLevel: wantedLevel(c.heat), reason: "heat decayed: escaped" }, o);
        for (const n of Object.values(this.state.npcs)) if (n.targetCharacterId === c.id) this.setNpcActivity(n.id, n.role === "POLICE" ? "PATROL" : "IDLE", {});
        this.reprice(c, "ESCAPE", o);
      }
      const bucket = Math.floor(c.heat / 5);
      if (bucket !== this.heatBucket.get(c.id)) { this.heatBucket.set(c.id, bucket); this.reprice(c, "HEAT", { correlationId }); this.dirty.characters.add(c.id); }
    }
  }

  // ---------- traces / dirty tracking ----------
  /**
   * A background comparison call (never applied to the world) finished after its decision was already delivered.
   * Attach it to the stored trace, persist it, feed metrics and refresh the NPC's inspector view.
   */
  applyShadow(update: DecisionTrace): void {
    if (!update.shadow || update.runId !== this.state.runId) return;
    const t = this.brains.traces().find((x) => x.id === update.id && x.agentId === update.agentId);
    if (!t) return;
    t.shadow = update.shadow;
    this.onTrace(t);
  }
  /** Decisions made by agents that are not NPCs (the fugitive) are persisted and counted like any other. */
  recordAgentTrace(t: DecisionTrace): void { this.onTrace(t); }
  private onTrace(t: DecisionTrace): void {
    try { this.deps.persistence.saveDecision(t); } catch (err) { console.error("[persistence] saveDecision failed", (err as Error).message); }
    try { this.deps.metrics.onDecision(t); } catch (err) { console.error("[metrics] onDecision failed", (err as Error).message); }
    this.dirty.agents.add(t.agentId);
  }
  private markDirty(e: GameEvent): void {
    const p = e.payload as Record<string, unknown>; const s = this.state; const d = this.dirty;
    for (const k of ["npcId", "agentId", "senderId", "recipientId", "issuedBy", "captorId", "ownerId"]) { const v = p[k]; if (typeof v === "string" && s.npcs[v]) d.agents.add(v); }
    if (s.npcs[e.actorId]) d.agents.add(e.actorId);
    if (Array.isArray(p.recipientIds)) for (const v of p.recipientIds) if (typeof v === "string" && s.npcs[v]) d.agents.add(v);
    for (const k of ["characterId", "previousCharacterId", "targetCharacterId"]) { const v = p[k]; if (typeof v === "string") d.characters.add(v); }
    if (typeof p.ticker === "string") d.listings.add(p.ticker);
    for (const k of ["walletId", "captorWalletId"]) { const v = p[k]; if (typeof v === "string") d.wallets.add(v); }
    if (typeof p.missionId === "string") d.missions.add(p.missionId);
    if (e.type === "TRADE_EXECUTED" || e.type === "LISTING_OPENED") for (const n of Object.values(s.npcs)) if (n.walletId) d.agents.add(n.id);
  }
  drainDirty(): { agents: AgentView[]; listings: ListingView[]; characters: CharacterView[]; missions: MissionView[]; wallets: WalletView[]; snapshot: boolean } {
    const d = this.dirty; const s = this.state;
    if (d.listings.size) for (const w of Object.values(s.wallets)) if (w.ownerKind === "HUMAN") d.wallets.add(w.id);
    const out = {
      agents: [...d.agents].filter((id) => s.npcs[id]).map((id) => this.agentView(id)),
      listings: [...d.listings].map((t) => this.deps.economy.listingView(s, t, this.now())).filter((x): x is ListingView => !!x),
      characters: [...d.characters].filter((id) => s.characters[id]).map((id) => this.characterView(s.characters[id]!)),
      missions: [...d.missions].filter((id) => s.missions[id]).map((id) => this.missionView(id)),
      wallets: [...d.wallets].map((id) => this.deps.economy.walletView(s, id)).filter((x): x is WalletView => !!x),
      snapshot: d.snapshot,
    };
    d.agents.clear(); d.listings.clear(); d.characters.clear(); d.missions.clear(); d.wallets.clear(); d.snapshot = false;
    return out;
  }

  // ---------- views ----------
  characterView(c: CharacterState): CharacterView {
    const crew = this.crews.get(this.crewOf.get(c.playerId) ?? "");
    return { id: c.id, agent: this.state.players[c.playerId]?.isBot ? { kind: "FUGITIVE" } : undefined, crewId: crew?.id ?? null, crewName: crew?.name ?? null, stunnedUntilMs: this.stunnedUntil.get(c.id) ?? null, ammo: this.ammo.get(c.id) ?? RULES.weapon.ammo, weaponReadyAtMs: this.weaponReadyAt.get(c.id) ?? 0, playerId: c.playerId, nickname: c.nickname, role: c.role, ticker: c.ticker, status: c.status, x: c.x, z: c.z, heat: Math.round(c.heat), wantedLevel: wantedLevel(c.heat), wanted: c.status === "WANTED", notorietyPoints: c.notorietyPoints, completedMissions: c.completedMissions, successfulEscapes: c.successfulEscapes, spawnTimeMs: c.spawnTimeMs, captureTimeMs: c.captureTimeMs, capturedBy: c.capturedBy };
  }
  missionView(id: string): MissionView { const m = this.state.missions[id]!; return { id: m.id, x: m.x, z: m.z, label: m.label, status: m.status, completedByCharacterId: m.completedByCharacterId }; }
  agentView(id: string): AgentView {
    const n = this.state.npcs[id]!; const w = n.walletId ? this.state.wallets[n.walletId] : undefined;
    const holdings: Record<string, number> = {};
    if (w) for (const [t, h] of Object.entries(w.holdings)) if (h.shares > 0) holdings[t] = h.shares;
    return { id: n.id, name: n.name, role: n.role, goal: n.goal, persona: n.persona, x: n.x, z: n.z, activity: n.activity, targetCharacterId: n.targetCharacterId, walletCredits: w ? w.balanceCredits : null, holdings, contacts: n.contacts, ...this.brains.view(id) };
  }
  playerView(p: PlayerState): PlayerView { return { id: p.id, nickname: p.nickname, role: p.role, connected: p.connected, characterId: p.characterId, isBot: p.isBot }; }
  private earned = new Map<string, { amount: number; reason: string }>();
  /** Credits an NPC agent's own wallet and records it as an event (replayable). */
  creditNpc(npcId: string, amount: number, reason: string, opts: EmitOpts): number | null {
    const n = this.state.npcs[npcId];
    const credit = Math.round(amount);
    if (!n?.walletId || !this.state.wallets[n.walletId] || !(credit > 0)) return null;
    const balanceAfter = this.deps.economy.credit(this.state, n.walletId, credit);
    this.earned.set(npcId, { amount: credit, reason });
    this.emit("WALLET_CREDITED", npcId, { walletId: n.walletId, ownerId: npcId, ownerName: n.name, amount: credit, reason, balanceAfter }, opts);
    return balanceAfter;
  }
  lastEarning(npcId: string): { amount: number; reason: string } | null { return this.earned.get(npcId) ?? null; }
  walletView(walletId: string | null): WalletView | null { return walletId ? this.deps.economy.walletView(this.state, walletId) : null; }
  worldTick(): WorldTick {
    const s = this.state;
    return {
      tick: s.tick, serverTimeMs: this.now(),
      characters: Object.values(s.characters).filter((c) => c.status !== "ARCHIVED").map((c) => ({ id: c.id, x: Math.round(c.x * 100) / 100, z: Math.round(c.z * 100) / 100, heat: Math.round(c.heat), status: c.status })),
      npcs: Object.values(s.npcs).map((n) => ({ id: n.id, x: Math.round(n.x * 100) / 100, z: Math.round(n.z * 100) / 100, activity: n.activity, pending: this.brains.view(n.id).pendingDecisionId !== null, ...(this.isStunned(n.id, this.now()) ? { stunned: true } : {}) })),
    };
  }
  snapshot(): RoomSnapshot {
    const s = this.state; const now = this.now();
    return {
      roomId: s.id, roomCode: s.code, runId: s.runId, seed: s.seed, ruleVersion: s.ruleVersion, tick: s.tick, serverTimeMs: now, lastEventSequence: s.lastEventSequence,
      mode: s.mode, providerStatus: this.deps.scheduler.status(), map: CITY_MAP,
      players: Object.values(s.players).map((p) => this.playerView(p)),
      characters: Object.values(s.characters).map((c) => this.characterView(c)),
      npcs: Object.keys(s.npcs).map((id) => this.agentView(id)),
      missions: Object.keys(s.missions).map((id) => this.missionView(id)),
      listings: Object.keys(s.listings).map((t) => this.deps.economy.listingView(s, t, now)).filter((x): x is ListingView => !!x),
      trades: s.trades.slice(-60), recentEvents: this.events.slice(-300), metrics: this.deps.metrics.view(),
      experiments: this.deps.experiments?.() ?? [], policy: this.deps.policy?.view() ?? null,
      crews: [...this.crews.values()].map((k) => ({ ...k, memberPlayerIds: [...k.memberPlayerIds] })),
    };
  }
}

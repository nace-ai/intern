// Server-internal state and the PORTS every module is built against.
// OWNER: main. Do not edit without messaging main; propose changes on the board.
import type {
  ActorKind, AgentView, CharacterRole, CharacterStatus, Citation, DecisionMode, DecisionTrace, EntityId,
  EventPayloads, EventType, ExperimentResult, FactType, GameEvent, HumanRole, ListingStatus, ListingView,
  MemoryEntry, MessageKind, MetricsView, NpcActivity, NpcRole, PolicyKnowledgeView, PricePoint, PriceReason,
  ProviderName, ProviderStatus, TradeRejectReason, TradeSide, TradeView, Vec2, WalletView,
} from "@vice/contracts";

export interface Clock { now(): number }
export interface Rng { next(): number; int(minIncl: number, maxIncl: number): number; pick<T>(items: readonly T[]): T }

// ---------- state ----------
export interface PlayerState {
  id: EntityId; nickname: string; role: HumanRole; sessionToken: string; socketId: string | null;
  connected: boolean; isDirector: boolean; isBot: boolean; characterId: EntityId | null; walletId: string | null;
  moveDir: Vec2; joinedAtMs: number; disconnectedAtMs: number | null;
}
export interface CharacterState {
  id: EntityId; playerId: EntityId; nickname: string; role: CharacterRole; ticker: string | null;
  status: CharacterStatus; x: number; z: number;
  notorietyPoints: number; completedMissions: number; heat: number; successfulEscapes: number;
  spawnTimeMs: number; captureTimeMs: number | null; capturedBy: EntityId | null;
  lastHeatGainAtMs: number; lastPoliceSightingAtMs: number;
}
export interface NpcState {
  id: EntityId; name: string; role: NpcRole; persona: string; goal: string;
  x: number; z: number; home: Vec2; patrol: Vec2[]; patrolIndex: number;
  activity: NpcActivity; moveTarget: Vec2 | null; targetCharacterId: EntityId | null;
  walletId: string | null; contacts: EntityId[]; cooldownUntilMs: number; lastCaptureAttemptAtMs: number;
}
export interface ListingState {
  ticker: string; characterId: EntityId; nickname: string; status: ListingStatus;
  referenceValue: number; spotPrice: number; openedAtMs: number; haltedAtMs: number | null;
  finalSpotBeforeHalt: number | null; history: PricePoint[];
  /** Signed units inside the rolling sentiment window (+buy, -sell). */
  demand: Array<{ atMs: number; units: number; walletId?: string }>;
  volume: number;
}
export interface WalletState {
  id: string; ownerId: EntityId; ownerKind: ActorKind; ownerName: string; balanceCredits: number;
  holdings: Record<string, { shares: number; costBasis: number }>;
  lastOrderAtMs: Record<string, number>;
}
export interface MissionState { id: string; spotIndex: number; x: number; z: number; label: string; status: "AVAILABLE" | "COMPLETED"; completedByCharacterId: EntityId | null; completedAtMs: number | null }
export interface MessageRecord {
  id: string; senderId: EntityId; recipientId: EntityId; kind: MessageKind;
  sourceMemoryId: string | null; sourceEventId: string; subjectCharacterId: EntityId | null;
  summary: string; payload: Record<string, unknown>; hop: number;
  sentAtMs: number; deliverAtMs: number; deliveredAtMs: number | null; sentEventId: string; correlationId: string;
}
export interface RoomState {
  id: string; code: string; runId: string; seed: number; tick: number; ruleVersion: string; mode: DecisionMode;
  createdAtMs: number; runStartedAtMs: number; lastEventSequence: number;
  players: Record<string, PlayerState>; characters: Record<string, CharacterState>; npcs: Record<string, NpcState>;
  listings: Record<string, ListingState>; wallets: Record<string, WalletState>; missions: Record<string, MissionState>;
  messages: MessageRecord[]; trades: TradeView[];
  /** Deterministic id counters (evt, dec, mem, msg, trd, chr, ...). Reset on demo.reset. */
  counters: Record<string, number>;
}

// ---------- ECONOMY port (apps/server/src/economy/**). Pure: mutates the passed state, performs no I/O, emits no events. ----------
export interface Quote { referenceValue: number; spotPrice: number; bid: number; ask: number; survivalFactor: number; sentimentFactor: number; netBuyUnits: number; survivalSeconds: number }
export interface PriceChange { ticker: string; referenceValue: number; spotPrice: number; previousSpotPrice: number; reason: PriceReason; heat: number; netBuyUnits: number }
export interface OrderRequest { walletId: string; actorId: EntityId; actorName: string; actorKind: ActorKind; ticker: string; side: TradeSide; qty: number; commandId: string; tradeId: string }
export type OrderResult =
  | { ok: true; trade: TradeView; walletId: string; balanceAfter: number; sharesAfter: number; priceChange: PriceChange | null }
  | { ok: false; reason: TradeRejectReason; message: string };
export interface EconomyPort {
  openWallet(state: RoomState, w: { id: string; ownerId: EntityId; ownerKind: ActorKind; ownerName: string; balance: number }): WalletState;
  /** Creates the listing for an OUTLAW character at its opening quote. */
  openListing(state: RoomState, character: CharacterState, nowMs: number): ListingState;
  quote(state: RoomState, ticker: string, nowMs: number): Quote | null;
  /** Recompute reference+spot; push a history point and return the change only if spot or reference moved. Halted listings return null. */
  reprice(state: RoomState, ticker: string, nowMs: number, reason: PriceReason): PriceChange | null;
  /** All-or-nothing. Validates ticker, halt, qty, cooldown, position cap, funds/shares; then moves credits and shares together, records demand, reprices. Duplicate commandIds are handled by the engine, not here. */
  executeOrder(state: RoomState, order: OrderRequest, nowMs: number): OrderResult;
  /** One-way ACTIVE -> HALTED_CAPTURED, spot = 0 exactly. Returns null if already halted/unknown. */
  haltListing(state: RoomState, ticker: string, nowMs: number): { finalSpotBeforeHalt: number; referenceAtHalt: number } | null;
  /** max(min, round(reference * fraction)) for the character's listing right now. */
  bountyFor(state: RoomState, characterId: EntityId, nowMs: number): number;
  credit(state: RoomState, walletId: string, amount: number): number;
  walletView(state: RoomState, walletId: string): WalletView | null;
  listingView(state: RoomState, ticker: string, nowMs: number): ListingView | null;
}

// ---------- MEMORY port (apps/server/src/agents/MemoryStore.ts) ----------
export interface MemoryPort {
  /** Adds a private memory for one NPC (bounded; evicts oldest). The store assigns `id` via nextId(). */
  remember(entry: Omit<MemoryEntry, "id">, id: string): MemoryEntry;
  /** Non-expired entries for ONE npc, newest first. Never returns another NPC's memory. */
  recall(agentId: EntityId, nowMs: number): MemoryEntry[];
  get(memoryId: string): MemoryEntry | undefined;
  all(): MemoryEntry[];
  clear(): void;
}

// ---------- DECISION ports (apps/server/src/decisions/**) ----------
export type ProviderErrorKind = "auth" | "credit" | "invalid_request" | "rate_limit" | "overloaded" | "timeout" | "network" | "server" | "invalid_response" | "aborted" | "unconfigured";
export class ProviderError extends Error {
  constructor(public kind: ProviderErrorKind, message: string, public opts: { status?: number; retryable?: boolean; retryAfterMs?: number; requestId?: string | null } = {}) { super(message); }
}
export interface ProviderCall { question: string; options: Record<string, string>; state: Record<string, unknown> }
export interface ProviderResult {
  provider: ProviderName; model: string | null; choice: string;
  confidence: number | null; probabilities: Record<string, number> | null;
  requestId: string | null; latencyMs: number; providerEvalMs: number | null;
  usage: { inputTokens: number | null; outputTokens: number | null } | null; attempts: number;
}
export interface DecisionProvider {
  readonly name: ProviderName; readonly model: string;
  isConfigured(): boolean;
  /** Resolves with a label that is one of the offered options, or throws ProviderError. Must honour signal and timeoutMs. */
  decide(call: ProviderCall, opts: { signal?: AbortSignal; timeoutMs: number }): Promise<ProviderResult>;
}
export interface DecisionRequest {
  decisionId: string; roomId: string; runId: string; agentId: EntityId; agentName: string; role: NpcRole;
  question: string; options: Record<string, string>;
  /** Built ONLY from this NPC's own memory, wallet and public information. */
  state: Record<string, unknown>;
  /** Deterministic baseline choice (used in scripted mode). */
  scriptedAction: string;
  /** Safe choice when the provider fails or is unsure (ignore / withhold / hold / patrol). */
  safeAction: string;
  triggerEventId: string; correlationId: string; requestedAtMs: number; requestedAtSequence: number; citations: Citation[];
  /** De-duplication key, normally `${agentId}:${triggerEventId}`. */
  dedupeKey: string;
}
export interface SchedulerPort {
  /**
   * Starts a decision. Returns the initial trace (status REQUESTED), or null when dropped (duplicate key or queue full).
   * onDone is called exactly once, asynchronously, with the final trace: decisionSource, chosenAction (always one of
   * request.options), provider metadata or errorType. The scheduler never applies actions and never touches room state.
   */
  request(req: DecisionRequest, onDone: (trace: DecisionTrace) => void): DecisionTrace | null;
  setMode(mode: DecisionMode): void;
  getMode(): DecisionMode;
  status(): ProviderStatus;
  pending(): number;
  /** Drop queued work and ignore in-flight results (room reset). */
  cancelAll(): void;
}

// ---------- WORLD port: implemented by RoomEngine, used by the NPC brains (apps/server/src/agents/**) ----------
export interface EmitOpts { causationId?: string | null; correlationId?: string }
export interface WorldPort {
  readonly state: RoomState; // read-only for brains
  now(): number;
  nextId(prefix: string): string;
  emit<T extends EventType>(type: T, actorId: string, payload: EventPayloads[T], opts?: EmitOpts): GameEvent<T>;
  /** Queues a message; the engine delivers it after RULES.messages.deliveryDelayMs: writes the recipient's memory (sourceKind MESSAGE) and emits AGENT_MESSAGE_DELIVERED. */
  sendMessage(m: { senderId: EntityId; recipientId: EntityId; kind: MessageKind; sourceMemoryId: string | null; sourceEventId: string; subjectCharacterId: EntityId | null; summary: string; payload: Record<string, unknown> }, opts: EmitOpts): MessageRecord;
  /** Same validation and settlement path as a human order. Emits TRADE_EXECUTED / TRADE_REJECTED and price events. */
  placeNpcOrder(npcId: EntityId, ticker: string, side: TradeSide, opts: EmitOpts): OrderResult;
  /** Credits an NPC's own wallet and emits WALLET_CREDITED (police bounty, informant fee, witness tip). */
  creditNpc?(npcId: EntityId, amount: number, reason: string, opts: EmitOpts): number | null;
  /** What this NPC last earned, if anything (its own knowledge). */
  lastEarning?(npcId: EntityId): { amount: number; reason: string } | null;
  setNpcActivity(npcId: EntityId, activity: NpcActivity, target: { moveTarget?: Vec2 | null; targetCharacterId?: EntityId | null }): void;
  /** Police make a character wanted: raises heat, emits WANTED_STATUS_CHANGED + BULLETIN_PUBLISHED, writes WANTED_BULLETIN memories (sourceKind BULLETIN) to every NPC. No-op if a bulletin for that character is still fresh. */
  publishBulletin(policeNpcId: EntityId, characterId: EntityId, lastKnown: Vec2, opts: EmitOpts): void;
  /** Hunter crews are public knowledge (joining is announced). Optional so test doubles need not implement it. */
  hunterCrews?(): Array<{ id: string; name: string; memberPlayerIds: EntityId[] }>;
  /** Public market info any trader may see (never private memories). */
  marketSummary(ticker: string): { ticker: string; status: ListingStatus; spotPrice: number; referenceValue: number; changePct60s: number; netBuyUnits: number } | null;
}
export interface BrainsPort {
  /** Called synchronously for every event after it is appended. Decide here whether an NPC should think. */
  onEvent(event: GameEvent): void;
  onTick(nowMs: number): void;
  /** Inspector data for one NPC. */
  view(npcId: EntityId): Pick<AgentView, "memories" | "pendingDecisionId" | "lastDecision" | "recentDecisions">;
  traces(): DecisionTrace[];
  reset(): void;
}

// ---------- PERSISTENCE / METRICS ports (apps/server/src/persistence/**, metrics/**) ----------
export interface PersistencePort {
  /** Called in order for every event, before it is broadcast. Must not throw into the engine (log and continue). */
  appendEvent(event: GameEvent): void;
  saveDecision(trace: DecisionTrace): void;
  saveExperiment(result: ExperimentResult): void;
  loadEvents(roomId: string, runId: string, afterSequence?: number): GameEvent[];
  loadDecisions(roomId: string, runId: string): DecisionTrace[];
  loadExperiments(): ExperimentResult[];
  close(): void;
}
export interface MetricsPort {
  onEvent(event: GameEvent): void;
  onDecision(trace: DecisionTrace): void;
  view(): MetricsView;
  reset(runId: string, mode: DecisionMode, seed: number, startedAtMs: number): void;
}
export interface PolicyPort { view(): PolicyKnowledgeView | null; factsFor(role: NpcRole): Citation[] }

export type { FactType };

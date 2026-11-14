// Shared public types. The server is authoritative; clients only render these.

export type EntityId = string;
export interface Vec2 { x: number; z: number }
export interface Rect { x0: number; z0: number; x1: number; z1: number }

export type HumanRole = "OUTLAW" | "HUNTER" | "SPECTATOR";
export type CharacterRole = "OUTLAW" | "HUNTER";
export type NpcRole = "WITNESS" | "INFORMANT" | "TRADER" | "POLICE";
export type CharacterStatus = "ACTIVE" | "WANTED" | "CAPTURED" | "ARCHIVED";
export type ListingStatus = "ACTIVE" | "HALTED_CAPTURED";
export type NpcActivity = "IDLE" | "PATROL" | "INVESTIGATE" | "PURSUE";
export type DecisionMode = "drex" | "meta" | "scripted";
export type ProviderName = "drex" | "meta";
/** Who actually decided. Only "drex" may ever be shown as a live Drex result. */
export type DecisionSource =
  | "drex" | "meta" | "scripted" | "fallback" | "drex_low_confidence_guardrail" | "replay";
export type DecisionStatus =
  | "REQUESTED" | "RESOLVED" | "VALIDATED" | "APPLIED" | "REJECTED" | "TIMEOUT" | "ERROR" | "FALLBACK_APPLIED";
export type FactType = "SIGHTING" | "INCIDENT" | "RUMOR" | "WANTED_BULLETIN" | "LISTING" | "POLICY";
export type SourceKind = "DIRECT" | "MESSAGE" | "BULLETIN" | "DOCUMENT";
export type MessageKind = "REPORT" | "GOSSIP" | "RELAY" | "BULLETIN";
export type TradeSide = "BUY" | "SELL";
export type ActorKind = "HUMAN" | "NPC";
export type TradeRejectReason =
  | "INSUFFICIENT_FUNDS" | "INSUFFICIENT_SHARES" | "LISTING_HALTED" | "UNKNOWN_TICKER"
  | "POSITION_LIMIT" | "COOLDOWN" | "DUPLICATE_COMMAND" | "ACTOR_NOT_ALLOWED" | "INVALID_QTY";
export type CaptureRejectReason =
  | "NOT_ALLOWED_FOR_ROLE" | "TARGET_NOT_FOUND" | "TARGET_NOT_WANTED" | "TARGET_ALREADY_CAPTURED"
  | "OUT_OF_RANGE" | "LINE_OF_SIGHT_BLOCKED" | "COOLDOWN" | "CAPTOR_INACTIVE" | "DUPLICATE_COMMAND" | "SELF_CAPTURE";
export type PriceReason =
  | "LISTING_OPENED" | "MISSION" | "HEAT" | "ESCAPE" | "TRADE" | "SURVIVAL_TICK" | "SENTIMENT_DECAY" | "CAPTURE";

export interface Building extends Rect { id: string; label: string; height: number; tone: "pink" | "aqua" | "purple" | "sand" | "teal" }
/** A parked car. Solid: the server stops players and NPCs at its footprint (it does not block line of sight). */
export interface ParkedCar { id: string; x: number; z: number; length: number; width: number; /** 0 = length along x, 90 = length along z. */ heading: 0 | 90; /** Paint colour (hex). */ tone: string }
export interface MapView {
  name: string; width: number; height: number;
  walkable: Rect; water: Rect[]; buildings: Building[];
  /** Solid parked cars. Optional so older snapshots still type-check. */
  cars?: ParkedCar[];
  districts: Array<{ id: string; label: string; x: number; z: number }>;
}

/** NDI-extracted fact reference shown in the inspector. */
export interface Citation {
  documentId: string; documentTitle: string; sourceUrl: string | null;
  page: number | null; quote: string; extractedField: string; ndiJobId: string | null;
}

export interface MemoryEntry {
  id: string; agentId: EntityId; factType: FactType;
  subjectCharacterId: EntityId | null; subjectTicker: string | null;
  /** Authoritative event that created this memory. */
  eventId: string;
  sourceActorId: EntityId; sourceKind: SourceKind;
  /** Sender's memory this was derived from (messages only). */
  sourceMemoryId: string | null;
  /** 0 = direct observation, +1 per message hop. */
  hop: number;
  observedAtMs: number; expiresAtMs: number;
  /** Provenance belief in [0,1]. NOT a provider decision confidence. */
  confidence: number;
  summary: string;
  payload: Record<string, unknown>;
  citation: Citation | null;
}

export interface ShadowResult {
  provider: ProviderName; model: string | null; chosenAction: string | null;
  latencyMs: number | null; providerRequestId: string | null; errorType: string | null; agrees: boolean | null;
}

export interface DecisionTrace {
  id: string; roomId: string; runId: string;
  agentId: EntityId; agentName: string; role: NpcRole;
  status: DecisionStatus;
  /** Room experiment mode when the decision was requested. */
  mode: DecisionMode;
  /** null while pending. */
  decisionSource: DecisionSource | null;
  /** External provider actually called (null = none was called). */
  provider: ProviderName | null;
  model: string | null;
  question: string;
  /** label -> description, exactly as offered. */
  offeredActions: Record<string, string>;
  /** Action that was applied (after guardrails/fallback). */
  chosenAction: string | null;
  /** Raw label the provider returned, if any. */
  providerChoice: string | null;
  /** Provider values only; null when the provider did not supply them. Never invented. */
  confidence: number | null;
  probabilities: Record<string, number> | null;
  providerRequestId: string | null;
  /** Measured wall-clock round trip. */
  latencyMs: number | null;
  /** Provider-reported evaluation time. */
  providerEvalMs: number | null;
  usage: { inputTokens: number | null; outputTokens: number | null } | null;
  attempts: number;
  errorType: string | null; errorMessage: string | null; rejectReason: string | null;
  /** Exactly what was sent as `state`: the agent's own knowledge only. */
  stateSnapshot: Record<string, unknown>;
  stateHash: string;
  triggerEventId: string; correlationId: string;
  effectEventIds: string[];
  requestedAtMs: number; resolvedAtMs: number | null; requestedAtSequence: number;
  citations: Citation[];
  /** Set when the decider is not one of the four NPC roles (the autonomous hunted outlaw). */
  agentKind?: "FUGITIVE";
  shadow: ShadowResult | null;
}

export interface AgentView {
  id: EntityId; name: string; role: NpcRole; goal: string; persona: string;
  x: number; z: number; activity: NpcActivity; targetCharacterId: EntityId | null;
  walletCredits: number | null; holdings: Record<string, number>;
  contacts: EntityId[];
  memories: MemoryEntry[];
  pendingDecisionId: string | null;
  lastDecision: DecisionTrace | null;
  recentDecisions: DecisionTrace[];
}

export interface PlayerView { id: EntityId; nickname: string; role: HumanRole; connected: boolean; characterId: EntityId | null; isBot: boolean }

export interface CharacterView {
  id: EntityId; playerId: EntityId; nickname: string; role: CharacterRole;
  /** Present when this character is driven by an autonomous agent, not a human. */
  agent?: { kind: "FUGITIVE" };
  /** Only outlaws are listed; hunters have no ticker. */
  ticker: string | null;
  status: CharacterStatus; x: number; z: number;
  heat: number; wantedLevel: number; wanted: boolean;
  notorietyPoints: number; completedMissions: number; successfulEscapes: number;
  spawnTimeMs: number; captureTimeMs: number | null; capturedBy: EntityId | null;
  /** Stun pulse state (optional so older clients ignore it). */
  stunnedUntilMs?: number | null; ammo?: number; weaponReadyAtMs?: number;
  /** Hunter crew, if any. */
  crewId?: string | null; crewName?: string | null;
}

export interface PricePoint { t: number; spot: number; reference: number }
export interface ListingView {
  ticker: string; characterId: EntityId; nickname: string; status: ListingStatus;
  referenceValue: number; spotPrice: number; bid: number; ask: number;
  history: PricePoint[]; volume: number; netBuyUnits: number;
  openedAtMs: number; haltedAtMs: number | null; finalSpotBeforeHalt: number | null;
  /** What a captor would earn right now (grows with survival and notoriety). */
  currentBounty: number;
}
export interface HoldingView { ticker: string; shares: number; costBasis: number; marketValue: number }
export interface WalletView { walletId: string; ownerId: EntityId; balanceCredits: number; holdings: HoldingView[]; netWorth: number }
export interface TradeView {
  tradeId: string; atMs: number; actorId: EntityId; actorName: string; actorKind: ActorKind;
  ticker: string; side: TradeSide; qty: number; executionPrice: number;
}
export interface MissionView { id: string; x: number; z: number; label: string; status: "AVAILABLE" | "COMPLETED"; completedByCharacterId: EntityId | null }

export interface ProviderHealth {
  configured: boolean; state: "LIVE" | "DEGRADED" | "DOWN" | "UNCONFIGURED";
  model: string | null; lastOkAtMs: number | null; lastErrorType: string | null;
  callsLastMinute: number; maxCallsPerMinute: number; inFlight: number; circuitOpenUntilMs: number | null;
}
export interface ProviderStatus {
  mode: DecisionMode; drex: ProviderHealth; meta: ProviderHealth; pending: number;
  /** Header badge text, e.g. "DREX: LIVE", "DREX: FALLBACK", "SCRIPTED", "META: LIVE". */
  badge: string;
}

export interface LatencyStats { count: number; meanMs: number; p50Ms: number; p95Ms: number; maxMs: number; evalP50Ms: number | null }
export interface MetricsView {
  runId: string; mode: DecisionMode; seed: number; startedAtMs: number;
  decisions: { total: number; bySource: Record<string, number>; byRole: Record<string, number>; byAction: Record<string, number>; rejected: number; stale: number };
  latency: { drex: LatencyStats | null; meta: LatencyStats | null };
  messages: { sent: number; delivered: number; maxHops: number };
  trades: { executed: number; rejected: number; byNpc: number; byHuman: number };
  priceUpdates: number; captures: number;
  /** sender -> recipient communication edges (collaboration / competition graph). */
  edges: Array<{ from: EntityId; to: EntityId; kind: MessageKind; count: number }>;
  /**
   * Live side-by-side: for Drex decisions that were also sent to the Meta LLM in the background (never applied).
   * Measured values only; null when no shadow comparison has completed.
   */
  shadow?: { compared: number; agreed: number; drexP50Ms: number | null; metaP50Ms: number | null } | null;
}

export interface ExperimentRow {
  mode: DecisionMode; decisions: number; realProviderCalls: number; fallbacks: number;
  latencyP50Ms: number | null; latencyP95Ms: number | null;
  messages: number; rumorHops: number; npcTrades: number;
  reportedToPolice: boolean; captured: boolean; timeToCaptureSec: number | null;
  peakPrice: number; finalPrice: number; actions: Record<string, number>;
}
export interface ExperimentResult {
  id: string; seed: number; durationSec: number; startedAtMs: number; finishedAtMs: number | null;
  status: "RUNNING" | "DONE" | "FAILED"; rows: ExperimentRow[];
  /** Always present: one run per mode is a demo, not statistical evidence. */
  caveat: string;
}

export interface PolicyKnowledgeView { documentTitle: string; sourceUrl: string | null; ingestedVia: "ndi" | "none"; ndiJobId: string | null; facts: Citation[] }

export interface WorldTick {
  tick: number; serverTimeMs: number;
  characters: Array<{ id: EntityId; x: number; z: number; heat: number; status: CharacterStatus }>;
  npcs: Array<{ id: EntityId; x: number; z: number; activity: NpcActivity; pending: boolean; stunned?: boolean }>;
}

export interface CrewView { id: string; name: string; memberPlayerIds: EntityId[]; formedAtMs: number }

export interface RoomSnapshot {
  roomId: string; roomCode: string; runId: string; seed: number; ruleVersion: string;
  tick: number; serverTimeMs: number; lastEventSequence: number;
  mode: DecisionMode; providerStatus: ProviderStatus; map: MapView;
  players: PlayerView[]; characters: CharacterView[]; npcs: AgentView[]; missions: MissionView[];
  listings: ListingView[]; trades: TradeView[]; recentEvents: import("./events").GameEvent[];
  metrics: MetricsView; experiments: ExperimentResult[]; policy: PolicyKnowledgeView | null;
  crews?: CrewView[];
}

export interface JoinResult {
  roomCode: string; playerId: EntityId; sessionToken: string; role: HumanRole;
  characterId: EntityId | null; walletId: string | null; isDirector: boolean;
  snapshot: RoomSnapshot; wallet: WalletView | null;
}

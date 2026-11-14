import type {
  ActorKind, CaptureRejectReason, CharacterRole, DecisionMode, DecisionSource, DecisionStatus, EntityId,
  FactType, HumanRole, MessageKind, NpcRole, PriceReason, ProviderName, TradeRejectReason, TradeSide, Vec2,
} from "./types";

export interface EventPayloads {
  ROOM_CREATED: { roomCode: string; seed: number; ruleVersion: string; mode: DecisionMode; npcCount: number };
  ROOM_RESET: { seed: number; previousRunId: string; newRunId: string; mode: DecisionMode };
  MODE_CHANGED: { mode: DecisionMode; previousMode: DecisionMode };
  PLAYER_JOINED: { playerId: EntityId; nickname: string; role: HumanRole; reconnect: boolean };
  PLAYER_LEFT: { playerId: EntityId; nickname: string };
  WALLET_OPENED: { walletId: string; ownerId: EntityId; ownerKind: ActorKind; balance: number };
  /** An agent earned fictional credits (police bounty, informant fee, witness tip). */
  WALLET_CREDITED: { walletId: string; ownerId: EntityId; ownerName: string; amount: number; reason: string; balanceAfter: number };
  CHARACTER_SPAWNED: { characterId: EntityId; playerId: EntityId; nickname: string; role: CharacterRole; ticker: string | null; x: number; z: number; walletId: string };
  LISTING_OPENED: { ticker: string; characterId: EntityId; nickname: string; referenceValue: number; spotPrice: number };
  MISSION_SPAWNED: { missionId: string; x: number; z: number; label: string };
  MISSION_COMPLETED: { missionId: string; characterId: EntityId; ticker: string | null; x: number; z: number; notorietyGained: number; heatGained: number; notorietyPoints: number; completedMissions: number; heat: number };
  INCIDENT_OCCURRED: { incidentId: string; kind: "MISSION" | "SIGHTING"; characterId: EntityId; ticker: string | null; x: number; z: number; description: string; observerIds: EntityId[] };
  NPC_OBSERVED: { npcId: EntityId; memoryId: string; factType: FactType; subjectCharacterId: EntityId | null; distance: number; incidentId: string | null; summary: string };
  AGENT_MESSAGE_SENT: { messageId: string; senderId: EntityId; recipientId: EntityId; kind: MessageKind; sourceMemoryId: string | null; sourceEventId: string; subjectCharacterId: EntityId | null; summary: string; hop: number };
  AGENT_MESSAGE_DELIVERED: { messageId: string; senderId: EntityId; recipientId: EntityId; kind: MessageKind; memoryId: string; hop: number };
  BULLETIN_PUBLISHED: { bulletinId: string; characterId: EntityId; ticker: string | null; lastKnown: Vec2; issuedBy: EntityId; recipientIds: EntityId[] };
  DECISION_REQUESTED: { decisionId: string; agentId: EntityId; role: NpcRole; mode: DecisionMode; offeredActions: string[]; triggerEventId: string; stateHash: string };
  DECISION_RESOLVED: {
    decisionId: string; agentId: EntityId; role: NpcRole; status: DecisionStatus;
    decisionSource: DecisionSource; provider: ProviderName | null; model: string | null;
    chosenAction: string | null; providerChoice: string | null; offeredActions: string[];
    confidence: number | null; probabilities: Record<string, number> | null;
    providerRequestId: string | null; latencyMs: number | null; providerEvalMs: number | null;
    errorType: string | null; rejectReason: string | null;
  };
  AGENT_ACTION_APPLIED: { decisionId: string; agentId: EntityId; role: NpcRole; action: string; decisionSource: DecisionSource; detail: string };
  AGENT_ACTION_REJECTED: { decisionId: string; agentId: EntityId; role: NpcRole; action: string | null; reason: string };
  MARKET_PRICE_UPDATED: { ticker: string; referenceValue: number; spotPrice: number; previousSpotPrice: number; reason: PriceReason; heat: number; netBuyUnits: number };
  TRADE_EXECUTED: { tradeId: string; commandId: string; walletId: string; actorId: EntityId; actorName: string; actorKind: ActorKind; ticker: string; side: TradeSide; qty: number; executionPrice: number; balanceAfter: number; sharesAfter: number };
  TRADE_REJECTED: { commandId: string; walletId: string | null; actorId: EntityId; actorKind: ActorKind; ticker: string; side: TradeSide; qty: number; reason: TradeRejectReason };
  WANTED_STATUS_CHANGED: { characterId: EntityId; ticker: string | null; wanted: boolean; heat: number; wantedLevel: number; reason: string };
  ESCAPE_SUCCEEDED: { characterId: EntityId; ticker: string | null; successfulEscapes: number };
  CAPTURE_SUCCEEDED: {
    captureId: string; commandId: string; characterId: EntityId; ticker: string | null;
    captorId: EntityId; captorName: string; captorKind: ActorKind; captorWalletId: string | null;
    bounty: number; captorBalanceAfter: number | null;
    referenceAtCapture: number; spotBeforeCapture: number; survivalSeconds: number;
    /** Present when the captor is in a crew: every wallet credited by this capture (captor first). Sums to `bounty`. */
    shares?: Array<{ playerId: EntityId; walletId: string; amount: number; balanceAfter: number }>;
    crewId?: string | null; crewName?: string | null; doubleCross?: boolean;
  };
  CREW_JOINED: { crewId: string; crewName: string; playerId: EntityId; nickname: string; created: boolean; memberPlayerIds: EntityId[] };
  CREW_LEFT: { crewId: string; crewName: string; playerId: EntityId; nickname: string; reason: "LEFT" | "SWITCHED" | "EJECTED"; memberPlayerIds: EntityId[] };
  /** An informant sold a wanted outlaw's last known location to a hunter crew (its own decision). */
  CREW_TIP: { crewId: string; crewName: string; memberPlayerIds: EntityId[]; informantId: EntityId; informantName: string; characterId: EntityId; ticker: string | null; x: number; z: number; fee: number; earnings: number };
  /** An informant sold the outlaw a warning about the crew instead. */
  OUTLAW_WARNED: { characterId: EntityId; playerId: EntityId; ticker: string | null; crewNames: string[]; informantId: EntityId; informantName: string; fee: number; earnings: number };
  INFORMANT_SWITCHED_SIDES: { informantId: EntityId; informantName: string; from: "crew" | "outlaw"; to: "crew" | "outlaw"; characterId: EntityId | null };
  CREW_BETRAYED: { crewId: string; crewName: string; betrayerPlayerId: EntityId; betrayerName: string; victimPlayerIds: EntityId[]; victimNames: string[]; bounty: number };
  /** A stun pulse was fired. Nobody is hurt: a hit only stuns. hitId is null on a miss. */
  WEAPON_FIRED: { shooterId: EntityId; shooterName: string; shooterKind: ActorKind; x: number; z: number; toX: number; toZ: number; hitId: EntityId | null; hitKind: "CHARACTER" | "NPC" | null; ammoLeft: number };
  STUNNED: { targetId: EntityId; targetName: string; targetKind: "CHARACTER" | "NPC"; untilMs: number; byId: EntityId };
  CAPTURE_REJECTED: { commandId: string; captorId: EntityId; targetCharacterId: EntityId; reason: CaptureRejectReason };
  LISTING_HALTED: { ticker: string; characterId: EntityId; status: "HALTED_CAPTURED"; finalSpotBeforeHalt: number };
  CHARACTER_ARCHIVED: { characterId: EntityId; ticker: string | null; playerId: EntityId };
  CHARACTER_RESPAWNED: { playerId: EntityId; previousCharacterId: EntityId; characterId: EntityId; ticker: string | null };
  POLICY_INGESTED: { documentTitle: string; sourceUrl: string | null; ndiJobId: string | null; factCount: number };
}

export type EventType = keyof EventPayloads;
export const EVENT_TYPES = [
  "ROOM_CREATED", "ROOM_RESET", "MODE_CHANGED", "PLAYER_JOINED", "PLAYER_LEFT", "WALLET_OPENED", "WALLET_CREDITED", "CHARACTER_SPAWNED",
  "LISTING_OPENED", "MISSION_SPAWNED", "MISSION_COMPLETED", "INCIDENT_OCCURRED", "NPC_OBSERVED",
  "AGENT_MESSAGE_SENT", "AGENT_MESSAGE_DELIVERED", "BULLETIN_PUBLISHED", "DECISION_REQUESTED", "DECISION_RESOLVED",
  "AGENT_ACTION_APPLIED", "AGENT_ACTION_REJECTED", "MARKET_PRICE_UPDATED", "TRADE_EXECUTED", "TRADE_REJECTED",
  "WANTED_STATUS_CHANGED", "ESCAPE_SUCCEEDED", "CAPTURE_SUCCEEDED", "CAPTURE_REJECTED", "WEAPON_FIRED", "STUNNED", "CREW_JOINED", "CREW_LEFT", "CREW_BETRAYED", "CREW_TIP", "OUTLAW_WARNED", "INFORMANT_SWITCHED_SIDES", "LISTING_HALTED",
  "CHARACTER_ARCHIVED", "CHARACTER_RESPAWNED", "POLICY_INGESTED",
] as const satisfies readonly EventType[];

/** Authoritative, append-only, ordered per room by serverSequence. */
export interface GameEvent<T extends EventType = EventType> {
  schemaVersion: 1;
  eventId: string;
  roomId: string;
  runId: string;
  serverSequence: number;
  /** ISO wall-clock time, for audit. */
  time: string;
  /** Rule clock (ms) used for all game logic. */
  simTimeMs: number;
  type: T;
  actorId: string;
  /** Event that directly caused this one. */
  causationId: string | null;
  /** Shared by every event in one causal story (mission -> ... -> capture). */
  correlationId: string;
  payload: EventPayloads[T];
}

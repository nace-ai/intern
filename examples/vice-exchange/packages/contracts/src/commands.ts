import { z } from "zod";

/**
 * Server-issued ids look like chr_0001 or mis_0003: a lowercase prefix, an underscore, then letters or digits.
 * The pattern also keeps inherited object keys such as __proto__, constructor or toString out of every lookup.
 */
const shortId = z.string().regex(/^[a-z]{2,8}_[A-Za-z0-9_-]{1,48}$/);
/** Tickers look like ACE-01: three to five capitals or digits, a dash, a counter. */
export const tickerSchema = z.string().regex(/^[A-Z0-9]{3,5}-[0-9]{2,5}$/);
/** Idempotency key: required on every command that mutates money, capture, missions or life state. */
const commandId = z.string().min(8).max(64);
const requestId = z.string().min(1).max(64);
export const nicknameSchema = z.string().trim().min(1).max(16).regex(/^[A-Za-z0-9 _.-]+$/);
export const roomCodeSchema = z.string().trim().toUpperCase().regex(/^[A-Z0-9-]{3,12}$/);
export const humanRoleSchema = z.enum(["OUTLAW", "HUNTER", "SPECTATOR"]);
export const decisionModeSchema = z.enum(["drex", "meta", "scripted"]);
const dir = z.number().min(-1).max(1);
const secret = z.string().max(128).optional();

export const clientCommandSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("room.create"), nickname: nicknameSchema, role: humanRoleSchema, requestId, seed: z.number().int().min(0).max(2147483647).optional(), directorSecret: secret }),
  z.object({ type: z.literal("room.join"), roomCode: roomCodeSchema, nickname: nicknameSchema, role: humanRoleSchema, requestId, sessionToken: z.string().max(128).optional(), directorSecret: secret }),
  /** Movement intent only. The server integrates position; clients never send coordinates here. */
  z.object({ type: z.literal("player.move"), dirX: dir, dirZ: dir, lastSeenTick: z.number().int().nonnegative().optional(), requestId: requestId.optional() }),
  /** External-world clients (the Godot city) report a pose; the server validates speed and bounds and may reject it. */
  z.object({ type: z.literal("player.pose"), x: z.number().finite(), z: z.number().finite(), requestId: requestId.optional() }),
  z.object({ type: z.literal("mission.interact"), missionId: shortId, commandId }),
  z.object({ type: z.literal("market.buy"), ticker: tickerSchema, qty: z.literal(1), commandId }),
  z.object({ type: z.literal("market.sell"), ticker: tickerSchema, qty: z.literal(1), commandId }),
  z.object({ type: z.literal("capture.attempt"), targetCharacterId: shortId, commandId }),
  /** Stun pulse: aim at a target id, or along a direction (the server picks the nearest legal target in a narrow cone). */
  z.object({ type: z.literal("weapon.fire"), targetId: shortId.optional(), dirX: z.number().min(-1).max(1).optional(), dirZ: z.number().min(-1).max(1).optional(), commandId }),
  /** Hunter crews: joining creates the crew if it does not exist. A crew shares a bounty; a double-cross takes it all. */
  z.object({ type: z.literal("crew.join"), crewName: z.string().trim().regex(/^[A-Za-z0-9]{3,12}$/), commandId }),
  z.object({ type: z.literal("crew.leave"), commandId }),
  z.object({ type: z.literal("character.respawn"), commandId }),
  z.object({ type: z.literal("demo.reset"), commandId, seed: z.number().int().min(0).max(2147483647).optional() }),
  z.object({ type: z.literal("demo.incident"), incidentType: z.literal("SIGHTING"), commandId, targetCharacterId: shortId.optional() }),
  z.object({ type: z.literal("demo.setMode"), mode: decisionModeSchema, commandId }),
  z.object({ type: z.literal("demo.runExperiment"), commandId, durationSec: z.number().int().min(20).max(180).optional() }),
]);
export type ClientCommand = z.infer<typeof clientCommandSchema>;
export type ClientCommandType = ClientCommand["type"];

export type CommandErrorCode =
  | "INVALID_COMMAND" | "NOT_JOINED" | "ROOM_NOT_FOUND" | "ROOM_FULL" | "FORBIDDEN" | "RATE_LIMITED"
  | "NOT_ALLOWED_FOR_ROLE" | "MISSION_UNAVAILABLE" | "OUT_OF_RANGE" | "CHARACTER_INACTIVE"
  | "TRADE_REJECTED" | "CAPTURE_REJECTED" | "NOT_CAPTURED" | "MOVE_REJECTED" | "WEAPON_REJECTED" | "CREW_REJECTED" | "INTERNAL";

export interface CommandAck<R = unknown> {
  ok: boolean;
  /** Echo of requestId / commandId. */
  id: string;
  /** True when this commandId was already processed and the original result is returned. */
  duplicate?: boolean;
  error?: { code: CommandErrorCode; message: string; reason?: string };
  result?: R;
}

/** Socket.IO event names. Client -> server: `command` with an ack callback. Everything else is server -> client. */
export const SOCKET = {
  command: "command",
  snapshot: "room.snapshot",   // RoomSnapshot (join, resync, reset)
  tick: "world.tick",          // WorldTick, 10 Hz
  event: "event",              // GameEvent (reliable, ordered by serverSequence)
  agent: "agent.update",       // AgentView
  listing: "listing.update",   // ListingView
  character: "character.update", // CharacterView
  mission: "mission.update",   // MissionView
  wallet: "wallet.update",     // WalletView, private to the owner's socket
  provider: "provider.status", // ProviderStatus
  metrics: "metrics.update",   // MetricsView
  experiment: "experiment.update", // ExperimentResult
} as const;

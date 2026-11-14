// SQLite persistence (node:sqlite). Append-only events, decision traces, experiments.
// Never throws into the caller: every failure is logged once per kind and swallowed. Never stores keys.
import { mkdirSync } from "node:fs";
import path from "node:path";
import { DatabaseSync, type StatementSync } from "node:sqlite";
import type { DecisionTrace, ExperimentResult, GameEvent } from "@vice/contracts";
import type { PersistencePort } from "../domain";

export interface RunInfo { roomId: string; runId: string; roomCode: string | null; events: number; firstTime: string; lastTime: string }
export interface Persistence extends PersistencePort {
  /** Runs known to the database, newest first. */
  listRuns(): RunInfo[];
  /** Resolve a room code (e.g. VICE-42) or a room id to a room id. */
  findRoomId(roomCodeOrId: string): string | null;
  /** True when the database opened and the schema is in place. */
  readonly healthy: boolean;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS events (
  room_id TEXT NOT NULL, run_id TEXT NOT NULL, server_sequence INTEGER NOT NULL,
  event_id TEXT NOT NULL, type TEXT NOT NULL, actor_id TEXT NOT NULL,
  causation_id TEXT, correlation_id TEXT NOT NULL, time TEXT NOT NULL, sim_time_ms REAL NOT NULL,
  schema_version INTEGER NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(room_id, run_id, server_sequence)
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(room_id, run_id, type);
CREATE INDEX IF NOT EXISTS idx_events_corr ON events(correlation_id);
CREATE TABLE IF NOT EXISTS decisions (
  room_id TEXT NOT NULL, run_id TEXT NOT NULL, id TEXT NOT NULL,
  npc_id TEXT NOT NULL, role TEXT NOT NULL, trigger_event_id TEXT NOT NULL, state_hash TEXT NOT NULL,
  status TEXT NOT NULL, source TEXT, provider TEXT, model_id TEXT, selected_action TEXT,
  provider_request_id TEXT, confidence REAL, latency_ms REAL, error_type TEXT,
  requested_at_ms REAL NOT NULL, trace_json TEXT NOT NULL,
  PRIMARY KEY(room_id, run_id, id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_trigger ON decisions(room_id, run_id, trigger_event_id);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, started_at_ms REAL NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL
);
`;

export function createPersistence(databaseUrl: string): Persistence {
  const warned = new Set<string>();
  const warn = (what: string, err: unknown) => {
    if (warned.has(what)) return;
    warned.add(what);
    console.error(`[persistence] ${what} failed: ${err instanceof Error ? err.message : String(err)}`);
  };

  let db: DatabaseSync | null = null;
  let st: Record<string, StatementSync> = {};
  try {
    if (databaseUrl !== ":memory:") mkdirSync(path.dirname(databaseUrl), { recursive: true });
    db = new DatabaseSync(databaseUrl);
    if (databaseUrl !== ":memory:") db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;");
    db.exec(SCHEMA);
    st = {
      insEvent: db.prepare(`INSERT OR IGNORE INTO events (room_id, run_id, server_sequence, event_id, type, actor_id, causation_id, correlation_id, time, sim_time_ms, schema_version, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`),
      selEvents: db.prepare(`SELECT * FROM events WHERE room_id = ? AND run_id = ? AND server_sequence > ? ORDER BY server_sequence ASC`),
      upDecision: db.prepare(`INSERT INTO decisions (room_id, run_id, id, npc_id, role, trigger_event_id, state_hash, status, source, provider, model_id, selected_action, provider_request_id, confidence, latency_ms, error_type, requested_at_ms, trace_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(room_id, run_id, id) DO UPDATE SET status=excluded.status, source=excluded.source, provider=excluded.provider, model_id=excluded.model_id, selected_action=excluded.selected_action, provider_request_id=excluded.provider_request_id, confidence=excluded.confidence, latency_ms=excluded.latency_ms, error_type=excluded.error_type, trace_json=excluded.trace_json`),
      selDecisions: db.prepare(`SELECT trace_json FROM decisions WHERE room_id = ? AND run_id = ? ORDER BY requested_at_ms ASC, id ASC`),
      upExperiment: db.prepare(`INSERT INTO experiments (id, started_at_ms, status, result_json) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, result_json=excluded.result_json`),
      selExperiments: db.prepare(`SELECT result_json FROM experiments ORDER BY started_at_ms ASC`),
      selRuns: db.prepare(`SELECT room_id, run_id, COUNT(*) AS n, MIN(time) AS first_time, MAX(time) AS last_time FROM events GROUP BY room_id, run_id ORDER BY last_time DESC`),
      selRoomCreated: db.prepare(`SELECT room_id, payload_json FROM events WHERE type = 'ROOM_CREATED' ORDER BY time DESC`),
      selRoomById: db.prepare(`SELECT room_id FROM events WHERE room_id = ? LIMIT 1`),
    };
  } catch (err) {
    warn("open", err);
    try { db?.close(); } catch { /* ignore */ }
    db = null;
  }

  const parse = <T>(json: unknown): T | null => {
    try { return JSON.parse(String(json)) as T; } catch { return null; }
  };
  const roomCodes = (): Map<string, string> => {
    const out = new Map<string, string>();
    if (!db) return out;
    for (const row of st.selRoomCreated!.all() as Array<{ room_id: string; payload_json: string }>) {
      const code = parse<{ roomCode?: string }>(row.payload_json)?.roomCode;
      if (code && !out.has(row.room_id)) out.set(row.room_id, code);
    }
    return out;
  };

  return {
    get healthy() { return db !== null; },

    appendEvent(event: GameEvent): void {
      if (!db) return;
      try {
        st.insEvent!.run(event.roomId, event.runId, event.serverSequence, event.eventId, event.type, event.actorId,
          event.causationId, event.correlationId, event.time, event.simTimeMs, event.schemaVersion, JSON.stringify(event.payload));
      } catch (err) { warn("appendEvent", err); }
    },

    saveDecision(t: DecisionTrace): void {
      if (!db) return;
      try {
        st.upDecision!.run(t.roomId, t.runId, t.id, t.agentId, t.role, t.triggerEventId, t.stateHash, t.status,
          t.decisionSource, t.provider, t.model, t.chosenAction, t.providerRequestId, t.confidence, t.latencyMs,
          t.errorType, t.requestedAtMs, JSON.stringify(t));
      } catch (err) { warn("saveDecision", err); }
    },

    saveExperiment(r: ExperimentResult): void {
      if (!db) return;
      try { st.upExperiment!.run(r.id, r.startedAtMs, r.status, JSON.stringify(r)); }
      catch (err) { warn("saveExperiment", err); }
    },

    loadEvents(roomId: string, runId: string, afterSequence = 0): GameEvent[] {
      if (!db) return [];
      try {
        const rows = st.selEvents!.all(roomId, runId, afterSequence) as Array<Record<string, unknown>>;
        return rows.map((r) => ({
          schemaVersion: 1,
          eventId: String(r.event_id), roomId: String(r.room_id), runId: String(r.run_id),
          serverSequence: Number(r.server_sequence), time: String(r.time), simTimeMs: Number(r.sim_time_ms),
          type: String(r.type), actorId: String(r.actor_id),
          causationId: r.causation_id === null ? null : String(r.causation_id),
          correlationId: String(r.correlation_id), payload: parse(r.payload_json) ?? {},
        }) as GameEvent);
      } catch (err) { warn("loadEvents", err); return []; }
    },

    loadDecisions(roomId: string, runId: string): DecisionTrace[] {
      if (!db) return [];
      try {
        return (st.selDecisions!.all(roomId, runId) as Array<{ trace_json: string }>)
          .map((r) => parse<DecisionTrace>(r.trace_json)).filter((t): t is DecisionTrace => t !== null);
      } catch (err) { warn("loadDecisions", err); return []; }
    },

    loadExperiments(): ExperimentResult[] {
      if (!db) return [];
      try {
        return (st.selExperiments!.all() as Array<{ result_json: string }>)
          .map((r) => parse<ExperimentResult>(r.result_json)).filter((t): t is ExperimentResult => t !== null);
      } catch (err) { warn("loadExperiments", err); return []; }
    },

    listRuns(): RunInfo[] {
      if (!db) return [];
      try {
        const codes = roomCodes();
        return (st.selRuns!.all() as Array<{ room_id: string; run_id: string; n: number; first_time: string; last_time: string }>)
          .map((r) => ({ roomId: r.room_id, runId: r.run_id, roomCode: codes.get(r.room_id) ?? null, events: Number(r.n), firstTime: r.first_time, lastTime: r.last_time }));
      } catch (err) { warn("listRuns", err); return []; }
    },

    findRoomId(roomCodeOrId: string): string | null {
      if (!db) return null;
      try {
        const wanted = roomCodeOrId.trim().toUpperCase();
        for (const [roomId, code] of roomCodes()) if (code.toUpperCase() === wanted) return roomId;
        const byId = st.selRoomById!.get(roomCodeOrId) as { room_id: string } | undefined;
        return byId ? byId.room_id : null;
      } catch (err) { warn("findRoomId", err); return null; }
    },

    close(): void {
      try { db?.close(); } catch (err) { warn("close", err); }
      db = null;
    },
  };
}

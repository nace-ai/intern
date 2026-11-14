import type { TrainingCheckpoint } from "../server/play-service";
interface Storage {
  sql: {
    exec(
      query: string,
      ...bindings: string[]
    ): Iterable<Record<string, unknown>>;
  };
  transactionSync<T>(fn: () => T): T;
}
// SQLite rows avoid the KV API's single-value size limit for a full arena.
// This module only receives Training checkpoints, never a BYOK Runtime/key.
export class TrainingStore {
  #known = new Map<string, string>();
  constructor(private storage: Storage) {
    storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS training_state (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind,id))",
    );
  }
  load(): TrainingCheckpoint {
    const state: TrainingCheckpoint = {
      version: 1,
      sessions: [],
      rates: [],
      scores: [],
    };
    for (const row of this.storage.sql.exec(
      "SELECT kind,id,data FROM training_state",
    )) {
      const kind = String(row.kind),
        id = String(row.id),
        data = String(row.data);
      this.#known.set(JSON.stringify([kind, id]), data);
      if (kind === "session") state.sessions.push(JSON.parse(data));
      else if (kind === "rate") state.rates.push([id, JSON.parse(data)]);
      else if (kind === "scores") state.scores = JSON.parse(data);
    }
    return state;
  }
  save(state: TrainingCheckpoint) {
    const rows = new Map<string, string>();
    for (const s of state.sessions)
      rows.set(JSON.stringify(["session", s.id]), JSON.stringify(s));
    for (const [id, rate] of state.rates)
      rows.set(JSON.stringify(["rate", id]), JSON.stringify(rate));
    rows.set(JSON.stringify(["scores", "top"]), JSON.stringify(state.scores));
    this.storage.transactionSync(() => {
      for (const [key, value] of rows)
        if (this.#known.get(key) !== value) {
          const [kind, id] = JSON.parse(key);
          this.storage.sql.exec(
            "INSERT INTO training_state(kind,id,data) VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
            kind,
            id,
            value,
          );
        }
      for (const key of this.#known.keys())
        if (!rows.has(key)) {
          const [kind, id] = JSON.parse(key);
          this.storage.sql.exec(
            "DELETE FROM training_state WHERE kind=? AND id=?",
            kind,
            id,
          );
        }
    });
    this.#known = rows;
  }
}

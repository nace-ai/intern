import type { DecisionMode, ExperimentResult, ExperimentRow, GameEvent } from "@vice/contracts";
import type { BuiltRoom, RoomBuildOpts } from "../engine/compose";
import type { PersistencePort } from "../domain";
import { BotOutlaw } from "./BotOutlaw";

const MODES: DecisionMode[] = ["scripted", "meta", "drex"];
const CAVEAT = "One real-time run per mode on the same seed and bot path. Model decisions are not deterministic and timing differs between runs: this is a demonstration, not statistical evidence.";

const pct = (xs: number[], p: number): number | null => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return Math.round(s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))]!);
};

function row(mode: DecisionMode, room: BuiltRoom, startedAtMs: number): ExperimentRow {
  const ev = room.engine.events as GameEvent[];
  const traces = room.engine.brains.traces().filter((t) => t.resolvedAtMs !== null && t.decisionSource !== null);
  const lat = traces.filter((t) => t.provider !== null && t.latencyMs !== null && (t.decisionSource === "drex" || t.decisionSource === "meta" || t.decisionSource === "drex_low_confidence_guardrail")).map((t) => t.latencyMs!);
  const sent = ev.filter((e): e is GameEvent<"AGENT_MESSAGE_SENT"> => e.type === "AGENT_MESSAGE_SENT");
  const cap = ev.find((e): e is GameEvent<"CAPTURE_SUCCEEDED"> => e.type === "CAPTURE_SUCCEEDED");
  const mission = ev.find((e) => e.type === "MISSION_COMPLETED");
  const listing = Object.values(room.engine.state.listings)[0];
  const actions: Record<string, number> = {};
  for (const t of traces) if (t.chosenAction) { const k = `${t.role}:${t.chosenAction}`; actions[k] = (actions[k] ?? 0) + 1; }
  return {
    mode, decisions: traces.length, realProviderCalls: traces.filter((t) => t.provider !== null).length, fallbacks: traces.filter((t) => t.decisionSource === "fallback").length,
    latencyP50Ms: pct(lat, 50), latencyP95Ms: pct(lat, 95), messages: sent.length, rumorHops: sent.reduce((m, e) => Math.max(m, e.payload.hop), 0),
    npcTrades: ev.filter((e) => e.type === "TRADE_EXECUTED" && (e as GameEvent<"TRADE_EXECUTED">).payload.actorKind === "NPC").length,
    // Measured from the bot's mission to the capture (from the experiment start if there was no mission).
    reportedToPolice: sent.some((e) => e.payload.kind === "REPORT"), captured: !!cap, timeToCaptureSec: cap ? Math.round((cap.simTimeMs - (mission?.simTimeMs ?? startedAtMs)) / 100) / 10 : null,
    peakPrice: listing ? Math.max(...listing.history.map((h) => h.spot)) : 0, finalPrice: listing ? listing.spotPrice : 0, actions,
  };
}

export class ExperimentRunner {
  private results: ExperimentResult[] = [];
  private running = false; private n = 0;
  constructor(private build: (o: RoomBuildOpts) => BuiltRoom, private persistence: PersistencePort, private onUpdate: (r: ExperimentResult) => void, private tickHz = 10) {
    // Earlier boots' results stay visible; one that was still running when its server stopped never finished.
    try { this.results = persistence.loadExperiments().slice(-8).map((r) => (r.status === "RUNNING" ? { ...r, status: "FAILED" as const, finishedAtMs: r.finishedAtMs ?? r.startedAtMs } : r)); } catch { /* no history */ }
  }
  list(): ExperimentResult[] { return this.results; }

  /** Three headless rooms, same seed, same bot path, one per mode, run side by side in real time. */
  start(seed: number, durationSec: number): ExperimentResult | null {
    if (this.running) return null;
    this.running = true;
    const startedAtMs = Date.now();
    const id = `exp_${startedAtMs.toString(36)}_${++this.n}`;
    const result: ExperimentResult = { id, seed, durationSec, startedAtMs, finishedAtMs: null, status: "RUNNING", rows: [], caveat: CAVEAT };
    this.results.push(result); if (this.results.length > 8) this.results.shift();
    let ticks = 0;
    let lanes: Array<{ mode: DecisionMode; room: BuiltRoom; bot: BotOutlaw; capturedAtTick: number | null }> = [];
    let timer: NodeJS.Timeout | null = null;
    const finish = (status: "DONE" | "FAILED") => {
      if (timer) clearInterval(timer);
      result.rows = lanes.map((l) => row(l.mode, l.room, startedAtMs));
      result.status = status; result.finishedAtMs = Date.now();
      // Report the measured length: a run ends early once every lane has been captured.
      result.durationSec = Math.max(1, Math.round((result.finishedAtMs - startedAtMs) / 1000));
      for (const l of lanes) l.room.scheduler.cancelAll();
      this.running = false;
      try { this.persistence.saveExperiment(result); } catch (err) { console.error("[experiment] save failed", (err as Error).message); }
      this.onUpdate(result);
    };
    try {
      // 2 in-flight calls per lane keeps three lanes plus the live room inside the provider's in-flight limit.
      lanes = MODES.map((mode) => {
        const room = this.build({ roomId: `${id}_${mode}`, code: `${id}_${mode}`, seed, mode, concurrency: 2, shadowMeta: false });
        const lane = { mode, room, bot: new BotOutlaw(room.engine), capturedAtTick: null as number | null };
        room.engine.onEvent((e) => { if (e.type === "CAPTURE_SUCCEEDED" && lane.capturedAtTick === null) lane.capturedAtTick = ticks; });
        return lane;
      });
    } catch (err) { console.error("[experiment] setup failed", err); finish("FAILED"); return result; }
    const total = durationSec * this.tickHz; const settle = 3 * this.tickHz;
    timer = setInterval(() => {
      try {
        for (const l of lanes) { l.bot.step(); l.room.engine.tick(); l.room.engine.drainDirty(); }
        if (++ticks % (this.tickHz * 2) === 0) { result.rows = lanes.map((l) => row(l.mode, l.room, startedAtMs)); this.onUpdate(result); }
        // Stop at the requested duration, or a few seconds after every lane's outlaw has been captured and decisions have drained.
        const allOver = lanes.every((l) => l.capturedAtTick !== null && ticks - l.capturedAtTick >= settle && l.room.scheduler.pending() === 0);
        if (ticks >= total || allOver) finish("DONE");
      } catch (err) { console.error("[experiment] tick failed", err); finish("FAILED"); }
    }, Math.round(1000 / this.tickHz));
    this.onUpdate(result);
    return result;
  }
}

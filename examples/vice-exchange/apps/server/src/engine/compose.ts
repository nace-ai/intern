// Composition root: wires the swarm's modules into a RoomEngine.
import type { DecisionMode, DecisionTrace, ExperimentResult } from "@vice/contracts";
import type { Clock, MetricsPort, PersistencePort, PolicyPort, SchedulerPort } from "../domain";
import { createBrains, createMemoryStore } from "../agents";
import { createScheduler } from "../decisions";
import { createEconomy } from "../economy";
import { createMetrics } from "../metrics";
import { FugitiveAgent } from "../demo/Fugitive";
import { RoomEngine } from "./RoomEngine";
import { systemClock } from "./util";

export interface ProviderConfig {
  naceApiKey: string; naceModel: string; metaApiKey: string; metaBaseUrl: string; metaModel: string; concurrency: number; maxCallsPerMinute: number;
  /** In drex mode also ask Meta in the background (never applied). Only acts when both keys are configured. */
  shadowMeta?: boolean;
}
export interface SchedulerHooks { onShadow: (trace: DecisionTrace) => void }
export interface RoomFactoryDeps {
  persistence: PersistencePort; providers: ProviderConfig; clock?: Clock; policy?: PolicyPort | null;
  experiments?: () => ExperimentResult[];
  onRunExperiment?: (engine: RoomEngine, durationSec: number) => ExperimentResult | null;
}
export interface BuiltRoom { engine: RoomEngine; scheduler: SchedulerPort; metrics: MetricsPort; fugitive?: FugitiveAgent | null }
export interface RoomBuildOpts {
  roomId: string; code: string; seed: number; mode: DecisionMode; concurrency?: number; clock?: Clock;
  /** Add the autonomous hunted outlaw to this room. Off unless asked (tests and experiment lanes do not get one). */
  fugitive?: boolean;
  /** Per-room override of ProviderConfig.shadowMeta (experiment lanes turn it off). */
  shadowMeta?: boolean;
  /** A ready scheduler, or a factory that receives the room's hooks (tests use this with stub providers). */
  scheduler?: SchedulerPort | ((hooks: SchedulerHooks) => SchedulerPort);
}

export function createRoomFactory(deps: RoomFactoryDeps): (o: RoomBuildOpts) => BuiltRoom {
  return (o) => {
    const clock = o.clock ?? deps.clock ?? systemClock;
    const memory = createMemoryStore();
    const metrics = createMetrics();
    // The scheduler exists before the engine, so the shadow callback reaches the engine through a late-bound reference.
    let room: RoomEngine | null = null;
    const hooks: SchedulerHooks = { onShadow: (trace) => room?.applyShadow(trace) };
    const shadowMeta = (o.shadowMeta ?? deps.providers.shadowMeta ?? false) && deps.providers.naceApiKey.length > 0 && deps.providers.metaApiKey.length > 0;
    const scheduler = typeof o.scheduler === "function" ? o.scheduler(hooks)
      : o.scheduler ?? createScheduler({ roomId: o.roomId, mode: o.mode, clock, ...deps.providers, concurrency: o.concurrency ?? deps.providers.concurrency, shadowMeta, onShadow: hooks.onShadow });
    const engine = new RoomEngine({
      clock, economy: createEconomy(), memory, scheduler, persistence: deps.persistence, metrics, policy: deps.policy ?? null,
      createBrains: (world, onTrace) => createBrains({ world, memory, scheduler, policy: deps.policy ?? null, onTrace }),
      experiments: deps.experiments, onRunExperiment: deps.onRunExperiment,
    }, { roomId: o.roomId, code: o.code, seed: o.seed, mode: o.mode });
    room = engine;
    return { engine, scheduler, metrics, fugitive: o.fugitive ? new FugitiveAgent(engine) : null };
  };
}

import type { DecisionMode, ProviderStatus, DecisionTrace } from "@vice/contracts";
import type { Clock, SchedulerPort } from "../domain";
import { createDrexProvider, createMetaProvider } from "./providers";
import { createScheduler as createSchedulerFromDeps, type SchedulerDeps } from "./scheduler";

export { createDrexProvider, createMetaProvider } from "./providers";
export type { DrexProviderConfig, MetaProviderConfig } from "./providers";
export { stateHash } from "./scheduler";
export type { SchedulerDeps } from "./scheduler";

/** Flat form used by the engine: one scheduler per room, built straight from config. Safe with empty keys. */
export interface SchedulerOptions {
  roomId?: string; mode: DecisionMode; clock: Clock;
  naceApiKey: string; naceModel: string; metaApiKey: string; metaBaseUrl: string; metaModel: string;
  concurrency: number; maxCallsPerMinute: number;
  onStatus?: (s: ProviderStatus) => void; shadowMeta?: boolean; onShadow?: (t: DecisionTrace) => void;
}

/** Accepts either ready-made providers (SchedulerDeps) or flat config (SchedulerOptions). Never throws at construction. */
export function createScheduler(opts: SchedulerDeps | SchedulerOptions): SchedulerPort {
  if ("drex" in opts) return createSchedulerFromDeps(opts);
  return createSchedulerFromDeps({
    drex: createDrexProvider({ apiKey: opts.naceApiKey ?? "", model: opts.naceModel }),
    meta: createMetaProvider({ apiKey: opts.metaApiKey ?? "", baseUrl: opts.metaBaseUrl, model: opts.metaModel }),
    clock: opts.clock, mode: opts.mode, concurrency: opts.concurrency, maxCallsPerMinute: opts.maxCallsPerMinute,
    onStatus: opts.onStatus, shadowMeta: opts.shadowMeta, onShadow: opts.onShadow,
  });
}

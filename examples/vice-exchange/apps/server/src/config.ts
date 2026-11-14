import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** Repo root, resolved from this file (apps/server/src -> ../../..) so it works from any cwd. */
export const repoRoot = path.resolve(here, "../../..");

try {
  // Variables already present in the process environment take precedence over the file.
  process.loadEnvFile(path.join(repoRoot, ".env"));
} catch {
  // No .env file (or unreadable): fall back to the process environment and defaults.
}

function str(name: string, fallback = ""): string {
  const raw = process.env[name];
  return raw === undefined || raw.trim() === "" ? fallback : raw.trim();
}

function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function resolveDatabaseUrl(raw: string): string {
  return raw === ":memory:" ? raw : path.resolve(repoRoot, raw);
}

export interface Config {
  /** HTTP + Socket.IO port. */
  port: number;
  /** Browser origin allowed to talk to the server directly (the Vite dev server proxies by default). */
  webOrigin: string;
  /** Absolute path of the SQLite file (or ":memory:"). */
  databaseUrl: string;
  /** Authoritative world tick rate. */
  tickHz: number;
  /** Max concurrent in-flight provider decision calls. */
  decisionConcurrency: number;
  /** Budget guard: max real provider decision calls per minute. */
  maxDecisionCallsPerMin: number;
  /** Secret for demo-director commands. Empty string = demo-director commands disabled. */
  demoDirectorSecret: string;
  /** SECRET. Nace Drex key (nace_sk_...). Empty string = Drex not configured. Never log. */
  naceApiKey: string;
  naceModel: string;
  /** SECRET. Meta model API key. Empty string = Meta not configured. Never log. */
  metaApiKey: string;
  metaBaseUrl: string;
  metaModel: string;
  /** SHADOW_META: in drex mode also ask Meta in the background (never applied) for a live side-by-side. Default on; only acts when both keys exist. */
  shadowMeta: boolean;
  /** The autonomous hunted outlaw in live rooms. FUGITIVE=0 turns it off. */
  fugitive: boolean;
}

export const config: Config = {
  port: num("PORT", 3001),
  webOrigin: str("WEB_ORIGIN", "http://localhost:5173"),
  databaseUrl: resolveDatabaseUrl(str("DATABASE_URL", "./data/vice-exchange.sqlite")),
  tickHz: num("GAME_TICK_HZ", 10),
  decisionConcurrency: num("NPC_DECISION_CONCURRENCY", 4),
  maxDecisionCallsPerMin: num("MAX_DECISION_CALLS_PER_MIN", 30),
  demoDirectorSecret: str("DEMO_DIRECTOR_SECRET"),
  naceApiKey: str("NACE_API_KEY"),
  naceModel: str("NACE_DEFAULT_DECISION_MODEL", "drex-latest"),
  // LLAMA_API_KEY is accepted as an alias for META_API_KEY.
  metaApiKey: str("META_API_KEY") || str("LLAMA_API_KEY"),
  metaBaseUrl: str("META_BASE_URL", "https://api.meta.ai/v1"),
  metaModel: str("META_DECISION_MODEL") || str("META_MODEL", "muse-ember-1.0"),
  shadowMeta: !/^(0|false|off|no)$/i.test(str("SHADOW_META", "on")),
  fugitive: !/^(0|false|off|no)$/i.test(str("FUGITIVE", "on")),
};

/** Safe-to-log / safe-to-serve view of the config: booleans only, never key material. */
export function publicConfigSummary(): { drexConfigured: boolean; metaConfigured: boolean } {
  return {
    drexConfigured: config.naceApiKey.length > 0,
    metaConfigured: config.metaApiKey.length > 0,
  };
}

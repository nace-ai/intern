import { randomBytes } from "node:crypto";
import { DEFAULT_ROOM_CODE } from "@vice/contracts";
import { config, publicConfigSummary } from "./config";
import type { PolicyPort } from "./domain";
import { createApp } from "./transport/app";

// The first browser to join the default room directs it. The secret is the recovery path (join form, "director secret")
// if that browser loses its session. When none is configured, one is generated for this run and shown to the operator only.
const directorSecret = config.demoDirectorSecret || randomBytes(9).toString("base64url");

// Police policy grounded in a real document through NDI. Optional: the server boots and runs without it.
let policy: (PolicyPort & { start(): Promise<void>; status?: () => unknown; xraySvg?: () => string | null }) | null = null;
try {
  const { createPolicy } = await import("./policy/index");
  policy = createPolicy(config); // returns at once; view() stays null until real NDI facts are loaded
} catch (err) {
  console.error("[policy] module not loaded:", err instanceof Error ? err.message : err);
}

const app = createApp({
  policy, fugitive: config.fugitive,
  port: config.port, databaseUrl: config.databaseUrl, directorSecret, tickHz: config.tickHz, webOrigin: config.webOrigin,
  providers: {
    naceApiKey: config.naceApiKey, naceModel: config.naceModel, metaApiKey: config.metaApiKey, metaBaseUrl: config.metaBaseUrl, metaModel: config.metaModel,
    concurrency: config.decisionConcurrency, maxCallsPerMinute: config.maxDecisionCallsPerMin, shadowMeta: config.shadowMeta,
  },
});

app.listen().then((port) => {
  const { drexConfigured, metaConfigured } = publicConfigSummary(); // booleans only, never key material
  console.log(`[vice-exchange] server listening on http://0.0.0.0:${port} (drexConfigured=${drexConfigured}, metaConfigured=${metaConfigured}, room=${DEFAULT_ROOM_CODE})`);
  if (!config.demoDirectorSecret) console.log(`[vice-exchange] demo director secret for this run: ${directorSecret} (set DEMO_DIRECTOR_SECRET in .env to keep one)`);
}).catch((err: unknown) => {
  console.error("[vice-exchange] failed to start:", err instanceof Error ? err.message : err);
  process.exit(1);
});

// Cached NDI results load in milliseconds; a first run calls NDI in the background. Either way boot never waits.
void policy?.start().then(() => app.announcePolicy()).catch((err: unknown) => console.error("[policy] start failed:", err instanceof Error ? err.message : err));

let closing = false;
const shutdown = () => {
  if (closing) return;
  closing = true;
  setTimeout(() => process.exit(0), 800).unref();
  void app.close().then(() => process.exit(0));
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

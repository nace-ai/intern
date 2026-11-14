// `npm run ndi:ingest` : runs the policy ingestion once and prints what happened.
//   (no flag)   use the cached NDI result when there is one (no NDI call, nothing billed), else run the real jobs
//   --force     ignore the cache and run new NDI jobs (billed again)
//   --json      machine-readable output
//   --no-color  plain text
// Prints the document metadata, the NDI job ids, the elapsed time and the facts. Never prints the Nace key.
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { config } from "../config";
import { POLICY_DOCUMENT } from "./document";
import { ingestPolicy } from "./ingest";
import { DEFAULT_DATA_DIR } from "./index";
import { createNdiClient, redact } from "./ndi";
import { POLICY_FIELDS } from "./schema";
import { renderReport } from "./report";
import { xrayOf } from "./xray";

const args = new Set(process.argv.slice(2));
const force = args.has("--force");
const json = args.has("--json");
const color = !json && !args.has("--no-color") && !process.env.NO_COLOR && (process.stdout.isTTY || process.env.FORCE_COLOR === "1");

const steps: Array<{ step: string; atMs: number }> = [];
const outcome = await ingestPolicy({
  dataDir: DEFAULT_DATA_DIR, document: POLICY_DOCUMENT, fields: POLICY_FIELDS,
  client: createNdiClient(config.naceApiKey), fetch: globalThis.fetch, nowMs: Date.now, force,
  onStep: (step) => {
    steps.push({ step, atMs: Date.now() });
    if (!json) process.stderr.write(`[ndi:ingest] ${step}...\n`);
  },
});

// The X-ray picture of this ingestion (git-ignored, next to the cache). Open it in a browser.
let xrayPath: string | null = null;
if (outcome.ok) {
  try {
    await mkdir(DEFAULT_DATA_DIR, { recursive: true });
    xrayPath = path.join(DEFAULT_DATA_DIR, "policy-xray.svg");
    await writeFile(xrayPath, xrayOf(outcome));
  } catch { xrayPath = null; }
}

if (json) {
  const out = outcome.ok
    ? {
      ok: true, cacheHit: outcome.cacheHit, cachePath: outcome.cachePath, xrayPath, tookMs: outcome.tookMs,
      document: { ...outcome.record.document, pages: outcome.derived.pageCount },
      ndi: {
        jobId: outcome.record.ndi.extract.jobId, parseJobId: outcome.record.ndi.parse.jobId, ranAt: outcome.record.ndi.ranAt,
        elapsedMs: outcome.record.ndi.elapsedMs, parseElapsedMs: outcome.record.ndi.parse.elapsedMs, extractElapsedMs: outcome.record.ndi.extract.elapsedMs,
        parseCost: outcome.record.ndi.parse.job.cost ?? null, extractCost: outcome.record.ndi.extract.job.cost ?? null,
      },
      identity: outcome.derived.identity, facts: outcome.derived.facts, rejected: outcome.derived.rejected, warnings: outcome.derived.warnings,
    }
    : outcome;
  process.stdout.write(`${redact(JSON.stringify(out, null, 2))}\n`);
} else {
  process.stdout.write(`${redact(renderReport(outcome, { color, document: POLICY_DOCUMENT, keyConfigured: config.naceApiKey.length > 0 }))}\n`);
  if (xrayPath) process.stdout.write(`  x-ray: ${xrayPath}  (animated SVG: open it in a browser)\n`);
}
process.exitCode = outcome.ok ? 0 : 1;

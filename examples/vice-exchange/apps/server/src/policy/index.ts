// POLICY module: brings ONE real-world document into the simulation through Nace NDI (Perception).
//
//   const policy = createPolicy(config);   // returns at once; view() is null
//   void policy.start();                   // loads the cached NDI result, or runs the real NDI jobs once
//
// Honesty contract: view() is non-null only when a real NDI Extract job produced the facts in this repo (run now,
// or run earlier and read back from data/ndi/). With no key, no network or a failed job it stays null and
// status() says why. Nothing here is hand-written policy text.
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Citation, NpcRole, PolicyKnowledgeView } from "@vice/contracts";
import type { PolicyPort } from "../domain";
import { POLICY_DOCUMENT, type PolicyDocumentSpec } from "./document";
import type { PageRegion, PolicyFact, RejectedField } from "./facts";
import { ingestPolicy, totalCostUsd, type IngestFailureReason, type IngestOutcome } from "./ingest";
import { createNdiClient, redact, type NdiClient } from "./ndi";
import { POLICY_FIELDS, type PolicyFieldSpec } from "./schema";
import { xrayOf } from "./xray";

export { POLICY_DOCUMENT } from "./document";
export type { PolicyDocumentSpec } from "./document";
export { deriveFacts, fingerprint, pageTexts } from "./facts";
export type { PageRegion, PolicyFact, RejectedField } from "./facts";
export { cachePathFor, ingestPolicy, readCache } from "./ingest";
export type { IngestOutcome, IngestRecord } from "./ingest";
export { createNdiClient } from "./ndi";
export type { NdiClient } from "./ndi";
export { POLICY_FIELDS, buildExtractSchema, schemaHash } from "./schema";
export { pageLayout, renderXraySvg, xrayOf } from "./xray";
export type { PageLayout, XrayInput } from "./xray";

/** The part of the app config this module reads. The Nace key reaches it only through this object. */
export interface PolicyConfig { naceApiKey?: string | null }

export interface PolicyOptions {
  /** Where the document and the NDI cache live. Default: <repo>/data/ndi (git-ignored). */
  dataDir?: string;
  document?: PolicyDocumentSpec;
  fields?: readonly PolicyFieldSpec[];
  /** Tests inject a fake NDI client. Default: the real nace-sdk client built from cfg.naceApiKey. */
  client?: NdiClient | null;
  fetch?: typeof fetch;
  nowMs?: () => number;
  /** One line per state change. Default: console.log. Never receives key material. */
  log?: (line: string) => void;
}

export interface PolicyStatus {
  /** idle: start() not called. loading: working. ready: view() is non-null. unavailable: view() is null (see reason). */
  state: "idle" | "loading" | "ready" | "unavailable";
  /** Why view() is null, e.g. "no_api_key", "download_failed", "ndi_job_failed". null when ready or not started. */
  reason: IngestFailureReason | null;
  message: string | null;
  /**
   * Label for the UI. "ndi_live": the NDI jobs ran in THIS process. "ndi_cache": an earlier real NDI job, read back
   * from data/ndi/. null: no NDI facts are loaded.
   */
  source: "ndi_live" | "ndi_cache" | null;
  document: {
    id: string; title: string; publisher: string; sourceUrl: string; landingUrl: string | null; revision: string;
    sha256: string; bytes: number; pages: number; rightsNote: string;
    /** ISO time the ingested bytes were downloaded from sourceUrl; null until a real ingestion is loaded. */
    retrievedAt: string | null;
  };
  ndi: {
    /** The Extract job that produced the facts (the id every Citation carries). */
    jobId: string; parseJobId: string;
    /** ISO time the real NDI jobs ran. On a cache hit this is the ORIGINAL run, not this boot. */
    ranAt: string;
    /** Wall clock of that original run (upload + parse + extract), ms. */
    elapsedMs: number;
    /** USD the Nace API recorded for the two jobs; null when it reported none. */
    costUsd: string | null;
    /** Nace's own timestamps and server duration for the Extract job; null when the API did not report them. */
    jobCreatedAt: string | null; jobFinishedAt: string | null; jobServerMs: number | null;
    /** true when Nace answered the create with an earlier job of the same work (an idempotent replay). */
    replayed: boolean | null;
    cacheHit: boolean;
    /** Cache file, relative to the repo root (never an absolute path: this object is served to browsers). */
    cachePath: string;
  } | null;
  factCount: number;
  /** Every admitted fact with its page, NDI confidence and highlight boxes (for a document overlay). */
  facts: PolicyFact[];
  /** Fields NDI did not find, or whose quote failed the word-for-word check. They are not shown as facts. */
  rejected: RejectedField[];
  /** What NDI read as the order's own number, title and dates (null when NDI did not find it). */
  identity: Record<string, string | null>;
  startedAtMs: number | null; finishedAtMs: number | null;
}

export interface PolicyService extends PolicyPort {
  /** Loads the facts. Idempotent, never rejects, never blocks the caller unless awaited. */
  start(): Promise<void>;
  status(): PolicyStatus;
  /** Highlight boxes for one fact (normalized page coordinates), [] when unknown. */
  regionsFor(field: string): PageRegion[];
  /**
   * "NDI X-ray": an animated SVG of the document drawn from NDI's own layout blocks, with every cited region
   * glowing and wired to its fact. null until real NDI facts are loaded. Serve it as image/svg+xml.
   */
  xraySvg(): string | null;
}

const here = path.dirname(fileURLToPath(import.meta.url));
/** apps/server/src/policy -> repo root. */
const REPO_ROOT = path.resolve(here, "../../../..");
export const DEFAULT_DATA_DIR = path.join(REPO_ROOT, "data/ndi");

/** status() is served to browsers: it never carries an absolute path of this machine. */
function publicPath(file: string): string {
  const rel = path.relative(REPO_ROOT, file);
  return rel && !rel.startsWith("..") && !path.isAbsolute(rel) ? rel.split(path.sep).join("/") : path.basename(file);
}
function publicText(text: string, dataDir: string): string {
  return text.split(dataDir).join("data/ndi").split(REPO_ROOT).join(".").replace(/(?:\/(?:Users|home)\/[^/\s'"]+)/g, "~");
}
const isoOrNull = (v: unknown): string | null => (typeof v === "string" && !Number.isNaN(Date.parse(v)) ? v : null);

const copyFact = (f: PolicyFact): PolicyFact => ({ ...f, roles: [...f.roles], regions: f.regions.map((r) => ({ ...r })) });

export function createPolicy(cfg?: PolicyConfig | null, opts: PolicyOptions = {}): PolicyService {
  const doc = opts.document ?? POLICY_DOCUMENT;
  const fields = opts.fields ?? POLICY_FIELDS;
  const dataDir = opts.dataDir ?? DEFAULT_DATA_DIR;
  const nowMs = opts.nowMs ?? Date.now;
  const log = (line: string): void => { try { (opts.log ?? console.log)(redact(line)); } catch { /* logging must not break boot */ } };

  let view: PolicyKnowledgeView | null = null;
  let byRole: Partial<Record<NpcRole, Citation[]>> = {};
  let started: Promise<void> | null = null;
  let xray: string | null = null;
  let status: PolicyStatus = {
    state: "idle", reason: null, message: null, source: null,
    document: {
      id: doc.id, title: doc.title, publisher: doc.publisher, sourceUrl: doc.sourceUrl, landingUrl: doc.landingUrl, revision: doc.revision,
      sha256: doc.sha256, bytes: doc.bytes, pages: doc.pages, rightsNote: doc.rightsNote, retrievedAt: null,
    },
    ndi: null, factCount: 0, facts: [], rejected: [], identity: {}, startedAtMs: null, finishedAtMs: null,
  };

  const apply = (outcome: IngestOutcome): void => {
    if (!outcome.ok) {
      view = null; byRole = {}; xray = null;
      status = { ...status, state: "unavailable", reason: outcome.reason, message: publicText(outcome.message, dataDir), source: null, ndi: null, factCount: 0, facts: [], rejected: [], identity: {}, finishedAtMs: nowMs() };
      log(`[policy] no NDI policy facts (${outcome.reason}): ${outcome.message}`);
      return;
    }
    const { record, derived } = outcome;
    const jobId = record.ndi.extract.jobId;
    const extractJob = record.ndi.extract.job;
    const cite = (f: PolicyFact): Citation => ({
      documentId: doc.id, documentTitle: record.document.title, sourceUrl: record.document.sourceUrl,
      page: f.page, quote: f.quote, extractedField: f.field, ndiJobId: jobId,
    });
    const roles: NpcRole[] = ["WITNESS", "INFORMANT", "TRADER", "POLICE"];
    byRole = Object.fromEntries(roles.map((r) => [r, derived.facts.filter((f) => f.roles.includes(r)).map(cite)]));
    view = { documentTitle: record.document.title, sourceUrl: record.document.sourceUrl, ingestedVia: "ndi", ndiJobId: jobId, facts: derived.facts.map(cite) };
    status = {
      ...status, state: "ready", reason: null, message: null, source: outcome.cacheHit ? "ndi_cache" : "ndi_live",
      document: { ...status.document, retrievedAt: record.document.retrievedAt },
      ndi: {
        jobId, parseJobId: record.ndi.parse.jobId, ranAt: record.ndi.ranAt, elapsedMs: record.ndi.elapsedMs,
        costUsd: totalCostUsd(record),
        jobCreatedAt: isoOrNull(extractJob.created_at), jobFinishedAt: isoOrNull(extractJob.finished_at),
        jobServerMs: typeof extractJob.timing?.total_ms === "number" ? extractJob.timing.total_ms : null,
        replayed: typeof extractJob.replayed === "boolean" ? extractJob.replayed : null,
        cacheHit: outcome.cacheHit, cachePath: publicPath(outcome.cachePath),
      },
      factCount: derived.facts.length, facts: derived.facts, rejected: derived.rejected, identity: derived.identity, finishedAtMs: nowMs(),
    };
    try { xray = xrayOf(outcome); } catch { xray = null; } // the picture is optional; the facts are not
    log(`[policy] ${derived.facts.length} NDI policy facts ready from "${record.document.title}" (${outcome.cacheHit ? "cached result of" : "live"} NDI job ${jobId}, ran ${record.ndi.ranAt})`);
  };

  const run = async (): Promise<void> => {
    status = { ...status, state: "loading", startedAtMs: nowMs() };
    try {
      const client = opts.client !== undefined ? opts.client : createNdiClient(cfg?.naceApiKey);
      apply(await ingestPolicy({ dataDir, document: doc, fields, client, fetch: opts.fetch ?? globalThis.fetch, nowMs }));
    } catch (err) {
      apply({ ok: false, reason: "internal_error", message: err instanceof Error ? err.message : String(err), jobId: null, cachePath: "", tookMs: 0 });
    }
  };

  // Callers get copies: nothing they do to a returned object can change (or throw on) the loaded policy.
  return {
    view: () => (view ? { ...view, facts: view.facts.map((c) => ({ ...c })) } : null),
    factsFor: (role) => (byRole[role] ?? []).map((c) => ({ ...c })),
    start: () => (started ??= run().catch(() => undefined)),
    status: () => ({
      ...status, document: { ...status.document }, ndi: status.ndi ? { ...status.ndi } : null,
      facts: status.facts.map(copyFact), rejected: status.rejected.map((r) => ({ ...r })), identity: { ...status.identity },
    }),
    regionsFor: (field) => (status.facts.find((f) => f.field === field)?.regions ?? []).map((r) => ({ ...r })),
    xraySvg: () => xray,
  };
}

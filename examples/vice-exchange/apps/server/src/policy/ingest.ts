// One ingestion: cache lookup -> download the pinned document -> NDI Parse -> NDI Extract -> cache the RAW jobs.
// The cache (data/ndi/policy-<sha256>.json, git-ignored) is the only thing a restart reads, so NDI is billed once
// per document + question set. A cache hit keeps the original job ids and the time the real jobs ran.
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import type { DocumentJob } from "nace-sdk";
import { PolicyDocumentError, obtainDocument, type PolicyDocumentSpec, type StoredDocument } from "./document";
import { deriveFacts, type DerivedPolicy } from "./facts";
import { NdiError, redact, runJob, toNdiError, type NdiClient } from "./ndi";
import { EXTRACT_INSTRUCTIONS, EXTRACT_OPTIONS, PARSE_OPTIONS, POLICY_FIELDS, buildExtractSchema, schemaHash, type PolicyFieldSpec } from "./schema";

export const CACHE_VERSION = 1;

export interface CachedJob {
  jobId: string;
  /** The Idempotency-Key the job was created under. */
  idempotencyKey: string;
  /** Wall clock we measured from create to terminal, ms. */
  elapsedMs: number;
  /** The job exactly as the Nace API returned it (status, timing, cost, result). */
  job: DocumentJob;
}

/** The cache file. Everything under `ndi` came from the Nace API; nothing in it is hand-written. */
export interface IngestRecord {
  cacheVersion: number;
  document: {
    id: string; title: string; publisher: string; sourceUrl: string; fileName: string;
    sha256: string; bytes: number; retrievedAt: string; httpLastModified: string | null;
  };
  schemaHash: string;
  ndi: {
    /** ISO time the real NDI work of this record started. */
    ranAt: string;
    /** Wall clock for upload + parse + extract of the run that produced `extract`, ms. */
    elapsedMs: number;
    upload: { workspaceId: string; fileId: string; elapsedMs: number } | null;
    parse: CachedJob;
    extract: CachedJob;
  };
}

export interface IngestDeps {
  dataDir: string;
  document: PolicyDocumentSpec;
  fields?: readonly PolicyFieldSpec[];
  /** null = no Nace key configured. A cache hit needs no client. */
  client: NdiClient | null;
  fetch: typeof fetch;
  nowMs(): number;
  /** Ignore the cache and run (and pay for) new NDI jobs. Only the CLI's --force sets this. */
  force?: boolean;
  parseWaitMs?: number;
  extractWaitMs?: number;
  onStep?(step: string): void;
}

export type IngestFailureReason =
  | "no_api_key" | "download_failed" | "document_hash_mismatch" | "document_not_pdf" | "document_too_large"
  | "document_io_error" | "no_verified_facts" | "internal_error"
  | NdiError["reason"];

export type IngestOutcome =
  | { ok: true; cacheHit: boolean; cachePath: string; record: IngestRecord; derived: DerivedPolicy; /** This call, ms. */ tookMs: number }
  | { ok: false; reason: IngestFailureReason; message: string; jobId: string | null; cachePath: string; tookMs: number };

/** USD the Nace API recorded for the parse and extract jobs together; null when it reported no cost. */
export function totalCostUsd(record: IngestRecord): string | null {
  let total = 0; let known = false;
  for (const job of [record.ndi.parse.job, record.ndi.extract.job]) {
    const usd = job.cost && typeof job.cost.total_usd === "string" ? Number(job.cost.total_usd) : NaN;
    if (Number.isFinite(usd)) { total += usd; known = true; }
  }
  return known ? total.toFixed(4) : null;
}

export function cachePathFor(dataDir: string, sha256: string): string {
  return path.join(dataDir, `policy-${sha256}.json`);
}

const succeeded = (c: CachedJob | undefined): c is CachedJob =>
  !!c && typeof c.jobId === "string" && c.jobId.length > 0 && !!c.job && c.job.status === "succeeded" && !!c.job.result && typeof c.job.result === "object";

/** Reads the cache for this document. Null when absent, unreadable, or for different bytes. Never throws. */
export async function readCache(dataDir: string, doc: PolicyDocumentSpec): Promise<IngestRecord | null> {
  try {
    const rec = JSON.parse(await readFile(cachePathFor(dataDir, doc.sha256), "utf8")) as IngestRecord;
    if (rec?.cacheVersion !== CACHE_VERSION || rec.document?.sha256 !== doc.sha256 || typeof rec.schemaHash !== "string") return null;
    if (typeof rec.ndi?.ranAt !== "string" || !succeeded(rec.ndi.parse) || !succeeded(rec.ndi.extract)) return null;
    return rec;
  } catch {
    return null;
  }
}

async function writeCache(dataDir: string, rec: IngestRecord): Promise<void> {
  await mkdir(dataDir, { recursive: true });
  const file = cachePathFor(dataDir, rec.document.sha256);
  const tmp = `${file}.${process.pid}.tmp`;
  await writeFile(tmp, `${JSON.stringify(rec, null, 2)}\n`);
  await rename(tmp, file);
}

/** Append-only evidence trail of every ingestion attempt (git-ignored). Best effort. */
async function logAttempt(dataDir: string, entry: Record<string, unknown>): Promise<void> {
  try {
    await mkdir(dataDir, { recursive: true });
    await appendFile(path.join(dataDir, "ingest-log.jsonl"), `${JSON.stringify(entry)}\n`);
  } catch { /* the log is a convenience, not a dependency */ }
}

/** Runs the ingestion once. Never throws: every failure is an `ok: false` outcome with a reason. */
export async function ingestPolicy(deps: IngestDeps): Promise<IngestOutcome> {
  const t0 = deps.nowMs();
  const fields = deps.fields ?? POLICY_FIELDS;
  const doc = deps.document;
  const cachePath = cachePathFor(deps.dataDir, doc.sha256);
  const hash = schemaHash(fields);
  const fail = async (reason: IngestFailureReason, message: string, jobId: string | null = null): Promise<IngestOutcome> => {
    const safe = redact(message);
    await logAttempt(deps.dataDir, { at: new Date(deps.nowMs()).toISOString(), ok: false, reason, message: safe, jobId, sha256: doc.sha256 });
    return { ok: false, reason, message: safe, jobId, cachePath, tookMs: deps.nowMs() - t0 };
  };

  try {
    const cached = await readCache(deps.dataDir, doc);
    if (cached && cached.schemaHash === hash && !deps.force) {
      const derived = deriveFacts(cached.ndi.parse.job.result, cached.ndi.extract.job.result, fields);
      if (derived.facts.length === 0) return await fail("no_verified_facts", "the cached NDI result holds no fact that passes the verbatim check", cached.ndi.extract.jobId);
      return { ok: true, cacheHit: true, cachePath, record: cached, derived, tookMs: deps.nowMs() - t0 };
    }

    const client = deps.client;
    if (!client) return await fail("no_api_key", "NACE_API_KEY is not configured and there is no cached NDI result for this document");

    // Parse: reuse the cached parse of the same bytes when only the questions changed (nothing is parsed or paid twice).
    let upload: IngestRecord["ndi"]["upload"] = cached && !deps.force ? cached.ndi.upload : null;
    let parse: CachedJob | null = cached && !deps.force ? cached.ndi.parse : null;

    // The document is needed only when it has to be parsed; it is fetched before the NDI clock starts.
    const held: { doc: StoredDocument | null } = { doc: null };
    const getDocument = async (): Promise<StoredDocument> => {
      if (!held.doc) { deps.onStep?.("document"); held.doc = await obtainDocument(doc, deps.dataDir, deps.fetch, deps.nowMs); }
      return held.doc;
    };
    if (!parse) await getDocument();

    const ranAt = new Date(deps.nowMs()).toISOString();
    const tNdi = deps.nowMs();
    const short = doc.sha256.slice(0, 32);

    const runParse = async (): Promise<CachedJob> => {
      const stored = await getDocument();
      deps.onStep?.("upload");
      const tUp = deps.nowMs();
      let file;
      try { file = await client.documents.upload(stored.bytes, { file_name: doc.fileName, on_conflict: "new_version" }); } catch (err) { throw toNdiError(err, "upload"); }
      upload = { workspaceId: file.workspace_id, fileId: file.file_id, elapsedMs: deps.nowMs() - tUp };
      deps.onStep?.("parse");
      const run = await runJob({
        step: "parse", stableKey: `policy-ingest:parse:${short}:v${CACHE_VERSION}`, forceFresh: deps.force, nowMs: deps.nowMs,
        waitTimeoutMs: deps.parseWaitMs ?? 150_000, wait: (id, o) => client.jobs.wait(id, o),
        create: (key) => client.documents.parse({
          source: { type: "workspace_file", workspace_id: file.workspace_id, file_id: file.file_id },
          ...PARSE_OPTIONS, wait_seconds: 60, idempotency_key: key,
        }),
      });
      return { jobId: run.job.job_id, idempotencyKey: run.idempotencyKey, elapsedMs: run.elapsedMs, job: run.job };
    };
    const runExtract = async (parseJobId: string): Promise<CachedJob> => {
      deps.onStep?.("extract");
      const run = await runJob({
        step: "extract", stableKey: `policy-ingest:extract:${short}:${hash}:v${CACHE_VERSION}`, forceFresh: deps.force, nowMs: deps.nowMs,
        waitTimeoutMs: deps.extractWaitMs ?? 240_000, wait: (id, o) => client.jobs.wait(id, o),
        create: (key) => client.documents.extract({
          source: { type: "parse_result", job_id: parseJobId },
          schema: buildExtractSchema(fields), instructions: EXTRACT_INSTRUCTIONS, ...EXTRACT_OPTIONS,
          wait_seconds: 60, idempotency_key: key,
        }),
      });
      return { jobId: run.job.job_id, idempotencyKey: run.idempotencyKey, elapsedMs: run.elapsedMs, job: run.job };
    };

    parse ??= await runParse();
    let extract: CachedJob;
    try {
      extract = await runExtract(parse.jobId);
    } catch (err) {
      // The cached parse job may have been deleted from the account: parse the pinned bytes again, once.
      const e = toNdiError(err, "extract");
      if (!(cached && parse === cached.ndi.parse && (e.reason === "ndi_not_found" || e.reason === "ndi_invalid_request"))) throw e;
      parse = await runParse();
      extract = await runExtract(parse.jobId);
    }

    // Retrieval facts of the bytes NDI parsed: from this run's download, or carried over with the reused parse.
    const bytesInfo = held.doc
      ? { sha256: held.doc.sha256, bytes: held.doc.size, retrievedAt: held.doc.retrievedAt, httpLastModified: held.doc.httpLastModified }
      : { sha256: cached!.document.sha256, bytes: cached!.document.bytes, retrievedAt: cached!.document.retrievedAt, httpLastModified: cached!.document.httpLastModified };
    const record: IngestRecord = {
      cacheVersion: CACHE_VERSION,
      document: { id: doc.id, title: doc.title, publisher: doc.publisher, sourceUrl: doc.sourceUrl, fileName: doc.fileName, ...bytesInfo },
      schemaHash: hash,
      ndi: { ranAt, elapsedMs: deps.nowMs() - tNdi, upload, parse, extract },
    };
    const derived = deriveFacts(parse.job.result, extract.job.result, fields);
    try { await writeCache(deps.dataDir, record); } catch (err) {
      // The facts are still real and live. The next start replays the same jobs under their stable keys (no new charge).
      derived.warnings.push(redact(`the NDI cache could not be written (${err instanceof Error ? err.message : String(err)})`));
    }
    await logAttempt(deps.dataDir, {
      at: new Date(deps.nowMs()).toISOString(), ok: true, cacheHit: false, sha256: doc.sha256, schemaHash: hash,
      parseJobId: parse.jobId, extractJobId: extract.jobId, elapsedMs: record.ndi.elapsedMs, facts: derived.facts.length, rejected: derived.rejected.length,
    });
    if (derived.facts.length === 0) return await fail("no_verified_facts", "NDI returned no fact that passes the verbatim check", extract.jobId);
    return { ok: true, cacheHit: false, cachePath, record, derived, tookMs: deps.nowMs() - t0 };
  } catch (err) {
    if (err instanceof PolicyDocumentError) return fail(err.code, err.message);
    if (err instanceof NdiError) return fail(err.reason, err.message, err.jobId);
    return fail("internal_error", err instanceof Error ? err.message : String(err));
  }
}

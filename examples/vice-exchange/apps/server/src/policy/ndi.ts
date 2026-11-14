// The slice of the Nace SDK (Perception / NDI) this module uses, plus a job runner that never bills twice.
import {
  APIConnectionError, APIError, APITimeoutError, AuthenticationError, InsufficientCreditError, JobFailedError,
  JobTimeoutError, NaceClient, NotFoundError, RateLimitError, UnprocessableEntityError,
} from "nace-sdk";
import type { CreateParams, DocumentJob, ExtractParams, UploadParams, UploadedFile } from "nace-sdk";

/**
 * Structural subset of `NaceClient`. The real client is used as-is (no adapter code that could drift from the
 * SDK); unit tests pass a fake with the same shape.
 */
export interface NdiClient {
  documents: {
    upload(content: Uint8Array, params?: UploadParams): Promise<UploadedFile>;
    parse(params: CreateParams): Promise<DocumentJob>;
    extract(params: ExtractParams): Promise<DocumentJob>;
  };
  jobs: {
    wait(jobId: string, opts?: { timeout?: number; raiseOnFailure?: boolean; signal?: AbortSignal }): Promise<DocumentJob>;
  };
}

/** Never shows key material, whatever ends up inside an error message. */
export function redact(text: string): string {
  return text.replace(/nace_sk_[A-Za-z0-9_-]+/g, "nace_sk_[redacted]").replace(/Bearer\s+[A-Za-z0-9._~+/=-]{8,}/g, "Bearer [redacted]");
}

/**
 * Builds the real client. Returns null without a key: `new NaceClient()` would otherwise fall back to the process
 * environment, and this module must use only the key the app's config object hands it.
 */
export function createNdiClient(apiKey: string | null | undefined): NdiClient | null {
  const key = typeof apiKey === "string" ? apiKey.trim() : "";
  if (!key) return null;
  try {
    return new NaceClient({ apiKey: key, logLevel: "warn" });
  } catch {
    return null;
  }
}

export type NdiFailureReason =
  | "ndi_auth" | "ndi_insufficient_credit" | "ndi_rate_limited" | "ndi_invalid_request" | "ndi_not_found"
  | "ndi_job_failed" | "ndi_timeout" | "ndi_network" | "ndi_error";

export class NdiError extends Error {
  constructor(public readonly reason: NdiFailureReason, message: string, public readonly jobId: string | null = null) {
    super(redact(message)); this.name = "NdiError";
  }
}

function jobErrorText(job: DocumentJob): string {
  const e = job.error;
  if (e && typeof e === "object") {
    const o = e as { code?: unknown; message?: unknown };
    return [o.code, o.message].filter((p) => typeof p === "string" && p.length > 0).join(": ") || "no error detail";
  }
  return typeof e === "string" && e ? e : "no error detail";
}

/** Maps anything thrown by the SDK (or a fake) to a reason the status can show. Subclasses first. */
export function toNdiError(err: unknown, step: string): NdiError {
  if (err instanceof NdiError) return err;
  if (err instanceof JobTimeoutError) return new NdiError("ndi_timeout", `${step}: job ${err.job.job_id} was still ${err.job.status} after ${err.timeoutMs} ms`, err.job.job_id);
  if (err instanceof JobFailedError) return new NdiError("ndi_job_failed", `${step}: job ${err.job.job_id} ${err.job.status} (${jobErrorText(err.job)})`, err.job.job_id);
  if (err instanceof APITimeoutError) return new NdiError("ndi_timeout", `${step}: request timed out`);
  if (err instanceof APIConnectionError) return new NdiError("ndi_network", `${step}: ${err.message}`);
  if (err instanceof AuthenticationError) return new NdiError("ndi_auth", `${step}: the Nace key was refused (HTTP 401)`);
  if (err instanceof InsufficientCreditError) return new NdiError("ndi_insufficient_credit", `${step}: the Nace balance does not cover this job (HTTP 402)`);
  if (err instanceof RateLimitError) return new NdiError("ndi_rate_limited", `${step}: rate limited (HTTP 429)`);
  if (err instanceof NotFoundError) return new NdiError("ndi_not_found", `${step}: ${err.message}`);
  if (err instanceof UnprocessableEntityError) return new NdiError("ndi_invalid_request", `${step}: ${err.message}`);
  if (err instanceof APIError) return new NdiError("ndi_error", `${step}: HTTP ${err.status} ${err.message}`);
  return new NdiError("ndi_error", `${step}: ${err instanceof Error ? err.message : String(err)}`);
}

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export interface JobRun {
  job: DocumentJob;
  /** The Idempotency-Key the job was created (or replayed) under. */
  idempotencyKey: string;
  /** Wall clock from the create call to the terminal job, in ms. */
  elapsedMs: number;
  /** Creates sent: 1, or 2 when a stale failed job under the stable key had to be replaced. */
  creates: number;
}

export interface RunJobOptions {
  step: "parse" | "extract";
  /** Stable key: a crashed or concurrent run replays the SAME job instead of starting (and billing) another. */
  stableKey: string;
  create(idempotencyKey: string): Promise<DocumentJob>;
  wait: NdiClient["jobs"]["wait"];
  waitTimeoutMs: number;
  nowMs(): number;
  /** Fresh keys only (an explicit forced re-run): never reuse the stable key. */
  forceFresh?: boolean;
  signal?: AbortSignal;
}

function usable(job: DocumentJob, step: string): boolean {
  if (job.status !== "succeeded" || !job.result || typeof job.result !== "object") return false;
  const type = (job.result as { result_type?: unknown }).result_type;
  return type === undefined || type === step;
}

/**
 * Creates one document job and reads it until it is terminal. A create under the stable key replays an earlier job
 * of the same work; if that earlier job is unusable (failed, cancelled, or its result is gone) exactly one more
 * job is created under a fresh key. Failed and cancelled jobs are not charged by Nace.
 */
export async function runJob(o: RunJobOptions): Promise<JobRun> {
  const started = o.nowMs();
  const unique = `${o.stableKey}:${started.toString(36)}`;
  const keys = o.forceFresh ? [unique] : [o.stableKey, unique];
  let last: DocumentJob | null = null;
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i]!;
    let job: DocumentJob;
    try {
      job = await o.create(key);
      if (!TERMINAL.has(String(job.status))) job = await o.wait(job.job_id, { timeout: o.waitTimeoutMs, raiseOnFailure: false, signal: o.signal });
    } catch (err) {
      throw toNdiError(err, o.step);
    }
    last = job;
    if (usable(job, o.step)) return { job, idempotencyKey: key, elapsedMs: o.nowMs() - started, creates: i + 1 };
    if (!TERMINAL.has(String(job.status))) break; // still running after the wait budget: do not start a second one
  }
  const job = last!;
  if (!TERMINAL.has(String(job.status))) throw new NdiError("ndi_timeout", `${o.step}: job ${job.job_id} was still ${job.status} after ${o.waitTimeoutMs} ms`, job.job_id);
  if (job.status === "succeeded") throw new NdiError("ndi_job_failed", `${o.step}: job ${job.job_id} succeeded but returned no ${o.step} result`, job.job_id);
  throw new NdiError("ndi_job_failed", `${o.step}: job ${job.job_id} ${job.status} (${jobErrorText(job)})`, job.job_id);
}

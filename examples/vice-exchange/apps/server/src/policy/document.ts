// The ONE real-world document the police NPCs' policy facts come from, and how it is fetched and identified.
// The repo keeps only this metadata (URL, title, publisher, SHA-256). The file itself is downloaded from the
// publisher's own site into data/ndi/ (git-ignored) and is never committed.
import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export interface PolicyDocumentSpec {
  /** Stable id used as Citation.documentId. */
  id: string;
  title: string;
  publisher: string;
  /** Official publisher URL the bytes are fetched from. */
  sourceUrl: string;
  /** The publisher's own web page for the same order (for people who want to read it). */
  landingUrl: string | null;
  fileName: string;
  /** SHA-256 of the exact bytes that may be ingested. A download with any other hash is refused. */
  sha256: string;
  bytes: number;
  pages: number;
  revision: string;
  /** Date the hash was pinned (the file was first retrieved and read by a person on this team). */
  pinnedOn: string;
  rightsNote: string;
}

/**
 * San Francisco Police Department, Department Notice 24-068 with Department General Order 5.25 "Foot Pursuits"
 * attached in full (page 1 is the notice, pages 2-5 are the order). It governs exactly what the police NPCs decide:
 * when a pursuit may start, what to do once sight of the person is lost, and what the officer must know first.
 */
export const POLICY_DOCUMENT: PolicyDocumentSpec = {
  id: "sfpd-dgo-5.25-foot-pursuits",
  title: "SFPD Department General Order 5.25 \"Foot Pursuits\" (Department Notice 24-068)",
  publisher: "San Francisco Police Department",
  sourceUrl: "https://www.sanfranciscopolice.org/sites/default/files/2024-06/SFPDDN_24_068_20240625.pdf",
  landingUrl: "https://www.sanfranciscopolice.org/node/51306",
  fileName: "SFPDDN_24_068_20240625.pdf",
  sha256: "95ce842aaa2a87c705406f033aae4f972fac3c7f26551fa3a72f702e0de38587",
  bytes: 279378,
  pages: 5,
  revision: "Rev. 03/06/24, Eff. 05/08/24",
  pinnedOn: "2026-11-14",
  rightsNote: "Public record published by the San Francisco Police Department on its official website. It is not "
    + "redistributed here: the repository stores only the URL, the SHA-256 and short attributed quotes.",
};

export type DocumentErrorCode = "download_failed" | "document_hash_mismatch" | "document_not_pdf" | "document_too_large" | "document_io_error";
export class PolicyDocumentError extends Error {
  constructor(public readonly code: DocumentErrorCode, message: string) { super(message); this.name = "PolicyDocumentError"; }
}

export interface StoredDocument {
  spec: PolicyDocumentSpec;
  filePath: string;
  bytes: Uint8Array;
  sha256: string;
  size: number;
  /** ISO time these bytes were downloaded from sourceUrl. */
  retrievedAt: string;
  /** true: already on disk with the pinned hash. false: downloaded during this call. */
  fromDisk: boolean;
  httpLastModified: string | null;
}

interface DocumentMeta { sourceUrl: string; sha256: string; bytes: number; retrievedAt: string; httpLastModified: string | null }

const MAX_BYTES = 12 * 1024 * 1024;
const DOWNLOAD_TIMEOUT_MS = 30_000;
/** Generic client name: no project or user details leave this machine. */
const USER_AGENT = "Mozilla/5.0 (compatible; policy-document-fetch/1.0)";

export function sha256Hex(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function isPdf(bytes: Uint8Array): boolean {
  return bytes.length > 5 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46 && bytes[4] === 0x2d; // "%PDF-"
}

async function readMeta(metaPath: string): Promise<DocumentMeta | null> {
  try {
    const m = JSON.parse(await readFile(metaPath, "utf8")) as Partial<DocumentMeta>;
    return typeof m.sha256 === "string" && typeof m.retrievedAt === "string" && typeof m.sourceUrl === "string"
      ? { sourceUrl: m.sourceUrl, sha256: m.sha256, bytes: Number(m.bytes) || 0, retrievedAt: m.retrievedAt, httpLastModified: m.httpLastModified ?? null }
      : null;
  } catch { return null; }
}

/**
 * Returns the pinned document's bytes: from data/ndi/ when a copy with the pinned hash and a retrieval record is
 * already there, otherwise freshly downloaded from the publisher. Throws PolicyDocumentError; never returns bytes
 * whose SHA-256 differs from the pin.
 */
export async function obtainDocument(spec: PolicyDocumentSpec, dataDir: string, fetchImpl: typeof fetch, nowMs: () => number): Promise<StoredDocument> {
  const filePath = path.join(dataDir, spec.fileName);
  const metaPath = `${filePath}.meta.json`;

  try {
    const onDisk = new Uint8Array(await readFile(filePath));
    const meta = await readMeta(metaPath);
    if (meta && meta.sha256 === spec.sha256 && meta.sourceUrl === spec.sourceUrl && sha256Hex(onDisk) === spec.sha256) {
      return { spec, filePath, bytes: onDisk, sha256: spec.sha256, size: onDisk.length, retrievedAt: meta.retrievedAt, fromDisk: true, httpLastModified: meta.httpLastModified };
    }
  } catch { /* not downloaded yet */ }

  let res: Response;
  try {
    res = await fetchImpl(spec.sourceUrl, {
      headers: { "user-agent": USER_AGENT, accept: "application/pdf,*/*;q=0.8" },
      redirect: "follow", signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS),
    });
  } catch (err) {
    throw new PolicyDocumentError("download_failed", `could not reach ${new URL(spec.sourceUrl).hostname}: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (!res.ok) throw new PolicyDocumentError("download_failed", `${new URL(spec.sourceUrl).hostname} answered HTTP ${res.status}`);
  const declared = Number(res.headers.get("content-length") ?? 0);
  if (declared > MAX_BYTES) throw new PolicyDocumentError("document_too_large", `document is ${declared} bytes (limit ${MAX_BYTES})`);
  let bytes: Uint8Array;
  try { bytes = new Uint8Array(await res.arrayBuffer()); } catch (err) {
    throw new PolicyDocumentError("download_failed", `download interrupted: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (bytes.length > MAX_BYTES) throw new PolicyDocumentError("document_too_large", `document is ${bytes.length} bytes (limit ${MAX_BYTES})`);
  if (!isPdf(bytes)) throw new PolicyDocumentError("document_not_pdf", "the publisher did not return a PDF (possibly a block or challenge page)");
  const actual = sha256Hex(bytes);
  if (actual !== spec.sha256) {
    throw new PolicyDocumentError("document_hash_mismatch",
      `the file at the source URL has SHA-256 ${actual} (${bytes.length} bytes), not the pinned ${spec.sha256}; it was not ingested. Re-read the document and update the pin deliberately.`);
  }

  const retrievedAt = new Date(nowMs()).toISOString();
  const meta: DocumentMeta = { sourceUrl: spec.sourceUrl, sha256: actual, bytes: bytes.length, retrievedAt, httpLastModified: res.headers.get("last-modified") };
  try {
    await mkdir(dataDir, { recursive: true });
    const tmp = `${filePath}.${process.pid}.tmp`;
    await writeFile(tmp, bytes);
    await rename(tmp, filePath);
    await writeFile(metaPath, `${JSON.stringify(meta, null, 2)}\n`);
  } catch (err) {
    throw new PolicyDocumentError("document_io_error", `could not store the document under ${dataDir}: ${err instanceof Error ? err.message : String(err)}`);
  }
  return { spec, filePath, bytes, sha256: actual, size: bytes.length, retrievedAt, fromDisk: false, httpLastModified: meta.httpLastModified };
}

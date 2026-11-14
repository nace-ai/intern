# NDI (Perception) — minimal flow for one PDF with cited fields

Probed 2026-11-14. Sources: `nace-sdk@0.8.0` types (compiled with `tsc --strict`) and `https://console.nace.ai/docs/perception/*.md`. **Nothing here was run against the API**: it needs a `nace_sk_` key, which we do not have. Every response shape below is from the docs and is UNVERIFIED live.

## What it is

Perception is NDI (Nace Document Intelligence) exposed through the same `nace_sk_` key, base URL and balance as Drex. Five tools, each `POST /v1/documents/{parse|split|classify|extract|ground}`. Each create returns an async job; you read the job until it is terminal. Server-side only (no CORS).

For the bonus we need one tool: **Extract** fills a JSON Schema and returns a status and citations per field.

## Minimal flow (compile-checked)

```ts
import { readFile } from "node:fs/promises";
import { NaceClient, workspaceFile } from "nace-sdk";

export interface ExtractCitation {
  location: { kind: string; page?: number; [key: string]: unknown };
  source_text?: string;
}
export interface ExtractField {
  path: string;                                  // JSON Pointer, e.g. "/last_known_location"
  value: unknown;
  status: "found" | "not_found" | "ambiguous";
  confidence?: number | null;
  citations: ExtractCitation[];
}
export interface ExtractResult {
  result_type: "extract";
  data: Record<string, unknown>;                 // shaped by the schema; missing values are null
  fields: ExtractField[];
  schema_id: string | null;
  schema_version: number | null;
  units: number;
  warnings: unknown[];
}

export async function extractBulletin(client: NaceClient, pdfPath: string): Promise<{ jobId: string; result: ExtractResult }> {
  // 1. Upload. Free, works at any balance. Under 32 MiB it is one request.
  const bytes = await readFile(pdfPath);
  const file = await client.documents.upload(new Uint8Array(bytes), {
    file_name: "police-bulletin.pdf",
    on_conflict: "new_version",
  });                                            // -> { workspace_id, file_id, path? }

  // 2. Extract with citations. wait_seconds holds the request open up to 60 s.
  const created = await client.documents.extract({
    source: workspaceFile(file),                 // { type: "workspace_file", workspace_id, file_id }
    schema: {
      type: "object",
      properties: {
        subject_alias: { type: "string", description: "Alias or ticker of the wanted character" },
        last_known_location: { type: "string", description: "Last known location named in the bulletin" },
        last_seen_time: { type: "string", description: "Time of the last sighting as written" },
        threat_level: { type: "string", enum: ["low", "medium", "high"] },
      },
      required: ["subject_alias", "last_known_location"],
    },
    citations: { enabled: true, include_source_text: true },
    wait_seconds: 60,
  });                                            // -> DocumentJob { job_id, kind, status, result?, error?, cost?, timing? }

  // 3. Wait until terminal. Throws JobFailedError or JobTimeoutError.
  const done = await client.jobs.wait(created.job_id, { timeout: 180_000 });
  return { jobId: done.job_id, result: done.result as ExtractResult };
}
```

## Method signatures used

- `client.documents.upload(content: Blob | Uint8Array | ArrayBuffer, params?: { file_name?, path?, on_conflict?: "reject" | "new_version", workspace_id? }, options?) : Promise<UploadedFile>`. `file_name` is required unless `content` is a `File`. The bytes go to the document service with a one-use token, never with the API key.
- `client.documents.extract(params: ExtractParams, options?) : APIPromise<DocumentJob>`. `ExtractParams` = `source` + exactly one of `schema` / `schema_id` (+ optional `schema_version`), plus `instructions?`, `citations?: { enabled?, include_source_text? }` (both default true), `parse_mode?: "low" | "medium" | "high"` (default low), `page_ranges?: { start, end }[]`, `wait_seconds?` (0–60), `idempotency_key?` (≤ 200 chars).
- `client.jobs.wait(jobId, { timeout?, raiseOnFailure?, signal? }) : Promise<DocumentJob>`.
- `source` may also be a bare `https://` URL whose last path segment has a file extension, `{ type: "url", url, file_name }`, or `{ type: "parse_result", job_id }` to reuse an earlier parse without paying for it again.
- Other tools with the same pattern: `documents.parse({ source, output: { formats: ["markdown"] } })` → `result.document.markdown`, `result.document.blocks[]` each with a `location`; `documents.ground({ source, targets: [{ id, text }] })` → `result.targets[].matches[]` with `matched_text`, `match_method`, `location`.

## Result shape (from the docs)

```json
{
  "result_type": "extract",
  "data": { "invoice_number": "INV-1042", "total": null },
  "fields": [
    { "path": "/invoice_number", "value": "INV-1042", "status": "found", "confidence": null,
      "citations": [ { "location": { "kind": "page_region", "page": 1, "polygons": [] }, "source_text": "Invoice INV-1042" } ] },
    { "path": "/total", "value": null, "status": "not_found", "citations": [] }
  ],
  "schema_id": null, "schema_version": null, "units": 1, "warnings": []
}
```

- `fields[].path` is a JSON Pointer; join it to `data`.
- `status`: `found`, `not_found` (the job still succeeded) or `ambiguous`.
- `citations[].location.kind`: `page_region` (one-based `page`, normalized polygons), `text_span` (`char_start`, `char_end`, optional `page`), `sheet_range`, `row_range`, `json_pointer`.
- `confidence` is often `null`; do not display it as a number when it is.

## Limits and rules

- Schema subset: root `type: "object"` with non-empty `properties`. Allowed keywords: `type`, `properties`, `items`, `required`, `description`, `enum`, `title`, `additionalProperties` (only `false`), `maxItems`, `uniqueItems`. `format`, `$ref`, `anyOf` and others are refused with `422 invalid_schema`.
- Formats Extract reads: `.pdf`, `.docx`, `.pptx`, `.xlsx`/`.csv`, images, `.html`, `.txt`, `.md`, `.json`, email. The type comes from the file's bytes and extension.
- Document rate limits (free tier): 120 creates/min, 600 job reads/min, 240 upload setups/min.
- Price: Extract is $12 per 1,000 pages at low effort (Parse $3.50 + Extract $8.50), so a one-page bulletin is about $0.012. Failed or cancelled jobs are not charged; an exact repeat of an earlier job is $0. A prepaid Extract reserves an allowance of up to 3 pages, so the balance must be above zero.
- The SDK mints an `Idempotency-Key` per create and reuses it across that call's automatic retries. Pass your own `idempotency_key` to make a re-run replay the same job.
- Each job has a charge id in the `x-drex-document-job` response header and the job's `idempotency_key` field: `(await client.documents.extract(p).withResponse()).response.headers`.

## How it plugs into the game (FR-064)

1. Ship one original, fictional bulletin PDF in the repo (for example `assets/ndi/police-bulletin.pdf`) that names a wanted alias, a last known location and a time.
2. On a demo-director trigger, run `extractBulletin`, then persist `{ jobId, result, fetchedAt }` to disk (for example `data/ndi-cache.json`).
3. Publish the found fields as a `WANTED_BULLETIN` memory with `sourceKind: "BULLETIN"` for police NPCs, carrying `{ path, value, status, source_text, page, jobId }` so the inspector can show the citation.
4. The police Drex `choice` then reads that memory in its state.
5. Labeling: show "NDI (live job `<id>`)" only when a job ran in this session, "NDI (cached parse, job `<id>`)" when replaying the saved result, and nothing NDI-branded when the content is hand-written JSON.

## Unknowns

- UNVERIFIED: the whole flow against the live API, including real job duration for a one-page PDF.
- UNVERIFIED: whether `wait_seconds: 60` usually returns the finished result directly for a small PDF.
- UNVERIFIED: whether a hackathon account's balance covers Perception as well as Drex (the docs say it is the same balance).
- We have no PDF yet. Extract also accepts `.md`, `.txt` and `.html`, but FR-064 asks for a PDF.

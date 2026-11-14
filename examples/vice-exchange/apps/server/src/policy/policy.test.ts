// POLICY module tests. NDI is always a fake here: no network, no key, nothing billed.
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { DocumentJob } from "nace-sdk";
import { sha256Hex, type PolicyDocumentSpec } from "./document";
import { deriveFacts, fingerprint, pageTexts, wholeSentences } from "./facts";
import { cachePathFor, ingestPolicy, type IngestRecord } from "./ingest";
import { createPolicy, type NdiClient } from "./index";
import { createNdiClient, redact, runJob } from "./ndi";
import { buildExtractSchema, schemaHash, type PolicyFieldSpec } from "./schema";
import { renderReport } from "./report";
import { pageLayout, renderXraySvg, xrayOf } from "./xray";

const PDF = new TextEncoder().encode("%PDF-1.6\nfake test document, never sent anywhere\n%%EOF\n");
const DOC: PolicyDocumentSpec = {
  id: "test-order", title: "Test Order 1.01 \"Chases\"", publisher: "Test Police Department",
  sourceUrl: "https://police.example.test/files/order-1-01.pdf", landingUrl: null, fileName: "order-1-01.pdf",
  sha256: sha256Hex(PDF), bytes: PDF.length, pages: 2, revision: "Rev. 01/01/24", pinnedOn: "2026-01-01", rightsNote: "test fixture",
};
const FIELDS: PolicyFieldSpec[] = [
  { name: "chase_may_start_when", description: "When a chase may start.", gameUse: "pursue needs grounds", roles: ["POLICE"] },
  { name: "if_sight_is_lost", description: "What to do when sight is lost.", gameUse: "investigate last known location", roles: ["POLICE"] },
  { name: "reward_for_capture", description: "The reward paid for a capture.", gameUse: "none", roles: ["POLICE"] },
];
const START = "An officer may start a chase when the officer reasonably believes the person committed an offence.";
const LOST = "When officers lose sight of the person, they should hold a position near where the person was last seen.";
const PARSE_TEXT = `--- Page 1 ---\nTEST ORDER 1.01\n\nA. Decision\n${START}\n\nPage 1 of 2\n--- Page 2 ---\nB. Losing sight\n• ${LOST}\n\nPage 2 of 2\n`;

const box = (page: number, y0: number): Record<string, unknown> => ({ kind: "page_region", page, polygons: [[[0.1, y0], [0.9, y0], [0.9, y0 + 0.05], [0.1, y0 + 0.05]]] });
const found = (name: string, value: string, page: number, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  path: `/${name}`, value, status: "found", confidence: 0.9, citations: [{ location: box(page, 0.3), source_text: value.slice(0, 240) }], ...extra,
});
const GOOD_FIELDS = [
  found("order_number", "1.01", 1), found("chase_may_start_when", START, 1), found("if_sight_is_lost", LOST, 2),
  { path: "/reward_for_capture", value: null, status: "not_found", confidence: null, citations: [] },
];

interface Fake {
  client: NdiClient;
  calls: { upload: number; parse: number; extract: number; wait: number; parseKeys: string[]; extractKeys: string[]; extractSources: unknown[] };
}
function fakeNdi(o: { extractFields?: unknown[]; extractStatus?: string; uploadThrows?: boolean; parseText?: string; idSuffix?: string } = {}): Fake {
  const sfx = o.idSuffix ?? "1";
  const calls: Fake["calls"] = { upload: 0, parse: 0, extract: 0, wait: 0, parseKeys: [], extractKeys: [], extractSources: [] };
  const done: Record<string, DocumentJob> = {
    [`job_parse_${sfx}`]: {
      job_id: `job_parse_${sfx}`, kind: "parse", status: "succeeded", cost: { currency: "USD", pricing_version: "test", total_usd: "0.0070", lines: [] },
      result: { result_type: "parse", lane: "pdf", document: { page_count: 2, text: o.parseText ?? PARSE_TEXT, markdown: null, blocks: [] } },
    },
    [`job_extract_${sfx}`]: {
      job_id: `job_extract_${sfx}`, kind: "extract", status: o.extractStatus ?? "succeeded", error: o.extractStatus === "failed" ? { code: "engine_error", message: "boom" } : undefined,
      cost: { currency: "USD", pricing_version: "test", total_usd: "0.0170", lines: [] },
      result: o.extractStatus === "failed" ? undefined : { result_type: "extract", data: {}, fields: o.extractFields ?? GOOD_FIELDS, warnings: [] },
    },
  };
  const client: NdiClient = {
    documents: {
      upload: async () => { calls.upload += 1; if (o.uploadThrows) throw new Error("socket hang up"); return { workspace_id: "ws_test", file_id: "file_test" }; },
      parse: async (p) => { calls.parse += 1; calls.parseKeys.push(String(p.idempotency_key)); return { job_id: `job_parse_${sfx}`, kind: "parse", status: "queued" }; },
      extract: async (p) => { calls.extract += 1; calls.extractKeys.push(String(p.idempotency_key)); calls.extractSources.push(p.source); return { job_id: `job_extract_${sfx}`, kind: "extract", status: "running" }; },
    },
    jobs: { wait: async (id) => { calls.wait += 1; return done[id]!; } },
  };
  return { client, calls };
}
const explodingClient: NdiClient = {
  documents: {
    upload: async () => { throw new Error("NDI must not be called"); },
    parse: async () => { throw new Error("NDI must not be called"); },
    extract: async () => { throw new Error("NDI must not be called"); },
  },
  jobs: { wait: async () => { throw new Error("NDI must not be called"); } },
};

let fetchCalls = 0;
const okFetch = (async () => { fetchCalls += 1; return new Response(PDF, { status: 200, headers: { "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT" } }); }) as unknown as typeof fetch;
const noFetch = (async () => { throw new Error("network must not be used"); }) as unknown as typeof fetch;

let dir = "";
const silent = (): void => undefined;
beforeEach(() => { dir = mkdtempSync(path.join(os.tmpdir(), "policy-test-")); fetchCalls = 0; });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });
const make = (client: NdiClient | null, extra: Record<string, unknown> = {}) =>
  createPolicy({ naceApiKey: "" }, { dataDir: dir, document: DOC, fields: FIELDS, client, fetch: okFetch, log: silent, ...extra });

describe("createPolicy: cache miss runs the NDI jobs once", () => {
  it("returns synchronously with a null view, then serves NDI facts after start()", async () => {
    const ndi = fakeNdi();
    const policy = make(ndi.client);
    expect(policy.view()).toBeNull();
    expect(policy.factsFor("POLICE")).toEqual([]);
    expect(policy.status().state).toBe("idle");

    await policy.start();
    const view = policy.view();
    expect(view).not.toBeNull();
    expect(view!.ingestedVia).toBe("ndi");
    expect(view!.ndiJobId).toBe("job_extract_1");
    expect(view!.documentTitle).toBe(DOC.title);
    expect(view!.sourceUrl).toBe(DOC.sourceUrl);
    expect(view!.facts.map((c) => c.extractedField)).toEqual(["chase_may_start_when", "if_sight_is_lost"]);

    expect(ndi.calls).toMatchObject({ upload: 1, parse: 1, extract: 1 });
    expect(ndi.calls.extractSources[0]).toEqual({ type: "parse_result", job_id: "job_parse_1" });
    expect(fetchCalls).toBe(1);
    const st = policy.status();
    expect(st).toMatchObject({ state: "ready", reason: null, source: "ndi_live", factCount: 2 });
    expect(st.ndi).toMatchObject({ jobId: "job_extract_1", parseJobId: "job_parse_1", cacheHit: false, costUsd: "0.0240", jobCreatedAt: null, replayed: null });
    // status() is served to browsers: no absolute path of this machine in it.
    expect(st.ndi!.cachePath).toBe(`policy-${DOC.sha256}.json`);
    expect(JSON.stringify(st)).not.toContain(dir);
    expect(st.rejected).toEqual([{ field: "reward_for_capture", reason: "not_found", ndiStatus: "not_found" }]);
    expect(st.identity.order_number).toBe("1.01");
    expect(st.document.retrievedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);

    // The downloaded file and the raw NDI jobs are on disk, keyed by the document hash.
    expect(sha256Hex(new Uint8Array(readFileSync(path.join(dir, DOC.fileName))))).toBe(DOC.sha256);
    const rec = JSON.parse(readFileSync(cachePathFor(dir, DOC.sha256), "utf8")) as IngestRecord;
    expect(path.basename(cachePathFor(dir, DOC.sha256))).toBe(`policy-${DOC.sha256}.json`);
    expect(rec.ndi.extract.job.result).toMatchObject({ result_type: "extract" });
    expect(rec.ndi.parse.job.result).toMatchObject({ result_type: "parse" });
  });

  it("start() is idempotent: a second call does not run a second job", async () => {
    const ndi = fakeNdi();
    const policy = make(ndi.client);
    await Promise.all([policy.start(), policy.start()]);
    await policy.start();
    expect(ndi.calls).toMatchObject({ upload: 1, parse: 1, extract: 1 });
  });
});

describe("createPolicy: cache hit", () => {
  it("loads the earlier real result without any NDI call or download, and keeps the original job id and time", async () => {
    const first = make(fakeNdi().client, { nowMs: () => Date.parse("2026-11-14T20:00:00.000Z") });
    await first.start();
    const original = first.status().ndi!;
    expect(original.ranAt).toBe("2026-11-14T20:00:00.000Z");

    const second = make(explodingClient, { fetch: noFetch, nowMs: () => Date.parse("2026-11-15T09:30:00.000Z") });
    await second.start();
    const st = second.status();
    expect(st).toMatchObject({ state: "ready", source: "ndi_cache", factCount: 2 });
    expect(st.ndi).toMatchObject({ jobId: original.jobId, parseJobId: original.parseJobId, ranAt: "2026-11-14T20:00:00.000Z", cacheHit: true });
    expect(second.view()).toEqual(first.view());
    expect(second.view()!.ndiJobId).toBe("job_extract_1");
  });

  it("a cache hit needs no key at all", async () => {
    await make(fakeNdi().client).start();
    const keyless = createPolicy({ naceApiKey: "" }, { dataDir: dir, document: DOC, fields: FIELDS, fetch: noFetch, log: silent });
    await keyless.start();
    expect(keyless.view()?.facts).toHaveLength(2);
    expect(keyless.status().source).toBe("ndi_cache");
  });

  it("changed questions reuse the cached parse: no second upload or parse, one new extract", async () => {
    await make(fakeNdi().client).start();
    const ndi = fakeNdi({ idSuffix: "2" });
    const fewer = FIELDS.slice(0, 2);
    // No download either: the bytes were already parsed, and their retrieval record is carried over.
    const policy = createPolicy({}, { dataDir: dir, document: DOC, fields: fewer, client: ndi.client, fetch: noFetch, log: silent });
    await policy.start();
    expect(policy.status().document.retrievedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(ndi.calls).toMatchObject({ upload: 0, parse: 0, extract: 1 });
    expect(ndi.calls.extractSources[0]).toEqual({ type: "parse_result", job_id: "job_parse_1" });
    expect(policy.status().ndi).toMatchObject({ jobId: "job_extract_2", parseJobId: "job_parse_1", cacheHit: false });
    expect(schemaHash(fewer)).not.toBe(schemaHash(FIELDS));
  });

  it("a corrupt cache file is a miss, not a crash", async () => {
    const { writeFileSync } = await import("node:fs");
    writeFileSync(cachePathFor(dir, DOC.sha256), "{ not json");
    const ndi = fakeNdi();
    const policy = make(ndi.client);
    await policy.start();
    expect(policy.view()?.facts).toHaveLength(2);
    expect(ndi.calls.extract).toBe(1);
  });
});

describe("createPolicy: failure leaves view() null and says why", () => {
  it("no key and no cache", async () => {
    const policy = createPolicy({ naceApiKey: "" }, { dataDir: dir, document: DOC, fields: FIELDS, fetch: noFetch, log: silent });
    await expect(policy.start()).resolves.toBeUndefined();
    expect(policy.view()).toBeNull();
    expect(policy.factsFor("POLICE")).toEqual([]);
    expect(policy.status()).toMatchObject({ state: "unavailable", reason: "no_api_key", source: null, ndi: null, factCount: 0 });
  });

  it("a failed NDI job: null view, nothing cached", async () => {
    const ndi = fakeNdi({ extractStatus: "failed" });
    const policy = make(ndi.client);
    await expect(policy.start()).resolves.toBeUndefined();
    expect(policy.view()).toBeNull();
    const st = policy.status();
    expect(st.state).toBe("unavailable");
    expect(st.reason).toBe("ndi_job_failed");
    expect(st.message).toContain("engine_error");
    expect(existsSync(cachePathFor(dir, DOC.sha256))).toBe(false);
    // The stale failed job under the stable key is replaced exactly once, under a fresh key.
    expect(ndi.calls.extract).toBe(2);
    expect(new Set(ndi.calls.extractKeys).size).toBe(2);
  });

  it("a failure message never carries an absolute path of this machine", async () => {
    const policy = createPolicy({}, {
      dataDir: dir, document: DOC, fields: FIELDS, fetch: okFetch, log: silent,
      client: { ...explodingClient, documents: { ...explodingClient.documents, upload: async () => { throw new Error(`EACCES: permission denied, open '${dir}/x' under /Users/somebody/secret`); } } },
    });
    await policy.start();
    const st = policy.status();
    expect(st.reason).toBe("ndi_error");
    expect(st.message).toContain("EACCES");
    expect(st.message).not.toContain(dir);
    expect(st.message).not.toContain("somebody");
  });

  it("an SDK error thrown mid-flight never escapes start()", async () => {
    const policy = make(fakeNdi({ uploadThrows: true }).client);
    await expect(policy.start()).resolves.toBeUndefined();
    expect(policy.view()).toBeNull();
    expect(policy.status()).toMatchObject({ state: "unavailable", reason: "ndi_error" });
  });

  it("a document whose hash is not the pinned one is refused before NDI is touched", async () => {
    const tampered = (async () => new Response(new TextEncoder().encode("%PDF-1.6\nsomething else\n"), { status: 200 })) as unknown as typeof fetch;
    const policy = make(explodingClient, { fetch: tampered });
    await policy.start();
    expect(policy.view()).toBeNull();
    expect(policy.status().reason).toBe("document_hash_mismatch");
    expect(existsSync(path.join(dir, DOC.fileName))).toBe(false);
  });

  it("a blocked download (HTTP 403) is reported as download_failed", async () => {
    const blocked = (async () => new Response("denied", { status: 403 })) as unknown as typeof fetch;
    const policy = make(explodingClient, { fetch: blocked });
    await policy.start();
    expect(policy.view()).toBeNull();
    expect(policy.status()).toMatchObject({ reason: "download_failed" });
  });

  it("when every quote NDI returns is a paraphrase there are no facts and the view stays null", async () => {
    const policy = make(fakeNdi({ extractFields: [found("chase_may_start_when", "Officers can chase anyone they think did something wrong, at any time.", 1)] }).client);
    await policy.start();
    expect(policy.view()).toBeNull();
    expect(policy.status()).toMatchObject({ state: "unavailable", reason: "no_verified_facts" });
  });
});

describe("citations", () => {
  it("carry document, page, verbatim quote, field and the NDI job id", async () => {
    const policy = make(fakeNdi().client);
    await policy.start();
    const [start, lost] = policy.factsFor("POLICE");
    expect(start).toEqual({
      documentId: "test-order", documentTitle: DOC.title, sourceUrl: DOC.sourceUrl,
      page: 1, quote: START, extractedField: "chase_may_start_when", ndiJobId: "job_extract_1",
    });
    expect(lost).toMatchObject({ page: 2, quote: LOST, extractedField: "if_sight_is_lost", ndiJobId: "job_extract_1" });
    for (const c of policy.view()!.facts) {
      expect(c.page).toBeGreaterThanOrEqual(1);
      expect(PARSE_TEXT.replace(/\s+/g, " ")).toContain(c.quote);
    }
    expect(policy.regionsFor("if_sight_is_lost")).toEqual([{ page: 2, x0: 0.1, y0: 0.3, x1: 0.9, y1: 0.35 }]);
  });

  it("only police are governed by the order", async () => {
    const policy = make(fakeNdi().client);
    await policy.start();
    expect(policy.factsFor("POLICE")).toHaveLength(2);
    for (const role of ["WITNESS", "INFORMANT", "TRADER"] as const) expect(policy.factsFor(role)).toEqual([]);
  });

  it("callers get copies: mutating a returned view or citation changes nothing", async () => {
    const policy = make(fakeNdi().client);
    await policy.start();
    const view = policy.view()!;
    view.facts[0]!.quote = "tampered"; view.facts.length = 0;
    policy.factsFor("POLICE")[0]!.page = 99;
    expect(policy.view()!.facts[0]!.quote).toBe(START);
    expect(policy.factsFor("POLICE")[0]!.page).toBe(1);
  });
});

describe("deriveFacts: only NDI-found, word-for-word quotes become facts", () => {
  const parse = { document: { page_count: 2, text: PARSE_TEXT } };
  const derive = (fields: unknown[]) => deriveFacts(parse, { result_type: "extract", fields }, FIELDS);

  it("drops a paraphrase, an ambiguous field and a not_found field, each with its reason", () => {
    const d = derive([
      found("chase_may_start_when", "An officer may start a chase if the person probably committed an offence.", 1),
      { path: "/if_sight_is_lost", value: LOST, status: "ambiguous", citations: [] },
    ]);
    expect(d.facts).toEqual([]);
    expect(d.rejected).toEqual([
      { field: "chase_may_start_when", reason: "quote_not_in_document", ndiStatus: "found" },
      { field: "if_sight_is_lost", reason: "ambiguous", ndiStatus: "ambiguous" },
      { field: "reward_for_capture", reason: "not_returned", ndiStatus: null },
    ]);
  });

  it("ignores typography (quote marks, bullets, line breaks) but not words", () => {
    const d = derive([found("if_sight_is_lost", LOST.replace("lose sight", "lose  sight").replace(",", " ,"), 2)]);
    expect(d.facts.map((f) => f.field)).toEqual(["if_sight_is_lost"]);
    expect(fingerprint("“Pursuit” • to\ncontain.")).toBe("pursuittocontain");
  });

  it("corrects a wrong page from NDI using NDI's own parsed text, and says so", () => {
    const d = derive([found("if_sight_is_lost", LOST, 1)]);
    expect(d.facts[0]).toMatchObject({ page: 2, pageSource: "ndi_parse_text" });
    expect(derive([found("if_sight_is_lost", LOST, 2)]).facts[0]).toMatchObject({ page: 2, pageSource: "ndi_citation", confidence: 0.9 });
  });

  it("falls back to the citation's source text when the value is not verbatim, and never shows a cut-off fragment", () => {
    const d = derive([{ path: "/chase_may_start_when", value: "They may chase.", status: "found", confidence: null, citations: [{ location: box(1, 0.2), source_text: `${START} When officers lose sig` }] }]);
    expect(d.facts[0]).toMatchObject({ quote: START, quoteSource: "citation_source_text", page: 1, confidence: null });
  });

  it("survives malformed results", () => {
    for (const junk of [null, undefined, 7, "x", {}, { fields: "no" }, { fields: [null, 3, { path: 5 }] }]) {
      expect(() => deriveFacts(junk, junk, FIELDS)).not.toThrow();
      expect(deriveFacts(parse, junk, FIELDS).facts).toEqual([]);
    }
  });

  it("splits pages on NDI's own markers and shortens only to whole sentences", () => {
    const pages = pageTexts(parse);
    expect([...pages.keys()]).toEqual([1, 2]);
    expect(pages.get(1)).toContain(START);
    expect(pages.get(2)).toContain(LOST);
    expect(pages.get(2)).not.toContain(START);
    expect(wholeSentences("One two. Three four. Five six seven", 22)).toBe("One two. Three four.");
    expect(wholeSentences("No sentence end here at all", 10)).toBe("");
  });
});

describe("NDI plumbing", () => {
  it("replays the stable key, and replaces a stale failed job exactly once", async () => {
    const keys: string[] = [];
    const run = await runJob({
      step: "parse", stableKey: "k", waitTimeoutMs: 1000, nowMs: () => 1_700_000_000_000,
      create: async (key) => { keys.push(key); return { job_id: `j${keys.length}`, kind: "parse", status: keys.length === 1 ? "failed" : "succeeded", result: keys.length === 1 ? undefined : { result_type: "parse" } }; },
      wait: async () => { throw new Error("already terminal: must not wait"); },
    });
    expect(keys[0]).toBe("k");
    expect(keys[1]).not.toBe("k");
    expect(run).toMatchObject({ creates: 2, idempotencyKey: keys[1] });
    expect(run.job.job_id).toBe("j2");
  });

  it("does not start a second job while the first is still running", async () => {
    let creates = 0;
    await expect(runJob({
      step: "extract", stableKey: "k", waitTimeoutMs: 5, nowMs: Date.now,
      create: async () => { creates += 1; return { job_id: "j1", kind: "extract", status: "queued" }; },
      wait: async () => ({ job_id: "j1", kind: "extract", status: "running" }),
    })).rejects.toMatchObject({ reason: "ndi_timeout", jobId: "j1" });
    expect(creates).toBe(1);
  });

  it("builds no client without a key, and never prints key material", () => {
    expect(createNdiClient("")).toBeNull();
    expect(createNdiClient(undefined)).toBeNull();
    expect(createNdiClient("   ")).toBeNull();
    const secret = `nace_sk_${"a1B2".repeat(10)}xyz`;
    expect(redact(`401 for ${secret} / Authorization: Bearer ${secret}`)).not.toContain("a1B2");
  });

  it("sends a schema NDI accepts: object root, string properties, supported keywords only", () => {
    const schema = buildExtractSchema(FIELDS) as { type: string; properties: Record<string, Record<string, unknown>>; additionalProperties: boolean };
    expect(schema.type).toBe("object");
    expect(schema.additionalProperties).toBe(false);
    expect(Object.keys(schema.properties)).toEqual(expect.arrayContaining(["order_number", "chase_may_start_when", "if_sight_is_lost", "reward_for_capture"]));
    for (const prop of Object.values(schema.properties)) expect(Object.keys(prop).sort()).toEqual(["description", "type"]);
  });
});

describe("ingest report", () => {
  it("prints the document, the job id, the elapsed time and the facts, and labels a cache hit", async () => {
    const deps = { dataDir: dir, document: DOC, fields: FIELDS, client: fakeNdi().client, fetch: okFetch, nowMs: Date.now };
    const live = await ingestPolicy(deps);
    const cached = await ingestPolicy({ ...deps, client: null, fetch: noFetch });
    expect(live.ok && !live.cacheHit).toBe(true);
    expect(cached.ok && cached.cacheHit).toBe(true);
    const text = renderReport(cached, { color: false, document: DOC, keyConfigured: false, width: 120 });
    for (const needle of [DOC.title, DOC.sourceUrl, DOC.sha256, "job_extract_1", "job_parse_1", "CACHE HIT", START, "page 1", "page 2", "chase_may_start_when"]) expect(text).toContain(needle);
    expect(renderReport(live, { color: false, document: DOC, keyConfigured: true, width: 100 })).toContain("LIVE NDI RUN");
    const failed = await ingestPolicy({ ...deps, dataDir: path.join(dir, "empty"), client: null, fetch: noFetch });
    expect(renderReport(failed, { color: true, document: DOC, keyConfigured: false })).toContain("no_api_key");
  });
});

describe("NDI X-ray (animated SVG of the document with every cited region)", () => {
  const parseWithBlocks = {
    document: {
      page_count: 2, text: PARSE_TEXT,
      blocks: [
        { type: "heading", content: "TEST ORDER 1.01", location: { kind: "page_region", page: 1, polygons: [[[0.1, 0.05], [0.6, 0.05], [0.6, 0.09], [0.1, 0.09]]] } },
        { type: "text", content: START, location: { kind: "page_region", page: 1, polygons: [[[0.1, 0.3], [0.9, 0.3], [0.9, 0.35], [0.1, 0.35]]] } },
        { type: "text", content: LOST, location: { kind: "page_region", page: 2, polygons: [[[0.1, 0.3], [0.9, 0.3], [0.9, 0.35], [0.1, 0.35]]] } },
        { type: "text", content: "no location", location: null }, "junk",
      ],
    },
  };

  it("is null until real NDI facts are loaded, then draws the document from NDI's own layout and regions", async () => {
    const policy = make(fakeNdi().client);
    expect(policy.xraySvg()).toBeNull();
    await policy.start();
    const svg = policy.xraySvg()!;
    expect(svg.startsWith("<svg ")).toBe(true);
    expect(svg.trimEnd().endsWith("</svg>")).toBe(true);
    for (const needle of ["NDI X-RAY", "job_extract_1", "job_parse_1", "chase_may_start_when", "if_sight_is_lost", "PAGE 1", "PAGE 2", "LIVE NDI RUN", "police.example.test"]) expect(svg).toContain(needle);
    expect(svg).not.toContain("reward_for_capture"); // a field NDI did not find is never drawn
    // One pulsing region and one travelling particle per admitted fact.
    expect(svg.match(/class="pulse"/g)).toHaveLength(2);
    expect(svg.match(/<animateMotion /g)).toHaveLength(2);

    const cached = make(null, { fetch: noFetch });
    await cached.start();
    expect(cached.xraySvg()).toContain("CACHED RESULT OF A REAL NDI JOB");
    expect(cached.xraySvg()).not.toContain("LIVE NDI RUN");
  });

  it("stays null when the policy could not load", async () => {
    const policy = createPolicy({ naceApiKey: "" }, { dataDir: dir, document: DOC, fields: FIELDS, fetch: noFetch, log: silent });
    await policy.start();
    expect(policy.xraySvg()).toBeNull();
  });

  it("reads page boxes from the parse blocks and ignores malformed ones", () => {
    const layout = pageLayout(parseWithBlocks);
    expect(layout.map((l) => [l.page, l.blocks.length])).toEqual([[1, 2], [2, 1]]);
    expect(layout[0]!.blocks[0]).toEqual({ type: "heading", x0: 0.1, y0: 0.05, x1: 0.6, y1: 0.09 });
    for (const junk of [null, 5, {}, { document: { blocks: "x" } }]) expect(pageLayout(junk)).toEqual([]);
  });

  it("escapes document text and marks a fact that has no region in the margin instead of inventing a position", async () => {
    const outcome = await ingestPolicy({ dataDir: dir, document: { ...DOC, title: "Order <1> & \"co\"" }, fields: FIELDS, client: fakeNdi().client, fetch: okFetch, nowMs: Date.now });
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    const svg = xrayOf(outcome);
    expect(svg).toContain("Order &lt;1&gt; &amp; &quot;co&quot;");
    expect(svg).not.toContain("Order <1>");

    const noRegion = renderXraySvg({
      title: "t", publisher: "p", sourceUrl: "https://police.example.test/x.pdf", sha256: DOC.sha256, source: "ndi_live",
      jobId: "j", parseJobId: "pj", ranAt: "2026-01-01T00:00:00.000Z", elapsedMs: 1200, costUsd: null, pageCount: 1, layout: [],
      facts: [{ field: "f", quote: "A quote <b>with</b> markup & more.", page: 1, confidence: null, pageSource: "ndi_parse_text", quoteSource: "extracted_value", regions: [], gameUse: "", roles: ["POLICE"] }],
    });
    expect(noRegion.match(/class="pulse"/g)).toBeNull();
    expect(noRegion).toContain("&lt;b&gt;with&lt;/b&gt;");
    expect(noRegion).toContain("cost not reported");
    expect(noRegion).not.toContain("NDI confidence");
  });
});

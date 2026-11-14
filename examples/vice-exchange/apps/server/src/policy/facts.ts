// Turns the RAW results of the NDI Parse and Extract jobs into policy facts. Pure: no I/O, no network.
// A fact is admitted only when NDI found it AND its quote really is in NDI's own reading of the document, word
// for word. Anything else is dropped and listed with the reason, never repaired or invented.
import type { NpcRole } from "@vice/contracts";
import { IDENTITY_FIELDS, POLICY_FIELDS, type PolicyFieldSpec } from "./schema";

/** A highlight box on a page, in NDI's normalized page coordinates (0..1, origin top-left). */
export interface PageRegion { page: number; x0: number; y0: number; x1: number; y1: number }

export interface PolicyFact {
  /** Schema property NDI filled (Citation.extractedField). */
  field: string;
  /** Verbatim text from the document, exactly as NDI returned it (whitespace collapsed). */
  quote: string;
  /** One-based page of the PDF the quote is on. */
  page: number;
  /** NDI's own confidence for the field; null when NDI gave none (never invented). */
  confidence: number | null;
  /** Where the page number came from: NDI's citation, or locating the quote in NDI's parsed page text. */
  pageSource: "ndi_citation" | "ndi_parse_text";
  /** Which NDI string is quoted: the extracted value, or the citation's source text. */
  quoteSource: "extracted_value" | "citation_source_text";
  /** Highlight boxes NDI returned for the citation ([] when it returned none). */
  regions: PageRegion[];
  /** How the simulation uses the rule (our reading, not document text). */
  gameUse: string;
  roles: NpcRole[];
}

export type RejectReason = "not_returned" | "not_found" | "ambiguous" | "empty_value" | "quote_not_in_document" | "page_unknown";
export interface RejectedField { field: string; reason: RejectReason; ndiStatus: string | null }

export interface DerivedPolicy {
  facts: PolicyFact[];
  rejected: RejectedField[];
  /** What NDI read as the order's identity (number, title, dates); values are NDI's, null when not found. */
  identity: Record<string, string | null>;
  pageCount: number | null;
  warnings: string[];
}

interface RawCitation { location?: Record<string, unknown> | null; source_text?: unknown }
interface RawField { path?: unknown; value?: unknown; status?: unknown; confidence?: unknown; citations?: unknown }

const asRecord = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const clean = (s: string): string => s.replace(/\s+/g, " ").trim();

/**
 * Letters and digits only, lower case. Two strings with the same fingerprint have the same words in the same
 * order; spacing, line breaks, quote marks, bullets and Markdown markup are ignored.
 */
export function fingerprint(text: string): string {
  return text.normalize("NFKD").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

const MIN_QUOTE_FINGERPRINT = 16;
const MAX_QUOTE_CHARS = 480;
const SENTENCE_END = /[.!?;][\u201d"\u2019')\]]*(?=\s|$)/g;
const ENDS_CLEANLY = /[.!?;:)\]\u201d"\u2019']$/;

/** Longest prefix made of whole sentences that fits in `max` characters; "" when even the first does not fit. */
export function wholeSentences(text: string, max: number): string {
  if (text.length <= max) return text;
  let end = 0;
  SENTENCE_END.lastIndex = 0;
  for (let m = SENTENCE_END.exec(text); m; m = SENTENCE_END.exec(text)) {
    const stop = m.index + m[0].length;
    if (stop > max) break;
    end = stop;
  }
  return text.slice(0, end).trim();
}

/** A candidate quote is only ever shortened to a contiguous prefix of whole sentences, never reworded. */
function prepare(text: string, source: PolicyFact["quoteSource"]): string {
  let t = clean(text);
  // NDI caps a citation's source text; a fragment cut mid-sentence is trimmed back to its last complete sentence.
  if (source === "citation_source_text" && !ENDS_CLEANLY.test(t)) t = wholeSentences(t, t.length - 1);
  return t.length > MAX_QUOTE_CHARS ? wholeSentences(t, MAX_QUOTE_CHARS) : t;
}

/** Splits NDI's parsed text into pages using its own `--- Page N ---` markers. One entry, page 1, when unmarked. */
export function pageTexts(parseResult: unknown): Map<number, string> {
  const doc = asRecord(asRecord(parseResult)?.document);
  const source = [doc?.text, doc?.markdown].find((t): t is string => typeof t === "string" && t.trim().length > 0);
  const pages = new Map<number, string>();
  if (!source) return pages;
  const marker = /^[ \t]*-{3,}[ \t]*Page[ \t]+(\d+)[ \t]*-{3,}[ \t]*$/gim;
  let current = 1; let cursor = 0; let sawMarker = false;
  for (let m = marker.exec(source); m; m = marker.exec(source)) {
    const chunk = source.slice(cursor, m.index);
    const next = Number(m[1]);
    // Text before the first marker belongs to the page before it ("between pages" markers start at page 2).
    if (!sawMarker) current = Math.max(1, next - 1);
    if (chunk.trim()) pages.set(current, `${pages.get(current) ?? ""}${chunk}`);
    current = next; cursor = m.index + m[0].length; sawMarker = true;
  }
  const tail = source.slice(cursor);
  if (tail.trim()) pages.set(current, `${pages.get(current) ?? ""}${tail}`);
  return pages;
}

function regionsOf(citations: RawCitation[]): PageRegion[] {
  const out: PageRegion[] = [];
  for (const c of citations) {
    const loc = asRecord(c.location);
    const page = typeof loc?.page === "number" ? loc.page : null;
    if (!loc || page === null || !Array.isArray(loc.polygons)) continue;
    for (const poly of loc.polygons) {
      // A polygon is a list of points; a point is [x, y] or { x, y }.
      const pts = (Array.isArray(poly) ? poly : Array.isArray(asRecord(poly)?.points) ? (asRecord(poly)!.points as unknown[]) : [])
        .map((p) => (Array.isArray(p) ? [Number(p[0]), Number(p[1])] : [Number(asRecord(p)?.x), Number(asRecord(p)?.y)]))
        .filter((p): p is [number, number] => Number.isFinite(p[0]) && Number.isFinite(p[1]));
      if (pts.length < 2) continue;
      const xs = pts.map((p) => p[0]); const ys = pts.map((p) => p[1]);
      const box = { page, x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
      if (box.x0 >= 0 && box.y0 >= 0 && box.x1 <= 1.001 && box.y1 <= 1.001 && box.x1 > box.x0 && box.y1 > box.y0) out.push(box);
    }
  }
  return out;
}

/** Derives the facts a role may cite from the two raw NDI job results. Never throws on malformed input. */
export function deriveFacts(parseResult: unknown, extractResult: unknown, fields: readonly PolicyFieldSpec[] = POLICY_FIELDS): DerivedPolicy {
  const pages = pageTexts(parseResult);
  const prints = new Map<number, string>([...pages].map(([n, t]) => [n, fingerprint(t)]));
  const doc = asRecord(asRecord(parseResult)?.document);
  const pageCount = typeof doc?.page_count === "number" ? doc.page_count : pages.size > 0 ? Math.max(...pages.keys()) : null;

  const extract = asRecord(extractResult);
  const rawFields: RawField[] = Array.isArray(extract?.fields) ? (extract.fields as RawField[]).filter((f) => asRecord(f) !== null) : [];
  const byPath = new Map<string, RawField>(rawFields.filter((f) => typeof f.path === "string").map((f) => [f.path as string, f]));
  const warnings = Array.isArray(extract?.warnings)
    ? extract.warnings.map((w) => (typeof w === "string" ? w : clean(JSON.stringify(w) ?? ""))).filter((w) => w.length > 0)
    : [];

  const identity: Record<string, string | null> = {};
  for (const f of IDENTITY_FIELDS) {
    const raw = byPath.get(`/${f.name}`);
    identity[f.name] = raw && raw.status === "found" && typeof raw.value === "string" && raw.value.trim() ? clean(raw.value) : null;
  }

  const facts: PolicyFact[] = []; const rejected: RejectedField[] = [];
  for (const spec of fields) {
    const raw = byPath.get(`/${spec.name}`);
    const ndiStatus = raw && typeof raw.status === "string" ? raw.status : null;
    const reject = (reason: RejectReason): void => { rejected.push({ field: spec.name, reason, ndiStatus }); };
    if (!raw) { reject("not_returned"); continue; }
    if (ndiStatus === "ambiguous") { reject("ambiguous"); continue; }
    if (ndiStatus !== "found") { reject("not_found"); continue; }

    const citations: RawCitation[] = Array.isArray(raw.citations) ? (raw.citations as RawCitation[]).filter((c) => asRecord(c) !== null) : [];
    const citedPages = citations.map((c) => asRecord(c.location)?.page).filter((p): p is number => typeof p === "number" && Number.isInteger(p) && p >= 1);
    const candidates: Array<{ text: string; source: PolicyFact["quoteSource"] }> = [];
    if (typeof raw.value === "string" && raw.value.trim()) candidates.push({ text: prepare(raw.value, "extracted_value"), source: "extracted_value" });
    for (const c of citations) if (typeof c.source_text === "string" && c.source_text.trim()) candidates.push({ text: prepare(c.source_text, "citation_source_text"), source: "citation_source_text" });
    if (candidates.length === 0) { reject("empty_value"); continue; }

    // First candidate that is in the document word for word; prefer the page NDI cited, else a unique page.
    let accepted: { quote: string; quoteSource: PolicyFact["quoteSource"]; page: number; pageSource: PolicyFact["pageSource"] } | null = null;
    let sawVerbatim = false;
    for (const cand of candidates) {
      const print = fingerprint(cand.text);
      if (print.length < MIN_QUOTE_FINGERPRINT) continue;
      const onPages = [...prints].filter(([, pagePrint]) => pagePrint.includes(print)).map(([n]) => n);
      if (onPages.length === 0) continue;
      sawVerbatim = true;
      const cited = citedPages.find((p) => onPages.includes(p));
      if (cited !== undefined) { accepted = { quote: cand.text, quoteSource: cand.source, page: cited, pageSource: "ndi_citation" }; break; }
      if (onPages.length === 1) { accepted = { quote: cand.text, quoteSource: cand.source, page: onPages[0]!, pageSource: "ndi_parse_text" }; break; }
    }
    if (!accepted) { reject(sawVerbatim ? "page_unknown" : "quote_not_in_document"); continue; }

    facts.push({
      field: spec.name, quote: accepted.quote, page: accepted.page,
      confidence: typeof raw.confidence === "number" && Number.isFinite(raw.confidence) ? raw.confidence : null,
      pageSource: accepted.pageSource, quoteSource: accepted.quoteSource,
      regions: regionsOf(citations).filter((r) => r.page === accepted.page),
      gameUse: spec.gameUse, roles: [...spec.roles],
    });
  }
  return { facts, rejected, identity, pageCount, warnings };
}

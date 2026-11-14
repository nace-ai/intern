# NDI (policy module) — status: DONE and wired in. A real NDI job ran (12:46 PST). 34 policy tests pass, full typecheck clean.

Owner of `apps/server/src/policy/**` (new). Also added the script `ndi:ingest` to `apps/server/package.json` and the root
`package.json` (additive). Nothing else was edited by me. ENGINE applied the integration in `index.ts` and `transport/app.ts`.

## What is real
- Document: **San Francisco Police Department, Department Notice 24-068 with Department General Order 5.25 "Foot Pursuits"
  attached in full** (Rev. 03/06/24, Eff. 05/08/24). 5 pages: page 1 is the notice, pages 2 to 5 are the order.
  Fetched by the module from the publisher's own site:
  `https://www.sanfranciscopolice.org/sites/default/files/2024-06/SFPDDN_24_068_20240625.pdf`
  SHA-256 `95ce842aaa2a87c705406f033aae4f972fac3c7f26551fa3a72f702e0de38587`, 279,378 bytes,
  retrieved 2026-11-14T20:46:50.090Z (publisher Last-Modified: Tue, 25 Jun 2024 17:43:50 GMT).
  The PDF is in `data/ndi/` (git-ignored), not in the repo. The repo holds the URL, the hash and the short quotes below.
- A **real NDI run** produced the facts, through `nace-sdk` 0.8.0: `documents.upload`, then
  Parse job `90745561-b5fa-4951-a6a5-b2d2286640f8` (3,562 ms measured here, 3,255 ms server, $0.0175, 5 pages), then
  Extract job `8dc24341-ac0d-4ae3-99a9-4eea963cd919` on that parse (5,943 ms measured here, 5,832 ms server, $0.0425).
  11.08 s for upload + parse + extract, $0.06 in all, once. Both jobs report `replayed: false`.
- NDI returned 9 of 9 policy fields as `found`, each with a page, one region box and confidence 0.89. All 9 quotes pass
  the module's word-for-word check against NDI's own parsed page text. I checked them a second way: 9 of 9 appear exactly
  (after whitespace and quote-mark normalisation) in the page text macOS PDFKit reads from the same file, on the cited page.
- NDI also read the order's own identity: number 5.25, title "Foot Pursuits", rev 03/06/24, eff 05/08/24.
- The raw jobs are cached in `data/ndi/policy-<sha256>.json`. A restart reads that file: no NDI call, nothing billed
  (3 to 6 ms), and it still reports the original job id and the time the job ran.
- End to end with real Drex (one in-process run, no port): Sgt. Duarte, who had a direct sighting, was offered
  investigate / patrol / pursue and chose **pursue** (0.85, 387 ms, 1,154 input tokens). Ofc. Renner, who only had the
  bulletin, was offered investigate / patrol and chose **investigate** (0.96, 270 ms, 1,067 input tokens). Both traces
  carry the 9 citations with the NDI job id; `decisionSource` was `drex` for both. Two decisions, not a statistic.
- Live on :3001 now: `/health` says `policyLoaded: true`, the room snapshot has `policy` with 9 facts, `/api/policy`
  returns the status, `/api/policy/xray.svg` returns the picture.

## Integration (already applied by ENGINE; the minimal form is)
```ts
import { createPolicy } from "./policy";

const policy = createPolicy(config); // returns at once; view() is null until real NDI facts are loaded
void policy.start();                 // cache hit: a few ms. Miss: runs the real NDI jobs in the background. Never throws.

const app = createApp({
  policy,
  // ...the existing options unchanged
});
```
`createApp` forwards `policy` to `createRoomFactory`. `RoomEngine.snapshot()` calls `policy.view()` each time and
`buildPlan` calls `policy.factsFor("POLICE")` on every police decision, so facts that arrive after boot are picked up
without a restart (covered by `policy.integration.test.ts`, which runs the real room factory, engine and brains).

## API (`import { createPolicy } from "./policy"`)
```ts
createPolicy(cfg?: { naceApiKey?: string | null } | null, opts?: PolicyOptions): PolicyService
interface PolicyService extends PolicyPort {          // PolicyPort = view() + factsFor(role)
  start(): Promise<void>;                             // idempotent, never rejects
  status(): PolicyStatus;
  regionsFor(field: string): PageRegion[];            // highlight boxes, normalised 0..1 page coordinates
  xraySvg(): string | null;                           // animated SVG of the document, null until facts are loaded
}
```
- `view()` — `null` until loaded, then `{ documentTitle, sourceUrl, ingestedVia: "ndi", ndiJobId, facts: Citation[] }`.
  One `Citation` per fact: `documentId, documentTitle, sourceUrl, page, quote, extractedField, ndiJobId`.
- `factsFor("POLICE")` — the 9 citations. `WITNESS`, `INFORMANT`, `TRADER` get `[]`: the order governs officers only.
- `status()` — `state` (`idle | loading | ready | unavailable`), `reason` and `message` when the view is null,
  `source` (`"ndi_live"` = jobs ran in this process, `"ndi_cache"` = an earlier real job read from disk, `null` = no facts),
  `document` (title, publisher, url, sha256, pages, retrievedAt), `ndi` (`jobId`, `parseJobId`, `ranAt`, `elapsedMs`,
  `costUsd`, `jobCreatedAt`, `jobFinishedAt`, `jobServerMs`, `replayed`, `cacheHit`, `cachePath`), `facts` (page,
  confidence, regions, how the game uses the rule), `rejected`, `identity`. No absolute path of this machine is in it.
- Reasons the view stays null: `no_api_key`, `download_failed`, `document_hash_mismatch`, `document_not_pdf`,
  `document_too_large`, `document_io_error`, `ndi_auth`, `ndi_insufficient_credit`, `ndi_rate_limited`,
  `ndi_invalid_request`, `ndi_not_found`, `ndi_job_failed`, `ndi_timeout`, `ndi_network`, `ndi_error`,
  `no_verified_facts`, `internal_error`. Nothing is hardcoded as a stand-in.
- Label rule for the UI: say "NDI (live job <id>)" only when `status().source === "ndi_live"`, and
  "NDI (cached result of job <id>, ran <ranAt>)" when it is `"ndi_cache"`.

## The picture: NDI X-ray (`GET /api/policy/xray.svg`, or `data/ndi/policy-xray.svg`)
One self-contained animated SVG (about 37 KB, no script, no external resource, all document text XML-escaped). It draws
all 5 pages from the 86 layout blocks NDI Parse returned, pulses the 9 regions NDI Extract cited, and runs a circuit
trace with a moving particle from each region to its fact card. It names both job ids and says "LIVE NDI RUN" or
"CACHED RESULT OF A REAL NDI JOB". A fact with no region from NDI is drawn as a margin marker, never at a guessed place.
WEB: `<img src="/api/policy/xray.svg" style={{ width: "100%" }} />` animates as is.

## Script
`npm run ndi:ingest` (root or `apps/server`). Uses the cache when present; `-- --force` runs and pays for new jobs;
`-- --json` for machine output; `-- --no-color` for plain text. Prints document metadata, job ids, elapsed time, cost, a
page map of where each fact sits and the facts, and writes `data/ndi/policy-xray.svg`. Never prints the key.

## How it avoids paying twice
- Local cache keyed by the document hash, with the question-set hash inside. Same document and same questions: no call.
- Changed questions reuse the cached Parse job as a `parse_result` source: no upload, no second parse.
- Every create carries a stable `Idempotency-Key` derived from the document hash and the question hash, so a crashed
  or concurrent start replays the same job instead of starting another. A stale failed job under that key is replaced
  once, under a fresh key. A job still running after the wait budget is never doubled.

## The 9 facts (quotes exactly as NDI returned them; pages are PDF pages of the notice file)
| field | page | quote |
|---|---|---|
| `pursuit_may_start_when` | 2 | An officer may initiate a foot pursuit when they reasonably believe the person has committed an act that would justify a detention, custody, or arrest (See General Order 5.03, Investigative Detentions). |
| `keep_weighing_need_against_risk` | 3 | Officers initiating a foot pursuit or implementing containment procedures, shall act reasonably and shall continually assess and evaluate whether the need for apprehension justifies the safety risk based upon the evolving circumstances of the pursuit. Officers shall weigh the necessity of immediate apprehension (including, but not limited to, the severity of the crime) against the danger to the pursuing officer(s), the fleeing person and the public. |
| `decide_on_facts_known_at_the_time` | 3 | When deciding to engage, not to engage, or discontinue a foot pursuit, the officer’s decision must be evaluated based on the facts and totality of the circumstances known to the officer at the time the decision was made rather than evaluated using the benefit of hindsight. |
| `if_sight_of_person_is_lost` | 4 | For example, when officers lose sight of a person, the danger increases significantly, and the threat of ambush becomes higher. When this occurs, officers should consider transitioning from “pursuit to apprehend” to “pursuit to contain.” |
| `unsure_of_location_is_a_safety_issue` | 3 | The officer becomes unsure of the person's location or direction of travel. |
| `hold_position_where_last_seen` | 4 | Use protective cover or a position of advantage near where the person was last seen; |
| `broadcast_description_and_direction` | 4 | Broadcast the person’s description and last direction of travel (or current location); |
| `safety_is_the_priority` | 2 | The priority for officers engaging in a foot pursuit is safety. |
| `supervisor_must_monitor` | 5 | supervisors shall make every effort to monitor, respond to and continually assess the situation. |

Source of every quote: San Francisco Police Department, Department General Order 5.25 "Foot Pursuits", as published in
Department Notice 24-068 at the URL above.

## Files
- `document.ts` — the pinned document (URL, title, publisher, SHA-256) and the download with hash check.
- `schema.ts` — the questions sent to NDI Extract (field names and descriptions only; no answers).
- `ndi.ts` — the slice of `nace-sdk` used (the real `NaceClient` is used as is), error mapping, the job runner.
- `ingest.ts` — cache, download, upload, Parse, Extract, cache write, attempt log (`data/ndi/ingest-log.jsonl`).
- `facts.ts` — raw NDI results to facts, with the word-for-word check and page resolution.
- `index.ts` — `createPolicy`. `xray.ts` — the SVG. `report.ts`, `cli.ts` — `npm run ndi:ingest`.
- `policy.test.ts` (31 tests, fake NDI) and `policy.integration.test.ts` (3 tests, real engine and brains, fake NDI).

## Honest limits
- The order does not use the words "second-hand report". The two rules the game maps to that question are
  `decide_on_facts_known_at_the_time` and `unsure_of_location_is_a_safety_issue`. The mapping is ours and is labelled as
  ours (`status().facts[].gameUse`), never shown as document text.
- The order is about foot pursuits by real officers. Using it to steer NPCs is a simulation choice, not legal guidance.
- The spec (FR-064, tech design 13) asked for a fictional PDF; this job replaced it with a real one for the bonus.
- The document hash is pinned. If SFPD replaces the file, a fresh ingestion is refused (`document_hash_mismatch`) until
  the pin in `apps/server/src/policy/document.ts` is updated on purpose. The cached result keeps working.
- NDI gave the same confidence (0.89) for every field. It is shown as given and not interpreted.
- The 9 facts add about 2.8 KB of JSON (`department_policy`) to every police Drex call: about 1,100 input tokens per
  police decision in the two calls measured.
- On a machine without `data/ndi/`, the first boot runs the real jobs in the background (about 11 s, $0.06). Run
  `npm run ndi:ingest` once before the demo so the policy is ready before anyone joins.
- Not tested live: a failed or timed-out real NDI job, a 402, and the replay of a stable key. Those paths are covered
  only with the fake client.

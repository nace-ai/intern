// Wiring test: the policy module plugged into the REAL room factory, engine and brains (fake NDI, mock scheduler).
// Proves the integration patch: createRoomFactory({ ..., policy }) puts the NDI facts in the room snapshot and in
// the police decision state, and that a policy loaded AFTER the room exists is picked up without a restart.
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ClientCommand, DecisionTrace } from "@vice/contracts";
import { MockScheduler } from "../../test/helpers";
import { createRoomFactory } from "../engine/compose";
import { createMemoryPersistence } from "../engine/fallbacks";
import { FakeClock } from "../engine/util";
import { sha256Hex, type PolicyDocumentSpec } from "./document";
import { createPolicy, type NdiClient, type PolicyService } from "./index";
import type { PolicyFieldSpec } from "./schema";

const PDF = new TextEncoder().encode("%PDF-1.6\nintegration fixture\n%%EOF\n");
const DOC: PolicyDocumentSpec = {
  id: "test-order", title: "Test Order 1.01", publisher: "Test Police Department", sourceUrl: "https://police.example.test/order.pdf",
  landingUrl: null, fileName: "order.pdf", sha256: sha256Hex(PDF), bytes: PDF.length, pages: 1, revision: "Rev. 1", pinnedOn: "2026-01-01", rightsNote: "test fixture",
};
const RULE = "An officer may start a chase when the officer reasonably believes the person committed an offence.";
const FIELDS: PolicyFieldSpec[] = [{ name: "chase_may_start_when", description: "When a chase may start.", gameUse: "pursue needs grounds", roles: ["POLICE"] }];
const ndi: NdiClient = {
  documents: {
    upload: async () => ({ workspace_id: "ws", file_id: "file" }),
    parse: async () => ({ job_id: "job_parse", kind: "parse", status: "succeeded", result: { result_type: "parse", document: { page_count: 1, text: `--- Page 1 ---\n${RULE}\n` } } }),
    extract: async () => ({
      job_id: "job_extract", kind: "extract", status: "succeeded",
      result: { result_type: "extract", fields: [{ path: "/chase_may_start_when", value: RULE, status: "found", confidence: 0.9, citations: [{ location: { kind: "page_region", page: 1, polygons: [] }, source_text: RULE }] }] },
    }),
  },
  jobs: { wait: async () => { throw new Error("jobs are already terminal"); } },
};
const pdfFetch = (async () => new Response(PDF, { status: 200 })) as unknown as typeof fetch;

let dir = "";
beforeEach(() => { dir = mkdtempSync(path.join(os.tmpdir(), "policy-int-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

function room(policy: PolicyService) {
  const clock = new FakeClock();
  const scheduler = new MockScheduler(clock);
  scheduler.auto = (req) => ({ action: req.scriptedAction, source: "scripted" });
  const build = createRoomFactory({
    persistence: createMemoryPersistence(), clock, policy,
    providers: { naceApiKey: "", naceModel: "", metaApiKey: "", metaBaseUrl: "", metaModel: "", concurrency: 2, maxCallsPerMinute: 60 },
  });
  const { engine } = build({ roomId: "POL-1", code: "POL-1", seed: 42, mode: "scripted", scheduler, clock });
  let n = 0;
  const run = (ms: number): void => { for (let t = 0; t < ms; t += 100) { clock.advance(100); engine.tick(); scheduler.flush(); } };
  const policeTraces = (): DecisionTrace[] => engine.brains.traces().filter((t) => t.role === "POLICE");
  const openMission = () => Object.values(engine.state.missions).find((m) => m.status === "AVAILABLE");
  const characterOf = (playerId: string) => engine.state.characters[engine.state.players[playerId]!.characterId!]!;
  /**
   * One incident: the outlaw completes the open mission, then the world runs until the police have decided on it.
   * Written against behaviour, not pacing (the police timings are tuned by other modules): scripted police capture
   * a stationary outlaw, so a captured character respawns first, and every wait is a bounded loop, not a fixed delay.
   */
  const incident = (playerId: string): void => {
    for (let i = 0; i < 600 && !openMission(); i += 1) run(100); // the next mission opens a while after the last one
    const mission = openMission()!;
    if (characterOf(playerId).status === "CAPTURED") {
      expect(engine.dispatch(playerId, { commandId: `cmd-${++n}`, type: "character.respawn" } as ClientCommand).ok).toBe(true);
    }
    const c = characterOf(playerId); // after a respawn this is a new character with a new ticker
    c.x = mission.x; c.z = mission.z;
    expect(engine.dispatch(playerId, { commandId: `cmd-${++n}`, type: "mission.interact", missionId: mission.id } as ClientCommand).ok).toBe(true);
    const seen = policeTraces().length;
    for (let i = 0; i < 600 && policeTraces().length === seen; i += 1) run(100); // until a police decision exists (60 s cap)
    run(3_000);
  };
  return { engine, run, incident, policeTraces };
}

describe("policy wired into the room factory", () => {
  it("puts the NDI facts in the snapshot and in every police decision, with the NDI job id", async () => {
    const policy = createPolicy({}, { dataDir: dir, document: DOC, fields: FIELDS, client: ndi, fetch: pdfFetch, log: () => undefined });
    await policy.start();
    const r = room(policy);
    const ace = r.engine.addPlayer({ nickname: "Ace", role: "OUTLAW", isDirector: true, sessionToken: "tok_ace" });
    r.run(4000);
    r.incident(ace.id);

    expect(r.engine.snapshot().policy).toEqual({
      documentTitle: "Test Order 1.01", sourceUrl: DOC.sourceUrl, ingestedVia: "ndi", ndiJobId: "job_extract",
      facts: [{ documentId: "test-order", documentTitle: "Test Order 1.01", sourceUrl: DOC.sourceUrl, page: 1, quote: RULE, extractedField: "chase_may_start_when", ndiJobId: "job_extract" }],
    });
    const police = r.policeTraces();
    expect(police.length).toBeGreaterThan(0);
    for (const t of police) {
      expect(t.stateSnapshot.department_policy).toEqual([{ rule: "chase_may_start_when", text: RULE, source: "Test Order 1.01" }]);
      expect(t.citations).toEqual(policy.factsFor("POLICE"));
    }
    // Nobody else is handed the police order.
    for (const t of r.engine.brains.traces().filter((x) => x.role !== "POLICE")) {
      expect(t.citations).toEqual([]);
      expect(t.stateSnapshot).not.toHaveProperty("department_policy");
    }
  });

  it("boot does not wait for NDI: the room runs with a null policy, then picks the facts up when start() finishes", async () => {
    const policy = createPolicy({}, { dataDir: dir, document: DOC, fields: FIELDS, client: ndi, fetch: pdfFetch, log: () => undefined });
    const r = room(policy);
    const ace = r.engine.addPlayer({ nickname: "Ace", role: "OUTLAW", isDirector: true, sessionToken: "tok_ace" });
    r.run(4000);
    r.incident(ace.id);
    expect(r.engine.snapshot().policy).toBeNull();
    const before = r.policeTraces();
    expect(before.length).toBeGreaterThan(0);
    for (const t of before) { expect(t.stateSnapshot.department_policy).toEqual([]); expect(t.citations).toEqual([]); }

    await policy.start();
    expect(r.engine.snapshot().policy?.ndiJobId).toBe("job_extract");
    r.incident(ace.id); // waits for the next mission and respawns the outlaw if the police caught it meanwhile
    const earlier = new Set(before.map((t) => t.id));
    const after = r.policeTraces().filter((t) => !earlier.has(t.id));
    expect(after.length).toBeGreaterThan(0);
    for (const t of after) {
      expect(t.citations.map((c) => c.ndiJobId)).toEqual(["job_extract"]);
      expect(t.stateSnapshot.department_policy).toEqual([{ rule: "chase_may_start_when", text: RULE, source: "Test Order 1.01" }]);
    }
  });

  it("a policy that could not load never breaks the room", async () => {
    const policy = createPolicy({ naceApiKey: "" }, { dataDir: dir, document: DOC, fields: FIELDS, fetch: pdfFetch, log: () => undefined });
    await policy.start();
    expect(policy.status().reason).toBe("no_api_key");
    const r = room(policy);
    const ace = r.engine.addPlayer({ nickname: "Ace", role: "OUTLAW", isDirector: true, sessionToken: "tok_ace" });
    r.run(4000);
    r.incident(ace.id);
    expect(r.engine.snapshot().policy).toBeNull();
    expect(r.policeTraces().length).toBeGreaterThan(0);
  });
});
